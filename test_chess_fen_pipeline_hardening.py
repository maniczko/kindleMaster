from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_fen_hardening import (
    crop_sha256,
    evaluate_diagram_acceptance,
    machine_accept_fen,
    machine_accept_placement,
    placement_from_fen_or_placement,
    placement_to_default_fen,
    render_square_diff_text,
    square_level_fen_diff,
    validate_fen_detailed,
    validate_placement_detailed,
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

    def test_placement_validation_accepts_placement_only_and_full_fen_input(self) -> None:
        placement = self.STARTING_FEN.split()[0]

        placement_only = validate_placement_detailed(placement)
        from_full_fen = validate_placement_detailed(self.STARTING_FEN)

        self.assertTrue(placement_only.is_structure_valid)
        self.assertTrue(placement_only.is_plausible_position)
        self.assertEqual(placement_only.normalized_placement, placement)
        self.assertEqual(from_full_fen.normalized_placement, placement)
        self.assertEqual(placement_from_fen_or_placement(self.STARTING_FEN), placement)

    def test_placement_validation_rejects_structural_errors(self) -> None:
        cases = {
            "invalid_rank_count": "8/8/8/8/8/8/8",
            "invalid_rank_width": "9/8/8/8/8/8/8/4K2k",
            "invalid_rank_digit": "44/8/8/8/8/8/8/4K2k",
            "invalid_piece": "8/8/8/8/8/8/8/4K2x",
        }

        for expected_code, placement in cases.items():
            with self.subTest(expected_code=expected_code):
                result = validate_placement_detailed(placement)
                self.assertFalse(result.is_structure_valid)
                self.assertIn(expected_code, {issue.code for issue in result.errors})

    def test_placement_validation_handles_plausibility_options(self) -> None:
        no_kings = "8/8/8/8/8/8/8/8"

        strict = validate_placement_detailed(no_kings)
        structural_only = validate_placement_detailed(no_kings, require_kings=False)

        self.assertTrue(strict.is_structure_valid)
        self.assertFalse(strict.is_plausible_position)
        self.assertIn("missing_white_king", {issue.code for issue in strict.errors})
        self.assertTrue(structural_only.is_structure_valid)
        self.assertTrue(structural_only.is_plausible_position)

    def test_placement_to_default_fen_validates_and_adds_safe_metadata(self) -> None:
        placement = self.STARTING_FEN.split()[0]

        self.assertEqual(placement_to_default_fen(placement), f"{placement} w - - 0 1")
        self.assertEqual(placement_to_default_fen(placement, side_to_move="b"), f"{placement} b - - 0 1")

    def test_full_fen_validation_still_requires_six_fields(self) -> None:
        placement = self.STARTING_FEN.split()[0]

        result = validate_fen_detailed(placement)

        self.assertIn("fen_must_have_six_fields", {issue.code for issue in result.errors})
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

    def test_machine_accept_fen_accepts_only_deterministic_valid_high_confidence_candidate(self) -> None:
        result = machine_accept_fen(
            {
                "source": "deterministic",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": [],
                "side_to_move": "w",
                "side_to_move_evidence": "marker_crop",
                "side_marker_status": "trusted_marker",
            },
            {"min_confidence": 0.90},
        )

        self.assertEqual(result["runtime_status"], "FEN_MACHINE_ACCEPTED")
        self.assertEqual(result["selected_value"], self.STARTING_FEN)

    def test_machine_accept_placement_accepts_deterministic_placement_without_side_evidence(self) -> None:
        placement = self.STARTING_FEN.split()[0]

        result = machine_accept_placement(
            {
                "source": "deterministic",
                "placement": placement,
                "confidence": 0.99,
                "warnings": ["side_to_move_inferred"],
            },
            {"min_confidence": 0.90},
        )

        self.assertEqual(result["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
        self.assertEqual(result["selected_placement"], placement)

    def test_machine_accept_placement_extracts_placement_from_full_fen_candidate(self) -> None:
        placement = self.STARTING_FEN.split()[0]

        result = machine_accept_placement(
            {
                "source": "image_template",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": ["side_to_move_inferred"],
            }
        )

        self.assertEqual(result["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
        self.assertEqual(result["selected_placement"], placement)

    def test_machine_accept_placement_blocks_ai_and_local_model_by_default(self) -> None:
        placement = self.STARTING_FEN.split()[0]
        ai = machine_accept_placement({"source": "ai_review_only", "placement": placement, "confidence": 0.99})
        local_model = machine_accept_placement({"source": "local_model_candidate", "placement": placement, "confidence": 0.99})

        self.assertEqual(ai["runtime_status"], "FEN_PLACEMENT_REVIEW_REQUIRED")
        self.assertIn("ai_review_only_source", {blocker["code"] for blocker in ai["acceptance_blockers"]})
        self.assertEqual(local_model["runtime_status"], "FEN_PLACEMENT_REVIEW_REQUIRED")
        self.assertIn("non_deterministic_source", {blocker["code"] for blocker in local_model["acceptance_blockers"]})

    def test_machine_accept_placement_requires_deterministic_ensemble_evidence(self) -> None:
        placement = self.STARTING_FEN.split()[0]
        missing_evidence = machine_accept_placement(
            {
                "source": "deterministic_ensemble",
                "placement": placement,
                "confidence": 0.99,
                "evidence": {"score_margin_to_second_candidate": 0.50},
            }
        )
        accepted = machine_accept_placement(
            {
                "source": "deterministic_ensemble",
                "placement": placement,
                "confidence": 0.99,
                "source_crop_hash": "sha256:abc123",
                "evidence": {
                    "score_margin_to_second_candidate": 0.50,
                    "local_model_candidate": True,
                    "square_alternatives_checked": True,
                },
            },
            {"min_score_margin": 0.05},
        )

        missing_codes = {blocker["code"] for blocker in missing_evidence["acceptance_blockers"]}
        self.assertIn("source_crop_hash_missing", missing_codes)
        self.assertIn("square_alternatives_not_checked", missing_codes)
        self.assertIn("no_template_or_model_agreement", missing_codes)
        self.assertEqual(accepted["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")

    def test_machine_accept_placement_rejects_invalid_missing_king_and_low_confidence(self) -> None:
        invalid = machine_accept_placement({"source": "deterministic", "placement": "8/8/8/8/8/8/8/8", "confidence": 0.99})
        low_confidence = machine_accept_placement(
            {"source": "deterministic", "placement": self.STARTING_FEN.split()[0], "confidence": 0.50},
            {"min_confidence": 0.90},
        )

        self.assertEqual(invalid["runtime_status"], "FEN_PLACEMENT_REVIEW_REQUIRED")
        self.assertIn("missing_white_king", {blocker["code"] for blocker in invalid["acceptance_blockers"]})
        self.assertEqual(low_confidence["runtime_status"], "FEN_PLACEMENT_REVIEW_REQUIRED")
        self.assertIn("confidence_below_runtime_threshold", {blocker["code"] for blocker in low_confidence["acceptance_blockers"]})

    def test_machine_accept_fen_requires_full_deterministic_ensemble_evidence(self) -> None:
        result = machine_accept_fen(
            {
                "source": "deterministic_ensemble",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": [],
                "evidence": {
                    "python_chess_valid": True,
                    "score_margin_to_second_candidate": 0.50,
                },
            },
            {"min_confidence": 0.90},
        )

        codes = {blocker["code"] for blocker in result["acceptance_blockers"]}
        self.assertEqual(result["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertIn("source_crop_hash_missing", codes)
        self.assertIn("square_alternatives_not_checked", codes)
        self.assertIn("no_template_or_model_agreement", codes)

    def test_machine_accept_fen_accepts_deterministic_ensemble_with_full_evidence_contract(self) -> None:
        result = machine_accept_fen(
            {
                "source": "deterministic_ensemble",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": [],
                "side_to_move": "w",
                "side_to_move_evidence": "marker_crop",
                "side_marker_status": "trusted_marker",
                "source_crop_hash": "sha256:abc123",
                "evidence": {
                    "python_chess_valid": True,
                    "validate_fen_detailed_passed": True,
                    "score_margin_to_second_candidate": 0.50,
                    "local_model_candidate": True,
                    "template_candidate": False,
                    "square_alternatives_checked": True,
                },
            },
            {"min_confidence": 0.90, "min_score_margin": 0.05},
        )

        self.assertEqual(result["runtime_status"], "FEN_MACHINE_ACCEPTED")
        self.assertEqual(result["selected_value"], self.STARTING_FEN)

    def test_machine_accept_fen_rejects_ai_approval_and_high_confidence_without_deterministic_source(self) -> None:
        result = machine_accept_fen(
            {
                "source": "ai_review_only",
                "fen": self.STARTING_FEN,
                "confidence": 0.99,
                "warnings": [],
                "ai_approved": True,
            },
            {"min_confidence": 0.90},
        )

        codes = {blocker["code"] for blocker in result["acceptance_blockers"]}
        self.assertEqual(result["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertIn("ai_review_only_source", codes)
        self.assertIn("non_deterministic_source", codes)

    def test_machine_accept_fen_rejects_known_square_mismatch_evidence(self) -> None:
        expected = "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"
        candidate = "6k1/p4p1p/3p1p2/2p1p3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1"

        result = machine_accept_fen(
            {
                "source": "deterministic",
                "fen": candidate,
                "confidence": 0.99,
                "warnings": [],
            },
            {"min_confidence": 0.90, "expected_fen": expected},
        )

        mismatch = [blocker for blocker in result["acceptance_blockers"] if blocker["code"] == "expected_fen_square_mismatch"]
        self.assertEqual(result["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertTrue(mismatch)
        self.assertEqual(mismatch[0]["square_diffs"][0]["square"], "e5")

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
