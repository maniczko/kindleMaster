from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from durable_job_queue import DurableJobDatabase, DurableJobQueue, SQLiteConversionJobStore


class DurableJobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DurableJobDatabase(Path(self.temp_dir.name) / "runtime.sqlite3")
        self.queue = DurableJobQueue(self.database)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_idempotency_returns_canonical_job(self) -> None:
        first = self.queue.enqueue(
            job_id="job-a",
            payload={"source_path": "a.pdf"},
            owner_key="user:1",
            idempotency_key="same",
        )
        second = self.queue.enqueue(
            job_id="job-b",
            payload={"source_path": "b.pdf"},
            owner_key="user:1",
            idempotency_key="same",
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.record.job_id, "job-a")

    def test_claim_is_atomic_and_lease_can_be_recovered(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=3)
        first = self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(first)
        self.assertEqual(first.attempt, 1)
        self.assertIsNone(self.queue.claim(worker_id="worker-b", lease_seconds=30))
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE durable_queue SET lease_expires_at = ? WHERE job_id = ?",
                (time.time() - 1, "job-a"),
            )
        recovered = self.queue.claim(worker_id="worker-b", lease_seconds=30)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.lease_owner, "worker-b")
        self.assertEqual(recovered.attempt, 2)

    def test_retry_backoff_and_dead_letter(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=2)
        first = self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(first)
        self.queue.mark_running("job-a", worker_id="worker-a", lease_seconds=30)
        retry = self.queue.fail(
            "job-a",
            worker_id="worker-a",
            error="temporary",
            retryable=True,
            base_backoff_seconds=1,
        )
        self.assertEqual(retry.status, "retry_wait")
        with self.database.connect() as connection:
            connection.execute("UPDATE durable_queue SET available_at = 0 WHERE job_id = 'job-a'")
        second = self.queue.claim(worker_id="worker-b", lease_seconds=30)
        self.assertIsNotNone(second)
        terminal = self.queue.fail(
            "job-a",
            worker_id="worker-b",
            error="still broken",
            retryable=True,
            base_backoff_seconds=1,
        )
        self.assertEqual(terminal.status, "dead_letter")

    def test_cancel_queued_job(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={})
        cancelled = self.queue.request_cancel("job-a")
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(self.queue.claim(worker_id="worker-a"))

    def test_sqlite_conversion_store_merges_updates(self) -> None:
        first = SQLiteConversionJobStore(self.database)
        second = SQLiteConversionJobStore(self.database)
        first.create({"job_id": "job-a", "status": "queued", "metadata": {"a": 1}})
        second.update("job-a", {"status": "running"})
        first.update("job-a", {"message": "working"})
        job = second.get("job-a")
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["message"], "working")
        self.assertEqual(job["metadata"], {"a": 1})


if __name__ == "__main__":
    unittest.main()
