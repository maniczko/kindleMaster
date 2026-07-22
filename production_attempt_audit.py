from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from durable_job_queue import DurableJobDatabase, DurableJobQueue, QueueRecord, unix_now, utc_now_label

_SCHEMA = """
CREATE TABLE IF NOT EXISTS durable_queue_attempts (
    job_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    worker_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    leased_at TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    heartbeat_at REAL NOT NULL DEFAULT 0,
    finished_at TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(job_id, attempt)
);
CREATE INDEX IF NOT EXISTS durable_queue_attempt_status
ON durable_queue_attempts(job_id, status, attempt);
"""


def _json_dumps(value: Mapping[str, Any] | None) -> str:
    return json.dumps(dict(value or {}), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def ensure_attempt_audit_schema(database: DurableJobDatabase) -> None:
    with database.connect() as connection:
        connection.executescript(_SCHEMA)


def _close_expired_attempts(connection, *, now: float, now_label: str) -> None:
    rows = connection.execute(
        """
        SELECT job_id, status, attempt, max_attempts, lease_owner
        FROM durable_queue
        WHERE status IN ('leased', 'running')
          AND lease_expires_at > 0
          AND lease_expires_at <= ?
        """,
        (now,),
    ).fetchall()
    for row in rows:
        job_id = str(row["job_id"])
        attempt = int(row["attempt"] or 0)
        exhausted = attempt >= int(row["max_attempts"] or 1)
        error = "worker lease expired after final allowed attempt" if exhausted else "worker lease expired"
        attempt_status = "dead_letter" if exhausted else "lease_expired"
        connection.execute(
            """
            UPDATE durable_queue_attempts
            SET status = ?, finished_at = ?, error = ?
            WHERE job_id = ? AND attempt = ? AND status IN ('leased', 'running')
            """,
            (attempt_status, now_label, error, job_id, attempt),
        )
        if exhausted:
            connection.execute(
                """
                UPDATE durable_queue
                SET status = 'dead_letter', lease_owner = '', lease_expires_at = 0,
                    heartbeat_at = 0, last_error = ?, updated_at = ?, finished_at = ?
                WHERE job_id = ? AND status IN ('leased', 'running')
                  AND lease_expires_at > 0 AND lease_expires_at <= ?
                """,
                (error, now_label, now_label, job_id, now),
            )


def _audited_claim(self: DurableJobQueue, *, worker_id: str, lease_seconds: int = 120) -> QueueRecord | None:
    normalized_worker = str(worker_id or "").strip()
    if not normalized_worker:
        raise ValueError("worker_id is required")
    ensure_attempt_audit_schema(self.database)
    now = unix_now()
    now_label = utc_now_label()
    lease_expires = now + max(30, int(lease_seconds))
    with self.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _close_expired_attempts(connection, now=now, now_label=now_label)
        row = connection.execute(
            """
            SELECT * FROM durable_queue
            WHERE cancellation_requested = 0
              AND attempt < max_attempts
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
        connection.execute(
            """
            INSERT INTO durable_queue_attempts(
                job_id, attempt, worker_id, status, leased_at, heartbeat_at
            ) VALUES (?, ?, ?, 'leased', ?, ?)
            """,
            (job_id, attempt, normalized_worker, now_label, now),
        )
        claimed = connection.execute("SELECT * FROM durable_queue WHERE job_id = ?", (job_id,)).fetchone()
        connection.commit()
    return QueueRecord.from_row(claimed) if claimed is not None else None


def _audited_lease_update(
    self: DurableJobQueue,
    job_id: str,
    *,
    worker_id: str,
    status: str,
    lease_seconds: int,
) -> QueueRecord:
    ensure_attempt_audit_schema(self.database)
    now = unix_now()
    now_label = utc_now_label()
    normalized_job_id = str(job_id)
    normalized_worker = str(worker_id)
    with self.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM durable_queue
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased','running')
            """,
            (normalized_job_id, normalized_worker),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise RuntimeError("worker does not own queue lease")
        attempt = int(row["attempt"] or 0)
        connection.execute(
            """
            UPDATE durable_queue
            SET status = ?, heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased','running')
            """,
            (
                status,
                now,
                now + max(30, int(lease_seconds)),
                now_label,
                normalized_job_id,
                normalized_worker,
            ),
        )
        connection.execute(
            """
            UPDATE durable_queue_attempts
            SET status = ?, started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                heartbeat_at = ?
            WHERE job_id = ? AND attempt = ? AND worker_id = ?
            """,
            (status, now_label, now, normalized_job_id, attempt, normalized_worker),
        )
        updated = connection.execute(
            "SELECT * FROM durable_queue WHERE job_id = ?", (normalized_job_id,)
        ).fetchone()
        connection.commit()
    assert updated is not None
    return QueueRecord.from_row(updated)


def _audited_heartbeat(
    self: DurableJobQueue,
    job_id: str,
    *,
    worker_id: str,
    lease_seconds: int = 120,
) -> bool:
    ensure_attempt_audit_schema(self.database)
    now = unix_now()
    now_label = utc_now_label()
    normalized_job_id = str(job_id)
    normalized_worker = str(worker_id)
    with self.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT attempt FROM durable_queue
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
            """,
            (normalized_job_id, normalized_worker),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        attempt = int(row["attempt"] or 0)
        connection.execute(
            """
            UPDATE durable_queue
            SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
            """,
            (
                now,
                now + max(30, int(lease_seconds)),
                now_label,
                normalized_job_id,
                normalized_worker,
            ),
        )
        connection.execute(
            """
            UPDATE durable_queue_attempts
            SET heartbeat_at = ?
            WHERE job_id = ? AND attempt = ? AND worker_id = ?
            """,
            (now, normalized_job_id, attempt, normalized_worker),
        )
        connection.commit()
    return True


def _audited_finish(
    self: DurableJobQueue,
    job_id: str,
    *,
    worker_id: str,
    status: str,
    error: str,
    result: Mapping[str, Any] | None,
) -> QueueRecord:
    if status not in {"succeeded", "failed", "dead_letter", "cancelled"}:
        raise ValueError(f"invalid terminal queue status: {status}")
    ensure_attempt_audit_schema(self.database)
    now_label = utc_now_label()
    normalized_job_id = str(job_id)
    normalized_worker = str(worker_id)
    with self.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT attempt FROM durable_queue
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
            """,
            (normalized_job_id, normalized_worker),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise RuntimeError("worker does not own active queue lease")
        attempt = int(row["attempt"] or 0)
        result_json = _json_dumps(result)
        bounded_error = str(error)[:4_000]
        connection.execute(
            """
            UPDATE durable_queue
            SET status = ?, result_json = ?, last_error = ?,
                lease_owner = '', lease_expires_at = 0, heartbeat_at = 0,
                updated_at = ?, finished_at = ?
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
            """,
            (
                status,
                result_json,
                bounded_error,
                now_label,
                now_label,
                normalized_job_id,
                normalized_worker,
            ),
        )
        connection.execute(
            """
            UPDATE durable_queue_attempts
            SET status = ?, finished_at = ?, error = ?, result_json = ?
            WHERE job_id = ? AND attempt = ? AND worker_id = ?
            """,
            (
                status,
                now_label,
                bounded_error,
                result_json,
                normalized_job_id,
                attempt,
                normalized_worker,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM durable_queue WHERE job_id = ?", (normalized_job_id,)
        ).fetchone()
        connection.commit()
    assert updated is not None
    return QueueRecord.from_row(updated)


def _audited_fail(
    self: DurableJobQueue,
    job_id: str,
    *,
    worker_id: str,
    error: str,
    retryable: bool,
    base_backoff_seconds: int = 10,
) -> QueueRecord:
    ensure_attempt_audit_schema(self.database)
    now = unix_now()
    now_label = utc_now_label()
    normalized_job_id = str(job_id)
    normalized_worker = str(worker_id)
    bounded_error = str(error)[:4_000]
    with self.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT * FROM durable_queue
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
            """,
            (normalized_job_id, normalized_worker),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise RuntimeError("worker does not own active queue lease")
        attempt = int(row["attempt"] or 0)
        max_attempts = int(row["max_attempts"] or 1)
        if retryable and attempt < max_attempts:
            delay = max(1, int(base_backoff_seconds)) * (2 ** max(0, attempt - 1))
            queue_status = "retry_wait"
            attempt_status = "failed_retryable"
            finished_at = ""
            available_at = now + delay
        else:
            queue_status = "dead_letter" if retryable else "failed"
            attempt_status = queue_status
            finished_at = now_label
            available_at = float(row["available_at"] or 0)
        connection.execute(
            """
            UPDATE durable_queue
            SET status = ?, available_at = ?, lease_owner = '', lease_expires_at = 0,
                heartbeat_at = 0, last_error = ?, updated_at = ?,
                finished_at = CASE WHEN ? <> '' THEN ? ELSE finished_at END
            WHERE job_id = ? AND lease_owner = ? AND status IN ('leased', 'running')
            """,
            (
                queue_status,
                available_at,
                bounded_error,
                now_label,
                finished_at,
                finished_at,
                normalized_job_id,
                normalized_worker,
            ),
        )
        connection.execute(
            """
            UPDATE durable_queue_attempts
            SET status = ?, finished_at = ?, error = ?
            WHERE job_id = ? AND attempt = ? AND worker_id = ?
            """,
            (
                attempt_status,
                now_label,
                bounded_error,
                normalized_job_id,
                attempt,
                normalized_worker,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM durable_queue WHERE job_id = ?", (normalized_job_id,)
        ).fetchone()
        connection.commit()
    assert updated is not None
    return QueueRecord.from_row(updated)


def _attempts(self: DurableJobQueue, job_id: str) -> list[dict[str, Any]]:
    ensure_attempt_audit_schema(self.database)
    with self.database.connect() as connection:
        rows = connection.execute(
            """
            SELECT job_id, attempt, worker_id, status, leased_at, started_at,
                   heartbeat_at, finished_at, error, result_json
            FROM durable_queue_attempts
            WHERE job_id = ?
            ORDER BY attempt ASC
            """,
            (str(job_id),),
        ).fetchall()
    return [
        {
            "job_id": str(row["job_id"]),
            "attempt": int(row["attempt"]),
            "worker_id": str(row["worker_id"] or ""),
            "status": str(row["status"]),
            "leased_at": str(row["leased_at"] or ""),
            "started_at": str(row["started_at"] or ""),
            "heartbeat_at": float(row["heartbeat_at"] or 0),
            "finished_at": str(row["finished_at"] or ""),
            "error": str(row["error"] or ""),
            "result": _json_loads(row["result_json"]),
        }
        for row in rows
    ]


def install_durable_attempt_audit(database: DurableJobDatabase) -> None:
    ensure_attempt_audit_schema(database)
    if getattr(DurableJobQueue.claim, "_kindlemaster_attempt_audit", False):
        return

    _audited_claim._kindlemaster_attempt_audit = True
    _audited_lease_update._kindlemaster_attempt_audit = True
    _audited_heartbeat._kindlemaster_attempt_audit = True
    _audited_finish._kindlemaster_attempt_audit = True
    _audited_fail._kindlemaster_attempt_audit = True
    DurableJobQueue.claim = _audited_claim
    DurableJobQueue._lease_update = _audited_lease_update
    DurableJobQueue.heartbeat = _audited_heartbeat
    DurableJobQueue._finish = _audited_finish
    DurableJobQueue.fail = _audited_fail
    DurableJobQueue.attempts = _attempts
