from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_study_export import (
    SEMANTIC_BOOK_SCHEMA,
    _semantic_source_styles_css,
    _semantic_source_index_html,
    _write_chess_reader_semantic_book_reports,
    build_chess_reader_semantic_book,
)


class ChessReaderSemanticBookTests(unittest.TestCase):
    def _sample_book(self) -> dict:
        return {
            "title": "Semantic chess sample",
            "source_pdf": "sample.pdf",
            "source_html": "sample.html",
            "summary": {
                "html_pages": 1,
                "diagrams_total": 1,
                "fen_accepted": 0,
                "accepted_pgn": 1,
                "fen_needs_review": 1,
                "pgn_needs_review": 0,
            },
            "pages": [
                {
                    "page": 10,
                    "page_preview": "assets/pages-preview/p010.png",
                    "text_chunks": [
                        {"text": "Chapter 1 Mating motifs", "text_kind": "heading", "reading_order": 1},
                        {"text": "Find the forcing move.", "reading_order": 2},
                        {"text": "a b c d e f g h", "reading_order": 3},
                        {"text": "fen_not_recognized", "reading_order": 4},
                        {"text": "raw bbox: [10, 20, 30, 40]", "reading_order": 5},
                    ],
                    "diagrams": [
                        {
                            "id": "p010_d01",
                            "caption": "Diagram 1-3 **",
                            "page": 10,
                            "reading_order": 6,
                            "fen": "",
                            "fen_candidate": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                            "review_reason": "fen_not_recognized",
                            "side_to_move": "b",
                            "board_crop_path": "assets/diagrams/p010_d01.png",
                            "image_path": "assets/diagrams/p010_d01.png",
                            "validation_status": "needs-human-review",
                        }
                    ],
                }
            ],
            "pgn_records": [
                {
                    "id": "pgn_1",
                    "label": "Diagram 1-3",
                    "raw_text": "1... Qh4+ wins",
                    "visible_review_text": "1... Qh4+ wins",
                    "pgn": '[Event "?"]\n[Result "*"]\n\n1... Qh4+ *',
                    "status": "accepted",
                    "source_page": 10,
                    "logical_page": 10,
                    "reading_order": 7,
                }
            ],
        }

    def test_builds_versioned_semantic_blocks_and_filters_reader_noise(self) -> None:
        semantic_book = build_chess_reader_semantic_book(self._sample_book())

        self.assertEqual(semantic_book["schema"], SEMANTIC_BOOK_SCHEMA)
        self.assertEqual(semantic_book["summary"]["diagram_count"], 1)
        page = semantic_book["pages"][0]
        blocks = page["blocks"]
        block_types = [block["type"] for block in blocks]
        self.assertIn("heading", block_types)
        self.assertIn("paragraph", block_types)
        self.assertIn("diagram", block_types)
        self.assertIn("exercise", block_types)
        self.assertIn("pgn", block_types)
        self.assertIn("solution", block_types)

        visible_text = "\n".join(str(block.get("text") or "") for block in blocks)
        self.assertIn("Find the forcing move.", visible_text)
        self.assertNotIn("a b c d e f g h", visible_text)
        self.assertNotIn("fen_not_recognized", visible_text)
        self.assertNotIn("raw bbox", visible_text)

        diagram = next(block for block in blocks if block["type"] == "diagram")
        self.assertEqual(diagram["side_to_move"], "black")
        self.assertEqual(diagram["fen_status"], "unavailable")
        self.assertEqual(diagram["exercise_id"], "ex_1_3")
        pgn = next(block for block in blocks if block["type"] == "pgn")
        self.assertEqual(pgn["pgn_status"], "available")
        self.assertEqual(pgn["exercise_id"], "ex_1_3")

    def test_reader_html_uses_semantic_book_instead_of_raw_ocr_text(self) -> None:
        book = self._sample_book()
        book["semantic_book"] = build_chess_reader_semantic_book(book)

        html = _semantic_source_index_html(book)

        self.assertIn("Find the forcing move.", html)
        self.assertIn("FEN unavailable", html)
        self.assertIn("exercise-card", html)
        self.assertIn("solution-card", html)
        self.assertNotIn("fen_not_recognized", html)
        self.assertNotIn("raw bbox", html)

    def test_reader_design_system_tokens_and_copy_blocks_are_present(self) -> None:
        book = self._sample_book()
        diagram = book["pages"][0]["diagrams"][0]
        diagram["fen"] = "8/8/8/8/8/8/4K3/4k3 b - - 0 1"
        diagram["validation_status"] = "accepted"
        book["semantic_book"] = build_chess_reader_semantic_book(book)

        html = _semantic_source_index_html(book)
        css = _semantic_source_styles_css()

        self.assertIn('class="reader-shell"', html)
        self.assertIn("fen-copy-block", html)
        self.assertIn("pgn-copy-block", html)
        self.assertIn("Copy FEN", html)
        self.assertIn("Copy PGN", html)
        self.assertIn("--km-bg-paper:#F6F0E6", css)
        self.assertIn("--km-surface:#FFFDF8", css)
        self.assertIn("--km-code-bg:#F1E7D6", css)
        self.assertIn("grid-template-columns:minmax(620px,var(--reader-text-width)) minmax(300px,var(--reader-diagram-width))", css)
        self.assertIn("@media (max-width: 1180px)", css)
        self.assertIn("@media (max-width: 720px)", css)

    def test_writes_json_and_markdown_reports(self) -> None:
        semantic_book = build_chess_reader_semantic_book(self._sample_book())
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_chess_reader_semantic_book_reports(out, semantic_book)

            json_path = out / "reports" / "chess_reader" / "semantic_book.json"
            md_path = out / "reports" / "chess_reader" / "semantic_book.md"
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], SEMANTIC_BOOK_SCHEMA)
            self.assertIn("Semantic Book", md_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
