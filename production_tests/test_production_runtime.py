from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from flask import Flask, g

from durable_job_queue import DurableJobDatabase, SQLiteConversionJobStore
from production_api_policy import (
    install_async_only_conversion_policy,
    install_idempotent_retry_response_policy,
    install_migrated_sqlite_store,
)
from production_runtime import install_durable_submission, install_sqlite_job_store


class FakeAppModule(types.SimpleNamespace):
    pass


class LegacyStore:
    def snapshot(self) -> dict:
        return {
            "legacy-job": {
                "job_id": "legacy-job",
                "status": "ready",
                "filename": "legacy.pdf",
            }
        }


class CanonicalJobStore:
    def get(self, job_id: str) -> dict | None:
        if job_id != "canonical-job":
            return None
        return {
            "job_id": "canonical-job",
            "status": "queued",
            "message": "Ponowienie oczekuje w kolejce.",
            "retry_of": "source-job",
        }


class ProductionRuntimeTests(unittest.TestCase):
    def test_submission_enqueues_without_calling_original_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DurableJobDatabase(Path(temp_dir) / "runtime.sqlite3")
            called: list[dict] = []
            module = FakeAppModule()
            module._spawn_conversion_job = lambda **kwargs: called.append(kwargs)
            module._set_conversion_job = lambda job_id, **fields: module._CONVERSION_JOB_STORE.update(job_id, fields)
            install_sqlite_job_store(module, database=database)
            module._CONVERSION_JOB_STORE.create({"job_id": "job-a", "status": "queued"})
            queue = install_durable_submission(module, database=database)
            module._spawn_conversion_job(
                job_id="job-a",
                source_path="a.pdf",
                source_type="pdf",
                original_filename="a.pdf",
                profile="auto-premium",
                force_ocr=False,
                language="pl",
                heading_repair_enabled=True,
                cloud_user_id="user-1",
                cloud_token="secret-not-persisted",
            )
            self.assertEqual(called, [])
            record = queue.get("job-a")
            self.assertIsNotNone(record)
            self.assertEqual(record.payload["cloud_token"], "")
            self.assertNotIn("secret-not-persisted", str(record.payload))
            self.assertEqual(record.owner_key, "user:user-1")

    def test_legacy_json_jobs_are_migrated_into_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DurableJobDatabase(Path(temp_dir) / "runtime.sqlite3")
            module = FakeAppModule(_CONVERSION_JOB_STORE=LegacyStore())
            result = install_migrated_sqlite_store(module, database=database)
            self.assertEqual(result, {"migrated": 1, "preserved": 0, "failed": 0})
            migrated = module._CONVERSION_JOB_STORE.get("legacy-job")
            self.assertEqual(migrated["status"], "ready")
            self.assertEqual(migrated["filename"], "legacy.pdf")

    def test_direct_and_nested_legacy_mutations_are_written_through(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = DurableJobDatabase(Path(temp_dir) / "runtime.sqlite3")
            module = FakeAppModule(_CONVERSION_JOB_STORE=LegacyStore())
            install_migrated_sqlite_store(module, database=database)
            module._CONVERSION_JOB_STORE.create(
                {
                    "job_id": "job-a",
                    "status": "queued",
                    "progress": {"stage": "queued", "messages": []},
                }
            )

            legacy_job = module._CONVERSION_JOBS["job-a"]
            legacy_job.update({"status": "timed_out"})
            legacy_job["progress"]["stage"] = "timed_out"
            legacy_job["progress"]["messages"].append("worker lease expired")

            persisted = SQLiteConversionJobStore(database).get("job-a")
            self.assertEqual(persisted["status"], "timed_out")
            self.assertEqual(persisted["progress"]["stage"], "timed_out")
            self.assertEqual(persisted["progress"]["messages"], ["worker lease expired"])

    def test_production_disables_synchronous_conversion_endpoint(self) -> None:
        flask_app = Flask("production-policy-test")

        @flask_app.post("/convert")
        def convert_sync():
            return {"unexpected": True}

        module = FakeAppModule(app=flask_app)
        module._json_error = lambda message, **kwargs: (
            {
                "error": message,
                "error_code": kwargs["error_code"],
                **dict(kwargs.get("extra") or {}),
            },
            kwargs["status_code"],
        )
        install_async_only_conversion_policy(module)
        response = flask_app.test_client().post("/convert")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "synchronous_conversion_disabled")
        self.assertEqual(response.get_json()["start_url"], "/convert/start")

    def test_retry_replay_returns_the_canonical_job(self) -> None:
        flask_app = Flask("retry-idempotency-test")

        @flask_app.post("/convert/retry/<job_id>")
        def retry(job_id: str):
            g.kindlemaster_canonical_job_id = "canonical-job"
            return {"success": True, "job_id": f"duplicate-{job_id}"}, 202

        module = FakeAppModule(
            app=flask_app,
            _CONVERSION_JOB_STORE=CanonicalJobStore(),
            DEFAULT_CONVERSION_POLL_INTERVAL_MS=1500,
            apply_no_store_headers=lambda _headers: None,
        )
        install_idempotent_retry_response_policy(module)
        response = flask_app.test_client().post("/convert/retry/source-job")
        payload = response.get_json()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["job_id"], "canonical-job")
        self.assertEqual(payload["retry_of"], "source-job")
        self.assertTrue(payload["idempotent_replay"])

    def test_railway_uses_supervised_production_entrypoint(self) -> None:
        dockerfile = Path("Dockerfile.railway").read_text(encoding="utf-8")
        active_lines = [
            line.strip()
            for line in dockerfile.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(active_lines[-1], 'CMD ["python", "production_server.py"]')
        railway = Path("railway.json").read_text(encoding="utf-8")
        self.assertIn('"startCommand": "python production_server.py"', railway)


if __name__ == "__main__":
    unittest.main()
