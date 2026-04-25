from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app_runtime_services import (
    ConversionJobStore,
    ConversionRequest,
    build_local_app_url,
    build_conversion_job_record,
    detect_supported_source_type,
    pick_epubcheck_error,
    resolve_debug_mode,
    resolve_server_port,
    run_document_conversion,
    serve_http_app,
)


class AppRuntimeServicesTests(unittest.TestCase):
    def test_detect_supported_source_type_accepts_pdf_and_docx_case_insensitively(self) -> None:
        self.assertEqual(detect_supported_source_type("sample.pdf"), "pdf")
        self.assertEqual(detect_supported_source_type("SAMPLE.DOCX"), "docx")
        self.assertIsNone(detect_supported_source_type("sample.epub"))
        self.assertIsNone(detect_supported_source_type(""))

    def test_build_local_app_url_normalizes_path_and_optional_port(self) -> None:
        self.assertEqual(build_local_app_url(5001), "http://kindlemaster.localhost:5001/")
        self.assertEqual(
            build_local_app_url(path="convert/status/job-1"),
            "http://kindlemaster.localhost/convert/status/job-1",
        )
        self.assertEqual(build_local_app_url(" 5511 ", path=""), "http://kindlemaster.localhost:5511/")

    def test_build_conversion_job_record_creates_consistent_queued_payload(self) -> None:
        payload = build_conversion_job_record(
            job_id="job-123",
            source_path="C:/temp/job-123.pdf",
            source_type="pdf",
            filename="Raport Finalny.PDF",
            created_at="2026-04-22T12:00:00Z",
        )

        self.assertEqual(payload["job_id"], "job-123")
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["source_type"], "pdf")
        self.assertEqual(payload["download_name"], "Raport Finalny.epub")
        self.assertEqual(payload["created_at"], "2026-04-22T12:00:00Z")
        self.assertEqual(payload["updated_at"], "2026-04-22T12:00:00Z")
        self.assertEqual(payload["metadata"], {})
        self.assertEqual(payload["error"], "")

    def test_conversion_job_store_persists_and_reloads_terminal_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            jobs: dict[str, dict] = {}
            lock = threading.Lock()
            store = ConversionJobStore(jobs, lock, persistence_path=store_path)
            store.create(
                {
                    "job_id": "job-ready",
                    "status": "ready",
                    "message": "EPUB gotowy do pobrania.",
                    "created_at": "2026-04-25T10:00:00Z",
                    "updated_at": "2026-04-25T10:00:00Z",
                    "metadata": {"profile": "book_reflow"},
                    "error": "",
                }
            )

            reloaded_jobs: dict[str, dict] = {}
            reloaded_store = ConversionJobStore(reloaded_jobs, threading.Lock(), persistence_path=store_path)
            load_result = reloaded_store.load()

        self.assertTrue(load_result["loaded"])
        self.assertEqual(load_result["job_count"], 1)
        self.assertEqual(reloaded_jobs["job-ready"]["status"], "ready")
        self.assertEqual(reloaded_jobs["job-ready"]["metadata"]["profile"], "book_reflow")

    def test_conversion_job_store_marks_active_jobs_failed_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "jobs.json"
            store = ConversionJobStore({}, threading.Lock(), persistence_path=store_path)
            store.create(
                {
                    "job_id": "job-running",
                    "status": "running",
                    "message": "Konwertuje PDF do EPUB...",
                    "created_at": "2026-04-25T10:00:00Z",
                    "updated_at": "2026-04-25T10:00:00Z",
                    "source_path": "C:/temp/job-running.pdf",
                    "metadata": {},
                    "error": "",
                }
            )

            reloaded_jobs: dict[str, dict] = {}
            reloaded_store = ConversionJobStore(reloaded_jobs, threading.Lock(), persistence_path=store_path)
            load_result = reloaded_store.load()

        self.assertEqual(load_result["interrupted_jobs"], 1)
        self.assertEqual(reloaded_jobs["job-running"]["status"], "failed")
        self.assertIn("restart", reloaded_jobs["job-running"]["message"])
        self.assertEqual(reloaded_jobs["job-running"]["source_path"], "")

    def test_resolve_server_port_and_debug_mode_use_safe_env_defaults(self) -> None:
        self.assertEqual(resolve_server_port({}), 5001)
        self.assertEqual(resolve_server_port({"PORT": "5512"}), 5512)
        self.assertEqual(resolve_server_port({"PORT": "bad"}), 5001)
        self.assertEqual(resolve_server_port({"PORT": "70000"}), 5001)

        self.assertFalse(resolve_debug_mode({}))
        self.assertTrue(resolve_debug_mode({"DEBUG": "true"}))
        self.assertTrue(resolve_debug_mode({"FLASK_DEBUG": "1"}))
        self.assertFalse(resolve_debug_mode({"FLASK_DEBUG": "off"}))

    def test_pick_epubcheck_error_prefers_explicit_error_line(self) -> None:
        message = pick_epubcheck_error(
            [
                "Validating using EPUB version 3.3 rules.",
                "WARNING(HTM-045): sample warning",
                "ERROR(RSC-007): broken fragment target",
            ]
        )

        self.assertEqual(message, "ERROR(RSC-007): broken fragment target")
        self.assertEqual(pick_epubcheck_error([]), "Heading/TOC repair failed.")

    def test_run_document_conversion_keeps_base_epub_when_heading_repair_epubcheck_fails(self) -> None:
        base_epub = b"base-epub"
        convert_impl = Mock(
            return_value={
                "epub_bytes": base_epub,
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.92,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Raport koncowy",
                    "author": "Jan Kowalski",
                    "layout_mode": "reflowable",
                    "section_count": 5,
                    "asset_count": 1,
                },
            }
        )
        heading_repair_impl = Mock(
            return_value=SimpleNamespace(
                epub_bytes=b"repaired-epub",
                summary={
                    "release_status": "fail",
                    "toc_entries_before": 2,
                    "toc_entries_after": 6,
                    "headings_removed": 1,
                    "manual_review_count": 4,
                    "epubcheck_status": "failed",
                },
                epubcheck={
                    "status": "failed",
                    "messages": [
                        "Validating using EPUB version 3.3 rules.",
                        "ERROR(RSC-007): missing target",
                    ],
                },
            )
        )
        status_updates: list[tuple[str, str]] = []

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="pl",
                heading_repair_enabled=True,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
            status_callback=lambda status, message: status_updates.append((status, message)),
        )

        self.assertEqual(outcome.epub_bytes, base_epub)
        self.assertEqual(outcome.download_name, "sample.epub")
        self.assertEqual(outcome.heading_repair_report["status"], "failed")
        self.assertIn("missing target", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "failed")
        self.assertEqual(outcome.metadata["strategy"], "text_reflowable")
        self.assertEqual(
            status_updates,
            [
                ("running", "Konwertuje PDF do EPUB..."),
                ("repairing_headings", "Naprawiam headingi i TOC w EPUB..."),
            ],
        )

    def test_run_document_conversion_skips_heading_repair_for_diagram_book_profile(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"diagram-epub",
                "source_type": "pdf",
                "analysis": {
                    "profile": "diagram_book_reflow",
                    "confidence": 0.88,
                    "legacy_strategy": "hybrid",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "The Woodpecker Method",
                    "author": "Unknown",
                    "layout_mode": "reflowable",
                    "section_count": 20,
                    "asset_count": 1164,
                },
            }
        )
        heading_repair_impl = Mock()
        status_updates: list[tuple[str, str]] = []

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="woodpecker.pdf",
                source_type="pdf",
                original_filename="woodpecker.pdf",
                profile="auto-premium",
                language="en",
                heading_repair_enabled=True,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
            status_callback=lambda status, message: status_updates.append((status, message)),
        )

        self.assertEqual(outcome.epub_bytes, b"diagram-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "skipped")
        self.assertEqual(outcome.heading_repair_report["release_status"], "skipped")
        self.assertEqual(outcome.heading_repair_report["epubcheck_status"], "skipped")
        self.assertIn("diagram-heavy training book", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "skipped")
        heading_repair_impl.assert_not_called()
        self.assertEqual(status_updates, [("running", "Konwertuje PDF do EPUB...")])

    def test_run_document_conversion_omits_source_type_for_cli_style_requests(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"cli-epub",
                "analysis": {"profile": "docx_reflow", "confidence": 0.95},
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Docx Probe",
                    "author": "Codex QA",
                    "layout_mode": "reflowable",
                    "section_count": 2,
                    "asset_count": 1,
                },
            }
        )
        heading_repair_impl = Mock()

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.docx",
                original_filename="sample.docx",
                profile="auto-premium",
                language="pl",
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        convert_kwargs = convert_impl.call_args.kwargs
        self.assertNotIn("source_type", convert_kwargs)
        self.assertFalse(convert_kwargs["config"].force_ocr)
        self.assertEqual(outcome.download_name, "sample.epub")
        self.assertEqual(outcome.metadata["source_type"], "docx")
        heading_repair_impl.assert_not_called()

    def test_run_document_conversion_uses_repaired_epub_and_conversion_flags_when_heading_repair_succeeds(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"base-epub",
                "source_type": "pdf",
                "analysis": {
                    "profile": "fixed_layout_fallback",
                    "confidence": 0.81,
                    "legacy_strategy": "layout_fixed",
                },
                "quality_report": {
                    "validation_status": "passed_with_warnings",
                    "validation_tool": "epubcheck",
                    "warnings": ["Large raster pages."],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                    "render_budget_class": "fixed_layout_dense",
                    "render_budget_attempt": "fallback",
                    "size_budget_status": "passed_with_warnings",
                    "size_budget_message": "Fallback preset was required.",
                    "target_warn_bytes": 2048,
                    "target_hard_bytes": 4096,
                    "final_output_size_bytes": 3072,
                },
                "document_summary": {
                    "title": "Layout Probe",
                    "author": "Codex QA",
                    "layout_mode": "fixed-layout",
                    "section_count": 1,
                    "asset_count": 12,
                },
            }
        )
        heading_repair_impl = Mock(
            return_value=SimpleNamespace(
                epub_bytes=b"repaired-epub",
                summary={
                    "release_status": "pass",
                    "toc_entries_before": 1,
                    "toc_entries_after": 1,
                    "headings_removed": 0,
                    "manual_review_count": 0,
                    "epubcheck_status": "passed",
                },
                epubcheck={"status": "passed", "messages": []},
            )
        )

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="visual.pdf",
                source_type="pdf",
                original_filename="visual.pdf",
                profile="preserve-layout",
                language="en",
                force_ocr=True,
                heading_repair_enabled=True,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        convert_kwargs = convert_impl.call_args.kwargs
        self.assertTrue(convert_kwargs["config"].prefer_fixed_layout)
        self.assertTrue(convert_kwargs["config"].force_ocr)
        self.assertEqual(convert_kwargs["config"].language, "en")
        self.assertEqual(outcome.epub_bytes, b"repaired-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "applied")
        self.assertEqual(outcome.metadata["render_budget_class"], "fixed_layout_dense")
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "applied")
        self.assertEqual(outcome.metadata["strategy"], "layout_fixed")

    def test_run_document_conversion_marks_heading_repair_exception_as_failed_and_keeps_base_epub(self) -> None:
        convert_impl = Mock(
            return_value={
                "epub_bytes": b"base-epub",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.88,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "title": "Crash Probe",
                    "author": "Codex QA",
                    "layout_mode": "reflowable",
                    "section_count": 2,
                    "asset_count": 0,
                },
            }
        )
        heading_repair_impl = Mock(side_effect=RuntimeError("repair exploded"))

        outcome = run_document_conversion(
            ConversionRequest(
                source_path="sample.pdf",
                source_type="pdf",
                original_filename="sample.pdf",
                profile="auto-premium",
                language="pl",
                heading_repair_enabled=True,
            ),
            convert_impl=convert_impl,
            heading_repair_impl=heading_repair_impl,
        )

        self.assertEqual(outcome.epub_bytes, b"base-epub")
        self.assertEqual(outcome.heading_repair_report["status"], "failed")
        self.assertIn("repair exploded", outcome.heading_repair_report["error"])
        self.assertEqual(outcome.metadata["heading_repair"]["status"], "failed")
        self.assertIn("repair exploded", outcome.metadata["heading_repair"]["error"])

    def test_serve_http_app_uses_flask_runtime(self) -> None:
        application = SimpleNamespace(run=Mock())

        exit_code = serve_http_app(
            application,
            host="127.0.0.1",
            port=5001,
            debug=True,
            runtime="flask",
        )

        self.assertEqual(exit_code, 0)
        application.run.assert_called_once_with(debug=True, host="127.0.0.1", port=5001)

    def test_serve_http_app_uses_waitress_runtime(self) -> None:
        application = SimpleNamespace(run=Mock())
        waitress_module = SimpleNamespace(serve=Mock())

        with patch.dict(sys.modules, {"waitress": waitress_module}):
            exit_code = serve_http_app(
                application,
                host="127.0.0.1",
                port=5002,
                debug=False,
                runtime="waitress",
            )

        self.assertEqual(exit_code, 0)
        waitress_module.serve.assert_called_once_with(application, host="127.0.0.1", port=5002)
        application.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
