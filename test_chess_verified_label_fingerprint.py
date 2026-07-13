from __future__ import annotations

import copy
import unittest

from chess_side_to_move_evidence import resolve_side_to_move_evidence
from chess_side_to_move_fusion import verified_side_labels_from_acceptance_manifest
from chess_yusupov_acceptance import fuse_fixed_edition_side_to_move_records


SOURCE_SHA = "1" * 64
OTHER_SOURCE_SHA = "2" * 64
FINGERPRINT = "dfp_" + "a" * 32
OTHER_FINGERPRINT = "dfp_" + "b" * 32


def _label(*, side: str = "w") -> dict[str, object]:
    return {
        "source_document_sha256": SOURCE_SHA,
        "diagram_fingerprint": FINGERPRINT,
        "side_to_move": side,
        "label_status": "verified",
        "human_verified": True,
        "verification_source": "dual_human",
        "verified_by": "reviewer-a+reviewer-b",
        "verified_at": "2026-07-10T00:00:00Z",
    }


def _manifest() -> dict[str, object]:
    return {
        "source_profile": "yusupov-fundamentals",
        "source": {"sha256": SOURCE_SHA},
        "verification": {
            "status": "verified",
            "verified_by": "reviewer-a+reviewer-b",
            "verified_at": "2026-07-10T00:00:00Z",
        },
        "diagrams": [
            {
                "diagram_fingerprint": FINGERPRINT,
                "expected_side": "w",
                "label_status": "verified",
                "source_of_truth": "dual_human",
            },
            {
                "diagram_fingerprint": OTHER_FINGERPRINT,
                "expected_side": "b",
                "label_status": "verified",
                "source_of_truth": "dual_human",
            },
        ],
    }


class ChessVerifiedLabelFingerprintTests(unittest.TestCase):
    def test_manifest_with_non_iterable_diagrams_is_rejected(self) -> None:
        manifest = _manifest()
        manifest["diagrams"] = 7

        self.assertEqual(verified_side_labels_from_acceptance_manifest(manifest), [])

    def test_exact_source_and_fingerprint_reuse_is_human_verified_not_marker(self) -> None:
        result = resolve_side_to_move_evidence(
            {
                "source_document_sha256": SOURCE_SHA,
                "diagram_fingerprint": FINGERPRINT,
                "side_marker_status": "marker_missing",
            },
            verified_labels=[_label()],
        )

        self.assertEqual(result["side_to_move"], "w")
        self.assertEqual(result["side_to_move_source"], "human_verified")
        self.assertEqual(result["side_to_move_evidence_tier"], "verified")
        self.assertEqual(
            result["side_to_move_primary_evidence"]["kind"],
            "exact_verified_label",
        )
        self.assertEqual(
            result["side_to_move_primary_evidence"]["provenance"]["source_document_sha256"],
            SOURCE_SHA,
        )
        self.assertFalse(result["full_fen_allowed"])

    def test_changed_edition_never_reuses_matching_fingerprint_label(self) -> None:
        result = resolve_side_to_move_evidence(
            {
                "source_document_sha256": OTHER_SOURCE_SHA,
                "diagram_fingerprint": FINGERPRINT,
            },
            verified_labels=[_label()],
        )

        self.assertEqual(result["side_to_move"], "unknown")
        self.assertEqual(result["side_to_move_source"], "unknown")
        self.assertTrue(
            any(
                row.get("reason") == "source_sha256_mismatch"
                for row in result["side_to_move_conflicts"]
            )
        )

    def test_changed_crop_fingerprint_never_reuses_same_edition_label(self) -> None:
        result = resolve_side_to_move_evidence(
            {
                "source_document_sha256": SOURCE_SHA,
                "diagram_fingerprint": OTHER_FINGERPRINT,
            },
            verified_labels=[_label()],
        )

        self.assertEqual(result["side_to_move"], "unknown")
        self.assertEqual(result["side_to_move_supporting_evidence"], [])

    def test_incomplete_audit_trail_rejects_verified_label(self) -> None:
        label = _label()
        label.pop("verified_by")

        result = resolve_side_to_move_evidence(
            {
                "source_document_sha256": SOURCE_SHA,
                "diagram_fingerprint": FINGERPRINT,
            },
            verified_labels=[label],
        )

        self.assertEqual(result["side_to_move"], "unknown")
        self.assertTrue(
            any(
                row.get("reason") == "verified_by_missing"
                for row in result["side_to_move_conflicts"]
            )
        )

    def test_manifest_labels_inherit_exact_source_and_top_level_audit(self) -> None:
        labels = verified_side_labels_from_acceptance_manifest(_manifest())

        self.assertEqual(len(labels), 2)
        self.assertEqual({row["source_document_sha256"] for row in labels}, {SOURCE_SHA})
        self.assertEqual({row["verified_by"] for row in labels}, {"reviewer-a+reviewer-b"})

    def test_fixed_edition_fallback_reaches_full_coverage_without_fake_trust(self) -> None:
        records = [
            {
                "diagram_fingerprint": FINGERPRINT,
                "side_marker_status": "marker_missing",
                "side_to_move": "unknown",
                "full_fen_allowed": False,
            },
            {
                "diagram_fingerprint": OTHER_FINGERPRINT,
                "side_marker_status": "marker_missing",
                "side_to_move": "unknown",
                "full_fen_allowed": False,
            },
        ]

        fused = fuse_fixed_edition_side_to_move_records(
            _manifest(),
            records,
            source_document_sha256=SOURCE_SHA,
        )

        self.assertEqual({row["side_to_move"] for row in fused}, {"w", "b"})
        self.assertEqual({row["side_to_move_source"] for row in fused}, {"human_verified"})
        self.assertEqual({row["side_marker_status"] for row in fused}, {"marker_missing"})
        self.assertFalse(any(row["full_fen_allowed"] for row in fused))
        self.assertEqual(
            {
                row["side_to_move_primary_evidence"]["kind"]
                for row in fused
            },
            {"exact_verified_label"},
        )

    def test_verified_label_conflicting_with_visual_marker_fails_safely(self) -> None:
        manifest = copy.deepcopy(_manifest())
        records = [
            {
                "diagram_fingerprint": FINGERPRINT,
                "side_marker_status": "trusted_marker",
                "side_to_move": "b",
                "full_fen_allowed": True,
            }
        ]

        fused = fuse_fixed_edition_side_to_move_records(
            manifest,
            records,
            source_document_sha256=SOURCE_SHA,
        )[0]

        self.assertEqual(fused["side_to_move"], "unknown")
        self.assertEqual(fused["side_to_move_source"], "conflict")
        self.assertEqual(fused["side_to_move_fusion_status"], "conflict")
        self.assertFalse(fused["full_fen_allowed"])
        self.assertTrue(any(row.get("blocking") for row in fused["side_to_move_conflicts"]))


if __name__ == "__main__":
    unittest.main()
