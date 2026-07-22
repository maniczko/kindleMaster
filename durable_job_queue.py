from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_QUEUE_STATUSES = frozenset({"succeeded", "failed", "dead_letter", "cancelled"})
ACTIVE_QUEUE_STATUSES = frozenset({"queued", "leased", "running", "retry_wait"})


def utc_now_label() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def unix_now() -> float:
    return time.time()


def _json_dumps(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    return dict(loaded) if isinstance(loaded, Mapping) else {}


@dataclass(frozen=True)
class QueueRecord:
    job_id: str
    status: str
    owner_key: str
    idempotency_key: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    available_at: float
    lease_owner: str
    lease_expires_at: float
    heartbeat_at: float
    cancellation_requested: bool
    last_error: str
    result: dict[str, Any]
    created_at: str
    updated_at: str
    started_at: str
    finished_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "QueueRecord":
        return cls(
            job_id=str(row["job_id"]),
            status=str(row["status"]),
            owner_key=str(row["owner_key"] or ""),
            idempotency_key=str(row["idempotency_key"] or ""),
            payload=_json_loads(row["payload_json"]),
            attempt=int(row["attempt"] or 0),
            max_attempts=int(row["max_attempts"] or 1),
            available_at=float(row["available_at"] or 0),
            lease_owner=str(row["lease_owner"] or ""),
            lease_expires_at=float(row["lease_expires_at"] or 0),
            heartbeat_at=float(row["heartbeat_at"] or 0),
            cancellation_requested=bool(row["cancellation_requested"]),
            last_error=str(row["last_error"] or ""),
            result=_json_loads(row["result_json"]),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            started_at=str(row["started_at"] or ""),
            finished_at=str(row["finished_at"] or ""),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "available_at": self.available_at,
            "lease_owner": self.lease_owner,
            "lease_expires_at": self.lease_expires_at,
            "heartbeat_at": self.heartbeat_at,
            "cancellation_requested": self.cancellation_requested,
            "last_error": self.last_error,
            "result": dict(self.result),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass(frozen=True)
class EnqueueResult:
    record: QueueRecord
    created: bool


class DurableJobDatabase:
    """SQLite state shared by API and worker processes on one persistent volume."""

    def __init__(self, path: str | os.PathLike[str], *, busy_timeout_ms: int = 15_000) -> None:
        self.path = Path(path)
        self.busy_timeout_ms = max(1_000, int(busy_timeout_ms))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversion_jobs (
                    job_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS durable_queue (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    owner_key TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at REAL NOT NULL DEFAULT 0,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_expires_at REAL NOT NULL DEFAULT 0,
                    heartbeat_at REAL NOT NULL DEFAULT 0,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );

                CREATE UNIQUE INDEX IF NOT EXISTS durable_queue_idempotency
                ON durable_queue(owner_key, idempotency_key)
                WHERE idempotency_key <> '';

                CREATE INDEX IF NOT EXISTS durable_queue_claim
                ON durable_queue(status, available_at, lease_expires_at, created_at);

                CREATE TABLE IF NOT EXISTS rate_limit_windows (
                    key TEXT PRIMARY KEY,
                    window_started_at REAL NOT NULL,
                    count INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )


class SQLiteJobMapping(MutableMapping[str, dict[str, Any]]):
    """MutableMapping compatibility for direct `_CONVERSION_JOBS` access."""

    def __init__(self, database: DurableJobDatabase) -> None:
        self.database = database

    def __getitem__(self, key: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM conversion_jobs WHERE job_id = ?", (str(key),)
            ).fetchone()
        if row is None:
            raise KeyError(key)
        return _json_loads(row["payload_json"])

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        payload = dict(value)
        payload["job_id"] = str(payload.get("job_id") or key)
        now = str(payload.get("updated_at") or utc_now_label())
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO conversion_jobs(job_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (str(key), _json_dumps(payload), now),
            )
            connection.commit()

    def __delitem__(self, key: str) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM conversion_jobs WHERE job_id = ?", (str(key),))
            connection.commit()
        if cursor.rowcount == 0:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT job_id FROM conversion_jobs ORDER BY job_id").fetchall()
        return iter([str(row["job_id"]) for row in rows])

    def __len__(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM conversion_jobs").fetchone()
        return int(row["count"] if row else 0)


class SQLiteConversionJobStore:
    """Process-safe replacement for the JSON-backed ConversionJobStore."""

    def __init__(self, database: DurableJobDatabase) -> None:
        self.database = database
        self.mapping = SQLiteJobMapping(database)

    @property
    def persistence_path(self) -> Path:
        return self.database.path

    def load(self, *, preserve_active_in_memory: bool = False) -> dict[str, Any]:
        del preserve_active_in_memory
        return {"loaded": True, "job_count": len(self.mapping), "interrupted_jobs": 0, "error": ""}

    def reload_if_changed(self) -> dict[str, Any]:
        return {
            "reloaded": False,
            "loaded": True,
            "job_count": len(self.mapping),
            "interrupted_jobs": 0,
            "error": "",
        }

    def persist(self) -> dict[str, Any]:
        return {"persisted": True, "job_count": len(self.mapping), "error": ""}

    def recover_from_artifacts(self, artifact_root: str | os.PathLike[str], *, limit: int = 200) -> dict[str, Any]:
        del artifact_root, limit
        return {"recovered": False, "job_count": 0, "error": "sqlite_store_authoritative"}

    def create(self, job: Mapping[str, Any]) -> dict[str, Any]:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("Conversion job requires a non-empty job_id.")
        payload = dict(job)
        payload["job_id"] = job_id
        payload.setdefault("updated_at", utc_now_label())
        self.mapping[job_id] = payload
        return dict(payload)

    def update(self, job_id: str, fields: Mapping[str, Any], *, updated_at: str | None = None) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM conversion_jobs WHERE job_id = ?", (normalized_job_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            payload = _json_loads(row["payload_json"])
            payload.update(dict(fields))
            payload["job_id"] = normalized_job_id
            payload["updated_at"] = updated_at or utc_now_label()
            connection.execute(
                "UPDATE conversion_jobs SET payload_json = ?, updated_at = ? WHERE job_id = ?",
                (_json_dumps(payload), payload["updated_at"], normalized_job_id),
            )
            connection.commit()
        return dict(payload)

    def get(self, job_id: str) -> dict[str, Any] | None:
        try:
            return self.mapping[str(job_id)]
        except KeyError:
            return None

    def delete(self, job_id: str) -> dict[str, Any] | None:
        existing = self.get(job_id)
        if existing is None:
            return None
        try:
            del self.mapping[str(job_id)]
        except KeyError:
            return None
        return existing

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT job_id, payload_json FROM conversion_jobs").fetchall()
        return {str(row["job_id"]): _json_loads(row["payload_json"]) for row in rows}


class DurableJobQueue:
    def __init__(self, database: DurableJobDatabase) -> None:
        self.database = database

    def enqueue(
        self,
        *,
        job_id: str,
        payload: Mapping[str, Any],
        owner_key: str = "",
        idempotency_key: str = "",
        max_attempts: int = 3,
    ) -> EnqueueResult:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            raise ValueError("job_id is required")
        normalized_owner = str(owner_key or "").strip()
        normalized_idempotency = str(idempotency_key or "").strip()[:200]
        now_label = utc_now_label()
        now = unix_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if normalized_idempotency:
                existing = connection.execute(
                    "SELECT * FROM durable_queue WHERE owner_key = ? AND idempotency_key = ?",
                    (normalized_owner, normalized_idempotency),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return EnqueueResult(QueueRecord.from_row(existing), False)
            connection.execute(
                """
                INSERT INTO durable_queue(
                    job_id, status, owner_key, idempotency_key, payload_json,
                    attempt, max_attempts, available_at, created_at, updated_at
                ) VALUES (?, 'queued', ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    normalized_job_id,
                    normalized_owner,
                    normalized_idempotency,
                    _json_dumps(payload),
                    max(1, int(max_attempts)),
                    now,
                    now_label,
                    now_label,
                ),
            )
            row = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (normalized_job_id,)).fetchone()
            connection.commit()
        assert row is not None
        return EnqueueResult(QueueRecord.from_row(row), True)

    def get(self, job_id: str) -> QueueRecord | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (str(job_id),)).fetchone()
        return QueueRecord.from_row(row) if row is not None else None

    def find_idempotent(self, owner_key: str, idempotency_key: str) -> QueueRecord | None:
        if not str(idempotency_key or "").strip():
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM durable_queue WHERE owner_key = ? AND idempotency_key = ?",
                (str(owner_key or "").strip(), str(idempotency_key or "").strip()[:200]),
            ).fetchone()
        return QueueRecord.from_row(row) if row is not None else None

    def claim(self, *, worker_id: str, lease_seconds: int = 120) -> QueueRecord | None:
        normalized_worker = str(worker_id or "").strip()
        if not normalized_worker:
            raise ValueError("worker_id is required")
        now = unix_now()
        now_label = utc_now_label()
        lease_expires = now + max(30, int(lease_seconds))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM durable_queue
                WHERE cancellation_requested = 0
                  AND (
                    (status IN ('queued', 'retry_wait') AND available_at <= ?)
                    OR (status IN ('leased', 'running') AND lease_expires_at > 0 AND lease_expires_at <= ?)
                  )
                ORDER BY available_at ASC, created_at ASC
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            job_id = str(row["job_id"])
            attempt = int(row["attempt"] or 0) + 1
            started_at = str(row["started_at"] or "") or now_label
            connection.execute(
                """
                UPDATE durable_queue
                SET status = 'leased', attempt = ?, lease_owner = ?,
                    lease_expires_at = ?, heartbeat_at = ?, updated_at = ?, started_at = ?
                WHERE job_id = ?
                """,
                (attempt, normalized_worker, lease_expires, now, now_label, started_at, job_id),
            )
            claimed = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        return QueueRecord.from_row(claimed) if claimed is not None else None

    def mark_running(self, job_id: str, *, worker_id: str, lease_seconds: int = 120) -> QueueRecord:
        return self._lease_update(job_id, worker_id=worker_id, status="running", lease_seconds=lease_seconds)

    def heartbeat(self, job_id: str, *, worker_id: str, lease_seconds: int = 120) -> bool:
        now = unix_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE durable_queue
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
                """,
                (
                    now,
                    now + max(30, int(lease_seconds)),
                    utc_now_label(),
                    str(job_id),
                    str(worker_id),
                ),
            )
            connection.commit()
        return cursor.rowcount == 1

    def complete(self, job_id: str, *, worker_id: str, result: Mapping[str, Any] | None = None) -> QueueRecord:
        return self._finish(job_id, worker_id=worker_id, status="succeeded", error="", result=result)

    def mark_cancelled(self, job_id: str, *, worker_id: str) -> QueueRecord:
        return self._finish(job_id, worker_id=worker_id, status="cancelled", error="cancelled", result={})

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        error: str,
        retryable: bool,
        base_backoff_seconds: int = 10,
    ) -> QueueRecord:
        current = self.get(job_id)
        if current is None:
            raise KeyError(job_id)
        if current.lease_owner != str(worker_id):
            raise RuntimeError("worker does not own queue lease")
        if retryable and current.attempt < current.max_attempts:
            delay = max(1, int(base_backoff_seconds)) * (2 ** max(0, current.attempt - 1))
            now = unix_now()
            now_label = utc_now_label()
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE durable_queue
                    SET status = 'retry_wait', available_at = ?, lease_owner = '',
                        lease_expires_at = 0, heartbeat_at = 0, last_error = ?, updated_at = ?
                    WHERE job_id = ? AND lease_owner = ?
                    """,
                    (now + delay, str(error)[:4_000], now_label, str(job_id), str(worker_id)),
                )
                row = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (str(job_id),)).fetchone()
                connection.commit()
            if row is None:
                raise KeyError(job_id)
            return QueueRecord.from_row(row)
        terminal = "dead_letter" if retryable else "failed"
        return self._finish(job_id, worker_id=worker_id, status=terminal, error=error, result={})

    def request_cancel(self, job_id: str) -> QueueRecord | None:
        now_label = utc_now_label()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (str(job_id),)).fetchone()
            if row is None:
                connection.rollback()
                return None
            status = str(row["status"])
            if status in TERMINAL_QUEUE_STATUSES:
                connection.commit()
                return QueueRecord.from_row(row)
            immediate = status in {"queued", "retry_wait"}
            connection.execute(
                """
                UPDATE durable_queue
                SET cancellation_requested = 1,
                    status = CASE WHEN ? THEN 'cancelled' ELSE status END,
                    finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (1 if immediate else 0, 1 if immediate else 0, now_label, now_label, str(job_id)),
            )
            updated = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (str(job_id),)).fetchone()
            connection.commit()
        return QueueRecord.from_row(updated) if updated is not None else None

    def active_count(self, *, owner_key: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS count FROM durable_queue WHERE status IN ('queued','leased','running','retry_wait')"
        parameters: tuple[Any, ...] = ()
        if owner_key is not None:
            sql += " AND owner_key = ?"
            parameters = (str(owner_key),)
        with self.database.connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return int(row["count"] if row else 0)

    def counts(self) -> dict[str, int]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT status, COUNT(*) AS count FROM durable_queue GROUP BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _lease_update(self, job_id: str, *, worker_id: str, status: str, lease_seconds: int) -> QueueRecord:
        now = unix_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE durable_queue
                SET status = ?, heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND lease_owner = ? AND status IN ('leased','running')
                """,
                (
                    status,
                    now,
                    now + max(30, int(lease_seconds)),
                    utc_now_label(),
                    str(job_id),
                    str(worker_id),
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("worker does not own queue lease")
            row = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (str(job_id),)).fetchone()
            connection.commit()
        assert row is not None
        return QueueRecord.from_row(row)

    def _finish(
        self,
        job_id: str,
        *,
        worker_id: str,
        status: str,
        error: str,
        result: Mapping[str, Any] | None,
    ) -> QueueRecord:
        now_label = utc_now_label()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE durable_queue
                SET status = ?, result_json = ?, last_error = ?,
                    lease_owner = '', lease_expires_at = 0, heartbeat_at = 0,
                    updated_at = ?, finished_at = ?
                WHERE job_id = ? AND lease_owner = ?
                """,
                (
                    status,
                    _json_dumps(result),
                    str(error)[:4_000],
                    now_label,
                    now_label,
                    str(job_id),
                    str(worker_id),
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("worker does not own queue lease")
            row = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (str(job_id),)).fetchone()
            connection.commit()
        assert row is not None
        return QueueRecord.from_row(row)
