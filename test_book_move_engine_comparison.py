from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chess_book_move_comparison import build_book_move_engine_comparison


VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


class BookMoveEngineComparisonTests(unittest.TestCase):
    def test_exact_match_writes_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)

            payload = build_book_move_engine_comparison(
                out,
                diagrams=[_diagram()],
                fen_payload=_fen_payload(),
                engine_payload=_engine_payload(best_uci="e1e2", best_san="Ke2"),
                pgn_payload=_pgn_payload(_solution_pgn("Ke2")),
                pgn_records=[_source_record(_solution_pgn("Ke2"))],
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["match_status"], "exact_match")
            self.assertFalse(item["requires_review"])
            self.assertEqual(item["book_move_san"], "Ke2")
            self.assertEqual(item["book_move_uci"], "e1e2")
            self.assertEqual(payload["report"]["summary"]["exact_match_count"], 1)
            self.assertTrue((out / "reports" / "chess_engine" / "book_move_comparison.json").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "book_move_comparison.md").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "book_move_comparison.html").is_file())
            self.assertTrue((out / "data" / "book_move_comparison.json").is_file())

    def test_legal_book_move_that_is_not_best_requires_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_book_move_engine_comparison(
                temp_dir,
                diagrams=[_diagram()],
                fen_payload=_fen_payload(),
                engine_payload=_engine_payload(best_uci="e1e2", best_san="Ke2"),
                pgn_payload=_pgn_payload(_solution_pgn("Kd2")),
                pgn_records=[_source_record(_solution_pgn("Kd2"))],
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["match_status"], "book_move_legal_but_not_best")
            self.assertTrue(item["requires_review"])
            self.assertEqual(item["book_move_uci"], "e1d2")

    def test_illegal_book_move_is_reported_without_correction(self) -> None:
        start_position_pgn = """[Event "Synthetic"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 *
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_book_move_engine_comparison(
                temp_dir,
                diagrams=[_diagram()],
                fen_payload=_fen_payload(),
                engine_payload=_engine_payload(best_uci="e1e2", best_san="Ke2"),
                pgn_payload=_pgn_payload(start_position_pgn),
                pgn_records=[_source_record(start_position_pgn)],
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["match_status"], "book_move_illegal")
            self.assertTrue(item["requires_review"])
            self.assertIn("illegal", item["review_reason"])

    def test_missing_book_move_returns_no_book_move(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_book_move_engine_comparison(
                temp_dir,
                diagrams=[_diagram()],
                fen_payload=_fen_payload(),
                engine_payload=_engine_payload(best_uci="e1e2", best_san="Ke2"),
                pgn_payload={"items": []},
                pgn_records=[],
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["match_status"], "no_book_move")
            self.assertTrue(item["requires_review"])

    def test_engine_unavailable_is_a_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = build_book_move_engine_comparison(
                temp_dir,
                diagrams=[_diagram()],
                fen_payload=_fen_payload(),
                engine_payload={"items": [{"diagram_id": "p001_d001", "engine_status": "engine_unavailable"}]},
                pgn_payload=_pgn_payload(_solution_pgn("Ke2")),
                pgn_records=[_source_record(_solution_pgn("Ke2"))],
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["match_status"], "engine_unavailable")
            self.assertTrue(item["requires_review"])


def _diagram() -> dict:
    return {"diagram_id": "p001_d001", "label": "Diagram 1-1", "page": 1}


def _fen_payload() -> dict:
    return {
        "items": [
            {
                "id": "p001_d001",
                "page": 1,
                "status": "FEN_MACHINE_ACCEPTED",
                "runtime_status": "FEN_MACHINE_ACCEPTED",
                "selected_value": VALID_FEN,
            }
        ]
    }


def _engine_payload(*, best_uci: str, best_san: str) -> dict:
    return {
        "items": [
            {
                "diagram_id": "p001_d001",
                "page": 1,
                "engine_status": "ok",
                "best_move_uci": best_uci,
                "best_move_san": best_san,
                "score_cp": 12,
                "pv": [{"rank": 1, "moves_uci": [best_uci], "moves_san": [best_san], "score_cp": 12, "mate": None}],
            }
        ]
    }


def _pgn_payload(pgn: str) -> dict:
    return {
        "items": [
            {
                "id": "solution_1",
                "page": 1,
                "label": "Diagram 1-1",
                "source_diagram": "Diagram 1-1",
                "status": "SOLUTION_LINE_ACCEPTED",
                "runtime_status": "SOLUTION_LINE_ACCEPTED",
                "source_type": "EXERCISE_SOLUTION",
                "source_fen": VALID_FEN,
                "selected_value": pgn,
            }
        ]
    }


def _source_record(pgn: str) -> dict:
    return {
        "record_id": "solution_1",
        "page": 1,
        "label": "Diagram 1-1",
        "pgn": pgn,
        "status": "accepted",
    }


def _solution_pgn(move_san: str) -> str:
    return f"""[Event "Synthetic"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]
[SetUp "1"]
[FEN "{VALID_FEN}"]

1. {move_san} *
"""


if __name__ == "__main__":
    unittest.main()
