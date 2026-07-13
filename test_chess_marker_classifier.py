import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from chess_marker_crop_classifier import evaluate_marker_crop_corpus
from pymupdf_chess_extractor import classify_scan_chess_side_marker_crop


class ChessMarkerClassifierTests(unittest.TestCase):
    def test_outline_triangle_maps_to_white_with_adaptive_reason(self) -> None:
        crop = Image.new("L", (72, 72), "white")
        draw = ImageDraw.Draw(crop)
        draw.line([(36, 6), (6, 66), (66, 66), (36, 6)], fill="black", width=3, joint="curve")

        result = classify_scan_chess_side_marker_crop(crop)

        self.assertEqual(result["classifier_version"], "marker_adaptive_v3")
        self.assertEqual(result["status"], "trusted_marker")
        self.assertEqual(result["side_to_move"], "w")
        self.assertEqual(result["side"], "w")
        self.assertEqual(result["symbol"], "\u25b3")
        self.assertEqual(result["reason"], "adaptive_outline_upright_triangle")
        self.assertGreaterEqual(result["confidence"], 0.90)

    def test_filled_inverted_triangle_maps_to_black_with_adaptive_reason(self) -> None:
        crop = Image.new("L", (72, 72), "white")
        draw = ImageDraw.Draw(crop)
        draw.polygon([(6, 6), (66, 6), (36, 66)], fill="black")

        result = classify_scan_chess_side_marker_crop(crop)

        self.assertEqual(result["classifier_version"], "marker_adaptive_v3")
        self.assertEqual(result["status"], "trusted_marker")
        self.assertEqual(result["side_to_move"], "b")
        self.assertEqual(result["side"], "b")
        self.assertEqual(result["symbol"], "\u25bc")
        self.assertEqual(result["reason"], "adaptive_filled_inverted_triangle")
        self.assertGreaterEqual(result["confidence"], 0.90)

    def test_non_marker_crops_are_not_trusted(self) -> None:
        edge_strip = Image.new("L", (24, 90), "white")
        ImageDraw.Draw(edge_strip).line([(3, 0), (3, 89)], fill="black", width=4)
        rank_number = Image.new("L", (48, 48), "white")
        ImageDraw.Draw(rank_number).text((12, 10), "8", fill="black")
        small_cut = Image.new("L", (26, 26), "white")
        ImageDraw.Draw(small_cut).line([(13, 3), (5, 22), (21, 22), (13, 3)], fill="black", width=1)

        for crop in (edge_strip, rank_number, small_cut):
            with self.subTest(crop=crop.size):
                result = classify_scan_chess_side_marker_crop(crop)
                self.assertNotEqual(result["status"], "trusted_marker")
                self.assertEqual(result["side_to_move"], "unknown")

    def test_multiple_markers_are_not_trusted(self) -> None:
        crop = Image.new("L", (92, 56), "white")
        draw = ImageDraw.Draw(crop)
        draw.line([(24, 8), (7, 44), (41, 44), (24, 8)], fill="black", width=3, joint="curve")
        draw.polygon([(45, 8), (79, 8), (62, 44)], fill="black")

        result = classify_scan_chess_side_marker_crop(crop)

        self.assertNotEqual(result["status"], "trusted_marker")
        self.assertEqual(result["reason"], "multiple_candidates")
        self.assertEqual(result["side_to_move"], "unknown")

    def test_corpus_report_meets_issue_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = evaluate_marker_crop_corpus(
                "reference_inputs/chess_fen/marker_crops",
                report_path=Path(tmp) / "marker_crop_classifier_report.json",
            )

        summary = report["summary"]
        by_class = report["by_class"]
        self.assertEqual(summary["classifier_version"], "marker_adaptive_v3")
        self.assertEqual(summary["decision"], "pass")
        self.assertGreaterEqual(summary["white_outline_triangle_accuracy"], 0.90)
        self.assertGreaterEqual(summary["black_filled_triangle_accuracy"], 0.90)
        self.assertEqual(summary["negative_false_trusted_count"], 0)
        self.assertEqual(by_class["bad_crop"]["false_trusted"], 0)
        self.assertEqual(by_class["multiple"]["false_trusted"], 0)
        self.assertEqual(by_class["unclear"]["false_trusted"], 0)


if __name__ == "__main__":
    unittest.main()
