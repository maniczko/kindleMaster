from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_fen_hardening import crop_sha256
from scripts.evaluate_chess_fen_label_inventory import evaluate_chess_fen_label_inventory
from scripts.validate_chess_fen_audit_dataset import validate_chess_fen_audit_dataset


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class ChessFenDatasetToolsTests(unittest.TestCase):
    def test_label_inventory_reports_missing_verified_labels_to_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            labels = root / "labels" / "sample_seed_positions.jsonl"
            _write_jsonl(
                labels,
                [
                    {
                        "id": "sample-1",
                        "crop_path": str(crop),
                        "crop_sha256": crop_sha256(crop),
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "verified_by": "tester",
                        "verified_at": "2026-06-24",
                        "label_status": "verified",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
                    }
                ],
            )

            result = evaluate_chess_fen_label_inventory(root / "labels", target_per_profile=3)

        self.assertEqual(result["summary"]["profile_count"], 1)
        self.assertEqual(result["summary"]["total_valid_human_verified_label_count"], 1)
        self.assertEqual(result["profiles"][0]["missing_to_target"], 2)
        self.assertFalse(result["profiles"][0]["ready_for_expanded_profile"])

    def test_audit_dataset_validator_accepts_valid_diagnostic_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            dataset = root / "audit.jsonl"
            _write_jsonl(
                dataset,
                [
                    {
                        "id": "cropped-1",
                        "sample_type": "cropped_board",
                        "crop_path": str(crop),
                        "expected_rejection_reason": "partial_board_crop_without_dense_board_evidence",
                        "expected_placement": "4k3/8/8/8/8/8/8/4K3",
                    },
                    {
                        "id": "negative-1",
                        "sample_type": "negative_non_board",
                        "crop_path": str(crop),
                        "expected_rejection_reason": "board_grid_not_detected",
                        "expected_placement": "",
                    },
                ],
            )

            result = validate_chess_fen_audit_dataset(dataset)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["summary"]["by_sample_type"]["cropped_board"], 1)

    def test_audit_dataset_validator_rejects_bad_negative_and_missing_crop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = root / "audit.jsonl"
            _write_jsonl(
                dataset,
                [
                    {
                        "id": "bad-negative",
                        "sample_type": "negative_non_board",
                        "crop_path": str(root / "missing.png"),
                        "expected_rejection_reason": "partial_board_crop_without_dense_board_evidence",
                        "expected_placement": "4k3/8/8/8/8/8/8/4K3",
                    }
                ],
            )

            result = validate_chess_fen_audit_dataset(dataset)

        codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("crop_path_missing_on_disk", codes)
        self.assertIn("expected_rejection_reason_mismatch", codes)
        self.assertIn("negative_sample_must_not_have_expected_placement", codes)


if __name__ == "__main__":
    unittest.main()
