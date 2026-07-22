from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4


class DurableJobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRYING = "retrying"
    READY = "ready"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


TERMINAL_STATUSES = {
    DurableJobStatus.READY,
    DurableJobStatus.FAILED,
    DurableJobStatus.TIMED_OUT,
    DurableJobStatus.CANCELLED,
    DurableJobStatus.DEAD_LETTER,
}

_ALLOWED_TRANSITIONS: dict[DurableJobStatus, set[DurableJobStatus]] = {
    DurableJobStatus.QUEUED: {DurableJobStatus.LEASED, DurableJobStatus.CANCELLED},
    DurableJobStatus.LEASED: {DurableJobStatus.RUNNING, DurableJobStatus.QUEUED, DurableJobStatus.CANCELLED},
    DurableJobStatus.RUNNING: {
        DurableJobStatus.READY,
        DurableJobStatus.RETRYING,
        DurableJobStatus.FAILED,
        DurableJobStatus.TIMED_OUT,
        DurableJobStatus.CANCELLED,
    },
    DurableJobStatus.RETRYING: {DurableJobStatus.QUEUED, DurableJobStatus.DEAD_LETTER, DurableJobStatus.CANCELLED},
    DurableJobStatus.FAILED: {DurableJobStatus.RETRYING},
    DurableJobStatus.TIMED_OUT: {DurableJobStatus.RETRYING},
    DurableJobStatus.READY: set(),
    DurableJobStatus.CANCELLED: set(),
    DurableJobStatus.DEAD_LETTER: set(),
}


@dataclass(frozen=True)
class DurableJob:
    job_id: str
    owner_id: str
    status: DurableJobStatus
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    idempotency_key: str
    worker_id: str
    lease_expires_at: str
    heartbeat_at: str
    available_at: str
    created_at: str
    updated_at: str
    error_code: str
    error_message: str


