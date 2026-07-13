from __future__ import annotations

import unittest

from chess_side_to_move_evidence import (
    build_side_to_move_coverage_dashboard,
    resolve_side_to_move_evidence,
)
from chess_side_to_move_fusion import (
    caption_evidence_candidates,
    fuse_side_to_move_candidates,
    pgn_evidence_candidates,
)


class ChessSideToMoveFusionTests(unittest.TestCase):
    def test_non_finite_confidence_falls_back_without_invalid_json_value(self) -> None:
        fused = fuse_side_to_move_candidates(
            [
                {
                    "side": "w",
                    "source": "text_inferred",
                    "confidence": float("nan"),
                    "kind": "caption_phrase",
                    "support_only": False,
                    "provenance": {"field": "caption"},
                }
            ]
        )

        self.assertEqual(fused["side"], "unknown")
        self.assertEqual(fused["confidence"], 0.0)

    def test_caption_and_ocr_phrases_produce_real_text_evidence(self) -> None:
        cases = [
            ({"caption": "White to move and win"}, "w"),
            ({"ocr_caption": "Ruch czarnych"}, "b"),
            ({"diagram_caption": "Weiß am Zug"}, "w"),
        ]

        for record, expected in cases:
            with self.subTest(record=record):
                candidates = caption_evidence_candidates(record)
                result = resolve_side_to_move_evidence(record)

                self.assertEqual({row["side"] for row in candidates}, {expected})
                self.assertEqual(result["side_to_move"], expected)
                self.assertEqual(result["side_to_move_source"], "text_inferred")
                self.assertEqual(result["side_to_move_fusion_status"], "resolved")
                self.assertTrue(result["side_to_move_supporting_evidence"])
                self.assertEqual(result["side_to_move_conflicts"], [])
                self.assertFalse(result["full_fen_allowed"])

    def test_move_number_dot_ellipsis_and_pgn_fen_tag_produce_first_mover(self) -> None:
        cases = [
            ({"movetext": "1. e4 e5 2. Nf3"}, "w", "move_number_notation"),
            ({"solution_text": "23... Nf6 24. Qg3"}, "b", "move_number_notation"),
            (
                {
                    "pgn": '[SetUp "1"]\n[FEN "8/8/8/8/8/8/4K3/4k3 b - - 0 23"]\n23... Kf2'
                },
                "b",
                "pgn_fen_tag",
            ),
        ]

        for record, expected, expected_kind in cases:
            with self.subTest(record=record):
                candidates = pgn_evidence_candidates(record)
                result = resolve_side_to_move_evidence(record)

                self.assertIn(expected_kind, {row["kind"] for row in candidates})
                self.assertEqual(result["side_to_move"], expected)
                self.assertEqual(result["side_to_move_source"], "pgn_inferred")
                self.assertFalse(result["full_fen_allowed"])

    def test_consensus_increases_confidence_and_keeps_provenance(self) -> None:
        candidates = [
            {
                "side": "w",
                "source": "text_inferred",
                "confidence": 0.90,
                "kind": "caption_phrase",
                "support_only": False,
                "provenance": {"field": "caption"},
            },
            {
                "side": "w",
                "source": "pgn_inferred",
                "confidence": 0.80,
                "kind": "move_number_notation",
                "support_only": False,
                "provenance": {"field": "movetext"},
            },
        ]

        fused = fuse_side_to_move_candidates(candidates)

        self.assertEqual(fused["status"], "resolved")
        self.assertEqual(fused["side"], "w")
        self.assertEqual(fused["source"], "text_inferred")
        self.assertEqual(fused["confidence"], 0.98)
        self.assertEqual(len(fused["supporting_evidence"]), 2)

    def test_high_confidence_marker_text_conflict_fails_without_winner(self) -> None:
        result = resolve_side_to_move_evidence(
            {
                "side_to_move": "w",
                "side_marker_status": "trusted_marker",
                "side_marker_confidence": 0.98,
                "board_crop_quality": "pass",
                "marker_crop_quality": "pass",
                "marker_bbox": [10, 20, 30, 40],
                "marker_crop_quality_gate": {"decision": "pass"},
                "caption": "Black to move",
            }
        )

        self.assertEqual(result["side_to_move"], "unknown")
        self.assertEqual(result["side_to_move_source"], "conflict")
        self.assertEqual(result["side_to_move_evidence_tier"], "conflict")
        self.assertEqual(result["full_fen_blocker"], "side_to_move_evidence_conflict")
        self.assertFalse(result["full_fen_allowed"])
        self.assertTrue(any(row.get("blocking") for row in result["side_to_move_conflicts"]))

    def test_layout_prior_is_support_only_and_never_fills_unknown(self) -> None:
        prior_only = resolve_side_to_move_evidence({"layout_prior_side": "w"})
        disagreement = resolve_side_to_move_evidence(
            {
                "caption": "Black to move",
                "source_profile_layout_prior": {
                    "side": "w",
                    "confidence": 0.40,
                    "rule": "left_column_starts_white",
                },
            }
        )

        self.assertEqual(prior_only["side_to_move"], "unknown")
        self.assertEqual(prior_only["side_to_move_source"], "unknown")
        self.assertEqual(disagreement["side_to_move"], "b")
        self.assertEqual(disagreement["side_to_move_source"], "text_inferred")
        self.assertTrue(
            any(
                row.get("kind") == "supporting_prior_disagreement"
                and row.get("blocking") is False
                for row in disagreement["side_to_move_conflicts"]
            )
        )

    def test_dashboard_counts_derived_coverage_and_conflicts_separately(self) -> None:
        report = build_side_to_move_coverage_dashboard(
            [
                {"diagram_id": "caption", "caption": "White to move"},
                {"diagram_id": "pgn", "movetext": "1... Kf7"},
                {
                    "diagram_id": "conflict",
                    "caption": "White to move",
                    "movetext": "1... Kf7",
                },
            ]
        )

        self.assertEqual(report["summary"]["side_to_move_coverage_rate"], 0.6667)
        self.assertEqual(report["summary"]["unknown_count"], 1)
        self.assertEqual(report["summary"]["conflict_count"], 1)
        self.assertEqual(report["summary"]["trusted_marker_count"], 0)


if __name__ == "__main__":
    unittest.main()
