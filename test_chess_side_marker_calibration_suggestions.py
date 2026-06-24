from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.suggest_chess_side_marker_calibration import suggest_side_marker_calibration


class ChessSideMarkerCalibrationSuggestionsTests(unittest.TestCase):
    def test_adds_evidence_only_suggestion_without_human_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "case-1",
                        "human_side_to_move": "",
                        "human_verified": False,
                        "side_marker_candidates": [
                            {"role": "top_right", "detected_side": "b", "score": 1200.0},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            output = root / "suggested.jsonl"
            summary = suggest_side_marker_calibration(draft, output_jsonl=output)
            row = json.loads(output.read_text(encoding="utf-8").strip())

            self.assertEqual(summary["row_count"], 1)
            self.assertTrue(Path(summary["review_sheet_path"]).is_file())
            self.assertEqual(row["ai_suggested_side_to_move"], "b")
            self.assertEqual(row["ai_suggested_marker_role"], "top_right")
            self.assertFalse(row["human_verified"])
            self.assertEqual(row["human_side_to_move"], "")
            self.assertTrue(row["ai_needs_human_review"])

    def test_ambiguous_conflict_does_not_guess_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "case-2",
                        "human_verified": False,
                        "side_marker_candidates": [
                            {"role": "top_right", "detected_side": "w", "score": 900.0},
                            {"role": "bottom_right", "detected_side": "b", "score": 800.0},
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            output = root / "suggested.jsonl"
            suggest_side_marker_calibration(draft, output_jsonl=output)
            row = json.loads(output.read_text(encoding="utf-8").strip())

            self.assertEqual(row["ai_suggestion_status"], "ambiguous")
            self.assertEqual(row["ai_suggested_side_to_move"], "")
            self.assertFalse(row["human_verified"])


if __name__ == "__main__":
    unittest.main()
