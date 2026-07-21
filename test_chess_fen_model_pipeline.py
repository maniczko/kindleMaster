from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

from chess_fen_square_model import _decode_king_constrained_predictions

from chess_study_export import (
    build_fen_square_dataset,
    evaluate_fen_square_classifier,
    evaluate_fen_ensemble,
    export_fen_corpus_manifest,
    preprocess_chess_board_crops,
    recognize_fen_local,
    train_fen_square_classifier,
)
from scripts.evaluate_chess_fen_benchmark_experiment import evaluate_chess_fen_benchmark_experiment, main as benchmark_main


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
    def test_king_constrained_decoder_recovers_runner_up_white_king(self) -> None:
        classes = np.asarray(["B", "K", "N", "P", "Q", "R", "b", "empty", "k", "n", "p", "q", "r"])
        probabilities = np.full((64, len(classes)), 0.001, dtype=np.float64)
        probabilities[:, 7] = 0.98
        probabilities[4, 5] = 0.55
        probabilities[4, 1] = 0.44
        probabilities[60, 8] = 0.99

        predicted, confidences, decoding = _decode_king_constrained_predictions(
            probabilities,
            classes,
        )

        self.assertEqual(list(predicted).count("K"), 1)
        self.assertEqual(list(predicted).count("k"), 1)
        self.assertEqual(predicted[4], "K")
        self.assertAlmostEqual(confidences[4], 0.44)
        self.assertTrue(decoding["constraint_applied"])

    def test_king_constrained_decoder_preserves_legal_argmax_board(self) -> None:
        classes = np.asarray(["B", "K", "N", "P", "Q", "R", "b", "empty", "k", "n", "p", "q", "r"])
        probabilities = np.full((64, len(classes)), 0.001, dtype=np.float64)
        probabilities[:, 7] = 0.98
        probabilities[4, 8] = 0.99
        probabilities[60, 1] = 0.99

        predicted, _confidences, decoding = _decode_king_constrained_predictions(
            probabilities,
            classes,
        )

        self.assertEqual(predicted[4], "k")
        self.assertEqual(predicted[60], "K")
        self.assertFalse(decoding["constraint_applied"])

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
                        "schema": "kindlemaster.chess_fen_label.v2",
                        "id": "p001_d001",
                        "diagram_id": "p001_d001",
                        "diagram_fingerprint": "1" * 64,
                        "source_document_sha256": "a" * 64,
                        "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "manual_label": "correct_diagram",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "crop_sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                        "verified_by": "unit-test",
                        "verified_at": "2026-07-16T12:00:00Z",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
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
            self.assertEqual(payload["leakage_check"]["status"], "passed")
            self.assertTrue((out / "data" / "fen_square_dataset.jsonl").is_file())

    def test_square_dataset_keeps_diagrams_from_one_page_in_one_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            labels = root / "labels.jsonl"
            label_rows = []
            for index in range(2):
                crop = root / f"crop-{index}.png"
                _make_board_crop(crop)
                label_rows.append(
                    {
                        "schema": "kindlemaster.chess_fen_label.v2",
                        "id": f"p001_d00{index + 1}",
                        "diagram_id": f"p001_d00{index + 1}",
                        "diagram_fingerprint": str(index + 1) * 64,
                        "source_document_sha256": "a" * 64,
                        "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "manual_label": "correct_diagram",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "crop_sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                        "verified_by": "unit-test",
                        "verified_at": "2026-07-16T12:00:00Z",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
                        "page": 1,
                    }
                )
            labels.write_text(
                "".join(json.dumps(row) + "\n" for row in label_rows),
                encoding="utf-8",
            )

            payload = build_fen_square_dataset(labels, out_dir=out, fold_count=5, holdout_fold=0)
            dataset_rows = [
                json.loads(line)
                for line in (out / "data" / "fen_square_dataset.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(payload["leakage_check"]["status"], "passed")
            self.assertEqual(len({row["split"] for row in dataset_rows}), 1)
            self.assertEqual(len({row["split_group"] for row in dataset_rows}), 1)

    def test_evaluate_classifier_fails_cleanly_when_candidate_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "out"
            dataset = out / "data" / "fen_square_dataset.jsonl"
            dataset.parent.mkdir(parents=True)
            dataset.write_text("", encoding="utf-8")

            with patch(
                "chess_fen_square_model._import_training_dependencies",
                return_value={"status": "unavailable", "exception": "No module named 'sklearn'"},
            ):
                payload = evaluate_fen_square_classifier(out)

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"], "model_missing")

    def test_missing_local_model_is_clean_review_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "out"

            payload = recognize_fen_local(out, model_path=out / "models" / "missing.npz")

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
                        "schema": "kindlemaster.chess_fen_label.v2",
                        "id": "p001_d001",
                        "diagram_id": "p001_d001",
                        "diagram_fingerprint": "1" * 64,
                        "source_document_sha256": "a" * 64,
                        "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "manual_label": "correct_diagram",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "crop_sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                        "verified_by": "unit-test",
                        "verified_at": "2026-07-16T12:00:00Z",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
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

    def test_benchmark_experiment_reports_review_only_metrics_from_legacy_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels_dir = root / "labels"
            labels_dir.mkdir()
            crop = root / "crop.png"
            _make_board_crop(crop)
            labels = [
                {
                    "id": f"legacy_{index}",
                    "crop_path": str(crop),
                    "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "verified_by": "legacy-grid-review",
                    "verified_at": "2026-06-26",
                    "page": index,
                }
                for index in range(6)
            ]
            (labels_dir / "legacy.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in labels),
                encoding="utf-8",
            )
            report = root / "report.json"

            payload = evaluate_chess_fen_benchmark_experiment(
                labels_dir=labels_dir,
                out_dir=root / "out",
                report_path=report,
                min_usable_labels=1,
                min_holdout_boards=1,
            )

            self.assertIn(payload["status"], {"completed", "insufficient_benchmark"})
            self.assertEqual(payload["experiment"]["accepted_fen_changed"], 0)
            self.assertFalse(payload["experiment"]["runtime_strict_acceptance_changed"])
            self.assertIn("exact_placement_rate", payload["metrics"])
            self.assertIn("exact_full_fen_rate", payload["metrics"])
            self.assertIn("false_positive_rate", payload["metrics"])
            self.assertIn("review_rate", payload["metrics"])
            self.assertTrue(report.is_file())

    def test_benchmark_experiment_completes_with_insufficiency_report_for_missing_crops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels_dir = root / "labels"
            labels_dir.mkdir()
            (labels_dir / "missing.jsonl").write_text(
                json.dumps(
                    {
                        "id": "missing",
                        "crop_path": str(root / "missing.png"),
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "verified_by": "legacy-grid-review",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            report = root / "report.json"

            exit_code = benchmark_main(
                [
                    "--labels-dir",
                    str(labels_dir),
                    "--out-dir",
                    str(root / "out"),
                    "--report",
                    str(report),
                    "--min-usable-labels",
                    "1",
                    "--min-holdout-boards",
                    "1",
                ]
            )

            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["status"], "insufficient_benchmark")
            self.assertEqual(payload["inputs"]["usable_label_count"], 0)
            self.assertTrue(payload["sufficiency"]["next_actions"])
            self.assertEqual(payload["experiment"]["accepted_fen_changed"], 0)


if __name__ == "__main__":
    unittest.main()
