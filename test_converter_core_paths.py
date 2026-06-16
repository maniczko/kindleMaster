from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from converter import (
    ConversionConfig,
    _legacy_convert_pdf_to_epub,
    _parse_pdf2htmlex_output,
    convert_document_to_epub_with_report,
    convert_pdf_to_epub_with_report,
    detect_pdf_type,
    extract_pdf_with_pymupdf,
    optimize_image_data,
    pdf_to_html_fixed_layout,
)


class FakeRect:
    width = 600
    height = 800


class FakePdfPage:
    rect = FakeRect()

    def __init__(self, *, text: str = "", blocks: list[dict] | None = None, images: list[tuple] | None = None):
        self._text = text
        self._blocks = blocks if blocks is not None else _text_blocks(text)
        self._images = images or []

    def get_text(self, kind: str | None = None, **_kwargs):
        if kind == "dict":
            return {"blocks": self._blocks}
        return self._text

    def get_images(self, full: bool = False):
        return self._images


class FakePdfDoc:
    def __init__(self, pages: list[FakePdfPage], *, toc: list[list] | None = None, xobjects: dict[int, dict] | None = None):
        self._pages = pages
        self._toc = toc or []
        self._xobjects = xobjects or {}
        self.closed = False

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, index: int):
        return self._pages[index]

    def get_toc(self):
        return self._toc

    def extract_image(self, xref: int):
        return self._xobjects.get(xref)

    def close(self):
        self.closed = True


def _span(text: str, *, size: int = 12, flags: int = 0) -> dict:
    return {"text": text, "size": size, "flags": flags, "font": "Test", "color": 0}


def _text_blocks(text: str, *, size: int = 12) -> list[dict]:
    return [
        {
            "type": 0,
            "bbox": (0, 0, 550, 100),
            "lines": [{"bbox": (0, 0, 550, 100), "spans": [_span(text, size=size)]}],
        }
    ]


def _image_block(data: bytes, *, y: int = 100) -> dict:
    return {"type": 1, "bbox": (0, y, 120, y + 80), "image": data}


class ConverterPdfTypeTests(unittest.TestCase):
    def test_detect_pdf_type_classifies_text_scanned_layout_and_hybrid_documents(self) -> None:
        text = "This page has a proper text layer. " * 3
        cases = [
            (
                [FakePdfPage(text=text, images=[]) for _ in range(3)],
                "text_reflowable",
                {"has_text_layer": True, "has_images": False, "is_scanned": False, "text_heavy": True},
            ),
            (
                [FakePdfPage(text="", blocks=[], images=[(1,)]) for _ in range(3)],
                "ocr_fixed",
                {"has_text_layer": False, "has_images": True, "is_scanned": True},
            ),
            (
                [FakePdfPage(text=text, images=[(1,)]) for _ in range(3)],
                "layout_fixed",
                {"has_text_layer": True, "has_images": True, "layout_heavy": True},
            ),
            (
                [
                    FakePdfPage(text=text, images=[(1,)]),
                    FakePdfPage(text=text, images=[]),
                    FakePdfPage(text=text, images=[]),
                ],
                "hybrid",
                {"has_text_layer": True, "has_images": True, "layout_heavy": False},
            ),
        ]

        for pages, expected_strategy, expected_flags in cases:
            with self.subTest(strategy=expected_strategy):
                fake_doc = FakePdfDoc(pages)
                with patch("converter.fitz.open", return_value=fake_doc):
                    result = detect_pdf_type("sample.pdf")

                self.assertEqual(result["recommended_strategy"], expected_strategy)
                self.assertEqual(result["page_count"], len(pages))
                for key, expected in expected_flags.items():
                    self.assertEqual(result[key], expected)
                self.assertTrue(fake_doc.closed)

    def test_detect_pdf_type_handles_empty_pdf_without_division_errors(self) -> None:
        with patch("converter.fitz.open", return_value=FakePdfDoc([])):
            result = detect_pdf_type("empty.pdf")

        self.assertEqual(result["page_count"], 0)
        self.assertEqual(result["image_page_ratio"], 0.0)
        self.assertEqual(result["text_page_ratio"], 0.0)
        self.assertEqual(result["recommended_strategy"], "text_reflowable")


