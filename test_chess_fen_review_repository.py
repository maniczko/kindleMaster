from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_fen_review_repository import ChessFenReviewRepository
from chess_fen_review_store import (
    FEN_REVIEW_DRAFT_FILENAME,
    FEN_REVIEW_PROGRESS_FILENAME,
    FenReviewConflictError,
    FenReviewOwnershipError,
    FenReviewStoreError,
)
from supabase_fen_review import SupabaseFenReviewConflictError, SupabaseFenReviewOwnershipError


class FakeCloudClient:
    available = True

    def __init__(
        self,
        loaded: dict | None = None,
        *,
        fail_load: bool = False,
        fail_save: bool = False,
        conflict: bool = False,
        owner_mismatch: bool = False,
    ) -> None:
        self.loaded = loaded
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.conflict = conflict
        self.owner_mismatch = owner_mismatch
        self.saved: dict | None = None

    def load_review(self, *, artifact_id: str, **_filters):
        if self.fail_load:
            raise RuntimeError("database unavailable")
        return self.loaded

    def save_review(self, **payload):
        if self.conflict:
            raise SupabaseFenReviewConflictError("fen_review_revision_conflict")
        if self.owner_mismatch:
            raise SupabaseFenReviewOwnershipError("fen_review_owner_mismatch")
        if self.fail_save:
            raise RuntimeError("database unavailable")
        self.saved = payload
        return {
            "schema": "kindlemaster.fen_review_progress.v1",
            "status": "saved",
            "saved_at": "2026-07-16T12:00:00Z",
            "submitted_count": len(payload["rows"]),
            "summary": dict(payload["summary"]),
            "storage": "database",
        }


class ChessFenReviewRepositoryTests(unittest.TestCase):
    def _seed_row(self) -> dict:
        return {
            "schema": "kindlemaster.fen_manual_review.row.v4",
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

    def _write_seed(self, review_dir: Path) -> dict:
        row = self._seed_row()
        review_dir.mkdir(parents=True)
        (review_dir / FEN_REVIEW_DRAFT_FILENAME).write_text(json.dumps(row) + "\n", encoding="utf-8")
        return row

    def _verified_row(self, row: dict) -> dict:
        cells = [""] * 64
        cells[4] = "k"
        cells[60] = "K"
        return {
            **row,
            "square_labels": cells,
            "piece_labels_verified": True,
            "manual_side_to_move": "w",
            "manual_side_evidence": "marker",
            "manual_visible_marker": "outline_triangle",
            "board_crop_label": "correct",
            "marker_crop_label": "clear",
            "label_status": "verified",
            "verified_by": "PM",
        }

    def test_load_prefers_database_over_stale_file_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)
            database_row = self._verified_row(seed)
            cloud = FakeCloudClient(
                {
                    "source_document_sha256": "a" * 64,
                    "rows": [database_row],
                    "summary": {"total": 1, "verified": 1},
                    "saved_at": "2026-07-16T12:00:00Z",
                }
            )

            payload = ChessFenReviewRepository(
                review_dir,
                artifact_id="artifact-1",
                cloud_client=cloud,
            ).load()

        self.assertEqual(payload["storage"], "database")
        self.assertEqual(payload["summary"]["verified"], 1)
        self.assertEqual(payload["rows"][0]["manual_fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")

    def test_source_bound_reuse_preserves_complete_session_for_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)
            database_row = self._verified_row(seed)
            cloud = FakeCloudClient(
                {
                    "source_document_sha256": "a" * 64,
                    "rows": [database_row],
                    "summary": {"total": 1, "verified": 1},
                    "saved_at": "2026-07-16T12:00:00Z",
                    "session_status": "complete",
                    "closed_at": "2026-07-16T12:00:00Z",
                    "revision": 109,
                    "reused_from_artifact_id": "source-artifact",
                }
            )

            payload = ChessFenReviewRepository(
                review_dir,
                artifact_id="current-artifact",
                owner_user_id="owner-1",
                cloud_client=cloud,
            ).load()

        self.assertEqual(payload["session_status"], "complete")
        self.assertEqual(payload["closed_at"], "2026-07-16T12:00:00Z")
        self.assertEqual(payload["revision"], 0)
        self.assertEqual(payload["reused_from_artifact_id"], "source-artifact")

    def test_stale_revision_is_not_silently_written_to_file_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)
            cloud = FakeCloudClient(conflict=True)

            with self.assertRaises(FenReviewConflictError):
                ChessFenReviewRepository(
                    review_dir,
                    artifact_id="artifact-1",
                    cloud_client=cloud,
                ).save(
                    [self._verified_row(seed)],
                    source_digest="a" * 64,
                    expected_revision=3,
                )

            self.assertFalse((review_dir / FEN_REVIEW_PROGRESS_FILENAME).exists())

    def test_close_requires_all_rows_to_be_terminal_and_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)

            with self.assertRaises(FenReviewStoreError):
                ChessFenReviewRepository(
                    review_dir,
                    artifact_id="artifact-1",
                    cloud_client=FakeCloudClient(),
                ).save([seed], source_digest="a" * 64, action="close")

    def test_owner_mismatch_is_not_written_to_file_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)

            with self.assertRaises(FenReviewOwnershipError):
                ChessFenReviewRepository(
                    review_dir,
                    artifact_id="artifact-1",
                    cloud_client=FakeCloudClient(owner_mismatch=True),
                ).save([self._verified_row(seed)], source_digest="a" * 64)

            self.assertFalse((review_dir / FEN_REVIEW_PROGRESS_FILENAME).exists())

    def test_close_does_not_fall_back_when_database_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)

            with self.assertRaises(FenReviewStoreError):
                ChessFenReviewRepository(
                    review_dir,
                    artifact_id="artifact-1",
                    cloud_client=FakeCloudClient(fail_save=True),
                ).save([self._verified_row(seed)], source_digest="a" * 64, action="close")

            self.assertFalse((review_dir / FEN_REVIEW_PROGRESS_FILENAME).exists())

    def test_save_writes_database_first_and_refreshes_file_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)
            cloud = FakeCloudClient()

            payload = ChessFenReviewRepository(
                review_dir,
                artifact_id="artifact-1",
                cloud_client=cloud,
            ).save([self._verified_row(seed)], source_digest="a" * 64)

            self.assertIsNotNone(cloud.saved)
            assert cloud.saved is not None
            self.assertEqual(cloud.saved["summary"]["verified"], 1)
            self.assertEqual(payload["storage"], "database")
            self.assertEqual(payload["cache_status"], "synced")
            self.assertTrue((review_dir / FEN_REVIEW_PROGRESS_FILENAME).is_file())

    def test_save_falls_back_to_file_when_database_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._write_seed(review_dir)
            cloud = FakeCloudClient(fail_save=True)

            payload = ChessFenReviewRepository(
                review_dir,
                artifact_id="artifact-1",
                cloud_client=cloud,
            ).save([self._verified_row(seed)], source_digest="a" * 64)

        self.assertEqual(payload["storage"], "server_fallback")
        self.assertEqual(payload["storage_warning"], "database_write_failed")
        self.assertEqual(payload["summary"]["verified"], 1)


if __name__ == "__main__":
    unittest.main()
