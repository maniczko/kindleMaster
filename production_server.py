from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from production_runtime import durable_runtime_enabled


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]


@dataclass
class ManagedProcess:
    spec: ProcessSpec
    process: subprocess.Popen[bytes]
    restart_count: int = 0


PopenFactory = Callable[..., subprocess.Popen[bytes]]


def _worker_count() -> int:
    return max(1, min(8, int(os.environ.get("KINDLEMASTER_WORKER_PROCESSES", "1") or 1)))


def process_specs(worker_count: int | None = None) -> tuple[ProcessSpec, ...]:
    count = _worker_count() if worker_count is None else max(1, min(8, int(worker_count)))
    root = Path(__file__).resolve().parent
    specs: list[ProcessSpec] = [
        ProcessSpec(
            name="api",
            argv=(sys.executable, str(root / "production_api.py")),
            environment={"KINDLEMASTER_PROCESS_ROLE": "api"},
        )
    ]
    for index in range(count):
        worker_name = f"worker-{index + 1}"
        specs.append(
            ProcessSpec(
                name=worker_name,
                argv=(sys.executable, str(root / "production_worker.py")),
                environment={
                    "KINDLEMASTER_PROCESS_ROLE": "worker",
                    "KINDLEMASTER_WORKER_ID": worker_name,
                },
            )
        )
    return tuple(specs)


class ProcessSupervisor:
    def __init__(
        self,
        specs: Sequence[ProcessSpec],
        *,
        popen_factory: PopenFactory = subprocess.Popen,
        monitor_seconds: float = 2.0,
        shutdown_seconds: float = 15.0,
    ) -> None:
        if not specs:
            raise ValueError("At least one managed process is required.")
        names = [spec.name for spec in specs]
        if len(names) != len(set(names)):
            raise ValueError("Managed process names must be unique.")
        self.specs = tuple(specs)
        self.popen_factory = popen_factory
        self.monitor_seconds = max(0.1, float(monitor_seconds))
        self.shutdown_seconds = max(1.0, float(shutdown_seconds))
        self.children: dict[str, ManagedProcess] = {}
        self.stop_event = threading.Event()

    def _spawn(self, spec: ProcessSpec, *, restart_count: int = 0) -> ManagedProcess:
        environment = os.environ.copy()
        environment.update({str(key): str(value) for key, value in spec.environment.items()})
        process = self.popen_factory(list(spec.argv), env=environment)
        return ManagedProcess(spec=spec, process=process, restart_count=restart_count)

    def start(self) -> None:
        for spec in self.specs:
            self.children[spec.name] = self._spawn(spec)

    def check_children(self) -> list[str]:
        restarted: list[str] = []
        if self.stop_event.is_set():
            return restarted
        for name, managed in list(self.children.items()):
            if managed.process.poll() is None:
                continue
            replacement = self._spawn(
                managed.spec,
                restart_count=managed.restart_count + 1,
            )
            self.children[name] = replacement
            restarted.append(name)
        return restarted

    def request_stop(self) -> None:
        self.stop_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        active = [managed.process for managed in self.children.values() if managed.process.poll() is None]
        for process in active:
            try:
                process.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + self.shutdown_seconds
        for process in active:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
                continue
            except subprocess.TimeoutExpired:
                pass
            except OSError:
                continue
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def run_forever(self) -> None:
        try:
            self.start()
            while not self.stop_event.wait(self.monitor_seconds):
                self.check_children()
        finally:
            self.stop()


def main() -> int:
    if not durable_runtime_enabled():
        raise RuntimeError(
            "production_server.py requires KINDLEMASTER_DURABLE_RUNTIME=1; "
            "use `python kindlemaster.py serve` for local thread mode."
        )
    supervisor = ProcessSupervisor(process_specs())

    def shutdown(_signum: int, _frame: object) -> None:
        supervisor.request_stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    supervisor.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
