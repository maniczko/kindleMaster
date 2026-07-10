from __future__ import annotations

import unittest

from app import _diagram_record_to_reader_position
from chess_fen_hardening import machine_accept_fen
from chess_side_to_move_evidence import (
    resolve_marker_semantic_contract,
    resolve_side_to_move_evidence,
)
from pymupdf_chess_extractor import (
    _apply_scan_chess_two_crop_quality_gate,
    _scan_chess_two_crop_trusted_side,
)


VALID_PLACEMENT = "4k3/8/8/8/8/8/8/4K3"
VALID_FEN = f"{VALID_PLACEMENT} b - - 0 1"


def _trusted_marker_record(**updates: object) -> dict[str, object]:
    record: dict[str, object] = {
        "fen": VALID_FEN,
        "full_fen": VALID_FEN,
        "placement": VALID_PLACEMENT,
        "source": "image-template-board",
        "method": "image-template-board",
        "confidence": 0.99,
        "warnings": [],
        "side_to_move": "b",
        "side_to_move_status": "explicit",
        "side_to_move_evidence": "marker",
        "side_marker_status": "trusted_marker",
        "side_marker_confidence": 0.96,
        "marker_assignment_status": "assigned",
        "marker_crop_quality": "pass",
        "marker_crop_fail_reason": [],
        "marker_crop_quality_gate": {
            "decision": "pass",
            "component_count": 1,
            "reasons": [],
            "side_to_move": "black",
            "confidence": 0.96,
        },
        "side_marker_crop_path": "review/marker.png",
        "marker_bbox": [10, 20, 30, 40],
        "selected_marker_zone": "right",
        "manual_review_required": False,
        "manual_review_reason": "",
    }
    record.update(updates)
    return record


class ChessMarkerSemanticGateTests(unittest.TestCase):
    def test_trusted_marker_survives_bad_board_while_full_fen_is_blocked(self) -> None:
        record = _trusted_marker_record(
            board_crop_quality="fail",
            board_crop_fail_reason=["contains_text"],
            manual_review_required=True,
            manual_review_reason="bad_crop",
        )

        marker = resolve_marker_semantic_contract(record)
        evidence = resolve_side_to_move_evidence(record)
        fen_gate = machine_accept_fen(record, {"min_confidence": 0.90})

        self.assertEqual(marker["marker_semantic_status"], "trusted")
        self.assertEqual(marker["marker_semantic_side"], "b")
        self.assertEqual(marker["marker_ownership_status"], "assigned")
        self.assertEqual(marker["board_placement_status"], "review")
        self.assertFalse(marker["full_fen_allowed"])
        self.assertIn("board_crop_quality_failed", marker["full_fen_blockers"])
        self.assertEqual(evidence["side_to_move_source"], "trusted_marker")
        self.assertFalse(evidence["manual_review_required"])
        self.assertEqual(fen_gate["marker_semantic_status"], "trusted")
        self.assertEqual(fen_gate["board_placement_status"], "review")
        self.assertFalse(fen_gate["full_fen_allowed"])

    def test_good_board_with_bad_marker_keeps_placement_but_blocks_side_and_fen(self) -> None:
        record = _trusted_marker_record(
            board_crop_quality="pass",
            placement_status="FEN_PLACEMENT_MACHINE_ACCEPTED",
            marker_crop_quality="fail",
            marker_crop_fail_reason=["unclear_symbol"],
            marker_crop_quality_gate={
                "decision": "fail",
                "component_count": 1,
                "reasons": ["unclear_symbol"],
            },
            manual_review_required=True,
            manual_review_reason="unclear",
        )

        marker = resolve_marker_semantic_contract(record)

        self.assertEqual(marker["marker_semantic_status"], "review")
        self.assertEqual(marker["marker_semantic_side"], "unknown")
        self.assertEqual(marker["board_placement_status"], "accepted")
        self.assertFalse(marker["full_fen_allowed"])
        self.assertIn("marker_semantic_not_trusted", marker["full_fen_blockers"])

    def test_good_marker_and_good_board_allow_full_fen(self) -> None:
        record = _trusted_marker_record(
            board_crop_quality="pass",
            placement_status="FEN_PLACEMENT_MACHINE_ACCEPTED",
        )

        marker = resolve_marker_semantic_contract(record)
        fen_gate = machine_accept_fen(record, {"min_confidence": 0.90})

        self.assertEqual(marker["marker_semantic_status"], "trusted")
        self.assertEqual(marker["board_placement_status"], "accepted")
        self.assertTrue(marker["full_fen_allowed"])
        self.assertEqual(marker["full_fen_blockers"], [])
        self.assertEqual(fen_gate["status"], "accepted")
        self.assertTrue(fen_gate["full_fen_allowed"])

    def test_marker_conflict_is_review_only_even_with_good_board(self) -> None:
        record = _trusted_marker_record(
            board_crop_quality="pass",
            placement_status="FEN_PLACEMENT_MACHINE_ACCEPTED",
            side_marker_status="marker_conflict",
            marker_assignment_status="needs_review_candidate_conflict",
            warnings=["side_to_move_marker_local_conflict"],
            manual_review_required=True,
            manual_review_reason="marker_conflict",
        )

        marker = resolve_marker_semantic_contract(record)

        self.assertEqual(marker["marker_semantic_status"], "review")
        self.assertEqual(marker["marker_semantic_side"], "unknown")
        self.assertEqual(marker["marker_ownership_status"], "ambiguous")
        self.assertEqual(marker["board_placement_status"], "accepted")
        self.assertFalse(marker["full_fen_allowed"])

    def test_extractor_trusts_marker_crop_without_requiring_board_crop(self) -> None:
        fields = {
            "board_crop_quality": "fail",
            "marker_crop_quality": "pass",
            "marker_bbox": [10, 20, 30, 40],
            "selected_marker_zone": "right",
            "marker_crop_fail_reason": [],
            "side_to_move_detected": "black",
            "side_to_move_confidence": 0.96,
            "marker_crop_quality_gate": {
                "decision": "pass",
                "component_count": 1,
                "reasons": [],
                "side_to_move": "black",
            },
        }
        payload = _trusted_marker_record(board_crop_quality="pass")

        self.assertEqual(_scan_chess_two_crop_trusted_side(fields), "b")
        gated = _apply_scan_chess_two_crop_quality_gate(payload, fields)
        self.assertEqual(gated["side_to_move"], "b")
        self.assertEqual(gated["side_to_move_status"], "explicit")
        self.assertEqual(gated["marker_semantic_status"], "trusted")
        self.assertEqual(gated["board_placement_status"], "review")
        self.assertFalse(gated["full_fen_allowed"])

    def test_reader_keeps_trusted_side_but_honors_explicit_full_fen_block(self) -> None:
        position = _diagram_record_to_reader_position(
            {
                **_trusted_marker_record(
                    board_crop_quality="fail",
                    board_placement_status="review",
                    full_fen_allowed=False,
                    full_fen_blockers=["board_crop_quality_failed"],
                ),
                "marker_semantic_status": "trusted",
                "marker_semantic_side": "b",
                "full_fen_status": "FEN_MACHINE_ACCEPTED",
                "status": "accepted",
            },
            1,
        )

        self.assertEqual(position["side_to_move"], "b")
        self.assertEqual(position["marker_semantic_status"], "trusted")
        self.assertEqual(position["status"], "needs_review")
        self.assertFalse(position["full_fen_allowed"])
        self.assertEqual(position["fen"], "")
        self.assertEqual(position["fen_candidate"], VALID_FEN)


if __name__ == "__main__":
    unittest.main()
