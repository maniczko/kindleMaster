from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.retrain_all import run_retrain_all


class MlRetrainAllTests(unittest.TestCase):
    def test_blocks_before_training_when_dataset_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report = root / "reports" / "ml" / "retrain_all" / "report.json"
            dataset_path = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            dataset_path.parent.mkdir(parents=True)
            dataset_path.write_text("", encoding="utf-8")

            with patch(
                "scripts.retrain_all.build_ml_datasets",
                return_value={
                    "status": "insufficient_data",
                    "dataset_version": "20260703-120000-gap",
                    "dataset_readiness": {"status": "insufficient_data", "promotion_allowed": False},
                    "outputs": {"route_examples": str(dataset_path)},
                },
            ), patch("scripts.retrain_all.train_route_classifier") as train_mock:
                payload = run_retrain_all(
                    repo_root=root,
                    from_feedback=True,
                    evaluate=True,
                    promote_if_better=True,
                    dry_run=True,
                    report_path=report,
                )

            self.assertEqual(payload["status"], "blocked_dataset_not_ready")
            self.assertEqual(payload["promotion_status"], "blocked")
            self.assertTrue(report.exists())
            train_mock.assert_not_called()

    def test_dry_run_generates_report_without_mutating_current_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_model = root / "models" / "route_classifier_v1.json"
            dataset_path = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            current_model.parent.mkdir(parents=True)
            dataset_path.parent.mkdir(parents=True)
            current_model.write_text(json.dumps({"model_version": "current-v1"}), encoding="utf-8")
            dataset_path.write_text("{}", encoding="utf-8")

            def fake_train(**kwargs):
                model_path = Path(kwargs["model_path"])
                model_path.parent.mkdir(parents=True, exist_ok=True)
                model_path.write_text(json.dumps({"model_version": "candidate-v2"}), encoding="utf-8")
                return {
                    "status": "candidate_trained",
                    "model_path": str(model_path),
                    "metrics": {
                        "accuracy": 0.95,
                        "macro_f1": 0.95,
                        "promotion_gates": {"passed": True, "failures": []},
                    },
                }

            def fake_evaluate(**kwargs):
                model_path = str(kwargs["model_path"])
                if model_path == str(current_model):
                    return {"status": "evaluated", "accuracy": 0.8, "per_label": {}}
                return {"status": "evaluated", "accuracy": 0.95, "per_label": {}}

            with patch(
                "scripts.retrain_all.build_ml_datasets",
                return_value={
                    "status": "ready",
                    "dataset_version": "20260703-120000-ready",
                    "dataset_readiness": {"status": "ready", "promotion_allowed": True},
                    "outputs": {"route_examples": str(dataset_path)},
                },
            ), patch("scripts.retrain_all.train_route_classifier", side_effect=fake_train), patch(
                "scripts.retrain_all.evaluate_route_classifier", side_effect=fake_evaluate
            ), patch("scripts.retrain_all.promote_route_classifier") as promote_mock:
                payload = run_retrain_all(
                    repo_root=root,
                    evaluate=True,
                    promote_if_better=True,
                    dry_run=True,
                    current_model=current_model,
                )

            self.assertEqual(payload["status"], "dry_run")
            self.assertEqual(payload["promotion_status"], "dry_run")
            self.assertEqual(payload["before_model_version"], "current-v1")
            self.assertEqual(payload["candidate_model_version"], "candidate-v2")
            self.assertEqual(payload["metric_delta"]["accuracy_delta"], 0.15)
            self.assertEqual(json.loads(current_model.read_text(encoding="utf-8"))["model_version"], "current-v1")
            promote_mock.assert_not_called()

    def test_promotion_blocked_when_candidate_is_not_better(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_model = root / "models" / "route_classifier_v1.json"
            dataset_path = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            current_model.parent.mkdir(parents=True)
            dataset_path.parent.mkdir(parents=True)
            current_model.write_text(json.dumps({"model_version": "current-v1"}), encoding="utf-8")
            dataset_path.write_text("{}", encoding="utf-8")

            def fake_train(**kwargs):
                model_path = Path(kwargs["model_path"])
                model_path.parent.mkdir(parents=True, exist_ok=True)
                model_path.write_text(json.dumps({"model_version": "candidate-v2"}), encoding="utf-8")
                return {
                    "status": "candidate_trained",
                    "model_path": str(model_path),
                    "metrics": {"promotion_gates": {"passed": True, "failures": []}},
                }

            def fake_evaluate(**kwargs):
                if str(kwargs["model_path"]) == str(current_model):
                    return {"status": "evaluated", "accuracy": 0.9, "per_label": {}}
                return {"status": "evaluated", "accuracy": 0.88, "per_label": {}}

            with patch(
                "scripts.retrain_all.build_ml_datasets",
                return_value={
                    "status": "ready",
                    "dataset_version": "20260703-120000-ready",
                    "dataset_readiness": {"status": "ready", "promotion_allowed": True},
                    "outputs": {"route_examples": str(dataset_path)},
                },
            ), patch("scripts.retrain_all.train_route_classifier", side_effect=fake_train), patch(
                "scripts.retrain_all.evaluate_route_classifier", side_effect=fake_evaluate
            ):
                payload = run_retrain_all(
                    repo_root=root,
                    evaluate=True,
                    promote_if_better=True,
                    current_model=current_model,
                )

            self.assertEqual(payload["status"], "promotion_blocked")
            self.assertIn("candidate_not_better", payload["promotion_decision"]["reasons"])
            self.assertEqual(payload["metric_delta"]["accuracy_delta"], -0.02)

    def test_promoted_path_writes_rollback_snapshot_before_model_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            current_model = root / "models" / "route_classifier_v1.json"
            corpus_report = root / "reports" / "corpus" / "premium_corpus_smoke_report.json"
            dataset_path = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            current_model.parent.mkdir(parents=True)
            corpus_report.parent.mkdir(parents=True)
            dataset_path.parent.mkdir(parents=True)
            current_model.write_text(json.dumps({"model_version": "current-v1"}), encoding="utf-8")
            corpus_report.write_text(json.dumps({"overall_status": "passed"}), encoding="utf-8")
            dataset_path.write_text("{}", encoding="utf-8")

            def fake_train(**kwargs):
                model_path = Path(kwargs["model_path"])
                model_path.parent.mkdir(parents=True, exist_ok=True)
                model_path.write_text(json.dumps({"model_version": "candidate-v2"}), encoding="utf-8")
                return {
                    "status": "candidate_trained",
                    "model_path": str(model_path),
                    "metrics": {"promotion_gates": {"passed": True, "failures": []}},
                }

            def fake_evaluate(**kwargs):
                if str(kwargs["model_path"]) == str(current_model):
                    return {"status": "evaluated", "accuracy": 0.8, "per_label": {}}
                return {"status": "evaluated", "accuracy": 0.95, "per_label": {}}

            def fake_promote(**kwargs):
                Path(kwargs["model_path"]).write_text(json.dumps({"model_version": "candidate-v2"}), encoding="utf-8")
                return {
                    "status": "promoted",
                    "candidate_path": str(kwargs["candidate_path"]),
                    "model_path": str(kwargs["model_path"]),
                    "metric_gates": {"passed": True},
                    "corpus_gate": {"passed": True, "status": "passed"},
                }

            with patch(
                "scripts.retrain_all.build_ml_datasets",
                return_value={
                    "status": "ready",
                    "dataset_version": "20260703-120000-ready",
                    "dataset_readiness": {"status": "ready", "promotion_allowed": True},
                    "outputs": {"route_examples": str(dataset_path)},
                },
            ), patch("scripts.retrain_all.train_route_classifier", side_effect=fake_train), patch(
                "scripts.retrain_all.evaluate_route_classifier", side_effect=fake_evaluate
            ), patch("scripts.retrain_all.promote_route_classifier", side_effect=fake_promote):
                payload = run_retrain_all(
                    repo_root=root,
                    evaluate=True,
                    promote_if_better=True,
                    current_model=current_model,
                    corpus_report=corpus_report,
                )

            rollback = Path(payload["rollback_snapshot"])
            self.assertEqual(payload["status"], "promoted")
            self.assertTrue(rollback.exists())
            self.assertEqual(json.loads(rollback.read_text(encoding="utf-8"))["model_version"], "current-v1")
            self.assertEqual(json.loads(current_model.read_text(encoding="utf-8"))["model_version"], "candidate-v2")


if __name__ == "__main__":
    unittest.main()
