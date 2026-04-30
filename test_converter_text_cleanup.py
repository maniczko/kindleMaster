import unittest
from unittest.mock import patch

from converter import ConversionConfig, finalize_epub_bytes


class ConverterTextCleanupTests(unittest.TestCase):
    def test_finalize_epub_bytes_skips_text_cleanup_for_diagram_book_reflow(self):
        with patch("text_normalization.clean_epub_text_package") as cleanup_mock:
            with patch(
                "kindle_semantic_cleanup.finalize_epub_for_kindle",
                return_value=(b"semantic-epub", {"entries_rebuilt": 0}),
            ):
                repair_stub = type(
                    "ReferenceRepairStub",
                    (),
                    {
                        "epub_bytes": b"repaired-epub",
                        "summary": {
                            "entries_rebuilt": 0,
                            "records_detected": 0,
                            "records_reconstructed": 0,
                            "records_flagged_for_review": 0,
                            "unresolved_fragment_count": 0,
                            "citations_detected": 0,
                            "citations_covered": 0,
                            "citations_missing_record": 0,
                            "citations_ambiguous": 0,
                            "unused_reference_records": [],
                            "reference_quality_gate_status": "passed",
                            "quality_gate_status": "passed",
                        },
                    },
                )()
                with patch("epub_reference_repair.repair_epub_reference_sections", return_value=repair_stub):
                    epub_bytes, text_cleanup = finalize_epub_bytes(
                        b"input-epub",
                        ConversionConfig(language="en"),
                        {"title": "Woodpecker", "author": "Unknown", "source_pdf_path": "example/woodpecker.pdf"},
                        "woodpecker.pdf",
                        publication_profile="diagram_book_reflow",
                        return_details=True,
                    )

        self.assertEqual(epub_bytes, b"repaired-epub")
        self.assertEqual(text_cleanup["status"], "skipped")
        self.assertTrue(text_cleanup["package_blocked"])
        self.assertTrue(text_cleanup["profile_skip"])
        self.assertIn("diagram-heavy training books", text_cleanup["skip_reason"])
        cleanup_mock.assert_not_called()

    def test_finalize_epub_bytes_can_return_text_cleanup_summary(self):
        cleanup_stub = type(
            "CleanupStub",
            (),
            {
                "epub_bytes": b"cleaned-epub",
                "summary": {
                    "auto_fix_count": 3,
                    "review_needed_count": 1,
                    "blocked_count": 0,
                    "unknown_term_count": 2,
                    "publish_blocked": False,
                    "epubcheck_status": "passed",
                },
                "epubcheck": {"status": "passed", "tool": "epubcheck", "messages": []},
                "unknown_terms": [{"term": "issuera", "count": 1}],
                "markdown_report": "# report",
                "chapter_diffs": {"EPUB/chapter_001.xhtml": "--- before"},
            },
        )()

        with patch("text_normalization.clean_epub_text_package", return_value=cleanup_stub):
            with patch(
                "kindle_semantic_cleanup.finalize_epub_for_kindle",
                return_value=(
                    b"final-epub",
                    {
                        "sections_detected": 1,
                        "entries_rebuilt": 4,
                        "split_record_count": 1,
                        "clickable_link_count": 3,
                        "repaired_link_count": 2,
                        "review_entry_count": 1,
                        "unresolved_fragment_count": 1,
                        "quality_gate_status": "passed",
                    },
                ),
            ):
                repair_stub = type(
                    "ReferenceRepairStub",
                    (),
                    {
                        "epub_bytes": b"repaired-epub",
                        "summary": {
                            "entries_rebuilt": 5,
                            "records_detected": 6,
                            "records_reconstructed": 5,
                            "records_flagged_for_review": 1,
                            "unresolved_fragment_count": 1,
                            "citations_detected": 4,
                            "citations_covered": 3,
                            "citations_missing_record": 1,
                            "citations_ambiguous": 0,
                            "unused_reference_records": ["[R9]"],
                            "reference_quality_gate_status": "failed",
                            "quality_gate_status": "passed",
                        },
                    },
                )()
                with patch("epub_reference_repair.repair_epub_reference_sections", return_value=repair_stub) as repair_mock:
                    epub_bytes, text_cleanup = finalize_epub_bytes(
                        b"input-epub",
                        ConversionConfig(language="pl"),
                        {"title": "Test", "author": "Tester", "source_pdf_path": "example/report.pdf"},
                        "report.pdf",
                        publication_profile="book_reflow",
                        return_details=True,
                    )

        self.assertEqual(epub_bytes, b"repaired-epub")
        self.assertEqual(text_cleanup["auto_fix_count"], 3)
        self.assertEqual(text_cleanup["epubcheck"]["status"], "passed")
        self.assertEqual(text_cleanup["chapter_diff_count"], 1)
        self.assertTrue(text_cleanup["report_available"])
        self.assertEqual(text_cleanup["reference_cleanup"]["entries_rebuilt"], 5)
        self.assertEqual(text_cleanup["reference_cleanup"]["unresolved_fragment_count"], 1)
        self.assertEqual(text_cleanup["reference_cleanup"]["citations_missing_record"], 1)
        self.assertEqual(text_cleanup["reference_cleanup"]["reference_quality_gate_status"], "failed")
        self.assertEqual(text_cleanup["reference_cleanup"]["semantic_prepass"]["entries_rebuilt"], 4)
        self.assertEqual(repair_mock.call_args.kwargs["source_pdf_path"], "example/report.pdf")

    def test_finalize_epub_bytes_exposes_compact_semantic_cleanup_gates(self):
        rich_semantic_report = {
            "status": "pass_with_review",
            "summary": {
                "cleanup_scope": "semantic-reflow",
                "chapter_count": 3,
                "toc_entry_count_before": 2,
                "toc_entry_count_after": 4,
                "manual_review_count": 1,
            },
            "reference_cleanup": {
                "entries_rebuilt": 2,
                "quality_gate_status": "passed",
            },
            "gates": {
                "A": {"status": "pass"},
                "B": {"status": "pass"},
                "C": {"status": "pass_with_review"},
                "D": {"status": "pass"},
                "E": {"status": "pass"},
                "F": {"status": "pass_with_review", "warnings": ["Manual heading review remains."]},
            },
            "phases": {
                "metadata_repair": {"status": "completed"},
                "toc_rebuild": {"status": "completed", "entry_count_before": 2, "entry_count_after": 4},
                "structural_integrity": {"status": "passed", "manual_review": []},
            },
            "manual_review_queue": [{"code": "heading-review", "message": "Check heading."}],
        }
        repair_stub = type(
            "ReferenceRepairStub",
            (),
            {
                "epub_bytes": b"repaired-epub",
                "summary": {
                    "entries_rebuilt": 2,
                    "records_detected": 2,
                    "records_reconstructed": 2,
                    "records_flagged_for_review": 0,
                    "unresolved_fragment_count": 0,
                    "citations_detected": 2,
                    "citations_covered": 2,
                    "citations_missing_record": 0,
                    "citations_ambiguous": 0,
                    "reference_quality_gate_status": "passed",
                    "quality_gate_status": "passed",
                },
            },
        )()

        with patch("text_normalization.clean_epub_text_package", side_effect=ImportError("skip")):
            with patch(
                "kindle_semantic_cleanup.finalize_epub_for_kindle",
                return_value=(b"semantic-epub", rich_semantic_report),
            ) as semantic_mock:
                with patch("epub_reference_repair.repair_epub_reference_sections", return_value=repair_stub):
                    _epub_bytes, text_cleanup = finalize_epub_bytes(
                        b"input-epub",
                        ConversionConfig(language="pl"),
                        {"title": "Test", "author": "Tester"},
                        "report.pdf",
                        publication_profile="book_reflow",
                        return_details=True,
                    )

        self.assertEqual(semantic_mock.call_args.kwargs["report_mode"], "rich")
        self.assertEqual(text_cleanup["semantic_cleanup"]["status"], "pass_with_review")
        self.assertEqual(text_cleanup["semantic_cleanup"]["manual_review_count"], 1)
        self.assertEqual(text_cleanup["semantic_cleanup"]["gate_statuses"]["F"], "pass_with_review")
        self.assertEqual(text_cleanup["reading_order"]["status"], "passed")
        self.assertEqual(text_cleanup["reference_cleanup"]["semantic_prepass"]["entries_rebuilt"], 2)


if __name__ == "__main__":
    unittest.main()