class ConverterLegacyRouteTests(unittest.TestCase):
    def test_legacy_route_uses_magazine_reflow_for_layout_heavy_text_pdf_without_finalizing_twice(self) -> None:
        content = {
            "success": True,
            "method": "magazine-kindle-reflow",
            "chapters": [{"title": "Article", "html_parts": ["<p>Body</p>"]}],
            "images": [],
            "text_content": True,
        }
        reflow = Mock(return_value=content)

        with patch(
            "converter.detect_pdf_type",
            return_value={
                "recommended_strategy": "layout_fixed",
                "is_scanned": False,
                "has_text_layer": True,
                "has_images": True,
                "layout_heavy": True,
                "text_heavy": False,
                "page_count": 12,
                "scanned_page_ratio": 0.0,
            },
        ), patch("converter._extract_pdf_metadata", return_value={"title": "Magazine"}), patch.dict(
            "sys.modules",
            {"magazine_kindle_reflow": SimpleNamespace(convert_magazine_to_kindle_reflow=reflow)},
        ), patch("converter.build_epub", return_value=b"magazine-epub") as build_epub, patch(
            "converter.finalize_epub_bytes", return_value=b"finalized"
        ) as finalize:
            epub_bytes = _legacy_convert_pdf_to_epub(
                "magazine.pdf",
                config=ConversionConfig(profile="auto-premium"),
                original_filename="magazine.pdf",
            )

        self.assertEqual(epub_bytes, b"magazine-epub")
        reflow.assert_called_once()
        build_epub.assert_called_once()
        finalize.assert_not_called()

    def test_legacy_route_prefers_fixed_layout_budget_for_scanned_preserve_layout_pdf(self) -> None:
        with patch(
            "converter.detect_pdf_type",
            return_value={
                "recommended_strategy": "ocr_fixed",
                "is_scanned": True,
                "has_text_layer": False,
                "has_images": True,
                "layout_heavy": False,
                "text_heavy": False,
                "page_count": 130,
                "scanned_page_ratio": 0.8,
            },
        ), patch("converter._extract_pdf_metadata", return_value={"title": "Scan"}), patch(
            "converter._build_fixed_layout_epub_with_budget",
            return_value=(
                b"fixed-layout",
                {
                    "builder": "fixed_layout_v2",
                    "render_budget_attempt": "primary",
                    "final_output_size_bytes": 12,
                },
            ),
        ) as fixed_builder, patch("converter.extract_pdf_with_pymupdf") as fallback:
            epub_bytes = _legacy_convert_pdf_to_epub(
                "scan.pdf",
                config=ConversionConfig(prefer_fixed_layout=True),
                original_filename="scan.pdf",
            )

        self.assertEqual(epub_bytes, b"fixed-layout")
        self.assertEqual(fixed_builder.call_args.kwargs["render_budget_class"], "fixed_layout_extreme")
        fallback.assert_not_called()

    def test_convert_pdf_to_epub_with_report_falls_back_to_legacy_payload_when_premium_pipeline_fails(self) -> None:
        with patch("converter._extract_pdf_metadata", return_value={"title": "Fallback Source"}), patch(
            "publication_analysis.analyze_publication",
            side_effect=RuntimeError("analysis unavailable"),
        ), patch("converter._legacy_convert_pdf_to_epub", return_value=b"legacy-epub") as legacy:
            result = convert_pdf_to_epub_with_report(
                "source.pdf",
                config=ConversionConfig(),
                original_filename="source.pdf",
            )

        self.assertEqual(result["epub_bytes"], b"legacy-epub")
        self.assertEqual(result["analysis"]["profile"], "legacy-fallback")
        self.assertIn("analysis unavailable", result["quality_report"]["validation_messages"][0])
        legacy.assert_called_once()

    def test_convert_document_dispatches_docx_and_rejects_unsupported_source_type(self) -> None:
        expected = {"epub_bytes": b"docx", "source_type": "docx"}
        with patch("converter.convert_docx_to_epub_with_report", return_value=expected) as docx_converter:
            result = convert_document_to_epub_with_report(
                "sample.any",
                config=ConversionConfig(),
                original_filename="sample.docx",
                source_type="docx",
            )

        self.assertEqual(result, expected)
        docx_converter.assert_called_once()

        with self.assertRaises(ValueError):
            convert_document_to_epub_with_report("sample.txt", source_type="txt")


