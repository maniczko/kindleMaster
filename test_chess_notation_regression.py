from __future__ import annotations

import unittest

from pymupdf_chess_extractor import _normalize_text_for_epub


class ChessNotationRegressionTests(unittest.TestCase):
    def test_woodpecker_rook_figurine_maps_to_rook_not_knight(self) -> None:
        self.assertEqual(
            _normalize_text_for_epub(
                "35...\xa6xf7 36.\xa6a8\u2020 \xa6f8 37.\xa6xf8",
                "SPTimeFig-Roman",
            ),
            "35...Rxf7 36.Ra8+ Rf8 37.Rxf8",
        )

    def test_woodpecker_knight_figurine_maps_to_knight_not_rook(self) -> None:
        self.assertEqual(
            _normalize_text_for_epub(
                "24.\xa4xf7! \xa2xf7",
                "SPAriesFig-Bold",
            ),
            "24.Nxf7! Kxf7",
        )


if __name__ == "__main__":
    unittest.main()
