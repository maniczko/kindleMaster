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
    def _request_from_job_payload(
        self,
        payload: dict,
        *,
        download_url: str,
    ) -> ConversionQualityStateRequest:
        enriched = dict(payload)
        if str(enriched.get("status", "") or "").strip().lower() == "ready":
            enriched.setdefault("output_path_exists", True)
        return ConversionQualityStateRequest.from_job_payload(
            enriched,
            download_url=download_url,
        )

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
        request = self._request_from_job_payload(
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
        self.assertTrue(state.download_available)
        self.assertEqual(state.download_state.status, "available")
        self.assertEqual(state.download_url, "/convert/download/job-1")
        self.assertEqual(state.reading_verdict, "ready_with_review")
        self.assertEqual(state.release_verdict, "ready_with_review")
        self.assertFalse(state.release_blocked)
        self.assertEqual(state.quality_blockers, ())
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
        self.assertTrue(payload["download_available"])
        self.assertEqual(payload["download_state"]["status"], "available")
        self.assertEqual(payload["reading_verdict"], "ready_with_review")
        self.assertEqual(payload["release_verdict"], "ready_with_review")
        self.assertFalse(payload["release_blocked"])
        self.assertEqual(payload["quality_blockers"], [])
        self.assertEqual(payload["user_facing_verdict"]["decision"], "review")
        self.assertEqual(payload["user_facing_verdict"]["label"], "Kontrola")
        self.assertEqual(payload["user_facing_verdict"]["download_label"], "Pobierz EPUB")
        self.assertGreaterEqual(len(payload["user_facing_reasons"]), 1)
        self.assertFalse(payload["send_to_kindle_ready"])
        self.assertEqual(payload["send_to_kindle_blockers"][0]["code"], "kindle_delivery_release_not_ready")
        self.assertEqual(payload["verdict"]["status"], "passed_with_warnings")
        json.dumps(payload)

    def test_ready_state_with_missing_output_is_not_download_available(self) -> None:
        request = ConversionQualityStateRequest.from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "missing-output.pdf",
                "message": "EPUB gotowy do pobrania.",
                "metadata": {"profile": "book_reflow", "validation": "passed"},
                "output_path": "missing-output.epub",
            },
            download_url="/convert/download/missing-output",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertFalse(state.download_ready)
        self.assertFalse(state.download_available)
        self.assertEqual(state.download_state.status, "missing_output")
        self.assertEqual(state.download_url, "")
        self.assertFalse(payload["download_available"])
        self.assertEqual(payload["download_state"]["status"], "missing_output")
        self.assertIsNone(payload["download_state"]["download_url"])

    def test_ready_epub_can_download_even_when_release_quality_is_blocked(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "cleanup.pdf",
                "message": "EPUB gotowy do pobrania.",
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "text_cleanup": {
                        "status": "passed_with_warnings",
                        "blocked_count": 1,
                    },
                },
            },
            download_url="/convert/download/cleanup-job",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertTrue(state.download_available)
        self.assertEqual(state.download_state.status, "available")
        self.assertEqual(state.reading_verdict, "ready_with_review")
        self.assertEqual(state.release_verdict, "release_blocked")
        self.assertTrue(state.release_blocked)
        self.assertEqual([item["code"] for item in state.quality_blockers], ["text_cleanup_blocked"])
        self.assertEqual(state.overall_severity, "error")
        self.assertEqual(state.verdict.status, "failed")
        self.assertFalse(state.verdict.blocks_download)
        self.assertTrue(payload["download_available"])
        self.assertEqual(payload["reading_verdict"], "ready_with_review")
        self.assertEqual(payload["release_verdict"], "release_blocked")
        self.assertTrue(payload["release_blocked"])
        self.assertEqual(payload["quality_blockers"][0]["source"], "text_cleanup")
        self.assertEqual(payload["user_facing_verdict"]["decision"], "blocked")
        self.assertEqual(payload["user_facing_verdict"]["label"], "Nie publikuj")
        self.assertEqual(payload["user_facing_verdict"]["download_label"], "Pobierz szkic EPUB do kontroli")
        self.assertEqual(payload["user_facing_reasons"][0]["code"], "text_cleanup_blocked")
        self.assertFalse(payload["send_to_kindle_ready"])
        self.assertEqual(
            [item["code"] for item in payload["send_to_kindle_blockers"]],
            [
                "kindle_delivery_release_not_ready",
                "kindle_delivery_validation_failed",
                "kindle_delivery_not_verified",
            ],
        )

    def test_strict_premium_score_blocks_kindle_ready_without_blocking_download(self) -> None:
        request = self._request_from_job_payload(
            {
                "job_id": "magazine-job",
                "status": "ready",
                "source_type": "epub",
                "filename": "magazine.epub",
                "message": "EPUB gotowy do pobrania.",
                "sentry_event_id": "event-123",
                "metadata": {
                    "profile": "magazine_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "premium_scoring": {
                        "status": "failed",
                        "technical_valid": True,
                        "mail_sendable": "likely",
                        "kindle_ready": False,
                        "premium_ready": False,
                        "premium_score": 5.0,
                        "issues": [
                            {
                                "severity": "blocker",
                                "code": "suspicious_metadata_author",
                                "message": "Creator looks like a section label.",
                                "source": "metadata",
                                "suggested_action": "Fix author metadata.",
                            }
                        ],
                    },
                    "ai_quality_verification": {
                        "status": "failed",
                        "decision": "block",
                        "confidence": 0.91,
                        "model_version": "quality-verifier-v1-bootstrap",
                        "features_hash": "abc123",
                        "reason_codes": ["premium-blockers"],
                    },
                },
            },
            download_url="/convert/download/magazine-job",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertTrue(state.download_available)
        self.assertEqual(state.reading_verdict, "ready_with_review")
        self.assertEqual(state.release_verdict, "release_blocked")
        self.assertTrue(state.release_blocked)
        self.assertEqual(state.premium_scoring["premium_score"], 5.0)
        self.assertEqual(payload["premium_scoring"]["kindle_ready"], False)
        self.assertEqual(payload["ai_quality_verification"]["decision"], "block")
        self.assertEqual(payload["ai_quality_verification"]["features_hash"], "abc123")
        self.assertIn("suspicious_metadata_author", [item["code"] for item in payload["quality_blockers"]])
        self.assertFalse(payload["send_to_kindle_ready"])
        self.assertEqual(payload["score"], 5.0)
        self.assertTrue(payload["sendable"])
        self.assertFalse(payload["kindle_ready"])
        self.assertFalse(payload["premium_ready"])
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["blockers"][0]["code"], "suspicious_metadata_author")
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(
            payload["reports"],
            {
                "json": "/convert/report/magazine-job.json",
                "markdown": "/convert/report/magazine-job.md",
            },
        )
        self.assertEqual(
            payload["artifacts"],
            {
                "download_url": "/convert/download/magazine-job",
            },
        )
        self.assertEqual(payload["sentry_event_id"], "event-123")

    def test_draft_quality_gate_blocks_release_not_download_even_when_score_passes(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "draft.pdf",
                "message": "EPUB gotowy do pobrania.",
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "quality_gate_mode": "draft",
                    "premium_scoring": {
                        "status": "passed",
                        "technical_valid": True,
                        "mail_sendable": "likely",
                        "kindle_ready": True,
                        "premium_ready": True,
                        "premium_score": 9.3,
                        "issues": [],
                    },
                    "ai_quality_verification": {
                        "status": "passed",
                        "decision": "ready",
                        "confidence": 0.95,
                        "features_hash": "readyhash",
                    },
                },
            },
            download_url="/convert/download/draft-job",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertTrue(state.download_available)
        self.assertEqual(state.release_verdict, "release_blocked")
        self.assertTrue(state.release_blocked)
        self.assertEqual(state.quality_gate_mode, "draft")
        self.assertEqual(payload["quality_gate_mode"], "draft")
        self.assertEqual(payload["quality_blockers"][0]["code"], "runtime_quality_gate_draft")
        self.assertEqual(payload["user_facing_verdict"]["label"], "Nie publikuj")
        self.assertEqual(payload["user_facing_verdict"]["download_label"], "Pobierz szkic EPUB do kontroli")
        self.assertFalse(payload["send_to_kindle_ready"])

    def test_semantic_ocr_and_reading_order_gates_block_release_not_download(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "ocr-heavy.pdf",
                "message": "EPUB gotowy do pobrania.",
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "semantic_cleanup": {
                        "status": "failed",
                        "message": "Semantic cleanup failed paragraph structure.",
                    },
                    "ocr_quality": {
                        "status": "degraded",
                        "degraded_count": 2,
                    },
                    "reading_order": {
                        "status": "passed_with_warnings",
                        "manual_review_count": 1,
                    },
                },
            },
            download_url="/convert/download/ocr-heavy-job",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertTrue(state.download_available)
        self.assertEqual(state.reading_verdict, "ready_with_review")
        self.assertEqual(state.release_verdict, "release_blocked")
        self.assertTrue(state.release_blocked)
        self.assertEqual(
            [item["code"] for item in state.quality_blockers],
            ["semantic_cleanup_failed", "ocr_degradation_failed"],
        )
        self.assertEqual(payload["semantic_cleanup"]["status"], "failed")
        self.assertEqual(payload["ocr_quality"]["status"], "degraded")
        self.assertEqual(payload["reading_order"]["status"], "passed_with_warnings")
        self.assertEqual(payload["issue_groups"]["warnings"][0]["code"], "reading_order_warning")

    def test_minimal_ready_epub_gets_review_until_premium_gates_are_reported(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "clean.pdf",
                "message": "EPUB ready.",
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                },
            },
            download_url="/convert/download/clean-job",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertTrue(state.download_available)
        self.assertEqual(state.reading_verdict, "ready_with_review")
        self.assertEqual(state.release_verdict, "ready_with_review")
        self.assertFalse(state.release_blocked)
        self.assertEqual(state.quality_blockers, ())
        self.assertIn("semantic_cleanup", payload["quality_completeness"]["missing_sections"])
        self.assertEqual(payload["release_verdict"], "ready_with_review")

    def test_quality_completeness_counts_missing_sections_without_blocking_release(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "minimal-quality.pdf",
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                },
            },
            download_url="/convert/download/minimal-quality",
        )

        state = assemble_quality_state(request)
        payload = assemble_quality_state_dict(request)

        self.assertEqual(state.release_verdict, "ready_with_review")
        self.assertFalse(state.release_blocked)
        self.assertEqual(state.quality_completeness.score, 15)
        self.assertEqual(state.quality_completeness.status, "partial")
        self.assertEqual(state.quality_completeness.expected_sections, 13)
        self.assertEqual(state.quality_completeness.reported_sections, 2)
        self.assertEqual(state.quality_completeness.missing_count, 11)
        self.assertEqual(state.quality_completeness.not_reported_count, 11)
        self.assertIn("text_cleanup", state.quality_completeness.missing_sections)
        self.assertIn("semantic_cleanup", state.quality_completeness.missing_sections)
        self.assertIn("ocr_quality", state.quality_completeness.missing_sections)
        self.assertIn("reading_order", state.quality_completeness.missing_sections)
        self.assertIn("table_semantics", state.quality_completeness.missing_sections)
        self.assertIn("reference_cleanup", payload["quality_completeness"]["missing_sections"])
        self.assertEqual(payload["quality_blockers"], [])

    def test_quality_completeness_reports_complete_cockpit_evidence(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "complete-quality.pdf",
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "epubcheck_detail": {
                        "status": "passed",
                        "tool": "epubcheck",
                        "error_count": 0,
                        "warning_count": 0,
                    },
                    "toc_preview": {
                        "status": "reported",
                        "entries": [{"label": "Start", "href": "chapter.xhtml"}],
                    },
                    "metadata_health": {"status": "passed", "message": "Metadata normalized."},
                    "link_health": {"status": "passed", "broken_count": 0},
                    "visible_junk": {"status": "passed", "count": 0},
                    "asset_summary": {"status": "reported", "image_count": 1},
                    "text_cleanup": {"status": "passed"},
                    "reference_cleanup": {"status": "passed"},
                    "semantic_cleanup": {"status": "passed"},
                    "ocr_quality": {"status": "passed"},
                    "reading_order": {"status": "passed"},
                    "content_metrics": {
                        "status": "reported",
                        "source_table_count": 2,
                        "xhtml_table_count": 2,
                        "table_cell_count": 18,
                        "table_page_count": 2,
                        "multi_page_table_count": 1,
                        "wide_table_count": 0,
                        "low_confidence_table_count": 0,
                        "table_summary": {
                            "review_tables": [
                                {
                                    "index": 1,
                                    "page": 2,
                                    "rows": 5,
                                    "columns": 3,
                                    "classification": "multi_page",
                                }
                            ]
                        },
                    },
                },
            },
            download_url="/convert/download/complete-quality",
        )

        payload = assemble_quality_state_dict(request)

        self.assertEqual(payload["quality_completeness"]["score"], 100)
        self.assertEqual(payload["quality_completeness"]["status"], "complete")
        self.assertEqual(payload["quality_completeness"]["reported_sections"], 13)
        self.assertEqual(payload["quality_completeness"]["missing_count"], 0)
        self.assertEqual(payload["quality_completeness"]["not_reported_count"], 0)
        self.assertEqual(payload["quality_completeness"]["missing_sections"], [])
        self.assertEqual(payload["quality_completeness"]["sections"][0]["key"], "validation")
        self.assertTrue(payload["quality_completeness"]["sections"][0]["reported"])
        table_section = next(
            section
            for section in payload["quality_completeness"]["sections"]
            if section["key"] == "table_semantics"
        )
        self.assertEqual(table_section["status"], "passed")
        self.assertIn("2/2", table_section["message"])
        self.assertEqual(payload["content_metrics"]["table_cell_count"], 18)
        self.assertEqual(payload["content_metrics"]["table_page_count"], 2)
        self.assertEqual(payload["content_metrics"]["multi_page_table_count"], 1)
        self.assertEqual(payload["content_metrics"]["wide_table_count"], 0)
        self.assertEqual(payload["content_metrics"]["table_row_count"], 0)
        self.assertEqual(payload["content_metrics"]["table_cell_coverage"], 1.0)
        self.assertEqual(payload["content_metrics"]["fragment_table_count"], 0)
        self.assertEqual(
            payload["content_metrics"]["table_summary"]["review_tables"][0]["classification"],
            "multi_page",
        )
        self.assertEqual(payload["release_verdict"], "release_ready")
        self.assertFalse(payload["send_to_kindle_ready"])
        self.assertEqual(payload["send_to_kindle_blockers"][0]["code"], "kindle_delivery_not_verified")

    def test_send_to_kindle_ready_requires_release_ready_validation_size_and_safe_assets(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "kindle-ready.pdf",
                "output_size_bytes": 45_646,
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "epubcheck_detail": {
                        "status": "passed",
                        "tool": "epubcheck",
                        "error_count": 0,
                        "warning_count": 0,
                    },
                    "toc_preview": {"status": "reported", "entries": [{"label": "Start", "href": "chapter.xhtml"}]},
                    "metadata_health": {"status": "passed"},
                    "link_health": {"status": "passed"},
                    "visible_junk": {"status": "passed", "count": 0},
                    "asset_summary": {"status": "reported", "image_count": 0, "unsupported_media_count": 0, "script_count": 0},
                    "text_cleanup": {"status": "passed"},
                    "reference_cleanup": {"status": "passed", "citations_detected": 16, "citations_covered": 16},
                    "semantic_cleanup": {"status": "passed"},
                    "ocr_quality": {"status": "passed"},
                    "reading_order": {"status": "passed"},
                    "content_metrics": {
                        "status": "reported",
                        "source_table_count": 9,
                        "xhtml_table_count": 9,
                        "table_cell_count": 96,
                        "table_row_count": 32,
                        "table_cell_coverage": 1.0,
                        "fragment_table_count": 0,
                    },
                },
            },
            download_url="/convert/download/kindle-ready",
        )

        payload = assemble_quality_state_dict(request)

        self.assertEqual(payload["release_verdict"], "release_ready")
        self.assertEqual(payload["user_facing_verdict"]["decision"], "ready")
        self.assertEqual(payload["user_facing_verdict"]["label"], "Publikuj")
        self.assertEqual(payload["user_facing_reasons"], [])
        self.assertTrue(payload["send_to_kindle_ready"])
        self.assertEqual(payload["send_to_kindle_blockers"], [])

    def test_send_to_kindle_gate_blocks_unsupported_media_and_oversized_email_asset(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "risky.epub",
                "output_size_bytes": 52 * 1024 * 1024,
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "epubcheck_detail": {"status": "passed"},
                    "toc_preview": {"status": "reported", "entries": [{"label": "Start", "href": "chapter.xhtml"}]},
                    "metadata_health": {"status": "passed"},
                    "link_health": {"status": "passed"},
                    "visible_junk": {"status": "passed", "count": 0},
                    "asset_summary": {"status": "reported", "unsupported_media_count": 1, "script_count": 1},
                    "text_cleanup": {"status": "passed"},
                    "reference_cleanup": {"status": "passed"},
                    "semantic_cleanup": {"status": "passed"},
                    "ocr_quality": {"status": "passed"},
                    "reading_order": {"status": "passed"},
                    "content_metrics": {
                        "status": "reported",
                        "source_table_count": 0,
                        "xhtml_table_count": 0,
                        "table_cell_count": 0,
                        "table_row_count": 0,
                        "table_cell_coverage": 1.0,
                        "fragment_table_count": 0,
                    },
                },
            },
            download_url="/convert/download/risky",
        )

        payload = assemble_quality_state_dict(request)

        self.assertEqual(payload["release_verdict"], "release_ready")
        self.assertFalse(payload["send_to_kindle_ready"])
        self.assertEqual(
            [item["code"] for item in payload["send_to_kindle_blockers"]],
            ["kindle_delivery_email_size_limit", "kindle_delivery_unsupported_assets"],
        )

    def test_send_to_kindle_gate_blocks_reported_media_quality_risks(self) -> None:
        request = self._request_from_job_payload(
            {
                "status": "ready",
                "source_type": "pdf",
                "filename": "media-risk.epub",
                "output_size_bytes": 1024 * 1024,
                "metadata": {
                    "profile": "book_reflow",
                    "validation": "passed",
                    "validation_tool": "epubcheck",
                    "size_budget_status": "failed",
                    "size_budget_message": "Image budget exceeded.",
                    "epubcheck_detail": {"status": "passed"},
                    "toc_preview": {"status": "reported", "entries": [{"label": "Start", "href": "chapter.xhtml"}]},
                    "metadata_health": {"status": "passed"},
                    "link_health": {"status": "passed"},
                    "visible_junk": {"status": "passed", "count": 0},
                    "asset_summary": {
                        "status": "reported",
                        "asset_budget_status": "failed",
                        "unsupported_media_count": 0,
                        "script_count": 0,
                        "image_quality": {
                            "status": "failed",
                            "cover": {
                                "status": "failed",
                                "path": "EPUB/images/cover.jpg",
                                "width": 500,
                                "height": 500,
                                "issues": ["cover_aspect_ratio", "cover_resolution"],
                            },
                            "low_resolution_count": 2,
                            "progressive_jpeg_count": 1,
                            "media_risk_count": 1,
                        },
                    },
                    "text_cleanup": {"status": "passed"},
                    "reference_cleanup": {"status": "passed"},
                    "semantic_cleanup": {"status": "passed"},
                    "ocr_quality": {"status": "passed"},
                    "reading_order": {"status": "passed"},
                    "content_metrics": {
                        "status": "reported",
                        "source_table_count": 0,
                        "xhtml_table_count": 0,
                        "table_cell_count": 0,
                        "table_row_count": 0,
                        "table_cell_coverage": 1.0,
                        "fragment_table_count": 0,
                    },
                },
            },
            download_url="/convert/download/media-risk",
        )

        payload = assemble_quality_state_dict(request)

        self.assertFalse(payload["send_to_kindle_ready"])
        self.assertEqual(
            [item["code"] for item in payload["send_to_kindle_blockers"]],
            [
                "kindle_delivery_release_not_ready",
                "kindle_delivery_size_budget_failed",
                "kindle_delivery_cover_image_quality",
                "kindle_delivery_progressive_jpeg",
                "kindle_delivery_low_resolution_images",
                "kindle_delivery_unsupported_assets",
            ],
        )

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
        self.assertFalse(state.download_available)
        self.assertEqual(state.download_state.status, "unavailable")
        self.assertEqual(state.overall_severity, "error")
        self.assertEqual(state.reading_verdict, "failed")
        self.assertEqual(state.release_verdict, "failed")
        self.assertTrue(state.release_blocked)
        self.assertEqual(state.quality_blockers[0]["code"], "conversion_failed")
        self.assertEqual(state.verdict.status, "failed")
        self.assertTrue(state.verdict.blocks_download)
        self.assertEqual(state.validation.status, "unavailable")
        self.assertEqual(len(state.alerts), 1)
        self.assertEqual(state.alerts[0].code, "conversion_failed")
        self.assertIn("timeout", state.alerts[0].message)

    def test_timed_out_state_surfaces_timeout_blocker(self) -> None:
        request = ConversionQualityStateRequest(
            job_status="timed_out",
            source_type="pdf",
            filename="slow.pdf",
            message="Konwersja przekroczyla limit czasu.",
            error="conversion watchdog expired",
        )

        state = assemble_quality_state(request)

        self.assertEqual(state.phase, "failed")
        self.assertTrue(state.is_terminal)
        self.assertFalse(state.download_available)
        self.assertEqual(state.download_state.status, "unavailable")
        self.assertEqual(state.reading_verdict, "failed")
        self.assertEqual(state.release_verdict, "failed")
        self.assertTrue(state.release_blocked)
        self.assertEqual(state.quality_blockers[0]["code"], "conversion_timeout")
        self.assertEqual(state.alerts[0].code, "conversion_timeout")

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
        self.assertEqual(state.download_state.status, "pending")
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
        request = self._request_from_job_payload(
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
        request = self._request_from_job_payload(
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
        request = self._request_from_job_payload(
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
