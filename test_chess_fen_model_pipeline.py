from __future__ import annotations

import base64
import json
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from chess_study_export import (
    _board_preprocess_sources,
    _export_rbf_svc_classifier,
    _predict_exported_rbf_svc,
    build_fen_square_dataset,
    evaluate_fen_ensemble,
    export_fen_corpus_manifest,
    preprocess_chess_board_crops,
    recover_fen_label_crops,
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
    def test_exported_rbf_svc_matches_sklearn_predictions(self) -> None:
        from sklearn.svm import SVC

        random = np.random.default_rng(17)
        features = np.vstack(
            [
                random.normal(loc=-2.0, scale=0.35, size=(20, 6)),
                random.normal(loc=0.0, scale=0.35, size=(20, 6)),
                random.normal(loc=2.0, scale=0.35, size=(20, 6)),
            ]
        ).astype(np.float32)
        labels = np.asarray(["B"] * 20 + ["K"] * 20 + ["empty"] * 20)
        label_sets = (labels, np.where(labels == "empty", "occupied", "empty"))
        for expected_labels in label_sets:
            classifier = SVC(C=4.0, gamma="scale", decision_function_shape="ovo").fit(features, expected_labels)
            expected = classifier.predict(features)
            actual, probabilities = _predict_exported_rbf_svc(features, _export_rbf_svc_classifier(classifier))

            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(probabilities.shape, (len(features), len(classifier.classes_)))
            np.testing.assert_allclose(probabilities.sum(axis=1), 1.0, atol=1e-6)

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
            self.assertEqual(payload["materialized_square_count"], 0)
            self.assertFalse(any((out / "assets" / "squares").rglob("*.png")))
            self.assertIn("holdout", {"train", "val", "holdout"})
            self.assertTrue((out / "data" / "fen_square_dataset.jsonl").is_file())

    def test_square_dataset_imports_hash_bound_legacy_label_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            crop = root / "legacy-crop.png"
            _make_board_crop(crop)
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "legacy-p001",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "source_crop_path": str(crop),
                        "sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                        "verified_by": "manual-grid-review",
                        "verified_at": "2026-07-14",
                        "page": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_fen_square_dataset(labels, out_dir=out, fold_count=3, holdout_fold=0)

            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["verified_label_count"], 1)
            self.assertEqual(payload["rejected_label_count"], 0)
            rows = [json.loads(line) for line in (out / "data" / "fen_square_dataset.jsonl").read_text().splitlines()]
            self.assertEqual({row["label_provenance"] for row in rows}, {"legacy_verified_metadata"})

    def test_square_dataset_rejects_legacy_label_when_crop_hash_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "legacy-crop.png"
            _make_board_crop(crop)
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "legacy-p001",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "source_crop_path": str(crop),
                        "sha256": "0" * 64,
                        "verified_by": "manual-grid-review",
                        "verified_at": "2026-07-14",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_fen_square_dataset(labels, out_dir=root / "out")

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["verified_label_count"], 0)
            self.assertEqual(payload["rejection_counts"], [{"key": "source_crop_sha256_mismatch", "count": 1}])

    def test_square_dataset_rejects_uncertain_legacy_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "legacy-crop.png"
            _make_board_crop(crop)
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "legacy-p001",
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "source_crop_path": str(crop),
                        "sha256": hashlib.sha256(crop.read_bytes()).hexdigest(),
                        "verified_by": "manual-grid-review",
                        "verified_at": "2026-07-14",
                        "notes": "re-verify because current crop hash changed",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_fen_square_dataset(labels, out_dir=root / "out")

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["verified_label_count"], 0)
            self.assertEqual(payload["rejection_counts"], [{"key": "label_provenance_uncertain", "count": 1}])

    def test_square_dataset_keeps_all_boards_from_one_page_in_one_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            labels = root / "labels.jsonl"
            records = []
            for index in range(2):
                crop = root / f"crop-{index}.png"
                _make_board_crop(crop)
                records.append(
                    {
                        "diagram_id": f"p001-d{index}",
                        "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "source_document_sha256": "source-sha",
                        "page": 1,
                    }
                )
            labels.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

            out = root / "out"
            payload = build_fen_square_dataset(labels, out_dir=out, fold_count=5, holdout_fold=0)
            rows = [json.loads(line) for line in (out / "data" / "fen_square_dataset.jsonl").read_text().splitlines()]

            self.assertEqual(payload["status"], "ok")
            self.assertFalse(payload["leakage_detected"])
            self.assertEqual(len({row["split"] for row in rows}), 1)
            self.assertEqual(len({row["split_group"] for row in rows}), 1)

    def test_square_dataset_keeps_duplicate_board_hashes_in_one_split(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "crop.png"
            _make_board_crop(crop)
            labels = root / "labels.jsonl"
            labels.write_text(
                "".join(
                    json.dumps(
                        {
                            "diagram_id": f"p{page:03d}-d1",
                            "manual_fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                            "label_status": "verified",
                            "crop_path": str(crop),
                            "source_document_sha256": "source-sha",
                            "page": page,
                        }
                    )
                    + "\n"
                    for page in (1, 2)
                ),
                encoding="utf-8",
            )

            out = root / "out"
            payload = build_fen_square_dataset(labels, out_dir=out, fold_count=5, holdout_fold=0)
            rows = [json.loads(line) for line in (out / "data" / "fen_square_dataset.jsonl").read_text().splitlines()]

            self.assertEqual(payload["status"], "ok")
            self.assertFalse(payload["leakage_detected"])
            self.assertEqual(len({row["split"] for row in rows}), 1)
            self.assertEqual(len({row["split_group"] for row in rows}), 1)

    def test_classifier_trains_from_source_crops_without_materialized_squares(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            labels = root / "labels.jsonl"
            placements = (
                "4k3/8/8/8/8/8/8/4K3",
                "3qk3/8/8/8/8/8/8/3QK3",
                "3rk3/8/8/8/8/8/8/3RK3",
            )
            records = []
            for index in range(15):
                crop = root / f"crop-{index}.png"
                _make_board_crop(crop)
                with Image.open(crop) as image:
                    varied = image.convert("RGB")
                draw = ImageDraw.Draw(varied)
                x = 2 + (index % 8) * 20
                y = 2 + ((index // 8) % 8) * 20
                draw.rectangle([x, y, x + 3, y + 3], fill=(index * 13 % 255, 0, 0))
                varied.save(crop)
                records.append(
                    {
                        "diagram_id": f"p{index + 1:03d}-d1",
                        "manual_fen": f"{placements[index % len(placements)]} w - - 0 1",
                        "label_status": "verified",
                        "crop_path": str(crop),
                        "source_document_sha256": "source-sha",
                        "page": index + 1,
                    }
                )
            labels.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")

            dataset = build_fen_square_dataset(labels, out_dir=out, fold_count=5, holdout_fold=0)
            model = train_fen_square_classifier(out)

            self.assertEqual(dataset["status"], "ok")
            self.assertEqual(dataset["materialized_square_count"], 0)
            self.assertEqual(model["status"], "ok")
            self.assertEqual(model["model_type"], "rbf_svc_hog_two_stage")
            self.assertEqual(model["training_hyperparameters"]["binary_svc_c"], 1.0)
            self.assertEqual(model["training_hyperparameters"]["piece_svc_c"], 1.0)
            self.assertIn("exact_board_accuracy", model)
            model_path = out / "models" / "chess_fen_square_v1.json"
            self.assertTrue(model_path.is_file())

            recovery_crop = root / "recovery-crop.png"
            _make_board_crop(recovery_crop)
            with Image.open(recovery_crop) as image:
                varied = image.convert("RGB")
            ImageDraw.Draw(varied).rectangle([122, 122, 130, 130], fill="black")
            varied.save(recovery_crop)
            recovery_labels = root / "recovery-labels.jsonl"
            recovery_labels.write_text(
                json.dumps(
                    {
                        "id": "legacy-recovery-label",
                        "fen": "4k3/8/8/8/8/8/3Q4/4K3 w - - 0 1",
                        "verified_by": "manual-grid-review",
                        "verified_at": "2026-07-14",
                        "page": 999,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            manifest = root / "chess_diagrams.json"
            manifest.write_text(
                json.dumps(
                    {
                        "source_pdf": "fixture.pdf",
                        "source_document_sha256": "fixture-source-sha",
                        "diagrams": [
                            {
                                "diagram_id": "p999_d01",
                                "diagram_fingerprint": "fixture-fingerprint",
                                "source_document_sha256": "fixture-source-sha",
                                "page": 999,
                                "board_crop_path": str(recovery_crop),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            recovery = recover_fen_label_crops(
                recovery_labels,
                board_manifest_path=manifest,
                model_path=model_path,
                out_dir=root / "recovery",
                min_square_match=0.0,
                min_occupied_match=0.0,
                min_match_margin=0.0,
            )

            self.assertEqual(recovery["status"], "ok")
            self.assertEqual(recovery["accepted_mapping_count"], 1)
            recovered = json.loads(
                (root / "recovery" / "review" / "fen_recovered_labels.jsonl").read_text(encoding="utf-8")
            )
            self.assertEqual(recovered["diagram_id"], "p999_d01")
            self.assertEqual(recovered["crop_sha256"], hashlib.sha256(recovery_crop.read_bytes()).hexdigest())

            artifact = root / "artifact"
            report_dir = artifact / "report"
            input_dir = artifact / "input"
            report_dir.mkdir(parents=True)
            input_dir.mkdir(parents=True)
            source_pdf = input_dir / "fixture.pdf"
            source_pdf.write_bytes(b"%PDF-1.4\nfixture\n")
            embedded_labels = root / "embedded-labels.jsonl"
            embedded_labels.write_text(
                json.dumps(
                    {
                        "id": "legacy-embedded-label",
                        "fen": "4k3/8/8/8/8/8/3Q4/4K3 w - - 0 1",
                        "verified_by": "manual-grid-review",
                        "verified_at": "2026-07-14",
                        "page": 1000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            embedded_manifest = report_dir / "chess_diagrams.json"
            embedded_manifest.write_text(
                json.dumps(
                    {
                        "diagram_count": 1,
                        "records": [
                            {
                                "id": "layout-chess-p1000-d01",
                                "page_number": 1000,
                                "board_crop_path": "images/missing.png",
                                "image_data_uri": "data:image/png;base64,"
                                + base64.b64encode(recovery_crop.read_bytes()).decode("ascii"),
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            embedded_recovery = recover_fen_label_crops(
                embedded_labels,
                board_manifest_path=embedded_manifest,
                model_path=model_path,
                out_dir=artifact,
                min_square_match=0.0,
                min_occupied_match=0.0,
                min_match_margin=0.0,
            )

            self.assertEqual(embedded_recovery["accepted_mapping_count"], 1)
            embedded = json.loads((artifact / "review" / "fen_recovered_labels.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(embedded["diagram_id"], "layout-chess-p1000-d01")
            self.assertEqual(embedded["source_document_sha256"], hashlib.sha256(source_pdf.read_bytes()).hexdigest())
            self.assertEqual(embedded["crop_sha256"], hashlib.sha256(recovery_crop.read_bytes()).hexdigest())
            self.assertTrue(Path(embedded["crop_path"]).is_file())

    def test_missing_local_model_is_clean_review_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "out"

            payload = recognize_fen_local(out)

            self.assertEqual(payload["status"], "needs_review")
            self.assertEqual(payload["reason"], "model_missing")
            self.assertEqual(payload["accepted_fen_changed"], 0)

    def test_conversion_artifact_inference_uses_full_manual_review_crop_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir) / "artifact"
            crop = out / "review" / "fen_manual_assets" / "board.png"
            crop.parent.mkdir(parents=True)
            _make_board_crop(crop)
            (out / "report").mkdir()
            (out / "report" / "chess_diagrams.json").write_text(
                json.dumps({"records": []}),
                encoding="utf-8",
            )
            (out / "review" / "fen_manual_draft.jsonl").write_text(
                json.dumps(
                    {
                        "diagram_id": "full-review-row",
                        "diagram_fingerprint": "f" * 64,
                        "page": 17,
                        "caption": "Diagram 17-1",
                        "crop_path": str(crop),
                        "crop_rel_path": "fen_manual_assets/board.png",
                        "detected_marker_symbol": "△",
                        "detected_marker_status": "trusted_marker",
                        "marker_crop_quality": "pass",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            sources = _board_preprocess_sources(out, labels_path=None)

            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["diagram_id"], "full-review-row")
            self.assertEqual(sources[0]["side_to_move"], "w")
            self.assertEqual(sources[0]["source"], "fen_manual_review_crop")

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
