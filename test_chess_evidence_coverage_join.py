from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import fitz

from chess_evidence_coverage_join import (
    join_chess_evidence_files,
    join_chess_evidence_records,
)


SOURCE_SHA = "a" * 64
OTHER_SHA = "b" * 64


def _canonical(index: int, *, legacy_id: str = "") -> dict[str, object]:
    return {
        "diagram_fingerprint": "dfp_" + str(index) * 32,
        "diagram_id": f"current-{index}",
        "legacy_intake_diagram_id": legacy_id or f"old-{index}",
        "page": index,
        "normalized_bbox_xyxy": [0.1, 0.1, 0.4, 0.4],
        "identity": {"status": "exact_fingerprint"},
    }


def _review(index: int, *, status: str = "verified") -> dict[str, object]:
    return {
        "diagram_fingerprint": str(index) * 64,
        "diagram_id": f"review-{index}",
        "source_document_sha256": SOURCE_SHA,
        "page": index,
        "normalized_bbox_xyxy": [0.1, 0.1, 0.4, 0.4],
        "label_status": status,
        "crop_rel_path": f"assets/review-{index}.png",
    }


def _fen(index: int, *, marker: str = "outline_triangle") -> dict[str, object]:
    return {
        **_review(index),
        "label_status": "verified",
        "human_verified": True,
        "fen_human_verified": True,
        "verification_source": "human_visual",
        "manual_visible_marker": marker,
        "manual_side_to_move": "w" if marker == "outline_triangle" else "b",
        "marker_crop_label": "clear",
    }


def _marker(index: int, *, bbox_verified: bool = False) -> dict[str, object]:
    return {
        "diagram_id": f"old-{index}",
        "page": index,
        "label_status": "verified",
        "human_verified": True,
        "verification_source": "human_visual",
        "manual_visible_marker": "outline_triangle",
        "manual_side_to_move": "w",
        "manual_marker_bbox": [0.8, 0.05, 0.9, 0.15] if bbox_verified else None,
        "marker_bbox_verified": bbox_verified,
    }


