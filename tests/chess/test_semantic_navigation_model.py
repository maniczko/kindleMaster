from __future__ import annotations

import unittest

from chess_exercise_model import ChessExerciseModel, build_chess_exercise_model, exercise_to_reader_item
from chess_study_export import SEMANTIC_BOOK_SCHEMA, _semantic_source_index_html


def _canonical_pages(*, solution_title: str = "Alpha - Beta, Oslo 2000") -> list[dict[str, object]]:
    return [
        {
            "page_number": 10,
            "blocks": [
                {
                    "type": "exercise",
                    "exercise_id": "ex_1_7",
                    "printed_number": 7,
                    "source_page": 10,
                    "difficulty": "**",
                },
                {
                    "type": "diagram",
                    "exercise_id": "ex_1_7",
                    "diagram_id": "diagram-1-7",
                    "exercise_number": 7,
                    "caption": "Alpha - Beta, Oslo 2000",
                    "source_page": 10,
                    "side_to_move": "white",
                    "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                    "fen_status": "available",
                    "review_status": "verified",
                    "board_crop_path": "assets/ex-1-7.png",
                    "original_crop_path": "assets/ex-1-7-original.png",
                },
            ],
        },
        {
            "page_number": 210,
            "blocks": [
                {
                    "type": "solution",
                    "exercise_id": "ex_1_7",
                    "solution_number": 7,
                    "solution_title": solution_title,
                    "solution_page": 210,
                    "book_line": "1. Qh7+ Kf8 2. Qh8#",
                    "pgn": "1. Qh7+ Kf8 2. Qh8#",
                    "best_move": "Qh7+",
                }
            ],
        },
    ]


def _reader_book(model: ChessExerciseModel) -> dict[str, object]:
    payload = model.to_dict()
    return {
        "title": "Canonical Navigation Reader",
        "summary": {
            "html_pages": 2,
            "diagrams_total": 1,
            "fen_accepted": 1,
            "accepted_pgn": 1,
        },
        "chapters": [
            {"title": "Exercises", "start_page": 10},
            {"title": "Solutions", "start_page": 210},
        ],
        "semantic_book": {
            "schema": SEMANTIC_BOOK_SCHEMA,
            "book_title": "Canonical Navigation Reader",
            "summary": {
                "page_count": 2,
                "exercise_count": 1,
                "solution_count": 1,
                "diagram_count": 1,
            },
            "pages": _canonical_pages(),
            "exercise_navigation": payload["exercise_navigation"],
            "exercises": payload["exercises"],
        },
    }


class SemanticNavigationModelTests(unittest.TestCase):
    def test_model_attaches_one_bidirectional_navigation_record(self) -> None:
        model = build_chess_exercise_model(_canonical_pages())
        payload = model.to_dict()
        navigation = payload["exercise_navigation"]
        exercise = payload["exercises"][0]

        self.assertEqual(navigation["summary"]["record_count"], 1)
        self.assertEqual(navigation["summary"]["accepted_count"], 1)
        self.assertEqual(navigation["summary"]["forward_link_count"], 1)
        self.assertEqual(navigation["summary"]["backlink_count"], 1)
        self.assertEqual(navigation["summary"]["orphan_count"], 0)
        self.assertFalse(navigation["summary"]["production_blocked"])
        self.assertEqual(exercise["navigation"]["status"], "accepted")
        self.assertEqual(exercise["navigation"]["exercise_number"], "1-7")
        self.assertIn("1-7", exercise["navigation"]["forward_text"])
        self.assertIn("1-7", exercise["navigation"]["backlink_text"])
        self.assertEqual(ChessExerciseModel.from_dict(payload).to_json(), model.to_json())

        reader_item = exercise_to_reader_item(exercise)
        self.assertEqual(reader_item["navigation_status"], "accepted")
        self.assertEqual(reader_item["exercise_anchor"], "exercise-ex-1-7")
        self.assertEqual(reader_item["solution_anchor"], "solution-ex-1-7")
        self.assertEqual(reader_item["solution_href"], "#solution-ex-1-7")
        self.assertEqual(reader_item["exercise_href"], "#exercise-ex-1-7")

    def test_reader_uses_exact_canonical_anchors_and_numbered_link_text(self) -> None:
        model = build_chess_exercise_model(_canonical_pages())
        html = _semantic_source_index_html(_reader_book(model))

        self.assertIn('id="exercise-ex-1-7"', html)
        self.assertIn('id="solution-ex-1-7"', html)
        self.assertIn('href="#solution-ex-1-7"', html)
        self.assertIn('href="#exercise-ex-1-7"', html)
        self.assertIn("Open solution for Exercise 1-7", html)
        self.assertIn("Back to Exercise 1-7", html)
        self.assertEqual(html.count('id="exercise-ex-1-7"'), 1)
        self.assertEqual(html.count('id="solution-ex-1-7"'), 1)

    def test_blocked_canonical_pair_emits_no_semantic_navigation_links(self) -> None:
        model = build_chess_exercise_model(
            _canonical_pages(solution_title="Different - Players, Riga 1999")
        )
        payload = model.to_dict()
        self.assertTrue(payload["exercise_navigation"]["summary"]["production_blocked"])
        self.assertEqual(payload["exercises"][0]["navigation"]["status"], "blocked")

        html = _semantic_source_index_html(_reader_book(model))
        self.assertNotIn("semantic-solution-link", html)
        self.assertNotIn("semantic-exercise-backlink", html)
        self.assertNotIn("Open solution for Exercise 1-7", html)
        self.assertNotIn("Back to Exercise 1-7", html)


if __name__ == "__main__":
    unittest.main()
