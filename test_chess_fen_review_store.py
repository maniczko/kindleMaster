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
    summarize_fen_review_rows,
)


class ChessFenReviewStoreTests(unittest.TestCase):
    def test_summary_separates_false_positives_from_unreadable_diagrams(self) -> None:
        rows = [
            {"label_status": "rejected", "verified_by": "reviewer"},
            {"label_status": "unreadable", "verified_by": "reviewer"},
        ]

        summary = summarize_fen_review_rows(rows)

        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(summary["unreadable"], 1)
        self.assertEqual(summary["excluded"], 2)
        self.assertEqual(summary["completed"], 2)

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
            self.assertEqual(payload["summary"]["completed"], 1)
            self.assertEqual(payload["summary"]["excluded"], 0)
            self.assertEqual(payload["summary"]["pending"], 1)
            loaded = load_fen_review_progress(review_dir)
            self.assertEqual(len(loaded["rows"]), 2)
            self.assertEqual(loaded["rows"][0]["manual_fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
            self.assertEqual(loaded["rows"][0]["fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
            self.assertTrue(loaded["rows"][0]["fen_human_verified"])
            self.assertTrue(loaded["rows"][0]["human_verified"])
            self.assertTrue(loaded["rows"][0]["square_diff_ack"])
            self.assertEqual(loaded["rows"][0]["verification_source"], "human_visual")
            self.assertEqual(loaded["rows"][0]["id"], "p001-d1")
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

    def test_save_preserves_verified_placement_without_claiming_full_fen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._seed_row("1" * 64, diagram_id="p001-d1")
            self._write_seed(review_dir, [seed])
            cells = [""] * 64
            cells[4] = "k"
            cells[60] = "K"

            payload = save_fen_review_progress(
                review_dir,
                [
                    {
                        **seed,
                        "square_labels": cells,
                        "piece_labels_verified": True,
                        "manual_side_to_move": "w",
                        "board_crop_label": "correct",
                        "marker_crop_label": "unreadable",
                        "manual_side_evidence": "unknown",
                        "label_status": "placement_verified",
                        "verified_by": "PM",
                    }
                ],
                artifact_id="artifact-123",
                source_digest="a" * 64,
            )

            row = load_fen_review_progress(review_dir)["rows"][0]
            self.assertEqual(payload["summary"]["placement_verified"], 1)
            self.assertEqual(payload["summary"]["completed"], 1)
            self.assertEqual(payload["summary"]["invalid"], 0)
            self.assertTrue(row["placement_human_verified"])
            self.assertFalse(row["fen_human_verified"])
            self.assertEqual(row["manual_fen"], "")
            self.assertEqual(row["fen"], "")

    def test_save_migrates_legacy_verified_row_without_side_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._seed_row("1" * 64, diagram_id="p001-d1")
            self._write_seed(review_dir, [seed])
            cells = [""] * 64
            cells[4] = "k"
            cells[60] = "K"

            payload = save_fen_review_progress(
                review_dir,
                [
                    {
                        **seed,
                        "square_labels": cells,
                        "piece_labels_verified": True,
                        "board_crop_label": "correct",
                        "marker_crop_label": "unreadable",
                        "manual_side_evidence": "unknown",
                        "label_status": "verified",
                        "verified_by": "PM",
                    }
                ],
                artifact_id="artifact-123",
                source_digest="a" * 64,
            )

            row = load_fen_review_progress(review_dir)["rows"][0]
            self.assertEqual(row["label_status"], "placement_verified")
            self.assertEqual(
                row["status_migration"],
                "verified_without_full_fen_to_placement_verified_v1",
            )
            self.assertEqual(payload["summary"]["placement_verified"], 1)

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

    def test_load_reuses_progress_by_diagram_id_when_source_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._seed_row("1" * 64, diagram_id="p001-d1")
            self._write_seed(review_dir, [seed])
            cells = [""] * 64
            cells[4] = "k"
            cells[60] = "K"
            legacy = {
                **seed,
                "diagram_fingerprint": "2" * 64,
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

            payload = load_fen_review_progress(review_dir, persisted_rows=[legacy], storage="database")

            self.assertEqual(payload["summary"]["verified"], 1)
            self.assertEqual(payload["rows"][0]["diagram_fingerprint"], "1" * 64)
            self.assertEqual(payload["rows"][0]["manual_fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")

    def test_load_does_not_reuse_diagram_id_when_source_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir) / "review"
            seed = self._seed_row("1" * 64, diagram_id="p001-d1")
            self._write_seed(review_dir, [seed])
            foreign = {**seed, "diagram_fingerprint": "2" * 64, "source_document_sha256": "b" * 64}

            payload = load_fen_review_progress(review_dir, persisted_rows=[foreign], storage="database")

            self.assertEqual(payload["summary"]["pending"], 1)
            self.assertEqual(payload["rows"][0]["diagram_fingerprint"], "1" * 64)


if __name__ == "__main__":
    unittest.main()
