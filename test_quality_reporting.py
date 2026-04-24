from __future__ import annotations

import unittest

from quality_reporting import (
    build_manual_review_queue_payload,
    collect_workflow_symptoms,
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


if __name__ == "__main__":
    unittest.main()
