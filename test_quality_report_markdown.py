from __future__ import annotations

import unittest
from unittest.mock import patch

from quality_report_markdown import build_manual_review_markdown, build_workflow_quality_report_markdown


class QualityReportMarkdownTests(unittest.TestCase):
    def test_build_manual_review_markdown_delegates_payload_assembly_for_raw_items(self) -> None:
        payload = {
            "summary": {
                "queue_size": 1,
                "distinct_file_count": 1,
                "kind_counts": {"heading": 1},
            },
            "items": [
                {
                    "kind": "heading",
                    "file": "chapter_001.xhtml",
                    "subject": "Intro",
                    "reason": "short-ambiguous-heading",
                    "confidence": 0.58,
                }
            ],
        }

        with patch("quality_report_markdown.build_manual_review_queue_payload", return_value=payload) as helper:
            markdown = build_manual_review_markdown(
                [
                    {
                        "kind": "heading",
                        "file": "chapter_001.xhtml",
                        "subject": "Intro",
                        "reason": "short-ambiguous-heading",
                        "confidence": 0.58,
                    }
                ]
            )

        helper.assert_called_once()
        self.assertIn("# Manual Review Queue", markdown)
        self.assertIn("[heading] chapter_001.xhtml: Intro", markdown)

    def test_build_manual_review_markdown_accepts_prebuilt_payload_without_reassembly(self) -> None:
        payload = {
            "summary": {
                "queue_size": 2,
                "distinct_file_count": 1,
                "kind_counts": {"heading": 1, "legacy-review-item": 1},
            },
            "items": [
                {
                    "kind": "heading",
                    "file": "chapter_001.xhtml",
                    "subject": "Intro",
                    "reason": "short-ambiguous-heading",
                    "confidence": 0.58,
                },
                "legacy queue item",
            ],
        }

        with patch("quality_report_markdown.build_manual_review_queue_payload") as helper:
            markdown = build_manual_review_markdown(payload)

        helper.assert_not_called()
        self.assertIn("[heading] chapter_001.xhtml: Intro", markdown)
        self.assertIn("- legacy queue item", markdown)

    def test_build_manual_review_markdown_keeps_empty_queue_output_stable(self) -> None:
        markdown = build_manual_review_markdown({"summary": {"queue_size": 0}, "items": []})

        self.assertEqual(markdown, "# Manual Review Queue\n\n- None\n")

    def test_build_workflow_quality_report_markdown_renders_verdict_and_raw_signals(self) -> None:
        markdown = build_workflow_quality_report_markdown(
            {
                "raw_signals": {
                    "validation_status": "passed",
                    "warning_count": 1,
                },
                "verdict": {
                    "status": "passed_with_warnings",
                    "severity": "warning",
                    "blocker_count": 0,
                    "warning_count": 1,
                    "review_count": 1,
                    "reasons": ["toc_gate_review"],
                },
                "symptoms": ["gate D: review"],
            }
        )

        self.assertIn("# Workflow Quality Report", markdown)
        self.assertIn("Verdict: `passed_with_warnings`", markdown)
        self.assertIn("`warning_count`: `1`", markdown)
        self.assertIn("`toc_gate_review`", markdown)
        self.assertIn("gate D: review", markdown)


if __name__ == "__main__":
    unittest.main()