class ConverterExtractionAndImageTests(unittest.TestCase):
    def test_extract_pdf_with_pymupdf_preserves_headings_images_toc_and_xobject_images(self) -> None:
        png = b"\x89PNG\r\n\x1a\nsmall"
        jpeg = b"\xff\xd8\xff\xe0jpeg"
        heading_block = {
            "type": 0,
            "bbox": (0, 0, 300, 20),
            "lines": [
                {
                    "bbox": (0, 0, 300, 20),
                    "spans": [
                        _span("Chapter Title", size=24, flags=16),
                        _span(" body@example.com", size=12, flags=0),
                    ],
                }
            ],
        }
        body_block = {
            "type": 0,
            "bbox": (0, 40, 300, 80),
            "lines": [{"bbox": (0, 40, 300, 80), "spans": [_span("Reader body text", size=12, flags=2)]}],
        }
        fake_doc = FakePdfDoc(
            [
                FakePdfPage(
                    text="Chapter Title Reader body text",
                    blocks=[heading_block, body_block, _image_block(png)],
                    images=[(7,)],
                )
            ],
            toc=[[1, "TOC Chapter", 1]],
            xobjects={7: {"image": jpeg, "ext": "jpg"}},
        )

        with patch("converter.fitz.open", return_value=fake_doc):
            result = extract_pdf_with_pymupdf(
                "source.pdf",
                ConversionConfig(),
                pdf_metadata={"title": "Source"},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "pymupdf")
        self.assertEqual(result["chapters"][0]["title"], "TOC Chapter")
        self.assertEqual(len(result["images"]), 2)
        joined_html = "".join(result["chapters"][0]["html_parts"])
        self.assertIn("<h1>", joined_html)
        self.assertIn("<em>Reader body text</em>", joined_html)
        self.assertNotIn("body@example.com", joined_html)
        self.assertTrue(fake_doc.closed)

    def test_optimize_image_data_respects_compression_flag_and_handles_invalid_bytes(self) -> None:
        config = ConversionConfig(compress_images=False)
        self.assertEqual(optimize_image_data(b"not-an-image", config), b"not-an-image")

        compressed_config = ConversionConfig(compress_images=True, image_max_width=100, image_max_height=100)
        self.assertEqual(optimize_image_data(b"not-an-image", compressed_config), b"not-an-image")

    def test_optimize_image_data_resizes_transparent_images(self) -> None:
        try:
            from PIL import Image
        except ImportError as error:  # pragma: no cover
            raise unittest.SkipTest("Pillow is required for image optimization tests") from error

        source = io.BytesIO()
        Image.new("RGBA", (400, 300), (255, 0, 0, 128)).save(source, format="PNG")

        optimized = optimize_image_data(
            source.getvalue(),
            ConversionConfig(compress_images=True, image_max_width=120, image_max_height=120),
        )

        image = Image.open(io.BytesIO(optimized))
        self.assertLessEqual(max(image.size), 120)

    def test_parse_pdf2htmlex_output_extracts_text_and_generated_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "figure.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nimage")
            parsed = _parse_pdf2htmlex_output(
                {
                    "html": '<div class="page"><span class="t">Hello user@example.com</span><img src="figure.png"/></div>',
                    "files": [{"filename": "figure.png", "filepath": str(image_path)}],
                },
                ConversionConfig(),
            )

        self.assertTrue(parsed["success"])
        self.assertEqual(parsed["method"], "pdf2htmlEX_parsed")
        self.assertEqual(len(parsed["chapters"]), 1)
        self.assertEqual(len(parsed["images"]), 1)
        self.assertNotIn("user@example.com", "".join(parsed["chapters"][0]["html_parts"]))

    def test_pdf_to_html_fixed_layout_maps_unavailable_error_timeout_and_success(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "pdf2htmlEX is not installed"):
            pdf_to_html_fixed_layout("source.pdf", "unused", ConversionConfig())

        with tempfile.TemporaryDirectory() as temp_dir:
            output_html = Path(temp_dir) / "output.html"

            def fake_run(_cmd, **_kwargs):
                output_html.write_text("<html>ok</html>", encoding="utf-8")
                return SimpleNamespace(returncode=0, stderr="")

            with patch("converter.check_pdf2htmlEX_available", return_value=True), patch(
                "converter.subprocess.run",
                side_effect=fake_run,
            ):
                result = pdf_to_html_fixed_layout("source.pdf", temp_dir, ConversionConfig())

        self.assertTrue(result["success"])
        self.assertEqual(result["html"], "<html>ok</html>")
        self.assertTrue(any(item["filename"] == "output.html" for item in result["files"]))


if __name__ == "__main__":
    unittest.main()
