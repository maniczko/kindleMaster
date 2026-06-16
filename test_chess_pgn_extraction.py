from __future__ import annotations

from collections import Counter
import io
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
import chess
import chess.pgn

from chess_pgn_extractor import (
    ChessBookLayoutElement,
    ChessBookLayoutPage,
    ChessDiagramRecord,
    ChessPgnRecord,
    DiagramSolutionCandidate,
    _extract_pgn_tokens,
    _legalize_san_token_with_board,
    _normalize_ocr_san_token,
    _prepare_mainline_token_source,
    _select_diagram_mainline_candidate,
    _split_candidate_game_blocks,
    annotate_records_with_replayed_fens,
    attach_fen_candidates_to_pgn_records,
    build_combined_pgn,
    build_exercises_pgn,
    build_pgn_download_html,
    chess_ocr_normalization_candidates,
    extract_chess_pgn_records_from_text,
    merge_chess_pgn_continuation_records,
    normalize_ocr_text_for_pgn,
    render_chess_pgn_html_parts,
    summarize_chess_pgn_records,
)
from kindle_semantic_cleanup import _extract_logical_blocks


ARAVINDH_PRAGG_FULL_RAW = """
D00
Aravindh, Chithambaram VR.
Praggnanandhaa, R
Titled Tue 11th Mar Early blitz (7)
[Kitty Kat]
B13: Caro-Kann: Exchange Variation
and Panov-Botvinnik Attack
1.Nc3 d5 2.d4 Nf6 3.Bf4 c5 4.e3
cxd4 5.exd4 a6
6.Bd3
Nc6
7.Nce2
Bg4
8.h3
The position is equal. 8... Bh5 9.g4 Bg6
10.Nf3 e6
11.Ne5 Nxe5 12.Bxe5 Nd7 13.Bg3
Qb6 14.c3-0. 68/21
[ 14.a4=-0. 28/17is superior. ]
14... Be7-0. 18/18
[ 14... Qxb2 15.Rb1 Qxa2 16.Rxb7 ]
[ 14... Bxd3 -0. 68/21 15.Qxd3 Qxb2 ]
15.Bxg6= hxg6
(Diagram)
16.Qb3 Qc6 17.Nf4 b5 18.Nd3 a5
19.a4 b4 20.Rc1 Qc4
21.Qd1-1. 12/21
[ 21.Qxc4 -0. 49/23dxc4 22.Ne5 ]
21... bxc3
22.Rxc3
Qxd4
23.Kf1
[ 23. O-O -1. 21/21was called for. ]
23... O-O-+ 24.Kg2 Rac8 25.Ne5 Qxd1
26.Rxd1 Nxe5 27.Bxe5 Bb4 28.Rg3
-2. 19/22
[ 28.Rxc8-1. 76/22 Rxc8 29.f4 ]
28... Rc2 29.h4-2. 28/22
[ 29.g5-1. 86/22]
29... f6 30.Bd4 e5
(Diagram)
31.Bc3 Bc5-1. 39/21
[Black should play 31... d4-+
-1. 78/20
32.Bxb4 axb4 ]
32.Be1-1. 99/20
[ 32.Rd2 -1. 39/21 Rxd2 33.Bxd2 ]
32... Bd4
[Worse is
32... Rxb2
33.Rxd5
Bd4
34.Rxa5]
33.h5-2. 64/21
[ 33.Rb3-1. 96/20was worth a try. ]
33... Kf7
[ 33... Rxb2 34.hxg6 f5 35.gxf5-+ ]
34.b4-3. 30/21
[ 34.Rd2-1. 98/20 Rc4 35.b3 ]
34... axb4 35.Rb1 Rb8
36.a5 Ra2 37.a6? -5. 45/21
[ 37.Rxb4-3. 38/22
might work better.
Rxb4 38.hxg6+ Kxg6 39.Bxb4 ]
(Diagram)
37... Rxa6
38.Rgb3
Ra2
39.Rxb4
Rxb4 40.Rxb4 gxh5
41.gxh5
Endgame KRB-KRB 41... Re2
42.Rb7+
Kg8
43.Kf1
Ra2
44.Rd7
Kh7 45.Rxd5 Kh6
46.Rd7 Rb2
[ 46... Kxh5 47.Rxg7 f5 48.Rg3]
47.Rf7 Ra2? 0. 01/22
[And not
47... Kxh5
48.Rxg7
f5
49.f3]
[Better is
47... Rb1-+
-2. 81/24... Bc3 is the strong threat. 48.Ke2
Rb2+ 49.Kf1 Ra2 ]
48.Rd7! =
White does not recover from
this. 48... Ra7
[Stronger than 48... Kxh5 49.Rxg7 f5
50.Rg3]
49.Bd2+ Kxh5 50.Rd8 Kg4
a i m i n g f o r... K f 3. 51.Ke2
Kf5
Strongly threatening... Ra2. 52.Rh8?
[ 52.f3-3. 29/23]
52... g5
Threatens to win with... Ra2.
Black is clearly winning. 53.Rf8
Ra2
54.f3 Bc3
Weighted Error Value: White=0. 51/
Black=0. 36
0-1
"""


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
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(text, page_num=20, ocr_confidence=0.88)
        )
        records = attach_fen_candidates_to_pgn_records(
            records,
            ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"],
        )
        records = annotate_records_with_replayed_fens(records)

        self.assertIn('[SetUp "1"]', records[0].pgn)
        self.assertIn('[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"]', records[0].pgn)
        self.assertTrue(build_combined_pgn(records).endswith("\n"))

    def test_attach_fen_reconstructs_wrong_side_marker_before_replay(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 2-2\nScan sample\n1. e5 2. Nf3 Nc6 *",
            page_num=21,
            source_title="Scanned page",
            ocr_confidence=0.9,
        )

        self.assertEqual(records[0].movetext, "1. e5 2. Nf3 Nc6 *")
        records = attach_fen_candidates_to_pgn_records(
            records,
            ["rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1"],
        )
        records = annotate_records_with_replayed_fens(records)

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1... e5 2. Nf3 Nc6 *", records[0].movetext)
        self.assertIn("side_to_move_marker_repaired", records[0].warnings)
        self.assertNotIn("side_to_move_mismatch", records[0].warnings)
        self.assertNotIn("move_number_jump", records[0].warnings)

    def test_board_legalizer_repairs_unique_bare_square_candidate(self) -> None:
        board = chess.Board("8/8/8/k1P5/1n5Q/8/1N2B3/7K w - - 2 2")

        repaired, warnings = _legalize_san_token_with_board("e4", board)

        self.assertEqual(repaired, "Qe4")
        self.assertIn("san_board_legalization_used", warnings)
        self.assertIn("san_board_unique_candidate_used", warnings)

    def test_board_legalizer_repairs_unique_piece_move_candidate(self) -> None:
        board = chess.Board("8/8/8/k1P5/1n5Q/8/1N2B3/7K w - - 2 2")

        repaired, warnings = _legalize_san_token_with_board("Qd8", board)

        self.assertEqual(repaired, "Qd8#")
        self.assertIn("san_board_legalization_used", warnings)
        self.assertIn("san_board_unique_candidate_used", warnings)

    def test_board_legalizer_rejects_ambiguous_piece_move_candidate(self) -> None:
        board = chess.Board("7k/8/8/8/8/1N3N2/8/K7 w - - 0 1")

        repaired, warnings = _legalize_san_token_with_board("Nd4", board)

        self.assertEqual(repaired, "Nd4")
        self.assertEqual(warnings, ["san_board_ambiguous_candidate_rejected"])

    def test_board_legalizer_rejects_ambiguous_promotion_square(self) -> None:
        board = chess.Board("k7/3P4/K7/8/8/8/8/8 w - - 0 1")

        repaired, warnings = _legalize_san_token_with_board("d8", board)

        self.assertEqual(repaired, "d8")
        self.assertEqual(warnings, ["san_board_ambiguous_candidate_rejected"])

    def test_attach_fen_legalizes_unique_bare_square_before_replay(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 2-3\nScan sample\n1. e4 Na2 *",
            page_num=22,
            source_title="Scanned page",
            ocr_confidence=0.9,
        )

        records = attach_fen_candidates_to_pgn_records(
            records,
            ["8/8/8/k1P5/1n5Q/8/1N2B3/7K w - - 0 1"],
        )
        records = annotate_records_with_replayed_fens(records)

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. Qe4 Na2 *", records[0].movetext)
        self.assertIn("san_board_unique_candidate_used", records[0].warnings)
        self.assertTrue(records[0].final_fen)

    def test_attach_fen_legalizes_black_to_move_fragment_before_replay(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 2-4\nScan sample\n1... e2 2. Na3 *",
            page_num=23,
            source_title="Scanned page",
            ocr_confidence=0.9,
        )

        records = attach_fen_candidates_to_pgn_records(
            records,
            ["7k/1n2b3/8/1N5q/K1p5/8/8/8 b - - 0 1"],
        )
        records = annotate_records_with_replayed_fens(records)

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1... Qe2 2. Na3 *", records[0].movetext)
        self.assertIn("san_board_unique_candidate_used", records[0].warnings)

    def test_attach_fen_legalizes_black_to_move_second_fragment_before_replay(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 2-5\nScan sample\n2... e2 3. Na3 *",
            page_num=24,
            source_title="Scanned page",
            ocr_confidence=0.9,
        )

        records = attach_fen_candidates_to_pgn_records(
            records,
            ["7k/1n2b3/8/1N5q/K1p5/8/8/8 b - - 0 2"],
        )
        records = annotate_records_with_replayed_fens(records)

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("2... Qe2 3. Na3 *", records[0].movetext)
        self.assertIn("san_board_unique_candidate_used", records[0].warnings)

    def test_attach_fen_leaves_ambiguous_promotion_square_in_review(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 2-6\nScan sample\n1. d8 Ka7 *",
            page_num=25,
            source_title="Scanned page",
            ocr_confidence=0.9,
        )

        records = attach_fen_candidates_to_pgn_records(
            records,
            ["k7/3P4/K7/8/8/8/8/8 w - - 0 1"],
        )
        records = annotate_records_with_replayed_fens(records)

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("san_board_ambiguous_candidate_rejected", records[0].warnings)
        self.assertIn("pgn_replay_errors", records[0].warnings)

    def test_summary_and_html_render_acceptance_metrics(self) -> None:
        records = annotate_records_with_replayed_fens(extract_chess_pgn_records_from_text(
            "Diagram 3-1\nExample - Player 2020\n1. d4 d5 2. c4 e6 *",
            page_num=31,
            ocr_confidence=0.9,
        ))

        summary = summarize_chess_pgn_records(records)
        html_parts = render_chess_pgn_html_parts(records)

        self.assertEqual(summary["candidate_game_count"], 1)
        self.assertEqual(summary["valid_pgn_count"], 1)
        self.assertEqual(summary["derived_final_fen_count"], 1)
        self.assertEqual(summary["manual_review_count"], 0)
        self.assertEqual(summary["continuation_fragment_count"], 0)
        self.assertEqual(summary["warning_counts"], {})
        self.assertGreaterEqual(summary["coverage"], 0.5)
        self.assertIn('class="chess-pgn"', html_parts[0])
        self.assertIn("Final FEN:", html_parts[0])
        self.assertIn("Pobierz PGN", html_parts[0])

    def test_html_render_can_omit_internal_download_link(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 4-1\nExample - Player 2020\n1. e4 e5 2. Nf3 Nc6 *",
                page_num=32,
                ocr_confidence=0.9,
            )
        )

        html_parts = render_chess_pgn_html_parts(records, download_href="")

        self.assertIn('class="chess-pgn"', html_parts[0])
        self.assertNotIn("chess_games.pgn", html_parts[0])
        self.assertNotIn("Pobierz PGN", html_parts[0])

    def test_replays_legal_pgn_to_final_fen_without_corrupting_headers(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 *",
                page_num=1,
                source_title="Legal sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertEqual(
            records[0].final_fen,
            "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
        )
        self.assertNotIn("[FEN", records[0].pgn)
        self.assertIn("Final FEN:", build_pgn_download_html(records))

    def test_pgn_download_html_includes_inline_diagram_fen_records(self) -> None:
        html = build_pgn_download_html(
            [],
            title="Diagram FEN",
            diagram_records=[
                {
                    "page": 12,
                    "filename": "scan_chess_p012_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "extension": "png",
                    "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
                    "confidence": 0.91,
                    "requires_review": False,
                    "method": "image-template-board",
                    "warnings": ["side_to_move_inferred"],
                }
            ],
        )

        self.assertIn("Detected chess diagrams / FEN", html)
        self.assertIn("Detected chess diagrams: 1", html)
        self.assertIn("Detected diagram FEN: 1", html)
        self.assertIn("FEN zaakceptowany", html)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("8/8/8/8/8/8/8/8 w - - 0 1", html)
        self.assertIn("Kopiuj FEN", html)

    def test_exercise_html_ai_review_only_never_exports_invalid_pgn(self) -> None:
        records = [
            ChessPgnRecord(
                id="review-1",
                source_pages=[12],
                title="Diagram 1-2 A. Yusu ov- P'Schlosser",
                headers={},
                movetext="",
                pgn="",
                confidence=0.32,
                status="requires_review",
                warnings=["ocr_noise"],
                raw_text=(
                    "Diagram 1-2\nA. Yusu ov- P'Schlosser\n"
                    "1. ge5+- Threateninggg5tand mate 1... gd7 2.gg5t @h7"
                ),
            )
        ]
        diagram_records = [
            {
                "diagram_number": "1-2",
                "page": 12,
                "source_order": 1,
                "caption_match_score": 95,
                "image_data": b"\x89PNG\r\n\x1a\nsample",
                "extension": "png",
                "fen": "6k1/6pp/8/4Q3/8/8/6PP/6K1 w - - 0 1",
                "confidence": 0.93,
                "requires_review": False,
                "selected_preprocess_variant": "autocontrast",
                "display_variant_used": "reader_enhanced",
            }
        ]

        with patch.dict(
            "os.environ",
            {
                "KINDLEMASTER_OPENAI_CHESS_REVIEW": "1",
                "KINDLEMASTER_OPENAI_CHESS_REVIEW_MODE": "review_only",
                "KINDLEMASTER_OPENAI_CHESS_PREMIUM_MODEL": "gpt-test-premium",
                "KINDLEMASTER_OPENAI_CHESS_BATCH_MODEL": "gpt-test-mini",
            },
            clear=False,
        ):
            html = build_pgn_download_html(records, title="AI review-only sample", diagram_records=diagram_records)

        self.assertIn("AI suggestions / review-only", html)
        self.assertIn("gpt-test-premium", html)
        self.assertIn("No automatic PGN export changes", html)
        self.assertIn("ai_review_status", html)
        self.assertIn("sequence_status", html)
        self.assertNotIn("Kopiuj PGN", html)

    def test_ai_repair_validated_export_accepts_single_local_replay_path(self) -> None:
        class FakeRepairProvider:
            name = "fake-ai-repair"

            def propose_chess_repair(self, context):
                return {
                    "status": "reviewed",
                    "provider": self.name,
                    "model": "unit-ai",
                    "fen_candidates": [
                        {
                            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                            "confidence": 0.93,
                            "notes": "unit candidate",
                        }
                    ],
                    "solution_line_candidates": [
                        {"movetext": "1. e4 e5", "confidence": 0.95, "notes": "unit line"}
                    ],
                    "ocr_token_repairs": [],
                    "confidence": 0.94,
                    "requires_human_review": False,
                    "notes": "single path",
                    "estimated_cost_usd": 0.001,
                }

        record = ChessPgnRecord(
            id="ai-repair-single",
            source_pages=[1],
            title="Diagram 1-1",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-1\nSolution line unclear in OCR.",
        )
        diagram_records = [
            {
                "page": 1,
                "diagram_number": "1-1",
                "filename": "diagram_1_1.png",
                "image_data": b"\x89PNG\r\n\x1a\nsample",
                "extension": "png",
                "confidence": 0.82,
                "requires_review": True,
            }
        ]

        with patch.dict(
            "os.environ",
            {
                "KINDLEMASTER_OPENAI_CHESS_REPAIR": "1",
                "KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE": "validated_export",
            },
            clear=False,
        ):
            with patch("chess_pgn_extractor._load_ai_repair_provider", return_value=FakeRepairProvider()):
                exercises_pgn = build_exercises_pgn([record], diagram_records=diagram_records)
                summary = summarize_chess_pgn_records([record], diagram_records=diagram_records)

        self.assertIn('[SetUp "1"]', exercises_pgn)
        self.assertIn('[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"]', exercises_pgn)
        self.assertIn("1. e4 e5", exercises_pgn)
        game = chess.pgn.read_game(io.StringIO(exercises_pgn))
        self.assertIsNotNone(game)
        self.assertFalse(game.errors)
        self.assertEqual(summary["ai_candidate_fen_count"], 1)
        self.assertEqual(summary["ai_candidate_line_count"], 1)
        self.assertEqual(summary["ai_validated_solution_count"], 1)

    def test_ai_repair_two_legal_paths_stays_review_without_export(self) -> None:
        class FakeRepairProvider:
            name = "fake-ai-repair"

            def propose_chess_repair(self, context):
                return {
                    "status": "reviewed",
                    "provider": self.name,
                    "model": "unit-ai",
                    "fen_candidates": [
                        {
                            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                            "confidence": 0.92,
                            "notes": "",
                        }
                    ],
                    "solution_line_candidates": [
                        {"movetext": "1. e4 e5", "confidence": 0.9, "notes": ""},
                        {"movetext": "1. d4 d5", "confidence": 0.88, "notes": ""},
                    ],
                    "ocr_token_repairs": [],
                    "confidence": 0.9,
                    "requires_human_review": True,
                    "notes": "two legal lines",
                    "estimated_cost_usd": 0.0,
                }

        record = ChessPgnRecord(
            id="ai-repair-ambiguous",
            source_pages=[1],
            title="Diagram 1-1",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            raw_text="Diagram 1-1\nOCR is ambiguous.",
        )
        diagram_records = [{"page": 1, "diagram_number": "1-1", "image_data": b"\x89PNG\r\n\x1a\nsample"}]

        with patch.dict(
            "os.environ",
            {
                "KINDLEMASTER_OPENAI_CHESS_REPAIR": "1",
                "KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE": "validated_export",
            },
            clear=False,
        ):
            with patch("chess_pgn_extractor._load_ai_repair_provider", return_value=FakeRepairProvider()):
                exercises_pgn = build_exercises_pgn([record], diagram_records=diagram_records)
                html = build_pgn_download_html([record], diagram_records=diagram_records)

        self.assertEqual(exercises_pgn, "")
        self.assertIn("validation_status:</strong> ambiguous", html)
        self.assertIn("ai_multiple_legal_paths", html)

    def test_ai_repair_illegal_san_stays_review_without_export(self) -> None:
        class FakeRepairProvider:
            name = "fake-ai-repair"

            def propose_chess_repair(self, context):
                return {
                    "status": "reviewed",
                    "provider": self.name,
                    "model": "unit-ai",
                    "fen_candidates": [
                        {
                            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                            "confidence": 0.92,
                            "notes": "",
                        }
                    ],
                    "solution_line_candidates": [
                        {"movetext": "1. e5", "confidence": 0.9, "notes": "illegal from start"}
                    ],
                    "ocr_token_repairs": [],
                    "confidence": 0.9,
                    "requires_human_review": True,
                    "notes": "illegal line",
                    "estimated_cost_usd": 0.0,
                }

        record = ChessPgnRecord(
            id="ai-repair-illegal",
            source_pages=[1],
            title="Diagram 1-1",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            raw_text="Diagram 1-1\nNoisy solution.",
        )
        diagram_records = [{"page": 1, "diagram_number": "1-1", "image_data": b"\x89PNG\r\n\x1a\nsample"}]

        with patch.dict(
            "os.environ",
            {
                "KINDLEMASTER_OPENAI_CHESS_REPAIR": "1",
                "KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE": "validated_export",
            },
            clear=False,
        ):
            with patch("chess_pgn_extractor._load_ai_repair_provider", return_value=FakeRepairProvider()):
                exercises_pgn = build_exercises_pgn([record], diagram_records=diagram_records)
                html = build_pgn_download_html([record], diagram_records=diagram_records)

        self.assertEqual(exercises_pgn, "")
        self.assertIn("ai_solution_replay_failed", html)
        self.assertIn("AI rejected candidates", html)

    def test_ai_repair_missing_json_fields_stays_review_without_crash(self) -> None:
        class FakeRepairProvider:
            name = "fake-ai-repair"

            def propose_chess_repair(self, context):
                return {"status": "reviewed", "provider": self.name, "model": "unit-ai"}

        record = ChessPgnRecord(
            id="ai-repair-malformed",
            source_pages=[1],
            title="Diagram 1-1",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            raw_text="Diagram 1-1\nNoisy solution.",
        )
        diagram_records = [{"page": 1, "diagram_number": "1-1", "image_data": b"\x89PNG\r\n\x1a\nsample"}]

        with patch.dict(
            "os.environ",
            {
                "KINDLEMASTER_OPENAI_CHESS_REPAIR": "1",
                "KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE": "validated_export",
            },
            clear=False,
        ):
            with patch("chess_pgn_extractor._load_ai_repair_provider", return_value=FakeRepairProvider()):
                exercises_pgn = build_exercises_pgn([record], diagram_records=diagram_records)
                html = build_pgn_download_html([record], diagram_records=diagram_records)

        self.assertEqual(exercises_pgn, "")
        self.assertIn("validation_status:</strong> no_ai_candidates", html)

    def test_ai_repair_review_only_never_changes_exercise_export(self) -> None:
        class FakeRepairProvider:
            name = "fake-ai-repair"

            def propose_chess_repair(self, context):
                return {
                    "status": "reviewed",
                    "provider": self.name,
                    "model": "unit-ai",
                    "fen_candidates": [
                        {
                            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                            "confidence": 0.93,
                            "notes": "",
                        }
                    ],
                    "solution_line_candidates": [
                        {"movetext": "1. e4 e5", "confidence": 0.95, "notes": ""}
                    ],
                    "ocr_token_repairs": [],
                    "confidence": 0.94,
                    "requires_human_review": False,
                    "notes": "single path but review only",
                    "estimated_cost_usd": 0.0,
                }

        record = ChessPgnRecord(
            id="ai-review-only",
            source_pages=[1],
            title="Diagram 1-1",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            raw_text="Diagram 1-1\nSolution line unclear in OCR.",
        )
        diagram_records = [{"page": 1, "diagram_number": "1-1", "image_data": b"\x89PNG\r\n\x1a\nsample"}]

        with patch.dict(
            "os.environ",
            {
                "KINDLEMASTER_OPENAI_CHESS_REPAIR": "1",
                "KINDLEMASTER_OPENAI_CHESS_REPAIR_MODE": "review_only",
            },
            clear=False,
        ):
            with patch("chess_pgn_extractor._load_ai_repair_provider", return_value=FakeRepairProvider()):
                exercises_pgn = build_exercises_pgn([record], diagram_records=diagram_records)
                html = build_pgn_download_html([record], diagram_records=diagram_records)

        self.assertEqual(exercises_pgn, "")
        self.assertIn("ai_suggested_but_not_validated", html)
        self.assertIn("No automatic PGN export changes", html)

    def test_pgn_download_html_shows_diagram_preprocess_metadata(self) -> None:
        html = build_pgn_download_html(
            [],
            title="Diagram preprocessing",
            diagram_records=[
                {
                    "page": 3,
                    "filename": "scan_chess_p003_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "extension": "png",
                    "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
                    "confidence": 0.77,
                    "requires_review": False,
                    "method": "image-template-board",
                    "selected_preprocess_variant": "unsharp_mask",
                    "display_variant_used": "reader_enhanced",
                }
            ],
        )

        self.assertIn("recognition unsharp_mask", html)
        self.assertIn("display reader_enhanced", html)

    def test_pgn_download_html_keeps_low_confidence_diagram_fen_as_review(self) -> None:
        review_record = ChessPgnRecord(
            id="review-ocr",
            source_pages=[1],
            title="OCR review",
            headers={"Event": "OCR review"},
            movetext="1. e4 e5 *",
            pgn='[Event "OCR review"]\n[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]\n\n1. e4 e5 *',
            annotated_pgn='[Event "OCR review"]\n[SetUp "1"]\n[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]\n\n1. e4 e5 *',
            status="requires_review",
            warnings=["pgn_replay_failed"],
            raw_text="Original OCR fragment: Wixh7t liJ noisy OCR",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "filename": "scan_chess_p001_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "confidence": 0.42,
                    "requires_review": True,
                    "warnings": ["image_board_requires_review"],
                }
            ],
        )

        self.assertIn("FEN do weryfikacji", html)
        self.assertIn("Original OCR fragment", html)
        self.assertIn("chess-diagram-thumb", html)
        self.assertNotIn('[FEN "8/8/8/8/8/8/8/8 w - - 0 1"]', html)

    def test_html_review_record_shows_matched_diagram_and_quality_flags(self) -> None:
        review_record = ChessPgnRecord(
            id="diagram-1-2",
            source_pages=[12],
            title="Diagram 1-2 A. Yusupov - P'Schlosser",
            headers={"Event": "Diagram 1-2"},
            movetext="1. ge5 *",
            pgn='[Event "Diagram 1-2"]\n\n1. ge5 *',
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-2\n1. ge5+- Threatening gg5t and mate.",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 12,
                    "diagram_number": "1-2",
                    "filename": "scan_chess_p012_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
                    "confidence": 0.84,
                    "requires_review": False,
                }
            ],
        )

        self.assertIn("Tekst OCR z książki, wymaga korekty", html)
        self.assertIn("chess-diagram-thumb", html)
        self.assertIn("diagram_visible", html)
        self.assertIn("fen_available", html)
        self.assertNotIn("Kopiuj PGN", html)

    def test_html_review_record_matches_diagram_number_without_page_or_fen(self) -> None:
        review_record = ChessPgnRecord(
            id="diagram-number-only",
            source_pages=[99],
            title="Diagram 1-2 A. Yusupov - P'Schlosser",
            headers={"Event": "Diagram 1-2"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-2\nOCR text requires review.",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "1-2",
                    "filename": "notation_chess_p001_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "requires_review": True,
                    "selected_preprocess_variant": "original",
                    "display_variant_used": "reader_enhanced",
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertIsNotNone(soup.select_one("section.chess-pgn-game img.chess-diagram-thumb"))
        self.assertEqual(flags["diagram_visible"], "yes")
        self.assertEqual(flags["fen_available"], "no")
        self.assertEqual(flags["missing_diagram_reason"], "")
        self.assertGreaterEqual(int(flags["diagram_candidate_count"]), 1)
        self.assertGreaterEqual(int(flags["diagram_match_score"]), 100)
        self.assertIn("display reader_enhanced", html)
        self.assertIn("recognition original", html)

    def test_html_review_record_allows_low_confidence_source_order_on_different_page(self) -> None:
        review_record = ChessPgnRecord(
            id="source-order-fallback",
            source_pages=[42],
            title="A. Yusupov - P'Schlosser Bundesliga 1997",
            headers={"Event": "Damaged caption"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="OCR text requires review.",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "source_order": 0,
                    "filename": "notation_chess_p001_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "requires_review": True,
                    "selected_preprocess_variant": "original",
                    "display_variant_used": "reader_enhanced",
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertIsNotNone(soup.select_one("section.chess-pgn-game img.chess-diagram-thumb"))
        self.assertEqual(flags["diagram_visible"], "yes")
        self.assertEqual(flags["fen_available"], "no")
        self.assertEqual(flags["diagram_match_score"], "24")
        self.assertEqual(flags["diagram_text_match_low_confidence"], "yes")
        self.assertNotIn("Kopiuj PGN", html)

    def test_html_review_record_allows_source_order_on_same_page(self) -> None:
        review_record = ChessPgnRecord(
            id="same-page-source-order-fallback",
            source_pages=[42],
            title="A. Yusupov - P'Schlosser Bundesliga 1997",
            headers={"Event": "Damaged caption"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="OCR text requires review.",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 42,
                    "source_order": 0,
                    "filename": "notation_chess_p042_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "requires_review": True,
                    "selected_preprocess_variant": "original",
                    "display_variant_used": "reader_enhanced",
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertIsNotNone(soup.select_one("section.chess-pgn-game img.chess-diagram-thumb"))
        self.assertEqual(flags["diagram_visible"], "yes")
        self.assertEqual(flags["fen_available"], "no")
        self.assertEqual(flags["diagram_match_score"], "95")

    def test_matched_diagram_fen_feeds_exercise_candidate_validation(self) -> None:
        review_record = ChessPgnRecord(
            id="diagram-fen-candidate",
            source_pages=[99],
            title="Diagram 1-2 A. Yusupov - P'Schlosser",
            headers={"Event": "Diagram 1-2"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-2\n1. e4 e5",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "1-2",
                    "filename": "notation_chess_p001_01.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.88,
                    "requires_review": False,
                    "selected_preprocess_variant": "autocontrast",
                    "display_variant_used": "reader_enhanced",
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertEqual(flags["diagram_visible"], "yes")
        self.assertEqual(flags["fen_available"], "yes")
        self.assertGreater(int(flags["legal_solution_line_count"]), 0)
        self.assertIn("legal from diagram FEN", html)
        self.assertNotIn("Kopiuj PGN", html)

    def test_ocr_normalizer_generates_review_candidates_for_broken_piece_tokens(self) -> None:
        candidates = chess_ocr_normalization_candidates("lLlg3t Yfih8 iWh5 Wlh6 @h7 It>fI \ufffdgl l:!h5#")
        candidate_text = "\n".join(candidate["text"] for candidate in candidates)

        self.assertIn("Ng3+", candidate_text)
        self.assertIn("Qh8", candidate_text)
        self.assertIn("Qh5", candidate_text)
        self.assertIn("Qh6", candidate_text)
        self.assertIn("Kh7", candidate_text)
        self.assertIn("Kf1", candidate_text)
        self.assertIn("Kg1", candidate_text)
        self.assertTrue(any("ocr_candidate_symbol_map" in candidate["warnings"] for candidate in candidates))

    def test_missing_primary_fen_is_reported_without_fake_export(self) -> None:
        review_record = ChessPgnRecord(
            id="exercise-no-fen",
            source_pages=[8],
            title="Diagram 8-1",
            headers={"Event": "Diagram 8-1"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 8-1\n1. e4 e5",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 8,
                    "diagram_number": "8-1",
                    "filename": "diagram_8_1.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "confidence": 0.72,
                    "requires_review": True,
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertEqual(flags["diagram_visible"], "yes")
        self.assertEqual(flags["primary_fen_status"], "no")
        self.assertEqual(flags["missing_fen_reason"], "primary_diagram_without_fen")
        self.assertEqual(flags["fen_available"], "no")
        self.assertIn("PGN export blocked until legal replay", html)
        self.assertNotIn("Kopiuj PGN", html)

    def test_summary_reports_exercise_level_fen_and_solution_metrics(self) -> None:
        review_record = ChessPgnRecord(
            id="exercise-summary",
            source_pages=[1],
            title="Diagram 1-1",
            headers={"Event": "Diagram 1-1"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-1\n1. e4 e5",
        )

        summary = summarize_chess_pgn_records(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "1-1",
                    "filename": "diagram_1_1.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.91,
                    "requires_review": False,
                }
            ],
        )

        self.assertEqual(summary["fen_available_yes_count"], 1)
        self.assertGreater(summary["candidate_solution_line_count"], 0)
        self.assertGreater(summary["legal_solution_line_count"], 0)
        self.assertEqual(summary["exportable_pgn_count"], 0)
        self.assertIn("records", summary)
        self.assertEqual(summary["exercise_export_count"], summary["legal_solution_line_count"])
        self.assertEqual(summary["solution_replay_legal_count"], summary["legal_solution_line_count"])

    def test_build_exercises_pgn_exports_legal_solution_without_polluting_games_pgn(self) -> None:
        review_record = ChessPgnRecord(
            id="exercise-export",
            source_pages=[1],
            title="Diagram 1-1",
            headers={"Event": "Diagram 1-1", "Result": "*"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-1\n1. e4 e5",
        )
        diagram_records = [
            {
                "page": 1,
                "diagram_number": "1-1",
                "filename": "diagram_1_1.png",
                "image_data": b"\x89PNG\r\n\x1a\nsample",
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "confidence": 0.91,
                "requires_review": False,
            }
        ]

        exercises_pgn = build_exercises_pgn(
            [review_record],
            diagram_records=diagram_records,
            source_title="Exercise sample",
        )

        self.assertIn('[SetUp "1"]', exercises_pgn)
        self.assertIn('[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"]', exercises_pgn)
        self.assertIn("1. e4 e5", exercises_pgn)
        self.assertEqual(build_combined_pgn([review_record]), "")
        game = chess.pgn.read_game(io.StringIO(exercises_pgn))
        self.assertIsNotNone(game)
        self.assertFalse(game.errors)

    def test_legal_external_fen_candidate_wins_over_higher_confidence_without_replay(self) -> None:
        review_record = ChessPgnRecord(
            id="external-fen-wins",
            source_pages=[1],
            title="Diagram 1-1",
            headers={"Event": "Diagram 1-1"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-1\n1. e4 e5",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "1-1",
                    "filename": "diagram_1_1.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "confidence": 0.99,
                    "requires_review": False,
                    "external_fen_candidates": [
                        {
                            "source": "linrock",
                            "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                            "confidence": 0.86,
                            "requires_review": False,
                            "method": "linrock/chessboard-recognizer",
                        }
                    ],
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertEqual(flags["fen_available"], "yes")
        self.assertEqual(flags["external_fen_candidate_count"], "1")
        self.assertEqual(flags["fen_candidate_conflict"], "no")
        self.assertIn("source linrock", html)
        self.assertIn("legal_lines 1", html)
        self.assertIn("legal from diagram FEN", html)

    def test_conflicting_fen_candidates_without_legal_replay_go_to_review(self) -> None:
        review_record = ChessPgnRecord(
            id="fen-conflict-review",
            source_pages=[1],
            title="Diagram 1-1",
            headers={"Event": "Diagram 1-1"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-1\n1. Qh5",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "1-1",
                    "filename": "diagram_1_1.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                    "confidence": 0.92,
                    "requires_review": False,
                    "external_fen_candidates": [
                        {
                            "source": "linrock",
                            "fen": "4k3/8/8/8/8/8/4K3/8 w - - 0 1",
                            "confidence": 0.90,
                            "requires_review": False,
                        }
                    ],
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertEqual(flags["fen_available"], "review")
        self.assertEqual(flags["fen_candidate_conflict"], "yes")
        self.assertEqual(flags["legal_solution_line_count"], "0")
        self.assertIn("fen_candidate_conflict", html)

    def test_diagram_one_two_uses_single_primary_fen_or_marks_ambiguity(self) -> None:
        review_record = ChessPgnRecord(
            id="diagram-one-two-primary",
            source_pages=[12],
            title="Diagram 1-2 A. Yusupov - P'Schlosser Bundesliga 1997",
            headers={"Event": "Diagram 1-2"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 1-2\n1. e4 e5",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 12,
                    "diagram_number": "1-2",
                    "source_order": 0,
                    "filename": "diagram_1_2_a.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.90,
                    "requires_review": False,
                    "selected_preprocess_variant": "autocontrast",
                    "display_variant_used": "reader_enhanced",
                },
                {
                    "page": 12,
                    "diagram_number": "1-2",
                    "source_order": 1,
                    "filename": "diagram_1_2_b.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
                    "confidence": 0.60,
                    "requires_review": True,
                    "selected_preprocess_variant": "threshold_bw",
                    "display_variant_used": "reader_enhanced",
                },
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertEqual(flags["diagram_visible"], "yes")
        self.assertEqual(flags["fen_available"], "yes")
        self.assertEqual(flags["fen_ambiguous"], "no")
        self.assertEqual(len(soup.select(".exercise-primary-fen code.diagram-fen-code")), 1)
        self.assertIn("Candidate FENs", html)
        self.assertIn("display reader_enhanced", html)
        self.assertIn("recognition autocontrast", html)

    def test_non_primary_fen_does_not_make_record_ambiguous(self) -> None:
        review_record = ChessPgnRecord(
            id="diagram-ambiguous-fen",
            source_pages=[4],
            title="Diagram 2-1",
            headers={"Event": "Diagram 2-1"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 2-1\n1. e4 e5",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 4,
                    "diagram_number": "2-1",
                    "source_order": 0,
                    "filename": "diagram_2_1_a.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.90,
                    "requires_review": False,
                },
                {
                    "page": 4,
                    "diagram_number": "2-1",
                    "source_order": 1,
                    "filename": "diagram_2_1_b.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                    "confidence": 0.91,
                    "requires_review": False,
                },
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags = {
            item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                ".exercise-quality-value"
            ).get_text(strip=True)
            for item in soup.select(".exercise-quality-flags li")
        }

        self.assertEqual(flags["fen_available"], "yes")
        self.assertEqual(flags["fen_ambiguous"], "no")
        self.assertGreater(int(flags["legal_solution_line_count"]), 0)
        self.assertIn("Candidate FENs", html)
        self.assertIn("legal from diagram FEN", html)
        self.assertNotIn("Kopiuj PGN", html)

    def test_primary_diagram_is_not_reused_for_multiple_records(self) -> None:
        records = [
            ChessPgnRecord(
                id="diagram-5-1-a",
                source_pages=[5],
                title="Diagram 5-1 first",
                headers={"Event": "Diagram 5-1"},
                movetext="",
                pgn="",
                status="requires_review",
                warnings=["pgn_replay_errors"],
                raw_text="Diagram 5-1\n1. e4 e5",
            ),
            ChessPgnRecord(
                id="diagram-5-1-b",
                source_pages=[5],
                title="Diagram 5-1 duplicate",
                headers={"Event": "Diagram 5-1"},
                movetext="",
                pgn="",
                status="requires_review",
                warnings=["pgn_replay_errors"],
                raw_text="Diagram 5-1\n1. d4 d5",
            ),
        ]

        html = build_pgn_download_html(
            records,
            diagram_records=[
                {
                    "page": 5,
                    "diagram_number": "5-1",
                    "source_order": 0,
                    "filename": "diagram_5_1.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.90,
                    "requires_review": False,
                }
            ],
        )
        soup = BeautifulSoup(html, "html.parser")
        flags_by_section = []
        for section in soup.select("section.chess-pgn-game"):
            flags_by_section.append(
                {
                    item.select_one(".exercise-quality-key").get_text(strip=True).rstrip(":"): item.select_one(
                        ".exercise-quality-value"
                    ).get_text(strip=True)
                    for item in section.select(".exercise-quality-flags li")
                }
            )

        self.assertEqual([flags["diagram_visible"] for flags in flags_by_section], ["yes", "no"])
        self.assertEqual([flags["fen_available"] for flags in flags_by_section], ["yes", "no"])

    def test_sequence_diagnostics_reports_duplicates_and_inversions(self) -> None:
        records = [
            ChessPgnRecord(
                id="diagram-3-7",
                source_pages=[1],
                title="Diagram 3-7",
                headers={},
                movetext="",
                pgn="",
                status="requires_review",
                raw_text="Diagram 3-7",
            ),
            ChessPgnRecord(
                id="diagram-3-6",
                source_pages=[2],
                title="Diagram 3-6",
                headers={},
                movetext="",
                pgn="",
                status="requires_review",
                raw_text="Diagram 3-6",
            ),
            ChessPgnRecord(
                id="diagram-24-2-a",
                source_pages=[3],
                title="Diagram 24-2",
                headers={},
                movetext="",
                pgn="",
                status="requires_review",
                raw_text="Diagram 24-2",
            ),
            ChessPgnRecord(
                id="diagram-24-2-b",
                source_pages=[4],
                title="Diagram 24-2",
                headers={},
                movetext="",
                pgn="",
                status="requires_review",
                raw_text="Diagram 24-2",
            ),
        ]

        html = build_pgn_download_html(records)

        self.assertIn("Sequence diagnostics", html)
        self.assertIn("24-2", html)
        self.assertIn("3-7 -&gt; 3-6", html)

    def test_html_records_sort_by_source_position_page_and_bbox(self) -> None:
        late_record = ChessPgnRecord(
            id="late-on-page",
            source_pages=[2],
            title="Diagram 6-2",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            raw_text="Diagram 6-2",
        )
        early_record = ChessPgnRecord(
            id="early-on-page",
            source_pages=[1],
            title="Diagram 6-1",
            headers={},
            movetext="",
            pgn="",
            status="requires_review",
            raw_text="Diagram 6-1",
        )

        html = build_pgn_download_html(
            [late_record, early_record],
            diagram_records=[
                {
                    "page": 2,
                    "diagram_number": "6-2",
                    "filename": "diagram_6_2.png",
                    "bbox": (10, 20, 100, 110),
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "requires_review": True,
                },
                {
                    "page": 1,
                    "diagram_number": "6-1",
                    "filename": "diagram_6_1.png",
                    "bbox": (10, 200, 100, 290),
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "requires_review": True,
                },
            ],
        )

        self.assertLess(html.index("Diagram 6-1"), html.index("Diagram 6-2"))

    def test_candidate_line_ocr_token_normalization_marks_review_variants(self) -> None:
        review_record = ChessPgnRecord(
            id="ocr-candidate-normalization",
            source_pages=[1],
            title="Diagram 4-1",
            headers={"Event": "Diagram 4-1"},
            movetext="",
            pgn="",
            status="requires_review",
            warnings=["pgn_replay_errors"],
            raw_text="Diagram 4-1\n1. gg5t @h7 2. l:!h5#",
        )

        html = build_pgn_download_html(
            [review_record],
            diagram_records=[
                {
                    "page": 1,
                    "diagram_number": "4-1",
                    "filename": "diagram_4_1.png",
                    "image_data": b"\x89PNG\r\n\x1a\nsample",
                    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                    "confidence": 0.90,
                    "requires_review": False,
                }
            ],
        )

        self.assertIn("ocr_candidate_normalized", html)
        self.assertNotIn("Kopiuj PGN", html)

    def test_exercise_record_validates_solution_from_diagram_fen(self) -> None:
        review_record = ChessPgnRecord(
            id="fen-solution",
            source_pages=[1],
            title="Diagram 1-1",
            headers={"Event": "Diagram 1-1"},
            movetext="1. e4 e5 *",
            pgn='[Event "Diagram 1-1"]\n\n1. e4 e5 *',
            status="requires_review",
            warnings=["pgn_replay_errors"],
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            raw_text="Diagram 1-1\n1. e4 e5",
        )

        html = build_pgn_download_html([review_record])

        self.assertIn("Candidate solution lines", html)
        self.assertIn("legal from diagram FEN", html)
        self.assertIn("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2", html)

    def test_variation_boundaries_do_not_merge_into_mainline(self) -> None:
        review_record = ChessPgnRecord(
            id="variation-boundaries",
            source_pages=[1],
            title="Diagram 1-4",
            headers={"Event": "Diagram 1-4"},
            movetext="1. e4 e5 *",
            pgn='[Event "Diagram 1-4"]\n\n1. e4 e5 *',
            status="requires_review",
            warnings=["move_number_regression"],
            fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            raw_text="1. e4 e5 if 1... c5 2. Nf3 or 1... e6 2. d4 in view of 2... d5",
        )

        html = build_pgn_download_html([review_record])

        self.assertGreaterEqual(html.count("Candidate line"), 3)
        self.assertIn("requires review", html)

    def test_ignores_bracketed_analysis_variations_for_legal_replay(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 [ 1... c5 2. Nf3 d6 ] 2. Nf3 Nc6 *",
                page_num=1,
                source_title="Variation sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertEqual(
            records[0].final_fen,
            "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
        )

    def test_reads_san_continuation_lines_after_numbered_movetext(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4\n"
                "e5\n"
                "2. Nf3\n"
                "Nc6\n"
                "*",
                page_num=1,
                source_title="Continuation sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. e4 e5 2. Nf3 Nc6", records[0].movetext)

    def test_duplicate_explicit_marker_is_suppressed_without_regression(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 1... 1... e5 2. Nf3 Nc6 *",
                page_num=1,
                source_title="Duplicate marker",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. e4 e5 2. Nf3 Nc6 *", records[0].movetext)
        self.assertIn("duplicate_marker_suppressed", records[0].warnings)
        self.assertNotIn("move_number_regression", records[0].warnings)

    def test_leading_page_fragment_before_explicit_move_is_not_inferred_as_move_one(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Kf7 25.Qh5+ Ke7\n26.Qxe5 Qa1+ *",
            page_num=160,
            source_title="Split page fragment",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertIn("25. Qh5+ Ke7 26. Qxe5 Qa1+", records[0].movetext)
        self.assertNotIn("1. Kf7", records[0].movetext)
        self.assertNotIn("move_number_inferred", records[0].warnings)

    def test_ignores_prose_before_first_move_number_on_comment_lines(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "1. e4 e5\n"
            "Strongly threatening ...Ra2. 2. Nf3 Nc6 *",
            page_num=1,
            source_title="Threat prose",
            ocr_confidence=1.0,
        )

        self.assertIn("1. e4 e5 2. Nf3 Nc6", records[0].movetext)
        self.assertNotIn("Ra2", records[0].movetext)

    def test_black_to_move_fragment_without_fen_stays_local_and_avoids_sequence_warnings(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "11... d5 12. Rxb7 Rd7 *",
                page_num=33,
                source_title="Continuation fragment",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("11... d5 12. Rxb7 Rd7 *", records[0].movetext)
        self.assertEqual(records[0].warnings, ["pgn_replay_errors"])

    def test_branching_analysis_line_with_embedded_moves_is_ignored_for_mainline_replay(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5\n"
                "White also loses after 2. Bc4 Qh4+ 3. Kf1 Qxf2#\n"
                "2. Nf3 Nc6 3. Bb5 a6 1-0",
                page_num=1,
                source_title="Embedded analysis prose",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertEqual(records[0].warnings, [])
        self.assertIn("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0", records[0].movetext)
        self.assertNotIn("2. Bc4", records[0].movetext)
        self.assertNotIn("Qxf2", records[0].movetext)

    def test_diagram_segment_body_starts_at_first_safe_mainline_line(self) -> None:
        text = (
            "Diagram 7-3\n"
            "B13: Caro-Kann sidelines including 2...Nf6 3.g3\n"
            "White also loses after 2. Bc4 Qh4+ 3. Kf1 Qxf2#\n"
            "1. e4 e5 2. Nf3 Nc6 1-0\n"
        )

        candidates = _split_candidate_game_blocks(text)

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["body"].startswith("1. e4 e5"))
        self.assertNotIn("White also loses after", candidates[0]["body"])

    def test_after_and_or_analysis_lines_do_not_trigger_move_number_regression(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6\n"
                "After 2.Bc4 comes 2...Bc5 while 2.Kg2 loses to 2...Qh4#\n"
                "Or 3.Bc4 Nf6 4.d3 Be7\n"
                "3. Bb5 a6 1-0",
                page_num=1,
                source_title="After or analysis",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertEqual(records[0].warnings, [])
        self.assertIn("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0", records[0].movetext)
        self.assertNotIn("Bc4", records[0].movetext)
        self.assertNotIn("move_number_regression", records[0].warnings)
        self.assertNotIn("side_to_move_mismatch", records[0].warnings)

    def test_prepare_mainline_token_source_drops_branching_commentary_lines_whole(self) -> None:
        source = (
            "1. e4 e5 2. Nf3 Nc6\n"
            "After 2.Bc4 comes 2...Bc5 while 2.Kg2 loses to 2...Qh4#\n"
            "Or 3.Bc4 Nf6 4.d3 Be7\n"
            "3. Bb5 a6\n"
            "due to 3...Nf6 4.O-O Be7\n"
            "1-0\n"
        )

        prepared = _prepare_mainline_token_source(source)

        self.assertIn("1. e4 e5 2. Nf3 Nc6", prepared)
        self.assertIn("3. Bb5 a6", prepared)
        self.assertIn("1-0", prepared)
        self.assertNotIn("After 2.Bc4", prepared)
        self.assertNotIn("Or 3.Bc4", prepared)
        self.assertNotIn("due to 3...Nf6", prepared)

    def test_alternative_prefix_line_is_ignored_for_strict_movetext(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6\n"
                "Black fights for the centre. A good alternative is 3...Nf6 4.O-O Be7.\n"
                "4. Ba4 Nf6 1-0",
                page_num=1,
                source_title="Alternative prefix",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertEqual(records[0].warnings, [])
        self.assertIn("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 1-0", records[0].movetext)
        self.assertNotIn("3... Nf6 4. O-O Be7", records[0].movetext)

    def test_keeps_continuation_move_before_next_move_number(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. d4 d5 2. c4\n"
                "dxc4 3. e4 e5 *",
                page_num=1,
                source_title="Continuation before number",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("2. c4 dxc4 3. e4 e5", records[0].movetext)

    def test_illegal_pgn_requires_review_and_omits_final_fen(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 3. Qxf7 *",
                page_num=1,
                source_title="Illegal sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertEqual(records[0].final_fen, "")
        self.assertIn("pgn_replay_errors", records[0].warnings)

    def test_jobava_interleaved_variation_sample_requires_review_and_is_not_exported(self) -> None:
        text = (
            "1. d4 Nf6 2. Nc3 d5 3. Bf4 c5 4. e3 cxd4 5. exd4 a6 "
            "6. Nf3 Nc6 7. Ne5 Bd7 8. g4 e6 9. g5 Nxe5 "
            "10... Nxe5 11. dxe5 Ne7 12. O-O Ng6 13. Bg3 Qxg5 "
            "14. f4 Qf5 15. Ne2 Bc5+ 16. Kh1 h5 17. Bf2 Bxf2 "
            "18. Rxf2 Ne7 19. Nd4 Qg6 20. Qd2 h4 21. Rg1 Qh6 "
            "22. Bh3 g6 23. c3 Nc6 24. Qe3 10. Bxe5 Ng8 "
            "11. h4 Ne7 12. Qb5 27. Qc2 Qc6 28. Qb3 Qb5 "
            "87. Qf1? 0. Bg2 87... Bb8! 1-0"
        )

        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                text,
                page_num=1,
                source_title="Jobava sample",
                ocr_confidence=1.0,
            )
        )
        warnings = set(records[0].warnings)
        html_parts = render_chess_pgn_html_parts(records, download_href="")

        self.assertEqual(records[0].status, "requires_review")
        self.assertEqual(records[0].final_fen, "")
        self.assertIn("side_to_move_mismatch", warnings)
        self.assertIn("move_number_regression", warnings)
        self.assertIn("move_number_jump", warnings)
        self.assertIn("invalid_move_number_zero", warnings)
        self.assertEqual(build_combined_pgn(records), "")
        self.assertIn('class="chess-pgn-review"', html_parts[0])
        self.assertNotIn('class="chess-pgn"', html_parts[0])

    def test_fundamenty_style_branch_line_is_removed_from_strict_movetext(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1... Ne2+ 2. Kh1\n"
                "White also loses after 3. Ng3+ 4. Kg1, due to 4...Nxf1\n"
                "3... Bh5 0-1",
                page_num=1,
                source_title="Fundamenty branch",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("1... Ne2+ 2. Kh1 3... Bh5 0-1", records[0].movetext)
        self.assertNotIn("Ng3+", records[0].movetext)
        self.assertNotIn("Nxf1", records[0].movetext)
        self.assertNotIn("move_number_regression", records[0].warnings)
        self.assertIn("move_number_jump", records[0].warnings)
        self.assertIn("side_to_move_mismatch", records[0].warnings)

    def test_missing_ply_conflict_stays_review_without_truncating_to_prefix(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 3. Nf3 Nc6 *",
                page_num=1,
                source_title="Missing ply",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("1. e4 e5 3. Nf3 Nc6 *", records[0].movetext)
        self.assertIn("move_number_jump", records[0].warnings)
        self.assertIn("side_to_move_mismatch", records[0].warnings)

    def test_invalid_zero_move_number_blocks_pgn_export(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 0. Bg2 *",
                page_num=1,
                source_title="Zero move sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("invalid_move_number_zero", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")

    def test_move_number_regression_blocks_pgn_export(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 24. Qe3 10. Bxe5 *",
                page_num=1,
                source_title="Regression sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("move_number_regression", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")

    def test_move_number_jump_blocks_pgn_export(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 12. Qb5 27. Qc2 *",
                page_num=1,
                source_title="Jump sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("move_number_jump", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")

    def test_notation_collection_adjacent_games_are_split_without_headers(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "1. e4 e5 2. Nf3 Nc6 1-0\n"
            "1. d4 d5 2. c4 e6 *",
            page_num=2,
            source_title="Adjacent collection",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 2)
        self.assertIn("1. e4 e5 2. Nf3 Nc6 1-0", records[0].movetext)
        self.assertIn("1. d4 d5 2. c4 e6 *", records[1].movetext)

    def test_short_branch_only_diagram_segment_does_not_create_record(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 5-1\nAfter 2.Bc4 comes 2...Bc5 while 2.Kg2 loses to 2...Qh4#\n",
            page_num=5,
            source_title="Branch only",
            ocr_confidence=1.0,
        )

        self.assertEqual(records, [])

    def test_diagram_if_line_is_suppressed_before_strict_movetext(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 9-1\n"
                "1... Rxh2! 2. gxh2 0-1\n"
                "If 1... Rh1+ then 2. Bxf7+ Kh8 3. Qh5#\n",
                page_num=9,
                source_title="Diagram if branch",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1... Rxh2! 2. gxh2 0-1", records[0].movetext)
        self.assertNotIn("Rh1+", records[0].movetext)
        self.assertIn("diagram_branch_line_suppressed", records[0].warnings)

    def test_diagram_black_cannot_accept_line_does_not_create_branch_record(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 10-2\n"
                "1... Rxh2! 2. gxh2 0-1\n"
                "Black cannot accept 1...gxh5 because 2. Qh7#\n",
                page_num=10,
                source_title="Cannot accept",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1... Rxh2! 2. gxh2 0-1", records[0].movetext)
        self.assertNotIn("1... gxh5", records[0].movetext)
        self.assertEqual(records[0].warnings.count("diagram_branch_line_suppressed"), 1)

    def test_diagram_illustrative_variation_line_with_semicolon_is_suppressed(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 11-4\n"
            "1... Rxh2! 2. gxh2 0-1\n"
            "1... Rh1+ 2. Bxf7+; 1... gxh5 2. Qh7#\n",
            page_num=11,
            source_title="Illustrative variation",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1... Rxh2! 2. gxh2 0-1", records[0].movetext)
        self.assertIn("diagram_illustrative_variation_suppressed", records[0].warnings)

    def test_diagram_multi_case_block_is_split_into_local_solution_blocks(self) -> None:
        candidates = _split_candidate_game_blocks(
            "Diagram 47-1\n"
            "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
            "Variation from the game\n"
            "1... gxh5 2. Qh7#\n"
            "A useful drawing position\n"
            "1... Rxh2! 2. gxh2 0-1\n"
        )

        self.assertEqual(len(candidates), 2)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1# 1-0", candidates[0]["body"])
        self.assertIn("1... Rxh2! 2. gxh2 0-1", candidates[1]["body"])
        self.assertIn("diagram_subblock_split_used", candidates[0]["warnings"])
        self.assertIn("diagram_multi_case_block_detected", candidates[1]["warnings"])

    def test_diagram_variation_from_game_branch_is_suppressed_before_solution_selection(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 47-2\n"
                "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
                "Variation from the game\n"
                "1... gxh5 2. Qh7#\n",
                page_num=47,
                source_title="Variation branch",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1# 1-0", records[0].movetext)
        self.assertNotIn("1... gxh5", records[0].movetext)
        self.assertIn("diagram_branch_example_suppressed", records[0].warnings)
        self.assertIn("diagram_subblock_split_used", records[0].warnings)

    def test_diagram_or_with_black_to_play_does_not_create_new_strict_record(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 47-3\n"
                "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
                "Or, with Black to play\n"
                "1... Rh1+ 2. Bxf7+ Kh8\n",
                page_num=47,
                source_title="Black to play branch",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1# 1-0", records[0].movetext)
        self.assertNotIn("Rh1+", records[0].movetext)
        self.assertIn("diagram_branch_example_suppressed", records[0].warnings)

    def test_diagram_main_variation_and_wins_the_queen_lines_are_suppressed(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 47-4\n"
                "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
                "The main variation would go 1... Rh1+ 2. Bxf7+ Kg7\n"
                "After 2... Qh4 White wins the queen\n",
                page_num=47,
                source_title="Main variation prose",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1# 1-0", records[0].movetext)
        self.assertNotIn("Rh1+", records[0].movetext)
        self.assertIn("diagram_branch_example_suppressed", records[0].warnings)

    def test_diagram_resigned_in_view_of_lines_do_not_pollute_solution_line(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 47-5\n"
                "1... Rxh2! 2. gxh2 0-1\n"
                "White resigned, in view of 2... Qh1#\n"
                "Black resigned, in view of 3. Rh8#\n",
                page_num=47,
                source_title="Resigned prose",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1... Rxh2! 2. gxh2 0-1", records[0].movetext)
        self.assertNotIn("Qh1#", records[0].movetext)
        self.assertNotIn("Rh8#", records[0].movetext)
        self.assertIn("diagram_branch_example_suppressed", records[0].warnings)

    def test_split_branch_block_does_not_emit_late_ply_tail_as_new_record(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 9-8 1967\n"
            "1. Nb2 Nb4\n"
            "1... Nd4 2. Qe1#; 1... Qd4 2. Qa4#\n"
            "2. d8#\n",
            page_num=96,
            source_title="Late ply tail",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Nb2 Nb4", records[0].movetext)
        self.assertNotIn("2. d8#", records[0].movetext)

    def test_diagram_real_black_start_is_kept_as_single_candidate(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 12-3\n"
                "1... Rxh2! 2. gxh2 0-1\n",
                page_num=12,
                source_title="Real black start",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1... Rxh2! 2. gxh2 0-1", records[0].movetext)
        self.assertNotIn("diagram_ambiguous_block_review", records[0].warnings)

    def test_diagram_threat_line_is_removed_without_polluting_movetext(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "Diagram 13-5\n"
            "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
            "The threat is 1... Qh2+ 2. Kxh2 Rh8#\n",
            page_num=13,
            source_title="Threat prose",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1# 1-0", records[0].movetext)
        self.assertNotIn("Qh2+", records[0].movetext)

    def test_diagram_continuation_lines_are_kept_only_without_branch_markers(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 14-1\n"
                "1. Qh7+ Kxh7\n"
                "2. Rh1+ Kg8\n"
                "3. Rh8+ Kxh8 1-0\n",
                page_num=14,
                source_title="Diagram continuation",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1+ Kg8 3. Rh8+ Kxh8 1-0", records[0].movetext)

    def test_diagram_ambiguous_two_candidates_force_review(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 15-7\n"
                "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
                "1. Bxf7+ Kxf7 2. Qh5+ 1-0\n",
                page_num=15,
                source_title="Ambiguous diagram",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("diagram_ambiguous_block_review", records[0].warnings)

    def test_diagram_selector_prefers_candidate_closest_to_diagram_when_quality_is_equal(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 16-2\n"
                "1. Qh7+ Kxh7 2. Rh1# 1-0\n"
                "Reference line\n"
                "1. Bxf7+ Kxf7 2. Qh5+ 1-0\n",
                page_num=16,
                source_title="Closer candidate",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1# 1-0", records[0].movetext)
        self.assertIn("diagram_block_selector_used", records[0].warnings)
        self.assertIn("diagram_solution_candidate_selected", records[0].warnings)

    def test_diagram_selector_prefers_later_candidate_when_earlier_one_is_branch_example(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 17-4\n"
                "Black cannot accept the threat.\n"
                "1... gxh5 2. Qh7#\n"
                "1... Rxh2! 2. gxh2 0-1\n",
                page_num=17,
                source_title="Later non-branch candidate",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1... Rxh2! 2. gxh2 0-1", records[0].movetext)
        self.assertNotIn("1... gxh5 2. Qh7#", records[0].movetext)
        self.assertIn("diagram_branch_example_suppressed", records[0].warnings)

    def test_diagram_selector_prefers_result_closed_candidate_when_other_scores_match(self) -> None:
        open_candidate = DiagramSolutionCandidate(
            source_lines=(),
            start_line_index=1,
            end_line_index=1,
            normalized_text="1. Qh7+ Kxh7 2. Rh1#",
            raw_text="1. Qh7+ Kxh7 2. Rh1#",
            distance_from_diagram=0,
            branch_example_score=(0, 0, 0),
            has_result=False,
            has_terminal_result=False,
            has_tactical_finish=False,
            sequence_risk_score=(0, 0, 0),
            halfmove_count=3,
        )
        closed_candidate = DiagramSolutionCandidate(
            source_lines=(),
            start_line_index=2,
            end_line_index=2,
            normalized_text="1. Qh7+ Kxh7 2. Rh1# 1-0",
            raw_text="1. Qh7+ Kxh7 2. Rh1# 1-0",
            distance_from_diagram=0,
            branch_example_score=(0, 0, 0),
            has_result=True,
            has_terminal_result=True,
            has_tactical_finish=False,
            sequence_risk_score=(0, 0, 0),
            halfmove_count=3,
        )

        selected, force_review, warnings = _select_diagram_mainline_candidate([open_candidate, closed_candidate])

        self.assertIs(selected, closed_candidate)
        self.assertFalse(force_review)
        self.assertEqual(warnings, ())

    def test_diagram_selector_rejects_nonmonotonic_candidate_even_if_it_is_closer(self) -> None:
        nonmonotonic = DiagramSolutionCandidate(
            source_lines=(),
            start_line_index=1,
            end_line_index=1,
            normalized_text="1. Qh7+ Kxh7 4. Rh1# 1-0",
            raw_text="1. Qh7+ Kxh7 4. Rh1# 1-0",
            distance_from_diagram=0,
            branch_example_score=(0, 0, 0),
            has_result=True,
            has_terminal_result=True,
            sequence_risk_score=(1, 0, 0),
            halfmove_count=3,
        )
        monotonic = DiagramSolutionCandidate(
            source_lines=(),
            start_line_index=3,
            end_line_index=3,
            normalized_text="1. Qh7+ Kxh7 2. Rh1# 1-0",
            raw_text="1. Qh7+ Kxh7 2. Rh1# 1-0",
            distance_from_diagram=2,
            branch_example_score=(0, 0, 0),
            has_result=True,
            has_terminal_result=True,
            sequence_risk_score=(0, 0, 0),
            halfmove_count=3,
        )

        selected, force_review, warnings = _select_diagram_mainline_candidate([nonmonotonic, monotonic])

        self.assertIs(selected, monotonic)
        self.assertFalse(force_review)
        self.assertEqual(warnings, ())

    def test_diagram_mating_finish_without_explicit_result_can_be_selected_when_clean(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 18-1\n"
                "1. Qh7+ Kxh7 2. Rh1#\n",
                page_num=18,
                source_title="Mate without result",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("1. Qh7+ Kxh7 2. Rh1#", records[0].movetext)
        self.assertNotIn("diagram_ambiguous_block_review", records[0].warnings)

    def test_wrong_side_move_number_blocks_pgn_export(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. d4 Nf6 2. Nc3 d5 3. Bf4 c5 10... Nxe5 *",
                page_num=1,
                source_title="Side mismatch sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("side_to_move_mismatch", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")

    def test_accepted_record_with_illegal_pgn_is_revalidated_before_export(self) -> None:
        record = ChessPgnRecord(
            id="accepted-but-illegal",
            source_pages=[1],
            title="Accepted but illegal",
            headers={"Event": "Accepted but illegal", "Result": "*"},
            movetext="1. e4 e5 2. Nf3 Nc6 3. Qxf7 *",
            pgn='[Event "Accepted but illegal"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 Nc6 3. Qxf7 *\n',
            status="accepted",
            final_fen="fake-final-fen",
        )

        summary = summarize_chess_pgn_records([record])

        self.assertEqual(build_combined_pgn([record]), "")
        self.assertEqual(summary["strict_export_count"], 0)
        self.assertEqual(summary["manual_review_count"], 1)

    def test_regex_valid_but_position_illegal_san_blocks_export_during_extraction(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 3. Qxf7 *",
                page_num=1,
                source_title="Illegal SAN context",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("illegal_san_token", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")

    def test_ambiguous_san_in_position_context_requires_review_without_guessing_piece(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 Ke7 2. Nd2 *",
                page_num=1,
                source_title="Ambiguous SAN context",
                ocr_confidence=1.0,
                fen_candidates=["4k3/8/8/8/8/5N2/4P3/4KN2 w - - 0 1"],
            )
        )

        self.assertEqual(records[0].status, "requires_review")
        self.assertIn("ambiguous_san_token", records[0].warnings)
        self.assertEqual(build_combined_pgn(records), "")

    def test_line_classifier_reports_metadata_comments_noise_and_san_corrections(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 1\n"
                "D00\n"
                "B13: Caro-Kann: Exchange Variation\n"
                "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. 0-0 Be7\n"
                "The position is equal.\n"
                "Weighted Error Value: White=0.51/ Black=0.36\n"
                "*",
                page_num=1,
                source_title="Line classifier",
                ocr_confidence=1.0,
            )
        )
        summary = summarize_chess_pgn_records(records)

        self.assertEqual(records[0].headers["ECO"], "D00")
        self.assertEqual(records[0].headers["Opening"], "Caro-Kann: Exchange Variation")
        self.assertIn("book_comment_excluded_from_strict_movetext", records[0].warnings)
        self.assertNotIn("The position is equal", records[0].movetext)
        self.assertGreaterEqual(summary["line_metadata_count"], 2)
        self.assertGreaterEqual(summary["line_comment_count"], 2)
        self.assertGreaterEqual(summary["line_noise_count"], 1)
        self.assertGreaterEqual(summary["san_correction_count"], 1)
        self.assertIn("{The position is equal.}", records[0].annotated_pgn)
        self.assertIn("{Weighted Error Value: White=0.51/ Black=0.36}", records[0].annotated_pgn)

    def test_splits_notation_collection_games_after_cleaned_eco_headers(self) -> None:
        text = """
        1 D00
        Aravindh,Chithambaram VR. 2731
        Praggnanandhaa,R 2758
        1.Nc3 d5 2.d4 Nf6 3.Bf4 c5 0-1
        a b c d e f g h
        2 D00
        Carlsen,M 2833
        Duda,J 2739
        1.d4 d5 2.Nc3 Nf6 3.Bf4 c5 *
        """

        records = extract_chess_pgn_records_from_text(text, page_num=2, source_title="Jobava", ocr_confidence=1.0)

        self.assertEqual(len(records), 2)
        self.assertIn("1. Nc3 d5", records[0].pgn)
        self.assertIn("1. d4 d5", records[1].pgn)

    def test_splits_notation_collection_games_after_result_and_player_caption(self) -> None:
        text = """
        Carlsen,M 2833
        Duda,J 2739
        1.e4 e5 2.Nf3 Nc6 1-0
        Praggnanandhaa,R 2758
        Giri,Anish 2749
        1.d4 d5 2.Nc3 Nf6 *
        """

        records = extract_chess_pgn_records_from_text(text, page_num=2, source_title="Collection", ocr_confidence=1.0)

        self.assertEqual(len(records), 2)
        self.assertIn("1. e4 e5", records[0].pgn)
        self.assertIn("1. d4 d5", records[1].pgn)

    def test_diagram_line_after_result_does_not_glue_next_game(self) -> None:
        text = """
        D00
        Carlsen,M 2833
        Duda,J 2739
        1.e4 e5 2.Nf3 Nc6 1-0
        (Diagram)
        D00
        Praggnanandhaa,R 2758
        Giri,Anish 2749
        1.d4 d5 2.Nc3 Nf6 *
        """

        records = extract_chess_pgn_records_from_text(text, page_num=2, source_title="Collection", ocr_confidence=1.0)

        self.assertEqual(len(records), 2)
        self.assertIn("1. e4 e5", records[0].pgn)
        self.assertIn("1. d4 d5", records[1].pgn)

    def test_notation_collection_caption_populates_pgn_headers(self) -> None:
        text = """
        1 D00
        Aravindh,Chithambaram VR. 2731
        Praggnanandhaa,R 2758
        Titled Tue 11th Mar Early blitz (7)
        [Kitty Kat]
        B13: Caro-Kann: Exchange Variation and Panov-Botvinnik Attack
        1.Nc3 d5 2.d4 Nf6 3.Bf4 c5 0-1
        """

        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(text, page_num=2, source_title="Jobava", ocr_confidence=1.0)
        )
        record = records[0]

        self.assertEqual(record.headers["White"], "Aravindh, Chithambaram VR.")
        self.assertEqual(record.headers["Black"], "Praggnanandhaa, R")
        self.assertEqual(record.headers["WhiteElo"], "2731")
        self.assertEqual(record.headers["BlackElo"], "2758")
        self.assertEqual(record.headers["Event"], "Titled Tue 11th Mar Early blitz")
        self.assertEqual(record.headers["Round"], "7")
        self.assertEqual(record.headers["Date"], "????.03.11")
        self.assertEqual(record.headers["Site"], "Kitty Kat")
        self.assertEqual(record.headers["ECO"], "D00")
        self.assertEqual(record.headers["Opening"], "Caro-Kann: Exchange Variation and Panov-Botvinnik Attack")
        self.assertIn('[White "Aravindh, Chithambaram VR."]', record.pgn)
        self.assertIn('[Black "Praggnanandhaa, R"]', record.pgn)
        self.assertIn('[ECO "D00"]', record.pgn)

    def test_notation_collection_caption_parses_compact_player_line(self) -> None:
        text = """
        D00 Praggnanandhaa, R Giri, Anish 2749
        1.Nc3 d5 2.d4 Nf6 3.Bf4 c5 *
        """

        records = extract_chess_pgn_records_from_text(text, page_num=2, source_title="Jobava", ocr_confidence=1.0)

        self.assertEqual(records[0].headers["White"], "Praggnanandhaa, R")
        self.assertEqual(records[0].headers["Black"], "Giri, Anish")
        self.assertEqual(records[0].headers["BlackElo"], "2749")
        self.assertEqual(records[0].headers["ECO"], "D00")

    def test_engine_eval_tokens_do_not_create_zero_move_warnings(self) -> None:
        text = (
            "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 "
            "6. Re1 b5 7. Bb3 d6 8. c3 O-O 9. h3 Nb8 10. d4 Nbd7 "
            "11. c4-0.68/21 c6 12. Nc3 Qc7 13. Be3 Re8 "
            "14. Bc2 Bf8 15. b3 h6 White=0.51/ Black=0.36 *"
        )

        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(text, page_num=1, source_title="Eval sample", ocr_confidence=1.0)
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertNotIn("invalid_move_number_zero", records[0].warnings)
        self.assertIn("11. c4 c6", records[0].movetext)

    def test_chessbase_private_symbols_are_normalized_in_book_notation(self) -> None:
        text = "14...Bxd3\ue02e-0.68/21 15.Qxd3 Qxb2 [ \ue02015...Be7 ] *"

        normalized = normalize_ocr_text_for_pgn(text)

        self.assertIn("14...Bxd3\u2a71-0.68/21", normalized)
        self.assertIn("\u231215...Be7", normalized)
        self.assertNotIn("\ue02e", normalized)
        self.assertNotIn("\ue020", normalized)

    def test_chessbase_private_piece_figures_are_normalized_to_san_letters(self) -> None:
        normalized = normalize_ocr_text_for_pgn(
            "4.\ue028f3 g6 5.\ue028bd2 \ue027b2 13.\ue025h5 "
            "14.\ue026h3 \ue024g8 \ue029g5 *"
        )

        self.assertIn("4.Nf3", normalized)
        self.assertIn("5.Nbd2", normalized)
        self.assertIn("Bb2", normalized)
        self.assertIn("13.Qh5", normalized)
        self.assertIn("14.Rh3", normalized)
        self.assertIn("Kg8", normalized)
        self.assertIn("Pg5", normalized)
        self.assertNotIn("\ue024", normalized)
        self.assertNotIn("\ue025", normalized)
        self.assertNotIn("\ue026", normalized)
        self.assertNotIn("\ue027", normalized)
        self.assertNotIn("\ue028", normalized)
        self.assertNotIn("\ue029", normalized)

    def test_chessbase_private_symbol_font_is_fully_mapped(self) -> None:
        private_symbols = (
            "\ue000\ue005\ue008\ue009\ue00a\ue00c\ue00d\ue010"
            "\ue012\ue013\ue017\ue018\ue019\ue01a\ue020\ue021"
            "\ue024\ue025\ue026\ue027\ue028\ue029\ue02e\ue02f"
        )

        normalized = normalize_ocr_text_for_pgn(private_symbols)

        self.assertEqual(
            normalized,
            "\u221e=\u25a3\u2642\u221e\u00b1\u2265\u25b3\u00d7"
            "\u2191\u2192\u21c4\u2194\u2197\u2213\u2312\u25a1"
            "KQRBNP\u2a71\u2a72",
        )
        self.assertFalse(any(0xE000 <= ord(char) <= 0xF8FF for char in normalized))

    def test_chessbase_private_pawn_figure_is_replayed_as_legal_san(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.\ue029g4 d5 2.\ue028f3 Nf6 *",
                page_num=1,
                source_title="Figurine pawn",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. g4 d5 2. Nf3 Nf6", records[0].movetext)
        self.assertNotIn("unmapped_chess_glyphs", records[0].warnings)

    def test_chess_ocr_token_confusions_are_normalized_inside_san_tokens(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "l.e4 eS 2.Nf3 Nc6 *",
                page_num=1,
                source_title="OCR token confusions",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. e4 e5 2. Nf3 Nc6", records[0].movetext)

    def test_multiplication_capture_symbol_is_normalized_to_pgn_capture(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.e4 d5 2.e×d5 Nf6 *",
                page_num=1,
                source_title="OCR capture symbol",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(records[0].status, "accepted")
        self.assertIn("2. exd5 Nf6", records[0].movetext)

    def test_full_first_jobava_game_is_accepted_despite_engine_evals(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                ARAVINDH_PRAGG_FULL_RAW,
                page_num=0,
                source_title="Jobava",
                ocr_confidence=1.0,
            )
        )
        record = records[0]

        self.assertEqual(record.title, "Aravindh, Chithambaram VR. - Praggnanandhaa, R, Titled Tue 11th Mar Early blitz")
        self.assertEqual(record.status, "accepted")
        self.assertEqual(record.movetext.count(". "), 54)
        self.assertNotIn("invalid_move_number_zero", record.warnings)
        self.assertEqual(record.final_fen, "5R2/8/5p2/4pkp1/8/2b2P2/r2BK3/8 w - - 1 55")

    def test_pgn_download_html_keeps_source_order_and_clean_full_book_notation(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                ARAVINDH_PRAGG_FULL_RAW
                + "\n\nD00 Praggnanandhaa, R Giri, Anish 2749\n1.d4 d5 2.Nc3 Nf6 3.Bf4 Bf5 *",
                page_num=0,
                source_title="Jobava",
                ocr_confidence=1.0,
            )
        )

        html = build_pgn_download_html(records, title="Jobava sample")
        soup = BeautifulSoup(html, "html.parser")
        first_section = soup.select_one("section.chess-pgn-game")

        self.assertIsNotNone(first_section)
        self.assertIn("Aravindh, Chithambaram VR. - Praggnanandhaa, R", first_section.get_text(" "))
        self.assertIn("Legalny PGN/FEN", first_section.get_text(" "))
        self.assertIn("The position is equal", html)
        self.assertIn("Kopiuj PGN", html)
        self.assertIn("Kopiuj pełną notację", html)
        full_notation = first_section.select_one("pre.chess-full-notation-text").get_text("\n")
        self.assertIn("{The position is equal.}", full_notation)
        self.assertIn("(14... Qxb2 15. Rb1 Qxa2 16. Rxb7)", full_notation)
        self.assertNotIn("[ 14... Qxb2", full_notation)
        self.assertNotIn("(Diagram)", full_notation)
        self.assertNotIn("\u2312", full_notation)
        self.assertNotIn("[%eval", full_notation)

    def test_pgn_download_html_exports_annotated_pgn_with_comments_and_variations(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                ARAVINDH_PRAGG_FULL_RAW,
                page_num=0,
                source_title="Jobava",
                ocr_confidence=1.0,
            )
        )

        html = build_pgn_download_html(records, title="Jobava sample")
        soup = BeautifulSoup(html, "html.parser")
        pgn_block = soup.select_one("section.chess-pgn-legal pre.chess-pgn-text")

        self.assertIsNotNone(pgn_block)
        pgn_text = pgn_block.get_text("\n")
        self.assertIn('[Result "0-1"]', pgn_text)
        self.assertIn("{The position is equal.}", pgn_text)
        self.assertIn("{-0.68/21}", pgn_text)
        self.assertIn("(14... Bxd3", pgn_text)
        self.assertIn("15. Qxd3 Qxb2", pgn_text)
        self.assertIn("{Weighted Error Value: White=0.51/ Black=0.36}", pgn_text)
        self.assertNotIn("[ 14... Bxd3", pgn_text)
        self.assertNotIn("(Diagram)", pgn_text)

    def test_annotated_pgn_for_first_jobava_game_is_parse_clean(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                ARAVINDH_PRAGG_FULL_RAW,
                page_num=0,
                source_title="Jobava",
                ocr_confidence=1.0,
            )
        )

        game = chess.pgn.read_game(io.StringIO(records[0].annotated_pgn))

        self.assertIsNotNone(game)
        self.assertEqual(getattr(game, "errors", []), [])

    def test_annotated_pgn_normalizes_spaced_ocr_prose_comments(self) -> None:
        raw = """
1.e4 e5 2.Nf3 Nc6 3.Bb5 a6
a i m i n g f o r... K f 3.
1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Spaced OCR prose",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertIn("{aiming for... Kf3.}", records[0].annotated_pgn)
        self.assertNotIn("a i m i n g", records[0].annotated_pgn)
        self.assertNotIn("K f 3", records[0].annotated_pgn)

    def test_mainline_extraction_ignores_inline_comment_san_and_eval_symbols(self) -> None:
        raw = """
1.e4 e5... Rh8 is the strong threat.
2.Nf3+- Nc6 3.Bb5= a6
1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Inline comment SAN",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertEqual(records[0].warnings, [])
        self.assertIn("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0", records[0].pgn)
        self.assertNotIn("Rh8", records[0].movetext)

    def test_mainline_extraction_skips_comment_san_before_explicit_same_ply(self) -> None:
        raw = """
1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Ba4 Nf6
5.O-O Be7 6.Re1 b5 7.Bb3 d6 8.c3 O-O
9.h3 Na5 10.Bc2 c5 11.d4 Qc7 12.Nbd2 cxd4
13.cxd4 Bd7 14.Nf1 Rac8 15.Ne3 Nc4 16.Nxc4 Qxc4
17.Bb3 Qc7 18.Bg5 h6 19.Bh4 Rfe8 20.Bg3 Bf8
21.Rc1 Qxc1 22.Qxc1 Rxc1 23.Rxc1 Nxe4 24.Rc7 Be6
25.Bxe6 Rxe6 26.Qg6+ Kd7 White must now prevent...
Be4.
27.Qf7+ Be7 28.Bg5 Re8 29.Rd3+ Bd5 30.Rxd5+ Kc6
31.Rd4 Bc5 32.Rc4 Be3 would kill now.
32...Rb8 33.Rd1 Kb5 1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Duplicate explicit marker",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertNotIn("27. Be4", records[0].movetext)
        self.assertNotIn("32... Be3", records[0].movetext)
        self.assertIn("27. Qf7+ Be7", records[0].movetext)
        self.assertIn("32. Rc4 Rb8", records[0].movetext)

    def test_mainline_extraction_strips_nested_square_bracket_variation(self) -> None:
        raw = """
1.d4 Nf6 2.Nc3 d5 3.Bf4 g6 4.h4 Bg7 5.e3 h5
6.Nf3 O-O 7.Be2 c5 8.dxc5
[ 8.Ne5 Nc6 9.Qd2 cxd4 10.exd4 Qb6
Shuvalova, P - Bodnaruk, A [Kitty Kat] 1-0 (56)) 11.Nc7 Ra7 12.a3 b6 ]
8...Qa5 9.Nd2 Qxc5 10.Nb3 Qb6 1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Nested bracket variation",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertNotIn("Nc7", records[0].movetext)
        self.assertNotIn("Ra7", records[0].movetext)
        self.assertIn("8. dxc5 Qa5 9. Nd2 Qxc5 10. Nb3 Qb6", records[0].movetext)

    def test_mainline_extraction_normalizes_compact_promotion_notation(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "37.e7 Kb3 38.e8Q Rxe8 39.Rhxc4 Rxe3 1-0",
            page_num=0,
            source_title="Compact promotion",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertIn("38. e8=Q Rxe8", records[0].movetext)
        self.assertNotIn("38. e8 Rxe8", records[0].movetext)

    def test_compact_promotion_before_endgame_label_replays(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.a4 h5 2.a5 h4 3.a6 h3 4.axb7 hxg2 5.bxa8QKQ-KR 1-0",
                page_num=0,
                source_title="Promotion endgame label",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("5. bxa8=Q", records[0].movetext)

    def test_compact_promotion_before_equal_comment_replays(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.a4 h5 2.a5 h4 3.a6 h3 4.axb7 hxg2 5.bxa8Q=R e a l l y s h a r p! 1-0",
                page_num=0,
                source_title="Promotion equal comment",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("5. bxa8=Q", records[0].movetext)

    def test_continuation_records_are_merged_when_combined_replay_is_legal(self) -> None:
        first_chunk = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.e4 e5 2.Nf3 Nc6 *",
                page_num=0,
                source_title="Split game",
                ocr_confidence=1.0,
            )
        )
        continuation = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "3.Bb5 a6 4.Ba4 Nf6 1-0",
                page_num=40,
                source_title="Split game",
                ocr_confidence=1.0,
            )
        )

        merged = merge_chess_pgn_continuation_records([*first_chunk, *continuation])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].status, "accepted")
        self.assertIn("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 1-0", merged[0].movetext)
        self.assertIn(41, merged[0].source_pages)

    def test_equal_evaluation_before_black_move_keeps_black_move(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "1.e4 e5 2.Nf3= Nc6 3.Bb5 a6 1-0",
            page_num=0,
            source_title="Equal marker",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertIn("2. Nf3 Nc6", records[0].movetext)
        self.assertNotIn("2. Nf3 3. Bb5", records[0].movetext)

    def test_tactical_analysis_decimal_scores_do_not_create_move_numbers(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "1.d4 0.14 Tactical Analysis 5.2 (4s) 1...Nf6 0.22 2.Nc3 0.08 d5N 1-0",
            page_num=0,
            source_title="Engine decimals",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertNotIn("move_number_jump", records[0].warnings)
        self.assertNotIn("move_number_regression", records[0].warnings)
        self.assertNotIn("side_to_move_mismatch", records[0].warnings)
        self.assertIn("1. d4 Nf6 2. Nc3 d5", records[0].movetext)

    def test_opening_descriptor_prefix_is_not_exported_as_game_moves(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "D02: 1 d4 d5 2 Nf3 sidelines, including\n"
                "2...Nf6 3 g3 and 2...Nf6 3 Bf4 1.d4 Nf6\n"
                "2.Nc3 d5 3.Bf4 Bf5 4.e3 e6 1-0",
                page_num=0,
                source_title="Opening descriptor",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. d4 Nf6 2. Nc3 d5 3. Bf4 Bf5 4. e3 e6 1-0", records[0].movetext)
        self.assertNotIn("2... Nf6 3. Bf4 1.", records[0].movetext)
        self.assertNotIn("move_number_regression", records[0].warnings)

    def test_opening_descriptor_without_real_game_is_not_a_pgn_record(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "D02: 1 d4 d5 2 Nf3 sidelines, including\n"
            "2...Nf6 3 g3 and 2...Nf6 3 Bf4",
            page_num=0,
            source_title="Opening descriptor only",
            ocr_confidence=1.0,
        )

        self.assertEqual(records, [])

    def test_prose_alternative_move_reference_does_not_shadow_real_move(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.Nc3 d5 2.d4 Nf6 3.Bf4 c5 4.e3 cxd4 5.exd4 a6\n"
                "6.Nf3 Bg4 7.h3 Bxf3 8.Qxf3 Nc6 9.O-O-O e6 10.g4 Bd6\n"
                "11.Be3\n"
                "is now debated instead of 11.g5.\n"
                "11...Rc8 12.g5 Nd7 13.Kb1 Nb6 1-0",
                page_num=0,
                source_title="Prose alternative",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("10. g4 Bd6 11. Be3 Rc8 12. g5 Nd7", records[0].movetext)
        self.assertNotIn("11. g5 Rc8", records[0].movetext)

    def test_prose_comparison_move_reference_does_not_replace_real_move(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.d4 d5 2.Nc3 Nf6 3.Bf4 a6 4.e3 e6 5.a3 c5 6.Nf3 Nc6\n"
                "7.h3! is recently more successful than 7.Be2. "
                "ist inzwischen erfolgreicher als 7. Le2.7...Bd6 8.dxc5 Bxc5 1-0",
                page_num=0,
                source_title="Comparison prose",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("6. Nf3 Nc6 7. h3! Bd6 8. dxc5 Bxc5 1-0", records[0].movetext)
        self.assertNotIn("7. e2", records[0].movetext)
        self.assertNotIn("7. Be2", records[0].movetext)

    def test_prose_modern_continuation_reference_does_not_replace_capture(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.d4 d5 2.Bf4 Nf6 3.Nc3 g6 4.e3 Bg7 5.Nf3 O-O 6.Be2 c5 "
                "7.Ne5 cxd4 8.exd4 Nc6 9.O-O Bf5 10.Nxc6 "
                "10.Re1 is the modern continuation. 10. Te1 ist die moderne Fortsetzung. "
                "10...bxc6 11.Be5 Qa5 1-0",
                page_num=0,
                source_title="Modern continuation prose",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("9. O-O Bf5 10. Nxc6 bxc6 11. Be5 Qa5 1-0", records[0].movetext)
        self.assertNotIn("10. e1", records[0].movetext)
        self.assertNotIn("10. Re1", records[0].movetext)

    def test_split_letter_modern_continuation_reference_does_not_replace_capture(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.d4 d5 2.Bf4 Nf6 3.Nc3 g6 4.e3 Bg7 5.Nf3 O-O 6.Be2 c5 "
                "7.Ne5 cxd4 8.exd4 Nc6 9.O-O Bf5 10.Nxc6 "
                "10.Re1 ist die m o d e r n e F o r t s e t z u n g. "
                "10.Re1 is the m o d e r n c o n t i n u a t i o n. "
                "10...bxc6 11.Be5 Qa5 1-0",
                page_num=0,
                source_title="Split prose reference",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("9. O-O Bf5 10. Nxc6 bxc6 11. Be5 Qa5 1-0", records[0].movetext)
        self.assertNotIn("10. Re1", records[0].movetext)

    def test_move_clock_artifacts_between_san_tokens_do_not_hide_moves(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.Nc3 2 Nf6 3:14 2.d4 21d5 8 3.Bf4 4 c5 9 "
                "4.e3 6cxd4 49 5.exd4 5a6 55 1-0",
                page_num=0,
                source_title="Clock artifact sample",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. Nc3 Nf6 2. d4 d5 3. Bf4 c5 4. e3 cxd4 5. exd4 a6 1-0", records[0].movetext)
        self.assertNotIn("move_number_jump", records[0].warnings)

    def test_engine_eval_glued_to_comment_does_not_create_zero_move(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "24.Qd4 Qh2 25.Ne3 0.95/18aiming for Nf1. 25...Rxh4 26.Qxa7 Bc8 1-0",
            page_num=0,
            source_title="Glued eval comment",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].warnings, [])
        self.assertIn("25. Ne3 Rxh4", records[0].movetext)
        self.assertNotIn("0.", records[0].movetext)
        self.assertNotIn("Nf1", records[0].movetext)

    def test_bare_eval_glued_to_move_keeps_following_move(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "1.d4 1...Nf6 2.Nc3 0.08d5N 3.Bf4 0.00c5 4.e3 cxd4 5.exd4 0.02a6N 1-0",
            page_num=0,
            source_title="Glued bare eval",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertNotIn("move_number_jump", records[0].warnings)
        self.assertNotIn("move_number_regression", records[0].warnings)
        self.assertNotIn("side_to_move_mismatch", records[0].warnings)
        self.assertIn("2. Nc3 d5 3. Bf4 c5 4. e3 cxd4 5. exd4 a6", records[0].movetext)
        self.assertNotIn("0.", records[0].movetext)

    def test_san_ocr_digit_suffix_tokens_are_normalized_with_audit_warnings(self) -> None:
        repaired = [_normalize_ocr_san_token(value) for value in ("Rxh24", "Kxh24", "Rh2#4")]

        self.assertEqual([token for token, _warnings in repaired], ["Rxh2", "Kxh2", "Rh2#"])
        for _token, warnings in repaired:
            self.assertIn("san_ocr_token_repaired", warnings)
            self.assertIn("san_ocr_digit_suffix_suppressed", warnings)

    def test_san_ocr_digit_prefix_tokens_are_normalized_with_audit_warnings(self) -> None:
        repaired = [_normalize_ocr_san_token(value) for value in ("1d5", "8d5N")]

        self.assertEqual([token for token, _warnings in repaired], ["d5", "d5"])
        for _token, warnings in repaired:
            self.assertIn("san_ocr_digit_prefix_suppressed", warnings)
            self.assertIn("san_ocr_token_repaired", warnings)

    def test_ambiguous_digit_prefixed_san_candidate_is_not_promoted(self) -> None:
        token, warnings = _normalize_ocr_san_token("2xh2")

        self.assertEqual(token, "")
        self.assertEqual(warnings, [])

    def test_annotation_noise_numbers_do_not_create_fake_san_tokens(self) -> None:
        tokens = _extract_pgn_tokens("26!? 14! 7?")

        self.assertEqual(tokens, [])

    def test_sensor_board_error_comment_does_not_create_fake_moves(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.e4 e5 2.Nf3 Nc6 Sensor Board Error (Ke4/e5)? 1-0",
                page_num=0,
                source_title="Sensor comment",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("1. e4 e5 2. Nf3 Nc6", records[0].movetext)
        self.assertNotIn("Ke4", records[0].movetext)
        self.assertNotIn("e5 3.", records[0].movetext)

    def test_pdf_hex_float_artifact_does_not_create_zero_move(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1.e4 e5 0x0.001d6b67c29bdp-1022s more active pieces. 2.Nf3 Nc6 1-0",
                page_num=0,
                source_title="Hex float artifact",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertNotIn("invalid_move_number_zero", records[0].warnings)
        self.assertNotIn("0.", records[0].movetext)

    def test_decimal_draw_result_is_not_tokenized_as_zero_move(self) -> None:
        records = extract_chess_pgn_records_from_text(
            "1.e4 e5 0.5-0.5",
            page_num=0,
            source_title="Decimal draw",
            ocr_confidence=1.0,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].result, "1/2-1/2")
        self.assertNotIn("invalid_move_number_zero", records[0].warnings)

    def test_castle_followed_by_broken_negative_eval_keeps_kingside_castle(self) -> None:
        text = (
            "1.d4 Nf6 2.Nc3 d5 3.Bf4 c5 4.e3 cxd4 5.exd4 a6 "
            "6.Nf3 Nc6 7.Ne5 Bd7 8.g4 h6 9.h4 g6 10.Bh3 Bg7 "
            "11.Nxd7 Nxd7 12.Nxd5 Qa5+ 13.Nc3 Nxd4 14.O-O Ne5 "
            "15.Bg2 Rd8 16.Bxe5 Bxe5 17.Re1 O-O-O. 79/20 "
            "18.Qd3 e6 1-0"
        )

        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                text,
                page_num=0,
                source_title="Castle eval OCR",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("17. Re1 O-O 18. Qd3 e6", records[0].movetext)
        self.assertNotIn("O-O-O", records[0].movetext)

    def test_mixed_zero_letter_queenside_castle_is_normalized(self) -> None:
        text = (
            "1.d4 Nf6 2.Nc3 d5 3.Bf4 c5 4.e3 cxd4 5.exd4 Bg4 "
            "6.Be2 Bxe2 7.Qxe2 a6 8.O-O-0N e6 9.g4 Bb4 1-0"
        )

        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                text,
                page_num=0,
                source_title="Mixed castle OCR",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "accepted")
        self.assertIn("8. O-O-O e6", records[0].movetext)
        self.assertNotIn("8. O-O e6", records[0].movetext)

    def test_combined_pgn_falls_back_to_strict_pgn_when_annotations_are_not_parse_clean(self) -> None:
        record = ChessPgnRecord(
            id="bad-annotation",
            source_pages=[1],
            title="Bad annotation",
            headers={"Event": "Bad annotation", "Result": "*"},
            movetext="1. e4 e5 *",
            pgn='[Event "Bad annotation"]\n[Result "*"]\n\n1. e4 e5 *\n',
            annotated_pgn='[Event "Bad annotation"]\n[Result "*"]\n\n1. e4 e5 (2. Qxf7) *\n',
            status="accepted",
            final_fen="rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
        )

        combined = build_combined_pgn([record])

        self.assertIn("1. e4 e5 *", combined)
        self.assertNotIn("(2. Qxf7)", combined)

    def test_annotated_pgn_strips_chessbase_eval_and_time_extensions(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 [%eval 0.36] e5 [%emt 0:00:05] 2. Nf3 [%eval -0.12/18] Nc6 *",
                page_num=1,
                source_title="ChessBase extensions",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        exported = build_combined_pgn(records)
        self.assertIn("{0.36}", exported)
        self.assertIn("{-0.12/18}", exported)
        self.assertIn("Nc6 *", exported)
        self.assertNotIn("{*}", exported)
        self.assertNotIn("[%eval", exported)
        self.assertNotIn("[%emt", exported)

    def test_annotated_pgn_repairs_engine_comments_and_square_bracket_variations(self) -> None:
        raw = """
D00
Carlsen, M
Duda, J 2739
Titled Tue 4th Mar Late blitz (11)
[Kitty Kat]
B13: Caro-Kann: Exchange Variation
1.d4 d5 2.Nc3 Nf6 3.Bf4 c5 4.e3 cxd4 5.exd4 a6
8.Ne2 The position is equal. 8...e6
14.Rb1 Qc8 0.64/17 [ 14...Nxe5= 0.21/18 should be considered. 15.dxe5 Ne8 ]
16.Re3 &Bianco is much more active.
23...Kf7... Rh8 is the strong threat.
29.Rd3+ Bd5 30.Rxd5+ [ 30.Rb6 Kc7 31.Rd6 Rb8+- ]
39...Qd5=Inhibits Bd8+.
(Diagram)
43.Bd8+ Weighted Error Value: White=0.62/ Black=0.53
1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Jobava comments",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        pgn_text = records[0].annotated_pgn
        self.assertIn('[Result "1-0"]', pgn_text)
        self.assertIn("{0.64/17}", pgn_text)
        self.assertIn("{Bianco is much more active.}", pgn_text)
        self.assertIn("23... Kf7 {Rh8 is the strong threat.}", pgn_text)
        self.assertIn("(30. Rb6 Kc7 31. Rd6 Rb8", pgn_text)
        self.assertIn("39... Qd5 {Inhibits Bd8+.}", pgn_text)
        self.assertIn("{Weighted Error Value: White=0.62/ Black=0.53}", pgn_text)
        self.assertNotIn("[ 30.Rb6", pgn_text)
        self.assertNotIn("{=Inhibits", pgn_text)
        self.assertNotIn("(Diagram)", pgn_text)

    def test_annotated_pgn_strips_equal_before_inline_text_comment_on_later_line(self) -> None:
        raw = """
1.d4 d5
39...Qd5=Inhibits Bd8+.
43.Bd8+ Weighted Error Value: White=0.62/ Black=0.53
1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Inline equals",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        pgn_text = records[0].annotated_pgn
        self.assertIn("39... Qd5 {Inhibits Bd8+.}", pgn_text)
        self.assertNotIn("{=Inhibits", pgn_text)

    def test_annotated_pgn_drops_chessbase_variation_marker_from_square_brackets(self) -> None:
        raw = """
1.d4 d5
32...Rb8 33.Rd1 4.74/21 [⌒ 33.Rbb4 9.93/23 Intending Be3 and mate. Kd5]
1-0
"""
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                raw,
                page_num=0,
                source_title="Variation marker",
                ocr_confidence=1.0,
            )
        )

        self.assertEqual(len(records), 1)
        pgn_text = records[0].annotated_pgn
        self.assertIn("(33. Rbb4 {9.93/23} {Intending Be3 and mate.} Kd5)", pgn_text)
        self.assertNotIn("\u2312", pgn_text)
        self.assertNotIn("{?}", pgn_text)

    def test_pgn_download_html_has_copy_pgn_and_full_notation_buttons(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 *",
                page_num=1,
                source_title="Copy sample",
                ocr_confidence=1.0,
            )
        )

        html = build_pgn_download_html(records, title="Copy sample")

        self.assertIn("Kopiuj PGN", html)
        self.assertIn("Kopiuj pełną notację", html)
        self.assertIn("data-copy-target=", html)
        self.assertIn("navigator.clipboard", html)
        self.assertIn('[Event &quot;Copy sample&quot;]', html)

    def test_pgn_download_html_hides_unmapped_glyphs_from_copyable_pgn(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 \"'t!;>b3\" *",
                page_num=1,
                source_title="Glyph sample",
                ocr_confidence=1.0,
            )
        )

        html = build_pgn_download_html(records, title="Glyph sample")
        soup = BeautifulSoup(html, "html.parser")

        self.assertIn("Do weryfikacji", html)
        self.assertNotIn("\"'t!;>b3\"", html)
        self.assertIsNone(soup.select_one(".chess-pgn-mainline"))
        self.assertIn("nierozpoznany glyph", html)

    def test_pgn_download_html_shows_full_book_notation_before_strict_pgn(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "1. e4 e5 2. Nf3 Nc6 *",
                page_num=1,
                source_title="Notation first",
                ocr_confidence=1.0,
            )
        )

        soup = BeautifulSoup(build_pgn_download_html(records, title="Notation first"), "html.parser")
        section = soup.select_one("section.chess-pgn-game")
        children = [child for child in section.children if getattr(child, "name", None)]
        full_index = children.index(section.select_one("div.chess-full-notation"))
        strict_index = children.index(section.select_one("div.chess-pgn-mainline"))

        self.assertLess(full_index, strict_index)

    def test_layout_aware_pgn_html_renders_diagram_card(self) -> None:
        records = annotate_records_with_replayed_fens(
            extract_chess_pgn_records_from_text(
                "Diagram 1-2\n1. e4 e5 2. Nf3 Nc6 *",
                page_num=0,
                source_title="Diagram sample",
                ocr_confidence=1.0,
            )
        )
        diagram = ChessDiagramRecord(
            id="diagram-1",
            page_index=0,
            page_number=1,
            bbox=(12.0, 24.0, 132.0, 144.0),
            caption="Diagram 1-2",
            image_data_uri="data:image/png;base64,AA==",
            fen_candidate="8/8/8/8/8/8/8/8 w - - 0 1",
        )

        html = build_pgn_download_html(
            records,
            title="Diagram sample",
            diagrams=[diagram],
            pdf_preview_href="pdf_layout_preview",
        )
        soup = BeautifulSoup(html, "html.parser")

        self.assertIn("Original OCR fragment: Wixh7t liJ noisy OCR", html)
        self.assertIn("Znormalizowana pr", html)
        self.assertIn("1. Rh8+ Kxh8 *", html)
        self.assertNotIn("[Event &quot;OCR review&quot;]", html)

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

    def test_semantic_cleanup_keeps_generated_pgn_review_section(self) -> None:
        soup = BeautifulSoup(
            '<section class="chess-pgn-review" id="game-review">'
            '<p class="chess-pgn-review-title"><strong>PGN do weryfikacji</strong></p>'
            '<pre class="chess-pgn-review-text"><code>24. Qe3 10. Bxe5 *</code></pre></section>',
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
        self.assertIn('class="chess-pgn-review"', blocks[0]["html"])


if __name__ == "__main__":
    unittest.main()
