from __future__ import annotations

import os
import signal

import app as app_module
from durable_job_queue import DurableJobDatabase, DurableJobQueue
from production_runtime import DurableConversionWorker, durable_database_path, install_sqlite_job_store


def main() -> int:
    database = DurableJobDatabase(durable_database_path())
    install_sqlite_job_store(app_module, database=database)
    queue = DurableJobQueue(database)
    worker = DurableConversionWorker(
        app_module,
        queue,
        worker_name=os.environ.get("KINDLEMASTER_WORKER_ID") or None,
        lease_seconds=int(os.environ.get("KINDLEMASTER_JOB_LEASE_SECONDS", "180") or 180),
        poll_seconds=float(os.environ.get("KINDLEMASTER_WORKER_POLL_SECONDS", "1") or 1),
        heartbeat_seconds=float(os.environ.get("KINDLEMASTER_JOB_HEARTBEAT_SECONDS", "10") or 10),
    )

    def stop_handler(_signum: int, _frame: object) -> None:
        worker.stop()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
