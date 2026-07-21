from __future__ import annotations

import os

import app as app_module
from production_api_policy import install_migrated_production_runtime
from production_capacity_guard import MemoryAdmissionPolicy, install_memory_admission_guard
from production_guardrails import ProductionGuardrailPolicy, install_production_guardrails
from production_runtime import durable_runtime_enabled
from production_security_events import install_admission_security_logging
from production_upload_limits import install_upload_limit_policy


def configure_production_api() -> None:
    if not durable_runtime_enabled():
        raise RuntimeError(
            "production_api.py requires KINDLEMASTER_DURABLE_RUNTIME=1; "
            "use `python kindlemaster.py serve` for local thread mode."
        )
    queue, migration = install_migrated_production_runtime(app_module)
    guardrail_policy = ProductionGuardrailPolicy.from_env()
    memory_policy = MemoryAdmissionPolicy.from_env()
    request_limit = install_upload_limit_policy(
        app_module,
        max_upload_bytes=guardrail_policy.max_upload_bytes,
    )
    install_memory_admission_guard(app_module, policy=memory_policy)
    install_production_guardrails(
        app_module,
        database=app_module._DURABLE_JOB_DATABASE,
        queue=queue,
        policy=guardrail_policy,
    )
    install_admission_security_logging(app_module)
    app_module.app.logger.info(
        "Durable API initialized: migrated=%s preserved=%s failed=%s max_request_bytes=%s",
        migration["migrated"],
        migration["preserved"],
        migration["failed"],
        request_limit,
    )


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
