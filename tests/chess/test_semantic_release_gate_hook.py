from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chess_study_export import render_semantic_source_reader


def exercise(*, accepted: bool) -> dict[str, object]:
    return {
        "exercise_id": "ex_1_1",
        "source": {"page_number": 11, "bounding_box": [1, 2, 3, 4]},
        "game": {"normalized_title": "white black event"},
        "diagram": {
            "diagram_id": "d-1",
            "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
            "fen_status": "available",
            "review_status": "verified",
        },
        "solution": {"raw_text": "1. Kf3", "normalized_notation": "1. Kf3"} if accepted else None,
        "solution_match": {"status": "exact" if accepted else "unmatched", "production_blocked": not accepted},
        "solution_integrity": {"status": "accepted" if accepted else "blocked", "strict_blocked": not accepted, "findings": []},
        "navigation": {
            "status": "accepted" if accepted else "blocked",
            "accepted": accepted,
            "exercise_number": "1-1",
            "exercise_anchor": "exercise-ex-1-1",
            "solution_anchor": "solution-ex-1-1",
            "forward_href": "#solution-ex-1-1" if accepted else "",
            "backlink_href": "#exercise-ex-1-1" if accepted else "",
            "forward_text": "Open solution for Exercise 1-1",
            "backlink_text": "Back to Exercise 1-1",
            "findings": [],
        },
        "validation": {"confidence": 1.0, "warnings": []},
    }


def semantic_book(*, accepted: bool) -> dict[str, object]:
    return {
        "schema": "kindlemaster.chess_reader.semantic_book.v1",
        "book_title": "Hook test",
        "exercises": [exercise(accepted=accepted)],
        "pages": [],
        "exercise_model_warnings": [],
    }


class SemanticReleaseGateHookTests(unittest.TestCase):
    def test_strict_failure_blocks_final_reader_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {"semantic_book": semantic_book(accepted=False), "pages": [], "title": "Hook test"}
            with (
                patch("chess_study_export._load_source_book", return_value=book),
                patch("chess_study_export._semantic_source_index_html", return_value="<html></html>"),
                patch("chess_study_export._write_chess_reader_semantic_book_reports"),
            ):
                payload = render_semantic_source_reader(root, integrity_mode="strict")
            self.assertEqual(payload["status"], "failed")
            self.assertTrue(payload["blocked_before_write"])
            self.assertFalse((root / "index.html").exists())
            self.assertFalse((root / "styles.css").exists())
            self.assertTrue((root / "reports" / "chess_reader" / "semantic_release_gate.json").is_file())

    def test_development_mode_keeps_current_reader_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {"semantic_book": semantic_book(accepted=False), "pages": [], "title": "Hook test"}
            with (
                patch("chess_study_export._load_source_book", return_value=book),
                patch("chess_study_export._semantic_source_index_html", return_value="<html>reader</html>"),
                patch("chess_study_export._semantic_source_styles_css", return_value="css"),
                patch("chess_study_export._semantic_source_app_js", return_value="js"),
                patch("chess_study_export._write_chess_reader_semantic_book_reports"),
            ):
                payload = render_semantic_source_reader(root, integrity_mode="development")
            self.assertEqual(payload["status"], "ok")
            self.assertEqual((root / "index.html").read_text(encoding="utf-8"), "<html>reader</html>")

    def test_strict_accepts_valid_semantic_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = {"semantic_book": semantic_book(accepted=True), "pages": [], "title": "Hook test"}
            html = (
                '<article id="exercise-ex-1-1"><a href="#solution-ex-1-1">solution</a></article>'
                '<section id="solution-ex-1-1"><a href="#exercise-ex-1-1">back</a></section>'
            )
            with (
                patch("chess_study_export._load_source_book", return_value=book),
                patch("chess_study_export._semantic_source_index_html", return_value=html),
                patch("chess_study_export._semantic_source_styles_css", return_value="css"),
                patch("chess_study_export._semantic_source_app_js", return_value="js"),
                patch("chess_study_export._write_chess_reader_semantic_book_reports"),
            ):
                payload = render_semantic_source_reader(root, integrity_mode="strict")
            self.assertEqual(payload["status"], "ok")
            self.assertTrue((root / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
