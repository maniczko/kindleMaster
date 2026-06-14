from __future__ import annotations

import unittest

from chess_fen_hardening import (
    compare_fen,
    compare_fen_placements,
    fen_placement_to_square_map,
    piece_name,
    render_square_diff_html,
    render_square_diff_json,
    render_square_diff_text,
)


EXPECTED_ROOK_E5_FEN = "4k3/8/8/4r3/8/8/8/4K3 w - - 0 1"
CANDIDATE_PAWN_E5_FEN = "4k3/8/8/4p3/8/8/8/4K3 w - - 0 1"
CANDIDATE_EMPTY_E5_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


class ChessFenSquareDiffTests(unittest.TestCase):
    def test_pawn_vs_rook_on_e5(self) -> None:
        diffs = compare_fen_placements(CANDIDATE_PAWN_E5_FEN, EXPECTED_ROOK_E5_FEN)

        self.assertEqual(
            diffs,
            [
                {
                    "square": "e5",
                    "candidate_piece": "black pawn",
                    "manual_piece": "black rook",
                    "candidate_fen_char": "p",
                    "manual_fen_char": "r",
                    "severity": "critical",
                    "reason": "piece_mismatch",
                    "expected_piece": "r",
                    "actual_piece": "p",
                }
            ],
        )
        self.assertEqual(render_square_diff_text("p010_d002", diffs), ["p010_d002: e5 black rook, not black pawn"])

    def test_empty_vs_piece_on_e5(self) -> None:
        diffs = compare_fen_placements(CANDIDATE_EMPTY_E5_FEN, EXPECTED_ROOK_E5_FEN)

        self.assertEqual(render_square_diff_text("p010_d002", diffs), ["p010_d002: e5 black rook, not empty square"])
        self.assertEqual(diffs[0]["reason"], "missing_piece")

    def test_piece_vs_empty_on_e5(self) -> None:
        diffs = compare_fen_placements(CANDIDATE_PAWN_E5_FEN, CANDIDATE_EMPTY_E5_FEN)

        self.assertEqual(render_square_diff_text("p010_d002", diffs), ["p010_d002: e5 empty square, not black pawn"])
        self.assertEqual(diffs[0]["reason"], "extra_piece")

    def test_side_to_move_only_difference_is_not_square_diff(self) -> None:
        candidate = "4k3/8/8/4r3/8/8/8/4K3 b - - 0 1"
        expected = "4k3/8/8/4r3/8/8/8/4K3 w - - 0 1"

        self.assertEqual(compare_fen_placements(candidate, expected), [])
        comparison = compare_fen(candidate, expected)
        self.assertEqual(comparison["placement_diffs"], [])
        self.assertEqual(
            comparison["side_to_move_diff"],
            {"candidate": "b", "manual": "w", "severity": "low", "reason": "side_to_move_mismatch"},
        )

    def test_invalid_candidate_fen_is_explicit_error(self) -> None:
        with self.assertRaises(ValueError):
            fen_placement_to_square_map("4k3/8/8/4p3/8/8/4K3 w - - 0 1")

        comparison = compare_fen("4k3/8/8/4p3/8/8/4K3 w - - 0 1", EXPECTED_ROOK_E5_FEN)
        self.assertEqual(comparison["placement_diffs"], [])
        self.assertEqual(comparison["errors"][0]["field"], "candidate_fen")
        self.assertEqual(comparison["errors"][0]["code"], "invalid_fen_placement")

    def test_invalid_expected_fen_is_explicit_error(self) -> None:
        comparison = compare_fen(CANDIDATE_PAWN_E5_FEN, "4k3/8/8/4r3/8/8/4K3 w - - 0 1")

        self.assertEqual(comparison["placement_diffs"], [])
        self.assertEqual(comparison["errors"][0]["field"], "expected_fen")

    def test_exact_match_has_no_diffs_or_errors(self) -> None:
        comparison = compare_fen(EXPECTED_ROOK_E5_FEN, EXPECTED_ROOK_E5_FEN)

        self.assertEqual(comparison["placement_diffs"], [])
        self.assertIsNone(comparison["side_to_move_diff"])
        self.assertEqual(comparison["metadata_diffs"], [])
        self.assertEqual(comparison["errors"], [])

    def test_piece_name_helper_all_pieces(self) -> None:
        expected = {
            "P": "white pawn",
            "N": "white knight",
            "B": "white bishop",
            "R": "white rook",
            "Q": "white queen",
            "K": "white king",
            "p": "black pawn",
            "n": "black knight",
            "b": "black bishop",
            "r": "black rook",
            "q": "black queen",
            "k": "black king",
            "": "empty square",
        }
        for symbol, name in expected.items():
            self.assertEqual(piece_name(symbol), name)

    def test_json_and_html_renderers_escape_values(self) -> None:
        diffs = [
            {
                "square": "e5<script>",
                "candidate_piece": "black pawn",
                "manual_piece": "black rook",
                "candidate_fen_char": "p",
                "manual_fen_char": "r",
                "severity": "critical",
                "reason": "piece_mismatch<script>",
                "expected_piece": "r",
                "actual_piece": "p",
            }
        ]

        self.assertEqual(render_square_diff_json("id", diffs), {"id": "id", "diffs": diffs})
        html = render_square_diff_html('p010"d002', diffs)
        self.assertIn("p010&quot;d002", html)
        self.assertIn("e5&lt;script&gt;", html)
        self.assertNotIn("e5<script>", html)


if __name__ == "__main__":
    unittest.main()
