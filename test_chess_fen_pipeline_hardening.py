from __future__ import annotations

import unittest


class ChessFenPipelineHardeningTests(unittest.TestCase):
    def test_machine_accept_fen_blocks_inferred_side_to_move(self) -> None:
        from chess_fen_ml_acceptance import machine_accept_fen

        self.assertFalse(
            machine_accept_fen(
                {
                    "fen": "8/8/8/3k4/8/8/4K3/8 w - - 0 1",
                    "warnings": ["side_to_move_inferred"],
                    "requires_review": False,
                    "side_to_move_status": "inferred",
                    "side_to_move_evidence": "inferred",
                }
            )
        )

    def test_machine_accept_fen_allows_explicit_side_to_move_marker(self) -> None:
        from chess_fen_ml_acceptance import machine_accept_fen

        self.assertTrue(
            machine_accept_fen(
                {
                    "fen": "8/8/8/3k4/8/8/4K3/8 b - - 0 1",
                    "warnings": ["side_to_move_marker_detected"],
                    "requires_review": False,
                    "side_to_move_status": "explicit",
                    "side_to_move_evidence": "marker",
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
