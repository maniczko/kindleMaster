from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from epub_premium_scoring import build_magazine_premium_quality_contract
from publication_model import PublicationAnalysis, PublicationDocument, PublicationQualityReport
from publication_pipeline import (
    _benchmark_scanned_chess_ocr_language,
    _build_scanned_content,
    _inject_scanned_chess_boards,
    _looks_like_cover_masthead_line,
    _ocr_quality_from_result,
    _preserve_weak_ocr_page_images,
    _should_coalesce_page_chapters_with_pdf_outline,
    _should_skip_external_ocr_for_large_scan,
    finalize_publication_epub,
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


def _minimal_magazine_epub(nav_labels: list[str]) -> bytes:
    buffer = io.BytesIO()
    nav_items = "\n".join(
        f'<li><a href="chapter_001.xhtml#nav-{index}">{label}</a></li>'
        for index, label in enumerate(nav_labels, start=1)
    )
    body_items = "\n".join(
        f'<section id="nav-{index}"><h1>{label}</h1><p>Clean editorial text for {label}.</p></section>'
        for index, label in enumerate(nav_labels, start=1)
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
""",
        )
        archive.writestr(
            "EPUB/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">publication-pipeline-fixture</dc:identifier>
    <dc:title>Magazine Issue</dc:title>
    <dc:creator>Editorial Team</dc:creator>
    <dc:language>en</dc:language>
    <dc:publisher>Publisher</dc:publisher>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter1"/></spine>
</package>
""",
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol>{nav_items}</ol></nav></body>
</html>
""",
        )
        archive.writestr(
            "EPUB/chapter_001.xhtml",
            f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Main Feature</title></head>
  <body>{body_items}</body>
</html>
""",
        )
    return buffer.getvalue()


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

    def test_finalize_publication_epub_refreshes_magazine_article_map_from_final_nav(self) -> None:
        article_map = {
            "article_count": 3,
            "editorial_article_count": 3,
            "toc_entry_count": 1,
            "toc_covered_article_count": 1,
            "toc_coverage": 0.333,
            "blockers": ["magazine_article_toc_coverage_below_95"],
            "review": [],
            "articles": [
                {"title": "Main Feature", "kind": "article", "toc_matched": True, "toc_excluded": False},
                {"title": "Second Feature", "kind": "article", "toc_matched": False, "toc_excluded": False},
                {"title": "Third Interview", "kind": "interview", "toc_matched": False, "toc_excluded": False},
            ],
        }
        quality_report = PublicationQualityReport(
            magazine_premium_quality=build_magazine_premium_quality_contract(
                magazine_audit={"article_map": article_map}
            )
        )
        document = PublicationDocument(
            title="Magazine Issue",
            author="Editorial Team",
            language="en",
            profile="magazine_reflow",
            analysis=_analysis(profile="magazine_reflow"),
            quality_report=quality_report,
        )

        with patch(
            "publication_pipeline.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            finalized = finalize_publication_epub(
                document,
                _minimal_magazine_epub(["Main Feature", "Second Feature", "Third Interview"]),
            )

        refreshed_map = finalized.magazine_premium_quality["article_map"]
        self.assertIn(finalized.validation_status, {"passed", "unavailable"})
        self.assertEqual(refreshed_map["coverage_source"], "final_epub_nav")
        self.assertEqual(refreshed_map["toc_coverage"], 1.0)
        self.assertEqual(refreshed_map["toc_missing_articles"], [])
        self.assertNotIn("magazine_article_toc_coverage_below_95", refreshed_map["blockers"])

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
        self.assertEqual(payload["recognized_page_count"], 1)
        self.assertEqual(payload["page_image_fallback_count"], 0)
        self.assertFalse(payload["scan_chess_image_preservation"])
        self.assertEqual(payload["manual_review_count"], 2)

    def test_ocr_quality_reports_page_image_fallback_preservation(self) -> None:
        class Page:
            def __init__(self, text: str, confidence: float) -> None:
                self.text = text
                self.confidence = confidence

        class Result:
            pages = [
                Page("Readable OCR text " * 10, 0.91),
                Page("", 0.0),
            ]
            engine_used = "ocrmypdf"
            total_pages = 2
            success_rate = 0.5

        payload = _ocr_quality_from_result(
            Result(),
            reason_codes=["ocrmypdf_full_document"],
            page_image_fallback_count=1,
        )

        self.assertEqual(payload["status"], "passed_with_warnings")
        self.assertIn("page_image_fallback", payload["reason_codes"])
        self.assertEqual(payload["recognized_page_count"], 1)
        self.assertEqual(payload["page_image_fallback_count"], 1)
        self.assertTrue(payload["scan_chess_image_preservation"])

    def test_scanned_chess_board_injection_adds_diagram_metric(self) -> None:
        class Board:
            page = 0
            bbox = (10.0, 20.0, 130.0, 140.0)
            confidence = 0.82
            filename = "scanned_board_p1_1.png"
            image_data = b"png"
            width = 240
            height = 240
            source_type = "scanned_board"

        content = {
            "chapters": [{"title": "Page 1", "page_num": 0, "html_parts": ["<p>1. e4 e5</p>"], "images": []}],
            "images": [],
        }

        import publication_pipeline

        original = publication_pipeline.detect_scanned_chess_boards if hasattr(publication_pipeline, "detect_scanned_chess_boards") else None
        try:
            publication_pipeline.detect_scanned_chess_boards = lambda *_args, **_kwargs: [Board()]
            count = _inject_scanned_chess_boards("fundamenty_1_1_sample_80p.pdf", content)
        finally:
            if original is None:
                delattr(publication_pipeline, "detect_scanned_chess_boards")
            else:
                publication_pipeline.detect_scanned_chess_boards = original

        self.assertEqual(count, 1)
        self.assertIn("chess-diagram scanned-board", content["chapters"][0]["html_parts"][-1])
        self.assertTrue(content["images"][0]["is_chess"])

        document = publication_from_content(
            content,
            _analysis(profile="scanned_reflow", is_scanned=True, has_text_layer=False),
            title="Fundamenty",
            author="QA",
            language="pl",
        )

        self.assertEqual(document.quality_report.detected_scanned_board_count, 1)
        self.assertGreaterEqual(document.quality_report.detected_diagrams, 1)

    def test_ocr_language_benchmark_selects_best_sample_language(self) -> None:
        class Config:
            ocr_language = "pol"

        class Result:
            def __init__(self, text: str, confidence: float) -> None:
                self.text = text
                self.confidence = confidence

        class Page:
            pass

        class Doc:
            def __len__(self) -> int:
                return 2

            def __getitem__(self, index: int) -> Page:
                return Page()

            def close(self) -> None:
                pass

        import publication_pipeline

        original_fitz_open = publication_pipeline.fitz.open
        original_import = __import__

        class OcrModule:
            @staticmethod
            def get_best_available_engine() -> str:
                return "tesseract"

            @staticmethod
            def run_ocr_on_page(_page, *, page_num: int, language: str, dpi: int, engine: str) -> Result:
                if language == "eng":
                    return Result("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 " * 3, 0.91)
                return Result("krótki tekst", 0.55)

        def fake_import(name, *args, **kwargs):
            if name == "ocr_module":
                return OcrModule
            return original_import(name, *args, **kwargs)

        try:
            publication_pipeline.fitz.open = lambda *_args, **_kwargs: Doc()
            import builtins

            builtins.__import__ = fake_import
            selected, benchmark = _benchmark_scanned_chess_ocr_language("fundamenty_1_1_sample_80p.pdf", Config())
        finally:
            publication_pipeline.fitz.open = original_fitz_open
            import builtins

            builtins.__import__ = original_import

        self.assertEqual(selected, "eng")
        self.assertEqual(benchmark["mode"], "sample")
        self.assertEqual(benchmark["selected_language"], "eng")

    def test_weak_ocr_page_preservation_adds_reference_image_without_empty_text(self) -> None:
        class Page:
            page_num = 0
            text = ""
            confidence = 0.0
            image_data = b"jpeg-bytes"

        class Result:
            pages = [Page()]

        content = {
            "chapters": [
                {
                    "title": "Scan page",
                    "page_num": 0,
                    "html_parts": [],
                    "images": [],
                }
            ],
            "images": [],
        }

        fallback_count = _preserve_weak_ocr_page_images(content, Result())

        self.assertEqual(fallback_count, 1)
        self.assertIn("ocr-page-fallback", content["chapters"][0]["html_parts"][0])
        self.assertEqual(content["chapters"][0]["_fallback_mode"], "ocr-page-image-preserved")
        self.assertEqual(content["images"][0]["fallback_role"], "weak_ocr_page_reference")

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

    def test_large_scanned_pdf_without_forced_ocr_skips_external_ocr(self) -> None:
        class Config:
            enable_external_ocr = True
            force_ocr = False

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "large-scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")
            with patch("converter.OCR_AVAILABLE", True), patch(
                "converter.extract_pdf_with_pymupdf",
                return_value={"chapters": [{"title": "Scan", "page_num": 0, "html_parts": []}], "metadata": {}},
            ) as fallback:
                content = _build_scanned_content(
                    str(pdf_path),
                    Config(),
                    {"source_page_count": 266},
                )

        fallback.assert_called_once()
        ocr_quality = content["metadata"]["ocr_quality"]
        self.assertTrue(ocr_quality["auto_ocr_skipped"])
        self.assertIn("ocr_skipped_large_scan", ocr_quality["reason_codes"])
        self.assertFalse(ocr_quality["environment_error"])

    def test_forced_ocr_does_not_skip_large_scanned_pdf(self) -> None:
        class Config:
            force_ocr = True

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "large-scan.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n")

            should_skip, details = _should_skip_external_ocr_for_large_scan(
                str(pdf_path),
                Config(),
                {"source_page_count": 266},
            )

        self.assertFalse(should_skip)
        self.assertEqual(details, {})

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
