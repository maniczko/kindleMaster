from __future__ import annotations

import unittest

from chess_fen_hardening import (
    FEN_FULL_INFERRED_REVIEW_REQUIRED,
    FEN_FULL_MACHINE_ACCEPTED,
    machine_accept_fen,
    validate_fen_placement_detailed,
)


class ChessFenPlacementVsFullFenTests(unittest.TestCase):
    STARTING_PLACEMENT = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
    STARTING_FEN = f"{STARTING_PLACEMENT} w KQkq - 0 1"

    def test_placement_validation_accepts_board_placement_without_metadata(self) -> None:
        result = validate_fen_placement_detailed(self.STARTING_PLACEMENT)

        self.assertTrue(result.is_syntax_valid)
        self.assertEqual(result.normalized_placement, self.STARTING_PLACEMENT)
        self.assertEqual(result.errors, [])

    def test_placement_validation_rejects_invalid_rank_width(self) -> None:
        result = validate_fen_placement_detailed("9/8/8/8/8/8/8/4K2k")

        self.assertFalse(result.is_syntax_valid)
        self.assertIn("invalid_rank_width", {issue.code for issue in result.errors})

    def test_machine_accept_blocks_full_fen_when_side_to_move_is_inferred(self) -> None:
        result = machine_accept_fen(
            {
                "source": "deterministic",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": ["side_to_move_inferred"],
                "side_to_move_status": "inferred",
                "side_to_move_evidence": "inferred",
            },
            {"min_confidence": 0.90},
        )

        blocker_codes = {blocker["code"] for blocker in result["acceptance_blockers"]}
        trace = result["acceptance_trace"]

        self.assertEqual(result["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertEqual(result["fen_semantic_status"], FEN_FULL_INFERRED_REVIEW_REQUIRED)
        self.assertIn("side_to_move_inferred", blocker_codes)
        self.assertTrue(trace["placement_valid"])
        self.assertTrue(trace["full_fen_valid"])
        self.assertFalse(trace["metadata_known"])
        self.assertEqual(trace["side_to_move_source"], "inferred")

    def test_machine_accept_allows_explicit_side_marker_when_other_gates_pass(self) -> None:
        result = machine_accept_fen(
            {
                "source": "deterministic",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": ["side_to_move_marker_detected"],
                "side_to_move_status": "explicit",
                "side_to_move_evidence": "marker",
            },
            {"min_confidence": 0.90},
        )

        trace = result["acceptance_trace"]

        self.assertEqual(result["runtime_status"], "FEN_MACHINE_ACCEPTED")
        self.assertEqual(result["fen_semantic_status"], FEN_FULL_MACHINE_ACCEPTED)
        self.assertEqual(result["selected_value"], self.STARTING_FEN)
        self.assertTrue(trace["placement_valid"])
        self.assertTrue(trace["full_fen_valid"])
        self.assertTrue(trace["metadata_known"])
        self.assertEqual(trace["side_to_move_source"], "marker")


if __name__ == "__main__":
    unittest.main()
