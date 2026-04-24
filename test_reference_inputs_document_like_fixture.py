from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

import premium_corpus_smoke as corpus_smoke
import scripts.prepare_reference_inputs as prepare_reference_inputs_module
from size_budget_policy import get_document_size_budget, load_size_budget_policy


class DocumentLikeReferenceInputTests(unittest.TestCase):
    def test_prepare_reference_inputs_generates_a_multi_page_pdf_fixture(self) -> None:
        document_like_case = next(
            case for case in prepare_reference_inputs_module.REFERENCE_CASES if case["id"] == "document_like_report_pdf"
        )

        with patch.object(prepare_reference_inputs_module, "REFERENCE_CASES", [document_like_case]):
            with tempfile.TemporaryDirectory() as temp_dir:
                manifest = prepare_reference_inputs_module.prepare_reference_inputs(root_dir=temp_dir)
                pdf_path = Path(temp_dir) / "reference_inputs/pdf/document_like_report.pdf"
                manifest_path = Path(temp_dir) / "reference_inputs/manifest.json"

                self.assertTrue(pdf_path.exists())
                self.assertTrue(manifest_path.exists())
                self.assertEqual(manifest["cases"][0]["id"], "document_like_report_pdf")
                self.assertEqual(manifest["cases"][0]["document_class"], "document-like-report")
                self.assertFalse(manifest["cases"][0]["quick_smoke"])
                self.assertGreater(manifest["cases"][0]["size_bytes"], 0)

                with fitz.open(pdf_path) as document:
                    self.assertEqual(document.page_count, 4)
                    self.assertEqual(document.metadata.get("title"), "Document-Like Report Fixture")
                    self.assertEqual(document.metadata.get("author"), "Anna Nowak")
                    toc = document.get_toc()
                    self.assertEqual(len(toc), 4)
                    self.assertEqual(toc[0][1], "Executive summary")

                saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_manifest["cases"][0]["id"], "document_like_report_pdf")
                self.assertEqual(saved_manifest["cases"][0]["target_path"], "reference_inputs/pdf/document_like_report.pdf")

    def test_document_like_report_is_in_corpus_selection_and_size_policy(self) -> None:
        document_like_case = corpus_smoke.CorpusCase(
            Path("reference_inputs/pdf/document_like_report.pdf"),
            document_class="document-like-report",
            notes="Generated multi-page report-style PDF from the reference-input bootstrap.",
        )

        with patch.object(corpus_smoke, "ANALYSIS_ONLY", []), patch.object(corpus_smoke, "CORPUS", [document_like_case]):
            selected_cases, selection = corpus_smoke._select_corpus_batch(
                case_filters=["document-like-report"],
                manifest_path=None,
            )
            self.assertEqual(len(selected_cases), 1)
            self.assertEqual(selection.selected_case_labels, ("document_like_report.pdf (document-like-report)",))

            policy = load_size_budget_policy()
            budget = get_document_size_budget("document-like-report", policy=policy)
            self.assertIsNotNone(budget)
            assert budget is not None
            self.assertEqual(budget["baseline_cases"], ["document_like_report_pdf"])

            rows = [
                {
                    "file": "document_like_report.pdf",
                    "document_class": "document-like-report",
                    "mode": "convert-and-audit",
                    "notes": document_like_case.notes,
                    "grade": "pass",
                    "analysis": {"profile": "book_reflow"},
                    "quality": {
                        "validation_status": "passed",
                        "text_cleanup": {
                            "auto_fix_count": 0,
                            "review_needed_count": 0,
                            "blocked_count": 0,
                            "reference_cleanup": {},
                        },
                    },
                    "epub_stats": {
                    "nav_entries": 4,
                    "nav_depth": 1,
                    "package_title": "Document-Like Report Fixture",
                    "package_creator": "Anna Nowak",
                    "package_language": "en",
                },
                    "heading_repair": {
                        "release_status": "skipped",
                        "toc_entries_before": 4,
                        "toc_entries_after": 4,
                        "headings_removed": 0,
                        "manual_review_count": 0,
                    },
                    "size_gate": {
                        "status": "passed",
                        "epub_size_bytes": 100000,
                        "warn_bytes": 131072,
                        "hard_bytes": 262144,
                    },
                }
            ]
            overall = corpus_smoke._build_overall_summary(rows)
            markdown = corpus_smoke._build_markdown_report(rows, overall)

        self.assertEqual(overall["class_grade"]["document-like-report"], "pass")
        self.assertIn("document-like-report", markdown)
        self.assertIn("document_like_report.pdf", markdown)


if __name__ == "__main__":
    unittest.main()
