from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from chess_auto_flow import (
    _side_marker_assignment_report,
    apply_runtime_accepted_fen,
    apply_runtime_accepted_pgn,
    build_auto_chess_flow_artifacts,
    run_auto_chess_process,
    validate_auto_chess_output,
)
from chess_fen_beam_search import build_deterministic_ensemble_fen
from chess_fen_hardening import fen_to_cells
from chess_pgn_auto_repair import repair_and_accept_pgn_records


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


def _squares_from_fen(fen: str, *, confidence: float = 0.99) -> list[dict]:
    rows: list[dict] = []
    for index, piece in enumerate(fen_to_cells(fen)):
        square = f"{'abcdefgh'[index % 8]}{'87654321'[index // 8]}"
        label = piece or "empty"
        rows.append(
            {
                "square": square,
                "class": label,
                "piece": piece,
                "confidence": confidence,
                "alternatives": [
                    {"class": label, "piece": piece, "confidence": confidence, "source": "model_centroid"},
                    {"class": "empty", "piece": "", "confidence": 0.01, "source": "model_centroid"},
                    {"class": "P", "piece": "P", "confidence": 0.005, "source": "model_centroid"},
                ],
            }
        )
    return rows


class AutoChessFlowTests(unittest.TestCase):
    def test_side_marker_report_preserves_explicit_zero_assignment_scores(self) -> None:
        report = _side_marker_assignment_report(
            [
                {
                    "diagram_id": "d1",
                    "marker_candidate_confidence": 0.0,
                    "marker_assignment_confidence": 0.0,
                    "marker_assignment_runner_up_margin": 0.0,
                }
            ],
            {
                "items": [
                    {
                        "id": "d1",
                        "marker_candidate_confidence": 0.8,
                        "marker_assignment_confidence": 0.7,
                        "marker_assignment_runner_up_margin": 0.6,
                    }
                ]
            },
        )

        row = report["items"][0]
        self.assertEqual(row["marker_candidate_confidence"], 0.0)
        self.assertEqual(row["marker_assignment_confidence"], 0.0)
        self.assertEqual(row["marker_assignment_runner_up_margin"], 0.0)

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
                                    "side_to_move": "w",
                                    "side_to_move_status": "explicit",
                                    "side_to_move_evidence": "marker",
                                    "side_marker_symbol": "\u25b3",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_source": "marker",
                                    "side_marker_confidence": 0.94,
                                    "board_crop_quality": "pass",
                                    "board_crop_quality_gate": {"decision": "pass", "reasons": []},
                                    "marker_crop_quality": "pass",
                                    "marker_bbox": [10.0, 20.0, 30.0, 40.0],
                                    "selected_marker_zone": "right",
                                    "marker_crop_quality_gate": {"decision": "pass", "component_count": 1, "reasons": []},
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
            self.assertIn("book_move_comparison", payload["artifacts"])
            self.assertIn("engine_hints", payload["artifacts"])
            self.assertIn("engine_hints_data", payload["artifacts"])
            self.assertTrue((out / "reports" / "chess_engine" / "book_move_comparison.json").is_file())
            self.assertTrue((out / "data" / "book_move_comparison.json").is_file())
            self.assertTrue((out / "reports" / "chess_engine" / "engine_hints.json").is_file())
            self.assertTrue((out / "data" / "engine_hints.json").is_file())
            self.assertIn("available_count", payload["engine_hints"])

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
            self.assertEqual(item["candidate_values"][0]["placement_runtime_status"], "FEN_PLACEMENT_REVIEW_REQUIRED")
            self.assertIn(
                "ai_review_only_source",
                {blocker["code"] for blocker in item["candidate_values"][0]["placement_acceptance_blockers"]},
            )

    def test_placement_only_candidate_is_reported_without_full_fen_acceptance(self) -> None:
        placement = VALID_KINGS_FEN.split()[0]
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
                                    "image_path": "assets/diagrams/p001_d001.png",
                                    "placement": placement,
                                    "full_fen": VALID_KINGS_FEN,
                                    "confidence": 0.99,
                                    "warnings": ["side_to_move_inferred"],
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

            payload = build_auto_chess_flow_artifacts(out)

            self.assertEqual(payload["status"], "MANUAL_REVIEW_AVAILABLE")
            fen_payload = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            item = fen_payload["items"][0]
            candidate = item["candidate_values"][0]
            self.assertEqual(item["status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
            self.assertEqual(item["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
            self.assertIsNone(item["selected_value"])
            self.assertEqual(item["selected_placement"], placement)
            self.assertEqual(item["next_action"], "resolve_full_fen_metadata_or_human_verify")
            self.assertEqual(candidate["placement_runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
            self.assertEqual(candidate["normalized_placement"], placement)

            quality = json.loads((out / "report" / "quality_report.json").read_text(encoding="utf-8"))
            summary = quality["summary"]
            self.assertEqual(summary["fen_placement_machine_accepted"], 1)
            self.assertEqual(summary["fen_full_machine_accepted"], 0)
            self.assertEqual(summary["automatic_placement_success_rate"], 1.0)

    def test_solution_pgn_does_not_consume_placement_only_source_fen(self) -> None:
        placement = VALID_KINGS_FEN.split()[0]
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
                                    "placement": placement,
                                    "full_fen": VALID_KINGS_FEN,
                                    "confidence": 0.99,
                                    "warnings": ["side_to_move_inferred"],
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

            fen_payload = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            self.assertEqual(fen_payload["items"][0]["status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
            pgn_payload = json.loads((out / "pgn" / "pgn_candidates.json").read_text(encoding="utf-8"))
            item = pgn_payload["items"][0]
            self.assertIsNone(item["selected_value"])
            self.assertIn("source_fen_not_machine_accepted", {error["code"] for error in item["validation_errors"]})

    def test_deterministic_ensemble_candidate_can_be_machine_accepted_but_local_model_stays_blocked(self) -> None:
        model_prediction = {
            "diagram_id": "p001_d001",
            "fen_candidate": VALID_KINGS_FEN,
            "global_confidence": 0.97,
            "deterministic_validation": {"valid": True, "warnings": []},
            "squares": _squares_from_fen(VALID_KINGS_FEN, confidence=0.97),
        }

        ensemble = build_deterministic_ensemble_fen(
            {"diagram_id": "p001_d001", "image_path": "assets/diagrams/p001_d001.png"},
            model_prediction,
            None,
            {"source_crop_hash": "abc123"},
        )

        self.assertEqual(ensemble["source"], "deterministic_ensemble")
        self.assertEqual(ensemble["fen"], VALID_KINGS_FEN)
        self.assertGreaterEqual(ensemble["confidence"], 0.97)

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
                                    "image_path": "assets/diagrams/p001_d001.png",
                                    "side_to_move": "w",
                                    "side_to_move_status": "explicit",
                                    "side_to_move_evidence": "marker",
                                    "side_marker_symbol": "\u25b3",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_source": "marker",
                                    "side_marker_confidence": 0.94,
                                    "board_crop_quality": "pass",
                                    "board_crop_quality_gate": {"decision": "pass", "reasons": []},
                                    "marker_crop_quality": "pass",
                                    "marker_bbox": [10.0, 20.0, 30.0, 40.0],
                                    "selected_marker_zone": "right",
                                    "marker_crop_quality_gate": {"decision": "pass", "component_count": 1, "reasons": []},
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
            (out / "review" / "fen_model_predictions.jsonl").write_text(json.dumps(model_prediction) + "\n", encoding="utf-8")
            (out / "review" / "fen_beam_candidates.jsonl").write_text(json.dumps(ensemble) + "\n", encoding="utf-8")

            build_auto_chess_flow_artifacts(out)

            fen_payload = json.loads((out / "fen" / "fen_candidates.json").read_text(encoding="utf-8"))
            item = fen_payload["items"][0]
            sources = {candidate["source"]: candidate["runtime_status"] for candidate in item["candidate_values"]}
            self.assertEqual(sources["local_model_candidate"], "FEN_REVIEW_REQUIRED")
            self.assertEqual(sources["deterministic_ensemble"], "FEN_MACHINE_ACCEPTED")
            self.assertEqual(item["selected_value"], VALID_KINGS_FEN)

    def test_apply_runtime_accepted_fen_updates_book_and_diagram_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            diagram = {
                "id": "p001_d001",
                "page": 1,
                "image_path": "assets/diagrams/p001_d001.png",
                "fen": "",
                "fen_candidate": "",
                "validation_status": "needs-human-review",
                "review_reason": "missing",
            }
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [{"page": 1, "diagrams": [dict(diagram)], "pgn_records": [], "text_blocks": []}],
                    "pgn_records": [],
                },
            )
            _write_json(out / "data" / "diagrams.json", {"schema": "test", "diagrams": [dict(diagram)]})
            _write_json(
                out / "fen" / "fen_candidates.json",
                {
                    "items": [
                        {
                            "id": "p001_d001",
                            "status": "FEN_MACHINE_ACCEPTED",
                            "runtime_status": "FEN_MACHINE_ACCEPTED",
                            "selected_value": VALID_KINGS_FEN,
                            "acceptance_trace": {"source": "deterministic_ensemble"},
                        }
                    ]
                },
            )

            result = apply_runtime_accepted_fen(out)

            self.assertEqual(result["applied_count"], 1)
            book = json.loads((out / "data" / "book.json").read_text(encoding="utf-8"))
            diagrams = json.loads((out / "data" / "diagrams.json").read_text(encoding="utf-8"))
            self.assertEqual(book["pages"][0]["diagrams"][0]["fen"], VALID_KINGS_FEN)
            self.assertEqual(book["pages"][0]["diagrams"][0]["validation_status"], "accepted")
            self.assertEqual(diagrams["diagrams"][0]["runtime_status"], "FEN_MACHINE_ACCEPTED")
            self.assertTrue((out / "reports" / "fen_apply_runtime_acceptance.json").is_file())

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
            categories = set(report["summary"]["by_category"])
            self.assertIn("fen_not_recognized", codes)
            self.assertIn("unmapped_chess_glyphs", codes)
            self.assertIn("unknown", categories)
            self.assertIn("pgn", categories)
            self.assertTrue(all("category" in blocker for item in report["items"] for blocker in item["blockers"]))
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

    def test_solution_line_from_accepted_fen_is_replayed_and_exported(self) -> None:
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
                                    "id": "diagram-1",
                                    "diagram_id": "diagram-1",
                                    "label": "Diagram 1-1",
                                    "fen": VALID_KINGS_FEN,
                                    "validation_status": "accepted",
                                    "runtime_status": "FEN_MACHINE_ACCEPTED",
                                }
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [
                        {
                            "record_id": "solution_1",
                            "label": "Diagram 1-1",
                            "raw_text": "Diagram 1-1 1. Kd2",
                            "visible_review_text": "1. Kd2",
                            "warnings": [],
                        }
                    ],
                },
            )

            repair = repair_and_accept_pgn_records(out)
            apply = apply_runtime_accepted_pgn(out)

            self.assertEqual(repair["accepted_count"], 1)
            self.assertEqual(apply["applied_count"], 1)
            games = (out / "data" / "games.pgn").read_text(encoding="utf-8")
            self.assertIn('[SetUp "1"]', games)
            self.assertIn(f'[FEN "{VALID_KINGS_FEN}"]', games)
            self.assertIn("1. Kd2", games)
            updated = json.loads((out / "data" / "book.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["pgn_records"][0]["runtime_status"], "SOLUTION_LINE_ACCEPTED")

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
            argv = [
                "kindlemaster.py",
                "process",
                "study.pdf",
                "--out",
                temp_dir,
                "--mode",
                "auto",
                "--debug-artifacts",
                "blockers",
            ]
            with patch.object(sys, "argv", argv):
                with patch("chess_auto_flow.run_auto_chess_process", return_value=fake_payload) as run_mock:
                    with patch.object(kindlemaster, "_print_json") as print_json:
                        exit_code = kindlemaster.main()

            self.assertEqual(exit_code, 0)
            run_mock.assert_called_once()
            self.assertEqual(run_mock.call_args.kwargs["debug_artifact_policy"], "blockers")
            self.assertEqual(print_json.call_args.args[0]["status"], "MANUAL_REVIEW_AVAILABLE")

    def test_run_auto_chess_process_executes_full_backend_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)

            def fake_export(*_args, **_kwargs):
                _write_json(
                    out / "data" / "book.json",
                    {"pages": [{"page": 1, "diagrams": [], "pgn_records": [], "text_blocks": []}], "pgn_records": []},
                )
                return {"status": "ok", "pages": 1}

            patches = [
                patch("chess_study_export.run_chess_study_export", side_effect=fake_export),
                patch("chess_study_export.preprocess_chess_board_crops", return_value={"status": "ok", "normalized_count": 0}),
                patch("chess_study_export.recognize_fen_local", return_value={"status": "ok", "prediction_count": 0}),
                patch("chess_study_export.evaluate_fen_ensemble", return_value={"status": "needs_review", "accepted_candidate_count": 0}),
                patch("chess_fen_ml_acceptance.build_runtime_template_candidates", return_value={"status": "needs_review", "template_candidate_count": 0}),
                patch("chess_fen_ml_acceptance.build_fen_beam_candidates", return_value={"status": "needs_review", "candidate_count": 0}),
                patch("chess_auto_flow.apply_runtime_accepted_fen", return_value={"status": "ok", "applied_count": 0}),
                patch("chess_study_export.build_chess_pgn_review", return_value={"status": "ok", "pgn_total": 0}),
                patch("chess_pgn_auto_repair.repair_and_accept_pgn_records", return_value={"status": "ok", "accepted_count": 0}),
                patch("chess_auto_flow.apply_runtime_accepted_pgn", return_value={"status": "ok", "applied_count": 0}),
                patch("chess_study_export.render_semantic_source_reader", return_value={"status": "ok"}),
                patch("chess_study_export.build_chess_quality_dashboard", return_value={"status": "ok"}),
            ]
            stack = contextlib.ExitStack()
            with stack:
                for item in patches:
                    stack.enter_context(item)
                payload = run_auto_chess_process("study.pdf", out_dir=out, mode="auto")

            stage_names = [stage["name"] for stage in payload["stage_results"]]
            self.assertIn("preprocess_chess_board_crops", stage_names)
            self.assertIn("generate_fen_template_candidates", stage_names)
            self.assertIn("generate_fen_beam_candidates", stage_names)
            self.assertIn("apply_runtime_accepted_fen", stage_names)
            self.assertIn("repair_and_accept_pgn_records", stage_names)
            self.assertIn("validate_auto_chess_output", stage_names)
            for stage in payload["stage_results"]:
                self.assertIn("elapsed_seconds", stage)


if __name__ == "__main__":
    unittest.main()
