from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

from flask import Flask, jsonify

from admission_runtime_integration import (
    install_admission_controls,
    reset_admission_controller_for_tests,
)


class AdmissionRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "KINDLEMASTER_ADMISSION_DB_PATH": str(self.root / "admission.sqlite3"),
                "KINDLEMASTER_RATE_WINDOW_SECONDS": "60",
                "KINDLEMASTER_ANON_REQUESTS_PER_WINDOW": "2",
                "KINDLEMASTER_ANON_POLLS_PER_WINDOW": "2",
                "KINDLEMASTER_ANON_READS_PER_WINDOW": "5",
                "KINDLEMASTER_MAX_ACTIVE_JOBS_PER_OWNER": "2",
                "KINDLEMASTER_MAX_QUEUED_JOBS_PER_OWNER": "2",
                "KINDLEMASTER_MAX_GLOBAL_JOBS": "5",
                "KINDLEMASTER_MIN_FREE_DISK_BYTES": "1",
                "KINDLEMASTER_MAX_FILE_BYTES": "1000000",
                "KINDLEMASTER_MAX_ARCHIVE_ENTRIES": "100",
                "KINDLEMASTER_MAX_ARCHIVE_UNCOMPRESSED_BYTES": "1000000",
                "KINDLEMASTER_MAX_ARCHIVE_RATIO": "50",
            },
            clear=False,
        )
        self.env.start()
        reset_admission_controller_for_tests()
        self.module = self._fake_module()
        install_admission_controls(app_module=self.module)
        self.client = self.module.app.test_client()
        self.guest = "guest-session-0123456789abcdef"

    def tearDown(self):
        reset_admission_controller_for_tests()
        self.env.stop()
        self.temp.cleanup()

    def _fake_module(self):
        module = types.SimpleNamespace()
        module.app = Flask(f"admission-test-{id(self)}")
        module.UPLOAD_DIR = str(self.root / "uploads")
        Path(module.UPLOAD_DIR).mkdir(parents=True)
        module.jobs = {}
        module.calls = 0

        def auth():
            return types.SimpleNamespace(authenticated=False, user_id="", error="")

        module._resolve_request_auth_context = auth
        module._visible_conversion_jobs_snapshot = lambda: {
            key: dict(value) for key, value in module.jobs.items()
        }

        @module.app.get("/convert/status/<job_id>")
        def status(job_id):
            module.calls += 1
            return jsonify({"success": True, "job_id": job_id})

        @module.app.post("/convert/start")
        def start():
            module.calls += 1
            return jsonify({"success": True}), 202

        return module

    def _docx_bytes(self) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<document><body/></document>")
        return output.getvalue()

    def test_poll_rate_limit_is_enforced_before_route(self):
        headers = {"X-KindleMaster-Guest-Id": self.guest}
        self.assertEqual(
            200,
            self.client.get(
                "/convert/status/job-1",
                headers=headers,
                base_url="https://api.example.com",
            ).status_code,
        )
        self.assertEqual(
            200,
            self.client.get(
                "/convert/status/job-1",
                headers=headers,
                base_url="https://api.example.com",
            ).status_code,
        )
        blocked = self.client.get(
            "/convert/status/job-1",
            headers=headers,
            base_url="https://api.example.com",
        )
        self.assertEqual(429, blocked.status_code)
        self.assertEqual("rate_limited", blocked.get_json()["error_code"])
        self.assertEqual("60", blocked.headers.get("Retry-After"))
        self.assertEqual(2, self.module.calls)

    def test_valid_docx_reaches_route_and_mismatch_is_blocked(self):
        valid = self.client.post(
            "/convert/start",
            data={
                "file": (
                    io.BytesIO(self._docx_bytes()),
                    "book.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers={"X-KindleMaster-Guest-Id": self.guest},
            base_url="https://api.example.com",
        )
        self.assertEqual(202, valid.status_code)
        self.assertEqual(1, self.module.calls)

        mismatch = self.client.post(
            "/convert/start",
            data={"file": (io.BytesIO(b"%PDF-1.7\n"), "book.docx", "application/pdf")},
            headers={"X-KindleMaster-Guest-Id": self.guest},
            base_url="https://api.example.com",
        )
        self.assertEqual(415, mismatch.status_code)
        self.assertEqual("extension_magic_mismatch", mismatch.get_json()["error_code"])
        self.assertEqual(1, self.module.calls)

    def test_owner_queue_quota_blocks_new_upload(self):
        owner = "guest:" + hashlib.sha256(self.guest.encode("utf-8")).hexdigest()
        self.module.jobs = {
            "job-1": {"status": "queued", "guest_owner_id": owner},
            "job-2": {"status": "queued", "guest_owner_id": owner},
        }
        blocked = self.client.post(
            "/convert/start",
            data={
                "file": (
                    io.BytesIO(self._docx_bytes()),
                    "book.docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers={"X-KindleMaster-Guest-Id": self.guest},
            base_url="https://api.example.com",
        )
        self.assertEqual(429, blocked.status_code)
        self.assertEqual("owner_queue_limit", blocked.get_json()["error_code"])
        self.assertEqual(0, self.module.calls)


if __name__ == "__main__":
    unittest.main()
