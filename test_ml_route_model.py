from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml_route_model import build_route_decision, predict_route
from scripts.train_route_classifier import promote_route_classifier, train_route_classifier


class MlRouteModelTests(unittest.TestCase):
    def _model(self) -> dict:
        return {
            "model_version": "test-route-model",
            "model_type": "multinomial_logistic_regression",
            "feature_order": ["input_type=pdf", "text_heavy", "layout_heavy", "has_diagrams", "scanned_page_ratio"],
            "classes": ["book_reflow", "magazine_reflow", "diagram_book_reflow"],
            "intercepts": {
                "book_reflow": 0.0,
                "magazine_reflow": -0.4,
                "diagram_book_reflow": -0.5,
            },
            "weights": {
                "book_reflow": [0.5, 3.0, -1.0, -1.0, -2.0],
                "magazine_reflow": [0.5, -1.0, 4.0, -1.0, -1.0],
                "diagram_book_reflow": [0.5, 0.5, 0.0, 6.0, -1.0],
            },
            "thresholds": {
                "assist_confidence": 0.82,
                "max_heuristic_confidence_for_override": 0.7,
                "protected_classes": ["diagram_book_reflow", "scanned_reflow"],
            },
        }

    def test_json_inference_predicts_without_runtime_sklearn(self) -> None:
        prediction = predict_route(
            {
                "input_type": "pdf",
                "text_heavy": False,
                "layout_heavy": True,
                "has_diagrams": False,
                "scanned_page_ratio": 0.0,
            },
            model=self._model(),
        )

        self.assertEqual(prediction["profile"], "magazine_reflow")
        self.assertGreater(prediction["confidence"], 0.82)
        self.assertEqual(prediction["model_version"], "test-route-model")

    def test_shadow_mode_reports_ml_but_keeps_heuristic_profile(self) -> None:
        decision = build_route_decision(
            heuristic_profile="book_reflow",
            heuristic_confidence=0.55,
            features={"input_type": "pdf", "layout_heavy": True, "text_heavy": False, "has_diagrams": False},
            mode="shadow",
            model=self._model(),
        )

        self.assertEqual(decision["ml_profile"], "magazine_reflow")
        self.assertEqual(decision["selected_profile"], "book_reflow")
        self.assertFalse(decision["override_used"])

    def test_assist_mode_overrides_only_when_thresholds_pass(self) -> None:
        decision = build_route_decision(
            heuristic_profile="book_reflow",
            heuristic_confidence=0.55,
            features={"input_type": "pdf", "layout_heavy": True, "text_heavy": False, "has_diagrams": False},
            mode="assist",
            model=self._model(),
        )

        self.assertEqual(decision["selected_profile"], "magazine_reflow")
        self.assertTrue(decision["override_used"])

    def test_assist_protects_diagram_route_without_signal(self) -> None:
        model = self._model()
        model["intercepts"]["diagram_book_reflow"] = 5.0
        decision = build_route_decision(
            heuristic_profile="book_reflow",
            heuristic_confidence=0.55,
            features={"input_type": "pdf", "layout_heavy": False, "text_heavy": True, "has_diagrams": False},
            mode="assist",
            model=model,
        )

        self.assertEqual(decision["selected_profile"], "book_reflow")
        self.assertFalse(decision["override_used"])
        self.assertIn("protected-class-without-signal:diagram_book_reflow", decision["reason_codes"])

    def test_train_blocks_when_dataset_readiness_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_path = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            report_path = root / "reports" / "ml" / "route_classifier.metrics.json"
            dataset_path.parent.mkdir(parents=True)
            dataset_path.write_text(
                json.dumps(
                    {
                        "case_id": "only-book",
                        "label": "book_reflow",
                        "features": {"input_type": "pdf", "text_heavy": True, "layout_heavy": False},
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "scripts.train_route_classifier._import_sklearn_training_dependencies",
                return_value={"status": "available"},
            ):
                payload = train_route_classifier(
                    dataset_path=dataset_path,
                    report_path=report_path,
                    min_examples_per_class=2,
                )

            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"], "dataset_not_ready")
            self.assertEqual(payload["dataset_readiness"]["status"], "insufficient_data")
            self.assertTrue(report_path.exists())

    def test_train_reports_unavailable_before_dataset_readiness_when_dependency_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset_path = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            report_path = root / "reports" / "ml" / "route_classifier.metrics.json"
            dataset_path.parent.mkdir(parents=True)
            dataset_path.write_text(
                json.dumps(
                    {
                        "case_id": "only-book",
                        "label": "book_reflow",
                        "features": {"input_type": "pdf", "text_heavy": True, "layout_heavy": False},
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "scripts.train_route_classifier._import_sklearn_training_dependencies",
                return_value={"status": "missing", "exception": "no sklearn"},
            ):
                payload = train_route_classifier(
                    dataset_path=dataset_path,
                    report_path=report_path,
                    min_examples_per_class=2,
                )

            self.assertEqual(payload["status"], "training_unavailable")
            self.assertEqual(payload["dependency"], "scikit-learn")
            self.assertTrue(report_path.exists())

    def test_promote_blocks_low_metric_candidate_without_overwriting_runtime_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate_path = root / "models" / "candidates" / "route_classifier_bad.json"
            target_path = root / "models" / "route_classifier_v1.json"
            corpus_path = root / "reports" / "corpus" / "premium_corpus_smoke_report.json"
            candidate_path.parent.mkdir(parents=True)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            corpus_path.parent.mkdir(parents=True)
            target_path.write_text('{"model_version":"existing"}', encoding="utf-8")
            corpus_path.write_text(json.dumps({"overall_status": "passed", "failed_routes": []}), encoding="utf-8")
            candidate_path.write_text(
                json.dumps(
                    {
                        "model_version": "candidate-bad",
                        "metrics": {
                            "accuracy": 0.4,
                            "macro_f1": 0.4,
                            "per_class_recall": {
                                "scanned_reflow": 0.2,
                                "diagram_book_reflow": 0.2,
                            },
                            "dataset_readiness": {"status": "ready"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            payload = promote_route_classifier(
                candidate_path=candidate_path,
                model_path=target_path,
                corpus_report_path=corpus_path,
            )

            self.assertEqual(payload["status"], "blocked")
            self.assertIn("holdout_accuracy_below_threshold", payload["metric_gates"]["failures"])
            self.assertEqual(json.loads(target_path.read_text(encoding="utf-8"))["model_version"], "existing")

    def test_promote_blocks_corpus_hard_negative_route_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidate_path = root / "models" / "candidates" / "route_classifier_good.json"
            target_path = root / "models" / "route_classifier_v1.json"
            corpus_path = root / "reports" / "corpus" / "premium_corpus_smoke_report.json"
            candidate_path.parent.mkdir(parents=True)
            corpus_path.parent.mkdir(parents=True)
            candidate_path.write_text(
                json.dumps(
                    {
                        "model_version": "candidate-good",
                        "metrics": {
                            "accuracy": 0.91,
                            "macro_f1": 0.9,
                            "per_class_recall": {
                                "scanned_reflow": 0.83,
                                "diagram_book_reflow": 0.84,
                            },
                            "dataset_readiness": {"status": "ready"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            corpus_path.write_text(
                json.dumps(
                    {
                        "overall_status": "passed_with_warnings",
                        "cases": [
                            {
                                "case_id": "magazine-fixture",
                                "document_class": "magazine-layout",
                                "focus_routes": ["magazine_layout_heavy"],
                                "grade": "fail",
                                "output_assertions": [
                                    {
                                        "id": "layout_output_has_visual_evidence",
                                        "route": "magazine_layout_heavy",
                                        "status": "failed",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = promote_route_classifier(
                candidate_path=candidate_path,
                model_path=target_path,
                corpus_report_path=corpus_path,
            )

            self.assertEqual(payload["status"], "blocked")
            self.assertTrue(payload["metric_gates"]["passed"])
            self.assertFalse(payload["corpus_gate"]["passed"])
            self.assertEqual(payload["corpus_gate"]["hard_negative_failures"][0]["routes"], ["magazine_layout_heavy"])
            self.assertFalse(target_path.exists())


if __name__ == "__main__":
    unittest.main()
