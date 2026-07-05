from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from chess_study_export import (
    SEMANTIC_BOOK_SCHEMA,
    _render_standalone_html,
    _semantic_source_index_html,
    _semantic_source_styles_css,
)


TECHNICAL_READER_JUNK = [
    "a b c d e f g h",
    "1 2 3 4 5 6 7 8",
    "fen_not_recognized",
    "mass_side_to_move_unknown",
    "side_to_move_unknown",
    "board_crop_quality",
    "marker_crop_quality",
    "raw trace ids",
]


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    return " ".join(soup.get_text(" ").split())


def _qa_book() -> dict[str, object]:
    return {
        "title": "Reader QA Fixture",
        "summary": {
            "html_pages": 4,
            "diagrams_total": 4,
            "fen_accepted": 2,
            "accepted_pgn": 2,
            "fen_needs_review": 1,
            "pgn_needs_review": 1,
        },
        "chapters": [{"title": "Chapter 1: Quality", "start_page": 1}],
        "semantic_book": {
            "schema": SEMANTIC_BOOK_SCHEMA,
            "book_title": "Reader QA Fixture",
            "summary": {"page_count": 4, "diagram_count": 4, "exercise_count": 3, "solution_count": 3},
            "pages": [
                {
                    "page_number": 1,
                    "blocks": [
                        {"type": "heading", "level": 2, "text": "Clean prose page"},
                        {"type": "paragraph", "text": "Find the forcing move without reading audit diagnostics."},
                    ],
                },
                {
                    "page_number": 2,
                    "blocks": [
                        {
                            "type": "diagram",
                            "diagram_id": "p002_d01",
                            "caption": "Diagram 1-3",
                            "source_page": 2,
                            "side_to_move": "black",
                            "fen": "8/8/8/8/8/8/4K3/4k3 b - - 0 1",
                            "fen_status": "available",
                            "pgn": "1... Qh4+ *",
                            "book_line": "1... Qh4+ *",
                            "pgn_status": "available",
                            "review_status": "verified",
                            "exercise_id": "ex_1_3",
                        },
                        {"type": "exercise", "exercise_id": "ex_1_3", "diagram_id": "p002_d01", "source_page": 2, "difficulty": "**"},
                    ],
                },
                {
                    "page_number": 3,
                    "blocks": [
                        {
                            "type": "diagram",
                            "diagram_id": "p003_d01",
                            "caption": "Diagram 1-4",
                            "source_page": 3,
                            "side_to_move": "unknown",
                            "fen": "",
                            "fen_status": "unavailable",
                            "pgn": "",
                            "book_line": "1. Nf3",
                            "pgn_status": "available",
                            "review_status": "needs_review",
                            "exercise_id": "ex_1_4",
                        },
                        {"type": "exercise", "exercise_id": "ex_1_4", "diagram_id": "p003_d01", "source_page": 3, "difficulty": "*"},
                        {
                            "type": "diagram",
                            "diagram_id": "p003_d02",
                            "caption": "Diagram 1-5",
                            "source_page": 3,
                            "side_to_move": "white",
                            "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                            "fen_status": "available",
                            "pgn": "",
                            "book_line": "",
                            "pgn_status": "unavailable",
                            "review_status": "verified",
                            "exercise_id": "ex_1_5",
                        },
                        {"type": "exercise", "exercise_id": "ex_1_5", "diagram_id": "p003_d02", "source_page": 3, "difficulty": "***"},
                    ],
                },
                {
                    "page_number": 4,
                    "blocks": [
                        {
                            "type": "solution",
                            "exercise_id": "ex_1_3",
                            "diagram_id": "p002_d01",
                            "source_page": 2,
                            "solution_page": 4,
                            "best_move": "Qh4+",
                            "pgn": "1... Qh4+ *",
                            "book_line": "",
                            "commentary": "The queen enters with tempo.",
                            "review_status": "available",
                        },
                        {
                            "type": "solution",
                            "exercise_id": "ex_1_4",
                            "diagram_id": "p003_d01",
                            "source_page": 3,
                            "solution_page": 4,
                            "best_move": "Nf3",
                            "pgn": "",
                            "book_line": "1. Nf3",
                            "commentary": "",
                            "review_status": "available",
                        },
                        {
                            "type": "solution",
                            "exercise_id": "ex_1_5",
                            "diagram_id": "p003_d02",
                            "source_page": 3,
                            "solution_page": 4,
                            "best_move": "",
                            "pgn": "",
                            "book_line": "",
                            "commentary": "",
                            "review_status": "needs_review",
                        },
                    ],
                },
            ],
        },
    }


