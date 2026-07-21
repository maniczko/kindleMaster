from __future__ import annotations

import os
from typing import Any

import app as app_module
from production_api_policy import install_migrated_production_runtime
from production_attempt_api import install_attempt_history_api
from production_attempt_audit import install_durable_attempt_audit
from production_capacity_guard import MemoryAdmissionPolicy, install_memory_admission_guard
from production_guardrails import ProductionGuardrailPolicy, install_production_guardrails
from production_queue_projection import install_queue_state_projection
from production_queue_quotas import QueueQuotaPolicy, install_queue_admission_quotas
from production_runtime import durable_runtime_enabled
from production_security_events import install_admission_security_logging
from production_sqlite_connections import install_closing_sqlite_connections
from production_upload_limits import install_upload_limit_policy


def configure_production_api(module: Any = app_module) -> dict[str, int]:
    if not durable_runtime_enabled():
        raise RuntimeError(
            "production_api.py requires KINDLEMASTER_DURABLE_RUNTIME=1; "
            "use `python kindlemaster.py serve` for local thread mode."
        )
    install_closing_sqlite_connections()
    queue, migration = install_migrated_production_runtime(module)
    install_durable_attempt_audit(queue.database)
    install_queue_state_projection(queue.database)
    install_attempt_history_api(module, queue)

    guardrail_policy = ProductionGuardrailPolicy.from_env()
    queue_policy = QueueQuotaPolicy.from_env()
    memory_policy = MemoryAdmissionPolicy.from_env()
    request_limit = install_upload_limit_policy(
        module,
        max_upload_bytes=guardrail_policy.max_upload_bytes,
    )
    install_queue_admission_quotas(
        module,
        database=queue.database,
        policy=queue_policy,
    )
    install_memory_admission_guard(module, policy=memory_policy)
    install_production_guardrails(
        module,
        database=queue.database,
        queue=queue,
        policy=guardrail_policy,
    )
    install_admission_security_logging(module)

    module.app.logger.info(
        "Durable API initialized: migrated=%s preserved=%s failed=%s max_request_bytes=%s",
        migration["migrated"],
        migration["preserved"],
        migration["failed"],
        request_limit,
    )
    return {
        "migrated": int(migration["migrated"]),
        "preserved": int(migration["preserved"]),
        "failed": int(migration["failed"]),
        "max_request_bytes": int(request_limit),
    }


def main() -> int:
    configure_production_api()
    from waitress import serve

    serve(
        app_module.app,
        host=os.environ.get("KINDLEMASTER_BIND_HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", os.environ.get("KINDLEMASTER_PORT", "5001")) or 5001),
        threads=max(4, int(os.environ.get("KINDLEMASTER_API_THREADS", "8") or 8)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
