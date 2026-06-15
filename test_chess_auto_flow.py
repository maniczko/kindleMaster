from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from chess_auto_flow import build_auto_chess_flow_artifacts, validate_auto_chess_output


VALID_KINGS_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
VALID_PGN = """[Event "Synthetic"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]
[SourcePage "1"]

1. e4 *
"""


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class AutoChessFlowTests(unittest.TestCase):
    def test_build_auto_flow_writes_canonical_artifacts_for_accepted_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "schema": "kindlemaster.semantic_chess_html.v1",
                    "pages": [
                        {
                            "page": 1,
                            "text_blocks": [{"text": "A forcing move.", "bbox": [1, 2, 30, 12], "reading_order": 1}],
                            "diagrams": [
                                {
                                    "diagram_id": "p001_d001",
                                    "page": 1,
                                    "image_path": "assets/diagrams/p001_d001.png",
                                    "fen": VALID_KINGS_FEN,
                                    "confidence": 0.99,
                                    "warnings": [],
                                    "validation_status": "accepted",
                                }
                            ],
                            "pgn_records": [
                                {
                                    "record_id": "pgn_001",
                                    "page": 1,
                                    "status": "accepted",
                                    "pgn": VALID_PGN,
                                    "warnings": [],
                                }
                            ],
                        }
                    ],
                    "pgn_records": [
                        {
                            "record_id": "pgn_001",
                            "page": 1,
                            "status": "accepted",
                            "pgn": VALID_PGN,
                            "warnings": [],
                        }
                    ],
                },
            )
            (out / "data" / "games.pgn").write_text(VALID_PGN, encoding="utf-8")
            _write_json(
                out / "reports" / "chess_quality_dashboard.json",
                {"pages": 1, "diagrams_total": 1, "fen_accepted": 1, "pgn_total": 1, "accepted_pgn": 1},
            )

            payload = build_auto_chess_flow_artifacts(out)

            self.assertEqual(payload["status"], "AUTO_SUCCESS")
            self.assertTrue((out / "pages" / "pages.json").is_file())
            self.assertTrue((out / "layout" / "layout.json").is_file())
            self.assertTrue((out / "text" / "text_blocks.jsonl").is_file())
            self.assertTrue((out / "fen" / "fen_candidates.json").is_file())
            self.assertTrue((out / "pgn" / "pgn_validation.json").is_file())
            self.assertTrue((out / "report" / "quality_report.html").is_file())
            self.assertEqual((out / "export" / "games.pgn").read_text(encoding="utf-8"), VALID_PGN)

            fen_payload = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(fen_payload["items"][0]["status"], "FEN_MACHINE_ACCEPTED")
            self.assertEqual(fen_payload["items"][0]["runtime_status"], "FEN_MACHINE_ACCEPTED")
            self.assertEqual(fen_payload["items"][0]["corpus_status"], "not_corpus_verified")
            pgn_payload = json.loads((out / "pgn" / "pgn_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(pgn_payload["items"][0]["status"], "PGN_MACHINE_ACCEPTED")
            self.assertTrue((out / "report" / "acceptance_blockers.json").is_file())

    def test_ai_fen_candidate_remains_review_only_without_human_or_deterministic_acceptance(self) -> None:
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
                                    "diagram_id": "p010_d002",
                                    "page": 10,
                                    "image_path": "assets/diagrams/p010_d002.png",
                                    "validation_status": "needs_review",
                                }
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )
            (out / "review").mkdir(parents=True)
            (out / "review" / "ai_fen_candidates.jsonl").write_text(
                json.dumps(
                    {
                        "diagram_id": "p010_d002",
                        "ai_fen_candidate": VALID_KINGS_FEN,
                        "confidence": 0.99,
                        "status": "ai_suggested",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            payload = build_auto_chess_flow_artifacts(out)

            self.assertEqual(payload["status"], "MANUAL_REVIEW_AVAILABLE")
            fen_payload = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            item = fen_payload["items"][0]
            self.assertEqual(item["status"], "FEN_MACHINE_VALID")
            self.assertEqual(item["runtime_status"], "FEN_MACHINE_VALID")
            self.assertIsNone(item["selected_value"])
            self.assertEqual(item["next_action"], "resolve_machine_acceptance_blockers_or_human_verify")
            blocker_codes = {
                blocker["code"]
                for blocker in item["acceptance_blockers"]
            }
            self.assertIn("ai_review_only_source", blocker_codes)

    def test_acceptance_blockers_report_groups_fen_and_pgn_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [
                        {
                            "page": 1,
                            "diagrams": [{"diagram_id": "p001_d001", "validation_status": "needs_review"}],
                            "pgn_records": [
                                {
                                    "record_id": "pgn_001",
                                    "page": 1,
                                    "pgn": "not a pgn",
                                    "warnings": ["unmapped_chess_glyphs"],
                                }
                            ],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [
                        {
                            "record_id": "pgn_001",
                            "page": 1,
                            "pgn": "not a pgn",
                            "warnings": ["unmapped_chess_glyphs"],
                        }
                    ],
                },
            )

            build_auto_chess_flow_artifacts(out)

            report = json.loads((out / "report" / "acceptance_blockers.json").read_text(encoding="utf-8"))
            codes = set(report["summary"]["by_code"])
            self.assertIn("fen_not_recognized", codes)
            self.assertIn("unmapped_chess_glyphs", codes)
            self.assertTrue((out / "report" / "acceptance_blockers.html").is_file())

    def test_exercise_solution_pgn_requires_accepted_source_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [
                        {
                            "page": 12,
                            "diagrams": [
                                {
                                    "diagram_id": "diagram-1-1",
                                    "label": "Diagram 1-1",
                                    "validation_status": "needs_review",
                                }
                            ],
                            "pgn_records": [
                                {
                                    "record_id": "solution_1_1",
                                    "page": 12,
                                    "label": "Ex. 1-1",
                                    "raw_text": "Ex. 1-1 1. e4",
                                    "pgn": VALID_PGN,
                                    "warnings": [],
                                }
                            ],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [
                        {
                            "record_id": "solution_1_1",
                            "page": 12,
                            "label": "Ex. 1-1",
                            "raw_text": "Ex. 1-1 1. e4",
                            "pgn": VALID_PGN,
                            "warnings": [],
                        }
                    ],
                },
            )

            build_auto_chess_flow_artifacts(out)

            pgn_payload = json.loads((out / "pgn" / "pgn_candidates.json").read_text(encoding="utf-8"))
            item = pgn_payload["items"][0]
            self.assertEqual(item["source_type"], "EXERCISE_SOLUTION")
            self.assertIsNone(item["selected_value"])
            self.assertIn("source_fen_not_machine_accepted", {error["code"] for error in item["validation_errors"]})

    def test_fen_recognition_limit_zero_means_all_and_positive_limit_reports_skipped_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            diagrams = [
                {"diagram_id": f"p001_d00{index}", "page": 1, "fen": VALID_KINGS_FEN, "confidence": 0.99}
                for index in range(1, 4)
            ]
            _write_json(
                out / "data" / "book.json",
                {"pages": [{"page": 1, "diagrams": diagrams, "pgn_records": [], "text_blocks": []}], "pgn_records": []},
            )

            build_auto_chess_flow_artifacts(out, chess_fen_recognition_max_diagrams=2)

            limited = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(limited["summary"]["skipped_diagram_count"], 1)
            self.assertEqual(limited["summary"]["skipped_diagram_ids"], ["p001_d003"])

            build_auto_chess_flow_artifacts(out, chess_fen_recognition_max_diagrams=0)
            unlimited = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(unlimited["summary"]["skipped_diagram_count"], 0)

    def test_strict_validation_fails_when_fen_or_pgn_remain_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [
                        {
                            "page": 1,
                            "diagrams": [{"diagram_id": "p001_d001", "validation_status": "needs_review"}],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )
            build_auto_chess_flow_artifacts(out, mode="auto-strict")

            validation = validate_auto_chess_output(out, strict=True)

            self.assertEqual(validation["overall_status"], "failed")
            self.assertIn("strict_unresolved_chess_items", {error["code"] for error in validation["errors"]})

    def test_kindlemaster_process_command_routes_to_auto_chess_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_payload = {"status": "MANUAL_REVIEW_AVAILABLE", "strict_failed": False, "out_dir": temp_dir}
            argv = ["kindlemaster.py", "process", "study.pdf", "--out", temp_dir, "--mode", "auto"]
            with patch.object(sys, "argv", argv):
                with patch("chess_auto_flow.run_auto_chess_process", return_value=fake_payload) as run_mock:
                    with contextlib.redirect_stdout(io.StringIO()) as stdout:
                        exit_code = kindlemaster.main()

            self.assertEqual(exit_code, 0)
            run_mock.assert_called_once()
            self.assertIn("MANUAL_REVIEW_AVAILABLE", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
