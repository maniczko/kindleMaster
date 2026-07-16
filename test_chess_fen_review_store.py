from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_fen_review_store import (
    FEN_REVIEW_DRAFT_FILENAME,
    FEN_REVIEW_PROGRESS_FILENAME,
    FenReviewStoreError,
    load_fen_review_progress,
    save_fen_review_progress,
)


class ChessFenReviewStoreTests(unittest.TestCase):
    def _seed_row(self, fingerprint: str, *, diagram_id: str) -> dict:
        return {
            "schema": "kindlemaster.fen_manual_review.row.v4",
            "review_contract": "source_bound_piece_grid_v2",
            "artifact_id": "artifact-123",
            "diagram_id": diagram_id,
            "diagram_fingerprint": fingerprint,
            "source_document_sha256": "a" * 64,
            "crop_sha256": fingerprint,
            "fen_candidate": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

    def _write_seed(self, review_dir: Path, rows: list[dict]) -> None:
        review_dir.mkdir(parents=True)
        (review_dir / FEN_REVIEW_DRAFT_FILENAME).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_save_merges_partial_submission_and_derives_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            first = self._seed_row("1" * 64, diagram_id="p001-d1")
            second = self._seed_row("2" * 64, diagram_id="p002-d1")
            self._write_seed(review_dir, [first, second])
            cells = [""] * 64
            cells[4] = "k"
            cells[60] = "K"

            payload = save_fen_review_progress(
                review_dir,
                [
                    {
                        "diagram_fingerprint": first["diagram_fingerprint"],
                        "square_labels": cells,
                        "piece_labels_verified": True,
                        "manual_side_to_move": "w",
                        "manual_side_evidence": "marker",
                        "manual_visible_marker": "outline_triangle",
                        "board_crop_label": "correct",
                        "marker_crop_label": "clear",
                        "label_status": "verified",
                        "verified_by": "PM",
                        "notes": "checked",
                    }
                ],
                artifact_id="artifact-123",
                source_digest="a" * 64,
            )

            self.assertEqual(payload["summary"]["verified"], 1)
            self.assertEqual(payload["summary"]["pending"], 1)
            loaded = load_fen_review_progress(review_dir)
            self.assertEqual(len(loaded["rows"]), 2)
            self.assertEqual(loaded["rows"][0]["manual_fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
            self.assertTrue(loaded["rows"][0]["fen_human_verified"])
            self.assertEqual(loaded["rows"][1]["label_status"], "needs_piece_labels")
            self.assertTrue((review_dir / FEN_REVIEW_PROGRESS_FILENAME).is_file())

    def test_save_rejects_unknown_source_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            row = self._seed_row("1" * 64, diagram_id="p001-d1")
            self._write_seed(review_dir, [row])

            with self.assertRaises(FenReviewStoreError):
                save_fen_review_progress(
                    review_dir,
                    [
                        {
                            "diagram_fingerprint": "9" * 64,
                            "square_labels": [""] * 64,
                        }
                    ],
                    artifact_id="artifact-123",
                    source_digest="a" * 64,
                )

    def test_save_rejects_source_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            row = self._seed_row("1" * 64, diagram_id="p001-d1")
            self._write_seed(review_dir, [row])

            with self.assertRaises(FenReviewStoreError):
                save_fen_review_progress(
                    review_dir,
                    [],
                    artifact_id="artifact-123",
                    source_digest="b" * 64,
                )


if __name__ == "__main__":
    unittest.main()
