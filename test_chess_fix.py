from __future__ import annotations

import base64
import io
import unittest
import zipfile

from PIL import Image, ImageDraw

from converter import ConversionConfig, build_epub
from pymupdf_chess_extractor import _optimize_chess_diagram_export


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

    @staticmethod
    def _tiny_jpeg_bytes(*, size: tuple[int, int]) -> bytes:
        image = Image.new("RGB", size, (240, 240, 240))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=84, optimize=True)
        return output.getvalue()


if __name__ == "__main__":
    unittest.main()
