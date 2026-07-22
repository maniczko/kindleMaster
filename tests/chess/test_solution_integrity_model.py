from __future__ import annotations

import unittest

from chess_exercise_model import ChessExerciseModel, build_chess_exercise_model


class ChessSolutionIntegrityModelTests(unittest.TestCase):
    def _build(self, *, solution_text: str, side_to_move: str = "white") -> ChessExerciseModel:
        return build_chess_exercise_model(
            [
                {
                    "page_number": 80,
                    "blocks": [
                        {
                            "type": "exercise",
                            "exercise_id": "exercise-224",
                            "printed_number": 224,
                            "source_page": 80,
                            "difficulty": "**",
                        },
                        {
                            "type": "diagram",
                            "exercise_id": "exercise-224",
                            "diagram_id": "diagram-224",
                            "exercise_number": 224,
                            "caption": "Alpha - Beta, Oslo 2000",
                            "source_page": 80,
                            "side_to_move": side_to_move,
                            "side_to_move_confidence": 0.99,
                            "board_crop_path": "assets/diagram-224.png",
                        },
                    ],
                },
                {
                    "page_number": 210,
                    "blocks": [
                        {
                            "type": "solution",
                            "exercise_id": "exercise-224",
                            "solution_number": 224,
                            "solution_title": "Alpha - Beta, Oslo 2000",
                            "solution_page": 210,
                            "book_line": solution_text,
                            "pgn": solution_text,
                        }
                    ],
                },
            ]
        )

    def test_complete_solution_integrity_is_attached_to_model(self) -> None:
        model = self._build(solution_text="23. Rxe6+! fxe6 24. Qxe6+ Kf8")
        exercise = model.exercises[0]

        self.assertEqual(exercise.solution_integrity["status"], "accepted")
        self.assertEqual(exercise.solution_integrity["exercise_number"], 224)
        report = model.to_dict()["solution_integrity"]
        self.assertEqual(report["summary"]["accepted_count"], 1)
        self.assertEqual(report["summary"]["strict_exit_code"], 0)
        self.assertEqual(ChessExerciseModel.from_dict(model.to_dict()).to_json(), model.to_json())

    def test_side_to_move_mismatch_adds_error_and_strict_blocker(self) -> None:
        model = self._build(solution_text="23...e3! 24. Rxe3", side_to_move="white")
        exercise = model.exercises[0]
        warnings = {warning.code: warning for warning in exercise.warnings}

        self.assertEqual(exercise.solution_integrity["status"], "blocked")
        self.assertIn("SIDE_TO_MOVE_MISMATCH", warnings)
        self.assertEqual(warnings["SIDE_TO_MOVE_MISMATCH"].severity, "error")
        self.assertEqual(model.to_dict()["solution_integrity"]["summary"]["strict_exit_code"], 1)

    def test_missing_move_number_is_warning_in_model_but_blocks_strict_gate(self) -> None:
        model = self._build(solution_text="Rxe6+! fxe6 24. Qxe6+")
        exercise = model.exercises[0]
        warnings = {warning.code: warning for warning in exercise.warnings}

        self.assertIn("MISSING_FIRST_MOVE_NUMBER", warnings)
        self.assertEqual(warnings["MISSING_FIRST_MOVE_NUMBER"].severity, "warning")
        self.assertTrue(exercise.solution_integrity["strict_blocked"])
        self.assertEqual(model.to_dict()["solution_integrity"]["summary"]["warning_exit_code"], 0)
        self.assertEqual(model.to_dict()["solution_integrity"]["summary"]["strict_exit_code"], 1)

    def test_reader_item_exposes_integrity_status_and_findings(self) -> None:
        model = self._build(solution_text="(23...fxe6 24. Qxe6+) 24. Rxe6")
        payload = model.to_dict()["exercises"][0]

        from chess_exercise_model import exercise_to_reader_item

        item = exercise_to_reader_item(payload)
        self.assertEqual(item["solution_integrity_status"], "blocked")
        self.assertIn("SOLUTION_STARTS_INSIDE_VARIATION", item["solution_integrity_findings"])


if __name__ == "__main__":
    unittest.main()
