from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from app_runtime_services import build_conversion_metadata
from model_registry import rollback_model
from quality_state_service import ConversionQualityStateRequest, assemble_quality_state_dict
from scripts.train_route_classifier import promote_route_classifier


def _candidate_model(version: str, *, dataset_version: str = "dataset-unit-v2") -> dict:
    return {
        "model_version": version,
        "model_type": "multinomial_logistic_regression",
        "dataset_version": dataset_version,
        "feature_order": ["page_count"],
        "classes": ["book_reflow", "scanned_reflow"],
        "scaler": {"mean": [0.0], "scale": [1.0]},
        "weights": {"book_reflow": [0.0], "scanned_reflow": [0.0]},
        "intercepts": {"book_reflow": 0.0, "scanned_reflow": 0.0},
        "metrics": {
            "accuracy": 0.95,
            "macro_f1": 0.94,
            "coverage": 1.0,
            "example_count": 80,
            "train_example_count": 60,
            "holdout_example_count": 20,
            "label_counts": {"book_reflow": 40, "scanned_reflow": 40},
            "per_class_recall": {
                "book_reflow": 0.95,
                "diagram_book_reflow": 0.9,
                "scanned_reflow": 0.92,
            },
            "dataset_readiness": {"status": "ready", "promotion_allowed": True, "dataset_version": dataset_version},
        },
    }


