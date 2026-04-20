import unittest
from unittest.mock import patch

from converter import ConversionConfig, finalize_epub_bytes


class ConverterTextCleanupTests(unittest.TestCase):
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
                with patch("epub_reference_repair.repair_epub_reference_sections", return_value=repair_stub):
                    epub_bytes, text_cleanup = finalize_epub_bytes(
                        b"input-epub",
                        ConversionConfig(language="pl"),
                        {"title": "Test", "author": "Tester"},
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


if __name__ == "__main__":
    unittest.main()
