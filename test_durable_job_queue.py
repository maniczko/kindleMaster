from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import threading
import unittest

from durable_job_queue import DurableJobQueue, DurableJobStatus, InvalidJobTransition


class DurableJobQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = DurableJobQueue(Path(self.temp.name) / "jobs.sqlite3")
        self.now = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)

    def tearDown(self):
        self.temp.cleanup()

    def test_idempotency_is_scoped_to_owner(self):
        first, created = self.queue.enqueue(owner_id="owner-a", payload={"file": "a"}, idempotency_key="same", now=self.now)
        repeated, repeated_created = self.queue.enqueue(owner_id="owner-a", payload={"file": "b"}, idempotency_key="same", now=self.now)
        other, other_created = self.queue.enqueue(owner_id="owner-b", payload={"file": "b"}, idempotency_key="same", now=self.now)
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertEqual(first.job_id, repeated.job_id)
        self.assertTrue(other_created)
        self.assertNotEqual(first.job_id, other.job_id)

    def test_only_one_worker_claims_a_job(self):
        job, _ = self.queue.enqueue(owner_id="owner", payload={}, now=self.now)
        claimed = []
        barrier = threading.Barrier(2)

        def claim(worker):
            barrier.wait()
            claimed.append(self.queue.claim(worker_id=worker, now=self.now))

        threads = [threading.Thread(target=claim, args=("worker-a",)), threading.Thread(target=claim, args=("worker-b",))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [item for item in claimed if item is not None]
        self.assertEqual(1, len(winners))
        self.assertEqual(job.job_id, winners[0].job_id)

    def test_expired_lease_is_recovered(self):
        job, _ = self.queue.enqueue(owner_id="owner", payload={}, now=self.now)
        claimed = self.queue.claim(worker_id="dead-worker", lease_seconds=10, now=self.now)
        self.assertEqual(DurableJobStatus.LEASED, claimed.status)
        self.assertEqual(1, self.queue.requeue_expired(now=self.now + timedelta(seconds=11)))
        reclaimed = self.queue.claim(worker_id="new-worker", now=self.now + timedelta(seconds=12))
        self.assertEqual(job.job_id, reclaimed.job_id)
        self.assertEqual(2, reclaimed.attempt)

    def test_retry_backoff_and_dead_letter(self):
        job, _ = self.queue.enqueue(owner_id="owner", payload={}, max_attempts=2, now=self.now)
        first = self.queue.claim(worker_id="worker", now=self.now)
        self.queue.start(first.job_id, worker_id="worker", now=self.now)
        retry = self.queue.fail(first.job_id, worker_id="worker", retryable=True, error_code="temporary", backoff_seconds=30, now=self.now)
        self.assertEqual(DurableJobStatus.QUEUED, retry.status)
        self.assertIsNone(self.queue.claim(worker_id="other", now=self.now + timedelta(seconds=29)))
        second = self.queue.claim(worker_id="other", now=self.now + timedelta(seconds=31))
        self.queue.start(second.job_id, worker_id="other", now=self.now + timedelta(seconds=31))
        dead = self.queue.fail(second.job_id, worker_id="other", retryable=True, error_code="temporary", now=self.now + timedelta(seconds=32))
        self.assertEqual(DurableJobStatus.DEAD_LETTER, dead.status)

    def test_owner_scoped_cancel_and_read(self):
        job, _ = self.queue.enqueue(owner_id="owner-a", payload={}, now=self.now)
        self.assertIsNone(self.queue.get(job.job_id, owner_id="owner-b"))
        with self.assertRaises(KeyError):
            self.queue.cancel(job.job_id, owner_id="owner-b", now=self.now)
        cancelled = self.queue.cancel(job.job_id, owner_id="owner-a", now=self.now)
        self.assertEqual(DurableJobStatus.CANCELLED, cancelled.status)

    def test_invalid_transition_fails_closed(self):
        job, _ = self.queue.enqueue(owner_id="owner", payload={}, now=self.now)
        claimed = self.queue.claim(worker_id="worker", now=self.now)
        self.queue.start(claimed.job_id, worker_id="worker", now=self.now)
        self.queue.complete(job.job_id, worker_id="worker", now=self.now)
        with self.assertRaises((KeyError, InvalidJobTransition)):
            self.queue.start(job.job_id, worker_id="worker", now=self.now)


if __name__ == "__main__":
    unittest.main()
