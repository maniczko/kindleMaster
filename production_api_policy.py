from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from typing import Any

from durable_job_queue import DurableJobDatabase, DurableJobQueue
from production_runtime import install_production_runtime, install_sqlite_job_store
from production_store_compat import install_sqlite_mapping_write_through


class ActiveCancellationUnsupported(RuntimeError):
    pass


def capture_legacy_jobs(app_module: ModuleType) -> dict[str, dict[str, Any]]:
    store = getattr(app_module, "_CONVERSION_JOB_STORE", None)
    if store is None or not callable(getattr(store, "snapshot", None)):
        return {}
    try:
        snapshot = store.snapshot()
    except Exception:
        return {}
    if not isinstance(snapshot, Mapping):
        return {}
    return {
        str(job_id): dict(job)
        for job_id, job in snapshot.items()
        if str(job_id or "").strip() and isinstance(job, Mapping)
    }


def migrate_legacy_jobs(app_module: ModuleType, legacy_jobs: Mapping[str, Mapping[str, Any]]) -> dict[str, int]:
    store = app_module._CONVERSION_JOB_STORE
    migrated = 0
    preserved = 0
    failed = 0
    for job_id, raw_job in legacy_jobs.items():
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            continue
        try:
            if store.get(normalized_job_id) is not None:
                preserved += 1
                continue
            payload = dict(raw_job)
            payload["job_id"] = normalized_job_id
            store.create(payload)
            migrated += 1
        except Exception:
            failed += 1
    return {"migrated": migrated, "preserved": preserved, "failed": failed}


def install_async_only_conversion_policy(app_module: ModuleType) -> None:
    app = app_module.app
    endpoint = ""
    for rule in app.url_map.iter_rules():
        if rule.rule == "/convert" and "POST" in rule.methods:
            endpoint = str(rule.endpoint)
            break
    if not endpoint:
        return
    original = app.view_functions.get(endpoint)
    if original is None or getattr(original, "_kindlemaster_async_only", False):
        return

    def async_only_conversion(*_args: Any, **_kwargs: Any):
        return app_module._json_error(
            "Synchroniczna konwersja jest wyłączona w środowisku produkcyjnym. Użyj /convert/start.",
            error_code="synchronous_conversion_disabled",
            status_code=409,
            phase="queue",
            retryable=False,
            extra={"start_url": "/convert/start"},
        )

    async_only_conversion._kindlemaster_async_only = True
    app.view_functions[endpoint] = async_only_conversion


def install_idempotent_retry_response_policy(app_module: ModuleType) -> None:
    """Return the canonical queued job when a retry request is replayed."""

    from flask import g, has_request_context, jsonify

    app = app_module.app
    endpoints: list[str] = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/convert/retry/") and "POST" in rule.methods:
            endpoints.append(str(rule.endpoint))

    for endpoint in endpoints:
        original = app.view_functions.get(endpoint)
        if original is None or getattr(original, "_kindlemaster_retry_idempotent", False):
            continue

        def wrapped_retry(*args: Any, __original=original, **kwargs: Any):
            response = __original(*args, **kwargs)
            canonical_job_id = (
                str(getattr(g, "kindlemaster_canonical_job_id", "") or "")
                if has_request_context()
                else ""
            )
            if not canonical_job_id:
                return response
            job = app_module._CONVERSION_JOB_STORE.get(canonical_job_id) or {}
            replay = jsonify(
                {
                    "success": True,
                    "job_id": canonical_job_id,
                    "status": str(job.get("status") or "queued"),
                    "message": str(job.get("message") or "Ponowienie zostało już przyjęte."),
                    "retry_of": str(job.get("retry_of") or ""),
                    "idempotent_replay": True,
                    "poll_after_ms": app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS,
                }
            )
            replay.status_code = 202
            app_module.apply_no_store_headers(replay.headers)
            return replay

        wrapped_retry._kindlemaster_retry_idempotent = True
        app.view_functions[endpoint] = wrapped_retry


def install_safe_queue_cancel_guard() -> None:
    """Prevent a cancellation flag from pretending to stop an active thread."""

    original = DurableJobQueue.request_cancel
    if getattr(original, "_kindlemaster_safe_cancel", False):
        return

    def safe_request_cancel(self: DurableJobQueue, job_id: str):
        record = self.get(job_id)
        if record is not None and record.status in {"leased", "running"}:
            raise ActiveCancellationUnsupported(
                "Active conversion cannot be cancelled safely before stage-aware hooks are available."
            )
        return original(self, job_id)

    safe_request_cancel._kindlemaster_safe_cancel = True
    DurableJobQueue.request_cancel = safe_request_cancel


