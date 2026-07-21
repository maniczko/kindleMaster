from __future__ import annotations

import os

import app as app_module
from production_api_policy import install_migrated_production_runtime
from production_runtime import durable_runtime_enabled


def configure_production_api() -> None:
    if not durable_runtime_enabled():
        raise RuntimeError(
            "production_api.py requires KINDLEMASTER_DURABLE_RUNTIME=1; "
            "use `python kindlemaster.py serve` for local thread mode."
        )
    _queue, migration = install_migrated_production_runtime(app_module)
    app_module.app.logger.info(
        "Durable API initialized: migrated=%s preserved=%s failed=%s",
        migration["migrated"],
        migration["preserved"],
        migration["failed"],
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
