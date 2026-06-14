from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

from converter import ConversionConfig
from pymupdf_chess_extractor import build_pdf_layout_preview_html
from publication_pipeline import _ensure_chess_pdf_layout_preview_artifact


class PdfLayoutPreviewTests(unittest.TestCase):
    def test_preview_renders_page_background_and_copyable_text_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "layout-preview.pdf"
            doc = fitz.open()
            page = doc.new_page(width=240, height=160)
            page.insert_text((36, 48), "1. e4 e5", fontsize=12)
            page.insert_text((36, 84), "2. Nf3 Nc6", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            html = build_pdf_layout_preview_html(
                str(pdf_path),
                ConversionConfig(pdf_layout_preview_dpi=72, pdf_layout_preview_jpeg_quality=70),
                title="Preview sample",
            )

        self.assertIn('class="pdf-page"', html)
        self.assertIn('style="width:240.00px;height:160.00px"', html)
        self.assertIn("data:image/jpeg;base64,", html)
        self.assertIn('class="pdf-text-span"', html)
        self.assertIn("1. e4 e5", html)
        self.assertIn("Show text layer", html)

    def test_pipeline_adds_preview_for_chess_like_book_reflow_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "book-reflow-chess.pdf"
            doc = fitz.open()
            page = doc.new_page(width=360, height=240)
            page.insert_text((36, 48), "D00 Chess opening", fontsize=12)
            page.insert_text((36, 84), "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            content = _ensure_chess_pdf_layout_preview_artifact(
                {"chapters": [], "extra_artifacts": []},
                str(pdf_path),
                ConversionConfig(pdf_layout_preview_dpi=72),
                analysis=SimpleNamespace(profile="book_reflow", detected_features=[]),
                source_title="Book reflow chess",
            )

        artifacts = content["extra_artifacts"]
        self.assertEqual([artifact["key"] for artifact in artifacts], ["pdf_layout_preview"])
        self.assertIn(b'class="pdf-page"', artifacts[0]["data"])

    def test_pipeline_does_not_add_preview_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "disabled-preview.pdf"
            doc = fitz.open()
            page = doc.new_page(width=360, height=240)
            page.insert_text((36, 84), "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *", fontsize=12)
            doc.save(pdf_path)
            doc.close()

            content = _ensure_chess_pdf_layout_preview_artifact(
                {"chapters": [], "extra_artifacts": []},
                str(pdf_path),
                ConversionConfig(pdf_layout_preview_enabled=False),
                analysis=SimpleNamespace(profile="book_reflow", detected_features=[]),
                source_title="Disabled preview",
            )

        self.assertEqual(content["extra_artifacts"], [])


if __name__ == "__main__":
    unittest.main()
