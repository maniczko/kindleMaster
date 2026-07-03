from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_learning_labels import build_chess_learning_benchmark, validate_chess_learning_labels
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

    def test_chess_learning_benchmark_accepts_human_labels_and_keeps_fen_gate_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels_dir = root / "reference_inputs" / "chess_fen" / "labels"
            board_hash = "sha256:" + "a" * 64
            marker_hash = "sha256:" + "b" * 64
            base = {
                "diagram_id": "p010_d03",
                "page": 10,
                "board_crop_hash": board_hash,
                "marker_crop_hash": marker_hash,
                "reviewer": "qa",
                "created_at": "2026-07-03T12:00:00Z",
                "confidence": "human_verified",
                "verification_source": "human_visual",
                "human_verified": True,
            }
            _write_jsonl(
                labels_dir / "chess_learning_labels.jsonl",
                [
                    {**base, "label_type": "board_crop", "label_value": "correct"},
                    {**base, "label_type": "side_marker_crop", "label_value": "correct"},
                    {**base, "label_type": "side_marker", "label_value": "black"},
                    {**base, "label_type": "fen", "label_value": "correct", "marker_crop_hash": ""},
                    {**base, "label_type": "pgn", "label_value": "unavailable", "marker_crop_hash": ""},
                    {**base, "label_type": "diagram_text_link", "label_value": "correct", "marker_crop_hash": ""},
                    {
                        **base,
                        "diagram_id": "ai-only",
                        "label_type": "side_marker",
                        "label_value": "white",
                        "verification_source": "openai",
                    },
                ],
            )

            payload = build_chess_learning_benchmark(
                labels_dir=labels_dir,
                repo_root=root,
                min_per_type=1,
                report_path=root / "reports" / "ml" / "datasets" / "chess_learning_benchmark_report.json",
            )

        self.assertEqual(payload["status"], "READY_FOR_BENCHMARK")
        self.assertEqual(payload["summary"]["usable_label_count"], 6)
        self.assertEqual(payload["summary"]["rejected_label_count"], 1)
        self.assertEqual(payload["rejected_labels"][0]["codes"], ["ai_only_label_cannot_be_human_verified"])
        self.assertFalse(payload["full_fen_gate"]["labels_bypass_full_fen_gate"])
        self.assertTrue(all(row["accepted_for_runtime"] is False for row in payload["usable_labels"]))
        self.assertTrue(all(row["bypasses_full_fen_gate"] is False for row in payload["usable_labels"]))

    def test_chess_learning_validator_rejects_ai_only_human_verified_label(self) -> None:
        usable, rejected = validate_chess_learning_labels(
            [
                {
                    "diagram_id": "p001_d01",
                    "label_type": "side_marker",
                    "label_value": "white",
                    "board_crop_hash": "sha256:" + "a" * 64,
                    "marker_crop_hash": "sha256:" + "b" * 64,
                    "reviewer": "qa",
                    "created_at": "2026-07-03T12:00:00Z",
                    "human_verified": True,
                    "verification_source": "ai_review",
                }
            ]
        )

        self.assertEqual(usable, [])
        self.assertIn("ai_only_label_cannot_be_human_verified", rejected[0]["codes"])


if __name__ == "__main__":
    unittest.main()
