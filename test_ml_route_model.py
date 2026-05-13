from __future__ import annotations

import unittest

from ml_route_model import build_route_decision, predict_route


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


if __name__ == "__main__":
    unittest.main()
