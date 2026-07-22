from __future__ import annotations

import hashlib
import os
import socket
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

from durable_job_queue import (
    DurableJobDatabase,
    DurableJobQueue,
    SQLiteConversionJobStore,
    SQLiteJobMapping,
)

DEFAULT_DATABASE_PATH = "/data/kindlemaster/runtime.sqlite3"
NON_RETRYABLE_ERROR_CODES = frozenset(
    {
        "conversion_quality_gate_failed",
        "interactive_runtime_budget_exceeded",
        "invalid_delivery_request",
        "invalid_profile_request",
        "missing_output",
        "unsupported_report_format",
        "upload_failed",
        "unsupported_source_type",
        "invalid_source_document",
        "password_protected_pdf",
        "input_policy_violation",
    }
)


def durable_database_path(env: Mapping[str, str] | None = None) -> Path:
    source = os.environ if env is None else env
    return Path(str(source.get("KINDLEMASTER_DURABLE_DB_PATH") or DEFAULT_DATABASE_PATH))


def durable_runtime_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    value = str(source.get("KINDLEMASTER_DURABLE_RUNTIME", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def install_sqlite_job_store(app_module: ModuleType, *, database: DurableJobDatabase | None = None) -> DurableJobDatabase:
    database = database or DurableJobDatabase(durable_database_path())
    store = SQLiteConversionJobStore(database)
    app_module._CONVERSION_JOBS = SQLiteJobMapping(database)
    app_module._CONVERSION_JOB_STORE = store
    return database


def _safe_owner_key(*, cloud_user_id: str, job_id: str) -> str:
    normalized_user = str(cloud_user_id or "").strip()
    if normalized_user:
        return f"user:{normalized_user}"
    # Anonymous ownership is finalized by #341. Until then the durable queue uses
    # a job-scoped owner class and never treats it as authorization.
    return f"guest-job:{str(job_id or '').strip()}"


def _request_idempotency_key() -> str:
    try:
        from flask import has_request_context, request

        if has_request_context():
            return str(request.headers.get("Idempotency-Key") or "").strip()[:200]
    except Exception:
        return ""
    return ""


def install_durable_submission(
    app_module: ModuleType,
    *,
    database: DurableJobDatabase | None = None,
) -> DurableJobQueue:
    database = database or install_sqlite_job_store(app_module)
    queue = DurableJobQueue(database)
    max_attempts = max(1, int(os.environ.get("KINDLEMASTER_JOB_MAX_ATTEMPTS", "3") or 3))

    if getattr(app_module, "_KINDLEMASTER_ORIGINAL_SPAWN", None) is None:
        app_module._KINDLEMASTER_ORIGINAL_SPAWN = app_module._spawn_conversion_job

    def enqueue_conversion_job(**kwargs: Any) -> None:
        job_id = str(kwargs.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("Durable conversion submission requires job_id.")
        owner_key = _safe_owner_key(
            cloud_user_id=str(kwargs.get("cloud_user_id") or ""),
            job_id=job_id,
        )
        payload = {key: value for key, value in kwargs.items() if key != "cloud_token"}
        payload["cloud_token"] = ""
        result = queue.enqueue(
            job_id=job_id,
            payload=payload,
            owner_key=owner_key,
            idempotency_key=_request_idempotency_key(),
            max_attempts=max_attempts,
        )
        if result.created:
            app_module._set_conversion_job(
                job_id,
                runtime_queue={
                    "provider": "sqlite-worker",
                    "status": result.record.status,
                    "attempt": result.record.attempt,
                    "max_attempts": result.record.max_attempts,
                    "database": str(database.path),
                },
            )
            return

        canonical_job_id = result.record.job_id
        if canonical_job_id != job_id:
            duplicate = app_module._CONVERSION_JOB_STORE.get(job_id) or {}
            source_path = str(duplicate.get("source_path") or kwargs.get("source_path") or "").strip()
            if source_path:
                try:
                    Path(source_path).unlink(missing_ok=True)
                except OSError:
                    # Duplicate cleanup must not fail the canonical enqueue.
                    pass
            app_module._CONVERSION_JOB_STORE.delete(job_id)
            try:
                from flask import g, has_request_context

                if has_request_context():
                    g.kindlemaster_canonical_job_id = canonical_job_id
            except Exception:
                # Flask request context is optional in worker-side calls.
                pass

    app_module._spawn_conversion_job = enqueue_conversion_job
    app_module._DURABLE_JOB_DATABASE = database
    app_module._DURABLE_JOB_QUEUE = queue
    return queue


def install_idempotent_response_replay(app_module: ModuleType, queue: DurableJobQueue) -> None:
    """Replay canonical job responses when concurrent duplicate submissions race."""

    try:
        from flask import g, has_request_context, jsonify, request
    except Exception:
        return

    original = app_module.app.view_functions.get("convert_start")
    if original is None or getattr(original, "_kindlemaster_durable_wrapped", False):
        return

    def wrapped_convert_start(*args: Any, **kwargs: Any):
        if has_request_context():
            key = str(request.headers.get("Idempotency-Key") or "").strip()[:200]
            if key:
                auth_context = app_module._resolve_request_auth_context()
                owner_key = (
                    f"user:{auth_context.user_id}"
                    if getattr(auth_context, "authenticated", False)
                    else ""
                )
                if owner_key:
                    existing = queue.find_idempotent(owner_key, key)
                    if existing is not None:
                        job = app_module._CONVERSION_JOB_STORE.get(existing.job_id) or {}
                        response = jsonify(
                            {
                                "success": True,
                                "job_id": existing.job_id,
                                "status": str(job.get("status") or existing.status),
                                "message": str(job.get("message") or "Żądanie zostało już przyjęte."),
                                "idempotent_replay": True,
                                "poll_after_ms": app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS,
                            }
                        )
                        response.status_code = 200 if existing.status in {"succeeded", "failed", "dead_letter", "cancelled"} else 202
                        app_module.apply_no_store_headers(response.headers)
                        return response
        response = original(*args, **kwargs)
        canonical_job_id = getattr(g, "kindlemaster_canonical_job_id", "") if has_request_context() else ""
        if not canonical_job_id:
            return response
        job = app_module._CONVERSION_JOB_STORE.get(canonical_job_id) or {}
        replay = jsonify(
            {
                "success": True,
                "job_id": canonical_job_id,
                "status": str(job.get("status") or "queued"),
                "message": str(job.get("message") or "Żądanie zostało już przyjęte."),
                "idempotent_replay": True,
                "poll_after_ms": app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS,
            }
        )
        replay.status_code = 202
        app_module.apply_no_store_headers(replay.headers)
        return replay

    wrapped_convert_start._kindlemaster_durable_wrapped = True
    app_module.app.view_functions["convert_start"] = wrapped_convert_start


def install_production_runtime(app_module: ModuleType) -> DurableJobQueue:
    database = install_sqlite_job_store(app_module)
    queue = install_durable_submission(app_module, database=database)
    install_idempotent_response_replay(app_module, queue)
    return queue


def _restore_source_path(app_module: ModuleType, job_id: str, payload: dict[str, Any]) -> str:
    job = app_module._CONVERSION_JOB_STORE.get(job_id) or {}
    source_path = str(job.get("source_path") or payload.get("source_path") or "").strip()
    if source_path and Path(source_path).is_file():
        payload["source_path"] = source_path
        return source_path

    input_bytes, input_filename = app_module._read_retry_input_artifact(job)
    if not input_bytes:
        raise RuntimeError("Durable worker cannot restore the retained input artifact.")
    source_type = str(payload.get("source_type") or app_module.detect_supported_source_type(input_filename) or "").strip()
    if source_type not in {"pdf", "docx"}:
        raise RuntimeError("Durable worker cannot determine the retained input type.")
    restored_path = Path(app_module.UPLOAD_DIR) / f"{job_id}.{source_type}"
    restored_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = restored_path.with_suffix(restored_path.suffix + ".tmp")
    temp_path.write_bytes(input_bytes)
    temp_path.replace(restored_path)
    payload["source_path"] = str(restored_path)
    app_module._CONVERSION_JOB_STORE.update(job_id, {"source_path": str(restored_path)})
    return str(restored_path)


def _retryable_job_failure(job: Mapping[str, Any]) -> bool:
    error_code = str(job.get("error_code") or "").strip().lower()
    if error_code in NON_RETRYABLE_ERROR_CODES:
        return False
    return str(job.get("status") or "").strip().lower() in {"failed", "timed_out"}


def worker_id(prefix: str = "worker") -> str:
    return f"{prefix}-{socket.gethostname()}-{os.getpid()}-{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}"


class DurableConversionWorker:
    def __init__(
        self,
        app_module: ModuleType,
        queue: DurableJobQueue,
        *,
        worker_name: str | None = None,
        lease_seconds: int = 180,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        self.app_module = app_module
        self.queue = queue
        self.worker_name = worker_name or worker_id()
        self.lease_seconds = max(60, int(lease_seconds))
        self.poll_seconds = max(0.1, float(poll_seconds))
        self.heartbeat_seconds = max(1.0, float(heartbeat_seconds))
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            record = self.queue.claim(worker_id=self.worker_name, lease_seconds=self.lease_seconds)
            if record is None:
                self._stop.wait(self.poll_seconds)
                continue
            self.run_claimed(record.job_id)

    def run_claimed(self, job_id: str) -> None:
        record = self.queue.get(job_id)
        if record is None or record.lease_owner != self.worker_name:
            return
        payload = dict(record.payload)
        try:
            self.queue.mark_running(job_id, worker_id=self.worker_name, lease_seconds=self.lease_seconds)
            _restore_source_path(self.app_module, job_id, payload)
            self.app_module._CONVERSION_JOB_STORE.update(
                job_id,
                {
                    "status": "queued",
                    "message": f"Zadanie podjęte przez trwały worker (próba {record.attempt}).",
                    "error": "",
                    "error_code": "",
                    "runtime_queue": {
                        "provider": "sqlite-worker",
                        "status": "running",
                        "worker_id": self.worker_name,
                        "attempt": record.attempt,
                        "max_attempts": record.max_attempts,
                    },
                },
            )
            original_spawn = getattr(self.app_module, "_KINDLEMASTER_ORIGINAL_SPAWN", None)
            if original_spawn is None:
                original_spawn = self.app_module._spawn_conversion_job
            payload["cloud_token"] = ""
            original_spawn(**payload)
            self._wait_for_terminal(job_id)
        except Exception as error:
            self._record_worker_failure(job_id, error, retryable=True)

    def _wait_for_terminal(self, job_id: str) -> None:
        next_heartbeat = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_heartbeat:
                if not self.queue.heartbeat(
                    job_id,
                    worker_id=self.worker_name,
                    lease_seconds=self.lease_seconds,
                ):
                    raise RuntimeError("Durable worker lost the queue lease.")
                next_heartbeat = now + self.heartbeat_seconds
            queue_record = self.queue.get(job_id)
            if queue_record and queue_record.cancellation_requested:
                self.app_module._CONVERSION_JOB_STORE.update(
                    job_id,
                    {
                        "status": "cancelled",
                        "message": "Konwersja anulowana.",
                        "error_code": "cancelled",
                    },
                )
                self.queue.mark_cancelled(job_id, worker_id=self.worker_name)
                return
            job = self.app_module._CONVERSION_JOB_STORE.get(job_id) or {}
            status = str(job.get("status") or "").strip().lower()
            if status == "ready":
                self.queue.complete(
                    job_id,
                    worker_id=self.worker_name,
                    result={
                        "status": "ready",
                        "output_size_bytes": int(job.get("output_size_bytes") or 0),
                    },
                )
                self.app_module._CONVERSION_JOB_STORE.update(
                    job_id,
                    {"runtime_queue": {**dict(job.get("runtime_queue") or {}), "status": "succeeded"}},
                )
                return
            if status in {"failed", "timed_out", "cancelled"}:
                error = str(job.get("error") or job.get("message") or status)
                self._record_worker_failure(job_id, RuntimeError(error), retryable=_retryable_job_failure(job))
                return
            self._stop.wait(self.poll_seconds)

    def _record_worker_failure(self, job_id: str, error: Exception, *, retryable: bool) -> None:
        try:
            queue_record = self.queue.fail(
                job_id,
                worker_id=self.worker_name,
                error=str(error),
                retryable=retryable,
                base_backoff_seconds=max(1, int(os.environ.get("KINDLEMASTER_JOB_RETRY_BACKOFF_SECONDS", "10") or 10)),
            )
        except Exception:
            return
        job = self.app_module._CONVERSION_JOB_STORE.get(job_id) or {}
        next_status = "queued" if queue_record.status == "retry_wait" else "failed"
        self.app_module._CONVERSION_JOB_STORE.update(
            job_id,
            {
                "status": next_status,
                "message": (
                    f"Konwersja oczekuje na ponowną próbę {queue_record.attempt + 1}/{queue_record.max_attempts}."
                    if queue_record.status == "retry_wait"
                    else "Konwersja zakończyła się błędem trwałego workera."
                ),
                "error": str(error),
                "error_code": str(job.get("error_code") or "durable_worker_failed"),
                "runtime_queue": {
                    **dict(job.get("runtime_queue") or {}),
                    "status": queue_record.status,
                    "attempt": queue_record.attempt,
                    "max_attempts": queue_record.max_attempts,
                    "last_error": str(error)[:500],
                },
            },
        )