def install_queued_cancellation_policy(app_module: ModuleType, queue: DurableJobQueue) -> None:
    """Expose cancellation only while a job is still safely queueable."""

    from flask import jsonify

    app = app_module.app
    endpoint = "durable_conversion_cancel"
    if endpoint in app.view_functions:
        return

    install_safe_queue_cancel_guard()

    def cancel_job(job_id: str):
        auth_context = app_module._resolve_request_auth_context()
        if getattr(auth_context, "error", ""):
            return app_module._json_auth_error(auth_context)
        job = app_module._get_conversion_job_for_auth(job_id, auth_context)
        if not job:
            return app_module._json_error(
                "Nie znaleziono zadania konwersji.",
                error_code="missing_conversion_job",
                status_code=404,
                phase="cancel",
                job_id=job_id,
                retryable=False,
            )
        record = queue.get(job_id)
        if record is None:
            return app_module._json_error(
                "Zadanie nie jest zarządzane przez trwałą kolejkę.",
                error_code="durable_queue_record_missing",
                status_code=409,
                phase="cancel",
                job_id=job_id,
                retryable=False,
            )
        if record.status in {"succeeded", "failed", "dead_letter", "cancelled"}:
            return app_module._json_error(
                "Zadanie jest już zakończone.",
                error_code="cancellation_not_applicable",
                status_code=409,
                phase="cancel",
                job_id=job_id,
                retryable=False,
            )
        try:
            cancelled = queue.request_cancel(job_id)
        except ActiveCancellationUnsupported:
            return app_module._json_error(
                "Aktywna konwersja nie może być jeszcze bezpiecznie anulowana.",
                error_code="active_cancellation_unsupported",
                status_code=409,
                phase="cancel",
                job_id=job_id,
                retryable=False,
            )
        if cancelled is None or cancelled.status != "cancelled":
            return app_module._json_error(
                "Nie udało się anulować zadania przed rozpoczęciem pracy.",
                error_code="cancellation_failed",
                status_code=409,
                phase="cancel",
                job_id=job_id,
                retryable=False,
            )
        updated = app_module._CONVERSION_JOB_STORE.update(
            job_id,
            {
                "status": "cancelled",
                "message": "Konwersja została anulowana przed rozpoczęciem pracy.",
                "error": "",
                "error_code": "cancelled",
                "runtime_queue": {
                    **dict(job.get("runtime_queue") or {}),
                    "status": "cancelled",
                    "cancellation_requested": True,
                },
            },
        )
        try:
            app_module._sync_job_to_cloud(job_id)
        except Exception:
            # Local cancellation remains authoritative when cloud sync is unavailable.
            pass
        response = jsonify(
            {
                "success": True,
                "job_id": job_id,
                "status": "cancelled",
                "message": str((updated or {}).get("message") or "Konwersja została anulowana."),
            }
        )
        app_module.apply_no_store_headers(response.headers)
        return response

    app.add_url_rule(
        "/convert/cancel/<job_id>",
        endpoint=endpoint,
        view_func=cancel_job,
        methods=["POST"],
    )


def install_worker_cloud_failure_sync(app_module: ModuleType) -> None:
    """Sync terminal/retry worker state without retaining a browser access token."""

    from production_runtime import DurableConversionWorker

    original = DurableConversionWorker._record_worker_failure
    if getattr(original, "_kindlemaster_cloud_failure_sync", False):
        return

    def wrapped_record_worker_failure(self, job_id: str, error: Exception, *, retryable: bool) -> None:
        original(self, job_id, error, retryable=retryable)
        try:
            app_module._sync_job_to_cloud(job_id)
        except Exception as sync_error:
            logger = getattr(getattr(app_module, "app", None), "logger", None)
            if logger is not None:
                logger.warning(
                    "Durable worker could not synchronize failed job %s to cloud: %s",
                    job_id,
                    sync_error.__class__.__name__,
                )

    wrapped_record_worker_failure._kindlemaster_cloud_failure_sync = True
    DurableConversionWorker._record_worker_failure = wrapped_record_worker_failure


def install_migrated_production_runtime(app_module: ModuleType) -> tuple[DurableJobQueue, dict[str, int]]:
    legacy_jobs = capture_legacy_jobs(app_module)
    install_sqlite_mapping_write_through()
    queue = install_production_runtime(app_module)
    migration = migrate_legacy_jobs(app_module, legacy_jobs)
    install_async_only_conversion_policy(app_module)
    install_idempotent_retry_response_policy(app_module)
    install_queued_cancellation_policy(app_module, queue)
    install_worker_cloud_failure_sync(app_module)
    return queue, migration


def install_migrated_sqlite_store(
    app_module: ModuleType,
    *,
    database: DurableJobDatabase,
) -> dict[str, int]:
    legacy_jobs = capture_legacy_jobs(app_module)
    install_sqlite_mapping_write_through()
    install_sqlite_job_store(app_module, database=database)
    migration = migrate_legacy_jobs(app_module, legacy_jobs)
    install_worker_cloud_failure_sync(app_module)
    return migration
