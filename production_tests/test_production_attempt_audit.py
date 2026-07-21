from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path

from flask import Flask, jsonify

from durable_job_queue import DurableJobDatabase, DurableJobQueue
from production_attempt_api import install_attempt_history_api
from production_attempt_audit import install_durable_attempt_audit


class ProductionAttemptAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DurableJobDatabase(Path(self.temp_dir.name) / "runtime.sqlite3")
        install_durable_attempt_audit(self.database)
        self.queue = DurableJobQueue(self.database)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_available(self, job_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE durable_queue SET available_at = 0 WHERE job_id = ?",
                (job_id,),
            )

    def _expire_lease(self, job_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE durable_queue SET lease_expires_at = ? WHERE job_id = ?",
                (time.time() - 1, job_id),
            )

    def test_retry_preserves_previous_attempt_evidence(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=2)
        first = self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(first)
        self.queue.mark_running("job-a", worker_id="worker-a", lease_seconds=30)
        self.assertTrue(self.queue.heartbeat("job-a", worker_id="worker-a", lease_seconds=30))
        retry = self.queue.fail(
            "job-a",
            worker_id="worker-a",
            error="temporary storage outage",
            retryable=True,
            base_backoff_seconds=1,
        )
        self.assertEqual(retry.status, "retry_wait")

        self._make_available("job-a")
        second = self.queue.claim(worker_id="worker-b", lease_seconds=30)
        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)
        self.queue.mark_running("job-a", worker_id="worker-b", lease_seconds=30)
        completed = self.queue.complete(
            "job-a",
            worker_id="worker-b",
            result={"artifact_sha256": "abc123"},
        )

        attempts = self.queue.attempts("job-a")
        self.assertEqual(completed.status, "succeeded")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["attempt"], 1)
        self.assertEqual(attempts[0]["worker_id"], "worker-a")
        self.assertEqual(attempts[0]["status"], "failed_retryable")
        self.assertEqual(attempts[0]["error"], "temporary storage outage")
        self.assertTrue(attempts[0]["finished_at"])
        self.assertEqual(attempts[1]["attempt"], 2)
        self.assertEqual(attempts[1]["worker_id"], "worker-b")
        self.assertEqual(attempts[1]["status"], "succeeded")
        self.assertEqual(attempts[1]["result"], {"artifact_sha256": "abc123"})

    def test_expired_lease_is_closed_before_next_attempt(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=2)
        first = self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(first)
        self.queue.mark_running("job-a", worker_id="worker-a", lease_seconds=30)
        self._expire_lease("job-a")

        second = self.queue.claim(worker_id="worker-b", lease_seconds=30)
        attempts = self.queue.attempts("job-a")

        self.assertIsNotNone(second)
        self.assertEqual(second.attempt, 2)
        self.assertEqual([attempt["status"] for attempt in attempts], ["lease_expired", "leased"])
        self.assertEqual(attempts[0]["error"], "worker lease expired")
        self.assertTrue(attempts[0]["finished_at"])

    def test_expired_final_attempt_enters_dead_letter_without_attempt_overflow(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=1)
        first = self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(first)
        self.queue.mark_running("job-a", worker_id="worker-a", lease_seconds=30)
        self._expire_lease("job-a")

        reclaimed = self.queue.claim(worker_id="worker-b", lease_seconds=30)
        record = self.queue.get("job-a")
        attempts = self.queue.attempts("job-a")

        self.assertIsNone(reclaimed)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "dead_letter")
        self.assertEqual(record.attempt, 1)
        self.assertEqual(record.last_error, "worker lease expired after final allowed attempt")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "dead_letter")

    def test_invalid_transitions_fail_closed(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=1)
        with self.assertRaisesRegex(RuntimeError, "active queue lease"):
            self.queue.complete("job-a", worker_id="worker-a", result={})

        claimed = self.queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(claimed)
        self.assertFalse(self.queue.heartbeat("job-a", worker_id="worker-b", lease_seconds=30))
        completed = self.queue.complete("job-a", worker_id="worker-a", result={})
        self.assertEqual(completed.status, "succeeded")

        with self.assertRaisesRegex(RuntimeError, "active queue lease"):
            self.queue.complete("job-a", worker_id="worker-a", result={})
        with self.assertRaisesRegex(RuntimeError, "active queue lease"):
            self.queue.fail(
                "job-a",
                worker_id="worker-a",
                error="late failure",
                retryable=True,
            )

    def test_attempt_history_api_uses_existing_owner_lookup(self) -> None:
        self.queue.enqueue(job_id="job-a", payload={}, max_attempts=2)
        self.queue.claim(worker_id="worker-a", lease_seconds=30)
        app = Flask("attempt-history-api-test")
        visible = {"allowed": True}

        def json_error(message: str, **kwargs):
            response = jsonify({"error": message, "error_code": kwargs["error_code"]})
            response.status_code = kwargs["status_code"]
            return response

        module = types.SimpleNamespace(
            app=app,
            _resolve_request_auth_context=lambda: types.SimpleNamespace(error="", authenticated=True),
            _json_auth_error=lambda _auth: ({"error_code": "auth_error"}, 401),
            _get_conversion_job_for_auth=lambda job_id, _auth: (
                {"job_id": job_id} if visible["allowed"] else None
            ),
            _json_error=json_error,
            apply_no_store_headers=lambda headers: headers.__setitem__("Cache-Control", "no-store"),
        )
        install_attempt_history_api(module, self.queue)
        client = app.test_client()

        response = client.get("/convert/attempts/job-a")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["attempts"][0]["worker_id"], "worker-a")
        self.assertEqual(payload["current_attempt"], 1)
        self.assertEqual(payload["max_attempts"], 2)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        visible["allowed"] = False
        denied = client.get("/convert/attempts/job-a")
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.get_json()["error_code"], "missing_conversion_job")


if __name__ == "__main__":
    unittest.main()
