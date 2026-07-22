from __future__ import annotations

import multiprocessing
import tempfile
import threading
import time
import unittest
from pathlib import Path

from durable_job_queue import DurableJobDatabase, DurableJobQueue, SQLiteConversionJobStore


def _claim_job_in_process(
    database_path: str,
    worker_id: str,
    lease_seconds: int,
    start_event,
    result_queue,
) -> None:
    database = DurableJobDatabase(Path(database_path))
    queue = DurableJobQueue(database)
    if not start_event.wait(timeout=10):
        result_queue.put({"worker_id": worker_id, "error": "start_timeout"})
        return
    try:
        record = queue.claim(worker_id=worker_id, lease_seconds=lease_seconds)
    except Exception as error:
        result_queue.put(
            {
                "worker_id": worker_id,
                "error": f"{error.__class__.__name__}: {error}",
            }
        )
        return
    result_queue.put(
        {
            "worker_id": worker_id,
            "claimed": record is not None,
            "job_id": record.job_id if record is not None else "",
            "lease_owner": record.lease_owner if record is not None else "",
            "attempt": record.attempt if record is not None else 0,
        }
    )


class DurableJobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "runtime.sqlite3"
        self.database = DurableJobDatabase(self.database_path)
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

    def test_two_workers_racing_claim_exactly_one_job(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={})
        barrier = threading.Barrier(3)
        results: list[object] = []
        errors: list[Exception] = []

        def claim(worker_id: str) -> None:
            queue = DurableJobQueue(self.database)
            barrier.wait()
            try:
                results.append(queue.claim(worker_id=worker_id, lease_seconds=30))
            except Exception as error:
                errors.append(error)

        workers = [
            threading.Thread(target=claim, args=("worker-a",)),
            threading.Thread(target=claim, args=("worker-b",)),
        ]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=5)

        self.assertEqual(errors, [])
        claimed = [record for record in results if record is not None]
        self.assertEqual(len(claimed), 1)
        self.assertIn(claimed[0].lease_owner, {"worker-a", "worker-b"})
        self.assertEqual(self.queue.get("job-a").attempt, 1)

    def test_two_processes_racing_claim_exactly_one_job(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={})
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_claim_job_in_process,
                args=(str(self.database_path), worker_id, 30, start_event, result_queue),
            )
            for worker_id in ("process-worker-a", "process-worker-b")
        ]
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)

        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertEqual([result.get("error") for result in results if result.get("error")], [])
        claimed = [result for result in results if result.get("claimed")]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["job_id"], "job-a")
        self.assertIn(claimed[0]["lease_owner"], {"process-worker-a", "process-worker-b"})
        self.assertEqual(claimed[0]["attempt"], 1)
        self.assertEqual(self.queue.get("job-a").attempt, 1)

    def test_expired_lease_from_exited_process_is_recovered(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=3)
        context = multiprocessing.get_context("spawn")

        first_event = context.Event()
        first_results = context.Queue()
        first_process = context.Process(
            target=_claim_job_in_process,
            args=(str(self.database_path), "process-worker-a", 1, first_event, first_results),
        )
        first_process.start()
        first_event.set()
        first = first_results.get(timeout=20)
        first_process.join(timeout=20)
        self.assertEqual(first_process.exitcode, 0)
        self.assertTrue(first["claimed"])
        self.assertEqual(first["attempt"], 1)

        time.sleep(1.25)

        second_event = context.Event()
        second_results = context.Queue()
        second_process = context.Process(
            target=_claim_job_in_process,
            args=(str(self.database_path), "process-worker-b", 30, second_event, second_results),
        )
        second_process.start()
        second_event.set()
        second = second_results.get(timeout=20)
        second_process.join(timeout=20)

        self.assertEqual(second_process.exitcode, 0)
        self.assertTrue(second["claimed"])
        self.assertEqual(second["job_id"], "job-a")
        self.assertEqual(second["lease_owner"], "process-worker-b")
        self.assertEqual(second["attempt"], 2)
        self.assertEqual(self.queue.get("job-a").attempt, 2)

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
