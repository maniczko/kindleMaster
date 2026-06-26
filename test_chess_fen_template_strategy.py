from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from scripts.evaluate_chess_fen_alternative_benchmarks import (
    evaluate_chess_fen_alternative_benchmarks,
    main as alternative_benchmark_main,
)
from scripts.evaluate_chess_fen_template_strategy import evaluate_chess_fen_template_strategy


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_board_crop(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (96, 96), 255)
    image.save(path)


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

    def test_alternative_benchmark_reports_bucket_gaps_and_review_only_strategies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels_dir = root / "labels"
            template_dir = root / "templates"
            template_dir.mkdir(parents=True)
            crop = root / "crop.png"
            _write_board_crop(crop)
            labels = [
                {
                    "id": "simple_false_positive_crop",
                    "crop_path": str(crop),
                    "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "verified_by": "codex-manual-grid-review",
                    "notes": "Rejected false positive crop.",
                },
                {
                    "id": "medium_scan",
                    "crop_path": str(crop),
                    "fen": "4k3/8/2n2n2/3PP3/4K3/5N2/8/8 w - - 0 1",
                    "verified_by": "codex-manual-grid-review",
                    "notes": "Scan benchmark sample.",
                },
                {
                    "id": "hard_scan",
                    "crop_path": str(crop),
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1",
                    "verified_by": "codex-manual-grid-review",
                    "notes": "Hard scan benchmark sample.",
                },
            ]
            (labels_dir / "sample.jsonl").parent.mkdir(parents=True)
            (labels_dir / "sample.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in labels),
                encoding="utf-8",
            )

            payload = evaluate_chess_fen_alternative_benchmarks(
                labels_dir=labels_dir,
                template_dir=template_dir,
                out_dir=root / "out",
                report_path=root / "report.json",
                max_cases=3,
            )

            self.assertEqual(payload["status"], "completed_with_gaps")
            self.assertTrue(payload["policy"]["report_only"])
            self.assertFalse(payload["policy"]["runtime_strict_acceptance_changed"])
            self.assertEqual(payload["policy"]["accepted_fen_changed"], 0)
            buckets = payload["benchmark_manifest"]["buckets"]
            self.assertEqual(buckets["simple_diagrams"]["available_count"], 1)
            self.assertEqual(buckets["false_positives"]["available_count"], 1)
            self.assertGreaterEqual(buckets["cropped_boards"]["available_count"], 1)
            self.assertGreater(buckets["simple_diagrams"]["missing_count"], 0)
            self.assertEqual(len(payload["strategies"]), 2)
            self.assertIn("exact_placement_rate", payload["metrics"])
            self.assertIn("exact_full_fen_rate", payload["metrics"])
            self.assertIn("false_positive_rate", payload["metrics"])
            self.assertIn("review_rate", payload["metrics"])

    def test_alternative_benchmark_cli_writes_insufficient_manifest_for_missing_crops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels_dir = root / "labels"
            labels_dir.mkdir()
            (labels_dir / "missing.jsonl").write_text(
                json.dumps(
                    {
                        "id": "missing_crop",
                        "crop_path": str(root / "missing.png"),
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = root / "report.json"

            exit_code = alternative_benchmark_main(
                [
                    "--labels-dir",
                    str(labels_dir),
                    "--template-dir",
                    str(root / "templates"),
                    "--out-dir",
                    str(root / "out"),
                    "--report",
                    str(report),
                ]
            )

            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "insufficient_inputs")
            self.assertEqual(payload["benchmark_manifest"]["total_available_records"], 0)
            self.assertEqual(payload["policy"]["accepted_fen_changed"], 0)


if __name__ == "__main__":
    unittest.main()
