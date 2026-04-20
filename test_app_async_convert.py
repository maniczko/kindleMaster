import io
import os
import unittest
from unittest.mock import patch

import app as app_module
from app import app


class AppAsyncConvertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.cleanup_paths: list[str] = []
        self.cleanup_job_ids: list[str] = []

    def tearDown(self) -> None:
        for path in self.cleanup_paths:
            if path and os.path.exists(path):
                os.remove(path)
        with app_module._CONVERSION_JOBS_LOCK:
            for job_id in self.cleanup_job_ids:
                app_module._CONVERSION_JOBS.pop(job_id, None)

    def test_convert_start_status_and_download_roundtrip(self) -> None:
        def fake_spawn(**kwargs) -> None:
            job_id = kwargs["job_id"]
            output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
            with open(output_path, "wb") as handle:
                handle.write(b"async-epub")
            self.cleanup_paths.append(output_path)
            self.cleanup_paths.append(os.path.join(app_module.UPLOAD_DIR, f"{job_id}.pdf"))
            app_module._set_conversion_job(
                job_id,
                status="ready",
                message="EPUB gotowy do pobrania.",
                output_path=output_path,
                download_name="sample.epub",
                metadata={
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "confidence": 0.94,
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "strategy": "premium",
                    "sections": 3,
                    "assets": 1,
                    "layout": "reflowable",
                    "warnings": 0,
                    "warning_list": [],
                    "high_risk_pages": 0,
                    "high_risk_page_list": [],
                    "high_risk_sections": 0,
                    "high_risk_section_list": [],
                    "heading_repair": {
                        "status": "applied",
                        "release": "pass_with_review",
                        "toc_before": 1,
                        "toc_after": 3,
                        "removed": 1,
                        "review": 2,
                        "epubcheck": "passed",
                        "error": "",
                    },
                },
                error="",
            )

        with patch("app._spawn_conversion_job", side_effect=fake_spawn):
            response = self.client.post(
                "/convert/start",
                data={
                    "file": (io.BytesIO(b"%PDF-1.4\n%synthetic\n"), "sample.pdf"),
                    "profile": "auto-premium",
                    "ocr": "false",
                    "language": "pl",
                    "heading_repair": "true",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "queued")
        job_id = payload["job_id"]
        self.cleanup_job_ids.append(job_id)

        status_response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertEqual(status_payload["status"], "ready")
        self.assertEqual(status_payload["conversion"]["profile"], "book_reflow")
        self.assertEqual(status_payload["download_url"], f"/convert/download/{job_id}")

        download_response = self.client.get(f"/convert/download/{job_id}")
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.data, b"async-epub")
        self.assertEqual(download_response.headers.get("X-Source-Type"), "pdf")
        self.assertEqual(download_response.headers.get("X-Heading-Repair-Status"), "applied")
        download_response.close()

    def test_convert_status_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/status/missing-job")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
