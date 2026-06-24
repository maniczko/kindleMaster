from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_chess_fen_template_strategy import evaluate_chess_fen_template_strategy


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ChessFenTemplateStrategyTests(unittest.TestCase):
    def test_strategy_blocks_model_replacement_without_hardened_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            eval_path = root / "eval.json"
            inventory_path = root / "inventory.json"
            _write_json(
                eval_path,
                {
                    "case_count": 40,
                    "exact_fen_accuracy": 0.925,
                    "square_accuracy": 1.0,
                    "false_positive_count": 0,
                    "fen_count": 37,
                },
            )
            _write_json(
                inventory_path,
                {
                    "summary": {
                        "total_valid_human_verified_label_count": 0,
                        "profiles_meeting_target": 0,
                        "profiles_missing_target": 3,
                    }
                },
            )

            result = evaluate_chess_fen_template_strategy(
                recognizer_eval_path=eval_path,
                label_inventory_path=inventory_path,
            )

        self.assertEqual(result["recommendation"], "keep_template_matcher_collect_evidence")
        self.assertIn("insufficient_hardened_ground_truth", {blocker["code"] for blocker in result["blockers"]})
        self.assertTrue(result["policy"]["no_model_replacement_without_hardened_ground_truth"])

    def test_strategy_keeps_template_when_eval_is_good_and_labels_are_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            eval_path = root / "eval.json"
            inventory_path = root / "inventory.json"
            readiness_path = root / "readiness.json"
            _write_json(
                eval_path,
                {
                    "case_count": 120,
                    "exact_fen_accuracy": 0.94,
                    "square_accuracy": 0.99,
                    "false_positive_count": 0,
                    "fen_count": 115,
                },
            )
            _write_json(
                inventory_path,
                {
                    "summary": {
                        "total_valid_human_verified_label_count": 120,
                        "profiles_meeting_target": 1,
                        "profiles_missing_target": 0,
                    }
                },
            )
            _write_json(
                readiness_path,
                {
                    "summary": {
                        "placement_machine_accepted_rate": 0.82,
                        "full_machine_accepted_rate": 0.40,
                        "top_blocker_categories": [{"code": "full_fen_validation", "count": 40}],
                    }
                },
            )

            result = evaluate_chess_fen_template_strategy(
                recognizer_eval_path=eval_path,
                readiness_path=readiness_path,
                label_inventory_path=inventory_path,
            )

        self.assertEqual(result["recommendation"], "keep_template_matcher_augment_evidence")
        self.assertEqual(result["blockers"], [])
        self.assertIn("side-to-move/full-FEN metadata", " ".join(result["next_actions"]))

    def test_strategy_recommends_spike_when_square_accuracy_is_low_after_evidence_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            eval_path = root / "eval.json"
            inventory_path = root / "inventory.json"
            _write_json(
                eval_path,
                {
                    "case_count": 120,
                    "exact_fen_accuracy": 0.60,
                    "square_accuracy": 0.82,
                    "false_positive_count": 0,
                    "fen_count": 80,
                },
            )
            _write_json(
                inventory_path,
                {"summary": {"total_valid_human_verified_label_count": 120}},
            )

            result = evaluate_chess_fen_template_strategy(
                recognizer_eval_path=eval_path,
                label_inventory_path=inventory_path,
            )

        self.assertEqual(result["recommendation"], "consider_alternative_recognizer_spike")


if __name__ == "__main__":
    unittest.main()
