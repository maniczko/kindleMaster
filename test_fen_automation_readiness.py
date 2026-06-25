from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_fen_automation_readiness import evaluate_fen_automation_readiness


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class FenAutomationReadinessTests(unittest.TestCase):
    def test_evaluator_counts_full_and_placement_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root / "fen" / "fen_candidates.json",
                {
                    "items": [
                        {
                            "id": "full",
                            "status": "FEN_MACHINE_ACCEPTED",
                            "runtime_status": "FEN_MACHINE_ACCEPTED",
                            "placement_runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                        },
                        {
                            "id": "placement",
                            "status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                            "runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                            "placement_runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                            "acceptance_blockers": [
                                {"code": "full_fen_metadata_not_accepted", "category": "full_fen_validation"}
                            ],
                        },
                        {
                            "id": "failed",
                            "status": "FEN_FAILED",
                            "runtime_status": "FEN_FAILED",
                            "placement_runtime_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
                            "acceptance_blockers": [{"code": "board_grid_not_detected", "category": "crop_grid"}],
                        },
                    ]
                },
            )
            _write_json(
                root / "report" / "acceptance_blockers.json",
                {
                    "summary": {
                        "by_code": {"board_grid_not_detected": 1, "full_fen_metadata_not_accepted": 1},
                        "by_category": {"crop_grid": 1, "full_fen_validation": 1},
                    }
                },
            )

            payload = evaluate_fen_automation_readiness(root, output_path=root / "reports" / "readiness.json")

        self.assertEqual(payload["schema"], "kindlemaster.fen_automation_readiness.v1")
        self.assertEqual(payload["status"], "ready_for_p0_review")
        self.assertEqual(payload["summary"]["total_fen_items"], 3)
        self.assertEqual(payload["summary"]["full_machine_accepted_count"], 1)
        self.assertEqual(payload["summary"]["placement_machine_accepted_count"], 2)
        self.assertEqual(payload["summary"]["placement_machine_accepted_rate"], 0.6667)
        self.assertEqual(payload["summary"]["failed_count"], 1)
        self.assertEqual(payload["summary"]["top_blocker_categories"][0]["code"], "crop_grid")

    def test_evaluator_handles_missing_artifacts_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = evaluate_fen_automation_readiness(Path(temp_dir) / "missing")

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["summary"]["total_fen_items"], 0)
        self.assertIn("fen_candidates", payload["input_paths"]["missing"])

    def test_evaluator_derives_missing_blocker_categories_from_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _write_json(
                root / "fen" / "fen_candidates.json",
                {
                    "items": [
                        {
                            "id": "failed",
                            "status": "FEN_FAILED",
                            "runtime_status": "FEN_FAILED",
                            "placement_runtime_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
                            "acceptance_blockers": [{"code": "piece_template_confidence_below_threshold"}],
                        }
                    ]
                },
            )
            _write_json(root / "report" / "acceptance_blockers.json", {"summary": {"by_code": {"piece_template_confidence_below_threshold": 1}}})

            payload = evaluate_fen_automation_readiness(root)

        self.assertEqual(payload["summary"]["top_blocker_categories"][0]["code"], "recognition")


if __name__ == "__main__":
    unittest.main()
