from __future__ import annotations

import unittest
from pathlib import Path

from chess_exercise_model import CHESS_EXERCISE_MODEL_SCHEMA, ChessExerciseModel, build_chess_exercise_model


class ChessStudyDataContractsTests(unittest.TestCase):
    def test_semantic_exercise_model_round_trips_and_keeps_source_evidence(self) -> None:
        model = build_chess_exercise_model(
            [
                {
                    "page_number": 12,
                    "blocks": [
                        {
                            "type": "exercise",
                            "exercise_id": "ex_12_9",
                            "diagram_id": "diagram-12-9",
                            "source_page": 12,
                            "source_column": 2,
                            "bounding_box": [10, 20, 110, 120],
                            "difficulty": "**",
                        },
                        {
                            "type": "diagram",
                            "exercise_id": "ex_12_9",
                            "diagram_id": "diagram-12-9",
                            "caption": "Diagram  12-9",
                            "source_page": 12,
                            "board_crop_path": "C:/private/run/assets/diagram_crops/diagram-12-9.png",
                            "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
                            "fen_confidence": 0.97,
                            "side_to_move": "white",
                            "review_status": "verified",
                        },
                        {
                            "type": "solution",
                            "exercise_id": "ex_12_9",
                            "diagram_id": "diagram-12-9",
                            "solution_page": 98,
                            "book_line": "23.Rxe6+!  fxe6",
                            "pgn": "23. Rxe6+! fxe6",
                            "best_move": "Rxe6+",
                        },
                    ],
                }
            ]
        )

        payload = model.to_dict()
        self.assertEqual(payload["schema"], CHESS_EXERCISE_MODEL_SCHEMA)
        self.assertEqual(payload["summary"]["exercise_count"], 1)
        self.assertEqual(payload["exercises"][0]["source"]["column"], 2)
        self.assertEqual(payload["exercises"][0]["diagram"]["image_path"], "assets/diagram_crops/diagram-12-9.png")
        self.assertEqual(payload["exercises"][0]["solution"]["raw_text"], "23.Rxe6+!  fxe6")
        self.assertEqual(ChessExerciseModel.from_dict(payload).to_json(), model.to_json())

    def test_semantic_exercise_model_flags_missing_components_and_rejects_unidentified_blocks(self) -> None:
        model = build_chess_exercise_model(
            [
                {
                    "page_number": 7,
                    "blocks": [
                        {"type": "exercise", "exercise_id": "ex_7_1", "source_page": 7},
                        {"type": "exercise", "exercise_id": "ex_7_1", "source_page": 8},
                        {"type": "diagram", "diagram_id": "orphan", "source_page": 7},
                    ],
                },
                {"blocks": [{"type": "exercise", "exercise_id": "ex_missing_page"}]},
            ]
        )

        self.assertEqual([item.exercise_id for item in model.exercises], ["ex_7_1"])
        self.assertEqual({warning.code for warning in model.exercises[0].warnings}, {"MISSING_DIAGRAM", "MISSING_SOLUTION"})
        self.assertEqual(
            {warning.code for warning in model.warnings},
            {"DUPLICATE_EXERCISE_COMPONENT", "MISSING_EXERCISE_ID", "MISSING_SOURCE_LOCATION"},
        )

    def test_data_contract_document_names_required_artifacts_and_policy(self) -> None:
        doc = Path("docs/chess-study-data-contracts.md").read_text(encoding="utf-8")

        self.assertIn("data/fen_square_dataset.jsonl", doc)
        self.assertIn("data/board_preprocess.jsonl", doc)
        self.assertIn("review/fen_model_predictions.jsonl", doc)
        self.assertIn("reports/fen_ensemble_eval.json", doc)
        self.assertIn("AI, preprocessing, template matching, and local classifiers create candidates only", doc)
        self.assertIn("reports/chess_reader/chess_exercises.json", doc)


if __name__ == "__main__":
    unittest.main()
