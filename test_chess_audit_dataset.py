from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_chess_audit_dataset import validate_chess_audit_dataset


class ChessAuditDatasetValidationTests(unittest.TestCase):
    def test_empty_scaffold_dataset_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _write_dataset_scaffold(Path(tmp))

            result = validate_chess_audit_dataset(manifest)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["counts"]["fen_rows"], 0)
        self.assertEqual(result["counts"]["pgn_rows"], 0)
        self.assertEqual(result["counts"]["negative_rows"], 0)
        self.assertFalse(result["release_readiness"]["accepted_for_release_proof"])
        self.assertEqual(result["release_readiness"]["status"], "review_required")
        self.assertIn(
            "pgn_ground_truth_missing",
            {blocker["code"] for blocker in result["release_readiness"]["blockers"]},
        )
        self.assertIn(
            "negative_samples_missing",
            {blocker["code"] for blocker in result["release_readiness"]["blockers"]},
        )

    def test_fen_row_requires_human_verified_placement_and_existing_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_scaffold(root)
            (root / "labels" / "fen_ground_truth.jsonl").write_text(
                json.dumps(
                    {
                        "id": "fen-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "crop_path": "crops/missing.png",
                        "expected_placement": "",
                        "side_to_move_source": "caption",
                        "crop_expected_bbox": [0, 0, 10, 10],
                        "crop_has_caption": False,
                        "crop_has_coordinates": False,
                        "human_verified": False,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-20",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_audit_dataset(manifest)

        codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("expected_placement_missing", codes)
        self.assertIn("human_verified_missing", codes)
        self.assertIn("crop_path_missing_on_disk", codes)

    def test_valid_fen_and_diagram_only_pgn_rows_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_scaffold(root)
            crop = root / "crops" / "board.png"
            crop.write_bytes(b"not-a-real-image-for-schema-test")
            (root / "labels" / "fen_ground_truth.jsonl").write_text(
                json.dumps(
                    {
                        "id": "fen-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "crop_path": "crops/board.png",
                        "expected_placement": "8/8/8/8/8/8/4K3/4k3",
                        "expected_full_fen": "",
                        "side_to_move_source": "unknown",
                        "crop_expected_bbox": [0, 0, 10, 10],
                        "crop_has_caption": False,
                        "crop_has_coordinates": False,
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-20",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "labels" / "pgn_ground_truth.jsonl").write_text(
                json.dumps(
                    {
                        "id": "pgn-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "input_type": "diagram_only",
                        "pgn_feasible": False,
                        "pgn_feasibility_reason": "diagram_only_no_movetext",
                        "expected_movetext": "",
                        "expected_pgn": "",
                        "linked_fen_id": "fen-1",
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-20",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_audit_dataset(manifest)

        self.assertEqual(result["status"], "passed")

    def test_diagram_only_pgn_cannot_be_feasible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_scaffold(root)
            (root / "labels" / "pgn_ground_truth.jsonl").write_text(
                json.dumps(
                    {
                        "id": "pgn-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "input_type": "diagram_only",
                        "pgn_feasible": True,
                        "pgn_feasibility_reason": "bad",
                        "human_verified": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_audit_dataset(manifest)

        codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("diagram_only_must_be_infeasible", codes)

    def test_negative_row_requires_human_verified_existing_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_scaffold(root)
            (root / "labels" / "negative_samples.jsonl").write_text(
                json.dumps(
                    {
                        "id": "negative-1",
                        "source_pdf": "book.pdf",
                        "page": 2,
                        "reason": "not_chess_diagram",
                        "crop_path": "crops/missing.png",
                        "human_verified": False,
                        "verified_by": "",
                        "verified_at": "not-a-date",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_audit_dataset(manifest)

        codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("human_verified_missing", codes)
        self.assertIn("verified_by_missing", codes)
        self.assertIn("verified_at_invalid", codes)
        self.assertIn("crop_path_missing_on_disk", codes)

    def test_valid_negative_row_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_scaffold(root)
            crop = root / "crops" / "not_chess.png"
            crop.write_bytes(b"not-a-real-image-for-schema-test")
            (root / "labels" / "negative_samples.jsonl").write_text(
                json.dumps(
                    {
                        "id": "negative-1",
                        "source_pdf": "book.pdf",
                        "page": 2,
                        "reason": "not_chess_diagram",
                        "crop_path": "crops/not_chess.png",
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-20",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_audit_dataset(manifest)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["counts"]["negative_rows"], 1)

    def test_release_readiness_passes_only_with_minimum_human_verified_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = _write_dataset_scaffold(root)
            _write_release_ready_rows(root)

            result = validate_chess_audit_dataset(manifest)

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["counts"]["fen_rows"], 20)
        self.assertEqual(result["counts"]["pgn_rows"], 1)
        self.assertEqual(result["counts"]["negative_rows"], 1)
        self.assertTrue(result["release_readiness"]["accepted_for_release_proof"])
        self.assertEqual(result["release_readiness"]["status"], "ready")
        self.assertEqual(result["release_readiness"]["blockers"], [])


def _write_dataset_scaffold(root: Path) -> Path:
    (root / "labels").mkdir(parents=True)
    (root / "crops").mkdir()
    (root / "overlays").mkdir()
    for name in ("fen_ground_truth.jsonl", "pgn_ground_truth.jsonl", "negative_samples.jsonl"):
        (root / "labels" / name).write_text("", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "kindlemaster.chess_audit_dataset.v1",
                "fen_ground_truth": "labels/fen_ground_truth.jsonl",
                "pgn_ground_truth": "labels/pgn_ground_truth.jsonl",
                "negative_samples": "labels/negative_samples.jsonl",
                "crops_dir": "crops",
                "overlays_dir": "overlays",
            }
        ),
        encoding="utf-8",
    )
    return manifest


def _write_release_ready_rows(root: Path) -> None:
    fen_rows = []
    for index in range(20):
        crop = root / "crops" / f"board_{index}.png"
        crop.write_bytes(b"not-a-real-image-for-schema-test")
        fen_rows.append(
            {
                "id": f"fen-{index}",
                "source_pdf": "book.pdf",
                "page": index + 1,
                "crop_path": f"crops/board_{index}.png",
                "expected_placement": "8/8/8/8/8/8/4K3/4k3",
                "side_to_move_source": "unknown",
                "crop_expected_bbox": [0, 0, 10, 10],
                "crop_has_caption": False,
                "crop_has_coordinates": False,
                "human_verified": True,
                "verified_by": "reviewer",
                "verified_at": "2026-06-20",
            }
        )
    (root / "labels" / "fen_ground_truth.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in fen_rows),
        encoding="utf-8",
    )
    (root / "labels" / "pgn_ground_truth.jsonl").write_text(
        json.dumps(
            {
                "id": "pgn-1",
                "source_pdf": "book.pdf",
                "page": 1,
                "input_type": "exercise_solution",
                "pgn_feasible": True,
                "pgn_feasibility_reason": "contains_solution_movetext",
                "expected_movetext": "1. e4 e5 *",
                "linked_fen_id": "fen-0",
                "human_verified": True,
                "verified_by": "reviewer",
                "verified_at": "2026-06-20",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    negative_crop = root / "crops" / "not_chess.png"
    negative_crop.write_bytes(b"not-a-real-image-for-schema-test")
    (root / "labels" / "negative_samples.jsonl").write_text(
        json.dumps(
            {
                "id": "negative-1",
                "source_pdf": "book.pdf",
                "page": 99,
                "reason": "not_chess_diagram",
                "crop_path": "crops/not_chess.png",
                "human_verified": True,
                "verified_by": "reviewer",
                "verified_at": "2026-06-20",
            }
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
