from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from chess_fen_review_corpus import export_fen_review_corpus
from chess_study_export import build_fen_square_dataset


class FakeReviewClient:
    available = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def load_review(self, *, artifact_id: str) -> dict:
        return self.payload


class ChessFenReviewCorpusTests(unittest.TestCase):
    def _review_payload(self, review_dir: Path, *, bad_hash: bool = False) -> dict:
        assets = review_dir / "fen_manual_assets"
        assets.mkdir(parents=True)
        crop = assets / "p001-d1.png"
        Image.new("RGB", (160, 160), "white").save(crop)
        crop_hash = hashlib.sha256(crop.read_bytes()).hexdigest()
        cells = [""] * 64
        cells[4] = "k"
        cells[60] = "K"
        verified = {
            "schema": "kindlemaster.fen_manual_review.row.v4",
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "source_artifact_sha256": "b" * 64,
            "source_binding": "source_pdf_sha256",
            "crop_rel_path": "fen_manual_assets/p001-d1.png",
            "crop_sha256": "0" * 64 if bad_hash else crop_hash,
            "square_labels": cells,
            "piece_labels_verified": True,
            "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "manual_label": "correct_diagram",
            "manual_side_to_move": "w",
            "manual_side_evidence": "marker",
            "manual_visible_marker": "outline_triangle",
            "board_crop_label": "correct",
            "marker_crop_label": "clear",
            "label_status": "verified",
            "human_verified": True,
            "fen_human_verified": True,
            "verification_source": "human_visual_piece_grid_and_marker",
            "verified_by": "reviewer",
            "verified_at": "2026-07-16T12:00:00Z",
            "page": 1,
            "review_index": 1,
        }
        excluded = {
            **verified,
            "diagram_id": "p002-d1",
            "diagram_fingerprint": "2" * 64,
            "label_status": "unreadable",
        }
        return {
            "storage": "database",
            "source_document_sha256": "a" * 64,
            "saved_at": "2026-07-16T12:00:00Z",
            "summary": {
                "total": 2,
                "verified": 1,
                "excluded": 1,
                "pending": 0,
                "invalid": 0,
            },
            "rows": [verified, excluded],
        }

    def test_export_backfills_legacy_review_contract_and_validates_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_dir = root / "source-review"
            payload = export_fen_review_corpus(
                artifact_id="artifact-1",
                out_dir=root / "out",
                review_dir=review_dir,
                cloud_client=FakeReviewClient(self._review_payload(review_dir)),
            )

            labels_path = Path(payload["artifacts"]["labels"])
            label = json.loads(labels_path.read_text(encoding="utf-8").strip())
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["verified_count"], 1)
            self.assertEqual(payload["excluded_count"], 1)
            self.assertEqual(payload["validator"]["valid_label_count"], 1)
            self.assertEqual(label["verification_source"], "human_visual")
            self.assertTrue(label["square_diff_ack"])
            self.assertTrue(label["human_verified"])
            self.assertEqual(label["id"], "p001-d1")

            dataset = build_fen_square_dataset(
                labels_path,
                out_dir=root / "dataset",
                fold_count=3,
                holdout_fold=0,
            )
            self.assertEqual(dataset["status"], "ok")
            self.assertEqual(dataset["sample_count"], 64)
            self.assertEqual(dataset["labels_validation"]["status"], "passed")

    def test_export_fails_closed_on_crop_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_dir = root / "source-review"
            payload = export_fen_review_corpus(
                artifact_id="artifact-1",
                out_dir=root / "out",
                review_dir=review_dir,
                cloud_client=FakeReviewClient(self._review_payload(review_dir, bad_hash=True)),
            )

            self.assertEqual(payload["status"], "failed")
            self.assertTrue(any(issue["code"] == "crop_sha256_mismatch" for issue in payload["issues"]))
            self.assertFalse((root / "out" / "review" / "fen_verified_labels.jsonl").exists())

    def test_placement_only_rows_remain_auditable_without_blocking_verified_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_dir = root / "source-review"
            review_payload = self._review_payload(review_dir)
            placement_only = {
                **review_payload["rows"][0],
                "diagram_id": "p003-d1",
                "diagram_fingerprint": "3" * 64,
                "label_status": "placement_verified",
                "manual_fen": "",
                "fen_human_verified": False,
            }
            review_payload["rows"].append(placement_only)
            review_payload["summary"].update(
                {"total": 3, "placement_verified": 1}
            )

            payload = export_fen_review_corpus(
                artifact_id="artifact-1",
                out_dir=root / "out",
                review_dir=review_dir,
                cloud_client=FakeReviewClient(review_payload),
            )

            excluded = [
                json.loads(line)
                for line in Path(payload["artifacts"]["excluded"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["verified_count"], 1)
            self.assertEqual(payload["excluded_count"], 2)
            self.assertEqual(payload["placement_verified_count"], 1)
            self.assertEqual(
                {row["label_status"] for row in excluded},
                {"unreadable", "placement_verified"},
            )

    def test_export_fails_closed_on_duplicate_excluded_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_dir = root / "source-review"
            review_payload = self._review_payload(review_dir)
            review_payload["rows"][1]["diagram_fingerprint"] = "1" * 64
            payload = export_fen_review_corpus(
                artifact_id="artifact-1",
                out_dir=root / "out",
                review_dir=review_dir,
                cloud_client=FakeReviewClient(review_payload),
            )

            self.assertEqual(payload["status"], "failed")
            self.assertTrue(any(issue["code"] == "duplicate_diagram_fingerprint" for issue in payload["issues"]))
            self.assertFalse((root / "out" / "review" / "fen_verified_labels.jsonl").exists())

    def test_square_dataset_rejects_noncanonical_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "diagram_id": "p001-d1",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "manual_label": "correct_diagram",
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_fen_square_dataset(labels, out_dir=root / "dataset")

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["reason"], "canonical_labels_validation_failed")
            self.assertEqual(payload["sample_count"], 0)

    def test_export_rejects_excluded_row_with_wrong_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            review_dir = root / "source-review"
            review_payload = self._review_payload(review_dir)
            review_payload["rows"][1]["source_document_sha256"] = "c" * 64
            payload = export_fen_review_corpus(
                artifact_id="artifact-1",
                out_dir=root / "out",
                review_dir=review_dir,
                cloud_client=FakeReviewClient(review_payload),
            )

            self.assertEqual(payload["status"], "failed")
            self.assertTrue(
                any(issue["code"] == "source_document_sha256_mismatch" for issue in payload["issues"])
            )
            self.assertFalse((root / "out" / "review" / "fen_verified_labels.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
