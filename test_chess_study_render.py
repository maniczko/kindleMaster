from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_study_export import build_study_pgn, render_study_html, validate_study_export, ChessStudyConfig
from chess_study_export import rebuild_chess_source_html_export


class ChessStudyRenderTests(unittest.TestCase):
    def test_rebuild_source_html_export_externalizes_assets_and_hides_raw_debug(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_b64 = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgaAAAAQQA"
                "AeYhvDMAAAAASUVORK5CYII="
            )
            html_path = root / "source.html"
            html_path.write_text(
                f"""
                <html><body>
                  <section class="chess-book-page" data-page="1">
                    <img class="book-page-bg" src="data:image/png;base64,{image_b64}">
                    <div class="book-element book-text" data-reading-order="1" style="left:10px;top:10px;width:80px;height:12px">1 Mating motifs</div>
                    <div class="book-element book-text" data-reading-order="2" style="left:10px;top:30px;width:120px;height:12px">Diagram 1-1 White to move</div>
                    <div class="book-element book-diagram" data-reading-order="3" style="left:10px;top:50px;width:80px;height:80px">
                      <img alt="Diagram 1-1" src="data:image/png;base64,{image_b64}">
                    </div>
                    <div class="book-element book-pgn-record review" data-reading-order="4" style="left:10px;top:140px;width:180px;height:80px">
                      Do weryfikacji Diagram 1-1 1. @e4 PGN requires review; strict export is blocked.
                      unmapped_chess_glyphs pgn_replay_errors http://localhost:5001/debug
                    </div>
                  </section>
                </body></html>
                """,
                encoding="utf-8",
            )

            payload = rebuild_chess_source_html_export(html_path, root / "out")

            index_html = (root / "out" / "index.html").read_text(encoding="utf-8")
            book_json = json.loads((root / "out" / "data" / "book.json").read_text(encoding="utf-8"))
            games_pgn = (root / "out" / "data" / "games.pgn").read_text(encoding="utf-8")
            diagram_path = root / "out" / book_json["pages"][0]["diagrams"][0]["image_path"]
            diagram_asset_exists = diagram_path.is_file()
            audit_report_exists = (root / "out" / "reports" / "conversion-audit.md").is_file()

        self.assertEqual(payload["summary"]["html_pages"], 1)
        self.assertEqual(payload["summary"]["diagrams_total"], 1)
        self.assertEqual(payload["summary"]["pgn_total"], 1)
        self.assertEqual(payload["summary"]["accepted_pgn"], 0)
        self.assertEqual(payload["summary"]["fen_accepted"], 0)
        self.assertTrue(book_json["pages"][0]["diagrams"][0]["image_path"].startswith("assets/diagrams/"))
        self.assertIn("Diagram 1-1 White to move", index_html)
        self.assertIn("Needs human review", index_html)
        self.assertNotIn("fen_not_recognized", index_html)
        self.assertNotIn("PGN requires review; strict export is blocked", index_html)
        self.assertNotIn("localhost", index_html)
        self.assertEqual(games_pgn, "")
        self.assertTrue(diagram_asset_exists)
        self.assertTrue(audit_report_exists)

    def test_render_exposes_filters_copy_buttons_only_for_accepted_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            structure = {
                "chapters": [{"chapter_no": 1, "title": "Mating motifs", "start_book_page": 1, "end_book_page": 2}],
                "pdf_page_count": 2,
            }
            positions = {
                "positions": [
                    {
                        "id": "ch01_ex_001",
                        "type": "exercise",
                        "chapter_no": 1,
                        "chapter_title": "Mating motifs",
                        "label": "Ex. 1-1",
                        "diagram_page": 1,
                        "solution_page": 2,
                        "side_to_move": "white",
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "solution_pgn": (
                            '[Event "Ex. 1-1"]\n'
                            '[Site "?"]\n'
                            '[Date "????.??.??"]\n'
                            '[White "?"]\n'
                            '[Black "?"]\n'
                            '[Result "*"]\n'
                            '[SourcePage "1"]\n\n'
                            '1. e4 *'
                        ),
                        "status": "accepted",
                        "warnings": [],
                        "critical_warnings": [],
                        "source_crop": "diagrams/source/ch01_ex_001.webp",
                        "rendered_diagram": "assets/diagram_svg/ch01_ex_001.svg",
                    },
                    {
                        "id": "ch01_ex_002",
                        "type": "exercise",
                        "chapter_no": 1,
                        "chapter_title": "Mating motifs",
                        "label": "Ex. 1-2",
                        "diagram_page": 1,
                        "solution_page": None,
                        "side_to_move": "white",
                        "fen": "",
                        "solution_pgn": "",
                        "status": "missing_fen",
                        "warnings": ["fen_missing"],
                        "critical_warnings": [],
                        "source_crop": "",
                    },
                ]
            }
            pgn_payload = build_study_pgn(positions, out)
            qa = validate_study_export(
                ChessStudyConfig(pdf=out / "missing.pdf", html=None, out=out),
                current_audit={"status": "not_provided"},
                structure={**structure, "validation": {"status": "passed", "errors": []}},
                segments={"pages": []},
                diagrams={"diagram_count": 2, "diagrams": []},
                positions=positions,
                page_model={"summary": {"page_images": 0, "pages_with_extractable_text": 0, "copyable_text_characters": 0}, "pages": []},
                notation_fragments={"fragment_count": 0, "fragments": []},
                pgn_payload=pgn_payload,
                exercises={"exercises": positions["positions"]},
                final_test={"positions": []},
            )

            render_study_html(
                out,
                structure=structure,
                positions=positions,
                qa_report=qa,
                page_model={
                    "pages": [
                        {
                            "page": 1,
                            "paragraphs": ["Ex. 1-1", "White to move."],
                            "elements": [
                                {
                                    "type": "heading",
                                    "page": 1,
                                    "bbox": [20, 20, 90, 34],
                                    "reading_order": 0,
                                    "source_kind": "pdf-text-layer",
                                    "text": "Ex. 1-1",
                                },
                                {
                                    "type": "text",
                                    "page": 1,
                                    "bbox": [20, 50, 120, 64],
                                    "reading_order": 1,
                                    "source_kind": "pdf-text-layer",
                                    "text": "White to move.",
                                },
                                {
                                    "type": "text",
                                    "page": 1,
                                    "bbox": [20, 66, 24, 74],
                                    "reading_order": 2,
                                    "source_kind": "pdf-text-layer",
                                    "text": "a",
                                },
                                {
                                    "type": "text",
                                    "page": 1,
                                    "bbox": [20, 76, 34, 84],
                                    "reading_order": 3,
                                    "source_kind": "pdf-text-layer",
                                    "text": "Ii",
                                },
                            ],
                        }
                    ]
                },
                notation_fragments={
                    "fragments": [
                        {
                            "id": "n_p001_001",
                            "page": 1,
                            "source_page": 1,
                            "raw_text": "1. @e4",
                            "normalized_text": "1. @e4",
                            "pgn": "",
                            "status": "needs_review",
                            "warnings": ["unmapped_chess_glyphs"],
                            "bbox": [24, 72, 140, 90],
                        }
                    ]
                },
            )
            html = (out / "index.html").read_text(encoding="utf-8")
            kindle_html = (out / "kindle.html").read_text(encoding="utf-8")
            audit_html = (out / "standalone_audit.html").read_text(encoding="utf-8")
            qa_summary = json.loads((out / "qa_report.json").read_text(encoding="utf-8"))["summary"]

        self.assertIn("data-status-filter", html)
        self.assertIn("Copy FEN", html)
        self.assertIn("Copy PGN", html)
        self.assertIn("missing_fen", html)
        self.assertIn("Debug view", html)
        self.assertNotIn("localhost", html)
        self.assertNotIn("localhost", kindle_html)
        self.assertIn('class="book-elements"', kindle_html)
        self.assertIn('class="scorebar"', kindle_html)
        self.assertIn('class="audit-summary"', kindle_html)
        self.assertIn('<link rel="icon" href="data:,">', kindle_html)
        self.assertNotIn("1. 1. Mating motifs", kindle_html)
        self.assertIn('class="page study-page"', kindle_html)
        self.assertIn('class="study-block diagram-card"', kindle_html)
        self.assertIn('class="study-diagram"', kindle_html)
        self.assertIn('class="book-text-block study-prose"', kindle_html)
        self.assertIn("White to move.", kindle_html)
        self.assertNotIn('data-reading-order="2">a</p>', kindle_html)
        self.assertNotIn('data-reading-order="3">Ii</p>', kindle_html)
        self.assertIn('class="study-block study-notation notation-fragment study-review"', kindle_html)
        self.assertIn('class="fen book-fen"', kindle_html)
        self.assertIn('class="pgn book-pgn-record"', kindle_html)
        self.assertEqual(kindle_html.count('class="pgn book-pgn-record"'), 1)
        self.assertNotIn('class="summary"', kindle_html)
        self.assertNotIn("data-copy-target", kindle_html)
        self.assertNotIn("localhost", audit_html)
        self.assertEqual(qa_summary["fens_accepted"], 1)
        self.assertEqual(qa_summary["accepted_pgn"], 1)

    def test_accepted_fen_requires_rendered_diagram(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            positions = {
                "positions": [
                    {
                        "id": "ch01_ex_001",
                        "type": "exercise",
                        "chapter_no": 1,
                        "label": "Ex. 1-1",
                        "diagram_page": 1,
                        "solution_page": 2,
                        "side_to_move": "white",
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "solution_pgn": "",
                        "status": "accepted",
                        "warnings": [],
                        "critical_warnings": [],
                        "source_crop": "assets/diagram_crops/ch01_ex_001.webp",
                        "rendered_diagram": "",
                    }
                ]
            }

            qa = validate_study_export(
                ChessStudyConfig(pdf=out / "missing.pdf", html=None, out=out),
                current_audit={"status": "not_provided"},
                structure={"pdf_page_count": 1, "chapters": [], "validation": {"status": "passed", "errors": []}},
                segments={"pages": []},
                diagrams={"diagram_count": 1, "diagrams": []},
                positions=positions,
                page_model={"summary": {"page_images": 0, "pages_with_extractable_text": 0, "copyable_text_characters": 0}, "pages": []},
                notation_fragments={"fragment_count": 0, "fragments": []},
                pgn_payload={"accepted_pgn_count": 0},
                exercises={"exercises": positions["positions"]},
                final_test={"positions": []},
            )

        self.assertEqual(qa["status"], "FAIL")
        self.assertTrue(any(problem["code"] == "accepted_fen_missing_render" for problem in qa["problems"]))

    def test_accepted_notation_with_unmapped_glyphs_fails_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            pgn = (
                '[Event "Source page 1"]\n'
                '[Site "?"]\n'
                '[Date "????.??.??"]\n'
                '[White "?"]\n'
                '[Black "?"]\n'
                '[Result "*"]\n'
                '[SourcePage "1"]\n\n'
                '1. e4 *'
            )

            qa = validate_study_export(
                ChessStudyConfig(pdf=out / "missing.pdf", html=None, out=out),
                current_audit={"status": "not_provided"},
                structure={"pdf_page_count": 1, "chapters": [], "validation": {"status": "passed", "errors": []}},
                segments={"pages": []},
                diagrams={"diagram_count": 0, "diagrams": []},
                positions={"positions": []},
                page_model={"summary": {"page_images": 0, "pages_with_extractable_text": 0, "copyable_text_characters": 0}, "pages": []},
                notation_fragments={
                    "fragment_count": 1,
                    "fragments": [
                        {
                            "id": "n_p001_001",
                            "page": 1,
                            "pgn": pgn,
                            "status": "accepted",
                            "warnings": ["unmapped_chess_glyphs"],
                        }
                    ],
                },
                pgn_payload={"accepted_pgn_count": 0},
                exercises={"exercises": []},
                final_test={"positions": []},
            )

        self.assertEqual(qa["status"], "FAIL")
        self.assertTrue(any(problem["code"] == "accepted_notation_has_unmapped_glyphs" for problem in qa["problems"]))

    def test_accepted_notation_with_raw_context_gap_fails_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            pgn = (
                '[Event "Source page 1"]\n'
                '[Site "?"]\n'
                '[Date "????.??.??"]\n'
                '[White "?"]\n'
                '[Black "?"]\n'
                '[Result "*"]\n'
                '[SourcePage "1"]\n\n'
                '1. e4 *'
            )

            qa = validate_study_export(
                ChessStudyConfig(pdf=out / "missing.pdf", html=None, out=out),
                current_audit={"status": "not_provided"},
                structure={"pdf_page_count": 1, "chapters": [], "validation": {"status": "passed", "errors": []}},
                segments={"pages": []},
                diagrams={"diagram_count": 0, "diagrams": []},
                positions={"positions": []},
                page_model={"summary": {"page_images": 0, "pages_with_extractable_text": 0, "copyable_text_characters": 0}, "pages": []},
                notation_fragments={
                    "fragment_count": 1,
                    "fragments": [
                        {
                            "id": "n_p001_001",
                            "page": 1,
                            "pgn": pgn,
                            "status": "accepted",
                            "warnings": [],
                            "glyph_diagnostics": [
                                {
                                    "source": "html-assist-no-raw-glyph-context",
                                    "reasons": ["raw_char_context_unavailable"],
                                }
                            ],
                        }
                    ],
                },
                pgn_payload={"accepted_pgn_count": 0},
                exercises={"exercises": []},
                final_test={"positions": []},
            )

        self.assertEqual(qa["status"], "FAIL")
        self.assertTrue(any(problem["code"] == "accepted_notation_missing_raw_glyph_context" for problem in qa["problems"]))


if __name__ == "__main__":
    unittest.main()