class ModelRegistryTests(unittest.TestCase):
    def test_promote_updates_registry_history_model_card_and_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_model = root / "models" / "route_classifier_v1.json"
            candidate = root / "models" / "candidates" / "candidate.json"
            corpus_report = root / "reports" / "corpus" / "premium_corpus_smoke_report.json"
            current_model.parent.mkdir(parents=True)
            candidate.parent.mkdir(parents=True)
            corpus_report.parent.mkdir(parents=True)
            current_model.write_text(json.dumps({"model_version": "route-current-v1"}), encoding="utf-8")
            candidate.write_text(json.dumps(_candidate_model("route-candidate-v2")), encoding="utf-8")
            corpus_report.write_text(json.dumps({"overall_status": "passed"}), encoding="utf-8")

            payload = promote_route_classifier(
                candidate_path=candidate,
                model_path=current_model,
                corpus_report_path=corpus_report,
                repo_root=root,
            )

            registry = json.loads((root / "models" / "registry.json").read_text(encoding="utf-8"))
            history = (root / "reports" / "ml" / "promotions" / "promotion_history.jsonl").read_text(encoding="utf-8")
            card_json = root / payload["model_registry"]["model_card_json_path"]
            card_md = root / payload["model_registry"]["model_card_md_path"]
            rollback = root / payload["rollback_snapshot"]
            card_json_exists = card_json.exists()
            card_md_exists = card_md.exists()
            card = json.loads(card_json.read_text(encoding="utf-8"))
            rollback_exists = rollback.exists()
            rollback_model_version = json.loads(rollback.read_text(encoding="utf-8"))["model_version"]

        self.assertEqual(payload["status"], "promoted")
        self.assertEqual(registry["active_models"]["route_classifier"]["model_version"], "route-candidate-v2")
        self.assertEqual(registry["active_models"]["route_classifier"]["dataset_version"], "dataset-unit-v2")
        self.assertIn("route-candidate-v2", history)
        self.assertTrue(card_json_exists)
        self.assertTrue(card_md_exists)
        self.assertEqual(card["model_name"], "route_classifier")
        self.assertEqual(card["training_data_counts"]["example_count"], 80)
        self.assertEqual(card["privacy_notes"]["stores_text"], False)
        self.assertTrue(rollback_exists)
        self.assertEqual(rollback_model_version, "route-current-v1")

    def test_promote_dry_run_does_not_mutate_model_or_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_model = root / "models" / "route_classifier_v1.json"
            candidate = root / "models" / "candidates" / "candidate.json"
            corpus_report = root / "reports" / "corpus" / "premium_corpus_smoke_report.json"
            current_model.parent.mkdir(parents=True)
            candidate.parent.mkdir(parents=True)
            corpus_report.parent.mkdir(parents=True)
            current_model.write_text(json.dumps({"model_version": "route-current-v1"}), encoding="utf-8")
            candidate.write_text(json.dumps(_candidate_model("route-candidate-v2")), encoding="utf-8")
            corpus_report.write_text(json.dumps({"overall_status": "passed"}), encoding="utf-8")

            with patch.object(
                sys,
                "argv",
                [
                    "kindlemaster.py",
                    "ml",
                    "promote",
                    "--candidate",
                    str(candidate),
                    "--model",
                    str(current_model),
                    "--corpus-report",
                    str(corpus_report),
                    "--dry-run",
                ],
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = kindlemaster.main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(current_model.read_text(encoding="utf-8"))["model_version"], "route-current-v1")
            self.assertFalse((root / "models" / "registry.json").exists())

    def test_rollback_restores_snapshot_and_updates_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_model = root / "models" / "route_classifier_v1.json"
            candidate = root / "models" / "candidates" / "candidate.json"
            corpus_report = root / "reports" / "corpus" / "premium_corpus_smoke_report.json"
            current_model.parent.mkdir(parents=True)
            candidate.parent.mkdir(parents=True)
            corpus_report.parent.mkdir(parents=True)
            current_model.write_text(json.dumps({"model_version": "route-current-v1"}), encoding="utf-8")
            candidate.write_text(json.dumps(_candidate_model("route-candidate-v2")), encoding="utf-8")
            corpus_report.write_text(json.dumps({"overall_status": "passed"}), encoding="utf-8")
            promote_route_classifier(
                candidate_path=candidate,
                model_path=current_model,
                corpus_report_path=corpus_report,
                repo_root=root,
            )

            payload = rollback_model(model_name="route_classifier", to_version="route-current-v1", repo_root=root)
            registry = json.loads((root / "models" / "registry.json").read_text(encoding="utf-8"))
            restored_model_version = json.loads(current_model.read_text(encoding="utf-8"))["model_version"]

        self.assertEqual(payload["status"], "rolled_back")
        self.assertEqual(restored_model_version, "route-current-v1")
        self.assertEqual(registry["active_models"]["route_classifier"]["model_version"], "route-current-v1")
        self.assertEqual(registry["last_promotion"]["event_type"], "model_rollback")

    def test_conversion_metadata_and_quality_state_include_model_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "models").mkdir()
            (root / "models" / "route_classifier_v1.json").write_text(
                json.dumps({"model_version": "route-registry-v1", "model_type": "multinomial_logistic_regression"}),
                encoding="utf-8",
            )
            (root / "models" / "quality_verifier_v1.json").write_text(
                json.dumps({"model_version": "quality-registry-v1", "model_type": "local_quality_policy"}),
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                metadata = build_conversion_metadata(
                    result={
                        "analysis": {
                            "profile": "book_reflow",
                            "confidence": 0.9,
                            "route_decision": {
                                "mode": "shadow",
                                "selected_profile": "book_reflow",
                                "model_version": "route-runtime-v2",
                            },
                        },
                        "quality_report": {
                            "validation_status": "passed",
                            "ai_quality_verification": {"model_version": "quality-runtime-v2"},
                        },
                        "document_summary": {"section_count": 1},
                    },
                    detected_source_type="pdf",
                    heading_repair_enabled=False,
                    heading_repair_report={"status": "skipped"},
                )
                quality_state = assemble_quality_state_dict(
                    ConversionQualityStateRequest.from_job_payload(
                        {
                            "status": "ready",
                            "metadata": metadata,
                            "output_path_exists": True,
                        }
                    )
                )
            finally:
                os.chdir(old_cwd)

        self.assertEqual(metadata["route_model_version"], "route-runtime-v2")
        self.assertEqual(metadata["quality_verifier_version"], "quality-runtime-v2")
        self.assertEqual(metadata["chess_fen_profile_version"], "chess-fen-profile-bootstrap")
        self.assertTrue(metadata["model_registry_version"].startswith("registry_"))
        self.assertEqual(quality_state["model_attribution"]["route_model_version"], "route-runtime-v2")
        self.assertEqual(quality_state["model_attribution"]["quality_verifier_version"], "quality-runtime-v2")


if __name__ == "__main__":
    unittest.main()
