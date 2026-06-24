from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from chess_board_geometry_cv import (
    detect_board_quad_cv,
    opencv_available,
    render_cv_geometry_overlay,
    warp_board_quad_cv,
)


class ChessCvBoardGeometryTests(unittest.TestCase):
    def test_missing_opencv_returns_unavailable_without_crashing(self):
        if opencv_available():
            self.skipTest("OpenCV is installed; unavailable branch is covered on lean toolchains.")
        result = detect_board_quad_cv(_synthetic_board_image())

        self.assertFalse(result.found)
        self.assertIn("opencv_unavailable", result.warnings)

    @unittest.skipUnless(importlib.util.find_spec("cv2") is not None, "OpenCV diagnostic dependency is optional")
    def test_detect_board_quad_cv_finds_synthetic_board(self):
        result = detect_board_quad_cv(_synthetic_board_image())

        self.assertTrue(result.found)
        self.assertIsNotNone(result.quad)
        self.assertGreater(result.confidence, 0.0)

    @unittest.skipUnless(importlib.util.find_spec("cv2") is not None, "OpenCV diagnostic dependency is optional")
    def test_warp_and_overlay_cv_geometry(self):
        image = _synthetic_board_image()
        result = detect_board_quad_cv(image)
        with tempfile.TemporaryDirectory() as tmp:
            overlay = render_cv_geometry_overlay(image, result, Path(tmp) / "overlay.png")
            warped = warp_board_quad_cv(image, result.quad or ())

            self.assertEqual(overlay["status"], "written")
            self.assertTrue((Path(tmp) / "overlay.png").exists())
            self.assertIsNotNone(warped)


def _synthetic_board_image() -> Image.Image:
    image = Image.new("RGB", (220, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 190, 190), outline="black", width=4)
    cell = 20
    for row in range(8):
        for col in range(8):
            fill = (225, 225, 225) if (row + col) % 2 == 0 else (80, 80, 80)
            draw.rectangle((30 + col * cell, 30 + row * cell, 30 + (col + 1) * cell, 30 + (row + 1) * cell), fill=fill)
    return image


if __name__ == "__main__":
    unittest.main()
