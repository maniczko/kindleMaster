from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_fen_hardening import (
    crop_sha256,
    evaluate_diagram_acceptance,
    render_square_diff_text,
    square_level_fen_diff,
    validate_fen_detailed,
)
from openai_chess_fen_reviewer import OpenAIChessFenReviewer, _review_schema
from scripts.audit_chess_fen_false_positives import audit_chess_fen_false_positives
from scripts.promote_chess_fen_label_draft import promote_chess_fen_label_draft
from scripts.validate_chess_fen_labels import validate_chess_fen_labels


class ChessFenPipelineHardeningTests(unittest.TestCase):
    STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    def test_detailed_fen_validation_accepts_and_normalizes_starting_position(self) -> None:
        result = validate_fen_detailed(f"  {self.STARTING_FEN.replace(' ', '   ')}  ")

        self.assertTrue(result.is_syntax_valid)
        self.assertTrue(result.is_legal_position)
        self.assertEqual(result.normalized_fen, self.STARTING_FEN)
        self.assertEqual(result.errors, [])

    def test_detailed_fen_validation_rejects_core_syntax_and_domain_errors(self) -> None:
        cases = {
            "invalid_rank_count": "8/8/8/8/8/8/8 w - - 0 1",
            "invalid_rank_width": "9/8/8/8/8/8/8/4K2k w - - 0 1",
            "invalid_piece": "rnbqkbnx/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "missing_white_king": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR w KQkq - 0 1",
            "missing_black_king": "rnbq1bnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "too_many_white_kings": "rnbqkbnr/pppppppp/8/8/8/8/PPPPKPPP/RNBQKBNR w KQkq - 0 1",
            "pawn_on_back_rank": "p3k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "side_to_move_invalid": f"{self.STARTING_FEN.split()[0]} x KQkq - 0 1",
            "castling_invalid": f"{self.STARTING_FEN.split()[0]} w KX - 0 1",
            "castling_order_invalid": f"{self.STARTING_FEN.split()[0]} w QKkq - 0 1",
            "en_passant_invalid": f"{self.STARTING_FEN.split()[0]} w KQkq e4 0 1",
            "move_counters_invalid": f"{self.STARTING_FEN.split()[0]} w KQkq - -1 1",
            "fullmove_number_invalid": f"{self.STARTING_FEN.split()[0]} w KQkq - 0 0",
        }

        for expected_code, fen in cases.items():
            with self.subTest(expected_code=expected_code):
                result = validate_fen_detailed(fen)
                codes = {issue.code for issue in result.errors}
                self.assertIn(expected_code, codes)
                self.assertIsNone(result.normalized_fen)

    def test_diagram_acceptance_auto_verifies_only_valid_high_confidence_fen(self) -> None:
        result = evaluate_diagram_acceptance(
            {
                "id": "diagram-1",
                "raw_fen": self.STARTING_FEN,
                "confidence": {"mean": 0.99, "min_occupied": 0.95, "orientation": 0.99},
                "context_match": True,
            }
        )

        self.assertEqual(result["status"], "auto_verified")
        self.assertEqual(result["normalized_fen"], self.STARTING_FEN)
        self.assertEqual(result["reasons"], ["all_auto_verify_gates_passed"])

    def test_diagram_acceptance_routes_low_confidence_or_context_mismatch_to_review(self) -> None:
        result = evaluate_diagram_acceptance(
            {
                "id": "diagram-1",
                "raw_fen": self.STARTING_FEN,
                "confidence": {"mean": 0.96, "min_occupied": 0.89, "orientation": 0.97},
                "context_match": False,
            }
        )

        self.assertEqual(result["status"], "manual_review_required")
        self.assertEqual(
            set(result["reasons"]),
            {
                "mean_confidence_below_auto_threshold",
                "occupied_square_confidence_below_auto_threshold",
                "orientation_confidence_below_auto_threshold",
                "context_mismatch",
            },
        )
        self.assertEqual(result["normalized_fen"], self.STARTING_FEN)

    def test_diagram_acceptance_rejects_invalid_fen_and_very_low_confidence(self) -> None:
        invalid = evaluate_diagram_acceptance(
            {
                "id": "diagram-1",
                "raw_fen": "8/8/8/8/8/8/8 w - - 0 1",
                "confidence": {"mean": 0.99, "min_occupied": 0.95, "orientation": 0.99},
            }
        )
        low_confidence = evaluate_diagram_acceptance(
            {
                "id": "diagram-2",
                "raw_fen": self.STARTING_FEN,
                "confidence": {"mean": 0.74, "min_occupied": 0.95, "orientation": 0.99},
            }
        )

        self.assertEqual(invalid["status"], "rejected")
        self.assertIn("invalid_fen", invalid["reasons"])
        self.assertEqual(low_confidence["status"], "rejected")
        self.assertIn("mean_confidence_below_reject_threshold", low_confidence["reasons"])

    def test_square_level_diff_reports_known_bad_e5_rook_vs_pawn(self) -> None:
        expected = "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"
        candidate = "rnbq1rk1/pppp1ppp/2n2n2/1B2p3/3P1N2/2N2Q2/PPP1PPPP/R1B1K2R w KQ - 0 1"

        diffs = square_level_fen_diff(expected, candidate)
        e5 = [diff for diff in diffs if diff["square"] == "e5"]

        self.assertEqual(e5[0]["expected_piece"], "r")
        self.assertEqual(e5[0]["actual_piece"], "p")
        self.assertEqual(e5[0]["manual_piece"], "black rook")
        self.assertEqual(e5[0]["candidate_piece"], "black pawn")
        self.assertEqual(e5[0]["severity"], "critical")
        self.assertEqual(render_square_diff_text("p010_d002", e5), ["p010_d002: e5 black rook, not black pawn"])

    def test_label_validation_requires_new_human_provenance_hash_and_square_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "new_label",
                        "crop_path": str(crop),
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "verified_by": "human",
                        "verified_at": "2026-06-14",
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_fen_labels(labels)

        codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("verification_source_missing", codes)
        self.assertIn("human_verified_missing", codes)
        self.assertIn("square_diff_ack_missing", codes)
        self.assertIn("crop_sha256_missing", codes)

    def test_label_validation_accepts_hardened_human_visual_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "new_label",
                        "crop_path": str(crop),
                        "crop_sha256": crop_sha256(crop),
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "verified_by": "human",
                        "verified_at": "2026-06-14",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_fen_labels(labels)

        self.assertEqual(result["status"], "passed")

    def test_label_validation_rejects_valid_fen_without_verified_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "high_confidence_review_only",
                        "crop_path": str(crop),
                        "crop_sha256": crop_sha256(crop),
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "candidate_confidence": 0.96,
                        "verified_by": "human",
                        "verified_at": "2026-06-14",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
                        "label_status": "needs_manual_fen",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_fen_labels(labels)

        codes = {issue["code"] for issue in result["issues"]}
        self.assertEqual(result["status"], "failed")
        self.assertIn("label_status_not_verified", codes)
        self.assertIn("review_only_label_status", codes)

    def test_label_validation_rejects_missing_label_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "missing_status",
                        "crop_path": str(crop),
                        "crop_sha256": crop_sha256(crop),
                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "verified_by": "human",
                        "verified_at": "2026-06-14",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = validate_chess_fen_labels(labels)

        self.assertEqual(result["status"], "failed")
        self.assertIn("label_status_missing", {issue["code"] for issue in result["issues"]})

    def test_known_bad_p010_d002_candidate_is_blocked_by_validator_and_audit(self) -> None:
        candidate = "rnbq1rk1/pppp1ppp/2n2n2/1B2p3/3P1N2/2N2Q2/PPP1PPPP/R1B1K2R w KQ - 0 1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            labels = root / "labels.jsonl"
            labels.write_text(
                json.dumps(
                    {
                        "id": "p010_d002",
                        "crop_path": str(crop),
                        "crop_sha256": crop_sha256(crop),
                        "fen": candidate,
                        "verified_by": "human",
                        "verified_at": "2026-06-14",
                        "verification_source": "human_visual",
                        "human_verified": True,
                        "square_diff_ack": True,
                        "label_status": "verified",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            validation = validate_chess_fen_labels(labels)
            audit = audit_chess_fen_false_positives([labels])

        validation_codes = {issue["code"] for issue in validation["issues"]}
        audit_codes = {finding["code"] for finding in audit["findings"]}
        self.assertIn("known_bad_square_mismatch", validation_codes)
        self.assertIn("known_bad_square_mismatch", audit_codes)

    def test_promote_draft_never_copies_ai_suggestion_to_verified_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.jsonl"
            output = root / "verified.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "ai_only",
                        "human_verified": True,
                        "square_diff_ack": True,
                        "ai_suggested_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "crop_path": "",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = promote_chess_fen_label_draft(
                draft,
                output_path=output,
                verified_by="human",
                verified_at="2026-06-14",
                accept_ai_suggestions=True,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["promoted_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "manual_fen_missing_ai_suggestion_ignored")

    def test_promote_draft_rejects_manual_fen_with_unresolved_ai_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"fake-crop")
            draft = root / "draft.jsonl"
            output = root / "verified.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "ai_unresolved",
                        "crop_path": str(crop),
                        "manual_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "human_verified": True,
                        "square_diff_ack": True,
                        "ai_requires_review": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = promote_chess_fen_label_draft(
                draft,
                output_path=output,
                verified_by="human",
                verified_at="2026-06-14",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["promoted_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "ai_review_unresolved")

    def test_promote_draft_rejects_manual_fen_without_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.jsonl"
            output = root / "verified.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "missing_crop",
                        "manual_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        "human_verified": True,
                        "square_diff_ack": True,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = promote_chess_fen_label_draft(
                draft,
                output_path=output,
                verified_by="human",
                verified_at="2026-06-14",
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["promoted_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "crop_path_missing")

    def test_openai_review_schema_has_no_authoritative_verified_or_accepted_fields(self) -> None:
        schema = _review_schema()
        properties = set(schema["properties"])

        self.assertNotIn("verified", properties)
        self.assertNotIn("accepted", properties)
        self.assertNotIn("accepted_for_corpus", properties)

    def test_openai_review_output_wraps_approved_as_review_opinion_only(self) -> None:
        response = {
            "output_text": json.dumps(
                {
                    "approved": True,
                    "corrected_fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                    "requires_review": False,
                    "ambiguous_squares": [],
                    "issues": [],
                    "confidence": 0.99,
                    "notes": "Looks consistent.",
                }
            )
        }

        reviewer = OpenAIChessFenReviewer(api_key="test", transport=lambda *_args: response)
        review = reviewer.review_chess_fen({"candidate": {"fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1"}})

        self.assertTrue(review["approved"])
        self.assertEqual(review["review_opinion"], "supports_candidate")
        self.assertFalse(review["mutates_fen"])
        self.assertFalse(review["changed_output"])


if __name__ == "__main__":
    unittest.main()
