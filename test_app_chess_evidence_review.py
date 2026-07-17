from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch

import app as app_module
from chess_evidence_review_store import EvidenceReviewStoreError


class AppChessEvidenceReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app_module.app.test_client()
        self.original_jobs = dict(app_module._CONVERSION_JOBS)
        app_module._CONVERSION_JOBS.clear()
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        app_module._CONVERSION_JOB_STORE.create(
            {
                "job_id": "artifact-1",
                "status": "ready",
                "message": "ready",
                "source_type": "pdf",
                "filename": "study.pdf",
                "created_at": now,
                "updated_at": now,
                "source_path": "",
                "output_path": "",
                "download_name": "study.epub",
                "metadata": {"profile": "chess_training"},
                "artifacts": {},
            }
        )

    def tearDown(self) -> None:
        app_module._CONVERSION_JOBS.clear()
        app_module._CONVERSION_JOBS.update(self.original_jobs)

    def _row(self, *, status: str = "open") -> dict:
        return {
            "artifact_id": "artifact-1",
            "source_document_sha256": "a" * 64,
            "source_profile": "fixed-edition",
            "canonical_diagram_fingerprint": "dfp_" + "1" * 32,
            "canonical_diagram_id": "canonical-1",
            "legacy_intake_diagram_id": "p001_d01",
            "page": 1,
            "queue_index": 1,
            "asset_kind": "marker_crop",
            "asset_rel_path": "fen_manual_assets/marker.png",
            "label_status": status,
            "marker_shape": "outline_triangle" if status == "verified_visible" else "",
            "side_to_move": "w" if status == "verified_visible" else "",
            "marker_bbox": [0.1, 0.2, 0.3, 0.4] if status == "verified_visible" else None,
            "marker_bbox_verified": status == "verified_visible",
            "crop_complete": False,
            "human_verified": status != "open",
            "verified_by": "PM" if status != "open" else "",
            "revision": 0,
        }

    def _payload(self, *, status: str = "open") -> dict:
        return {
            "artifact_id": "artifact-1",
            "source_document_sha256": "a" * 64,
            "source_profile": "fixed-edition",
            "summary": {"total": 1, "open": int(status == "open")},
            "rows": [self._row(status=status)],
        }

    def test_page_renders_lightweight_bbox_editor_without_full_source_sha(self) -> None:
        response = self.client.get("/convert/artifact/artifact-1/chess_evidence_review")

        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="image-stage"', text)
        self.assertIn('/static/chess_evidence_review.js', text)
        self.assertIn('id="progress-label"', text)
        self.assertNotIn("a" * 64, text)
        self.assertNotIn('id="review-seed"', text)
        self.assertEqual(response.headers["X-KindleMaster-Artifact-Source"], "supabase-evidence-review")

    def test_progress_get_redacts_full_source_sha_from_browser_payload(self) -> None:
        with patch("chess_evidence_review_routes.ChessEvidenceReviewRepository") as repository_class:
            repository_class.return_value.load.return_value = self._payload()
            response = self.client.get(
                "/convert/artifact/artifact-1/chess_evidence_review_progress"
            )

        serialized = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["source_sha_short"], "aaaaaaaaaaaa...")
        self.assertNotIn("a" * 64, serialized)
        self.assertNotIn("source_document_sha256", serialized)

    def test_progress_put_returns_saved_row_and_revision(self) -> None:
        with patch("chess_evidence_review_routes.ChessEvidenceReviewRepository") as repository_class:
            repository_class.return_value.save_item.return_value = {
                "revision": 1,
                "row": self._row(status="verified_visible"),
            }
            response = self.client.put(
                "/convert/artifact/artifact-1/chess_evidence_review_progress",
                json={"expected_revision": 0, "row": self._row()},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertEqual(response.get_json()["revision"], 1)

    def test_progress_put_maps_stale_revision_to_http_409(self) -> None:
        with patch("chess_evidence_review_routes.ChessEvidenceReviewRepository") as repository_class:
            repository_class.return_value.save_item.side_effect = EvidenceReviewStoreError(
                "Rekord zostal zmieniony w innej sesji. Odswiez dane."
            )
            response = self.client.put(
                "/convert/artifact/artifact-1/chess_evidence_review_progress",
                json={"expected_revision": 0, "row": self._row()},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "evidence_review_revision_conflict")

    def test_export_returns_only_terminal_marker_labels(self) -> None:
        with patch("chess_evidence_review_routes.ChessEvidenceReviewRepository") as repository_class:
            repository_class.return_value.load.return_value = self._payload(status="verified_visible")
            response = self.client.get("/convert/artifact/artifact-1/chess_evidence_review_export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/x-ndjson")
        self.assertIn('"marker_bbox_verified": true', response.get_data(as_text=True))
        self.assertIn("attachment", response.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
