from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

import fitz

from scanned_chess_detector import detect_scanned_chess_boards


class ScannedChessDetectorTests(unittest.TestCase):
    def test_detects_synthetic_8x8_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "grid.pdf"
            doc = fitz.open()
            page = doc.new_page(width=420, height=560)
            rect = fitz.Rect(90, 120, 330, 360)
            cell = rect.width / 8
            for row in range(8):
                for col in range(8):
                    if (row + col) % 2:
                        square = fitz.Rect(
                            rect.x0 + col * cell,
                            rect.y0 + row * cell,
                            rect.x0 + (col + 1) * cell,
                            rect.y0 + (row + 1) * cell,
                        )
                        page.draw_rect(square, color=(0.72, 0.72, 0.72), fill=(0.72, 0.72, 0.72), width=0)
            for index in range(9):
                x = rect.x0 + index * cell
                y = rect.y0 + index * cell
                page.draw_line(fitz.Point(x, rect.y0), fitz.Point(x, rect.y1), color=(0, 0, 0), width=1.4)
                page.draw_line(fitz.Point(rect.x0, y), fitz.Point(rect.x1, y), color=(0, 0, 0), width=1.4)
            doc.save(pdf_path)
            doc.close()

            boards = detect_scanned_chess_boards(str(pdf_path), render_dpi=100)

        self.assertGreaterEqual(len(boards), 1)
        self.assertEqual(boards[0].source_type, "scanned_board")
        self.assertGreater(boards[0].confidence, 0.26)
        self.assertTrue(boards[0].image_data)

    def test_plain_text_page_does_not_create_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "plain.pdf"
            doc = fitz.open()
            page = doc.new_page(width=420, height=560)
            for index in range(20):
                page.insert_text((60, 80 + index * 18), "This is a plain scanned-like text line.", fontsize=11)
            doc.save(pdf_path)
            doc.close()

            boards = detect_scanned_chess_boards(str(pdf_path), render_dpi=100)

        self.assertEqual(boards, [])


if __name__ == "__main__":
    unittest.main()
