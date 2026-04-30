from __future__ import annotations

import base64
import io
import unittest
import zipfile

from PIL import Image, ImageDraw

from converter import ConversionConfig, build_epub
from chess_diagram_renderer import generate_chess_diagram_css
from fixed_layout_builder_v2 import FIXED_LAYOUT_CSS_V2
from publication_model import PublicationAnalysis, PublicationBlock, PublicationDocument, PublicationSection
from publication_pipeline import publication_to_content
from pymupdf_chess_extractor import (
    _archive_cost,
    _build_line_items,
    _chess_diagram_quality_score,
    _encode_legacy_size_ceiling_png,
    _normalize_text_for_epub,
    _optimize_chess_diagram_export,
)


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO8B9SMAAAAASUVORK5CYII="
)


class ChessDiagramEpubBuilderTests(unittest.TestCase):
    def _read_epub_entry(self, epub_bytes: bytes, entry_name: str) -> str:
        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
            return archive.read(entry_name).decode("utf-8")

    def test_build_epub_preserves_chess_assets_without_duplicate_loss(self) -> None:
        content = {
            "chapters": [
                {
                    "title": "Easy Exercises",
                    "html_parts": ["<p>White to move and win.</p>"],
                    "images": [
                        {
                            "filename": "board_001.png",
                            "data": _TINY_PNG,
                            "extension": "png",
                            "bbox": (0, 0, 220, 220),
                            "is_chess": True,
                        }
                    ],
                },
                {
                    "title": "Solutions to Easy Exercises",
                    "html_parts": ["<p>Solution diagram.</p>"],
                    "images": [
                        {
                            "filename": "board_001.png",
                            "data": _TINY_PNG,
                            "extension": "png",
                            "bbox": (0, 0, 220, 220),
                            "is_chess": True,
                        },
                        {
                            "filename": "board_002.png",
                            "data": _TINY_PNG,
                            "extension": "png",
                            "bbox": (0, 0, 220, 220),
                            "is_chess": True,
                        },
                    ],
                },
            ],
            "images": [
                {
                    "filename": "board_001.png",
                    "data": _TINY_PNG,
                    "extension": "png",
                }
            ],
            "method": "unit-probe",
        }

        epub_bytes = build_epub(
            content,
            ConversionConfig(language="en"),
            "woodpecker.pdf",
            {"title": "Woodpecker Probe", "author": "Codex QA"},
        )

        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
            names = archive.namelist()
            image_names = [name for name in names if name.startswith("EPUB/images/board_")]

        self.assertCountEqual(
            image_names,
            [
                "EPUB/images/board_001.png",
                "EPUB/images/board_002.png",
            ],
        )

        chapter_one = self._read_epub_entry(epub_bytes, "EPUB/chapter_001.xhtml")
        chapter_two = self._read_epub_entry(epub_bytes, "EPUB/chapter_002.xhtml")
        self.assertIn('class="chess-diagram"', chapter_one)
        self.assertIn('src="images/board_001.png"', chapter_one)
        self.assertIn('src="images/board_001.png"', chapter_two)
        self.assertIn('src="images/board_002.png"', chapter_two)

    def test_build_epub_does_not_append_already_inline_chess_diagrams(self) -> None:
        content = {
            "chapters": [
                {
                    "title": "Easy Exercises",
                    "html_parts": [
                        '<div class="figure chess-diagram-container">'
                        '<img class="chess-diagram" src="images/board_inline.png" alt="Diagram szachowy"/>'
                        "</div>"
                    ],
                    "images": [
                        {
                            "filename": "board_inline.png",
                            "data": _TINY_PNG,
                            "extension": "png",
                            "bbox": (0, 0, 220, 220),
                            "is_chess": True,
                        }
                    ],
                }
            ],
            "images": [],
            "method": "unit-probe",
        }

        epub_bytes = build_epub(
            content,
            ConversionConfig(language="en"),
            "woodpecker.pdf",
            {"title": "Woodpecker Probe", "author": "Codex QA"},
        )

        chapter_one = self._read_epub_entry(epub_bytes, "EPUB/chapter_001.xhtml")
        self.assertEqual(chapter_one.count('src="images/board_inline.png"'), 1)
        self.assertNotIn("chess-diagrams-section", chapter_one)

    def test_publication_to_content_preserves_inline_chess_diagram_marker(self) -> None:
        analysis = PublicationAnalysis(
            profile="diagram_book_reflow",
            confidence=0.95,
            page_count=1,
            render_budget_class="",
            has_toc=False,
            has_tables=False,
            has_diagrams=True,
            has_meaningful_images=True,
            estimated_sections=1,
            fallback_recommendation="",
            ui_profile="diagram_book_reflow",
            legacy_strategy="",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=True,
        )
        asset = {
            "filename": "board_inline.png",
            "data": _TINY_PNG,
            "extension": "png",
            "bbox": (0, 0, 220, 220),
            "is_chess": True,
        }
        section = PublicationSection(
            section_id="section-001",
            title="Easy Exercises",
            blocks=[
                PublicationBlock(
                    block_type="diagram",
                    raw_html=(
                        '<div class="figure chess-diagram-container">'
                        '<img class="chess-diagram" src="images/board_inline.png" alt="Diagram szachowy"/>'
                        "</div>"
                    ),
                )
            ],
            assets=[asset],
        )
        document = PublicationDocument(
            title="Woodpecker Probe",
            author="Codex QA",
            language="en",
            profile="diagram_book_reflow",
            analysis=analysis,
            sections=[section],
            assets=[asset],
        )

        content = publication_to_content(document)

        self.assertTrue(content["chapters"][0]["inline_chess_diagrams"])
        self.assertEqual(content["chapters"][0]["images"][0]["filename"], "board_inline.png")

    def test_build_epub_keeps_image_only_sections_instead_of_empty_placeholder(self) -> None:
        content = {
            "chapters": [
                {
                    "title": "Diagram Drill",
                    "html_parts": [],
                    "images": [
                        {
                            "filename": "diagram_only.png",
                            "data": _TINY_PNG,
                            "extension": "png",
                            "bbox": (0, 0, 320, 320),
                            "is_chess": False,
                        }
                    ],
                }
            ],
            "images": [
                {
                    "filename": "diagram_only.png",
                    "data": _TINY_PNG,
                    "extension": "png",
                }
            ],
            "method": "unit-probe",
        }

        epub_bytes = build_epub(
            content,
            ConversionConfig(language="en"),
            "diagram-book.pdf",
            {"title": "Diagram Book", "author": "Codex QA"},
        )

        chapter_one = self._read_epub_entry(epub_bytes, "EPUB/chapter_001.xhtml")
        self.assertIn("<h1>Diagram Drill</h1>", chapter_one)
        self.assertIn('src="images/diagram_only.png"', chapter_one)
        self.assertNotIn("Brak tre", chapter_one)

    def test_build_epub_reuses_existing_first_image_for_cover_instead_of_duplicating_cover_asset(self) -> None:
        content = {
            "chapters": [
                {
                    "title": "Front Cover",
                    "html_parts": ["<p>Cover chapter.</p>"],
                    "images": [],
                }
            ],
            "images": [
                {
                    "filename": "img_p0_1.jpeg",
                    "data": self._tiny_jpeg_bytes(size=(32, 48)),
                    "extension": "jpeg",
                }
            ],
            "method": "unit-probe",
        }

        epub_bytes = build_epub(
            content,
            ConversionConfig(language="en"),
            "cover-probe.pdf",
            {"title": "Cover Probe", "author": "Codex QA"},
        )

        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
            image_names = sorted(
                name
                for name in archive.namelist()
                if name.startswith("EPUB/images/") and name.lower().endswith((".png", ".jpg", ".jpeg"))
            )
            cover_xhtml = archive.read("EPUB/cover.xhtml").decode("utf-8")

        self.assertEqual(image_names, ["EPUB/images/img_p0_1.jpeg"])
        self.assertIn('src="images/img_p0_1.jpeg"', cover_xhtml)
        self.assertNotIn("images/cover.jpeg", cover_xhtml)

    def test_build_epub_uses_reader_smooth_scaling_for_chess_diagrams(self) -> None:
        content = {
            "chapters": [
                {
                    "title": "Diagram Drill",
                    "html_parts": ["<p>White to move.</p>"],
                    "images": [
                        {
                            "filename": "board_001.png",
                            "data": _TINY_PNG,
                            "extension": "png",
                            "bbox": (0, 0, 220, 220),
                            "is_chess": True,
                        }
                    ],
                }
            ],
            "images": [],
            "method": "unit-probe",
        }

        epub_bytes = build_epub(
            content,
            ConversionConfig(language="en"),
            "diagram-book.pdf",
            {"title": "Diagram Book", "author": "Codex QA"},
        )

        stylesheet = self._read_epub_entry(epub_bytes, "EPUB/style/default.css")
        chess_rule = stylesheet[stylesheet.index(".chess-diagram {") : stylesheet.index(".diagram-caption {")]
        self.assertIn("image-rendering: auto;", chess_rule)
        self.assertNotIn("crisp-edges", chess_rule)

    def test_chess_renderer_css_uses_reader_smooth_scaling(self) -> None:
        renderer_css = generate_chess_diagram_css()

        self.assertIn("image-rendering: auto;", renderer_css)
        self.assertNotIn("crisp-edges", renderer_css)
        self.assertNotIn("pixelated", FIXED_LAYOUT_CSS_V2)

    def test_optimize_chess_diagram_export_reduces_palette_and_caps_long_edge(self) -> None:
        image = Image.new("L", (900, 900), 255)
        draw = ImageDraw.Draw(image)
        cell = 100
        for row in range(8):
            for col in range(8):
                fill = 220 if (row + col) % 2 == 0 else 70
                draw.rectangle(
                    (col * cell, row * cell, (col + 1) * cell, (row + 1) * cell),
                    fill=fill,
                )
        for offset in range(8):
            draw.line((0, offset * cell, 900, offset * cell), fill=0, width=3)
            draw.line((offset * cell, 0, offset * cell, 900), fill=0, width=3)

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False, compress_level=0)
        raw_png = output.getvalue()

        optimized_png, width, height = _optimize_chess_diagram_export(
            raw_png,
            ConversionConfig(
                diagram_image_long_edge=560,
                diagram_palette_colors=12,
            ),
        )

        self.assertLess(len(optimized_png), len(raw_png))
        self.assertEqual(max(width, height), 560)
        with Image.open(io.BytesIO(optimized_png)) as optimized_image:
            self.assertEqual(max(optimized_image.size), 560)
            self.assertIn(optimized_image.mode, {"P", "L"})

    def test_optimize_chess_diagram_export_never_increases_over_legacy_size_ceiling(self) -> None:
        image = Image.new("L", (720, 720), 245)
        draw = ImageDraw.Draw(image)
        cell = 90
        for row in range(8):
            for col in range(8):
                fill = 225 if (row + col) % 2 == 0 else 112
                draw.rectangle(
                    (col * cell, row * cell, (col + 1) * cell, (row + 1) * cell),
                    fill=fill,
                )
        for offset in range(9):
            draw.line((0, offset * cell, 720, offset * cell), fill=58, width=2)
            draw.line((offset * cell, 0, offset * cell, 720), fill=58, width=2)
        draw.ellipse((185, 185, 260, 260), outline=40, width=7)
        draw.rectangle((455, 455, 525, 525), outline=40, width=7)

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False, compress_level=0)
        raw_png = output.getvalue()
        config = ConversionConfig(diagram_image_long_edge=560, diagram_palette_colors=12)

        source_image = Image.open(io.BytesIO(raw_png)).convert("L")
        legacy_ceiling_png = _encode_legacy_size_ceiling_png(
            source_image,
            config.diagram_palette_colors,
            config.diagram_image_long_edge,
        )
        optimized_png, _, _ = _optimize_chess_diagram_export(raw_png, config)

        self.assertLessEqual(_archive_cost(optimized_png), _archive_cost(legacy_ceiling_png))

    def test_optimize_chess_diagram_export_can_improve_contrast_without_size_cost(self) -> None:
        image = Image.new("L", (480, 480), 164)
        draw = ImageDraw.Draw(image)
        cell = 60
        for row in range(8):
            for col in range(8):
                fill = 166 if (row + col) % 2 == 0 else 118
                draw.rectangle(
                    (col * cell, row * cell, (col + 1) * cell, (row + 1) * cell),
                    fill=fill,
                )
        for offset in range(9):
            draw.line((0, offset * cell, 480, offset * cell), fill=92, width=2)
            draw.line((offset * cell, 0, offset * cell, 480), fill=92, width=2)
        draw.ellipse((145, 88, 215, 190), outline=72, width=5)
        draw.rectangle((270, 270, 340, 340), outline=72, width=5)

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False, compress_level=0)
        raw_png = output.getvalue()
        config = ConversionConfig(diagram_image_long_edge=480, diagram_palette_colors=8)

        baseline_png = _encode_legacy_size_ceiling_png(
            image,
            config.diagram_palette_colors,
            config.diagram_image_long_edge,
        )
        optimized_png, _, _ = _optimize_chess_diagram_export(raw_png, config)

        self.assertLessEqual(_archive_cost(optimized_png), _archive_cost(baseline_png))
        self.assertGreaterEqual(
            _chess_diagram_quality_score(optimized_png),
            _chess_diagram_quality_score(baseline_png),
        )

    def test_optimize_chess_diagram_export_handles_low_contrast_hatched_board_with_thin_pieces(self) -> None:
        image = Image.new("L", (640, 640), 148)
        draw = ImageDraw.Draw(image)
        cell = 80
        for row in range(8):
            for col in range(8):
                light_square = (row + col) % 2 == 0
                fill = 156 if light_square else 132
                hatch = 146 if light_square else 122
                left = col * cell
                top = row * cell
                right = (col + 1) * cell
                bottom = (row + 1) * cell
                draw.rectangle((left, top, right, bottom), fill=fill)
                for offset in range(-cell, cell, 8):
                    draw.line(
                        (left + offset, bottom, left + offset + cell, top),
                        fill=hatch,
                        width=1,
                    )
        for offset in range(9):
            draw.line((0, offset * cell, 640, offset * cell), fill=108, width=1)
            draw.line((offset * cell, 0, offset * cell, 640), fill=108, width=1)
        draw.ellipse((185, 105, 255, 205), outline=96, width=2)
        draw.line((220, 105, 220, 70), fill=96, width=2)
        draw.line((206, 84, 234, 84), fill=96, width=2)
        draw.rectangle((374, 356, 434, 452), outline=96, width=2)
        draw.line((386, 356, 422, 320), fill=96, width=2)
        draw.line((374, 452, 434, 452), fill=96, width=2)

        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False, compress_level=0)
        raw_png = output.getvalue()
        config = ConversionConfig(diagram_image_long_edge=560, diagram_palette_colors=8)

        baseline_png = _encode_legacy_size_ceiling_png(
            image,
            config.diagram_palette_colors,
            config.diagram_image_long_edge,
        )
        optimized_png, width, height = _optimize_chess_diagram_export(raw_png, config)

        self.assertGreater(
            _chess_diagram_quality_score(optimized_png),
            _chess_diagram_quality_score(baseline_png),
        )
        self.assertLessEqual(_archive_cost(optimized_png), _archive_cost(baseline_png))
        self.assertEqual(max(width, height), config.diagram_image_long_edge)
        with Image.open(io.BytesIO(optimized_png)) as optimized_image:
            self.assertEqual(max(optimized_image.size), config.diagram_image_long_edge)
            colors = optimized_image.convert("P").getcolors(maxcolors=256)
            self.assertIsNotNone(colors)
            self.assertLessEqual(len(colors or []), config.diagram_palette_colors)

    def test_figural_chess_notation_maps_every_piece_to_san_letter(self) -> None:
        self.assertEqual(
            _normalize_text_for_epub("\xa2\xa3\xa4\xa5\xa6", "SPTimeFig-Roman"),
            "KQRBN",
        )
        self.assertEqual(
            _normalize_text_for_epub("\xa2\xa3\xa4\xa5\xa6", "SPAriesFig-Bold"),
            "KQRBN",
        )
        self.assertEqual(
            _normalize_text_for_epub("\xa2b5 \xa3xd7+ \xa4a8 \xa5g2 \xa6f3", "SPTimeFig-Roman"),
            "Kb5 Qxd7+ Ra8 Bg2 Nf3",
        )
        self.assertEqual(_normalize_text_for_epub("\xb1", "SPAriesFig-Bold"), " \u00b1")
        self.assertEqual(_normalize_text_for_epub("\xb2", "SPAriesFig-Bold"), " +=")
        self.assertEqual(_normalize_text_for_epub("\xb3", "SPAriesFig-Bold"), " =+")
        self.assertEqual(_normalize_text_for_epub("\xb5", "SPAriesFig-Bold"), " \u2213")
        self.assertEqual(_normalize_text_for_epub("\xa9", "SPAriesFig-Bold"), " with compensation")
        self.assertEqual(_normalize_text_for_epub("\u201e", "SPAriesFig-Bold"), " with counterplay")
        self.assertEqual(_normalize_text_for_epub("\xf7", "SPAriesFig-Bold"), " unclear")

    def test_figural_chess_notation_keeps_piece_letters_attached_to_squares(self) -> None:
        raw_lines = [
            {
                "segments": [
                    {"index": 1, "text": "35...", "font_name": "Times-Roman", "font_size": 10, "x0": 0, "x1": 24, "y0": 0},
                    {"index": 2, "text": "\xa3", "font_name": "SPTimeFig-Roman", "font_size": 10, "x0": 28, "x1": 34, "y0": 0},
                    {"index": 3, "text": "xh4", "font_name": "Times-Roman", "font_size": 10, "x0": 34, "x1": 52, "y0": 0},
                    {"index": 4, "text": "\u2020", "font_name": "Times-Roman", "font_size": 10, "x0": 52, "x1": 58, "y0": 0},
                    {"index": 5, "text": " 36.", "font_name": "Times-Roman", "font_size": 10, "x0": 64, "x1": 88, "y0": 0},
                    {"index": 6, "text": "\xa2", "font_name": "SPTimeFig-Roman", "font_size": 10, "x0": 92, "x1": 98, "y0": 0},
                    {"index": 7, "text": "xh4", "font_name": "Times-Roman", "font_size": 10, "x0": 98, "x1": 116, "y0": 0},
                ]
            }
        ]

        line_items = _build_line_items(raw_lines, set())

        self.assertEqual(len(line_items), 1)
        self.assertIn("Qxh4+ 36. Kxh4", line_items[0].text)
        self.assertNotIn("Q xh4", line_items[0].text)
        self.assertNotIn("K xh4", line_items[0].text)

    def test_figural_chess_notation_preserves_promotion_and_suffixes(self) -> None:
        raw_lines = [
            {
                "segments": [
                    {"index": 1, "text": "58.", "font_name": "Times-Roman", "font_size": 10, "x0": 0, "x1": 18, "y0": 0},
                    {"index": 2, "text": "e8=", "font_name": "Times-Roman", "font_size": 10, "x0": 22, "x1": 42, "y0": 0},
                    {"index": 3, "text": "\xa3", "font_name": "SPTimeFig-Roman", "font_size": 10, "x0": 42, "x1": 48, "y0": 0},
                    {"index": 4, "text": "+", "font_name": "Times-Roman", "font_size": 10, "x0": 48, "x1": 54, "y0": 0},
                    {"index": 5, "text": "!!", "font_name": "Times-Roman", "font_size": 10, "x0": 54, "x1": 66, "y0": 0},
                    {"index": 6, "text": " 59. O-O-O", "font_name": "Times-Roman", "font_size": 10, "x0": 72, "x1": 128, "y0": 0},
                    {"index": 7, "text": " 1/2-1/2", "font_name": "Times-Roman", "font_size": 10, "x0": 132, "x1": 184, "y0": 0},
                ]
            }
        ]

        line_items = _build_line_items(raw_lines, set())

        self.assertEqual(line_items[0].text, "58. e8=Q+!! 59. O-O-O 1/2-1/2")
        self.assertNotIn("= Q", line_items[0].text)

    @staticmethod
    def _tiny_jpeg_bytes(*, size: tuple[int, int]) -> bytes:
        image = Image.new("RGB", size, (240, 240, 240))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=84, optimize=True)
        return output.getvalue()


if __name__ == "__main__":
    unittest.main()
