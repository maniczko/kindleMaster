import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_chess_side_header_calibration import analyze_side_header_calibration


class ChessSideHeaderCalibrationTest(unittest.TestCase):
    def test_symbol_mapping_requires_support_and_purity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.jsonl"
            report = root / "report.json"
            rows = [
                {"page": 1, "filename": "a.png", "header_symbol_candidates": [{"symbol": "A"}], "ocr_line_items": []},
                {"page": 2, "filename": "b.png", "header_symbol_candidates": [{"symbol": "A"}], "ocr_line_items": []},
                {"page": 3, "filename": "c.png", "header_symbol_candidates": [{"symbol": "A"}], "ocr_line_items": []},
                {"page": 4, "filename": "d.png", "header_symbol_candidates": [{"symbol": "A"}], "ocr_line_items": []},
                {"page": 5, "filename": "e.png", "header_symbol_candidates": [{"symbol": "A"}], "ocr_line_items": []},
            ]
            candidates.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            report.write_text(
                json.dumps(
                    {
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    _record(1, "a.png", "w"),
                                    _record(2, "b.png", "w"),
                                    _record(3, "c.png", "w"),
                                    _record(4, "d.png", "w"),
                                    _record(5, "e.png", "w"),
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            summary = analyze_side_header_calibration(
                candidates,
                report,
                output_json=root / "summary.json",
                output_md=root / "summary.md",
                min_support=5,
                min_purity=0.9,
            )

            self.assertEqual(summary["trusted_mapping_count"], 1)
            self.assertEqual(summary["symbol_calibration"][0]["suggested_mapping"], "w")
            self.assertTrue((root / "summary.md").is_file())

    def test_mixed_symbol_support_stays_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.jsonl"
            report = root / "report.json"
            rows = [
                {"page": 1, "filename": "a.png", "header_symbol_candidates": [{"symbol": "A"}]},
                {"page": 2, "filename": "b.png", "header_symbol_candidates": [{"symbol": "A"}]},
                {"page": 3, "filename": "c.png", "header_symbol_candidates": [{"symbol": "A"}]},
                {"page": 4, "filename": "d.png", "header_symbol_candidates": [{"symbol": "A"}]},
            ]
            candidates.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            report.write_text(
                json.dumps(
                    {
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    _record(1, "a.png", "w"),
                                    _record(2, "b.png", "w"),
                                    _record(3, "c.png", "b"),
                                    _record(4, "d.png", "b"),
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            summary = analyze_side_header_calibration(
                candidates,
                report,
                output_json=root / "summary.json",
                min_support=2,
                min_purity=0.9,
            )

            self.assertEqual(summary["trusted_mapping_count"], 0)
            self.assertEqual(summary["symbol_calibration"][0]["recommendation"], "evidence_only_needs_more_calibration")


def _record(page: int, filename: str, side: str) -> dict[str, object]:
    return {
        "page": page,
        "filename": filename,
        "requires_review": False,
        "fen": f"8/8/8/8/8/8/8/4K2k {side} - - 0 1",
        "side_to_move": side,
        "side_to_move_status": "explicit",
        "side_to_move_evidence": "marker",
    }


if __name__ == "__main__":
    unittest.main()
