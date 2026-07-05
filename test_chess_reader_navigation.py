from __future__ import annotations

import unittest

from chess_study_export import SEMANTIC_BOOK_SCHEMA, _semantic_source_app_js, _semantic_source_index_html, _semantic_source_styles_css


def _navigation_book() -> dict[str, object]:
    return {
        "title": "Navigation Reader",
        "summary": {"html_pages": 2, "diagrams_total": 2, "fen_accepted": 1, "accepted_pgn": 1, "fen_needs_review": 1, "pgn_needs_review": 1},
        "chapters": [{"title": "Chapter 1: Mating Motifs", "start_page": 10}],
        "semantic_book": {
            "schema": SEMANTIC_BOOK_SCHEMA,
            "book_title": "Navigation Reader",
            "summary": {"page_count": 2, "diagram_count": 2, "exercise_count": 2, "solution_count": 1},
            "pages": [
                {
                    "page_number": 10,
                    "blocks": [
                        {"type": "heading", "level": 2, "text": "Anastasia's mate"},
                        {"type": "paragraph", "text": "Find the forcing move."},
                        {
                            "type": "diagram",
                            "diagram_id": "p010_d01",
                            "caption": "Diagram 1-1",
                            "source_page": 10,
                            "side_to_move": "white",
                            "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                            "fen_status": "available",
                            "pgn": "1. Nf3 *",
                            "book_line": "1. Nf3 *",
                            "pgn_status": "available",
                            "review_status": "verified",
                            "exercise_id": "ex_1_1",
                        },
                        {"type": "exercise", "exercise_id": "ex_1_1", "diagram_id": "p010_d01", "source_page": 10, "difficulty": "*"},
                        {
                            "type": "solution",
                            "exercise_id": "ex_1_1",
                            "diagram_id": "p010_d01",
                            "source_page": 10,
                            "solution_page": 11,
                            "best_move": "Nf3",
                            "pgn": "1. Nf3 *",
                            "book_line": "1. Nf3 *",
                            "commentary": "The knight move starts the mating net.",
                            "review_status": "available",
                        },
                    ],
                },
                {
                    "page_number": 12,
                    "blocks": [
                        {"type": "heading", "level": 2, "text": "Exercises"},
                        {
                            "type": "diagram",
                            "diagram_id": "p012_d02",
                            "caption": "Diagram 1-2",
                            "source_page": 12,
                            "side_to_move": "unknown",
                            "fen": "",
                            "fen_status": "unavailable",
                            "pgn": "",
                            "book_line": "",
                            "pgn_status": "unavailable",
                            "review_status": "needs_review",
                            "exercise_id": "ex_1_2",
                        },
                        {"type": "exercise", "exercise_id": "ex_1_2", "diagram_id": "p012_d02", "source_page": 12, "difficulty": "**"},
                    ],
                },
            ],
        },
    }


class ChessReaderNavigationTests(unittest.TestCase):
    def test_reader_navigation_has_toc_indexes_filters_and_search(self) -> None:
        html = _semantic_source_index_html(_navigation_book())

        self.assertIn("reader-navigation", html)
        self.assertIn('id="reader-search"', html)
        self.assertIn('data-reader-filter="all"', html)
        self.assertIn('data-reader-filter="diagram"', html)
        self.assertIn('data-reader-filter="exercise"', html)
        self.assertIn('data-reader-filter="solution"', html)
        self.assertIn('data-reader-filter="needs-review"', html)
        self.assertIn('data-reader-filter="fen-available"', html)
        self.assertIn('data-reader-filter="pgn-available"', html)
        self.assertIn('data-reader-filter="fen-unavailable"', html)
        self.assertIn('href="#diagram-p010-d01"', html)
        self.assertIn('href="#exercise-ex-1-1"', html)
        self.assertIn('href="#solution-ex-1-1"', html)
        self.assertIn('href="#page-10"', html)
        self.assertIn("Anastasia&#x27;s mate", html)

    def test_reader_items_are_searchable_by_ids_fen_and_pgn(self) -> None:
        html = _semantic_source_index_html(_navigation_book())

        self.assertIn('id="diagram-p010-d01"', html)
        self.assertIn('data-diagram-id="p010_d01"', html)
        self.assertIn('id="exercise-ex-1-1"', html)
        self.assertIn('id="solution-ex-1-1"', html)
        self.assertIn('data-reader-kinds="diagram fen-available pgn-available"', html)
        self.assertIn('data-reader-kinds="diagram fen-unavailable needs-review pgn-unavailable"', html)
        self.assertIn("8/8/8/8/8/8/4K3/4k3 w - - 0 1", html)
        self.assertIn("1. Nf3 *", html)

    def test_navigation_css_and_js_support_mobile_drawer_and_filtering(self) -> None:
        css = _semantic_source_styles_css()
        js = _semantic_source_app_js()

        self.assertIn(".reader-nav-drawer", css)
        self.assertIn(".reader-filter-button.active", css)
        self.assertIn("@media (max-width: 940px)", css)
        self.assertIn("activeReaderFilter", js)
        self.assertIn("data-reader-search", js)
        self.assertIn("data-reader-empty", js)
        self.assertIn("updateReaderNavigation", js)


if __name__ == "__main__":
    unittest.main()
