from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import fitz
from PIL import Image

from converter import CHESS_REFLOW_CSS, ConversionConfig, dedupe_html_ids
from publication_pipeline import _fragment_to_blocks
from pymupdf_chess_extractor import (
    _clean_chess_notation_line,
    _is_single_board_coordinate_line,
    _looks_like_board_coordinate_noise,
    _scan_chess_pgn_extra_artifacts,
    extract_chess_notation_pdf_reflow,
)
from chess_pgn_extractor import ChessPgnRecord


class ChessNotationReflowTests(unittest.TestCase):
    def test_large_collection_extractor_preserves_notation_and_skips_raster_boards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "jobava-sample.pdf"
            image_buffer = io.BytesIO()
            Image.new("RGB", (120, 120), "white").save(image_buffer, format="PNG")
            board_bytes = image_buffer.getvalue()

            doc = fitz.open()
            page = doc.new_page(width=612, height=792)
            page.insert_text((36, 36), "1", fontsize=9)
            page.insert_text((72, 72), "D00 Aravindh,Chithambaram VR. 2731 Praggnanandhaa,R 2758", fontsize=10)
            page.insert_text((72, 96), "Jobava London Attack", fontsize=10)
            page.insert_text((72, 128), "1.Nc3 d5 2.d4 Nf6 3.Bf4 c5 4.e3 cxd4 5.exd4", fontsize=10)
            page.insert_text((72, 152), "a b c d e f g h", fontsize=8)
            page.insert_image(fitz.Rect(72, 190, 192, 310), stream=board_bytes)
            doc.save(pdf_path)
            doc.close()

            content = extract_chess_notation_pdf_reflow(str(pdf_path), ConversionConfig(), {})

        html = "\n".join(content["chapters"][0]["html_parts"])
        self.assertEqual(content["method"], "chess-notation-text-reflow")
        self.assertEqual(content["images"], [])
        self.assertIn("chess-notation-text", html)
        self.assertIn("1.Nc3 d5 2.d4 Nf6", html)
        self.assertIn('class="chess-pgn"', html)
        self.assertIn("Final FEN:", html)
        self.assertNotIn("chess_games.pgn", html)
        self.assertNotIn("a b c d e f g h", html)
        self.assertEqual(content["metadata"]["skipped_embedded_image_count"], 1)
        self.assertEqual(content["metadata"]["chess_pgn"]["valid_pgn_count"], 1)
        self.assertEqual(content["metadata"]["chess_pgn"]["derived_final_fen_count"], 1)
        self.assertEqual(
            sorted(artifact["key"] for artifact in content["extra_artifacts"]),
            ["chess_pgn", "chess_pgn_html", "pdf_layout_preview"],
        )
        preview = next(artifact for artifact in content["extra_artifacts"] if artifact["key"] == "pdf_layout_preview")
        self.assertEqual(preview["content_type"], "text/html; charset=utf-8")
        self.assertIn(b'class="pdf-page"', preview["data"])

    def test_xml_fragment_parser_preserves_multi_token_classes(self) -> None:
        marker_blocks = _fragment_to_blocks('<span id="book-page-12" class="page-marker"></span>', page_index=11)
        notation_blocks = _fragment_to_blocks(
            '<pre class="chess-notation-page chess-notation-text" data-page="12"><code>1.Nc3 d5</code></pre>',
            page_index=11,
        )

        self.assertEqual(marker_blocks[0].block_type, "page_marker")
        self.assertEqual(notation_blocks[0].style_class, "chess-notation-page chess-notation-text")

    def test_epub_chapter_html_dedupes_repeated_page_marker_ids(self) -> None:
        html = (
            '<html><body>'
            '<span id="book-page-1" class="page-marker"></span>'
            '<pre id="notation-1">A</pre>'
            '<span id="book-page-1" class="page-marker"></span>'
            '<span id="book-page-1" class="page-marker"></span>'
            '<pre id="notation-1">B</pre>'
            "</body></html>"
        )

        deduped = dedupe_html_ids(html)

        self.assertIn('id="book-page-1"', deduped)
        self.assertIn('id="book-page-1-2"', deduped)
        self.assertIn('id="book-page-1-3"', deduped)
        self.assertIn('id="notation-1"', deduped)
        self.assertIn('id="notation-1-2"', deduped)

    def test_notation_cleanup_removes_inline_board_coordinate_fragments(self) -> None:
        self.assertEqual(_clean_chess_notation_line("a b c d e f g h1 D00"), "1 D00")
        self.assertEqual(_clean_chess_notation_line("1 a b c d e f g h 1"), "1 1")
        self.assertTrue(_looks_like_board_coordinate_noise("65 65"))
        self.assertTrue(_looks_like_board_coordinate_noise("8 8 7 7"))
        self.assertTrue(_is_single_board_coordinate_line("a"))
        self.assertTrue(_is_single_board_coordinate_line("8"))
        self.assertFalse(_looks_like_board_coordinate_noise("15.Bxg6 hxg6"))

    def test_notation_cleanup_decodes_chessbase_private_symbols_for_epub_text(self) -> None:
        cleaned = _clean_chess_notation_line(
            "14...Bxd3\ue02e -0.68/21 15.Qxd3 \ue027b2 [\ue020 28.Rxc8]"
        )

        self.assertIn("14...Bxd3\u2a71 -0.68/21", cleaned)
        self.assertIn("15.Qxd3 Bb2", cleaned)
        self.assertIn("\u2312 28.Rxc8", cleaned)
        self.assertFalse(any(0xE000 <= ord(char) <= 0xF8FF for char in cleaned))

    def test_review_only_pgn_records_do_not_create_pgn_download_artifact(self) -> None:
        record = ChessPgnRecord(
            id="review-only",
            source_pages=[1],
            title="Review only",
            headers={"Event": "Review only", "Result": "*"},
            movetext="1. e4 e5 10. Qh5 *",
            pgn='[Event "Review only"]\n[Result "*"]\n\n1. e4 e5 10. Qh5 *\n',
            raw_text="Raw OCR: 1.e4 e5 10.Qh5 *",
            status="requires_review",
            warnings=["move_number_jump"],
        )

        artifacts = _scan_chess_pgn_extra_artifacts([record], source_title="Review only")

        self.assertEqual([artifact["key"] for artifact in artifacts], ["chess_pgn_html"])
        self.assertIn(b"Raw OCR: 1.e4 e5 10.Qh5", artifacts[0]["data"])

    def test_legal_exercise_records_create_separate_exercises_pgn_artifact(self) -> None:
        record = ChessPgnRecord(
            id="exercise-only",
            source_pages=[1],
            title="Diagram 1-1",
            headers={"Event": "Diagram 1-1", "Result": "*"},
            movetext="",
            pgn="",
            raw_text="Diagram 1-1\n1. e4 e5",
            status="requires_review",
            warnings=["pgn_replay_errors"],
        )

        artifacts = _scan_chess_pgn_extra_artifacts(
            [record],
            source_title="Exercise only",
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "1-1",
                    "filename": "diagram_1_1.png",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.91,
                    "requires_review": False,
                }
            ],
        )

        self.assertEqual([artifact["key"] for artifact in artifacts], ["chess_exercises_pgn", "chess_pgn_html"])
        self.assertEqual(artifacts[0]["filename"], "chess_exercises.pgn")
        self.assertIn(b'[SetUp "1"]', artifacts[0]["data"])
        self.assertIn(b"1. e4 e5", artifacts[0]["data"])

    def test_chess_notation_css_uses_high_contrast_text(self) -> None:
        self.assertIn(".chess-notation-text", CHESS_REFLOW_CSS)
        self.assertIn(".chess-pgn-text", CHESS_REFLOW_CSS)
        self.assertIn("color: #111", CHESS_REFLOW_CSS)
        notation_section = CHESS_REFLOW_CSS.split(".chess-notation-page", 1)[1]
        self.assertNotIn("color: #666", notation_section)
        self.assertNotIn("color: #999", notation_section)

    def test_notation_reflow_adds_layout_diagrams_to_html_artifact(self) -> None:
        crop_path = Path("reference_inputs/chess_fen/crops/fundamenty_1_1_scan_chess_p010_runtime_01.png")
        if not crop_path.is_file():
            self.skipTest("reference chess crop fixture is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "notation-with-diagram.pdf"
            doc = fitz.open()
            page = doc.new_page(width=420, height=420)
            page.insert_text((40, 34), "Diagram 1", fontsize=11)
            page.insert_text((40, 380), "1. e4 e5 2. Nf3 Nc6 *", fontsize=11)
            page.insert_image(fitz.Rect(50, 50, 370, 370), filename=str(crop_path))
            doc.save(pdf_path)
            doc.close()

            content = extract_chess_notation_pdf_reflow(
                str(pdf_path),
                ConversionConfig(
                    chess_diagram_dpi=120,
                    scanned_chess_min_grid_confidence=0.30,
                    chess_fen_piece_template_dir="",
                ),
                {},
            )

        artifact_keys = sorted(artifact["key"] for artifact in content["extra_artifacts"])
        self.assertIn("chess_diagrams", artifact_keys)
        html_artifact = next(artifact for artifact in content["extra_artifacts"] if artifact["key"] == "chess_pgn_html")
        html = html_artifact["data"].decode("utf-8")
        self.assertIn('class="book-element book-diagram"', html)
        self.assertIn('data-km-view="chess-book-review"', html)
        self.assertNotIn("localhost", html)


if __name__ == "__main__":
    unittest.main()
