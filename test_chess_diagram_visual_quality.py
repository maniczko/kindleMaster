from __future__ import annotations

import io
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from converter import CHESS_REFLOW_CSS
from chess_diagram_renderer import ChessDiagramRegion, _crop_to_board_region, render_chess_diagram_to_png
from kindle_semantic_cleanup import _maybe_enhance_diagram_asset
from pymupdf_chess_extractor import _expand_chess_region_for_auxiliary_labels, _prepare_chess_diagram_for_reader
from scripts.run_smoke_tests import _evaluate_chess_asset_quality_gate, _inspect_epub_chess_quality


class ChessDiagramVisualQualityTests(unittest.TestCase):
    def test_reflow_css_does_not_add_decorative_frame_or_padding(self) -> None:
        diagram_block = _css_rule_block(CHESS_REFLOW_CSS, ".chess-diagram")

        self.assertIn("image-rendering: auto;", diagram_block)
        self.assertNotRegex(diagram_block, r"(?m)^\s*border\s*:")
        self.assertNotRegex(diagram_block, r"(?m)^\s*padding\s*:")

    def test_semantic_chess_css_does_not_add_decorative_frame_or_padding(self) -> None:
        from kindle_semantic_cleanup import KINDLE_CSS

        diagram_block = _css_rule_block(KINDLE_CSS, ".chess-problem img")

        self.assertIn("image-rendering: auto;", diagram_block)
        self.assertNotRegex(diagram_block, r"(?m)^\s*border\s*:")
        self.assertNotRegex(diagram_block, r"(?m)^\s*padding\s*:")

    def test_smoke_metrics_report_css_frame_and_image_whitespace(self) -> None:
        board = Image.new("L", (120, 120), 255)
        draw = ImageDraw.Draw(board)
        for row in range(8):
            for col in range(8):
                if (row + col) % 2:
                    draw.rectangle((col * 15, row * 15, (col + 1) * 15 - 1, (row + 1) * 15 - 1), fill=32)
        framed = Image.new("L", (160, 150), 255)
        framed.paste(board, (20, 12))

        epub_bytes = _build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "EPUB/styles/chess.css": """
.chess-diagram {
  image-rendering: auto;
  border: 1px solid #ddd;
  padding: 3px;
}
""",
                "EPUB/chapter.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><link rel="stylesheet" href="styles/chess.css"/></head>
  <body><img class="chess-diagram" src="images/board.png" alt="board"/></body>
