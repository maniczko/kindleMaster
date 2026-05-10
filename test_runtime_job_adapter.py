from __future__ import annotations

import unittest

from runtime_job_adapter import (
    LocalRuntimeJobAdapter,
    ReplayableCommand,
    RetryPolicy,
    RuntimeJobExecutionError,
    RuntimeJobStatus,
)


class RuntimeJobAdapterTests(unittest.TestCase):
    def test_local_adapter_preserves_local_provider_defaults(self) -> None:
        adapter = LocalRuntimeJobAdapter()
        handle = adapter.submit(
            ReplayableCommand(
                name="convert",
                args=("sample.pdf",),
                kwargs={"profile": "auto-premium"},
                context={"job_id": "job-1"},
            )
        )

        metadata = handle.to_metadata()

        self.assertEqual(metadata["provider"], "local")
        self.assertEqual(metadata["status"], "queued")
        self.assertEqual(metadata["external_id"], "")
        self.assertEqual(metadata["retry_policy"]["max_attempts"], 1)
        self.assertEqual(metadata["timeout_seconds"], 1800)
        self.assertEqual(metadata["replay"]["command"]["name"], "convert")
        self.assertEqual(metadata["replay"]["context"]["job_id"], "job-1")

    def test_local_adapter_tracks_status_updates_without_external_runtime(self) -> None:
        adapter = LocalRuntimeJobAdapter()
        handle = adapter.submit(ReplayableCommand(name="convert"))

        running = adapter.update_status(handle.job_id, RuntimeJobStatus.RUNNING, message="Started")
        ready = adapter.update_status(handle.job_id, RuntimeJobStatus.SUCCEEDED, external_id="local-finished")

        self.assertEqual(running.status, RuntimeJobStatus.RUNNING)
        self.assertEqual(running.message, "Started")
        self.assertEqual(ready.status, RuntimeJobStatus.SUCCEEDED)
        self.assertEqual(ready.external_id, "local-finished")
        self.assertEqual(adapter.get(handle.job_id), ready)

    def test_retry_policy_and_timeout_are_metadata_only(self) -> None:
        adapter = LocalRuntimeJobAdapter(
            retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=5, retryable_statuses=("failed",)),
            timeout_seconds=90,
        )
        handle = adapter.submit(ReplayableCommand(name="convert", context={"source_type": "pdf"}))

        metadata = handle.to_metadata()

        self.assertEqual(metadata["retry_policy"]["max_attempts"], 3)
        self.assertEqual(metadata["retry_policy"]["backoff_seconds"], 5)
        self.assertEqual(metadata["retry_policy"]["retryable_statuses"], ["failed"])
        self.assertEqual(metadata["timeout_seconds"], 90)
        self.assertEqual(metadata["replay"]["context"]["source_type"], "pdf")

    def test_local_adapter_can_execute_runner_and_keep_replay_metadata(self) -> None:
        adapter = LocalRuntimeJobAdapter()
        command = ReplayableCommand(name="convert", args=("sample.pdf",), context={"job_id": "job-2"})

        handle, result = adapter.run_local(command, lambda replay: {"name": replay.name})

        self.assertEqual(result, {"name": "convert"})
        self.assertEqual(handle.status, RuntimeJobStatus.SUCCEEDED)
        self.assertEqual(handle.to_metadata()["replay"]["context"]["job_id"], "job-2")

    def test_local_adapter_marks_failed_runner_before_reraising(self) -> None:
        adapter = LocalRuntimeJobAdapter()

        def fail(_: ReplayableCommand) -> None:
            raise ValueError("boom")

        with self.assertRaises(RuntimeJobExecutionError):
            adapter.run_local(ReplayableCommand(name="convert"), fail, job_id="job-3")

        failed = adapter.get("job-3")
        self.assertIsNotNone(failed)
        self.assertEqual(failed.status, RuntimeJobStatus.FAILED)
        self.assertEqual(failed.error, "boom")


if __name__ == "__main__":
    unittest.main()
