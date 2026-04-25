from __future__ import annotations

import unittest

from quality_reporting import (
    build_raw_workflow_quality_signals,
    build_workflow_quality_report,
    build_manual_review_queue_payload,
    collect_workflow_symptoms,
    derive_workflow_quality_verdict,
    derive_workflow_snapshot_status,
)


class QualityReportingTests(unittest.TestCase):
    def test_build_manual_review_queue_payload_dedupes_and_summarizes_items(self) -> None:
        payload = build_manual_review_queue_payload(
            [
                {
                    "kind": "heading",
                    "file": "chapter_001.xhtml",
                    "subject": "Intro",
                    "reason": "short-ambiguous-heading",
                    "confidence": 0.58,
                },
                {
                    "kind": "heading",
                    "file": "chapter_001.xhtml",
                    "subject": "Intro",
                    "reason": "short-ambiguous-heading",
                    "confidence": 0.58,
                },
                {
                    "kind": "metadata",
                    "file": "content.opf",
                    "subject": "title",
                    "reason": "metadata-conflict",
                    "confidence": 0.66,
                },
                "legacy queue item",
                "legacy queue item",
            ]
        )

        self.assertEqual(payload["summary"]["queue_size"], 3)
        self.assertEqual(payload["summary"]["distinct_file_count"], 2)
        self.assertEqual(
            payload["summary"]["kind_counts"],
            {
                "heading": 1,
                "metadata": 1,
                "legacy-review-item": 1,
            },
        )
        self.assertEqual(len(payload["items"]), 3)
        self.assertEqual(payload["items"][0]["kind"], "heading")
        self.assertEqual(payload["items"][1]["kind"], "metadata")
        self.assertEqual(payload["items"][2], "legacy queue item")

    def test_build_manual_review_queue_payload_handles_empty_input(self) -> None:
        payload = build_manual_review_queue_payload()

        self.assertEqual(
            payload,
            {
                "summary": {
                    "queue_size": 0,
                    "distinct_file_count": 0,
                    "kind_counts": {},
                },
                "items": [],
            },
        )

    def test_derive_workflow_snapshot_status_ignores_superseded_embedded_validation_warning(self) -> None:
        validation = {
            "summary": {"status": "passed", "error_count": 0, "warning_count": 0},
            "epubcheck": {"status": "passed"},
        }
        audit = {"decision": "pass", "gates": {}}
        quality_report = {
            "validation_status": "unavailable",
            "warnings": ["Formalna walidacja EPUBCheck nie mogla zostac wykonana."],
        }

        status = derive_workflow_snapshot_status(
            validation=validation,
            audit=audit,
            quality_report=quality_report,
        )

        self.assertEqual(status, "passed")

    def test_collect_workflow_symptoms_ignores_superseded_embedded_validation_warning(self) -> None:
        validation = {
            "summary": {"status": "passed", "error_count": 0, "warning_count": 0},
            "epubcheck": {"status": "passed"},
            "internal_links": {"errors": []},
            "external_links": {"errors": []},
        }
        audit = {"decision": "pass", "gates": {}}
        quality_report = {
            "validation_status": "unavailable",
            "warnings": ["Formalna walidacja EPUBCheck nie mogla zostac wykonana."],
            "text_cleanup": {},
        }

        symptoms = collect_workflow_symptoms(
            validation=validation,
            audit=audit,
            quality_report=quality_report,
        )

        self.assertEqual(symptoms, [])

    def test_workflow_quality_report_separates_raw_signals_from_verdict(self) -> None:
        validation = {
            "summary": {"status": "passed", "error_count": 0, "warning_count": 1},
            "epubcheck": {"status": "passed"},
            "internal_links": {"errors": []},
            "external_links": {"errors": []},
            "package": {"errors": []},
        }
        audit = {
            "decision": "pass_with_review",
            "gates": {
                "C": {"status": "pass"},
                "D": {"status": "pass_with_review"},
            },
        }
        quality_report = {
            "validation_status": "passed",
            "warnings": ["Manual TOC review suggested."],
            "text_cleanup": {
                "review_needed_count": 2,
                "blocked_count": 0,
                "reference_cleanup": {
                    "quality_gate_status": "passed",
                    "visible_junk_detected": 0,
                },
            },
        }

        raw = build_raw_workflow_quality_signals(
            validation=validation,
            audit=audit,
            quality_report=quality_report,
        )
        verdict = derive_workflow_quality_verdict(
            validation=validation,
            audit=audit,
            quality_report=quality_report,
            raw_signals=raw,
        )
        payload = build_workflow_quality_report(
            validation=validation,
            audit=audit,
            quality_report=quality_report,
            symptoms=["gate D: review"],
        )

        self.assertEqual(raw.warning_count, 1)
        self.assertEqual(raw.toc_gate_status, "passed_with_warnings")
        self.assertEqual(raw.text_cleanup_review_needed_count, 2)
        self.assertEqual(verdict.status, "passed_with_warnings")
        self.assertEqual(verdict.severity, "warning")
        self.assertIn("toc_gate_review", verdict.reasons)
        self.assertEqual(payload["raw_signals"]["warning_count"], 1)
        self.assertEqual(payload["verdict"]["status"], "passed_with_warnings")
        self.assertEqual(payload["symptoms"], ["gate D: review"])

    def test_workflow_quality_verdict_marks_structural_blockers_failed(self) -> None:
        validation = {
            "summary": {"status": "failed", "error_count": 1, "warning_count": 0},
            "epubcheck": {"status": "failed"},
            "internal_links": {"errors": ["duplicate id chapter_1", "fragment target missing"]},
            "external_links": {"errors": []},
            "package": {"errors": []},
        }
        audit = {"decision": "fail", "gates": {"C": {"status": "fail"}, "D": {"status": "pass"}}}

        verdict = derive_workflow_quality_verdict(
            validation=validation,
            audit=audit,
            quality_report=None,
        )

        self.assertEqual(verdict.status, "failed")
        self.assertEqual(verdict.severity, "error")
        self.assertIn("validation_failed", verdict.reasons)
        self.assertIn("duplicate_dom_ids", verdict.reasons)
        self.assertIn("heading_gate_failed", verdict.reasons)
        self.assertGreaterEqual(verdict.blocker_count, 3)


if __name__ == "__main__":
    unittest.main()
