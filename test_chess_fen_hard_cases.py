from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_chess_fen_hard_cases import (
    MISSING_ARTIFACT,
    build_chess_fen_hard_cases,
    write_markdown,
)


class ChessFenHardCasesTests(unittest.TestCase):
    def test_ai_unreadable_enters_hard_cases(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "page": 1,
                    "filename": "d1.png",
                    "ai_category": "ai_unreadable",
                    "warnings": ["board_not_detected"],
                }
            ]
        )

        self.assertEqual(payload["summary"]["hard_case_count"], 1)
        record = payload["records"][0]
        self.assertEqual(record["ai_category"], "ai_unreadable")
        self.assertEqual(record["hard_case_type"], "grid_failed")
        self.assertTrue(record["requires_manual_label"])
        self.assertTrue(record["requires_crop_repair"])
        self.assertTrue(record["requires_source_image_review"])
        self.assertEqual(record["recommended_action"], "inspect_crop_grid_geometry")
        self.assertEqual(record["normal_metrics_segment"], "excluded_hard_case")

    def test_ai_best_effort_enters_hard_cases(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "page": 2,
                    "crop_path": "reference_inputs/chess_fen/crops/d1.png",
                    "ai_category": "ai_best_effort",
                }
            ]
        )

        record = payload["records"][0]
        self.assertEqual(record["ai_category"], "ai_best_effort")
        self.assertEqual(record["hard_case_type"], "best_effort")
        self.assertEqual(record["recommended_action"], "manual_label_required")

    def test_ai_consensus_and_tiebreak_are_excluded(self) -> None:
        payload = self._build(
            [
                {"diagram_id": "c", "ai_category": "ai_consensus", "crop_path": "c.png"},
                {"diagram_id": "t", "ai_category": "ai_tie_break_resolved", "crop_path": "t.png"},
                {"diagram_id": "u", "ai_category": "ai_unreadable", "crop_path": "u.png"},
            ]
        )

        self.assertEqual(payload["summary"]["hard_case_count"], 1)
        self.assertEqual(payload["summary"]["skipped"]["ai_consensus"], 1)
        self.assertEqual(payload["summary"]["skipped"]["ai_tie_break_resolved"], 1)

    def test_missing_crop_path_creates_missing_artifact(self) -> None:
        payload = self._build([{"diagram_id": "d1", "ai_category": "ai_unreadable"}])

        record = payload["records"][0]
        self.assertEqual(record["crop_path"], MISSING_ARTIFACT)
        self.assertEqual(record["status"], MISSING_ARTIFACT)
        self.assertEqual(record["code"], "crop_path_missing")
        self.assertEqual(record["hard_case_type"], "crop_missing")
        self.assertEqual(record["recommended_action"], "repair_or_regenerate_crop")

    def test_known_eighteen_hard_cases_are_counted_by_category(self) -> None:
        records = [
            {
                "diagram_id": f"u{index:03d}",
                "page": index,
                "filename": f"u{index:03d}.png",
                "ai_category": "ai_unreadable",
            }
            for index in range(17)
        ]
        records.append({"diagram_id": "best", "page": 99, "filename": "best.png", "ai_category": "ai_best_effort"})
        records.append({"diagram_id": "consensus", "ai_category": "ai_consensus", "crop_path": "consensus.png"})

        payload = self._build(records)

        self.assertEqual(payload["summary"]["hard_case_count"], 18)
        self.assertEqual(payload["summary"]["by_ai_category"], {"ai_best_effort": 1, "ai_unreadable": 17})
        self.assertEqual(payload["summary"]["skipped"]["ai_consensus"], 1)
        self.assertTrue(payload["summary"]["excluded_from_normal_metrics"])

    def test_current_report_blockers_can_classify_ambiguous_piece(self) -> None:
        payload = self._build(
            [{"diagram_id": "d1", "ai_category": "ai_unreadable", "crop_path": "d1.png"}],
            current_records=[{"diagram_id": "d1", "primary_blocker": "queen_color_ambiguous_suppressed"}],
        )

        record = payload["records"][0]
        self.assertEqual(record["primary_blocker"], "queen_color_ambiguous_suppressed")
        self.assertEqual(record["primary_category"], "ambiguous_piece")
        self.assertEqual(record["hard_case_type"], "ambiguous_piece")
        self.assertEqual(record["recommended_action"], "manual_square_label_review")

    def test_markdown_contains_summary_and_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_md = root / "hard_cases.md"
            payload = self._build([{"diagram_id": "d1", "ai_category": "ai_best_effort", "crop_path": "d1.png"}])

            write_markdown(payload, output_md)
            text = output_md.read_text(encoding="utf-8")

        self.assertIn("Chess FEN Hard Cases", text)
        self.assertIn("Excluded from normal recognizer metrics", text)
        self.assertIn("manual_label_required", text)

    def _build(self, ai_records: list[dict[str, object]], current_records: list[dict[str, object]] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_report = root / "ai.json"
            current_report = root / "current.json"
            ai_report.write_text(json.dumps({"records": ai_records}), encoding="utf-8")
            current_report.write_text(json.dumps({"records": current_records or []}), encoding="utf-8")
            return build_chess_fen_hard_cases(ai_report, current_report)


if __name__ == "__main__":
    unittest.main()
