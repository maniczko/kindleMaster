from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_engine_hints import build_engine_study_hint_artifacts, build_engine_study_hints


class ChessEngineHintsTests(unittest.TestCase):
    def test_engine_ok_builds_progressive_hints_without_spoiling_best_move(self) -> None:
        payload = build_engine_study_hints(
            {
                "items": [
                    {
                        "diagram_id": "p001_d001",
                        "page": 1,
                        "fen": "4k3/8/8/8/8/8/4K3/R7 w - - 0 1",
                        "engine_status": "ok",
                        "best_move_uci": "a1a8",
                        "best_move_san": "Ra8+",
                        "score_cp": 120,
                        "mate": None,
                        "pv": [{"moves_san": ["Ra8+", "Ke7"], "moves_uci": ["a1a8", "e8e7"]}],
                    }
                ]
            }
        )

        item = payload["items"][0]
        self.assertEqual(item["hint_status"], "available")
        self.assertEqual(item["source"], "engine_rule_based_v1")
        self.assertTrue(item["full_reveal_available"])
        self.assertTrue(item["move_features"]["is_check"])
        self.assertIn("forcing", item["hint_level_1"])
        self.assertIn("check", item["hint_level_2"])
        self.assertNotIn("Ra8+", item["hint_level_1"])
        self.assertNotIn("Ra8+", item["hint_level_2"])
        self.assertEqual(item["best_move_san"], "Ra8+")

    def test_engine_unavailable_stays_unavailable(self) -> None:
        payload = build_engine_study_hints(
            {
                "items": [
                    {
                        "diagram_id": "p001_d002",
                        "engine_status": "engine_unavailable",
                        "skip_reason": "engine_unavailable",
                    }
                ]
            }
        )

        item = payload["items"][0]
        self.assertEqual(item["hint_status"], "unavailable")
        self.assertFalse(item["full_reveal_available"])
        self.assertEqual(item["unavailable_reason"], "engine_unavailable")
        self.assertEqual(payload["summary"]["unavailable_count"], 1)

    def test_invalid_engine_move_does_not_create_hint(self) -> None:
        payload = build_engine_study_hints(
            {
                "items": [
                    {
                        "diagram_id": "p001_d003",
                        "fen": "4k3/8/8/8/8/8/4K3/4K3 w - - 0 1",
                        "engine_status": "ok",
                        "best_move_uci": "a1a8",
                        "best_move_san": "Ra8+",
                    }
                ]
            }
        )

        item = payload["items"][0]
        self.assertEqual(item["hint_status"], "unavailable")
        self.assertEqual(item["unavailable_reason"], "invalid_engine_move")

    def test_artifacts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            result = build_engine_study_hint_artifacts(
                out,
                {
                    "items": [
                        {
                            "diagram_id": "p001_d001",
                            "fen": "4k3/8/8/8/8/8/4K3/R7 w - - 0 1",
                            "engine_status": "ok",
                            "best_move_uci": "a1a8",
                            "best_move_san": "Ra8+",
                            "score_cp": 120,
                            "pv": [],
                        }
                    ]
                },
            )

            self.assertTrue((out / "reports" / "chess_engine" / "engine_hints.json").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "engine_hints.md").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "engine_hints.html").is_file())
            self.assertTrue((out / "data" / "engine_hints.json").is_file())
            data = json.loads((out / "data" / "engine_hints.json").read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "kindlemaster.chess_engine.study_hints_data.v1")
            self.assertIn("engine_hints_data", result["paths"])


if __name__ == "__main__":
    unittest.main()
