from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.apply_chess_audit_dataset_intake import apply_chess_audit_dataset_intake
from scripts.validate_chess_audit_dataset import validate_chess_audit_dataset


class ChessAuditDatasetIntakeApplyTests(unittest.TestCase):
    def test_evidence_only_rows_are_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset(root)
            draft = root / "pgn_draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "pgn-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "input_type": "exercise_solution",
                        "pgn_feasible": True,
                        "pgn_feasibility_reason": "candidate_only",
                        "candidate_movetext": "1. e4 e5 *",
                        "expected_movetext": "",
                        "human_verified": False,
                        "verified_by": "",
                        "verified_at": "",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_chess_audit_dataset_intake(target_dataset_dir=dataset, pgn_draft=draft, apply=True)

        self.assertEqual(summary["pgn"]["accepted_rows"], 0)
        self.assertEqual(summary["pgn"]["skipped_rows"], 1)
        self.assertEqual(summary["pgn"]["skipped"][0]["reason"], "human_verified_missing")

    def test_human_verified_pgn_and_negative_rows_are_applied_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset(root, fen_count=20)
            negative_crop = dataset / "crops" / "not_chess.png"
            negative_crop.write_bytes(b"not-a-real-image")
            pgn_draft = root / "pgn_draft.jsonl"
            negative_draft = root / "negative_draft.jsonl"
            pgn_draft.write_text(
                json.dumps(
                    {
                        "id": "pgn-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "input_type": "exercise_solution",
                        "pgn_feasible": True,
                        "pgn_feasibility_reason": "contains_solution_movetext",
                        "expected_movetext": "1. e4 e5 *",
                        "expected_pgn": "",
                        "candidate_movetext": "WRONG EVIDENCE ONLY",
                        "linked_fen_id": "fen-0",
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-21",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            negative_draft.write_text(
                json.dumps(
                    {
                        "id": "negative-1",
                        "source_pdf": "book.pdf",
                        "page": 30,
                        "reason": "not_chess_diagram",
                        "crop_path": "crops/not_chess.png",
                        "candidate_crop_path": "reports/intake/candidate.png",
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-21",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_chess_audit_dataset_intake(
                target_dataset_dir=dataset,
                pgn_draft=pgn_draft,
                negative_draft=negative_draft,
                apply=True,
            )
            pgn_rows = _read_jsonl(dataset / "labels" / "pgn_ground_truth.jsonl")
            negative_rows = _read_jsonl(dataset / "labels" / "negative_samples.jsonl")
            validation = validate_chess_audit_dataset(dataset / "manifest.json")

        self.assertEqual(summary["pgn"]["accepted_rows"], 1)
        self.assertEqual(summary["negative"]["accepted_rows"], 1)
        self.assertNotIn("candidate_movetext", pgn_rows[0])
        self.assertNotIn("candidate_crop_path", negative_rows[0])
        self.assertEqual(validation["status"], "passed")
        self.assertTrue(validation["release_readiness"]["accepted_for_release_proof"])

    def test_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset(root, fen_count=20)
            draft = root / "pgn_draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "pgn-1",
                        "source_pdf": "book.pdf",
                        "page": 1,
                        "input_type": "exercise_solution",
                        "pgn_feasible": True,
                        "pgn_feasibility_reason": "contains_solution_movetext",
                        "expected_movetext": "1. e4 e5 *",
                        "human_verified": True,
                        "verified_by": "reviewer",
                        "verified_at": "2026-06-21",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = apply_chess_audit_dataset_intake(target_dataset_dir=dataset, pgn_draft=draft)
            pgn_rows = _read_jsonl(dataset / "labels" / "pgn_ground_truth.jsonl")

        self.assertFalse(summary["applied"])
        self.assertEqual(summary["pgn"]["new_rows"], 1)
        self.assertEqual(pgn_rows, [])


def _write_dataset(root: Path, *, fen_count: int = 0) -> Path:
    dataset = root / "dataset"
    (dataset / "labels").mkdir(parents=True)
    (dataset / "crops").mkdir()
    (dataset / "overlays").mkdir()
    (dataset / "labels" / "pgn_ground_truth.jsonl").write_text("", encoding="utf-8")
    (dataset / "labels" / "negative_samples.jsonl").write_text("", encoding="utf-8")
    fen_rows = []
    for index in range(fen_count):
        crop = dataset / "crops" / f"board_{index}.png"
        crop.write_bytes(b"not-a-real-image")
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
                "verified_at": "2026-06-21",
            }
        )
    (dataset / "labels" / "fen_ground_truth.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in fen_rows),
        encoding="utf-8",
    )
    (dataset / "manifest.json").write_text(
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
    return dataset


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    unittest.main()
