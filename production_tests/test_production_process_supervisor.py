from __future__ import annotations

import unittest

from production_server import ProcessSupervisor, process_specs


class FakeProcess:
    def __init__(self, argv: list[str], env: dict[str, str]) -> None:
        self.argv = tuple(argv)
        self.env = dict(env)
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakePopenFactory:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.processes: list[FakeProcess] = []
        self.fail_on_call = fail_on_call
        self.calls = 0

    def __call__(self, argv: list[str], *, env: dict[str, str]) -> FakeProcess:
        self.calls += 1
        if self.fail_on_call == self.calls:
            raise OSError("simulated spawn failure")
        process = FakeProcess(argv, env)
        self.processes.append(process)
        return process


class ProductionProcessSupervisorTests(unittest.TestCase):
    def test_process_specs_separate_api_and_workers(self) -> None:
        specs = process_specs(worker_count=2)

        self.assertEqual([spec.name for spec in specs], ["api", "worker-1", "worker-2"])
        self.assertTrue(specs[0].argv[-1].endswith("production_api.py"))
        self.assertEqual(specs[0].environment["KINDLEMASTER_PROCESS_ROLE"], "api")
        self.assertTrue(specs[1].argv[-1].endswith("production_worker.py"))
        self.assertEqual(specs[1].environment["KINDLEMASTER_WORKER_ID"], "worker-1")
        self.assertEqual(specs[2].environment["KINDLEMASTER_WORKER_ID"], "worker-2")

    def test_api_restart_does_not_restart_worker(self) -> None:
        factory = FakePopenFactory()
        supervisor = ProcessSupervisor(
            process_specs(worker_count=1),
            popen_factory=factory,
            monitor_seconds=0.1,
        )
        supervisor.start()
        first_api = supervisor.children["api"].process
        worker = supervisor.children["worker-1"].process
        first_api.returncode = 1

        restarted = supervisor.check_children()

        self.assertEqual(restarted, ["api"])
        self.assertIsNot(supervisor.children["api"].process, first_api)
        self.assertIs(supervisor.children["worker-1"].process, worker)
        self.assertEqual(supervisor.children["api"].restart_count, 1)
        self.assertEqual(supervisor.children["worker-1"].restart_count, 0)
        supervisor.stop()

    def test_worker_restart_does_not_restart_api(self) -> None:
        factory = FakePopenFactory()
        supervisor = ProcessSupervisor(
            process_specs(worker_count=1),
            popen_factory=factory,
            monitor_seconds=0.1,
        )
        supervisor.start()
        api = supervisor.children["api"].process
        first_worker = supervisor.children["worker-1"].process
        first_worker.returncode = 2

        restarted = supervisor.check_children()

        self.assertEqual(restarted, ["worker-1"])
        self.assertIs(supervisor.children["api"].process, api)
        self.assertIsNot(supervisor.children["worker-1"].process, first_worker)
        self.assertEqual(supervisor.children["worker-1"].restart_count, 1)
        supervisor.stop()

    def test_stop_terminates_all_active_children(self) -> None:
        factory = FakePopenFactory()
        supervisor = ProcessSupervisor(
            process_specs(worker_count=2),
            popen_factory=factory,
            shutdown_seconds=1,
        )
        supervisor.start()

        supervisor.stop()

        self.assertTrue(supervisor.stop_event.is_set())
        self.assertEqual(len(factory.processes), 3)
        self.assertTrue(all(process.terminated for process in factory.processes))
        self.assertTrue(all(process.returncode == 0 for process in factory.processes))

    def test_partial_startup_failure_cleans_already_started_processes(self) -> None:
        factory = FakePopenFactory(fail_on_call=2)
        supervisor = ProcessSupervisor(
            process_specs(worker_count=2),
            popen_factory=factory,
            shutdown_seconds=1,
        )

        with self.assertRaisesRegex(OSError, "simulated spawn failure"):
            supervisor.run_forever()

        self.assertEqual(len(factory.processes), 1)
        self.assertTrue(factory.processes[0].terminated)
        self.assertEqual(factory.processes[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()
