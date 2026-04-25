from __future__ import annotations

import json
import unittest

from app_runtime_services import build_conversion_metadata
from quality_state_service import (
    ConversionQualityStateRequest,
    assemble_quality_state,
    assemble_quality_state_dict,
)


class QualityStateServiceTests(unittest.TestCase):
    def test_ready_state_normalizes_current_runtime_metadata_contract(self) -> None:
        metadata = build_conversion_metadata(
            result={
                "source_type": "pdf",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.94,
                    "legacy_strategy": "text_reflowable",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": ["Manual table review needed."],
                    "high_risk_pages": [
                        {
                            "page_index": 12,
                            "title": "Tabela budzetowa",
                            "content_type": "table",
                            "risk_flags": ["manual-table-review"],
                        }
                    ],
                    "high_risk_sections": [
                        {
                            "title": "Finanse",
                            "page_range": [12, 14],
                            "risk_flags": ["complex-table-layout"],
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
        request = ConversionQualityStateRequest.from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "report.pdf",
                "message": "EPUB gotowy do pobrania.",
                "conversion": metadata,
                "output_size_bytes": 8192,
            },
            download_url="/convert/download/job-1",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertEqual(state.status, "ready")
        self.assertEqual(state.phase, "completed")
        self.assertTrue(state.is_terminal)
        self.assertTrue(state.quality_available)
        self.assertTrue(state.download_ready)
        self.assertEqual(state.download_url, "/convert/download/job-1")
        self.assertEqual(state.overall_severity, "warning")
        self.assertEqual(state.summary.profile, "book_reflow")
        self.assertEqual(state.summary.strategy, "text_reflowable")
        self.assertEqual(state.summary.sections, 5)
        self.assertEqual(state.summary.assets, 2)
        self.assertEqual(state.summary.output_size_bytes, 8192)
        self.assertEqual(state.validation.status, "passed")
        self.assertEqual(state.validation.tool, "epubcheck")
        self.assertEqual(state.heading_repair.status, "applied")
        self.assertEqual(state.heading_repair.release, "pass_with_review")
        self.assertEqual(state.heading_repair.toc_before, 2)
        self.assertEqual(state.heading_repair.toc_after, 5)
        self.assertEqual(state.heading_repair.review, 2)
        self.assertEqual(state.audit.warning_count, 1)
        self.assertEqual(state.audit.high_risk_pages, 1)
        self.assertEqual(state.audit.high_risk_sections, 1)
        self.assertEqual(state.audit.high_risk_page_list[0].page, 12)
        self.assertEqual(state.audit.high_risk_section_list[0].pages, (12, 14))
        self.assertEqual(state.render_budget.budget_class, "fixed_layout_dense")
        self.assertEqual(state.render_budget.attempt, "fallback")
        self.assertEqual(state.size_budget.status, "passed_with_warnings")
        self.assertEqual(state.raw_signals.warning_count, 1)
        self.assertEqual(state.raw_signals.heading_review_count, 2)
        self.assertEqual(state.raw_signals.output_size_bytes, 8192)
        self.assertEqual(state.verdict.status, "passed_with_warnings")
        self.assertEqual(state.verdict.severity, "warning")
        self.assertTrue(state.verdict.requires_manual_review)
        self.assertFalse(state.verdict.blocks_download)
        self.assertEqual(
            [alert.code for alert in state.alerts],
            ["size_budget_warning", "manual_review_needed", "quality_warning"],
        )
        self.assertEqual(payload["summary"]["output_size_bytes"], 8192)
        self.assertEqual(payload["raw_signals"]["warning_count"], 1)
        self.assertEqual(payload["verdict"]["status"], "passed_with_warnings")
        json.dumps(payload)

    def test_failed_state_surfaces_terminal_error_without_quality_payload(self) -> None:
        request = ConversionQualityStateRequest(
            job_status="failed",
            source_type="docx",
            filename="broken.docx",
            message="Konwersja nie powiodla sie.",
            error="timeout while reading source",
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.phase, "failed")
        self.assertTrue(state.is_terminal)
        self.assertFalse(state.quality_available)
        self.assertFalse(state.download_ready)
        self.assertEqual(state.overall_severity, "error")
        self.assertEqual(state.verdict.status, "failed")
        self.assertTrue(state.verdict.blocks_download)
        self.assertEqual(state.validation.status, "unavailable")
        self.assertEqual(len(state.alerts), 1)
        self.assertEqual(state.alerts[0].code, "conversion_failed")
        self.assertIn("timeout", state.alerts[0].message)

    def test_progress_state_stays_non_terminal_and_quality_safe(self) -> None:
        request = ConversionQualityStateRequest(
            job_status="repairing_headings",
            source_type="pdf",
            filename="sample.pdf",
            message="Naprawiam headingi i TOC w EPUB...",
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.phase, "heading_repair")
        self.assertFalse(state.is_terminal)
        self.assertFalse(state.quality_available)
        self.assertFalse(state.download_ready)
        self.assertEqual(state.overall_severity, "info")
        self.assertEqual(state.heading_repair.status, "unavailable")
        self.assertEqual(state.audit.warning_count, 0)
        self.assertEqual(state.alerts, ())

    def test_malformed_metadata_is_safely_normalized(self) -> None:
        request = ConversionQualityStateRequest(
            job_status="READY",
            conversion_metadata={
                "profile": 17,
                "confidence": "bad",
                "validation": "FAILED",
                "validation_tool": None,
                "sections": -3,
                "assets": "oops",
                "layout": None,
                "warning_list": "not-a-list",
                "warnings": -8,
                "high_risk_page_list": [{"page": "x", "title": 9, "kind": None, "flags": "bad"}],
                "high_risk_sections": "bad-shape",
                "heading_repair": {
                    "status": None,
                    "release": 5,
                    "toc_before": "bad",
                    "toc_after": 3.8,
                    "removed": -1,
                    "review": "bad",
                    "epubcheck": None,
                    "error": 7,
                },
                "size_budget_status": "FAILED",
                "size_budget_message": 123,
            },
            output_size_bytes=-5,
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.status, "ready")
        self.assertTrue(state.quality_available)
        self.assertEqual(state.summary.profile, "unknown")
        self.assertEqual(state.summary.confidence, 0.0)
        self.assertEqual(state.summary.sections, 0)
        self.assertEqual(state.summary.assets, 0)
        self.assertIsNone(state.summary.output_size_bytes)
        self.assertEqual(state.validation.status, "failed")
        self.assertEqual(state.validation.tool, "unknown")
        self.assertEqual(state.heading_repair.status, "skipped")
        self.assertEqual(state.heading_repair.toc_after, 3)
        self.assertEqual(state.heading_repair.removed, 0)
        self.assertEqual(state.audit.warning_count, 0)
        self.assertEqual(state.audit.high_risk_pages, 0)
        self.assertEqual(state.size_budget.status, "failed")
        self.assertEqual([alert.code for alert in state.alerts], ["validation_failed", "size_budget_failed"])

    def test_raw_conversion_payload_shape_is_normalized_without_flattening(self) -> None:
        request = ConversionQualityStateRequest.from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "legacy-shape.pdf",
                "conversion": {
                    "source_type": "pdf",
                    "analysis": {
                        "profile": "book_reflow",
                        "confidence": 0.91,
                        "legacy_strategy": "text_reflowable",
                        "render_budget_class": "fixed_layout_balanced",
                    },
                    "quality_report": {
                        "validation_status": "passed",
                        "validation_tool": "epubcheck",
                        "warnings": ["Legacy warning surfaced."],
                        "high_risk_pages": [
                            {
                                "page_index": 7,
                                "title": "Tabela",
                                "content_type": "table",
                                "risk_flags": ["manual-table-review"],
                            }
                        ],
                        "high_risk_sections": [
                            {
                                "title": "Aneks",
                                "page_range": [7, 9],
                                "risk_flags": ["complex-layout"],
                            }
                        ],
                        "render_budget_attempt": "primary",
                        "size_budget_status": "passed_with_warnings",
                        "size_budget_message": "Budget warning from raw payload.",
                        "target_warn_bytes": 1024,
                        "target_hard_bytes": 2048,
                        "final_output_size_bytes": 1536,
                    },
                    "document_summary": {
                        "layout_mode": "reflowable",
                        "section_count": 4,
                        "asset_count": 1,
                    },
                    "heading_repair_report": {
                        "status": "applied",
                        "release_status": "pass_with_review",
                        "toc_entries_before": 1,
                        "toc_entries_after": 4,
                        "headings_removed": 0,
                        "manual_review_count": 1,
                        "epubcheck_status": "passed",
                        "error": "",
                    },
                },
            },
            download_url="/convert/download/raw-shape",
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.summary.profile, "book_reflow")
        self.assertEqual(state.summary.strategy, "text_reflowable")
        self.assertEqual(state.summary.confidence, 0.91)
        self.assertEqual(state.summary.sections, 4)
        self.assertEqual(state.summary.assets, 1)
        self.assertEqual(state.summary.output_size_bytes, 1536)
        self.assertEqual(state.validation.status, "passed")
        self.assertEqual(state.validation.tool, "epubcheck")
        self.assertEqual(state.heading_repair.release, "pass_with_review")
        self.assertEqual(state.heading_repair.toc_before, 1)
        self.assertEqual(state.heading_repair.toc_after, 4)
        self.assertEqual(state.heading_repair.review, 1)
        self.assertEqual(state.audit.warning_count, 1)
        self.assertEqual(state.audit.high_risk_pages, 1)
        self.assertEqual(state.audit.high_risk_page_list[0].page, 7)
        self.assertEqual(state.audit.high_risk_page_list[0].kind, "table")
        self.assertEqual(state.audit.high_risk_section_list[0].pages, (7, 9))
        self.assertEqual(state.render_budget.budget_class, "fixed_layout_balanced")
        self.assertEqual(state.render_budget.attempt, "primary")
        self.assertEqual(state.render_budget.target_warn_bytes, 1024)
        self.assertEqual(state.size_budget.status, "passed_with_warnings")
        self.assertEqual(state.download_url, "/convert/download/raw-shape")
        self.assertEqual(
            [alert.code for alert in state.alerts],
            ["size_budget_warning", "manual_review_needed", "quality_warning"],
        )

    def test_ready_state_reports_diagram_book_size_budget_metadata(self) -> None:
        metadata = build_conversion_metadata(
            result={
                "source_type": "pdf",
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
                    "target_warn_bytes": 8192,
                    "target_hard_bytes": 12288,
                    "final_output_size_bytes": 9216,
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
        request = ConversionQualityStateRequest.from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "woodpecker.pdf",
                "message": "EPUB gotowy do pobrania.",
                "conversion": metadata,
                "output_size_bytes": 9216,
            },
            download_url="/convert/download/woodpecker",
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.summary.profile, "diagram_book_reflow")
        self.assertEqual(state.summary.strategy, "image-first-reflow")
        self.assertEqual(state.summary.assets, 224)
        self.assertEqual(state.render_budget.budget_class, "diagram_book_reflow_balanced")
        self.assertEqual(state.render_budget.attempt, "primary")
        self.assertEqual(state.size_budget.status, "passed_with_warnings")
        self.assertEqual(state.size_budget.message, "Diagram-heavy output is near the warn threshold.")
        self.assertEqual(state.heading_repair.status, "skipped")
        self.assertIn("diagram-heavy training book", state.heading_repair.error)
        self.assertIn("size_budget_warning", [alert.code for alert in state.alerts])

    def test_skipped_heading_repair_reason_is_preserved_without_failed_alert(self) -> None:
        metadata = build_conversion_metadata(
            result={
                "analysis": {
                    "profile": "diagram_book_reflow",
                    "confidence": 0.85,
                    "legacy_strategy": "image-first-reflow",
                },
                "quality_report": {
                    "validation_status": "passed",
                    "validation_tool": "epubcheck",
                    "warnings": [],
                    "high_risk_pages": [],
                    "high_risk_sections": [],
                },
                "document_summary": {
                    "layout_mode": "reflowable",
                    "section_count": 12,
                    "asset_count": 180,
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
                "error": "Skipped for diagram-heavy training book to avoid TOC churn.",
            },
        )
        request = ConversionQualityStateRequest.from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "woodpecker.pdf",
                "message": "EPUB gotowy do pobrania.",
                "conversion": metadata,
            },
            download_url="/convert/download/woodpecker",
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.heading_repair.status, "skipped")
        self.assertEqual(state.heading_repair.release, "skipped")
        self.assertIn("diagram-heavy training book", state.heading_repair.error)
        self.assertNotIn("heading_repair_failed", [alert.code for alert in state.alerts])


if __name__ == "__main__":
    unittest.main()
