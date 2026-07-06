from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_side_to_move_trust_audit import (
    build_side_to_move_diagnostic_report,
    side_to_move_diagnostic_html,
    side_to_move_diagnostic_markdown,
)


class ChessSideToMoveTrustAuditTests(unittest.TestCase):
    def test_audit_classifies_required_blocker_chain(self) -> None:
        report = build_side_to_move_diagnostic_report(
            [
                {
                    "diagram_id": "source-missing",
                    "page": 1,
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "no-board",
                    "page": 2,
                    "image_path": "assets/page.png",
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "board-fail",
                    "page": 3,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "fail",
                    "board_crop_fail_reason": ["contains_coordinates"],
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "no-search-zone",
                    "page": 4,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "no-marker-bbox",
                    "page": 5,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "no-marker-crop",
                    "page": 6,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "marker-fail",
                    "page": 7,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/marker-fail.png",
                    "marker_crop_quality": "fail",
                    "marker_crop_fail_reason": ["marker_cut_off"],
                    "side_marker_status": "trusted_marker",
                    "side_to_move": "w",
                },
                {
                    "diagram_id": "conflict",
                    "page": 8,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/conflict.png",
                    "marker_crop_quality": "pass",
                    "side_marker_status": "marker_conflict",
                    "side_to_move": "unknown",
                },
                {
                    "diagram_id": "ambiguous",
                    "page": 9,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/ambiguous.png",
                    "marker_crop_quality": "pass",
                    "side_marker_status": "ambiguous_marker",
                },
                {
                    "diagram_id": "classifier-missing",
                    "page": 10,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/missing.png",
                    "marker_crop_quality": "pass",
                    "side_marker_status": "marker_missing",
                },
                {
                    "diagram_id": "trusted-not-propagated",
                    "page": 11,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/trusted.png",
                    "marker_crop_quality": "pass",
                    "side_marker_status": "trusted_marker",
                    "side_to_move": "unknown",
                },
                {
                    "diagram_id": "full-fen-blocked",
                    "page": 12,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/full.png",
                    "marker_crop_quality": "pass",
                    "side_marker_status": "trusted_marker",
                    "side_to_move": "b",
                    "full_fen_status": "FEN_REVIEW_REQUIRED",
                },
                {
                    "diagram_id": "trusted",
                    "page": 13,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "marker_search_zones": {"top": [0, 0, 120, 20]},
                    "selected_marker_zone": "top",
                    "marker_bbox": [70, 2, 88, 18],
                    "side_marker_crop_path": "review/chess_fen/two_crop/trusted-ok.png",
                    "marker_crop_quality": "pass",
                    "side_marker_symbol": "\u25bc",
                    "side_marker_status": "trusted_marker",
                    "side_to_move": "b",
                    "full_fen_status": "FEN_MACHINE_ACCEPTED",
                },
            ],
            source_gate={"status": "source_artifact_missing"},
        )

        by_id = {item["diagram_id"]: item for item in report["items"]}
        self.assertEqual(by_id["source-missing"]["primary_blocker"], "source_artifact_missing")
        self.assertEqual(by_id["no-board"]["primary_blocker"], "board_bbox_missing")
        self.assertEqual(by_id["board-fail"]["primary_blocker"], "board_crop_quality_fail")
        self.assertEqual(by_id["no-search-zone"]["primary_blocker"], "marker_search_zone_missing")
        self.assertEqual(by_id["no-marker-bbox"]["primary_blocker"], "marker_bbox_not_found")
        self.assertEqual(by_id["no-marker-crop"]["primary_blocker"], "marker_crop_not_generated")
        self.assertEqual(by_id["marker-fail"]["primary_blocker"], "marker_crop_quality_fail")
        self.assertEqual(by_id["conflict"]["primary_blocker"], "marker_classifier_conflict")
        self.assertEqual(by_id["ambiguous"]["primary_blocker"], "marker_classifier_ambiguous")
        self.assertEqual(by_id["classifier-missing"]["primary_blocker"], "marker_classifier_missing")
        self.assertEqual(by_id["trusted-not-propagated"]["primary_blocker"], "trusted_marker_not_propagated")
        self.assertEqual(by_id["full-fen-blocked"]["primary_blocker"], "full_fen_gate_blocked")
        self.assertEqual(by_id["trusted"]["primary_blocker"], "no_blocker_trusted")
        self.assertEqual(by_id["trusted"]["detected_marker_symbol"], "\u25bc")
        self.assertEqual(by_id["trusted"]["side_to_move_detected"], "b")
        for item in report["items"]:
            self.assertTrue(item["primary_blocker"])
            self.assertTrue(item["next_action"])
        self.assertEqual(report["summary"]["diagram_count"], 13)
        self.assertEqual(report["summary"]["side_unknown_count"], 10)
        self.assertEqual(report["summary"]["marker_search_zone_coverage_count"], 9)
        self.assertEqual(report["summary"]["marker_bbox_detection_count"], 8)
        self.assertEqual(report["summary"]["marker_crop_generation_count"], 7)
        self.assertEqual(report["summary"]["marker_crop_not_generated_count"], 1)
        self.assertEqual(report["summary"]["trusted_marker_count"], 2)
        self.assertIn("marker_classifier_missing", report["summary"]["by_primary_blocker"])

    def test_audit_counts_runtime_search_bbox_without_full_zone_map(self) -> None:
        report = build_side_to_move_diagnostic_report(
            [
                {
                    "diagram_id": "runtime-search-bbox",
                    "page": 1,
                    "board_bbox": [10, 10, 90, 90],
                    "board_crop_quality": "pass",
                    "side_marker_search_bbox": [90, 4, 120, 96],
                    "side_marker_status": "marker_missing",
                }
            ]
        )

        self.assertEqual(report["summary"]["marker_search_zone_coverage_count"], 1)
        self.assertEqual(report["summary"]["marker_search_zone_coverage_rate"], 1.0)
        self.assertEqual(report["items"][0]["primary_blocker"], "marker_bbox_not_found")

    def test_markdown_and_html_include_top_blockers_and_samples(self) -> None:
        report = build_side_to_move_diagnostic_report(
            [
                {
                    "diagram_id": "p010_d03",
                    "page": 10,
                    "board_bbox": [1, 2, 101, 102],
                    "board_crop_quality": "pass",
                    "marker_search_zone_preview_bbox": [0, 0, 120, 20],
                    "side_marker_status": "marker_missing",
                }
            ]
        )

        markdown = side_to_move_diagnostic_markdown(report)
        html = side_to_move_diagnostic_html(report)

        self.assertIn("Top Blockers", markdown)
        self.assertIn("p010_d03@p10", markdown)
        self.assertIn("marker_bbox_not_found", markdown)
        self.assertIn("Why Side To Move Is Not Trusted", html)
        self.assertIn("marker_bbox_not_found", html)

    def test_artifact_root_checks_real_crop_existence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop = root / "review" / "chess_fen" / "two_crop" / "marker.png"
            crop.parent.mkdir(parents=True)
            crop.write_bytes(b"marker")
            report = build_side_to_move_diagnostic_report(
                [
                    {
                        "diagram_id": "existing-crop",
                        "page": 1,
                        "board_bbox": [1, 2, 101, 102],
                        "board_crop_quality": "pass",
                        "marker_search_zones": {"top": [0, 0, 120, 20]},
                        "marker_bbox": [10, 0, 20, 10],
                        "side_marker_crop_path": "review/chess_fen/two_crop/marker.png",
                        "marker_crop_quality": "pass",
                        "side_marker_status": "trusted_marker",
                        "side_to_move": "w",
                    }
                ],
                artifact_root=root,
            )

        self.assertTrue(report["items"][0]["side_marker_crop_exists"])
        self.assertEqual(report["items"][0]["primary_blocker"], "no_blocker_trusted")

    def test_report_is_json_serializable(self) -> None:
        report = build_side_to_move_diagnostic_report([])
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertIn("why_side_to_move_not_trusted", encoded)


if __name__ == "__main__":
    unittest.main()
