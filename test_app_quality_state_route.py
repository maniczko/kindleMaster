from __future__ import annotations

import unittest
import os
from datetime import UTC, datetime

import app as app_module
from app import app
from app_runtime_services import build_conversion_metadata


class AppQualityStateRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.cleanup_job_ids: list[str] = []
        self.cleanup_paths: list[str] = []

    def tearDown(self) -> None:
        for path in self.cleanup_paths:
            if path and os.path.exists(path):
                os.remove(path)
        with app_module._CONVERSION_JOBS_LOCK:
            for job_id in self.cleanup_job_ids:
                app_module._CONVERSION_JOBS.pop(job_id, None)

    def _write_output_fixture(self, job_id: str, size_bytes: int) -> str:
        output_path = os.path.join(app_module.UPLOAD_DIR, f"{job_id}.epub")
        with open(output_path, "wb") as handle:
            handle.write(b"x" * max(1, size_bytes))
        self.cleanup_paths.append(output_path)
        return output_path

    def _register_job(
        self,
        job_id: str,
        *,
        status: str,
        source_type: str = "pdf",
        filename: str = "sample.pdf",
        message: str,
        metadata: dict | None = None,
        output_size_bytes: int = 0,
        output_path: str = "",
        output_path_exists: bool | None = None,
        error: str = "",
        sentry_event_id: str = "",
    ) -> None:
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if status == "ready" and not output_path and output_path_exists is not False:
            output_path = self._write_output_fixture(job_id, output_size_bytes)
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": status,
                "message": message,
                "source_type": source_type,
                "filename": filename,
                "created_at": created_at,
                "updated_at": created_at,
                "source_path": "",
                "output_path": output_path,
                "download_name": filename.rsplit(".", 1)[0] + ".epub",
                "metadata": metadata or {},
                "output_size_bytes": output_size_bytes,
                "error": error,
                "sentry_event_id": sentry_event_id,
            }
        self.cleanup_job_ids.append(job_id)

    def test_convert_status_includes_normalized_quality_state(self) -> None:
        metadata = build_conversion_metadata(
            result={
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.94,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": ["Manual table review needed."],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                    "render_budget_class": "fixed_layout_dense",
                    "render_budget_attempt": "fallback",
                    "size_budget_status": "passed_with_warnings",
                    "size_budget_message": "Fallback preset was required.",
                },
                "document_summary": {
                    "layout_mode": "reflowable",
                    "section_count": 5,
                    "asset_count": 2,
                },
            },
            detected_source_type="pdf",
            heading_repair_enabled=True,
            heading_repair_report={
                "status": "applied",
                "release_status": "pass_with_review",
                "toc_entries_before": 2,
                "toc_entries_after": 5,
                "headings_removed": 1,
                "manual_review_count": 2,
                "epubcheck_status": "passed",
                "error": "",
            },
        )
        self._register_job(
            "quality-ready",
            status="ready",
            message="EPUB gotowy do pobrania.",
            metadata=metadata,
            output_size_bytes=8192,
        )

        response = self.client.get("/convert/status/quality-ready")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["conversion"]["profile"], "book_reflow")
        self.assertEqual(payload["quality_state_url"], "/convert/quality/quality-ready")
        self.assertEqual(payload["quality_state"]["status"], "ready")
        self.assertEqual(payload["quality_state"]["phase"], "completed")
        self.assertTrue(payload["quality_state"]["quality_available"])
        self.assertTrue(payload["quality_state"]["download_ready"])
        self.assertTrue(payload["quality_state"]["download_available"])
        self.assertEqual(payload["quality_state"]["download_state"]["status"], "available")
        self.assertEqual(payload["quality_state"]["reading_verdict"], "ready_with_review")
        self.assertEqual(payload["quality_state"]["release_verdict"], "ready_with_review")
        self.assertFalse(payload["quality_state"]["release_blocked"])
        self.assertEqual(payload["quality_state"]["quality_blockers"], [])
        self.assertFalse(payload["quality_state"]["send_to_kindle_ready"])
        self.assertEqual(
            payload["quality_state"]["send_to_kindle_blockers"][0]["code"],
            "kindle_delivery_release_not_ready",
        )
        self.assertEqual(payload["quality_state"]["download_url"], "/convert/download/quality-ready")
        self.assertTrue(payload["download_available"])
        self.assertEqual(payload["download_state"]["status"], "available")
        self.assertEqual(payload["quality_state"]["summary"]["profile"], "book_reflow")
        self.assertEqual(payload["quality_state"]["summary"]["output_size_bytes"], 8192)
        self.assertEqual(payload["quality_state"]["raw_signals"]["warning_count"], 1)
        self.assertEqual(payload["quality_state"]["verdict"]["status"], "passed_with_warnings")
        self.assertFalse(payload["quality_state"]["verdict"]["blocks_download"])
        self.assertEqual(
            [alert["code"] for alert in payload["quality_state"]["alerts"]],
            ["size_budget_warning", "quality_warning"],
        )

    def test_convert_quality_route_includes_ready_quality_cockpit_contract(self) -> None:
        metadata = build_conversion_metadata(
            result={
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.91,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed_with_warnings",
                    "validation_tool": "epubcheck",
                    "warnings": ["Publisher metadata is missing."],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "layout_mode": "reflowable",
                    "section_count": 12,
                    "asset_count": 7,
                },
            },
            detected_source_type="pdf",
            heading_repair_enabled=True,
            heading_repair_report={
                "status": "applied",
                "release_status": "pass",
                "toc_entries_before": 3,
                "toc_entries_after": 12,
                "headings_removed": 1,
                "manual_review_count": 0,
                "epubcheck_status": "passed_with_warnings",
                "error": "",
            },
        )
        metadata.update(
            {
                "epubcheck_detail": {
                    "status": "passed_with_warnings",
                    "tool": "epubcheck",
                    "error_count": 0,
                    "warning_count": 2,
                    "messages": ["OPF metadata warning"],
                },
                "toc_preview": {
                    "status": "ready",
                    "total": 3,
                    "entries": [
                        {"label": "Introduction", "href": "chapter1.xhtml"},
                        {"label": "Methods", "href": "chapter2.xhtml"},
                    ],
                },
                "asset_summary": {
                    "image_count": 5,
                    "table_count": 2,
                    "figure_count": 3,
                    "oversized_count": 1,
                },
                "metadata_summary": {
                    "title": "Quality Cockpit Sample",
                    "author": "KindleMaster QA",
                    "language": "pl",
                },
                "metadata_health": {
                    "status": "passed_with_warnings",
                    "count": 1,
                    "message": "Publisher metadata is missing.",
                    "placeholder_count": 1,
                },
                "link_health": {
                    "status": "passed",
                    "broken_count": 0,
                    "checked_count": 12,
                    "message": "12 links checked.",
                },
                "visible_junk": {
                    "status": "passed",
                    "count": 0,
                    "samples": [],
                    "message": "No visible junk detected.",
                },
            }
        )
        self._register_job(
            "quality-cockpit-ready",
            status="ready",
            message="EPUB gotowy do pobrania.",
            metadata=metadata,
            output_size_bytes=16384,
        )

        response = self.client.get("/convert/quality/quality-cockpit-ready")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        quality_state = payload["quality_state"]
        self.assertEqual(quality_state["summary"]["profile"], "book_reflow")
        for cockpit_key in (
            "issue_groups",
            "epubcheck_detail",
            "toc_preview",
            "asset_summary",
            "metadata_summary",
            "metadata_health",
            "link_health",
            "visible_junk",
        ):
            self.assertIn(cockpit_key, quality_state)
        self.assertEqual(
            [issue["code"] for issue in quality_state["issue_groups"]["warnings"]],
            ["audit_warning", "metadata_placeholder"],
        )
        self.assertEqual(
            quality_state["issue_groups"]["warnings"][1]["message"],
            "Metadata contains 1 placeholder value.",
        )
        self.assertEqual(quality_state["epubcheck_detail"]["warning_count"], 2)
        self.assertEqual(quality_state["toc_preview"]["entries"][0]["label"], "Introduction")
        self.assertEqual(quality_state["asset_summary"]["table_count"], 2)
        self.assertEqual(quality_state["metadata_summary"]["title"], "Quality Cockpit Sample")
        self.assertEqual(quality_state["metadata_health"]["status"], "passed_with_warnings")
        self.assertEqual(quality_state["link_health"]["broken_count"], 0)
        self.assertEqual(quality_state["visible_junk"]["status"], "passed")
        self.assertTrue(quality_state["download_available"])
        self.assertEqual(quality_state["download_state"]["status"], "available")
        self.assertEqual(quality_state["reading_verdict"], "ready_with_review")
        self.assertEqual(quality_state["release_verdict"], "ready_with_review")
        self.assertFalse(quality_state["release_blocked"])
        self.assertFalse(quality_state["send_to_kindle_ready"])

    def test_convert_quality_route_separates_download_from_release_blocker(self) -> None:
        self._register_job(
            "quality-release-blocked",
            status="ready",
            message="EPUB gotowy do pobrania.",
            metadata={
                "source_type": "pdf",
                "profile": "book_reflow",
                "validation": "passed",
                "validation_tool": "epubcheck",
                "text_cleanup": {
                    "status": "passed_with_warnings",
                    "blocked_count": 1,
                },
            },
            output_size_bytes=4096,
        )

        response = self.client.get("/convert/quality/quality-release-blocked")

        self.assertEqual(response.status_code, 200)
        quality_state = response.get_json()["quality_state"]
        self.assertTrue(quality_state["download_available"])
        self.assertEqual(quality_state["reading_verdict"], "ready_with_review")
        self.assertEqual(quality_state["release_verdict"], "release_blocked")
        self.assertTrue(quality_state["release_blocked"])
        self.assertEqual(
            [item["code"] for item in quality_state["quality_blockers"]],
            ["text_cleanup_blocked"],
        )
        self.assertFalse(quality_state["verdict"]["blocks_download"])

    def test_convert_quality_route_defaults_missing_optional_cockpit_data(self) -> None:
        self._register_job(
            "quality-cockpit-minimal",
            status="ready",
            message="EPUB gotowy do pobrania.",
            metadata={
                "source_type": "pdf",
                "profile": "book_reflow",
                "validation": "passed",
                "validation_tool": "epubcheck",
            },
            output_size_bytes=4096,
            output_path_exists=False,
        )

        response = self.client.get("/convert/quality/quality-cockpit-minimal")

        self.assertEqual(response.status_code, 200)
        quality_state = response.get_json()["quality_state"]
        self.assertEqual(quality_state.get("issue_groups"), {"blockers": [], "warnings": [], "review": []})
        self.assertEqual(quality_state.get("epubcheck_detail", {}).get("status"), "not_reported")
        self.assertEqual(quality_state.get("toc_preview", {}).get("status"), "not_reported")
        self.assertEqual(quality_state.get("asset_summary", {}).get("status"), "not_reported")
        self.assertEqual(quality_state.get("metadata_summary", {}).get("status"), "not_reported")
        self.assertEqual(quality_state.get("metadata_health", {}).get("status"), "not_reported")
        self.assertEqual(quality_state.get("link_health", {}).get("status"), "not_reported")
        self.assertEqual(quality_state.get("visible_junk", {}).get("status"), "not_reported")

    def test_convert_quality_route_returns_failed_terminal_state(self) -> None:
        self._register_job(
            "quality-failed",
            status="failed",
            filename="broken.docx",
            source_type="docx",
            message="Konwersja nie powiodla sie.",
            error="timeout while reading source",
            sentry_event_id="event-failed-1",
        )

        response = self.client.get("/convert/quality/quality-failed")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["job_id"], "quality-failed")
        self.assertEqual(payload["quality_state"]["status"], "failed")
        self.assertEqual(payload["quality_state"]["phase"], "failed")
        self.assertFalse(payload["quality_state"]["quality_available"])
        self.assertFalse(payload["quality_state"]["download_available"])
        self.assertEqual(payload["quality_state"]["reading_verdict"], "failed")
        self.assertEqual(payload["quality_state"]["release_verdict"], "failed")
        self.assertTrue(payload["quality_state"]["release_blocked"])
        self.assertEqual(payload["quality_state"]["quality_blockers"][0]["code"], "conversion_failed")
        self.assertEqual(payload["quality_state"]["overall_severity"], "error")
        self.assertEqual(payload["quality_state"]["source_type"], "docx")
        self.assertEqual(payload["quality_state"]["score"], 0)
        self.assertFalse(payload["quality_state"]["sendable"])
        self.assertFalse(payload["quality_state"]["kindle_ready"])
        self.assertFalse(payload["quality_state"]["premium_ready"])
        self.assertEqual(payload["quality_state"]["blockers"][0]["code"], "conversion_failed")
        self.assertEqual(payload["quality_state"]["warnings"], [])
        self.assertEqual(payload["quality_state"]["reports"], {})
        self.assertEqual(payload["quality_state"]["artifacts"], {})
        self.assertEqual(payload["quality_state"]["sentry_event_id"], "event-failed-1")
        self.assertEqual(
            [alert["code"] for alert in payload["quality_state"]["alerts"]],
            ["conversion_failed"],
        )

    def test_convert_quality_route_normalizes_raw_conversion_metadata_shape(self) -> None:
        self._register_job(
            "quality-raw-shape",
            status="ready",
            message="EPUB gotowy do pobrania.",
            metadata={
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.87,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": ["Legacy warning surfaced."],
                    "high_risk_pages": [
                        {
                            "page_index": 3,
                            "title": "Tabela",
                            "content_type": "table",
                            "risk_flags": ["manual-table-review"],
                        }
                    ],
                    "high_risk_sections": [
                        {
                            "title": "Aneks",
                            "page_range": [3, 5],
                            "risk_flags": ["complex-layout"],
                        }
                    ],
                    "render_budget_class": "fixed_layout_dense",
                    "render_budget_attempt": "fallback",
                    "size_budget_status": "passed_with_warnings",
                    "size_budget_message": "Fallback preset was required.",
                    "target_warn_bytes": 2048,
                    "target_hard_bytes": 4096,
                    "final_output_size_bytes": 3072,
                },
                "document_summary": {
                    "layout_mode": "reflowable",
                    "section_count": 5,
                    "asset_count": 2,
                },
                "heading_repair_report": {
                    "status": "applied",
                    "release_status": "pass_with_review",
                    "toc_entries_before": 2,
                    "toc_entries_after": 5,
                    "headings_removed": 1,
                    "manual_review_count": 2,
                    "epubcheck_status": "passed",
                    "error": "",
                },
            },
            output_size_bytes=8192,
        )

        response = self.client.get("/convert/quality/quality-raw-shape")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["quality_state"]["quality_available"])
        self.assertEqual(payload["quality_state"]["summary"]["profile"], "book_reflow")
        self.assertEqual(payload["quality_state"]["summary"]["strategy"], "text_reflowable")
        self.assertEqual(payload["quality_state"]["summary"]["output_size_bytes"], 8192)
        self.assertEqual(payload["quality_state"]["validation"]["tool"], "epubcheck")
        self.assertEqual(payload["quality_state"]["heading_repair"]["toc_after"], 5)
        self.assertEqual(payload["quality_state"]["audit"]["high_risk_pages"], 1)
        self.assertEqual(payload["quality_state"]["audit"]["high_risk_page_list"][0]["page"], 3)
        self.assertEqual(payload["quality_state"]["audit"]["high_risk_section_list"][0]["pages"], [3, 5])
        self.assertEqual(payload["quality_state"]["render_budget"]["budget_class"], "fixed_layout_dense")
        self.assertEqual(
            [alert["code"] for alert in payload["quality_state"]["alerts"]],
            ["size_budget_warning", "manual_review_needed", "quality_warning"],
        )

    def test_convert_quality_route_surfaces_heading_skip_reason_for_diagram_books(self) -> None:
        metadata = build_conversion_metadata(
            result={
                "analysis": {
                    "profile": "diagram_book_reflow",
                    "confidence": 0.88,
                    "legacy_strategy": "image-first-reflow",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                    "size_budget_key": "diagram_book_reflow_balanced",
                    "render_budget_attempt": "primary",
                    "size_budget_status": "passed_with_warnings",
                    "size_budget_message": "Diagram-heavy output is near the warn threshold.",
                },
                "document_summary": {
                    "layout_mode": "reflowable",
                    "section_count": 18,
                    "asset_count": 224,
                },
            },
            detected_source_type="pdf",
            heading_repair_enabled=True,
            heading_repair_report={
                "status": "skipped",
                "release_status": "skipped",
                "toc_entries_before": 0,
                "toc_entries_after": 0,
                "headings_removed": 0,
                "manual_review_count": 0,
                "epubcheck_status": "skipped",
                "error": "Skipped for diagram-heavy training book to avoid noisy TOC churn.",
            },
        )
        self._register_job(
            "quality-skip-reason",
            status="ready",
            message="EPUB gotowy do pobrania.",
            metadata=metadata,
            output_size_bytes=9216,
        )

        response = self.client.get("/convert/quality/quality-skip-reason")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["quality_state"]["summary"]["profile"], "diagram_book_reflow")
        self.assertEqual(payload["quality_state"]["size_budget"]["status"], "passed_with_warnings")
        self.assertEqual(payload["quality_state"]["render_budget"]["budget_class"], "diagram_book_reflow_balanced")
        self.assertEqual(payload["quality_state"]["heading_repair"]["status"], "skipped")
        self.assertIn(
            "diagram-heavy training book",
            payload["quality_state"]["heading_repair"]["error"],
        )
        self.assertNotIn(
            "heading_repair_failed",
            [alert["code"] for alert in payload["quality_state"]["alerts"]],
        )

    def test_convert_quality_route_returns_404_for_unknown_job(self) -> None:
        response = self.client.get("/convert/quality/missing-job")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Nie znaleziono zadania konwersji.")


if __name__ == "__main__":
    unittest.main()
