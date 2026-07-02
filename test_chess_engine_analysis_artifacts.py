from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_auto_flow import build_auto_chess_flow_artifacts
from chess_engine_analysis import build_engine_analysis_artifacts

VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
BLACK_FEN = "4k3/8/8/8/8/8/8/4K3 b - - 0 1"


class ChessEngineAnalysisArtifactTests(unittest.TestCase):
    def test_accepted_trusted_fen_is_analyzed_and_artifacts_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            payload = build_engine_analysis_artifacts(
                out,
                [
                    {
                        "diagram_id": "p001_d001",
                        "page": 1,
                        "fen": VALID_FEN,
                        "runtime_status": "FEN_MACHINE_ACCEPTED",
                        "side_marker_status": "trusted_marker",
                        "side_to_move": "w",
                    }
                ],
                analyze_fen_fn=_fake_ok_engine,
            )

            report = payload["report"]
            item = report["items"][0]
            self.assertEqual(report["summary"]["diagram_count"], 1)
            self.assertEqual(report["summary"]["eligible_count"], 1)
            self.assertEqual(report["summary"]["analyzed_count"], 1)
            self.assertEqual(item["engine_status"], "ok")
            self.assertEqual(item["best_move_uci"], "e1e2")
            self.assertEqual(item["best_move_san"], "Ke2")
            self.assertEqual(item["score_cp"], 12)
            self.assertTrue((out / "reports" / "chess_engine" / "engine_analysis.json").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "engine_analysis.jsonl").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "engine_analysis.md").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "engine_analysis.html").is_file())
            self.assertTrue((out / "data" / "engine_analysis.json").is_file())

    def test_review_only_fen_is_skipped_without_calling_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            payload = build_engine_analysis_artifacts(
                out,
                [
                    {
                        "diagram_id": "p001_d001",
                        "page": 1,
                        "fen": VALID_FEN,
                        "runtime_status": "FEN_REVIEW_REQUIRED",
                        "side_marker_status": "trusted_marker",
                    }
                ],
                analyze_fen_fn=_raising_engine,
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["engine_status"], "skipped")
            self.assertEqual(item["skip_reason"], "fen_not_accepted")

    def test_missing_trusted_side_marker_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            payload = build_engine_analysis_artifacts(
                out,
                [
                    {
                        "diagram_id": "p001_d001",
                        "page": 1,
                        "fen": BLACK_FEN,
                        "runtime_status": "FEN_MACHINE_ACCEPTED",
                        "side_marker_status": "marker_missing",
                    }
                ],
                analyze_fen_fn=_raising_engine,
            )

            item = payload["report"]["items"][0]
            self.assertEqual(item["engine_status"], "skipped")
            self.assertEqual(item["skip_reason"], "side_to_move_not_trusted")

    def test_engine_unavailable_is_reported_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            payload = build_engine_analysis_artifacts(
                out,
                [
                    {
                        "diagram_id": "p001_d001",
                        "page": 1,
                        "fen": VALID_FEN,
                        "runtime_status": "FEN_MACHINE_ACCEPTED",
                        "side_marker_status": "trusted_marker",
                    }
                ],
                analyze_fen_fn=_fake_engine_unavailable,
            )

            summary = payload["report"]["summary"]
            item = payload["report"]["items"][0]
            self.assertEqual(item["engine_status"], "engine_unavailable")
            self.assertEqual(item["skip_reason"], "engine_unavailable")
            self.assertEqual(summary["engine_unavailable_count"], 1)

    def test_auto_chess_flow_registers_engine_analysis_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [
                        {
                            "page": 1,
                            "diagrams": [
                                {
                                    "diagram_id": "p001_d001",
                                    "page": 1,
                                    "fen": VALID_FEN,
                                    "runtime_status": "FEN_MACHINE_ACCEPTED",
                                    "side_marker_status": "trusted_marker",
                                }
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )
            flow = build_auto_chess_flow_artifacts(out)

            artifacts = flow["artifacts"]
            self.assertIn("engine_analysis", artifacts)
            self.assertIn("engine_analysis_jsonl", artifacts)
            self.assertIn("engine_analysis_md", artifacts)
            self.assertIn("engine_analysis_html", artifacts)
            self.assertIn("engine_analysis_data", artifacts)
            self.assertTrue((out / "auto_chess_flow.json").is_file())
            self.assertTrue(Path(artifacts["engine_analysis"]).is_file())
            self.assertIn("engine_unavailable_count", flow["engine_analysis"])


def _fake_ok_engine(fen: str, **_: object) -> dict:
    return {
        "status": "ok",
        "fen": fen,
        "side_to_move": fen.split()[1],
        "best_move_uci": "e1e2",
        "best_move_san": "Ke2",
        "score_cp": 12,
        "mate": None,
        "pv": [{"rank": 1, "moves_uci": ["e1e2"], "moves_san": ["Ke2"], "score_cp": 12, "mate": None}],
        "depth": 6,
        "elapsed_ms": 7,
        "cache": {"hit": False, "key": "test"},
        "warnings": [],
    }


def _fake_engine_unavailable(fen: str, **_: object) -> dict:
    return {
        "status": "engine_unavailable",
        "fen": fen,
        "side_to_move": fen.split()[1],
        "best_move_uci": "",
        "best_move_san": "",
        "score_cp": None,
        "mate": None,
        "pv": [],
        "depth": None,
        "elapsed_ms": 1,
        "cache": {"hit": False, "key": ""},
        "warnings": ["stockfish_not_found"],
    }


def _raising_engine(*_: object, **__: object) -> dict:
    raise AssertionError("engine should not be called")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
