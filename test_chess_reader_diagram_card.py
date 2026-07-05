from __future__ import annotations

import unittest

from chess_study_export import _semantic_source_diagram_html


class ChessReaderDiagramCardTests(unittest.TestCase):
    def test_available_fen_renders_board_and_copy_fen(self) -> None:
        html = _semantic_source_diagram_html(
            {
                "id": "p010_d01",
                "caption": "Diagram 1-3",
                "players": "Gast - E. Bhend",
                "source": "Berne 1987",
                "page": 10,
                "side_to_move": "b",
                "validation_status": "accepted",
                "fen": "8/8/8/8/8/8/4K3/4k3 b - - 0 1",
                "pgn": "1... Qh4+ *",
                "board_crop_path": "assets/diagrams/p010_d01.png",
                "original_crop_path": "assets/original/p010_d01.png",
            }
        )

        self.assertIn('class="diagram-card"', html)
        self.assertIn("Gast - E. Bhend, Berne 1987", html)
        self.assertIn("Black to move", html)
        self.assertIn("mini-board", html)
        self.assertIn("Copy FEN", html)
        self.assertIn("Copy PGN", html)
        self.assertIn("Show original crop", html)
        self.assertNotIn("board-crop-fallback", html)
        self.assertNotIn("fen_not_recognized", html)

    def test_unavailable_fen_uses_crop_fallback_and_disables_copy_fen(self) -> None:
        html = _semantic_source_diagram_html(
            {
                "id": "p010_d02",
                "caption": "Diagram 1-4",
                "page": 10,
                "side_to_move": "",
                "validation_status": "needs-human-review",
                "fen": "",
                "fen_candidate": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                "review_reason": "fen_not_recognized side_to_move_unknown",
                "board_crop_path": "assets/diagrams/p010_d02.png",
                "book_line": "1... Ne2+ 2. Kh1 Qxh2+",
            }
        )

        self.assertIn("board-crop-fallback", html)
        self.assertIn("FEN unavailable", html)
        self.assertIn("Unknown side to move", html)
        self.assertIn("Copy line", html)
        self.assertIn("Book line", html)
        self.assertNotIn("Copy FEN", html)
        self.assertNotIn("fen_not_recognized", html)

    def test_invalid_fen_never_gets_copy_fen_even_when_status_is_accepted(self) -> None:
        html = _semantic_source_diagram_html(
            {
                "id": "p010_d03",
                "caption": "Diagram 1-5",
                "page": 10,
                "side_to_move": "w",
                "validation_status": "accepted",
                "fen": "not a fen",
                "board_crop_path": "assets/diagrams/p010_d03.png",
            }
        )

        self.assertIn("White to move", html)
        self.assertIn("FEN unavailable", html)
        self.assertIn("board-crop-fallback", html)
        self.assertIn("Moves unavailable", html)
        self.assertNotIn("Copy FEN", html)


if __name__ == "__main__":
    unittest.main()
