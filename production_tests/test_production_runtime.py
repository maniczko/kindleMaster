from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from durable_job_queue import DurableJobDatabase
from production_runtime import install_durable_submission, install_sqlite_job_store


class FakeAppModule(types.SimpleNamespace):
    pass


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

    def test_railway_uses_supervised_production_entrypoint(self) -> None:
        dockerfile = Path("Dockerfile.railway").read_text(encoding="utf-8")
        active_lines = [line.strip() for line in dockerfile.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(active_lines[-1], 'CMD ["python", "production_server.py"]')
        railway = Path("railway.json").read_text(encoding="utf-8")
        self.assertIn('"startCommand": "python production_server.py"', railway)


if __name__ == "__main__":
    unittest.main()
