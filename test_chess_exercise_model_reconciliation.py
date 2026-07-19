from __future__ import annotations

import unittest

from chess_exercise_model import ChessExerciseModel, build_chess_exercise_model


class ChessExerciseModelReconciliationTests(unittest.TestCase):
    def test_swapped_solution_ids_are_repaired_by_canonical_identity(self) -> None:
        model = build_chess_exercise_model(
            [
                {
                    "page_number": 33,
                    "blocks": [
                        {
                            "type": "exercise",
                            "exercise_id": "exercise-301",
                            "printed_number": 301,
                            "source_page": 33,
                            "difficulty": "**",
                        },
                        {
                            "type": "diagram",
                            "exercise_id": "exercise-301",
                            "diagram_id": "diagram-301",
                            "exercise_number": 301,
                            "caption": "Alpha - Beta, Oslo 2000",
                            "source_page": 33,
                            "board_crop_path": "assets/diagram-301.png",
                        },
                        {
                            "type": "exercise",
                            "exercise_id": "exercise-302",
                            "printed_number": 302,
                            "source_page": 33,
                            "difficulty": "***",
                        },
                        {
                            "type": "diagram",
                            "exercise_id": "exercise-302",
                            "diagram_id": "diagram-302",
                            "exercise_number": 302,
                            "caption": "Gamma - Delta, Paris 2001",
                            "source_page": 33,
                            "board_crop_path": "assets/diagram-302.png",
                        },
                    ],
                },
                {
                    "page_number": 210,
                    "blocks": [
                        {
                            "type": "solution",
                            "exercise_id": "exercise-302",
                            "solution_number": 301,
                            "solution_title": "Alpha - Beta, Oslo 2000",
                            "solution_page": 210,
                            "book_line": "1. Rxe6+!",
                            "pgn": "1. Rxe6+!",
                        },
                        {
                            "type": "solution",
                            "exercise_id": "exercise-301",
                            "solution_number": 302,
                            "solution_title": "Gamma - Delta, Paris 2001",
                            "solution_page": 210,
                            "book_line": "1... Qh2+!",
                            "pgn": "1... Qh2+!",
                        },
                    ],
                },
            ]
        )

        by_id = {exercise.exercise_id: exercise for exercise in model.exercises}
        self.assertEqual(by_id["exercise-301"].solution.raw_text, "1. Rxe6+!")
        self.assertEqual(by_id["exercise-302"].solution.raw_text, "1... Qh2+!")
        self.assertTrue(by_id["exercise-301"].solution_match["reassigned"])
        self.assertTrue(by_id["exercise-302"].solution_match["reassigned"])
        self.assertIn("SOLUTION_IDENTITY_REASSIGNED", {warning.code for warning in by_id["exercise-301"].warnings})

        report = model.to_dict()["solution_reconciliation"]
        self.assertEqual(report["summary"]["matched_count"], 2)
        self.assertFalse(report["summary"]["production_blocked"])
        self.assertEqual(len(report["exercise_identities"]), 2)
        self.assertEqual(len(report["solution_identities"]), 2)
        self.assertEqual(ChessExerciseModel.from_dict(model.to_dict()).to_json(), model.to_json())

    def test_solution_only_identifier_does_not_create_phantom_exercise(self) -> None:
        model = build_chess_exercise_model(
            [
                {
                    "page_number": 50,
                    "blocks": [
                        {
                            "type": "exercise",
                            "exercise_id": "exercise-500",
                            "printed_number": 500,
                            "raw_title": "A - B, Berlin 2001",
                            "source_page": 50,
                        },
                        {
                            "type": "solution",
                            "exercise_id": "solution-record-500",
                            "solution_number": 500,
                            "solution_title": "A - B, Berlin 2001",
                            "solution_page": 250,
                            "book_line": "1. Qh7+",
                        },
                    ],
                }
            ]
        )

        self.assertEqual([exercise.exercise_id for exercise in model.exercises], ["exercise-500"])
        self.assertEqual(model.exercises[0].solution.raw_text, "1. Qh7+")
        self.assertEqual(model.exercises[0].solution_match["selected_solution_id"], "solution-record-500")

    def test_title_mismatch_adds_error_and_blocks_model_report(self) -> None:
        model = build_chess_exercise_model(
            [
                {
                    "page_number": 40,
                    "blocks": [
                        {
                            "type": "exercise",
                            "exercise_id": "exercise-422",
                            "printed_number": 422,
                            "source_page": 40,
                        },
                        {
                            "type": "diagram",
                            "exercise_id": "exercise-422",
                            "diagram_id": "diagram-422",
                            "exercise_number": 422,
                            "caption": "Ivanchuk - Anand, Linares 1992",
                            "source_page": 40,
                            "board_crop_path": "assets/diagram-422.png",
                        },
                    ],
                },
                {
                    "page_number": 240,
                    "blocks": [
                        {
                            "type": "solution",
                            "exercise_id": "exercise-422",
                            "solution_number": 422,
                            "solution_title": "Ivanchuk - Anand, Linares 1993",
                            "solution_page": 240,
                            "book_line": "1. Qh7+",
                        }
                    ],
                },
            ]
        )

        exercise = model.exercises[0]
        warning_by_code = {warning.code: warning for warning in exercise.warnings}
        self.assertIn("SOLUTION_TITLE_MISMATCH", warning_by_code)
        self.assertEqual(warning_by_code["SOLUTION_TITLE_MISMATCH"].severity, "error")
        self.assertEqual(exercise.solution_match["status"], "mismatch")
        self.assertIsNone(exercise.solution)
        self.assertTrue(model.to_dict()["solution_reconciliation"]["summary"]["production_blocked"])


if __name__ == "__main__":
    unittest.main()
