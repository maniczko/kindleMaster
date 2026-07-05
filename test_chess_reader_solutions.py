from __future__ import annotations

import unittest

from chess_study_export import SEMANTIC_BOOK_SCHEMA, _semantic_source_index_html


def _solution_book(solution: dict[str, object]) -> dict[str, object]:
    exercise_id = str(solution.get("exercise_id") or "ex_20_7")
    return {
        "title": "Solution Reader",
        "summary": {"html_pages": 1, "diagrams_total": 1, "fen_accepted": 1, "accepted_pgn": 1},
        "chapters": [{"title": "Solutions", "start_page": 42}],
        "semantic_book": {
            "schema": SEMANTIC_BOOK_SCHEMA,
            "book_title": "Solution Reader",
            "summary": {"page_count": 1, "exercise_count": 1, "solution_count": 1},
            "pages": [
                {
                    "page_number": 42,
                    "blocks": [
                        {"type": "heading", "level": 2, "text": "Solutions"},
                        {
                            "type": "diagram",
                            "diagram_id": "p042_d01",
                            "caption": "Diagram 20-7",
                            "source_page": 42,
                            "side_to_move": "white",
                            "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                            "fen_status": "available",
                            "review_status": "verified",
                            "exercise_id": exercise_id,
                        },
                        {
                            "type": "exercise",
                            "exercise_id": exercise_id,
                            "diagram_id": "p042_d01",
                            "source_page": 42,
                            "difficulty": "**",
                        },
                        {**solution, "type": "solution", "exercise_id": exercise_id, "diagram_id": "p042_d01"},
                    ],
                }
            ],
        },
    }


class ChessReaderSolutionCardTests(unittest.TestCase):
    def test_full_pgn_solution_has_pgn_copy_and_moves_only_action(self) -> None:
        html = _semantic_source_index_html(
            _solution_book(
                {
                    "best_move": "Nd4",
                    "pgn": '[Event "?"]\n[Result "*"]\n\n1. Nd4 Nf6 2. b5 *',
                    "book_line": '[Event "?"]\n[Result "*"]\n\n1. Nd4 Nf6 2. b5 *',
                    "commentary": "The weak point is c6.",
                    "review_status": "available",
                }
            )
        )

        self.assertIn("solution-card-rich", html)
        self.assertIn("Best move", html)
        self.assertIn("Nd4", html)
        self.assertIn("PGN", html)
        self.assertIn("Copy PGN", html)
        self.assertIn("Copy moves only", html)
        self.assertIn("Copy solution text", html)
        self.assertIn("Open linked diagram", html)
        self.assertIn("The weak point is c6.", html)

    def test_book_line_solution_splits_variation_from_main_line(self) -> None:
        html = _semantic_source_index_html(
            _solution_book(
                {
                    "best_move": "Nd4",
                    "book_line": "1. Nd4 Nf6 2. b5 (1. a3? Bxa3) 2... Bxg2",
                    "variations": ["1. a3? Bxa3"],
                    "commentary": "Only one point for 1.a3.",
                    "review_status": "needs_review",
                }
            )
        )

        self.assertIn("Book line", html)
        self.assertIn("Copy line", html)
        self.assertIn("Variation 1", html)
        self.assertIn("1. a3? Bxa3", html)
        self.assertIn("Only one point for 1.a3.", html)

    def test_best_move_only_does_not_pretend_to_have_pgn(self) -> None:
        html = _semantic_source_index_html(_solution_book({"best_move": "Nd4", "review_status": "needs_review"}))

        self.assertIn("Best move", html)
        self.assertIn("Nd4", html)
        self.assertIn("Moves unavailable", html)
        self.assertNotIn("Copy PGN", html)

    def test_unrecognized_moves_keep_explanation_without_wall_of_moves(self) -> None:
        html = _semantic_source_index_html(
            _solution_book(
                {
                    "commentary": "The OCR explanation is readable but the moves were not recognized.",
                    "review_status": "needs_review",
                }
            )
        )

        self.assertIn("Explanation", html)
        self.assertIn("Moves unavailable", html)
        self.assertIn("The OCR explanation is readable", html)


if __name__ == "__main__":
    unittest.main()
