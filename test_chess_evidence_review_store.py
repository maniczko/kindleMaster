from __future__ import annotations

import unittest

from chess_evidence_coverage_join import join_chess_evidence_records
from chess_evidence_review_store import (
    EvidenceReviewStoreError,
    export_marker_labels,
    prepare_evidence_review_queue,
    prepare_evidence_review_submission,
)


SOURCE_SHA = "a" * 64
PROFILE = "fixed-edition"


def _coverage(*, fingerprint: str = "dfp_" + "1" * 32, asset: str = "fen_manual_assets/marker.png") -> dict:
    return {
        "source_profile": PROFILE,
        "canonical_diagram_fingerprint": fingerprint,
        "canonical_diagram_id": "canonical-1",
        "legacy_intake_diagram_id": "p001_d01",
        "page": 1,
        "normalized_bbox_xyxy": [0.1, 0.1, 0.8, 0.8],
        "identity_status": "page_bbox_one_to_one",
        "fen_evidence": {"status": "bound_human_verified", "crop_rel_path": "fen_manual_assets/board.png"},
        "marker_evidence": {
            "manual_visible_marker": "outline_triangle",
            "manual_side_to_move": "w",
            "marker_crop_rel_path": asset,
            "review_suggestion_bbox": [10, 20, 30, 40],
        },
        "blockers": ["marker_bbox_verification_required"],
    }


class ChessEvidenceReviewStoreTests(unittest.TestCase):
    def _queue_row(self, **kwargs) -> dict:
        return prepare_evidence_review_queue(
            [_coverage(**kwargs)],
            artifact_id="artifact-1",
            source_document_sha256=SOURCE_SHA,
            source_profile=PROFILE,
        )["rows"][0]

    def test_queue_prefills_suggestions_without_claiming_human_bbox(self) -> None:
        row = self._queue_row()

        self.assertEqual(row["asset_kind"], "marker_crop")
        self.assertEqual(row["marker_shape"], "outline_triangle")
        self.assertEqual(row["side_to_move"], "w")
        self.assertIsNone(row["marker_bbox"])
        self.assertFalse(row["marker_bbox_verified"])
        self.assertEqual(row["label_status"], "open")

    def test_absolute_assets_are_removed_from_database_payload(self) -> None:
        row = self._queue_row(asset="C:/private/marker.png")

        self.assertEqual(row["asset_kind"], "board_crop")
        self.assertEqual(row["asset_rel_path"], "fen_manual_assets/board.png")

    def test_visible_marker_requires_normalized_bbox_and_matching_side(self) -> None:
        row = self._queue_row()
        with self.assertRaisesRegex(EvidenceReviewStoreError, "wymaga poprawnego bbox"):
            prepare_evidence_review_submission(
                row,
                {
                    "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                    "label_status": "verified_visible",
                    "marker_shape": "outline_triangle",
                    "side_to_move": "w",
                    "verified_by": "PM",
                },
                expected_revision=0,
            )

        saved = prepare_evidence_review_submission(
            row,
            {
                "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                "label_status": "verified_visible",
                "marker_shape": "outline_triangle",
                "side_to_move": "w",
                "marker_bbox": [0.1, 0.2, 0.3, 0.4],
                "verified_by": "PM",
            },
            expected_revision=0,
        )
        self.assertTrue(saved["marker_bbox_verified"])
        self.assertEqual(saved["marker_bbox_space"], "review_asset_normalized")

    def test_absence_requires_explicit_complete_crop(self) -> None:
        row = self._queue_row()
        with self.assertRaisesRegex(EvidenceReviewStoreError, "kompletnego cropa"):
            prepare_evidence_review_submission(
                row,
                {
                    "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                    "label_status": "verified_absence",
                    "marker_shape": "none_confirmed",
                    "crop_complete": False,
                    "verified_by": "PM",
                },
                expected_revision=0,
            )

    def test_stale_revision_fails_closed(self) -> None:
        row = {**self._queue_row(), "revision": 2}
        with self.assertRaisesRegex(EvidenceReviewStoreError, "innej sesji"):
            prepare_evidence_review_submission(
                row,
                {
                    "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                    "label_status": "open",
                },
                expected_revision=1,
            )

    def test_export_round_trip_preserves_join_marker_contract(self) -> None:
        row = self._queue_row()
        saved = prepare_evidence_review_submission(
            row,
            {
                "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                "label_status": "verified_visible",
                "marker_shape": "outline_triangle",
                "side_to_move": "w",
                "marker_bbox": [0.1, 0.2, 0.3, 0.4],
                "verified_by": "PM",
            },
            expected_revision=0,
        )

        exported = export_marker_labels([saved])

        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["diagram_id"], "p001_d01")
        self.assertTrue(exported[0]["human_verified"])
        self.assertTrue(exported[0]["marker_bbox_verified"])
        self.assertEqual(exported[0]["manual_marker_bbox"], [0.1, 0.2, 0.3, 0.4])

    def test_exported_label_completes_marker_evidence_in_join(self) -> None:
        row = self._queue_row()
        saved = prepare_evidence_review_submission(
            row,
            {
                "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                "label_status": "verified_visible",
                "marker_shape": "outline_triangle",
                "side_to_move": "w",
                "marker_bbox": [0.1, 0.2, 0.3, 0.4],
                "verified_by": "PM",
            },
            expected_revision=0,
        )
        marker_labels = export_marker_labels([saved])
        fen_row = {
            "diagram_fingerprint": "f" * 64,
            "diagram_id": "review-1",
            "source_document_sha256": SOURCE_SHA,
            "page": 1,
            "normalized_bbox_xyxy": [0.1, 0.1, 0.8, 0.8],
            "label_status": "verified",
            "human_verified": True,
            "fen_human_verified": True,
            "verification_source": "human_visual",
            "manual_visible_marker": "outline_triangle",
            "manual_side_to_move": "w",
            "marker_crop_label": "clear",
            "crop_rel_path": "fen_manual_assets/board.png",
        }
        result = join_chess_evidence_records(
            canonical_rows=[{
                "diagram_fingerprint": row["canonical_diagram_fingerprint"],
                "diagram_id": row["canonical_diagram_id"],
                "legacy_intake_diagram_id": row["legacy_intake_diagram_id"],
                "page": 1,
                "normalized_bbox_xyxy": [0.1, 0.1, 0.8, 0.8],
                "identity": {"status": "exact_fingerprint"},
            }],
            fen_labels=[fen_row],
            fen_review_rows=[{
                "diagram_fingerprint": "f" * 64,
                "diagram_id": "review-1",
                "source_document_sha256": SOURCE_SHA,
                "page": 1,
                "normalized_bbox_xyxy": [0.1, 0.1, 0.8, 0.8],
                "label_status": "verified",
                "crop_rel_path": "fen_manual_assets/board.png",
            }],
            marker_labels=marker_labels,
            source_document_sha256=SOURCE_SHA,
            source_profile=PROFILE,
            page_sizes={1: (100.0, 100.0)},
        )

        self.assertEqual(result["report"]["counts"]["marker_labels_bound"], 1)
        self.assertEqual(result["report"]["counts"]["canonical_marker_evidence_complete"], 1)


if __name__ == "__main__":
    unittest.main()