</html>
""",
                "EPUB/images/board.png": _png_bytes(framed),
            }
        )

        metrics = _inspect_epub_chess_quality(epub_bytes)

        self.assertEqual(metrics["chess_css_border_rule_count"], 1)
        self.assertEqual(metrics["chess_css_padding_rule_count"], 1)
        self.assertGreater(metrics["outer_whitespace_ratio_max"], 0.1)
        self.assertGreater(metrics["outer_whitespace_ratio_avg"], 0.1)
        self.assertEqual(_evaluate_chess_asset_quality_gate(metrics)["status"], "failed")

    def test_chess_renderer_keeps_tight_non_square_crop_instead_of_white_canvas(self) -> None:
        source = Image.new("RGB", (260, 220), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((50, 30, 190, 170), outline=32, width=3)
        for offset in range(8):
            x = 50 + offset * 17
            draw.line((x, 30, x, 170), fill=120, width=1)
            y = 30 + offset * 17
            draw.line((50, y, 190, y), fill=120, width=1)

        png_data, width, height = render_chess_diagram_to_png(
            _FakePage(source),
            ChessDiagramRegion(page_num=0, bbox=(0, 0, 260, 220), text_span_indices=[]),
            dpi=144,
            optimize=False,
        )

        self.assertNotEqual(width, height)
        with Image.open(io.BytesIO(png_data)) as rendered:
            self.assertEqual(rendered.size, (width, height))

    def test_chess_crop_excludes_neighboring_exercise_numbers(self) -> None:
        source = Image.new("RGB", (520, 560), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((40, 40, 420, 420), outline=32, width=3)
        for offset in range(9):
            x = 40 + offset * 47
            draw.line((x, 40, x, 420), fill=120, width=1)
            y = 40 + offset * 47
            draw.line((40, y, 420, y), fill=120, width=1)
        for idx in range(8):
            draw.rectangle((65 + idx * 44, 432, 75 + idx * 44, 442), fill=32)
        draw.rectangle((218, 468, 244, 506), fill=32)

        cropped = _crop_to_board_region(source)

        self.assertLess(cropped.height, 450)
        bottom_band = cropped.crop((0, max(0, cropped.height - 10), cropped.width, cropped.height)).convert("L")
        dark_pixels = sum(count for value, count in bottom_band.getcolors(maxcolors=256) or [] if value < 96)
        self.assertLess(dark_pixels, 80)

    def test_chess_crop_keeps_file_coordinates_with_bottom_breathing_room(self) -> None:
        source = Image.new("RGB", (520, 560), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((40, 40, 420, 420), outline=32, width=3)
        for offset in range(9):
            x = 40 + offset * 47
            draw.line((x, 40, x, 420), fill=120, width=1)
            y = 40 + offset * 47
            draw.line((40, y, 420, y), fill=120, width=1)
        for idx in range(8):
            draw.rectangle((66 + idx * 44, 432, 78 + idx * 44, 455), fill=32)
        draw.rectangle((218, 486, 244, 524), fill=32)

        cropped = _crop_to_board_region(source).convert("L")
        dark_rows = [
            y
            for y in range(cropped.height)
            if sum(1 for x in range(cropped.width) if cropped.getpixel((x, y)) < 96) >= 8
        ]

        self.assertTrue(dark_rows)
        self.assertLessEqual(max(dark_rows), cropped.height - 7)
        self.assertLess(cropped.height, 470)

    def test_chess_crop_replaces_fragile_right_marker_without_neighboring_board(self) -> None:
        source = Image.new("RGB", (650, 560), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((40, 40, 420, 420), outline=32, width=3)
        for offset in range(9):
            x = 40 + offset * 47
            draw.line((x, 40, x, 420), fill=120, width=1)
            y = 40 + offset * 47
            draw.line((40, y, 420, y), fill=120, width=1)
        draw.line((472, 342, 472, 423), fill=32, width=5)
        draw.rectangle((464, 430, 484, 448), fill=32)
        draw.rectangle((585, 40, 640, 420), outline=32, width=4)

        cropped = _crop_to_board_region(source).convert("L")
        dark_pixels = [
            (x, y)
            for y in range(cropped.height)
            for x in range(cropped.width)
            if cropped.getpixel((x, y)) < 96
        ]
        max_dark_x = max(x for x, _y in dark_pixels)
        full_height_columns = []
        for x in range(cropped.width):
            current_run = 0
            longest_run = 0
            for y in range(cropped.height):
                if cropped.getpixel((x, y)) < 96:
                    current_run += 1
                    longest_run = max(longest_run, current_run)
                else:
                    current_run = 0
            if longest_run > 300:
                full_height_columns.append(x)
        board_right = max(full_height_columns)
        marker_region = cropped.crop((board_right + 5, 0, cropped.width, cropped.height))
        marker_dark_pixels = sum(
            1
            for y in range(marker_region.height)
            for x in range(marker_region.width)
            if marker_region.getpixel((x, y)) < 96
        )
        longest_vertical_run = 0
        for x in range(marker_region.width):
            current_run = 0
            for y in range(marker_region.height):
                if marker_region.getpixel((x, y)) < 96:
                    current_run += 1
                    longest_vertical_run = max(longest_vertical_run, current_run)
                else:
                    current_run = 0

        self.assertGreater(marker_dark_pixels, 30)
        self.assertLess(longest_vertical_run, 24)
        self.assertLessEqual(max_dark_x, cropped.width - 7)
        self.assertLess(cropped.width, 560)

    def test_chess_reader_tone_preserves_pieces_and_softens_hatch_midtones(self) -> None:
        image = Image.new("L", (4, 1), 255)
        image.putpixel((0, 0), 0)
        image.putpixel((1, 0), 119)
        image.putpixel((2, 0), 213)
        image.putpixel((3, 0), 255)

        prepared = _prepare_chess_diagram_for_reader(image)

        self.assertEqual(prepared.getpixel((0, 0)), 0)
        self.assertGreater(prepared.getpixel((1, 0)), 119)
        self.assertGreater(prepared.getpixel((2, 0)), 213)
        self.assertEqual(prepared.getpixel((3, 0)), 255)

    def test_chess_pdf_region_expansion_keeps_room_for_graphical_side_marker(self) -> None:
        region = SimpleNamespace(bbox=(100.0, 100.0, 200.0, 200.0), text_span_indices=[])
        page_rect = SimpleNamespace(x0=0.0, y0=0.0, x1=260.0, y1=260.0)

        expanded_bbox, suppressed = _expand_chess_region_for_auxiliary_labels(region, [], page_rect)

        self.assertEqual(suppressed, set())
        self.assertGreaterEqual(expanded_bbox[2], 220.0)
        self.assertLessEqual(expanded_bbox[2], page_rect.x1)

    def test_semantic_cleanup_enhances_chess_diagrams_as_compact_line_art(self) -> None:
        image = Image.new("L", (90, 120), 255)
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 10, 80, 100), outline=32, width=2)
        draw.line((20, 20, 70, 90), fill=96, width=2)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = f"{temp_dir}/board.png"
            image.save(path, format="PNG")

            result = _maybe_enhance_diagram_asset(
                Path(path),
                min_long_edge=160,
                line_art=True,
                palette_colors=8,
            )

            self.assertTrue(result["enhanced"])
            self.assertEqual(max(result["width"], result["height"]), 160)
            with Image.open(path) as enhanced:
                colors = enhanced.convert("P").getcolors(maxcolors=256)
                self.assertIsNotNone(colors)
                self.assertLessEqual(len(colors or []), 8)


def _css_rule_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}", css, flags=re.DOTALL)
    if match is None:
        raise AssertionError(f"CSS selector not found: {selector}")
    return match.group("body")


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _build_epub_bytes(files: dict[str, str | bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for archive_path, content in files.items():
            compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
            payload = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(archive_path, payload, compress_type=compress_type)
    return output.getvalue()


class _FakePixmap:
    def __init__(self, image: Image.Image) -> None:
        self._image = image

    def tobytes(self, output: str) -> bytes:
        if output != "png":
            raise ValueError(output)
        return _png_bytes(self._image)


class _FakePage:
    def __init__(self, image: Image.Image) -> None:
        import fitz

        self.rect = fitz.Rect(0, 0, image.width, image.height)
        self._image = image

    def get_pixmap(self, **_kwargs) -> _FakePixmap:
        return _FakePixmap(self._image)


if __name__ == "__main__":
    unittest.main()
