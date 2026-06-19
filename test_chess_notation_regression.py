from __future__ import annotations

import unittest

from pymupdf_chess_extractor import _normalize_chess_span_text, _normalize_text_for_epub


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

    def test_span_normalization_preserves_sptimefig_mapping_without_warning(self) -> None:
        segment = {
            "text": "35...\xa6xf7 36.\xa6a8\u2020",
            "font_name": "SPTimeFig-Roman",
        }

        self.assertEqual(_normalize_chess_span_text(segment), "35...Rxf7 36.Ra8+")
        self.assertNotIn("warnings", segment)

    def test_span_normalization_preserves_spariesfig_mapping_without_warning(self) -> None:
        segment = {
            "text": "24.\xa4xf7! \xa2xf7",
            "font_name": "SPAriesFig-Bold",
        }

        self.assertEqual(_normalize_chess_span_text(segment), "24.Nxf7! Kxf7")
        self.assertNotIn("warnings", segment)

    def test_span_normalization_marks_suspicious_custom_encoding(self) -> None:
        segment = {
            "text": "1. \"'t!;>b3\"",
            "font_name": "CustomChess",
        }

        self.assertIn("\"'t!;>b3", _normalize_chess_span_text(segment))
        self.assertIn("unmapped_chess_glyphs", segment["warnings"])


if __name__ == "__main__":
    unittest.main()
