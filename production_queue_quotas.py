from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from durable_job_queue import DurableJobDatabase, DurableJobQueue, QueueRecord, unix_now, utc_now_label
from production_attempt_audit import _close_expired_attempts, ensure_attempt_audit_schema
from production_guardrails import load_or_create_rate_secret, pseudonymous_owner_key


@dataclass(frozen=True)
class QueueQuotaPolicy:
    authenticated_queued_jobs: int = 4
    guest_queued_jobs: int = 1
    global_queued_jobs: int = 12
    authenticated_running_jobs: int = 2
    guest_running_jobs: int = 1
    global_running_jobs: int = 4

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "QueueQuotaPolicy":
        source = os.environ if env is None else env

        def integer(name: str, default: int) -> int:
            return max(1, int(source.get(name, str(default)) or default))

        workers = integer("KINDLEMASTER_WORKER_PROCESSES", 1)
        return cls(
            authenticated_queued_jobs=integer("KINDLEMASTER_AUTH_QUEUED_JOBS", 4),
            guest_queued_jobs=integer("KINDLEMASTER_GUEST_QUEUED_JOBS", 1),
            global_queued_jobs=integer("KINDLEMASTER_GLOBAL_QUEUED_JOBS", 12),
            authenticated_running_jobs=integer("KINDLEMASTER_AUTH_RUNNING_JOBS", min(2, workers)),
            guest_running_jobs=integer("KINDLEMASTER_GUEST_RUNNING_JOBS", 1),
            global_running_jobs=integer("KINDLEMASTER_GLOBAL_RUNNING_JOBS", workers),
        )


def queue_depths(
    database: DurableJobDatabase,
    *,
    owner_key: str = "",
) -> dict[str, int]:
    with database.connect() as connection:
        global_queued = connection.execute(
            "SELECT COUNT(*) AS count FROM durable_queue WHERE status IN ('queued','retry_wait')"
        ).fetchone()["count"]
        global_running = connection.execute(
            "SELECT COUNT(*) AS count FROM durable_queue WHERE status IN ('leased','running')"
        ).fetchone()["count"]
        if owner_key:
            owner_queued = connection.execute(
                """
                SELECT COUNT(*) AS count FROM durable_queue
                WHERE owner_key = ? AND status IN ('queued','retry_wait')
                """,
                (owner_key,),
            ).fetchone()["count"]
            owner_running = connection.execute(
                """
                SELECT COUNT(*) AS count FROM durable_queue
                WHERE owner_key = ? AND status IN ('leased','running')
                """,
                (owner_key,),
            ).fetchone()["count"]
        else:
            owner_queued = 0
            owner_running = 0
    return {
        "global_queued": int(global_queued),
        "global_running": int(global_running),
        "owner_queued": int(owner_queued),
        "owner_running": int(owner_running),
    }


def _request_owner(app_module: Any, secret: bytes, request: Any) -> tuple[str, bool]:
    try:
        auth_context = app_module._resolve_request_auth_context()
    except Exception:
        auth_context = None
    if auth_context is not None and getattr(auth_context, "authenticated", False):
        return f"user:{auth_context.user_id}", True
    return pseudonymous_owner_key(
        secret=secret,
        authorization="",
        guest_capability="",
        remote_address=str(request.remote_addr or ""),
        user_agent="",
    )


def install_queue_admission_quotas(
    app_module: Any,
    *,
    database: DurableJobDatabase,
    policy: QueueQuotaPolicy | None = None,
) -> None:
    from flask import g, request

    policy = policy or QueueQuotaPolicy.from_env()
    secret = load_or_create_rate_secret(database)

    @app_module.app.before_request
    def enforce_queue_admission_quota():
        if request.method != "POST" or not (
            request.path in {"/convert/start", "/convert"}
            or request.path.startswith("/convert/retry/")
        ):
            return None
        owner_key, authenticated = _request_owner(app_module, secret, request)
        g.kindlemaster_quota_owner_key = owner_key
        depths = queue_depths(database, owner_key=owner_key)
        owner_limit = (
            policy.authenticated_queued_jobs
            if authenticated
            else policy.guest_queued_jobs
        )
        if depths["global_queued"] >= policy.global_queued_jobs:
            response = app_module._json_error(
                "Kolejka osiągnęła bezpieczny limit oczekujących zadań.",
                error_code="global_queue_depth_exceeded",
                status_code=503,
                phase="admission",
                retryable=True,
                extra={"retry_after_seconds": 30},
            )
            response.headers["Retry-After"] = "30"
            return response
        if depths["owner_queued"] >= owner_limit:
            response = app_module._json_error(
                "Osiągnięto limit oczekujących zadań dla tego konta lub sesji.",
                error_code="owner_queue_depth_exceeded",
                status_code=429,
                phase="admission",
                retryable=True,
                extra={"retry_after_seconds": 30},
            )
            response.headers["Retry-After"] = "30"
            return response
        return None

    app_module._PRODUCTION_QUEUE_QUOTA_POLICY = policy


def _quota_claim_factory(policy: QueueQuotaPolicy):
    def quota_claim(
        self: DurableJobQueue,
        *,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> QueueRecord | None:
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
            global_running = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM durable_queue WHERE status IN ('leased','running')"
                ).fetchone()["count"]
            )
            if global_running >= policy.global_running_jobs:
                connection.commit()
                return None
            row = connection.execute(
                """
                SELECT candidate.*
                FROM durable_queue AS candidate
                WHERE candidate.cancellation_requested = 0
                  AND candidate.attempt < candidate.max_attempts
                  AND (
                    (candidate.status IN ('queued','retry_wait') AND candidate.available_at <= ?)
                    OR (
                      candidate.status IN ('leased','running')
                      AND candidate.lease_expires_at > 0
                      AND candidate.lease_expires_at <= ?
                    )
                  )
                  AND (
                    SELECT COUNT(*)
                    FROM durable_queue AS active
                    WHERE active.owner_key = candidate.owner_key
                      AND active.status IN ('leased','running')
                  ) < CASE
                    WHEN candidate.owner_key LIKE 'user:%' THEN ?
                    ELSE ?
                  END
                ORDER BY candidate.available_at ASC, candidate.created_at ASC
                LIMIT 1
                """,
                (
                    now,
                    now,
                    policy.authenticated_running_jobs,
                    policy.guest_running_jobs,
                ),
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
                (
                    attempt,
                    normalized_worker,
                    lease_expires,
                    now,
                    now_label,
                    started_at,
                    job_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO durable_queue_attempts(
                    job_id, attempt, worker_id, status, leased_at, heartbeat_at
                ) VALUES (?, ?, ?, 'leased', ?, ?)
                """,
                (job_id, attempt, normalized_worker, now_label, now),
            )
            claimed = connection.execute(
                "SELECT * FROM durable_queue WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            connection.commit()
        return QueueRecord.from_row(claimed) if claimed is not None else None

    quota_claim._kindlemaster_queue_quota = True
    return quota_claim


def install_worker_claim_quotas(policy: QueueQuotaPolicy | None = None) -> None:
    policy = policy or QueueQuotaPolicy.from_env()
    current = DurableJobQueue.claim
    if getattr(current, "_kindlemaster_queue_quota", False):
        return
    DurableJobQueue.claim = _quota_claim_factory(policy)
