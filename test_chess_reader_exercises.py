from __future__ import annotations

import unittest

from chess_study_export import SEMANTIC_BOOK_SCHEMA, _semantic_source_index_html


def _diagram_block(index: int, *, fen: str | None = None, side: str = "white") -> dict[str, object]:
    exercise_id = f"ex_1_{index}"
    return {
        "type": "diagram",
        "diagram_id": f"p010_d{index:02d}",
        "caption": f"Diagram 1-{index}",
        "source_page": 10,
        "side_to_move": side,
        "fen": fen,
        "fen_status": "available" if fen else "unavailable",
        "pgn": f"1. Nf{index} *" if fen else "",
        "book_line": f"1. Nf{index} *",
        "pgn_status": "available" if fen else "needs_review",
        "board_crop_path": f"assets/board-{index}.png",
        "original_crop_path": f"assets/original-{index}.png",
        "review_status": "verified" if fen else "needs_review",
        "exercise_id": exercise_id,
    }


def _exercise_block(index: int, *, difficulty: str = "*") -> dict[str, object]:
    return {
        "type": "exercise",
        "exercise_id": f"ex_1_{index}",
        "diagram_id": f"p010_d{index:02d}",
        "source_page": 10,
        "difficulty": difficulty,
    }


def _solution_block(index: int) -> dict[str, object]:
    return {
        "type": "solution",
        "exercise_id": f"ex_1_{index}",
        "diagram_id": f"p010_d{index:02d}",
        "best_move": f"Nf{index}",
        "book_line": f"1. Nf{index} *",
        "commentary": "Best move wins material.",
    }


class ChessReaderExerciseGridTests(unittest.TestCase):
    def _book_with_exercise_count(self, count: int) -> dict[str, object]:
        blocks: list[dict[str, object]] = [{"type": "heading", "level": 2, "text": "Exercises - Chapter 1"}]
        for index in range(1, count + 1):
            fen = "8/8/8/8/8/8/4K3/4k3 w - - 0 1" if index % 2 else None
            side = "black" if index % 2 == 0 else "white"
            diagram = _diagram_block(index, fen=fen, side=side)
            if index == count:
                diagram["pgn"] = ""
                diagram["book_line"] = ""
                diagram["pgn_status"] = "unavailable"
            blocks.append(diagram)
            blocks.append(_exercise_block(index, difficulty="**" if index > 2 else "*"))
            if index != count:
                blocks.append(_solution_block(index))
        return {
            "title": "Exercise Reader",
            "summary": {"html_pages": 1, "diagrams_total": count, "fen_accepted": count // 2, "accepted_pgn": count - 1},
            "chapters": [{"title": "Exercises", "start_page": 10}],
            "semantic_book": {
                "schema": SEMANTIC_BOOK_SCHEMA,
                "book_title": "Exercise Reader",
                "summary": {"page_count": 1, "exercise_count": count, "diagram_count": count},
                "pages": [{"page_number": 10, "blocks": blocks}],
            },
        }

    def test_exercise_grid_replaces_raw_diagram_sequence_for_multi_exercise_page(self) -> None:
        html = _semantic_source_index_html(self._book_with_exercise_count(4))

        self.assertIn('data-kind="exercise-grid"', html)
        self.assertIn('data-count="4"', html)
        self.assertIn("Exercises - Page 10", html)
        self.assertIn("Ex. 1-1", html)
        self.assertIn("Ex. 1-4", html)
        self.assertIn("White to move", html)
        self.assertIn("Black to move", html)
        self.assertIn("Copy FEN", html)
        self.assertIn("FEN unavailable", html)
        self.assertIn("Show solution", html)
        self.assertIn("Solution not linked", html)
        self.assertNotIn('class="diagram-card"', html)

    def test_study_mode_defaults_to_one_hidden_solution_flow(self) -> None:
        html = _semantic_source_index_html(self._book_with_exercise_count(2))

        self.assertIn('data-kind="study-mode"', html)
        self.assertIn("Practice one position at a time", html)
        self.assertIn("Previous exercise", html)
        self.assertIn("Next exercise", html)
        self.assertIn('data-study-index="0"', html)
        self.assertIn('data-study-index="1" hidden', html)
        self.assertIn("Hide solution", html)

    def test_layout_supports_representative_exercise_counts(self) -> None:
        for count in (1, 2, 4, 6):
            with self.subTest(count=count):
                html = _semantic_source_index_html(self._book_with_exercise_count(count))
                self.assertIn(f'data-count="{count}"', html)
                self.assertIn(f"{count} exercises", html)
                self.assertIn("exercise-grid", html)
                self.assertIn("study-mode-panel", html)


if __name__ == "__main__":
    unittest.main()
