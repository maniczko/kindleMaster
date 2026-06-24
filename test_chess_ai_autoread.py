import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from openai_chess_fen_reviewer import POLICY_ACKNOWLEDGEMENT
from scripts.build_chess_ai_autoread_requests import build_chess_ai_autoread_requests
from scripts.export_chess_ai_unreadable_enhanced_crops import export_chess_ai_unreadable_enhanced_crops
from scripts.export_chess_ai_tiebreak_fen_evidence import export_chess_ai_tiebreak_fen_evidence
from scripts.import_chess_ai_autoread_responses import import_chess_ai_autoread_responses
from scripts.analyze_chess_ai_fen_recovery_plan import analyze_chess_ai_fen_recovery_plan
from scripts.run_chess_ai_autoread_requests import run_chess_ai_autoread_requests


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63600000020001e221bc3300000000"
    "49454e44ae426082"
)
VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
ALT_FEN = "4k3/8/8/8/8/8/8/3K4 w - - 0 1"


class ChessAiAutoreadTests(unittest.TestCase):
    def test_builds_pending_artifacts_without_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("EPUB/images/diagram.png", PNG_1X1)
            report_path = root / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "output_path": str(epub_path),
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    {
                                        "page": 7,
                                        "filename": "diagram.png",
                                        "placement": "4k3/8/8/8/8/8/8/4K3",
                                        "full_fen": VALID_FEN,
                                        "warnings": ["side_to_move_inferred"],
                                    }
                                ]
                            },
                            "chess_pgn": {
                                "records": [
                                    {
                                        "id": "pgn-1",
                                        "raw_text": "1. Ke2 *",
                                        "warnings": ["requires_review"],
                                    }
                                ]
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            out_dir = root / "ai"

            summary = build_chess_ai_autoread_requests(report_path, output_dir=out_dir)

            self.assertEqual(summary["fen_total"], 1)
            self.assertEqual(summary["pgn_total"], 1)
            self.assertEqual(summary["fen_request_count"], 2)
            self.assertEqual(summary["pgn_request_count"], 2)
            fen_rows = _read_jsonl(out_dir / "ai_fen_readout.jsonl")
            pgn_rows = _read_jsonl(out_dir / "ai_pgn_readout.jsonl")
            for row in [*fen_rows, *pgn_rows]:
                self.assertEqual(row["source"], "ai_autoread")
                self.assertFalse(row["release_safe"])
                self.assertFalse(row["human_verified"])
                self.assertFalse(row["accepted_for_corpus"])
                self.assertNotIn("fen", row)

    def test_import_fen_consensus_ignores_authority_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_pending_dir(Path(tmp))
            responses = Path(tmp) / "responses.jsonl"
            _write_jsonl(
                responses,
                [
                    _fen_response("fen::fen-1::direct_read", VALID_FEN, human_verified=True),
                    _fen_response("fen::fen-1::skeptical_verify", VALID_FEN),
                ],
            )

            summary = import_chess_ai_autoread_responses(source, responses)

            self.assertEqual(summary["fen_status_counts"], {"ai_consensus": 1})
            row = _read_jsonl(source / "ai_fen_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "ai_consensus")
            self.assertEqual(row["ai_fen"], VALID_FEN)
            self.assertFalse(row["human_verified"])
            self.assertFalse(row["accepted_for_corpus"])
            self.assertIn("ai_authoritative_field_ignored", row["ai_policy_issues"])

    def test_import_conflicting_fen_responses_stays_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_pending_dir(Path(tmp))
            responses = Path(tmp) / "responses.jsonl"
            _write_jsonl(
                responses,
                [
                    _fen_response("fen::fen-1::direct_read", VALID_FEN),
                    _fen_response("fen::fen-1::skeptical_verify", ALT_FEN),
                ],
            )

            import_chess_ai_autoread_responses(source, responses)

            row = _read_jsonl(source / "ai_fen_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "ai_readout_conflict")
            self.assertFalse(row["release_safe"])

    def test_import_pgn_consensus_writes_ai_only_pgn(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_pending_dir(Path(tmp), include_pgn=True)
            responses = Path(tmp) / "responses.jsonl"
            _write_jsonl(
                responses,
                [
                    _pgn_response("pgn::pgn-1::direct_read", "1. Ke2 *"),
                    _pgn_response("pgn::pgn-1::skeptical_verify", "1. Ke2 *"),
                ],
            )

            summary = import_chess_ai_autoread_responses(source, responses)

            self.assertEqual(summary["pgn_status_counts"], {"ai_consensus": 1})
            row = _read_jsonl(source / "ai_pgn_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "ai_consensus")
            self.assertEqual(row["ai_movetext"], "1. Ke2 *")
            self.assertFalse(row["release_safe"])
            self.assertFalse(row["accepted_for_corpus"])

    def test_strict_existing_fen_is_baseline_without_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("EPUB/images/diagram.png", PNG_1X1)
            report_path = root / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "output_path": str(epub_path),
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    {
                                        "page": 7,
                                        "filename": "diagram.png",
                                        "fen": VALID_FEN,
                                        "requires_review": False,
                                    }
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = build_chess_ai_autoread_requests(report_path, output_dir=root / "ai")

            self.assertEqual(summary["fen_total"], 1)
            self.assertEqual(summary["fen_request_count"], 0)
            row = _read_jsonl(root / "ai" / "ai_fen_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "strict_existing")
            self.assertEqual(row["ai_fen"], VALID_FEN)
            self.assertTrue(row["ai_consensus"])
            self.assertNotIn("fen", row)

    def test_import_preserves_strict_existing_without_responses(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "ai"
            source.mkdir()
            _write_jsonl(
                source / "ai_fen_readout.jsonl",
                [
                    {
                        "id": "fen-1",
                        "kind": "fen",
                        "source": "ai_autoread",
                        "release_safe": False,
                        "human_verified": False,
                        "accepted_for_corpus": False,
                        "ai_readout_status": "strict_existing",
                        "ai_consensus": True,
                        "ai_fen": VALID_FEN,
                        "ai_placement": VALID_FEN.split()[0],
                        "ai_side_to_move": "w",
                        "ai_confidence": 1.0,
                    }
                ],
            )
            _write_jsonl(source / "ai_pgn_readout.jsonl", [])
            responses = Path(tmp) / "responses.jsonl"
            _write_jsonl(responses, [])

            summary = import_chess_ai_autoread_responses(source, responses)

            self.assertEqual(summary["fen_status_counts"], {"strict_existing": 1})
            row = _read_jsonl(source / "ai_fen_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "strict_existing")
            self.assertEqual(row["ai_fen"], VALID_FEN)
            self.assertFalse(row["release_safe"])

    def test_import_conflicting_fen_high_margin_becomes_best_effort(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_pending_dir(Path(tmp))
            responses = Path(tmp) / "responses.jsonl"
            _write_jsonl(
                responses,
                [
                    _fen_response("fen::fen-1::direct_read", VALID_FEN, confidence=0.96),
                    _fen_response("fen::fen-1::skeptical_verify", ALT_FEN, confidence=0.70),
                ],
            )

            import_chess_ai_autoread_responses(source, responses)

            row = _read_jsonl(source / "ai_fen_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "ai_best_effort")
            self.assertEqual(row["ai_fen"], VALID_FEN)
            self.assertFalse(row["ai_consensus"])
            self.assertFalse(row["release_safe"])

    def test_pgn_consensus_marks_replay_legal_from_start_fen(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _write_pending_dir(Path(tmp), include_pgn=True)
            pgn_rows = _read_jsonl(source / "ai_pgn_readout.jsonl")
            pgn_rows[0]["deterministic_fen"] = VALID_FEN
            _write_jsonl(source / "ai_pgn_readout.jsonl", pgn_rows)
            responses = Path(tmp) / "responses.jsonl"
            _write_jsonl(
                responses,
                [
                    _pgn_response("pgn::pgn-1::direct_read", "1. Ke2 *"),
                    _pgn_response("pgn::pgn-1::skeptical_verify", "1. Ke2 *"),
                ],
            )

            import_chess_ai_autoread_responses(source, responses)

            row = _read_jsonl(source / "ai_pgn_readout.jsonl")[0]
            self.assertEqual(row["ai_readout_status"], "ai_consensus")
            self.assertTrue(row["ai_pgn_replay_legal"])
            self.assertFalse(row["release_safe"])

    def test_live_runner_without_api_key_is_disabled_not_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests = root / "requests.jsonl"
            responses = root / "responses.jsonl"
            _write_jsonl(requests, [{"custom_id": "fen::fen-1::direct_read", "body": {}}])

            with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
                summary = run_chess_ai_autoread_requests(requests, output_jsonl=responses, cwd=root)

            self.assertEqual(summary["status"], "disabled")
            self.assertEqual(summary["reason"], "openai_api_key_missing")
            self.assertFalse(summary["openai_status"]["release_safe"])

    def test_recovery_plan_separates_marker_rule_and_tie_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_jsonl(
                root / "ai_fen_readout.jsonl",
                [
                    {
                        "id": "fen-marker",
                        "kind": "fen",
                        "source": "ai_autoread",
                        "release_safe": False,
                        "human_verified": False,
                        "accepted_for_corpus": False,
                        "ai_readout_status": "ai_consensus",
                        "ai_fen": VALID_FEN,
                        "ai_side_to_move": "w",
                        "deterministic_placement": VALID_FEN.split()[0],
                        "side_marker_candidates": [
                            {"role": "bottom_left", "side_candidate": "w", "detected_shape": "outline_triangle", "score": 10}
                        ],
                    },
                    {
                        "id": "fen-conflict",
                        "kind": "fen",
                        "source": "ai_autoread",
                        "release_safe": False,
                        "human_verified": False,
                        "accepted_for_corpus": False,
                        "ai_readout_status": "ai_readout_conflict",
                    },
                ],
            )
            _write_jsonl(
                root / "ai_autoread_requests.jsonl",
                [
                    {
                        "custom_id": "fen::fen-conflict::direct_read",
                        "method": "POST",
                        "url": "/v1/responses",
                        "body": {"model": "unit", "input": [{"content": [{"text": "{}"}]}]},
                    }
                ],
            )
            _write_jsonl(
                root / "ai_autoread_responses.jsonl",
                [
                    _fen_response("fen::fen-conflict::direct_read", VALID_FEN, confidence=0.8),
                    _fen_response("fen::fen-conflict::skeptical_verify", ALT_FEN, confidence=0.8),
                ],
            )

            summary = analyze_chess_ai_fen_recovery_plan(root)

            self.assertEqual(summary["candidate_deterministic_marker_rule_count"], 1)
            self.assertEqual(summary["tie_break_request_count"], 1)
            cases = _read_jsonl(root / "strict_fen_recovery_cases.jsonl")
            recommendations = {row["id"]: row["recommendation"] for row in cases}
            self.assertEqual(recommendations["fen-marker"], "candidate_deterministic_marker_rule")
            self.assertEqual(recommendations["fen-conflict"], "run_tie_break_high_reasoning")

    def test_tie_break_evidence_stays_non_release_and_non_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_jsonl(
                root / "strict_fen_recovery_cases.jsonl",
                [
                    {
                        "id": "fen-tiebreak",
                        "page": 25,
                        "filename": "scan_chess_p025_03.png",
                        "ai_fen": VALID_FEN,
                        "ai_side_to_move": "w",
                        "deterministic_placement": VALID_FEN.split()[0],
                        "placement_matches_deterministic": True,
                        "marker_roles": ["top_right", "top_left"],
                        "marker_sides": ["b", "w"],
                        "marker_conflict": True,
                        "response_fens": [VALID_FEN, ALT_FEN],
                        "release_safe": True,
                        "accepted_for_corpus": True,
                        "fen": VALID_FEN,
                        "recommendation": "tie_break_evidence_needs_rule_or_exact_label",
                    },
                    {
                        "id": "fen-rule",
                        "recommendation": "candidate_deterministic_marker_rule",
                    },
                ],
            )

            summary = export_chess_ai_tiebreak_fen_evidence(root)

            self.assertEqual(summary["evidence_count"], 1)
            row = _read_jsonl(root / "ai_tiebreak_evidence.jsonl")[0]
            self.assertEqual(row["id"], "fen-tiebreak")
            self.assertFalse(row["release_safe"])
            self.assertFalse(row["accepted_for_corpus"])
            self.assertNotIn("fen", row)
            self.assertEqual(row["recommendation"], "tie_break_evidence_needs_rule_or_exact_label")
            html = (root / "ai_tiebreak_review.html").read_text(encoding="utf-8")
            self.assertIn("AI evidence only, not strict/corpus authority", html)

    def test_unreadable_enhanced_crop_manifest_is_ai_only_and_missing_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("EPUB/images/other.png", PNG_1X1)
            report_path = root / "report.json"
            report_path.write_text(json.dumps({"output_path": str(epub_path)}), encoding="utf-8")
            cases_path = root / "strict_fen_recovery_cases.jsonl"
            _write_jsonl(
                cases_path,
                [
                    {
                        "id": "fen-missing",
                        "page": 12,
                        "filename": "missing.png",
                        "ai_readout_status": "ai_readout_unreadable",
                        "recommendation": "run_enhanced_vision_retry",
                        "release_safe": False,
                    }
                ],
            )

            summary = export_chess_ai_unreadable_enhanced_crops(cases_path, report_path, output_dir=root / "enhanced")

            self.assertEqual(summary["candidate_count"], 1)
            self.assertEqual(summary["missing_count"], 1)
            rows = _read_jsonl(root / "enhanced" / "ai_unreadable_enhanced_manifest.jsonl")
            self.assertEqual(rows[0]["status"], "crop_missing")
            self.assertEqual(rows[0]["original_status"], "ai_readout_unreadable")
            self.assertFalse(rows[0]["release_safe"])
            self.assertNotIn("fen", rows[0])


def _write_pending_dir(path: Path, *, include_pgn: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _write_jsonl(
        path / "ai_fen_readout.jsonl",
        [
            {
                "id": "fen-1",
                "kind": "fen",
                "source": "ai_autoread",
                "release_safe": False,
                "human_verified": False,
                "accepted_for_corpus": False,
                "ai_readout_status": "ai_review_pending",
                "ai_fen": "",
            }
        ],
    )
    _write_jsonl(
        path / "ai_pgn_readout.jsonl",
        [
            {
                "id": "pgn-1",
                "kind": "pgn",
                "source": "ai_autoread",
                "release_safe": False,
                "human_verified": False,
                "accepted_for_corpus": False,
                "ai_readout_status": "ai_review_pending",
                "ai_pgn": "",
                "ai_movetext": "",
            }
        ]
        if include_pgn
        else [],
    )
    return path


def _fen_response(custom_id: str, fen: str, **extra):
    payload = {
        "id": "fen-1",
        "readout_status": "ai_readout_complete",
        "ai_fen": fen,
        "placement": fen.split()[0],
        "side_to_move": fen.split()[1],
        "confidence": 0.91,
        "reason": "test",
        "policy_acknowledgement": POLICY_ACKNOWLEDGEMENT,
    }
    payload.update(extra)
    return {"custom_id": custom_id, **payload}


def _pgn_response(custom_id: str, movetext: str):
    return {
        "custom_id": custom_id,
        "id": "pgn-1",
        "readout_status": "ai_readout_complete",
        "pgn_feasibility": "solution_line",
        "ai_movetext": movetext,
        "ai_pgn": '[Event "AI Autoread"]\n\n' + movetext,
        "confidence": 0.86,
        "reason": "test",
        "policy_acknowledgement": POLICY_ACKNOWLEDGEMENT,
    }


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
