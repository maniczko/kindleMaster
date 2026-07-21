from __future__ import annotations

from collections.abc import Mapping
from types import ModuleType
from typing import Any

from durable_job_queue import DurableJobDatabase, DurableJobQueue
from production_runtime import install_production_runtime, install_sqlite_job_store


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


def install_migrated_production_runtime(app_module: ModuleType) -> tuple[DurableJobQueue, dict[str, int]]:
    legacy_jobs = capture_legacy_jobs(app_module)
    queue = install_production_runtime(app_module)
    migration = migrate_legacy_jobs(app_module, legacy_jobs)
    install_async_only_conversion_policy(app_module)
    return queue, migration


def install_migrated_sqlite_store(
    app_module: ModuleType,
    *,
    database: DurableJobDatabase,
) -> dict[str, int]:
    legacy_jobs = capture_legacy_jobs(app_module)
    install_sqlite_job_store(app_module, database=database)
    return migrate_legacy_jobs(app_module, legacy_jobs)
