from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from admission_control import AdmissionPolicy, DistributedAdmissionController


class AdmissionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.policy = AdmissionPolicy(
            window_seconds=60,
            anonymous_requests=2,
            authenticated_requests=3,
            max_active_jobs_per_owner=1,
            max_queued_jobs_per_owner=2,
            max_global_jobs=3,
            min_free_disk_bytes=100,
            max_file_bytes=1000,
            max_pdf_pages=10,
        )
        self.control = DistributedAdmissionController(Path(self.temp.name) / "admission.sqlite3", self.policy)
        self.now = datetime(2026, 7, 22, 8, 0, tzinfo=UTC)

    def tearDown(self):
        self.temp.cleanup()

    def test_anonymous_rate_limit_is_distributed(self):
        other_process = DistributedAdmissionController(self.control.path, self.policy)
        self.assertTrue(self.control.check_request(owner_id="guest", route="start", authenticated=False, now=self.now).allowed)
        self.assertTrue(other_process.check_request(owner_id="guest", route="start", authenticated=False, now=self.now).allowed)
        blocked = self.control.check_request(owner_id="guest", route="start", authenticated=False, now=self.now)
        self.assertFalse(blocked.allowed)
        self.assertEqual(429, blocked.status_code)
        self.assertTrue(self.control.check_request(owner_id="guest", route="start", authenticated=False, now=self.now + timedelta(seconds=61)).allowed)

    def test_owner_and_global_job_limits(self):
        self.assertEqual("owner_active_job_limit", self.control.check_job_admission(owner_id="a", active_jobs=1, queued_jobs=0, global_jobs=1, free_disk_bytes=1000).code)
        self.assertEqual("owner_queue_limit", self.control.check_job_admission(owner_id="a", active_jobs=0, queued_jobs=2, global_jobs=2, free_disk_bytes=1000).code)
        self.assertEqual("global_capacity_exhausted", self.control.check_job_admission(owner_id="a", active_jobs=0, queued_jobs=0, global_jobs=3, free_disk_bytes=1000).code)
        self.assertEqual("insufficient_storage_capacity", self.control.check_job_admission(owner_id="a", active_jobs=0, queued_jobs=0, global_jobs=0, free_disk_bytes=99).code)

    def test_upload_magic_must_match_extension_and_mime(self):
        allowed = self.control.validate_upload(filename="book.pdf", declared_mime="application/pdf", prefix=b"%PDF-1.7", size_bytes=500, pdf_pages=3)
        self.assertTrue(allowed.allowed)
        self.assertEqual("extension_magic_mismatch", self.control.validate_upload(filename="book.docx", declared_mime="application/pdf", prefix=b"%PDF-1.7", size_bytes=500).code)
        self.assertEqual("mime_magic_mismatch", self.control.validate_upload(filename="book.pdf", declared_mime="application/octet-stream", prefix=b"%PDF-1.7", size_bytes=500).code)
        self.assertEqual("pdf_page_limit", self.control.validate_upload(filename="book.pdf", declared_mime="application/pdf", prefix=b"%PDF-1.7", size_bytes=500, pdf_pages=11).code)
        self.assertEqual("upload_too_large", self.control.validate_upload(filename="book.pdf", declared_mime="application/pdf", prefix=b"%PDF-1.7", size_bytes=1001).code)


if __name__ == "__main__":
    unittest.main()