class ChessEvidenceCoverageJoinTests(unittest.TestCase):
    def test_complete_fen_and_marker_evidence_bind_safely(self) -> None:
        result = join_chess_evidence_records(
            canonical_rows=[_canonical(1)],
            fen_labels=[_fen(1)],
            fen_review_rows=[_review(1)],
            marker_labels=[_marker(1, bbox_verified=True)],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
            page_sizes={1: (100.0, 100.0)},
            hard_negatives=[
                {"kind": kind, "label_status": "verified"}
                for kind in (
                    "arrows",
                    "borders",
                    "captions",
                    "coordinates",
                    "letters",
                    "neighboring_diagrams",
                )
            ],
        )

        self.assertEqual(result["report"]["counts"]["fen_labels_bound"], 1)
        self.assertEqual(result["report"]["counts"]["marker_labels_bound"], 1)
        self.assertEqual(result["report"]["counts"]["canonical_fully_evidenced"], 1)
        self.assertEqual(result["coverage"][0]["evidence_status"], "fully_evidenced")

    def test_partial_conflicting_and_orphan_evidence_is_queued(self) -> None:
        conflicting = _fen(1, marker="filled_triangle")
        conflicting["manual_side_to_move"] = "b"
        result = join_chess_evidence_records(
            canonical_rows=[_canonical(1)],
            fen_labels=[conflicting],
            fen_review_rows=[_review(1)],
            marker_labels=[_marker(1), {**_marker(2), "diagram_id": "unknown"}],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
            page_sizes={1: (100.0, 100.0)},
        )

        self.assertEqual(result["coverage"][0]["marker_evidence"]["status"], "conflicting_human_evidence")
        self.assertIn("marker_evidence_conflict", result["coverage"][0]["blockers"])
        self.assertEqual(result["report"]["counts"]["marker_labels_orphan"], 1)
        self.assertGreaterEqual(result["report"]["counts"]["action_queue"], 2)

    def test_geometry_ties_and_missing_matches_never_auto_bind(self) -> None:
        canonical_a = _canonical(1)
        canonical_b = {**_canonical(2), "page": 1, "normalized_bbox_xyxy": [0.1, 0.1, 0.4, 0.4]}
        result = join_chess_evidence_records(
            canonical_rows=[canonical_a, canonical_b],
            fen_labels=[_fen(1)],
            fen_review_rows=[_review(1)],
            marker_labels=[],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
            page_sizes={1: (100.0, 100.0)},
        )

        self.assertEqual(
            result["report"]["fen_label_status_counts"],
            {"ambiguous_canonical_geometry": 1},
        )
        self.assertEqual(result["report"]["counts"]["fen_labels_bound"], 0)

    def test_marker_page_mismatch_never_contributes_evidence(self) -> None:
        marker = _marker(1, bbox_verified=True)
        marker["page"] = 2
        result = join_chess_evidence_records(
            canonical_rows=[_canonical(1)],
            fen_labels=[_fen(1)],
            fen_review_rows=[_review(1)],
            marker_labels=[marker],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
            page_sizes={1: (100.0, 100.0)},
        )

        self.assertEqual(result["report"]["marker_label_status_counts"], {"page_mismatch": 1})
        self.assertEqual(result["report"]["counts"]["marker_labels_bound"], 0)
        self.assertEqual(result["report"]["counts"]["marker_labels_unbound"], 1)
        self.assertNotIn("historical_marker_review", result["coverage"][0]["marker_evidence"]["sources"])

    def test_unverified_marker_input_fails_closed(self) -> None:
        marker = _marker(1)
        marker["human_verified"] = False
        with self.assertRaisesRegex(ValueError, "marker_label.*human_verification_missing"):
            join_chess_evidence_records(
                canonical_rows=[_canonical(1)],
                fen_labels=[_fen(1)],
                fen_review_rows=[_review(1)],
                marker_labels=[marker],
                source_document_sha256=SOURCE_SHA,
                source_profile="fixed-edition",
                page_sizes={1: (100.0, 100.0)},
            )

    def test_duplicate_and_source_mismatch_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "fen_fingerprint.*duplicate"):
            join_chess_evidence_records(
                canonical_rows=[_canonical(1)],
                fen_labels=[_fen(1), _fen(1)],
                fen_review_rows=[_review(1)],
                marker_labels=[],
                source_document_sha256=SOURCE_SHA,
                source_profile="fixed-edition",
                page_sizes={1: (100.0, 100.0)},
            )

        mismatched = _review(1)
        mismatched["source_document_sha256"] = OTHER_SHA
        with self.assertRaisesRegex(ValueError, "fen_review.*source_sha256_mismatch"):
            join_chess_evidence_records(
                canonical_rows=[_canonical(1)],
                fen_labels=[_fen(1)],
                fen_review_rows=[mismatched],
                marker_labels=[],
                source_document_sha256=SOURCE_SHA,
                source_profile="fixed-edition",
                page_sizes={1: (100.0, 100.0)},
            )

    def test_file_report_is_redacted_and_status_totals_cover_every_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "source.pdf"
            document = fitz.open()
            document.new_page(width=100, height=100)
            document.save(pdf_path)
            document.close()
            source_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
            canonical = _canonical(1)
            fen = _fen(1)
            review = _review(1)
            for row in (fen, review):
                row["source_document_sha256"] = source_sha
            draft_path = root / "draft.json"
            fen_path = root / "fen.jsonl"
            review_path = root / "review.jsonl"
            marker_path = root / "marker.jsonl"
            output_path = root / "output"
            draft_path.write_text(
                json.dumps(
                    {
                        "source_profile": "fixed-edition",
                        "source": {"sha256": source_sha},
                        "verification": {"blockers": []},
                        "reconciliation": {
                            "intake_status_counts": {"exact_fingerprint": 1}
                        },
                        "diagrams": [canonical],
                        "hard_negatives": [],
                    }
                ),
                encoding="utf-8",
            )
            fen_path.write_text(json.dumps(fen) + "\n", encoding="utf-8")
            review_path.write_text(json.dumps(review) + "\n", encoding="utf-8")
            marker_path.write_text(json.dumps(_marker(1)) + "\n", encoding="utf-8")

            report = join_chess_evidence_files(
                reconciliation_draft=draft_path,
                fen_labels=fen_path,
                fen_review_rows=review_path,
                marker_labels=marker_path,
                source_pdf=pdf_path,
                output_dir=output_path,
                source_profile="fixed-edition",
            )
            persisted = (output_path / "chess_evidence_coverage_report.json").read_text(
                encoding="utf-8"
            )
            markdown = (output_path / "chess_evidence_coverage_report.md").read_text(
                encoding="utf-8"
            )

        self.assertNotIn(source_sha, persisted)
        self.assertNotIn(str(root), persisted)
        self.assertNotIn(source_sha, markdown)
        self.assertEqual(sum(report["fen_label_status_counts"].values()), 1)
        self.assertEqual(sum(report["marker_label_status_counts"].values()), 1)


if __name__ == "__main__":
    unittest.main()
