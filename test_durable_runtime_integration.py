from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile
import types
import unittest

from flask import Flask, jsonify

from durable_job_queue import DurableJobStatus
from durable_runtime_integration import install_durable_runtime
from durable_runtime_store import RuntimeDurableJobQueue


class FakeStore:
    def __init__(self):
        self.jobs = {}

    def create(self, job):
        self.jobs[str(job["job_id"])] = dict(job)
        return dict(job)

    def delete(self, job_id):
        return self.jobs.pop(job_id, None)


def fake_module(root: Path):
    app = Flask(__name__)
    store = FakeStore()
    module = types.SimpleNamespace()
    module.app = app
    module.UPLOAD_DIR = str(root / "uploads")
    Path(module.UPLOAD_DIR).mkdir(parents=True)
    module.DEFAULT_CONVERSION_POLL_INTERVAL_MS = 1500
    module._CONVERSION_JOB_STORE = store
    module._counter = 0

    def get_job(job_id):
        job = store.jobs.get(job_id)
        return dict(job) if job else None

    def set_job(job_id, **fields):
        job = store.jobs.setdefault(job_id, {"job_id": job_id})
        job.update(fields)
        return dict(job)

    def snapshot():
        return {key: dict(value) for key, value in store.jobs.items()}

    def auth():
        return types.SimpleNamespace(authenticated=False, user_id="", error="")

    def json_error(message, *, error_code, status_code, phase, **kwargs):
        response = jsonify(
            {
                "success": False,
                "error": message,
                "error_code": error_code,
                "phase": phase,
            }
        )
        response.status_code = status_code
        return response

    def original_spawn(**kwargs):
        raise AssertionError("production API must enqueue instead of starting a thread")

    module._get_conversion_job = get_job
    module._set_conversion_job = set_job
    module._visible_conversion_jobs_snapshot = snapshot
    module._resolve_request_auth_context = auth
    module._json_error = json_error
    module._spawn_conversion_job = original_spawn

    @app.post("/convert/start")
    def convert_start():
        module._counter += 1
        job_id = f"job-{module._counter}"
        source = Path(module.UPLOAD_DIR) / f"{job_id}.pdf"
        source.write_bytes(b"%PDF-1.7\n")
        created = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        store.create(
            {
                "job_id": job_id,
                "status": "queued",
                "message": "created",
                "source_path": str(source),
                "source_type": "pdf",
                "filename": f"{job_id}.pdf",
                "created_at": created,
                "updated_at": created,
                "runtime": {},
            }
        )
        module._spawn_conversion_job(
            job_id=job_id,
            source_path=str(source),
            source_type="pdf",
            original_filename=f"{job_id}.pdf",
            profile="auto-premium",
            force_ocr=False,
            language="pl",
            heading_repair_enabled=True,
            route_model_mode="shadow",
            quality_gate_mode="draft",
            cloud_user_id="",
            cloud_token="",
        )
        response = jsonify({"success": True, "job_id": job_id, "status": "queued"})
        response.status_code = 202
        return response

    @app.get("/convert/status/<job_id>")
    def status(job_id):
        job = module._get_conversion_job(job_id)
        if not job:
            return jsonify({"success": False}), 404
        return jsonify(job)

    @app.delete("/convert/jobs/<job_id>")
    def convert_job_delete(job_id):
        job = module._get_conversion_job(job_id)
        if not job:
            return jsonify({"success": True, "status": "already_missing"})
        if job.get("status") in {"queued", "running"}:
            return jsonify({"success": False}), 409
        store.delete(job_id)
        return jsonify({"success": True, "status": "deleted"})

    return module


class DurableRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.queue = RuntimeDurableJobQueue(self.root / "queue.sqlite3")
        self.module = fake_module(self.root)
        install_durable_runtime(self.module, self.queue)
        self.client = self.module.app.test_client()
        self.guest = "guest-session-0123456789abcdef"

    def tearDown(self):
        self.temp.cleanup()

    def test_start_enqueues_and_replay_returns_existing_job(self):
        response = self.client.post(
            "/convert/start",
            headers={
                "X-KindleMaster-Guest-Id": self.guest,
                "Idempotency-Key": "upload-1",
            },
            base_url="https://api.example.com",
        )
        self.assertEqual(202, response.status_code)
        first_id = response.get_json()["job_id"]
        durable = self.queue.get(first_id)
        self.assertIsNotNone(durable)
        self.assertEqual(DurableJobStatus.QUEUED, durable.status)
        self.assertTrue(Path(durable.payload["source_path"]).is_file())

        replay = self.client.post(
            "/convert/start",
            headers={
                "X-KindleMaster-Guest-Id": self.guest,
                "Idempotency-Key": "upload-1",
            },
            base_url="https://api.example.com",
        )
        self.assertEqual(first_id, replay.get_json()["job_id"])
        self.assertTrue(replay.get_json()["idempotent_replay"])
        self.assertEqual(1, self.queue.counts().total)

    def test_status_is_overlaid_from_durable_queue(self):
        response = self.client.post(
            "/convert/start",
            headers={"X-KindleMaster-Guest-Id": self.guest},
            base_url="https://api.example.com",
        )
        job_id = response.get_json()["job_id"]
        claimed = self.queue.claim(worker_id="worker")
        self.queue.start(claimed.job_id, worker_id="worker")
        self.queue.heartbeat_with_payload(
            claimed.job_id,
            worker_id="worker",
            lease_seconds=120,
            payload_patch={
                "job_record": {
                    "job_id": job_id,
                    "message": "halfway",
                    "progress": {"percent_estimate": 50},
                }
            },
        )
        status = self.client.get(
            f"/convert/status/{job_id}",
            headers={"X-KindleMaster-Guest-Id": self.guest},
            base_url="https://api.example.com",
        ).get_json()
        self.assertEqual("running", status["status"])
        self.assertEqual("halfway", status["message"])
        self.assertEqual("durable-sqlite", status["runtime"]["provider"])


if __name__ == "__main__":
    unittest.main()
