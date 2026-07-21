from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import app as app_module
from production_api_policy import install_migrated_production_runtime
from production_capacity_guard import MemoryAdmissionPolicy, install_memory_admission_guard
from production_guardrails import ProductionGuardrailPolicy, install_production_guardrails
from production_runtime import durable_runtime_enabled


def _worker_count() -> int:
    return max(1, min(8, int(os.environ.get("KINDLEMASTER_WORKER_PROCESSES", "1") or 1)))


class WorkerSupervisor:
    def __init__(self, count: int) -> None:
        self.count = count
        self.processes: list[subprocess.Popen[bytes]] = []
        self.stop_event = threading.Event()
        self.monitor_thread: threading.Thread | None = None

    def start(self) -> None:
        for index in range(self.count):
            self.processes.append(self._spawn(index))
        self.monitor_thread = threading.Thread(
            target=self._monitor,
            daemon=True,
            name="kindlemaster-worker-supervisor",
        )
        self.monitor_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 15
        for process in self.processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                process.kill()

    def _spawn(self, index: int) -> subprocess.Popen[bytes]:
        environment = os.environ.copy()
        environment["KINDLEMASTER_WORKER_ID"] = f"worker-{index + 1}"
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("production_worker.py"))],
            env=environment,
        )

    def _monitor(self) -> None:
        while not self.stop_event.wait(2):
            for index, process in enumerate(list(self.processes)):
                if process.poll() is None:
                    continue
                if self.stop_event.is_set():
                    return
                self.processes[index] = self._spawn(index)


def main() -> int:
    supervisor: WorkerSupervisor | None = None
    if durable_runtime_enabled():
        queue, migration = install_migrated_production_runtime(app_module)
        guardrail_policy = ProductionGuardrailPolicy.from_env()
        memory_policy = MemoryAdmissionPolicy.from_env()
        app_module.app.config["MAX_CONTENT_LENGTH"] = guardrail_policy.max_upload_bytes
        install_memory_admission_guard(app_module, policy=memory_policy)
        install_production_guardrails(
            app_module,
            database=app_module._DURABLE_JOB_DATABASE,
            queue=queue,
            policy=guardrail_policy,
        )
        app_module.app.logger.info(
            "Durable runtime initialized: migrated=%s preserved=%s failed=%s",
            migration["migrated"],
            migration["preserved"],
            migration["failed"],
        )
        supervisor = WorkerSupervisor(_worker_count())
        supervisor.start()

    def shutdown(_signum: int, _frame: object) -> None:
        if supervisor is not None:
            supervisor.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        from waitress import serve

        serve(
            app_module.app,
            host=os.environ.get("KINDLEMASTER_BIND_HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", os.environ.get("KINDLEMASTER_PORT", "5001")) or 5001),
            threads=max(4, int(os.environ.get("KINDLEMASTER_API_THREADS", "8") or 8)),
        )
    finally:
        if supervisor is not None:
            supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
