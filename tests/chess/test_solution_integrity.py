from __future__ import annotations

import unittest

from chess_solution_integrity import (
    SOLUTION_INTEGRITY_SCHEMA,
    analyze_solution_integrity,
    analyze_solution_integrity_records,
)


class ChessSolutionIntegrityTests(unittest.TestCase):
    def test_complete_white_and_black_solutions_are_accepted(self) -> None:
        white = analyze_solution_integrity(
            exercise_id="224",
            exercise_number=224,
            source_page=80,
            solution_page=210,
            text="23. Rxe6+! fxe6 24. Qxe6+ Kf8",
            expected_side_to_move="white",
        )
        black = analyze_solution_integrity(
            exercise_id="225",
            exercise_number=225,
            source_page=80,
            solution_page=210,
            text="33...e3! 34. Rxe3 Rxe3",
            expected_side_to_move="black",
        )

        self.assertEqual(white.status, "accepted")
        self.assertEqual(white.detected_side_to_move, "white")
        self.assertEqual(black.status, "accepted")
        self.assertEqual(black.detected_side_to_move, "black")

    def test_missing_first_move_number_is_reported_and_blocks_strict_mode(self) -> None:
        record = analyze_solution_integrity(
            exercise_id="226",
            exercise_number=226,
            source_page=81,
            solution_page=211,
            text="Rxe6+! fxe6 24. Qxe6+",
            expected_side_to_move="white",
        )

        self.assertEqual(record.status, "blocked")
        self.assertIn("MISSING_FIRST_MOVE_NUMBER", {item.code for item in record.findings})
        report = analyze_solution_integrity_records(
            [
                {
                    "exercise_id": "226",
                    "exercise_number": 226,
                    "source_page": 81,
                    "solution_page": 211,
                    "text": "Rxe6+! fxe6 24. Qxe6+",
                    "side_to_move": "white",
                }
            ]
        )
        self.assertEqual(report.schema, SOLUTION_INTEGRITY_SCHEMA)
        self.assertEqual(report.exit_code("warning"), 0)
        self.assertEqual(report.exit_code("strict"), 1)

    def test_side_to_move_and_move_number_mismatch_are_blocking(self) -> None:
        record = analyze_solution_integrity(
            exercise_id="227",
            exercise_number=227,
            source_page=81,
            solution_page=211,
            text="24...e3! 25. Rxe3",
            expected_side_to_move="white",
            expected_first_move_number=23,
        )

        codes = {item.code for item in record.findings}
        self.assertEqual(record.status, "blocked")
        self.assertIn("SIDE_TO_MOVE_MISMATCH", codes)
        self.assertIn("FIRST_MOVE_NUMBER_MISMATCH", codes)

    def test_variation_or_continuation_start_is_reported(self) -> None:
        variation = analyze_solution_integrity(
            exercise_id="variation",
            text="(23...fxe6 24. Qxe6+) 24. Rxe6",
            expected_side_to_move="white",
        )
        continuation = analyze_solution_integrity(
            exercise_id="continuation",
            text="...e3! 34. Rxe3",
            expected_side_to_move="black",
        )

        self.assertIn("SOLUTION_STARTS_INSIDE_VARIATION", {item.code for item in variation.findings})
        self.assertIn("SOLUTION_CONTINUATION_START", {item.code for item in continuation.findings})

    def test_commentary_before_numbered_move_is_warning_only(self) -> None:
        record = analyze_solution_integrity(
            exercise_id="commentary",
            text="{The tactical point is the pin.} 23. Rxe6+! fxe6",
            expected_side_to_move="white",
        )

        self.assertEqual(record.status, "warning")
        self.assertFalse(record.strict_blocked)
        self.assertIn("COMMENTARY_BEFORE_FIRST_MOVE", {item.code for item in record.findings})

    def test_short_solution_is_flagged_without_claiming_truncation(self) -> None:
        record = analyze_solution_integrity(
            exercise_id="mate",
            text="12. Qh7#",
            expected_side_to_move="white",
        )

        self.assertEqual(record.status, "warning")
        self.assertIn("SUSPICIOUSLY_SHORT_SOLUTION", {item.code for item in record.findings})
        self.assertFalse(record.strict_blocked)

    def test_report_contains_required_context_and_200_character_excerpt(self) -> None:
        long_text = "23. Rxe6+! " + ("commentary " * 40)
        report = analyze_solution_integrity_records(
            [
                {
                    "exercise_id": "context",
                    "exercise_number": 99,
                    "source_page": 12,
                    "solution_page": 200,
                    "text": long_text,
                    "side_to_move": "white",
                }
            ]
        )
        item = report.to_dict()["records"][0]

        self.assertEqual(item["exercise_number"], 99)
        self.assertEqual(item["source_page"], 12)
        self.assertEqual(item["solution_page"], 200)
        self.assertEqual(len(item["excerpt"]), 200)

    def test_unbalanced_variation_is_blocking(self) -> None:
        record = analyze_solution_integrity(
            exercise_id="unbalanced",
            text="23. Rxe6+! (23...fxe6 24. Qxe6+",
            expected_side_to_move="white",
        )

        self.assertIn("UNBALANCED_VARIATION", {item.code for item in record.findings})
        self.assertTrue(record.strict_blocked)

    def test_invalid_mode_is_rejected(self) -> None:
        report = analyze_solution_integrity_records([])
        with self.assertRaises(ValueError):
            report.exit_code("production")


if __name__ == "__main__":
    unittest.main()
