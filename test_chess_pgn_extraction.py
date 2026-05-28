from __future__ import annotations

from collections import Counter
import unittest

from bs4 import BeautifulSoup

from chess_pgn_extractor import (
    attach_fen_candidates_to_pgn_records,
    build_combined_pgn,
    extract_chess_pgn_records_from_text,
    render_chess_pgn_html_parts,
    summarize_chess_pgn_records,
)
from kindle_semantic_cleanup import _extract_logical_blocks


class ChessPgnExtractionTests(unittest.TestCase):
    def test_extracts_black_to_move_combination_as_full_pgn(self) -> None:
        text = """
        Diagram 1-3
        Berne 1987
        1...Qe2+! 2.Kh1 Rxh2!! 3.Kxh2 Bh4#
        0-1
        """

        records = extract_chess_pgn_records_from_text(text, page_num=9, source_title="Fundamenty", ocr_confidence=0.9)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn('[Event "Fundamenty"]', records[0].pgn)
        self.assertIn("1... Qe2+! 2. Kh1 Rxh2!! 3. Kxh2 Bh4# 0-1", records[0].pgn)

    def test_attaches_fen_to_pgn_headers_for_downloadable_record(self) -> None:
        text = "Diagram 2-1\nA.White - B.Black London 1899\n1. e4 e5 2. Nf3 Nc6 *"
        records = extract_chess_pgn_records_from_text(text, page_num=20, ocr_confidence=0.88)
        records = attach_fen_candidates_to_pgn_records(
            records,
            ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"],
        )

        self.assertIn('[SetUp "1"]', records[0].pgn)
        self.assertIn('[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"]', records[0].pgn)
        self.assertTrue(build_combined_pgn(records).endswith("\n"))

    def test_summary_and_html_render_acceptance_metrics(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 3-1\nExample - Player 2020\n1. d4 d5 2. c4 e6 *",
            page_num=31,
            ocr_confidence=0.9,
        )

        summary = summarize_chess_pgn_records(records)
        html_parts = render_chess_pgn_html_parts(records)

        self.assertEqual(summary["candidate_game_count"], 1)
        self.assertEqual(summary["valid_pgn_count"], 1)
        self.assertGreaterEqual(summary["coverage"], 0.5)
        self.assertIn('class="chess-pgn"', html_parts[0])
        self.assertIn("Pobierz PGN", html_parts[0])

    def test_html_render_can_omit_internal_download_link(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 4-1\nExample - Player 2020\n1. e4 e5 2. Nf3 Nc6 *",
            page_num=32,
            ocr_confidence=0.9,
        )

        html_parts = render_chess_pgn_html_parts(records, download_href="")

        self.assertIn('class="chess-pgn"', html_parts[0])
        self.assertNotIn("chess_games.pgn", html_parts[0])
        self.assertNotIn("Pobierz PGN", html_parts[0])

    def test_semantic_cleanup_keeps_generated_pgn_section(self) -> None:
        soup = BeautifulSoup(
            '<section class="chess-pgn" id="game-1"><h2>PGN: game</h2>'
            '<pre class="chess-pgn-text"><code>1. e4 e5 *</code></pre></section>',
            "xml",
        )

        blocks = _extract_logical_blocks(
            [soup.find("section")],
            repeated_counts=Counter(),
            keep_first_seen=set(),
            title="",
            author="",
        )

        self.assertEqual(blocks[0]["type"], "raw-html")
        self.assertIn('class="chess-pgn"', blocks[0]["html"])


if __name__ == "__main__":
    unittest.main()
