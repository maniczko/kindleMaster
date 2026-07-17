from __future__ import annotations

import unittest

from chess_evidence_review_repository import ChessEvidenceReviewRepository


class FakeCloudClient:
    available = True

    def __init__(self) -> None:
        self.payload = None
        self.imported = None
        self.saved = None

    def load_review(self, *, artifact_id: str):
        return self.payload

    def load_item(self, *, artifact_id: str, canonical_diagram_fingerprint: str):
        if self.payload is None:
            return None
        row = next(
            (
                item
                for item in self.payload.get("rows") or []
                if item.get("canonical_diagram_fingerprint") == canonical_diagram_fingerprint
            ),
            None,
        )
        if row is None:
            return None
        return {
            "source_document_sha256": self.payload.get("source_document_sha256"),
            "source_profile": self.payload.get("source_profile"),
            "row": row,
        }

    def import_queue(self, **payload):
        self.imported = payload
        return {"artifact_id": payload["artifact_id"], "storage": "database"}

    def save_item(self, **payload):
        self.saved = payload
        return {"revision": payload["expected_revision"] + 1, "row": payload["row"]}


class ChessEvidenceReviewRepositoryTests(unittest.TestCase):
    def _coverage(self) -> dict:
        return {
            "source_profile": "fixed-edition",
            "canonical_diagram_fingerprint": "dfp_" + "1" * 32,
            "canonical_diagram_id": "canonical-1",
            "legacy_intake_diagram_id": "p001_d01",
            "page": 1,
            "normalized_bbox_xyxy": [0.1, 0.1, 0.8, 0.8],
            "fen_evidence": {"crop_rel_path": "fen_manual_assets/board.png"},
            "marker_evidence": {"marker_crop_rel_path": "fen_manual_assets/marker.png"},
            "blockers": [],
        }

    def test_import_prepares_source_bound_rows(self) -> None:
        cloud = FakeCloudClient()
        repository = ChessEvidenceReviewRepository(cloud_client=cloud)

        result = repository.import_queue(
            [self._coverage()],
            artifact_id="artifact-1",
            source_document_sha256="a" * 64,
            source_profile="fixed-edition",
        )

        self.assertEqual(result["summary"]["total"], 1)
        self.assertIsNotNone(cloud.imported)
        assert cloud.imported is not None
        self.assertEqual(cloud.imported["rows"][0]["label_status"], "open")

    def test_save_validates_against_current_database_revision(self) -> None:
        cloud = FakeCloudClient()
        repository = ChessEvidenceReviewRepository(cloud_client=cloud)
        prepared = repository.import_queue(
            [self._coverage()],
            artifact_id="artifact-1",
            source_document_sha256="a" * 64,
            source_profile="fixed-edition",
        )
        row = prepared["rows"][0]
        cloud.payload = {
            "source_document_sha256": "a" * 64,
            "source_profile": "fixed-edition",
            "rows": [row],
        }

        result = repository.save_item(
            artifact_id="artifact-1",
            expected_revision=0,
            submitted={
                "canonical_diagram_fingerprint": row["canonical_diagram_fingerprint"],
                "label_status": "verified_visible",
                "marker_shape": "outline_triangle",
                "side_to_move": "w",
                "marker_bbox": [0.1, 0.2, 0.3, 0.4],
                "verified_by": "PM",
            },
        )

        self.assertEqual(result["revision"], 1)
        self.assertIsNotNone(cloud.saved)


if __name__ == "__main__":
    unittest.main()
