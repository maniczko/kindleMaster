from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from durable_job_queue import DurableJobStatus
from durable_runtime_store import RuntimeDurableJobQueue


class RuntimeDurableStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = RuntimeDurableJobQueue(Path(self.temp.name) / "jobs.sqlite3")
        self.now = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_payload_counts_and_delete(self):
        job, _ = self.queue.enqueue(
            owner_id="owner",
            payload={"job_record": {"status": "queued"}},
            idempotency_key="key",
            now=self.now,
        )
        self.assertEqual(
            job.job_id,
            self.queue.find_by_idempotency(
                owner_id="owner", idempotency_key="key"
            ).job_id,
        )
        claimed = self.queue.claim(worker_id="worker", now=self.now)
        self.queue.start(claimed.job_id, worker_id="worker", now=self.now)
        mirrored = self.queue.heartbeat_with_payload(
            claimed.job_id,
            worker_id="worker",
            lease_seconds=120,
            payload_patch={"job_record": {"status": "running"}},
        )
        self.assertEqual("running", mirrored.payload["job_record"]["status"])
        self.assertEqual(1, self.queue.counts(owner_id="owner").active)
        ready = self.queue.complete_with_payload(
            claimed.job_id,
            worker_id="worker",
            payload_patch={"job_record": {"status": "ready"}},
        )
        self.assertEqual(DurableJobStatus.READY, ready.status)
        self.assertEqual(1, self.queue.counts(owner_id="owner").terminal)
        deleted = self.queue.delete(claimed.job_id, owner_id="owner")
        self.assertEqual(claimed.job_id, deleted.job_id)
        self.assertEqual(0, self.queue.counts(owner_id="owner").total)


if __name__ == "__main__":
    unittest.main()
