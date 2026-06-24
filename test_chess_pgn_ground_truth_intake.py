from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_chess_pgn_ground_truth_intake import prepare_chess_pgn_ground_truth_intake
from scripts.validate_chess_audit_dataset import validate_chess_audit_dataset


class ChessPgnGroundTruthIntakeTests(unittest.TestCase):
    def test_feasible_template_is_review_only_and_fails_until_movetext_and_human_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")

            summary = prepare_chess_pgn_ground_truth_intake(
                profile_id="Book PGN",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                target_dataset_dir=dataset.parent,
                count=2,
                default_input_type="exercise_solution",
            )
            template = Path(summary["template"])
            rows = [json.loads(line) for line in template.read_text(encoding="utf-8").splitlines() if line.strip()]
            (dataset.parent / "labels" / "pgn_ground_truth.jsonl").write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            readme = Path(summary["readme"]).read_text(encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(summary["status"], "review_required")
        self.assertFalse(summary["accepted_for_release_proof"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["pgn_feasible"] is True for row in rows))
        self.assertIn("apply_chess_audit_dataset_intake.py", readme)
        self.assertIn("--pgn-draft", readme)
        self.assertIn("--apply", readme)
        codes = {issue["code"] for issue in validation["issues"]}
        self.assertEqual(validation["status"], "failed")
        self.assertIn("feasible_pgn_expected_text_missing", codes)
        self.assertIn("human_verified_missing", codes)

    def test_completed_feasible_row_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")
            summary = prepare_chess_pgn_ground_truth_intake(
                profile_id="Book PGN",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                target_dataset_dir=dataset.parent,
                count=1,
                default_input_type="exercise_solution",
            )
            row = json.loads(Path(summary["template"]).read_text(encoding="utf-8").splitlines()[0])
            row.update(
                {
                    "page": 5,
                    "expected_movetext": "1. e4 e5 2. Nf3 Nc6 *",
                    "human_verified": True,
                    "verified_by": "reviewer",
                    "verified_at": "2026-06-20",
                }
            )
            (dataset.parent / "labels" / "pgn_ground_truth.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["counts"]["pgn_rows"], 1)

    def test_completed_diagram_only_row_validates_as_infeasible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")
            summary = prepare_chess_pgn_ground_truth_intake(
                profile_id="Book PGN",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                target_dataset_dir=dataset.parent,
                count=1,
                default_input_type="diagram_only",
            )
            row = json.loads(Path(summary["template"]).read_text(encoding="utf-8").splitlines()[0])
            row.update(
                {
                    "page": 6,
                    "human_verified": True,
                    "verified_by": "reviewer",
                    "verified_at": "2026-06-20",
                }
            )
            (dataset.parent / "labels" / "pgn_ground_truth.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(validation["status"], "passed")
        self.assertFalse(row["pgn_feasible"])
        self.assertEqual(row["pgn_feasibility_reason"], "diagram_only_no_movetext")

    def test_source_report_candidates_are_review_evidence_not_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")
            source_report = root / "runtime_report.json"
            source_report.write_text(
                json.dumps(
                    {
                        "quality_report": {
                            "chess_pgn": {
                                "records": [
                                    {
                                        "id": "scan-chess-p010-g01",
                                        "source_pages": [10],
                                        "status": "requires_review",
                                        "movetext": "1... Qe2+ 2. Kh1 Rxh2 *",
                                        "pgn": "[Event \"Book\"]\n\n1... Qe2+ 2. Kh1 Rxh2 *\n",
                                        "raw_text": "Diagram 1-3\n1...Qe2+ 2.Kh1 Rxh2",
                                        "warnings": ["pgn_replay_errors"],
                                    }
                                ]
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            summary = prepare_chess_pgn_ground_truth_intake(
                profile_id="Book PGN",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                source_report=source_report,
                target_dataset_dir=dataset.parent,
                count=5,
            )
            rows = [json.loads(line) for line in Path(summary["template"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            (dataset.parent / "labels" / "pgn_ground_truth.jsonl").write_text(Path(summary["template"]).read_text(encoding="utf-8"), encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(summary["candidate_source"], "runtime_report")
        self.assertEqual(summary["candidate_counts"]["rows"], 1)
        self.assertEqual(summary["candidate_counts"]["with_candidate_movetext"], 1)
        self.assertEqual(rows[0]["source_record_id"], "scan-chess-p010-g01")
        self.assertEqual(rows[0]["candidate_movetext"], "1... Qe2+ 2. Kh1 Rxh2 *")
        self.assertEqual(rows[0]["expected_movetext"], "")
        self.assertFalse(rows[0]["human_verified"])
        self.assertEqual(validation["status"], "failed")
        self.assertIn("feasible_pgn_expected_text_missing", {issue["code"] for issue in validation["issues"]})


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


if __name__ == "__main__":
    unittest.main()
