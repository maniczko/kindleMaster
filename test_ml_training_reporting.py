from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml_features import ROUTE_MODEL_FEATURE_ORDER, normalize_route_features
from scripts.train_route_classifier import evaluate_route_classifier, train_route_classifier


class MlTrainingReportingTests(unittest.TestCase):
    def test_training_reports_unavailable_when_sklearn_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = root / "route_examples.jsonl"
            report = root / "metrics.json"
            model = root / "route_model.json"
            dataset.write_text(
                json.dumps(
                    {
                        "case_id": "book",
                        "label": "book_reflow",
                        "features": normalize_route_features({"input_type": "pdf", "page_count": 10}),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with patch(
                "scripts.train_route_classifier._import_sklearn_training_dependencies",
                return_value={"status": "unavailable", "exception": "No module named 'sklearn'"},
            ):
                payload = train_route_classifier(dataset_path=dataset, model_path=model, report_path=report)

            saved = json.loads(report.read_text(encoding="utf-8"))

        self.assertEqual(payload["status"], "training_unavailable")
        self.assertEqual(saved["dependency"], "scikit-learn")
        self.assertEqual(saved["example_count"], 1)
        self.assertFalse(saved["online_learning"])

    def test_evaluation_reports_per_label_and_misclassification_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = root / "route_examples.jsonl"
            model = root / "route_model.json"
            report = root / "evaluation.json"
            rows = [
                {
                    "case_id": "book",
                    "document_class": "business_report",
                    "label": "book_reflow",
                    "features": normalize_route_features({"input_type": "pdf", "page_count": 10, "text_heavy": True}),
                },
                {
                    "case_id": "magazine",
                    "document_class": "magazine",
                    "label": "magazine_reflow",
                    "features": normalize_route_features({"input_type": "pdf", "page_count": 10, "layout_heavy": True}),
                },
            ]
            dataset.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            model.write_text(
                json.dumps(
                    {
                        "model_type": "multinomial_logistic_regression",
                        "model_version": "unit",
                        "classes": ["book_reflow", "magazine_reflow"],
                        "feature_order": list(ROUTE_MODEL_FEATURE_ORDER),
                        "scaler": {
                            "mean": [0.0] * len(ROUTE_MODEL_FEATURE_ORDER),
                            "scale": [1.0] * len(ROUTE_MODEL_FEATURE_ORDER),
                        },
                        "weights": {
                            "book_reflow": [0.0] * len(ROUTE_MODEL_FEATURE_ORDER),
                            "magazine_reflow": [0.0] * len(ROUTE_MODEL_FEATURE_ORDER),
                        },
                        "intercepts": {"book_reflow": 1.0, "magazine_reflow": 0.0},
                    }
                ),
                encoding="utf-8",
            )

            payload = evaluate_route_classifier(dataset_path=dataset, model_path=model, report_path=report)

        self.assertEqual(payload["status"], "evaluated")
        self.assertEqual(payload["accuracy"], 0.5)
        self.assertEqual(payload["per_label"]["magazine_reflow"]["recall"], 0.0)
        self.assertEqual(payload["per_document_class"]["magazine"]["accuracy"], 0.0)
        self.assertEqual(payload["warnings"][0]["expected"], "magazine_reflow")
        self.assertEqual(payload["confusion_counts"]["magazine_reflow->book_reflow"], 1)


if __name__ == "__main__":
    unittest.main()
