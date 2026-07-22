from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from durable_job_queue import (
    DurableJob,
    DurableJobQueue,
    DurableJobStatus,
    TERMINAL_STATUSES,
    _row_to_job,
)


_ACTIVE = {
    DurableJobStatus.LEASED,
    DurableJobStatus.RUNNING,
    DurableJobStatus.RETRYING,
}


@dataclass(frozen=True)
class QueueCounts:
    queued: int = 0
    active: int = 0
    terminal: int = 0
    total: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "queued": self.queued,
            "active": self.active,
            "terminal": self.terminal,
            "total": self.total,
        }


class RuntimeDurableJobQueue(DurableJobQueue):
    """Runtime extensions that mirror API-visible job state into queue payloads."""

    @contextmanager
    def _runtime_connection(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def find_by_idempotency(
        self, *, owner_id: str, idempotency_key: str
    ) -> DurableJob | None:
        owner = str(owner_id or "").strip()
        key = str(idempotency_key or "").strip()
        if not owner or not key:
            return None
        digest = hashlib.sha256(f"{owner}\0{key}".encode("utf-8")).hexdigest()
        with self._runtime_connection() as connection:
            row = connection.execute(
                "SELECT * FROM durable_jobs WHERE idempotency_hash = ?", (digest,)
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def update_payload(
        self,
        job_id: str,
        patch: Mapping[str, Any],
        *,
        worker_id: str | None = None,
        owner_id: str | None = None,
        replace: bool = False,
    ) -> DurableJob:
        if worker_id is None and owner_id is None:
            raise ValueError("worker_id or owner_id is required")
        with self._runtime_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            if worker_id is not None and row["worker_id"] != worker_id:
                raise KeyError("job not found")
            if owner_id is not None and row["owner_id"] != owner_id:
                raise KeyError("job not found")
            payload = dict(patch) if replace else json.loads(row["payload_json"])
            if not replace:
                payload.update(dict(patch))
            connection.execute(
                """
                UPDATE durable_jobs
                SET payload_json = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE job_id = ?
                """,
                (
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                        default=str,
                    ),
                    job_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM durable_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            connection.commit()
        return _row_to_job(updated)

    def heartbeat_with_payload(
        self,
        job_id: str,
        *,
        worker_id: str,
        payload_patch: Mapping[str, Any],
        lease_seconds: int,
    ) -> DurableJob:
        self.update_payload(job_id, payload_patch, worker_id=worker_id)
        return self.heartbeat(
            job_id, worker_id=worker_id, lease_seconds=lease_seconds
        )

    def complete_with_payload(
        self,
        job_id: str,
        *,
        worker_id: str,
        payload_patch: Mapping[str, Any],
    ) -> DurableJob:
        self.update_payload(job_id, payload_patch, worker_id=worker_id)
        return self.complete(job_id, worker_id=worker_id)

    def fail_with_payload(
        self,
        job_id: str,
        *,
        worker_id: str,
        retryable: bool,
        error_code: str,
        error_message: str,
        backoff_seconds: int,
        payload_patch: Mapping[str, Any],
    ) -> DurableJob:
        self.update_payload(job_id, payload_patch, worker_id=worker_id)
        return self.fail(
            job_id,
            worker_id=worker_id,
            retryable=retryable,
            error_code=error_code,
            error_message=error_message,
            backoff_seconds=backoff_seconds,
        )

    def list_jobs(
        self, *, owner_id: str | None = None, limit: int = 100
    ) -> list[DurableJob]:
        params: list[Any] = []
        where = ""
        if owner_id is not None:
            where = "WHERE owner_id = ?"
            params.append(owner_id)
        params.append(max(1, min(int(limit), 1000)))
        with self._runtime_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM durable_jobs
                {where}
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def counts(self, *, owner_id: str | None = None) -> QueueCounts:
        where = "WHERE owner_id = ?" if owner_id is not None else ""
        params = (owner_id,) if owner_id is not None else ()
        with self._runtime_connection() as connection:
            rows = connection.execute(
                f"SELECT status, COUNT(*) AS count FROM durable_jobs {where} GROUP BY status",
                params,
            ).fetchall()
        queued = active = terminal = total = 0
        for row in rows:
            status = DurableJobStatus(row["status"])
            count = int(row["count"])
            total += count
            if status == DurableJobStatus.QUEUED:
                queued += count
            elif status in _ACTIVE:
                active += count
            elif status in TERMINAL_STATUSES:
                terminal += count
        return QueueCounts(queued=queued, active=active, terminal=terminal, total=total)

    def delete(
        self,
        job_id: str,
        *,
        owner_id: str,
        terminal_only: bool = True,
    ) -> DurableJob:
        with self._runtime_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM durable_jobs WHERE job_id = ? AND owner_id = ?",
                (job_id, owner_id),
            ).fetchone()
            if row is None:
                raise KeyError("job not found")
            status = DurableJobStatus(row["status"])
            if terminal_only and status not in TERMINAL_STATUSES:
                raise ValueError("active durable job cannot be deleted")
            connection.execute("DELETE FROM durable_jobs WHERE job_id = ?", (job_id,))
            connection.commit()
        return _row_to_job(row)
