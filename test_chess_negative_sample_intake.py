from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_chess_negative_sample_intake import prepare_chess_negative_sample_intake
from scripts.validate_chess_audit_dataset import validate_chess_audit_dataset


class ChessNegativeSampleIntakeTests(unittest.TestCase):
    def test_intake_package_is_review_only_and_template_fails_until_manual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")

            summary = prepare_chess_negative_sample_intake(
                profile_id="Book Negative Samples",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                target_dataset_dir=dataset.parent,
                count=2,
            )
            template = Path(summary["template"])
            rows = [json.loads(line) for line in template.read_text(encoding="utf-8").splitlines() if line.strip()]
            (dataset.parent / "labels" / "negative_samples.jsonl").write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            readme = Path(summary["readme"]).read_text(encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(summary["status"], "review_required")
        self.assertFalse(summary["accepted_for_release_proof"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["human_verified"] is False for row in rows))
        self.assertIn("apply_chess_audit_dataset_intake.py", readme)
        self.assertIn("--negative-draft", readme)
        self.assertIn("--apply", readme)
        codes = {issue["code"] for issue in validation["issues"]}
        self.assertEqual(validation["status"], "failed")
        self.assertIn("crop_path_missing", codes)
        self.assertIn("human_verified_missing", codes)
        self.assertIn("verified_by_missing", codes)
        self.assertIn("verified_at_invalid", codes)

    def test_completed_negative_row_from_template_can_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")
            summary = prepare_chess_negative_sample_intake(
                profile_id="Book Negative Samples",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                target_dataset_dir=dataset.parent,
                count=1,
            )
            crop = dataset.parent / "crops" / "not_chess.png"
            crop.write_bytes(b"negative-crop")
            row = json.loads(Path(summary["template"]).read_text(encoding="utf-8").splitlines()[0])
            row.update(
                {
                    "page": 7,
                    "crop_path": "crops/not_chess.png",
                    "human_verified": True,
                    "verified_by": "reviewer",
                    "verified_at": "2026-06-20",
                }
            )
            (dataset.parent / "labels" / "negative_samples.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(validation["status"], "passed")
        self.assertEqual(validation["counts"]["negative_rows"], 1)

    def test_source_crops_dir_creates_review_only_candidates_without_canonical_crop_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")
            source_crops = root / "source_crops"
            source_crops.mkdir()
            (source_crops / "table.png").write_bytes(b"fake-png-for-review")
            (source_crops / "text.jpg").write_bytes(b"fake-jpg-for-review")

            summary = prepare_chess_negative_sample_intake(
                profile_id="Book Negative Samples",
                output_dir=root / "intake",
                source_pdf="book.pdf",
                source_crops_dir=source_crops,
                target_dataset_dir=dataset.parent,
                count=1,
                default_reason="table",
            )
            template = Path(summary["template"])
            rows = [json.loads(line) for line in template.read_text(encoding="utf-8").splitlines() if line.strip()]
            candidate_crop_exists = Path(rows[0]["candidate_crop_path"]).exists()
            (dataset.parent / "labels" / "negative_samples.jsonl").write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(summary["candidate_source"], "source_crops_dir")
        self.assertEqual(summary["candidate_counts"]["rows"], 1)
        self.assertEqual(summary["candidate_counts"]["with_candidate_crop_path"], 1)
        self.assertEqual(summary["candidate_counts"]["with_canonical_crop_path"], 0)
        self.assertEqual(rows[0]["reason"], "table")
        self.assertTrue(rows[0]["candidate_crop_path"])
        self.assertTrue(candidate_crop_exists)
        self.assertEqual(rows[0]["crop_path"], "")
        self.assertFalse(rows[0]["human_verified"])
        self.assertEqual(validation["status"], "failed")
        self.assertIn("crop_path_missing", {issue["code"] for issue in validation["issues"]})

    def test_extract_from_pdf_creates_review_only_region_candidates(self) -> None:
        try:
            import fitz  # type: ignore

        except Exception:
            self.skipTest("PyMuPDF unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = _write_dataset_scaffold(root / "audit_dataset")
            pdf_path = root / "source.pdf"
            document = fitz.open()
            page = document.new_page(width=240, height=320)
            page.insert_text((32, 32), "Intro text, not a board")
            document.save(pdf_path)
            document.close()

            summary = prepare_chess_negative_sample_intake(
                profile_id="Book Negative Samples",
                output_dir=root / "intake",
                source_pdf=pdf_path,
                target_dataset_dir=dataset.parent,
                count=2,
                extract_from_pdf=True,
                default_reason="text_only",
            )
            rows = [json.loads(line) for line in Path(summary["template"]).read_text(encoding="utf-8").splitlines() if line.strip()]
            candidate_paths_exist = all(Path(row["candidate_crop_path"]).exists() for row in rows)
            (dataset.parent / "labels" / "negative_samples.jsonl").write_text(Path(summary["template"]).read_text(encoding="utf-8"), encoding="utf-8")
            validation = validate_chess_audit_dataset(dataset)

        self.assertEqual(summary["candidate_source"], "source_pdf_regions")
        self.assertEqual(summary["candidate_counts"]["rows"], 2)
        self.assertEqual(summary["candidate_counts"]["with_candidate_crop_path"], 2)
        self.assertEqual(summary["candidate_counts"]["with_canonical_crop_path"], 0)
        self.assertTrue(candidate_paths_exist)
        self.assertEqual(rows[0]["page"], 1)
        self.assertTrue(rows[0]["candidate_region"])
        self.assertEqual(rows[0]["crop_path"], "")
        self.assertFalse(rows[0]["human_verified"])
        self.assertEqual(validation["status"], "failed")
        self.assertIn("crop_path_missing", {issue["code"] for issue in validation["issues"]})


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