class DurableJobQueue:
    """SQLite-backed durable queue for a shared Railway volume.

    SQLite WAL and BEGIN IMMEDIATE provide atomic claiming across processes on
    one mounted volume. The API is intentionally backend-agnostic so the same
    contract can later be implemented by PostgreSQL or Trigger.dev.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(
        self,
        *,
        owner_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str = "",
        max_attempts: int = 3,
        job_id: str = "",
        now: datetime | None = None,
    ) -> tuple[DurableJob, bool]:
        owner = _required(owner_id, "owner_id")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        current = _utc(now)
        idem_hash = _idempotency_hash(owner, idempotency_key) if idempotency_key else ""
        serialized = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idem_hash:
                row = connection.execute(
                    "SELECT * FROM durable_jobs WHERE idempotency_hash = ?",
                    (idem_hash,),
                ).fetchone()
                if row is not None:
                    connection.commit()
                    return _row_to_job(row), False
            normalized_job_id = job_id.strip() or f"job-{uuid4().hex}"
            label = _label(current)
            connection.execute(
                """
                INSERT INTO durable_jobs (
                    job_id, owner_id, status, payload_json, attempt, max_attempts,
                    idempotency_key, idempotency_hash, worker_id, lease_expires_at,
                    heartbeat_at, available_at, created_at, updated_at,
                    error_code, error_message
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, '', '', '', ?, ?, ?, '', '')
                """,
                (
                    normalized_job_id,
                    owner,
                    DurableJobStatus.QUEUED.value,
                    serialized,
                    max_attempts,
                    idempotency_key,
                    idem_hash,
                    label,
                    label,
                    label,
                ),
            )
            row = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (normalized_job_id,)).fetchone()
            connection.commit()
        return _row_to_job(row), True

    def claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: datetime | None = None,
    ) -> DurableJob | None:
        worker = _required(worker_id, "worker_id")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        current = _utc(now)
        now_label = _label(current)
        lease_label = _label(current + timedelta(seconds=lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._requeue_expired_locked(connection, current)
            row = connection.execute(
                """
                SELECT * FROM durable_jobs
                WHERE status = ? AND available_at <= ?
                ORDER BY created_at ASC, job_id ASC
                LIMIT 1
                """,
                (DurableJobStatus.QUEUED.value, now_label),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            changed = connection.execute(
                """
                UPDATE durable_jobs
                SET status = ?, worker_id = ?, lease_expires_at = ?, heartbeat_at = ?,
                    attempt = attempt + 1, updated_at = ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    DurableJobStatus.LEASED.value,
                    worker,
                    lease_label,
                    now_label,
                    now_label,
                    row["job_id"],
                    DurableJobStatus.QUEUED.value,
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                return None
            claimed = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (row["job_id"],)).fetchone()
            connection.commit()
        return _row_to_job(claimed)

    def start(self, job_id: str, *, worker_id: str, now: datetime | None = None) -> DurableJob:
        return self._worker_transition(job_id, worker_id, DurableJobStatus.RUNNING, now=now)

    def heartbeat(
        self,
        job_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: datetime | None = None,
    ) -> DurableJob:
        current = _utc(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_worker_job(connection, job_id, worker_id)
            if DurableJobStatus(row["status"]) not in {DurableJobStatus.LEASED, DurableJobStatus.RUNNING}:
                raise InvalidJobTransition(f"heartbeat not allowed from {row['status']}")
            label = _label(current)
            connection.execute(
                "UPDATE durable_jobs SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ? WHERE job_id = ?",
                (label, _label(current + timedelta(seconds=lease_seconds)), label, job_id),
            )
            updated = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        return _row_to_job(updated)

    def complete(self, job_id: str, *, worker_id: str, now: datetime | None = None) -> DurableJob:
        return self._worker_transition(job_id, worker_id, DurableJobStatus.READY, now=now)

    def fail(
        self,
        job_id: str,
        *,
        worker_id: str,
        retryable: bool,
        error_code: str,
        error_message: str = "",
        backoff_seconds: int = 30,
        now: datetime | None = None,
    ) -> DurableJob:
        current = _utc(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_worker_job(connection, job_id, worker_id)
            status = DurableJobStatus(row["status"])
            if status not in {DurableJobStatus.LEASED, DurableJobStatus.RUNNING}:
                raise InvalidJobTransition(f"failure not allowed from {status.value}")
            attempt = int(row["attempt"])
            max_attempts = int(row["max_attempts"])
            if retryable and attempt < max_attempts:
                next_status = DurableJobStatus.QUEUED
                available_at = _label(current + timedelta(seconds=max(0, backoff_seconds)))
            elif retryable:
                next_status = DurableJobStatus.DEAD_LETTER
                available_at = _label(current)
            else:
                next_status = DurableJobStatus.FAILED
                available_at = _label(current)
            label = _label(current)
            connection.execute(
                """
                UPDATE durable_jobs
                SET status = ?, worker_id = '', lease_expires_at = '', heartbeat_at = '',
                    available_at = ?, updated_at = ?, error_code = ?, error_message = ?
                WHERE job_id = ?
                """,
                (next_status.value, available_at, label, error_code, error_message, job_id),
            )
            updated = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        return _row_to_job(updated)

    def cancel(self, job_id: str, *, owner_id: str, now: datetime | None = None) -> DurableJob:
        owner = _required(owner_id, "owner_id")
        current = _utc(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None or row["owner_id"] != owner:
                raise KeyError("job not found")
            status = DurableJobStatus(row["status"])
            if status in TERMINAL_STATUSES:
                connection.commit()
                return _row_to_job(row)
            if DurableJobStatus.CANCELLED not in _ALLOWED_TRANSITIONS[status]:
                raise InvalidJobTransition(f"cancel not allowed from {status.value}")
            label = _label(current)
            connection.execute(
                "UPDATE durable_jobs SET status = ?, worker_id = '', lease_expires_at = '', updated_at = ? WHERE job_id = ?",
                (DurableJobStatus.CANCELLED.value, label, job_id),
            )
            updated = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        return _row_to_job(updated)

    def get(self, job_id: str, *, owner_id: str | None = None) -> DurableJob | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        if owner_id is not None and row["owner_id"] != owner_id:
            return None
        return _row_to_job(row)

    def requeue_expired(self, *, now: datetime | None = None) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = self._requeue_expired_locked(connection, _utc(now))
            connection.commit()
        return count

    def _worker_transition(
        self,
        job_id: str,
        worker_id: str,
        target: DurableJobStatus,
        *,
        now: datetime | None,
    ) -> DurableJob:
        current = _utc(now)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._require_worker_job(connection, job_id, worker_id)
            source = DurableJobStatus(row["status"])
            if target not in _ALLOWED_TRANSITIONS[source]:
                raise InvalidJobTransition(f"{source.value} -> {target.value} is not allowed")
            label = _label(current)
            release_worker = target in TERMINAL_STATUSES
            connection.execute(
                """
                UPDATE durable_jobs SET status = ?, worker_id = ?, lease_expires_at = ?,
                    heartbeat_at = ?, updated_at = ? WHERE job_id = ?
                """,
                (
                    target.value,
                    "" if release_worker else worker_id,
                    "" if release_worker else row["lease_expires_at"],
                    "" if release_worker else label,
                    label,
                    job_id,
                ),
            )
            updated = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
            connection.commit()
        return _row_to_job(updated)

    def _require_worker_job(self, connection: sqlite3.Connection, job_id: str, worker_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None or row["worker_id"] != worker_id:
            raise KeyError("job not found")
        return row

    def _requeue_expired_locked(self, connection: sqlite3.Connection, now: datetime) -> int:
        label = _label(now)
        return connection.execute(
            """
            UPDATE durable_jobs
            SET status = ?, worker_id = '', lease_expires_at = '', heartbeat_at = '',
                available_at = ?, updated_at = ?, error_code = 'lease_expired',
                error_message = 'Worker lease expired before completion.'
            WHERE status IN (?, ?) AND lease_expires_at != '' AND lease_expires_at <= ?
            """,
            (
                DurableJobStatus.QUEUED.value,
                label,
                label,
                DurableJobStatus.LEASED.value,
                DurableJobStatus.RUNNING.value,
                label,
            ),
        ).rowcount

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS durable_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    max_attempts INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    idempotency_hash TEXT NOT NULL,
                    worker_id TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_code TEXT NOT NULL,
                    error_message TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_durable_jobs_idempotency ON durable_jobs(idempotency_hash) WHERE idempotency_hash != ''"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_durable_jobs_claim ON durable_jobs(status, available_at, created_at)"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection


class InvalidJobTransition(RuntimeError):
    pass


def _row_to_job(row: sqlite3.Row) -> DurableJob:
    return DurableJob(
        job_id=row["job_id"],
        owner_id=row["owner_id"],
        status=DurableJobStatus(row["status"]),
        payload=json.loads(row["payload_json"]),
        attempt=int(row["attempt"]),
        max_attempts=int(row["max_attempts"]),
        idempotency_key=row["idempotency_key"],
        worker_id=row["worker_id"],
        lease_expires_at=row["lease_expires_at"],
        heartbeat_at=row["heartbeat_at"],
        available_at=row["available_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


def _required(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _idempotency_hash(owner_id: str, key: str) -> str:
    return hashlib.sha256(f"{owner_id}\0{key}".encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)


def _label(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
