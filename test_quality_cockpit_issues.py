from __future__ import annotations

import json
import unittest

from quality_cockpit_issues import build_quality_cockpit_issue_groups


class QualityCockpitIssueTests(unittest.TestCase):
    def test_failed_epubcheck_creates_blocker_with_required_fields(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            validation={"status": "failed", "message": "Structural validation failed."},
            epubcheck_detail={
                "status": "failed",
                "messages": [
                    {
                        "message": "Missing nav document.",
                        "file": "EPUB/package.opf",
                    }
                ],
            },
        )

        self.assertEqual([issue["code"] for issue in groups["blockers"]], ["validation_failed", "epubcheck_failed"])
        epubcheck_issue = groups["blockers"][1]
        self.assertEqual(epubcheck_issue["severity"], "blocker")
        self.assertEqual(epubcheck_issue["source"], "epubcheck")
        self.assertEqual(epubcheck_issue["message"], "Missing nav document.")
        self.assertEqual(epubcheck_issue["file"], "EPUB/package.opf")
        self.assertIn("suggested_action", epubcheck_issue)
        json.dumps(groups)

    def test_duplicate_prevention_uses_severity_code_source_message(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            epubcheck_detail={
                "status": "failed",
                "messages": [
                    {"message": "Broken href target.", "file": "chapter1.xhtml"},
                    {"message": "Broken href target.", "file": "chapter2.xhtml"},
                ],
            },
            link_health={"status": "failed", "message": "Broken href target."},
        )

        epubcheck_issues = [issue for issue in groups["blockers"] if issue["code"] == "epubcheck_failed"]
        self.assertEqual(len(epubcheck_issues), 1)
        self.assertEqual(epubcheck_issues[0]["file"], "chapter1.xhtml")
        self.assertEqual([issue["code"] for issue in groups["blockers"]], ["epubcheck_failed", "link_health_failed"])

    def test_warning_and_review_grouping_for_cockpit_sources(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            size_budget={"status": "passed_with_warnings", "message": "Output is near the hard budget."},
            heading_repair={"release": "pass_with_review", "review": 2},
            audit={
                "warnings": ["Low confidence reading order."],
                "high_risk_page_list": [{"page": 12, "title": "Complex table"}],
                "high_risk_sections": [{"title": "Appendix A"}],
            },
            text_cleanup={"review_needed_count": 3},
            reference_cleanup={
                "unresolved_fragment_count": 1,
                "review_records": [{"title": "Bibliography item"}],
            },
            metadata_health={"placeholders": ["Unknown Author"]},
            link_health={"status": "passed_with_warnings", "message": "External URL timeout."},
            visible_junk={"status": "passed_with_warnings", "message": "Suspicious cleanup marker."},
        )

        self.assertEqual(
            [issue["code"] for issue in groups["warnings"]],
            [
                "size_budget_warning",
                "audit_warning",
                "link_health_warning",
                "metadata_placeholder",
                "visible_junk_warning",
            ],
        )
        self.assertEqual(
            [issue["code"] for issue in groups["review"]],
            [
                "heading_repair_review",
                "manual_review_page",
                "manual_review_section",
                "text_cleanup_review",
                "reference_unresolved_fragments",
                "reference_review_record",
            ],
        )
        self.assertEqual(groups["review"][1]["page"], 12)
        self.assertEqual(groups["review"][2]["section"], "Appendix A")
        json.dumps(groups)

    def test_missing_and_neutral_data_returns_empty_groups(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            validation={"status": "passed"},
            epubcheck_detail={"status": "passed"},
            size_budget={"status": "passed"},
            heading_repair={"status": "applied", "release": "passed", "review": 0},
            text_cleanup={"blocked_count": 0, "review_needed_count": 0},
            reference_cleanup={"status": "passed", "visible_junk_detected": 0, "unresolved_fragment_count": 0},
            semantic_cleanup={"status": "passed", "manual_review_count": 0},
            ocr_quality={"status": "passed", "low_confidence_page_count": 0},
            reading_order={"status": "passed", "manual_review_count": 0},
            link_health={"status": "passed"},
            metadata_health={"placeholders": [], "placeholder_count": 0},
            visible_junk={"status": "passed", "count": 0},
        )

        self.assertEqual(groups, {"blockers": [], "warnings": [], "review": []})
        self.assertEqual(build_quality_cockpit_issue_groups(), {"blockers": [], "warnings": [], "review": []})

    def test_semantic_ocr_and_reading_order_gates_promote_release_issues(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            semantic_cleanup={
                "status": "failed",
                "message": "Paragraph structure is not publishable.",
                "manual_review_count": 2,
            },
            ocr_quality={
                "status": "degraded",
                "degraded_count": 3,
            },
            reading_order={
                "status": "passed_with_warnings",
                "manual_review_count": 1,
            },
        )

        self.assertEqual(
            [issue["code"] for issue in groups["blockers"]],
            ["semantic_cleanup_failed", "ocr_degradation_failed"],
        )
        self.assertEqual([issue["source"] for issue in groups["blockers"]], ["semantic_cleanup", "ocr_quality"])
        self.assertEqual([issue["code"] for issue in groups["warnings"]], ["reading_order_warning"])
        self.assertEqual(
            [issue["code"] for issue in groups["review"]],
            ["semantic_cleanup_review", "reading_order_review"],
        )
        json.dumps(groups)

    def test_failed_reference_and_metadata_gates_are_release_blockers(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            reference_cleanup={
                "quality_gate_status": "failed",
                "message": "Bibliography cleanup could not preserve records.",
            },
            metadata_health={
                "status": "failed",
                "message": "Missing required language metadata.",
            },
        )

        self.assertEqual(
            [issue["code"] for issue in groups["blockers"]],
            ["reference_cleanup_failed", "metadata_health_failed"],
        )
        self.assertEqual([issue["source"] for issue in groups["blockers"]], ["reference_cleanup", "metadata_health"])

    def test_document_report_content_metrics_create_release_blockers(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            content_metrics={
                "source_toc_entries": 32,
                "source_table_count": 18,
                "xhtml_table_count": 0,
                "fragment_table_count": 1,
                "tiny_tail_sections": [{"section": "15. Referencje publiczne", "page_start": 19}],
            },
            toc_preview={"entry_count": 0},
            asset_summary={"asset_budget_status": "failed"},
            reference_cleanup={
                "citations_detected": 16,
                "citations_covered": 6,
                "citations_missing_record": 10,
            },
        )

        self.assertEqual(
            [issue["code"] for issue in groups["blockers"]],
            [
                "reference_coverage_failed",
                "source_toc_lost",
                "table_semantics_lost",
                "table_fragment_detected",
                "tiny_tail_section",
                "asset_budget_failed",
            ],
        )
        self.assertEqual(groups["blockers"][4]["section"], "15. Referencje publiczne")
        self.assertEqual(groups["blockers"][4]["page"], 19)
        json.dumps(groups)

    def test_partial_table_metrics_create_warning_and_review_issues(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            content_metrics={
                "source_table_count": 3,
                "xhtml_table_count": 2,
                "wide_table_count": 1,
                "low_confidence_table_count": 1,
            }
        )

        self.assertEqual(groups["blockers"], [])
        self.assertIn("table_semantics_partial", [issue["code"] for issue in groups["warnings"]])
        self.assertEqual(
            [issue["code"] for issue in groups["review"]],
            ["wide_table_review"],
        )

    def test_suppressed_table_candidates_account_for_source_tables(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            content_metrics={
                "source_table_count": 4,
                "xhtml_table_count": 1,
                "transformed_table_count": 1,
                "fragment_table_count": 2,
                "suppressed_table_fragment_count": 2,
                "rendered_low_confidence_table_count": 0,
                "rendered_fragment_table_count": 0,
            }
        )

        self.assertEqual(groups["blockers"], [])
        self.assertEqual(groups["warnings"], [])

    def test_rendered_false_positive_and_transformed_table_loss_are_blockers(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            content_metrics={
                "source_table_count": 4,
                "xhtml_table_count": 4,
                "rendered_low_confidence_table_count": 1,
                "rendered_fragment_table_count": 1,
                "transformed_table_content_loss_count": 1,
            }
        )

        self.assertEqual(
            [issue["code"] for issue in groups["blockers"]],
            ["table_false_positive_rendered", "transformed_table_content_lost"],
        )

    def test_empty_reference_section_without_citations_is_review_issue(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            reference_cleanup={
                "reference_quality_gate_status": "passed_with_warnings",
                "citations_detected": 0,
                "empty_reference_sections_unresolved": 1,
            }
        )

        self.assertEqual(groups["blockers"], [])
        self.assertIn("reference_empty_section_review", [issue["code"] for issue in groups["review"]])

    def test_empty_reference_section_with_citations_blocks_release(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            reference_cleanup={
                "reference_quality_gate_status": "passed_with_warnings",
                "citations_detected": 16,
                "empty_reference_sections_unresolved": 1,
            }
        )

        self.assertIn("empty_reference_section", [issue["code"] for issue in groups["blockers"]])
        self.assertEqual(groups["blockers"][0]["source"], "reference_cleanup")

    def test_toc_noise_entries_are_review_issues(self) -> None:
        groups = build_quality_cockpit_issue_groups(
            content_metrics={"toc_noise_entry_count": 2},
            toc_preview={"noise_entry_count": 1},
        )

        self.assertEqual(groups["blockers"], [])
        self.assertIn("toc_noise_entry", [issue["code"] for issue in groups["review"]])


if __name__ == "__main__":
    unittest.main()
