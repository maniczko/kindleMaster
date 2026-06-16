from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from chess_study_export import (
    build_fen_square_dataset,
    evaluate_fen_ensemble,
    export_fen_corpus_manifest,
    preprocess_chess_board_crops,
    recognize_fen_local,
    train_fen_square_classifier,
)


def _make_board_crop(path: Path) -> None:
    image = Image.new("RGB", (160, 160), "white")
    draw = ImageDraw.Draw(image)
    cell = 20
    for rank in range(8):
        for file_index in range(8):
            fill = "#f2d9b0" if (rank + file_index) % 2 == 0 else "#7a5232"
            draw.rectangle(
                [file_index * cell, rank * cell, (file_index + 1) * cell, (rank + 1) * cell],
                fill=fill,
            )
    draw.text((72, 6), "k", fill="black")
    draw.text((72, 144), "K", fill="black")
    image.save(path)


def _write_source_book(out: Path, crop_rel: str) -> None:
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "book.json").write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "page": 1,
                        "diagrams": [
                            {
                                "id": "p001_d001",
                                "page": 1,
                                "image_path": crop_rel,
                                "caption": "Diagram 1",
                                "confidence": 0.91,
                                "validation_status": "needs_review",
                            }
                        ],
                    }
                ],
                "pgn_records": [],
            }
        ),
        encoding="utf-8",
    )


class ChessFenModelPipelineTests(unittest.TestCase):
    def test_preprocess_boards_writes_evidence_without_accepting_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "out"
            crop = out / "assets" / "diagrams" / "p001_d001.png"
            crop.parent.mkdir(parents=True)
            _make_board_crop(crop)
            _write_source_book(out, "assets/diagrams/p001_d001.png")

            payload = preprocess_chess_board_crops(out)

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["normalized_count"], 1)
            self.assertEqual(payload["accepted_fen_changed"], 0)
            self.assertTrue((out / "data" / "board_preprocess.jsonl").is_file())
            self.assertTrue((out / "review" / "board_preprocess_review.html").is_file())

    def test_square_dataset_uses_verified_labels_and_holdout_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop = root / "crop.png"
            _make_board_crop(crop)
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "diagram_id": "p001_d001",
                        "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "manual_label": "correct_diagram",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "page": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_fen_square_dataset(labels, out_dir=out, fold_count=3, holdout_fold=0)

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["verified_label_count"], 1)
            self.assertEqual(payload["sample_count"], 64)
            self.assertIn("holdout", {"train", "val", "holdout"})
            self.assertTrue((out / "data" / "fen_square_dataset.jsonl").is_file())

    def test_missing_local_model_is_clean_review_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "out"

            payload = recognize_fen_local(out)

            self.assertEqual(payload["status"], "needs_review")
            self.assertEqual(payload["reason"], "model_missing")
            self.assertEqual(payload["accepted_fen_changed"], 0)

    def test_train_classifier_and_ensemble_do_not_auto_accept_without_verified_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop = out / "assets" / "diagrams" / "p001_d001.png"
            crop.parent.mkdir(parents=True)
            _make_board_crop(crop)
            _write_source_book(out, "assets/diagrams/p001_d001.png")
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "diagram_id": "p001_d001",
                        "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "manual_label": "correct_diagram",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "page": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            build_fen_square_dataset(labels, out_dir=out, fold_count=5, holdout_fold=0)
            train_fen_square_classifier(out)
            recognize_fen_local(out)

            payload = evaluate_fen_ensemble(out, min_confidence=0.99)

            self.assertEqual(payload["accepted_fen_changed"], 0)
            self.assertGreaterEqual(payload["conflict_count"], 1)
            self.assertTrue((out / "review" / "fen_ensemble_conflicts.html").is_file())

    def test_corpus_manifest_hashes_existing_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "out"
            (out / "review").mkdir(parents=True)
            (out / "review" / "fen_verified_labels.jsonl").write_text("{}", encoding="utf-8")

            payload = export_fen_corpus_manifest(out)

            self.assertEqual(payload["status"], "ok")
            self.assertTrue((out / "data" / "fen_corpus_manifest.json").is_file())
            self.assertTrue(any(item["exists"] for item in payload["artifacts"]))


if __name__ == "__main__":
    unittest.main()
