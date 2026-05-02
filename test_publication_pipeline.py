from __future__ import annotations

import unittest

from publication_model import PublicationAnalysis
from publication_pipeline import (
    _looks_like_cover_masthead_line,
    _ocr_quality_from_result,
    _should_coalesce_page_chapters_with_pdf_outline,
    publication_from_content,
)


def _analysis(**overrides) -> PublicationAnalysis:
    defaults = {
        "profile": "book_reflow",
        "confidence": 0.88,
        "page_count": 2,
        "render_budget_class": "reflowable",
        "has_toc": True,
        "has_tables": False,
        "has_diagrams": False,
        "has_meaningful_images": False,
        "estimated_sections": 2,
        "fallback_recommendation": "",
        "ui_profile": "technical-study",
        "legacy_strategy": "text_reflowable",
        "has_text_layer": True,
        "is_scanned": False,
        "layout_heavy": False,
        "text_heavy": True,
    }
    defaults.update(overrides)
    return PublicationAnalysis(**defaults)


class PublicationPipelineTests(unittest.TestCase):
    def test_cover_masthead_line_is_not_used_as_publication_title(self) -> None:
        self.assertTrue(
            _looks_like_cover_masthead_line(
                "KWARTALNIK PROJECT MANAGEMENT INSTITUTE POLAND CHAPTER | WWW.STREFAPMI.PL | MARZEC 2026"
            )
        )
        self.assertFalse(_looks_like_cover_masthead_line("AI jako partner, nie narzędzie"))

    def test_book_reflow_with_dense_page_chapters_uses_pdf_outline_grouping(self) -> None:
        analysis = _analysis(profile="book_reflow", has_toc=True, estimated_sections=6)
        content = {
            "toc": [(1, "Chapter 1", 1), (1, "Chapter 2", 8), (1, "Chapter 3", 15)],
            "chapters": [
                {"title": f"Page {index}", "page_num": index, "html_parts": [f"<p>Page {index}</p>"]}
                for index in range(24)
            ],
        }

        self.assertTrue(_should_coalesce_page_chapters_with_pdf_outline(content, analysis))

    def test_book_reflow_without_page_sliced_oversegmentation_keeps_existing_chapters(self) -> None:
        analysis = _analysis(profile="book_reflow", has_toc=True, estimated_sections=6)
        content = {
            "toc": [(1, "Chapter 1", 1), (1, "Chapter 2", 8), (1, "Chapter 3", 15)],
            "chapters": [
                {"title": f"Chapter {index}", "html_parts": [f"<p>Chapter {index}</p>"]}
                for index in range(3)
            ],
        }

        self.assertFalse(_should_coalesce_page_chapters_with_pdf_outline(content, analysis))

    def test_table_summary_is_carried_into_quality_report(self) -> None:
        analysis = PublicationAnalysis(
            profile="book_reflow",
            confidence=0.88,
            page_count=2,
            render_budget_class="reflowable",
            has_toc=True,
            has_tables=True,
            has_diagrams=False,
            has_meaningful_images=False,
            estimated_sections=2,
            fallback_recommendation="",
            ui_profile="technical-study",
            legacy_strategy="text_reflowable",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=True,
        )
        content = {
            "chapters": [
                {
                    "title": "Metrics",
                    "html_parts": [
                        (
                            '<table class="report-table wide-table">'
                            "<thead><tr><th scope=\"col\">Metric</th><th scope=\"col\">Value</th></tr></thead>"
                            "<tbody><tr><td>Revenue</td><td>123 PLN</td></tr></tbody>"
                            "</table>"
                        )
                    ],
                    "_page_start": 0,
                    "_page_end": 1,
                }
            ],
            "metadata": {
                "source_table_count": 1,
                "xhtml_table_count": 1,
                "table_summary": {
                    "source_table_count": 1,
                    "xhtml_table_count": 1,
                    "table_cell_count": 4,
                    "table_row_count": 2,
                    "table_cell_coverage": 1.0,
                    "table_page_count": 2,
                    "multi_page_table_count": 0,
                    "wide_table_count": 1,
                    "low_confidence_table_count": 0,
                    "fragment_table_count": 0,
                    "review_tables": [{"index": 1, "classification": "wide"}],
                },
            },
        }

        document = publication_from_content(
            content,
            analysis,
            title="Business Report",
            author="QA",
            language="en",
        )

        self.assertEqual(document.quality_report.table_count, 1)
        self.assertEqual(document.quality_report.source_table_count, 1)
        self.assertEqual(document.quality_report.xhtml_table_count, 1)
        self.assertEqual(document.quality_report.table_cell_count, 4)
        self.assertEqual(document.quality_report.table_row_count, 2)
        self.assertEqual(document.quality_report.table_cell_coverage, 1.0)
        self.assertEqual(document.quality_report.table_page_count, 2)
        self.assertEqual(document.quality_report.wide_table_count, 1)
        self.assertEqual(document.quality_report.fragment_table_count, 0)
        self.assertEqual(
            document.quality_report.content_metrics_dict()["table_summary"]["review_tables"][0]["classification"],
            "wide",
        )

    def test_ocr_quality_result_reports_reason_codes_and_review_counts(self) -> None:
        class Page:
            def __init__(self, text: str, confidence: float) -> None:
                self.text = text
                self.confidence = confidence

        class Result:
            pages = [
                Page("Readable OCR text " * 10, 0.91),
                Page("short", 0.44),
            ]
            engine_used = "tesseract"
            total_pages = 2
            success_rate = 0.5

        payload = _ocr_quality_from_result(Result(), reason_codes=["full_document_ocr_fallback"])

        self.assertEqual(payload["status"], "passed_with_warnings")
        self.assertIn("full_document_ocr_fallback", payload["reason_codes"])
        self.assertIn("low_ocr_confidence", payload["reason_codes"])
        self.assertIn("empty_ocr_page", payload["reason_codes"])
        self.assertEqual(payload["low_confidence_page_count"], 1)
        self.assertEqual(payload["empty_ocr_page_count"], 1)
        self.assertEqual(payload["manual_review_count"], 2)

    def test_ocr_quality_is_carried_into_quality_report(self) -> None:
        analysis = _analysis(profile="scanned_reflow", is_scanned=True, has_text_layer=False)
        content = {
            "chapters": [{"title": "Scan", "page_num": 0, "html_parts": ["<p>OCR text</p>"]}],
            "metadata": {
                "ocr_quality": {
                    "status": "degraded",
                    "quality_gate_status": "degraded",
                    "reason_codes": ["ocr_unavailable", "pymupdf_fallback"],
                    "fallback_reason": "ocr_unavailable",
                    "manual_review_count": 1,
                }
            },
        }

        document = publication_from_content(
            content,
            analysis,
            title="Scan",
            author="QA",
            language="en",
        )

        self.assertEqual(document.quality_report.ocr_quality["status"], "degraded")
        self.assertIn("ocr_unavailable", document.quality_report.to_dict()["ocr_quality"]["reason_codes"])
        self.assertIn("ocr_quality", document.quality_report.content_metrics_dict())

    def test_diagram_book_back_cover_is_not_flagged_as_empty_fallback_section(self) -> None:
        analysis = PublicationAnalysis(
            profile="diagram_book_reflow",
            confidence=0.91,
            page_count=394,
            render_budget_class="",
            has_toc=True,
            has_tables=False,
            has_diagrams=True,
            has_meaningful_images=True,
            estimated_sections=18,
            fallback_recommendation="",
            ui_profile="book",
            legacy_strategy="image-first-reflow",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=False,
        )
        content = {
            "images": [],
            "chapters": [
                {
                    "title": "Back Cover",
                    "html_parts": [],
                    "images": [],
                    "_page_start": 393,
                    "_page_end": 393,
                }
            ],
            "audit": {},
        }

        document = publication_from_content(
            content,
            analysis,
            title="The Woodpecker Method",
            author="Axel Smith",
            language="en",
        )

        self.assertEqual(document.sections[0].kind, "appendix")
        self.assertEqual(document.quality_report.fallback_pages, [])
        self.assertEqual(document.quality_report.fallback_sections, [])
        self.assertEqual(document.quality_report.fallback_regions, [])
        self.assertNotIn(
            "Wykryto 1 pustych lub fallbackowych sekcji.",
            document.quality_report.warnings,
        )

    def test_extractor_contract_reports_missing_core_fields_without_crashing(self) -> None:
        analysis = _analysis(has_tables=True, has_meaningful_images=True)

        document = publication_from_content(
            {},
            analysis,
            title="Contract Probe",
            author="QA",
            language="en",
        )

        codes = {warning["code"] for warning in document.quality_report.extractor_contract_warnings}
        self.assertEqual(document.sections, [])
        self.assertIn("missing_chapters", codes)
        self.assertIn("missing_images", codes)
        self.assertIn("missing_table_summary", codes)
        self.assertEqual(
            document.quality_report.content_metrics_dict()["extractor_contract_warnings"],
            document.quality_report.extractor_contract_warnings,
        )
        self.assertTrue(
            all(warning["severity"] == "warning" for warning in document.quality_report.extractor_contract_warnings)
        )

    def test_extractor_contract_sanitizes_malformed_fields_before_document_build(self) -> None:
        analysis = _analysis(has_tables=True)
        content = {
            "images": "cover.jpg",
            "chapters": [
                {
                    "title": ["Recovered"],
                    "html_parts": "<p>Recovered paragraph</p>",
                    "images": ["not-an-asset"],
                    "_page_start": "bad",
                    "_page_end": "also-bad",
                },
                "not-a-chapter",
            ],
            "metadata": {
                "source_table_count": "bad",
                "xhtml_table_count": "also-bad",
                "table_summary": {
                    "table_cell_count": "bad",
                    "table_cell_coverage": "also-bad",
                },
            },
        }

        document = publication_from_content(
            content,
            analysis,
            title="Contract Probe",
            author="QA",
            language="en",
        )

        codes = {warning["code"] for warning in document.quality_report.extractor_contract_warnings}
        self.assertEqual(len(document.sections), 1)
        self.assertEqual(document.sections[0].blocks[0].text, "Recovered paragraph")
        self.assertEqual(document.sections[0].page_start, 0)
        self.assertEqual(document.sections[0].page_end, 0)
        self.assertEqual(document.quality_report.source_table_count, 0)
        self.assertEqual(document.quality_report.xhtml_table_count, 0)
        self.assertEqual(document.quality_report.table_cell_count, 0)
        self.assertEqual(document.quality_report.table_cell_coverage, 0.0)
        self.assertIn("malformed_images", codes)
        self.assertIn("malformed_html_parts", codes)
        self.assertIn("malformed_image", codes)
        self.assertIn("malformed_chapter", codes)
        self.assertIn("malformed_chapter_page", codes)
        self.assertIn("malformed_metadata_metric", codes)
        self.assertIn("malformed_table_summary_metric", codes)