class ChessReaderQualityAssuranceTests(unittest.TestCase):
    def test_reader_view_blocks_ocr_and_diagnostic_junk_from_visible_text(self) -> None:
        book = _qa_book()
        book["semantic_book"]["pages"][0]["blocks"].extend(
            [
                {"type": "paragraph", "text": "a b c d e f g h"},
                {"type": "paragraph", "text": "1 2 3 4 5 6 7 8"},
                {"type": "paragraph", "text": "fen_not_recognized"},
                {"type": "paragraph", "text": "mass_side_to_move_unknown"},
                {"type": "paragraph", "text": "board_crop_quality marker_crop_quality raw trace ids"},
            ]
        )

        html = _semantic_source_index_html(book)
        visible = _visible_text(html)

        self.assertIn("Find the forcing move without reading audit diagnostics.", visible)
        for token in TECHNICAL_READER_JUNK:
            self.assertNotIn(token, visible)

    def test_copy_buttons_and_statuses_are_accessible_without_color_only_signals(self) -> None:
        soup = BeautifulSoup(_semantic_source_index_html(_qa_book()), "html.parser")
        copy_buttons = soup.select("button[data-copy-value]")

        self.assertGreaterEqual(len(copy_buttons), 5)
        for button in copy_buttons:
            self.assertTrue((button.get("aria-label") or "").strip(), str(button))
            self.assertTrue(button.get_text(strip=True), str(button))

        diagram = soup.select_one("figure.diagram-card[data-diagram-id='p002_d01']")
        self.assertIsNotNone(diagram)
        self.assertEqual(diagram.get("aria-label"), "Diagram 1-3, Black to move, FEN available")
        statuses = soup.select(".component-status, .review-badge")
        self.assertGreaterEqual(len(statuses), 4)
        for status in statuses:
            self.assertTrue(status.get_text(" ", strip=True), str(status))

    def test_reader_css_preserves_focus_responsive_and_minimum_diagram_layout(self) -> None:
        css = _semantic_source_styles_css()

        self.assertIn(".copy-button:focus-visible", css)
        self.assertIn("min-height:44px", css)
        self.assertIn("@media (max-width: 1180px)", css)
        self.assertIn("@media (max-width: 940px)", css)
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn(".diagram-grid { display:grid; grid-template-columns:minmax(280px,340px) minmax(0,1fr)", css)
        self.assertIn(".board-placeholder { min-height:280px", css)
        self.assertIn(".study-mode-card .exercise-board { min-height:260px", css)
        self.assertIn(".code-block-header { align-items:stretch; flex-direction:column; }", css)

    def test_required_static_snapshot_cases_are_generated_for_reader_and_audit_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            reader_html = _semantic_source_index_html(_qa_book())
            snapshot_names = [
                "01-text-page.html",
                "02-single-diagram.html",
                "03-exercise-grid.html",
                "04-solutions.html",
                "05-fen-available.html",
                "06-fen-unavailable.html",
                "07-pgn-available.html",
                "08-book-line-only.html",
                "09-mobile-layout.html",
            ]
            for name in snapshot_names:
                (out / name).write_text(reader_html, encoding="utf-8")

            audit_dir = out / "audit"
            audit_dir.mkdir()
            _render_standalone_html(
                audit_dir,
                structure={"chapters": [{"chapter_no": 1, "title": "QA"}]},
                positions={
                    "positions": [
                        {
                            "id": "p002_d01",
                            "diagram_page": 2,
                            "status": "needs_review",
                            "board_crop_path": "assets/tight-board.png",
                            "side_marker_crop_path": "assets/marker.png",
                            "debug_overlay_path": "assets/overlay.png",
                            "board_crop_quality": "fail",
                            "board_crop_fail_reason": "board_bbox_not_tight_8x8",
                            "marker_crop_quality": "fail",
                            "marker_crop_fail_reason": "marker_missing",
                            "marker_search_zones": {"right": [112, 20, 150, 120]},
                        }
                    ]
                },
                qa_report={"summary": {"pages": 1, "diagrams_total": 1}, "problems": []},
                page_model={"pages": [{"page": 2, "page_image": "assets/page-2.png", "elements": []}]},
                notation_fragments={"fragments": []},
            )
            (out / "10-audit-overlay.html").write_text((audit_dir / "standalone_audit.html").read_text(encoding="utf-8"), encoding="utf-8")

            generated = sorted(path.name for path in out.glob("*.html"))

        self.assertEqual(
            generated,
            [
                "01-text-page.html",
                "02-single-diagram.html",
                "03-exercise-grid.html",
                "04-solutions.html",
                "05-fen-available.html",
                "06-fen-unavailable.html",
                "07-pgn-available.html",
                "08-book-line-only.html",
                "09-mobile-layout.html",
                "10-audit-overlay.html",
            ],
        )


if __name__ == "__main__":
    unittest.main()
