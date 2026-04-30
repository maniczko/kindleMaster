import io
import os
import tempfile
import threading
import unittest
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
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

    def _write_epub_fixture(self, job_id: str, body: str) -> str:
        output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with zipfile.ZipFile(output_path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr(
                "EPUB/chapter.xhtml",
                f"<html><body><h1>Chapter</h1><p>{body}</p></body></html>",
            )
        self.cleanup_paths.append(output_path)
        return output_path

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
        self.assertTrue(status_payload["download_available"])
        self.assertEqual(status_payload["download_state"]["status"], "available")
        self.assertTrue(status_payload["quality_state"]["download_available"])
        self.assertEqual(status_payload["quality_state"]["download_state"]["status"], "available")

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
        self.assertEqual(response.get_json()["error_code"], "missing_output")

    def test_convert_start_returns_structured_upload_error_for_missing_file(self) -> None:
        response = self.client.post("/convert/start", data={}, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "upload_failed")
        self.assertEqual(payload["phase"], "upload")

    def test_convert_start_returns_structured_upload_error_for_unsupported_file(self) -> None:
        response = self.client.post(
            "/convert/start",
            data={"file": (io.BytesIO(b"text"), "notes.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "upload_failed")
        self.assertEqual(payload["phase"], "upload")

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
                "error_code": "conversion_failed",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"], "timeout while reading source")
        self.assertEqual(payload["error_code"], "conversion_failed")
        self.assertIsNone(payload["conversion"])
        self.assertIsNone(payload["download_url"])
        self.assertEqual(payload["poll_after_ms"], 0)

    def test_convert_jobs_lists_ready_running_and_failed_summaries(self) -> None:
        now = datetime.now(UTC)
        ready_id = "history-ready-job"
        running_id = "history-running-job"
        failed_id = "history-failed-job"
        ready_output_path = os.path.join(app_module.UPLOAD_DIR, f"{ready_id}.epub")
        with open(ready_output_path, "wb") as handle:
            handle.write(b"history-ready-epub")
        self.cleanup_paths.append(ready_output_path)

        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[ready_id] = {
                "job_id": ready_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "ready.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": ready_output_path,
                "download_name": "ready.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
            app_module._CONVERSION_JOBS[running_id] = {
                "job_id": running_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "docx",
                "filename": "running.docx",
                "created_at": (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": "",
                "download_name": "running.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
            app_module._CONVERSION_JOBS[failed_id] = {
                "job_id": failed_id,
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "filename": "failed.pdf",
                "created_at": (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=3)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": "",
                "download_name": "failed.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "timeout while reading source",
                "error_code": "conversion_failed",
            }
        self.cleanup_job_ids.extend([ready_id, running_id, failed_id])

        response = self.client.get("/convert/jobs?limit=100")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        jobs = {job["job_id"]: job for job in payload["jobs"]}
        self.assertIn(ready_id, jobs)
        self.assertIn(running_id, jobs)
        self.assertIn(failed_id, jobs)

        ready_job = jobs[ready_id]
        self.assertEqual(ready_job["download_url"], f"/convert/download/{ready_id}")
        self.assertEqual(ready_job["quality_state_url"], f"/convert/quality/{ready_id}")
        self.assertEqual(ready_job["output_size_bytes"], len(b"history-ready-epub"))
        self.assertTrue(ready_job["download_available"])
        self.assertEqual(ready_job["download_state"]["status"], "available")
        self.assertNotIn("error", ready_job)

        running_job = jobs[running_id]
        self.assertEqual(running_job["status"], "running")
        self.assertEqual(running_job["source_type"], "docx")
        self.assertFalse(running_job["download_available"])
        self.assertEqual(running_job["download_state"]["status"], "pending")
        self.assertNotIn("download_url", running_job)
        self.assertNotIn("error", running_job)

        failed_job = jobs[failed_id]
        self.assertEqual(failed_job["status"], "failed")
        self.assertEqual(failed_job["error"], "timeout while reading source")
        self.assertEqual(failed_job["error_code"], "conversion_failed")
        self.assertFalse(failed_job["download_available"])
        self.assertEqual(failed_job["download_state"]["status"], "unavailable")
        self.assertNotIn("download_url", failed_job)

    def test_convert_library_filters_ready_jobs_and_exposes_report_links(self) -> None:
        now = datetime.now(UTC)
        ready_id = "library-ready-job"
        failed_id = "library-failed-job"
        ready_output_path = self._write_epub_fixture(ready_id, "Diagram handbook chapter")

        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[ready_id] = {
                "job_id": ready_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "diagram-book.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": ready_output_path,
                "download_name": "diagram-book.epub",
                "metadata": {
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "title": "Diagram Handbook",
                    "creator": "KindleMaster",
                    "language": "pl",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": 0,
                },
                "output_size_bytes": 0,
                "error": "",
            }
            app_module._CONVERSION_JOBS[failed_id] = {
                "job_id": failed_id,
                "status": "failed",
                "message": "Konwersja nie powiodla sie.",
                "source_type": "pdf",
                "filename": "broken-diagram.pdf",
                "created_at": (now - timedelta(seconds=20)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": "",
                "download_name": "broken-diagram.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "conversion failed",
                "error_code": "conversion_failed",
            }
        self.cleanup_job_ids.extend([ready_id, failed_id])

        response = self.client.get("/convert/library?status=ready&q=diagram&limit=100")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["index_version"], "kindlemaster-library-v1")
        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["job_id"], ready_id)
        self.assertEqual(item["title"], "Diagram Handbook")
        self.assertEqual(item["download_url"], f"/convert/download/{ready_id}")
        self.assertEqual(item["quality_state_url"], f"/convert/quality/{ready_id}")
        self.assertEqual(item["report_json_url"], f"/convert/report/{ready_id}.json")
        self.assertEqual(item["report_markdown_url"], f"/convert/report/{ready_id}.md")
        self.assertTrue(item["download_available"])
        self.assertEqual(item["download_state"]["status"], "available")
        self.assertIn("title", item["matched_fields"])

    def test_ready_missing_output_reports_consistent_download_state_without_availability(self) -> None:
        now = datetime.now(UTC)
        job_id = "contract-ready-missing-output"
        missing_output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "missing-output.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": missing_output_path,
                "download_name": "missing-output.epub",
                "metadata": {"source_type": "pdf", "profile": "book_reflow"},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        status_payload = self.client.get(f"/convert/status/{job_id}").get_json()
        quality_payload = self.client.get(f"/convert/quality/{job_id}").get_json()
        jobs_payload = self.client.get("/convert/jobs?limit=100").get_json()
        library_payload = self.client.get("/convert/library?status=ready&limit=100").get_json()

        self.assertFalse(status_payload["download_available"])
        self.assertIsNone(status_payload["download_url"])
        self.assertEqual(status_payload["download_state"]["status"], "missing_output")
        self.assertFalse(status_payload["quality_state"]["download_available"])
        self.assertEqual(status_payload["quality_state"]["download_state"]["status"], "missing_output")

        quality_state = quality_payload["quality_state"]
        self.assertFalse(quality_state["download_available"])
        self.assertEqual(quality_state["download_state"]["status"], "missing_output")

        job_summary = {
            item["job_id"]: item
            for item in jobs_payload["jobs"]
        }[job_id]
        self.assertFalse(job_summary["download_available"])
        self.assertEqual(job_summary["download_state"]["status"], "missing_output")
        self.assertNotIn("download_url", job_summary)

        library_item = {
            item["job_id"]: item
            for item in library_payload["items"]
        }[job_id]
        self.assertFalse(library_item["download_available"])
        self.assertEqual(library_item["download_state"]["status"], "missing_output")
        self.assertEqual(library_item["download_url"], "")

    def test_convert_search_uses_local_epub_full_text_excerpt(self) -> None:
        now = datetime.now(UTC)
        job_id = "library-search-job"
        output_path = self._write_epub_fixture(
            job_id,
            "The blockade motif appears in this generated EPUB text.",
        )
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "pdf",
                "filename": "quiet-title.pdf",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": output_path,
                "download_name": "quiet-title.epub",
                "metadata": {
                    "source_type": "pdf",
                    "profile": "book_reflow",
                    "title": "Quiet Title",
                    "creator": "KindleMaster",
                    "language": "en",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                },
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get("/convert/search?q=blockade&limit=100")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["count"], 1)
        item = payload["items"][0]
        self.assertEqual(item["job_id"], job_id)
        self.assertTrue(item["searchable_text_available"])
        self.assertIn("blockade motif", item["text_excerpt"])
        self.assertIn("full_text", item["matched_fields"])

    def test_convert_quality_report_exports_json_and_markdown(self) -> None:
        now = datetime.now(UTC)
        job_id = "library-report-job"
        output_path = self._write_epub_fixture(job_id, "Report export excerpt")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy do pobrania.",
                "source_type": "docx",
                "filename": "report-source.docx",
                "created_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                "updated_at": (now - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
                "source_path": "",
                "output_path": output_path,
                "download_name": "report-source.epub",
                "metadata": {
                    "source_type": "docx",
                    "profile": "book_reflow",
                    "title": "Report Source",
                    "creator": "KindleMaster",
                    "language": "pl",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                },
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        json_response = self.client.get(f"/convert/report/{job_id}.json")
        markdown_response = self.client.get(f"/convert/report/{job_id}.md")

        self.assertEqual(json_response.status_code, 200)
        json_payload = json_response.get_json()
        self.assertEqual(json_payload["job"]["job_id"], job_id)
        self.assertEqual(json_payload["job"]["report_markdown_url"], f"/convert/report/{job_id}.md")
        self.assertIn("quality_state", json_payload)
        self.assertEqual(markdown_response.status_code, 200)
        markdown = markdown_response.get_data(as_text=True)
        self.assertIn("# KindleMaster quality report: Report Source", markdown)
        self.assertIn("Report export excerpt", markdown)

    def test_convert_jobs_uses_reloaded_store_without_dropping_active_jobs(self) -> None:
        active_id = "history-active-memory-job"
        reloaded_id = "history-reloaded-ready-job"
        now = datetime.now(UTC)
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "conversion_jobs.json"
            seed_store = app_module.ConversionJobStore(
                {},
                threading.Lock(),
                persistence_path=store_path,
                active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
            )
            seed_store.create(
                {
                    "job_id": reloaded_id,
                    "status": "ready",
                    "message": "EPUB gotowy do pobrania.",
                    "source_type": "pdf",
                    "filename": "reloaded.pdf",
                    "created_at": (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
                    "updated_at": (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                    "source_path": "",
                    "output_path": "",
                    "download_name": "reloaded.epub",
                    "metadata": {},
                    "output_size_bytes": 1234,
                    "error": "",
                }
            )
            with app_module._CONVERSION_JOBS_LOCK:
                app_module._CONVERSION_JOBS[active_id] = {
                    "job_id": active_id,
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "source_type": "pdf",
                    "filename": "active.pdf",
                    "created_at": (now - timedelta(seconds=15)).isoformat().replace("+00:00", "Z"),
                    "updated_at": (now - timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "source_path": "",
                    "output_path": "",
                    "download_name": "active.epub",
                    "metadata": {},
                    "output_size_bytes": 0,
                    "error": "",
                }
            self.cleanup_job_ids.extend([active_id, reloaded_id])

            reloaded_store = app_module.ConversionJobStore(
                app_module._CONVERSION_JOBS,
                app_module._CONVERSION_JOBS_LOCK,
                persistence_path=store_path,
                active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
            )
            with patch.object(app_module, "_CONVERSION_JOB_STORE", reloaded_store):
                load_result = reloaded_store.load()
                response = self.client.get("/convert/jobs?limit=100")

        self.assertTrue(load_result["loaded"])
        self.assertEqual(load_result["job_count"], 1)
        self.assertIsNotNone(app_module._get_conversion_job(active_id))
        self.assertEqual(response.status_code, 200)
        jobs = {job["job_id"]: job for job in response.get_json()["jobs"]}
        self.assertIn(active_id, jobs)
        self.assertIn(reloaded_id, jobs)
        self.assertEqual(jobs[active_id]["status"], "running")
        self.assertNotIn("download_url", jobs[active_id])
        self.assertFalse(jobs[reloaded_id]["download_available"])
        self.assertEqual(jobs[reloaded_id]["download_state"]["status"], "missing_output")
        self.assertNotIn("download_url", jobs[reloaded_id])

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

    def test_convert_start_rejects_when_active_queue_is_full(self) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        active_ids = ["active-queue-1", "active-queue-2"]
        with app_module._CONVERSION_JOBS_LOCK:
            for job_id in active_ids:
                app_module._CONVERSION_JOBS[job_id] = {
                    "job_id": job_id,
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "source_type": "pdf",
                    "filename": f"{job_id}.pdf",
                    "created_at": now,
                    "updated_at": now,
                    "source_path": "",
                    "output_path": "",
                    "download_name": f"{job_id}.epub",
                    "metadata": {},
                    "output_size_bytes": 0,
                    "error": "",
                }
        self.cleanup_job_ids.extend(active_ids)

        with patch("app._spawn_conversion_job") as spawn_mock:
            response = self.client.post(
                "/convert/start",
                data={"file": (io.BytesIO(b"%PDF-1.4"), "queued.pdf")},
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 429)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "queue_failed")
        self.assertEqual(payload["phase"], "queue")
        self.assertTrue(payload["retryable"])
        spawn_mock.assert_not_called()

    def test_stale_active_job_is_marked_timed_out_by_status_route(self) -> None:
        job_id = "stale-running-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=app_module.MAX_CONVERSION_JOB_RUNTIME_SECONDS + 60)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "stale.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "stale.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        response = self.client.get(f"/convert/status/{job_id}")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "timed_out")
        self.assertEqual(payload["error_code"], "conversion_timeout")
        self.assertEqual(payload["poll_after_ms"], 0)
        self.assertEqual(payload["quality_state"]["release_verdict"], "failed")
        self.assertEqual(payload["quality_state"]["quality_blockers"][0]["code"], "conversion_timeout")

    def test_conversion_timeout_writes_structured_recovery_log(self) -> None:
        job_id = "stale-log-job"
        created_at = (datetime.now(UTC) - timedelta(seconds=app_module.MAX_CONVERSION_JOB_RUNTIME_SECONDS + 60)).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "running",
                "message": "Konwertuje PDF do EPUB...",
                "source_type": "pdf",
                "filename": "stale.pdf",
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": "",
                "download_name": "stale.epub",
                "metadata": {},
                "output_size_bytes": 0,
                "error": "",
            }
        self.cleanup_job_ids.append(job_id)

        with patch.object(app_module.app.logger, "error") as log_mock:
            self.client.get(f"/convert/status/{job_id}")

        log_payload = json.loads(log_mock.call_args.args[0])
        self.assertEqual(log_payload["event"], "convert.job.failed")
        self.assertEqual(log_payload["job_id"], job_id)
        self.assertEqual(log_payload["phase"], "conversion")
        self.assertEqual(log_payload["status"], "timed_out")
        self.assertEqual(log_payload["error_code"], "conversion_timeout")
        self.assertIn("safe_message", log_payload)

    def test_convert_download_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/download/missing-job")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Nie znaleziono zadania konwersji.")
        self.assertEqual(response.get_json()["error_code"], "missing_output")

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
        self.assertEqual(response.get_json()["error_code"], "queue_failed")
        self.assertTrue(response.get_json()["retryable"])

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
        self.assertEqual(response.get_json()["error_code"], "missing_output")
        with app_module._CONVERSION_JOBS_LOCK:
            job = app_module._CONVERSION_JOBS[job_id]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["error"], "Brak pliku EPUB do pobrania.")
            self.assertEqual(job["error_code"], "missing_output")

    def test_missing_download_writes_structured_recovery_log(self) -> None:
        job_id = "ready-missing-log-file"
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

        with patch.object(app_module.app.logger, "error") as log_mock:
            self.client.get(f"/convert/download/{job_id}")

        log_payload = json.loads(log_mock.call_args.args[0])
        self.assertEqual(log_payload["event"], "convert.job.download_missing")
        self.assertEqual(log_payload["job_id"], job_id)
        self.assertEqual(log_payload["phase"], "download")
        self.assertEqual(log_payload["error_code"], "missing_output")


if __name__ == "__main__":
    unittest.main()
