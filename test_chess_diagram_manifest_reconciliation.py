from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_diagram_manifest_reconciliation import (
    DRAFT_SCHEMA,
    reconcile_diagram_manifest_files,
    reconcile_diagram_records,
)


SOURCE_SHA = "a" * 64
OTHER_SHA = "b" * 64


def _row(
    diagram_id: str,
    *,
    page: int,
    bbox: list[float],
    fingerprint: str = "",
    legacy_id: str = "",
) -> dict[str, object]:
    row: dict[str, object] = {
        "diagram_id": diagram_id,
        "page": page,
        "normalized_bbox_xyxy": bbox,
        "source_document_sha256": SOURCE_SHA,
        "board_crop_path": f"assets/{diagram_id}.png",
    }
    if fingerprint:
        row["diagram_fingerprint"] = fingerprint
    if legacy_id:
        row["legacy_diagram_id"] = legacy_id
    return row


def _label(diagram_id: str, *, bbox_verified: bool = False) -> dict[str, object]:
    return {
        "diagram_id": diagram_id,
        "label_status": "verified",
        "human_verified": True,
        "manual_visible_marker": "outline_triangle",
        "manual_side_to_move": "w",
        "manual_marker_bbox": [0.8, 0.05, 0.9, 0.15] if bbox_verified else None,
        "marker_bbox_verified": bbox_verified,
        "verification_source": "human_visual",
        "verified_by": "reviewer",
        "verified_at": "2026-07-17T00:00:00Z",
    }


