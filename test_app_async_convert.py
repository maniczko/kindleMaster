import io
import os
import unittest
from datetime import UTC, datetime, timedelta
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
                    "render_budget_class": "fixed_layout_dense",
                    "render_budget_attempt": "fallback",
                    "size_budget_status": "passed",
                    "size_budget_message": "fallback ok",
                    "target_warn_bytes": 2048,
                    "target_hard_bytes": 4096,
                    "final_output_size_bytes": len(b"async-epub"),
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
        self.assertEqual(payload["poll_after_ms"], app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS)
        job_id = payload["job_id"]
        self.cleanup_job_ids.append(job_id)

        status_response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(status_response.status_code, 200)
        status_payload = status_response.get_json()
        self.assertEqual(status_payload["status"], "ready")
        self.assertEqual(status_payload["conversion"]["profile"], "book_reflow")
        self.assertEqual(status_payload["conversion"]["output_size_bytes"], len(b"async-epub"))
        self.assertEqual(status_payload["conversion"]["render_budget_class"], "fixed_layout_dense")
        self.assertEqual(status_payload["conversion"]["render_budget_attempt"], "fallback")
        self.assertEqual(status_payload["conversion"]["size_budget_status"], "passed")
        self.assertEqual(status_payload["output_size_bytes"], len(b"async-epub"))
        self.assertEqual(status_payload["poll_after_ms"], 0)
        self.assertEqual(status_payload["download_url"], f"/convert/download/{job_id}")

        download_response = self.client.get(f"/convert/download/{job_id}")
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response.data, b"async-epub")
        self.assertEqual(download_response.headers.get("X-Source-Type"), "pdf")
        self.assertEqual(download_response.headers.get("X-Heading-Repair-Status"), "applied")
        self.assertEqual(download_response.headers.get("X-Render-Budget-Class"), "fixed_layout_dense")
        self.assertEqual(download_response.headers.get("X-Render-Budget-Attempt"), "fallback")
        download_response.close()

    def test_convert_status_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/status/missing-job")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Nie znaleziono zadania konwersji.")

    def test_convert_status_surfaces_running_state_and_poll_hint(self) -> None:
        job_id = "running-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertIsNone(payload["conversion"])
        self.assertIsNone(payload["download_url"])
        self.assertEqual(payload["error"], "")
        self.assertEqual(payload["poll_after_ms"], app_module.MAX_CONVERSION_POLL_INTERVAL_MS)
        self.assertGreaterEqual(payload["elapsed_seconds"], 89)

    def test_convert_status_uses_heading_repair_poll_hint_even_before_long_runtime(self) -> None:
        job_id = "repairing-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "repairing_headings",
                "message": "Naprawiam headingi i TOC w EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "repairing_headings")
        self.assertEqual(payload["poll_after_ms"], 2500)
        self.assertGreaterEqual(payload["elapsed_seconds"], 9)

    def test_convert_status_caps_poll_hint_for_very_long_running_job(self) -> None:
        job_id = "long-running-job"
        created_at = (datetime.now(UTC) - timedelta(minutes=6)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["poll_after_ms"], app_module.MAX_CONVERSION_POLL_INTERVAL_MS)
        self.assertGreaterEqual(payload["elapsed_seconds"], 359)

    def test_convert_status_surfaces_failed_state_without_download(self) -> None:
        job_id = "failed-job"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "filename": "broken.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "output_path": "",
                "download_name": "broken.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "timeout while reading source",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "timeout while reading source")
        self.assertIsNone(payload["conversion"])
        self.assertIsNone(payload["download_url"])
        self.assertEqual(payload["poll_after_ms"], 0)

    def test_attach_output_size_metadata_warns_for_oversized_epub(self) -> None:
        metadata = {"warnings": 0, "warning_list": []}
        enriched = app_module._attach_output_size_metadata(
            metadata,
            app_module.OVERSIZED_EPUB_WARNING_BYTES + 1,
        )
        self.assertIn("output_size_bytes", enriched)
        self.assertGreaterEqual(enriched["warnings"], 1)
        self.assertTrue(any("EPUB ma" in message for message in enriched["warning_list"]))

    def test_convert_start_accepts_docx_and_queues_async_job(self) -> None:
        with patch("app._spawn_conversion_job") as spawn_mock:
            response = self.client.post(
                "/convert/start",
                data={
                    "file": (io.BytesIO(b"docx-bytes"), "sample.docx"),
                    "profile": "auto-premium",
                    "ocr": "false",
                    "language": "pl",
                    "heading_repair": "false",
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()
        job_id = payload["job_id"]
        self.cleanup_job_ids.append(job_id)
        with app_module._CONVERSION_JOBS_LOCK:
            job = dict(app_module._CONVERSION_JOBS[job_id])
        self.cleanup_paths.append(job["source_path"])

        self.assertEqual(payload["source_type"], "docx")
        self.assertEqual(payload["poll_after_ms"], app_module.DEFAULT_CONVERSION_POLL_INTERVAL_MS)
        self.assertEqual(job["source_type"], "docx")
        self.assertEqual(job["download_name"], "sample.epub")
        spawn_mock.assert_called_once()
        self.assertEqual(spawn_mock.call_args.kwargs["source_type"], "docx")
        self.assertEqual(spawn_mock.call_args.kwargs["original_filename"], "sample.docx")

    def test_convert_download_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/download/missing-job")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Nie znaleziono zadania konwersji.")

    def test_convert_download_returns_409_when_job_is_not_ready(self) -> None:
        job_id = "queued-job"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "sample.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/download/{job_id}")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "EPUB nie jest jeszcze gotowy do pobrania.")

    def test_convert_download_returns_500_and_marks_job_failed_when_ready_file_is_missing(self) -> None:
        job_id = "ready-missing-file"
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        missing_output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": missing_output_path,
                "download_name": "sample.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/download/{job_id}")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["error"], "Brak pliku EPUB do pobrania.")
        with app_module._CONVERSION_JOBS_LOCK:
            job = app_module._CONVERSION_JOBS[job_id]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error"], "Brak pliku EPUB do pobrania.")


if __name__ == "__main__":
    unittest.main()
