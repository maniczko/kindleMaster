from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import app as app_module


class ConversionCleanupTTLContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.temp_dir.name)
        self.jobs_backup = {}
        self.cleanup_backup = app_module._LAST_CONVERSION_CLEANUP_AT
        with app_module._CONVERSION_JOBS_LOCK:
            self.jobs_backup = dict(app_module._CONVERSION_JOBS)
            app_module._CONVERSION_JOBS.clear()
        app_module._LAST_CONVERSION_CLEANUP_AT = None

    def tearDown(self) -> None:
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS.clear()
            app_module._CONVERSION_JOBS.update(self.jobs_backup)
        app_module._LAST_CONVERSION_CLEANUP_AT = self.cleanup_backup
        self.temp_dir.cleanup()

    def test_cleanup_hook_removes_expired_terminal_jobs_and_output_files(self) -> None:
        old_time = datetime.now(UTC) - timedelta(seconds=app_module.CONVERSION_JOB_RETENTION_SECONDS + 120)
        source_path = self.upload_dir / "expired.pdf"
        source_path.write_bytes(b"%PDF-expired")
        output_path = self.upload_dir / "expired.epub"
        output_path.write_bytes(b"expired-epub")
        with patch.object(app_module, "UPLOAD_DIR", str(self.upload_dir)):
            with app_module._CONVERSION_JOBS_LOCK:
                app_module._CONVERSION_JOBS["expired-job"] = {
                    "job_id": "expired-job",
                    "status": "ready",
                    "message": "EPUB gotowy do pobrania.",
                    "source_type": "pdf",
                    "filename": "expired.pdf",
                    "created_at": old_time.isoformat().replace("+00:00", "Z"),
                    "updated_at": old_time.isoformat().replace("+00:00", "Z"),
                    "source_path": str(source_path),
                    "output_path": str(output_path),
                    "download_name": "expired.epub",
                    "metadata": {},
                    "output_size_bytes": output_path.stat().st_size,
                    "error": "",
                }

            result = app_module._cleanup_expired_conversion_jobs(now=datetime.now(UTC), force=True)

        self.assertTrue(result["ran"])
        self.assertEqual(result["removed_jobs"], 1)
        self.assertFalse(source_path.exists())
        self.assertFalse(output_path.exists())
        self.assertIsNone(app_module._get_conversion_job("expired-job"))

    def test_cleanup_hook_preserves_active_jobs_and_their_files(self) -> None:
        now = datetime.now(UTC)
        source_path = self.upload_dir / "active.pdf"
        source_path.write_bytes(b"%PDF-active")
        output_path = self.upload_dir / "active.epub"
        output_path.write_bytes(b"active-epub")
        with patch.object(app_module, "UPLOAD_DIR", str(self.upload_dir)):
            with app_module._CONVERSION_JOBS_LOCK:
                app_module._CONVERSION_JOBS["active-job"] = {
                    "job_id": "active-job",
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "source_type": "pdf",
                    "filename": "active.pdf",
                    "created_at": now.isoformat().replace("+00:00", "Z"),
                    "updated_at": now.isoformat().replace("+00:00", "Z"),
                    "source_path": str(source_path),
                    "output_path": str(output_path),
                    "download_name": "active.epub",
                    "metadata": {},
                    "output_size_bytes": output_path.stat().st_size,
                    "error": "",
                }

            result = app_module._cleanup_expired_conversion_jobs(
                now=now + timedelta(seconds=app_module.CONVERSION_TEMP_FILE_RETENTION_SECONDS + 120),
                force=True,
            )

        self.assertTrue(result["ran"])
        self.assertEqual(result["removed_jobs"], 0)
        self.assertTrue(source_path.exists())
        self.assertTrue(output_path.exists())
        self.assertIsNotNone(app_module._get_conversion_job("active-job"))

    def test_cleanup_hook_removes_old_orphan_files_from_upload_dir(self) -> None:
        now = datetime.now(UTC)
        orphan_source = self.upload_dir / "orphan.pdf"
        orphan_output = self.upload_dir / "orphan.epub"
        orphan_source.write_bytes(b"%PDF-orphan")
        orphan_output.write_bytes(b"epub-orphan")
        old_timestamp = (now - timedelta(seconds=app_module.CONVERSION_TEMP_FILE_RETENTION_SECONDS + 120)).timestamp()
        orphan_source.touch()
        orphan_output.touch()
        Path(orphan_source).chmod(0o666)
        Path(orphan_output).chmod(0o666)
        import os
        os.utime(orphan_source, (old_timestamp, old_timestamp))
        os.utime(orphan_output, (old_timestamp, old_timestamp))

        with patch.object(app_module, "UPLOAD_DIR", str(self.upload_dir)):
            result = app_module._cleanup_expired_conversion_jobs(now=now, force=True)

        self.assertTrue(result["ran"])
        self.assertGreaterEqual(result["removed_files"], 2)
        self.assertFalse(orphan_source.exists())
        self.assertFalse(orphan_output.exists())


if __name__ == "__main__":
    unittest.main()