class DiagramManifestReconciliationTests(unittest.TestCase):
    def test_exact_fingerprint_and_one_to_one_bbox_are_resolved(self) -> None:
        exact = "dfp_" + "1" * 32
        result = reconcile_diagram_records(
            intake_rows=[
                _row("old-exact", page=1, bbox=[0.1, 0.1, 0.4, 0.4], fingerprint=exact),
                _row("old-bbox", page=2, bbox=[0.2, 0.2, 0.5, 0.5]),
            ],
            detected_rows=[
                _row("new-exact", page=1, bbox=[0.1, 0.1, 0.4, 0.4], fingerprint=exact),
                _row("new-bbox", page=2, bbox=[0.205, 0.205, 0.495, 0.495]),
            ],
            marker_labels=[_label("old-exact", bbox_verified=True)],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
        )

        self.assertEqual(result["report"]["counts"]["canonical_resolved"], 2)
        self.assertEqual(
            result["report"]["intake_status_counts"],
            {"exact_fingerprint": 1, "page_bbox_one_to_one": 1},
        )
        self.assertEqual(result["draft"]["schema"], DRAFT_SCHEMA)
        self.assertEqual(result["report"]["counts"]["marker_evidence_complete"], 1)
        self.assertFalse(result["report"]["acceptance_ready"])

    def test_split_and_merge_relations_never_auto_resolve(self) -> None:
        result = reconcile_diagram_records(
            intake_rows=[
                _row("old-split", page=1, bbox=[0.1, 0.1, 0.8, 0.8]),
                _row("old-merge-a", page=2, bbox=[0.1, 0.1, 0.4, 0.4]),
                _row("old-merge-b", page=2, bbox=[0.45, 0.45, 0.75, 0.75]),
            ],
            detected_rows=[
                _row("new-split-a", page=1, bbox=[0.12, 0.12, 0.42, 0.42]),
                _row("new-split-b", page=1, bbox=[0.46, 0.46, 0.76, 0.76]),
                _row("new-merge", page=2, bbox=[0.08, 0.08, 0.78, 0.78]),
            ],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
            bbox_iou_threshold=0.90,
        )

        self.assertEqual(result["report"]["counts"]["canonical_resolved"], 0)
        self.assertEqual(result["report"]["intake_status_counts"]["split_candidate"], 1)
        self.assertEqual(result["report"]["intake_status_counts"]["merge_candidate"], 2)

    def test_many_to_many_and_duplicate_id_fail_closed(self) -> None:
        result = reconcile_diagram_records(
            intake_rows=[
                _row("duplicate", page=1, bbox=[0.1, 0.1, 0.5, 0.5]),
                _row("duplicate", page=1, bbox=[0.11, 0.11, 0.51, 0.51]),
                _row("many-a", page=2, bbox=[0.1, 0.1, 0.6, 0.6]),
                _row("many-b", page=2, bbox=[0.11, 0.11, 0.61, 0.61]),
            ],
            detected_rows=[
                _row("new-a", page=2, bbox=[0.1, 0.1, 0.6, 0.6]),
                _row("new-b", page=2, bbox=[0.11, 0.11, 0.61, 0.61]),
            ],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
            bbox_iou_threshold=0.85,
        )

        self.assertEqual(result["report"]["intake_status_counts"]["duplicate_identity"], 2)
        self.assertEqual(result["report"]["intake_status_counts"]["ambiguous_many_to_many"], 2)
        self.assertEqual(result["report"]["counts"]["canonical_resolved"], 0)

    def test_low_iou_id_alias_is_review_only(self) -> None:
        result = reconcile_diagram_records(
            intake_rows=[_row("old-id", page=1, bbox=[0.1, 0.1, 0.3, 0.3])],
            detected_rows=[
                _row(
                    "new-id",
                    page=1,
                    bbox=[0.6, 0.6, 0.9, 0.9],
                    legacy_id="old-id",
                )
            ],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
        )

        self.assertEqual(result["report"]["intake_status_counts"], {"id_geometry_conflict": 1})
        self.assertEqual(result["report"]["counts"]["canonical_resolved"], 0)

    def test_low_iou_containment_is_an_explicit_review_conflict(self) -> None:
        result = reconcile_diagram_records(
            intake_rows=[_row("old", page=1, bbox=[0.1, 0.1, 0.8, 0.8])],
            detected_rows=[_row("new", page=1, bbox=[0.2, 0.2, 0.5, 0.5])],
            source_document_sha256=SOURCE_SHA,
            source_profile="fixed-edition",
        )

        self.assertEqual(
            result["report"]["intake_status_counts"],
            {"low_iou_containment_conflict": 1},
        )
        self.assertEqual(
            result["report"]["detected_status_counts"],
            {"low_iou_containment_conflict": 1},
        )
        self.assertEqual(result["report"]["counts"]["canonical_resolved"], 0)

    def test_source_mismatch_and_unverified_label_fail_closed(self) -> None:
        detected = [_row("new", page=1, bbox=[0.1, 0.1, 0.3, 0.3])]
        detected[0]["source_document_sha256"] = OTHER_SHA
        with self.assertRaisesRegex(ValueError, "detected_row.*source_sha256_mismatch"):
            reconcile_diagram_records(
                intake_rows=[_row("old", page=1, bbox=[0.1, 0.1, 0.3, 0.3])],
                detected_rows=detected,
                source_document_sha256=SOURCE_SHA,
                source_profile="fixed-edition",
            )

        label = _label("old")
        label["human_verified"] = False
        with self.assertRaisesRegex(ValueError, "verified_label_requires_human_verified"):
            reconcile_diagram_records(
                intake_rows=[_row("old", page=1, bbox=[0.1, 0.1, 0.3, 0.3])],
                detected_rows=[_row("new", page=1, bbox=[0.1, 0.1, 0.3, 0.3])],
                marker_labels=[label],
                source_document_sha256=SOURCE_SHA,
                source_profile="fixed-edition",
            )

    def test_file_report_is_redacted_and_every_row_has_one_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detected_path = root / "detected.json"
            intake_path = root / "intake.json"
            labels_path = root / "labels.jsonl"
            output_path = root / "reports"
            exact = "dfp_" + "3" * 32
            detected_path.write_text(
                json.dumps(
                    {
                        "source_document_sha256": SOURCE_SHA,
                        "diagrams": [
                            _row("new", page=1, bbox=[0.1, 0.1, 0.4, 0.4], fingerprint=exact),
                            _row("extra", page=2, bbox=[0.1, 0.1, 0.4, 0.4]),
                        ],
                    }
                ),
                encoding="utf-8",
            )
            intake_path.write_text(
                json.dumps(
                    {
                        "source": {"sha256": SOURCE_SHA},
                        "source_profile": "fixed-edition",
                        "diagrams": [
                            _row("old", page=1, bbox=[0.1, 0.1, 0.4, 0.4], fingerprint=exact)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            labels_path.write_text(json.dumps(_label("old")) + "\n", encoding="utf-8")

            report = reconcile_diagram_manifest_files(
                detected_manifest=detected_path,
                intake_manifest=intake_path,
                marker_labels=labels_path,
                output_dir=output_path,
                source_profile="fixed-edition",
            )
            persisted = (output_path / "diagram_reconciliation_report.json").read_text(
                encoding="utf-8"
            )
            markdown = (output_path / "diagram_reconciliation_report.md").read_text(
                encoding="utf-8"
            )

        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(
            report["detected_status_counts"],
            {"detector_only": 1, "exact_fingerprint": 1},
        )
        self.assertNotIn(SOURCE_SHA, persisted)
        self.assertNotIn(str(root), persisted)
        self.assertNotIn(SOURCE_SHA, markdown)
        self.assertEqual(sum(report["intake_status_counts"].values()), report["counts"]["intake"])
        self.assertEqual(
            sum(report["detected_status_counts"].values()),
            report["counts"]["detected"],
        )

    def test_file_reconciliation_rejects_manifest_profile_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            detected_path = root / "detected.json"
            intake_path = root / "intake.json"
            payload = {
                "source_document_sha256": SOURCE_SHA,
                "source_profile": "other-edition",
                "diagrams": [_row("one", page=1, bbox=[0.1, 0.1, 0.4, 0.4])],
            }
            detected_path.write_text(json.dumps(payload), encoding="utf-8")
            payload["source_profile"] = "fixed-edition"
            intake_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "source_profile_mismatch"):
                reconcile_diagram_manifest_files(
                    detected_manifest=detected_path,
                    intake_manifest=intake_path,
                    output_dir=root / "output",
                    source_profile="fixed-edition",
                )


if __name__ == "__main__":
    unittest.main()
