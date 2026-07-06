from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_auto_flow import build_auto_chess_flow_artifacts
from chess_side_to_move_evidence import (
    build_side_to_move_coverage_dashboard,
    resolve_side_to_move_evidence,
    side_to_move_coverage_dashboard_html,
    side_to_move_coverage_dashboard_markdown,
)


VALID_KINGS_FEN = "4k3/8/8/8/8/8/8/4K3 b - - 0 1"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ChessSideToMoveEvidenceTierTests(unittest.TestCase):
    def test_trusted_marker_allows_full_fen_side_gate(self) -> None:
        evidence = resolve_side_to_move_evidence(
            {
                "diagram_id": "trusted",
                "side_to_move": "b",
                "side_marker_status": "trusted_marker",
                "side_marker_confidence": 0.97,
                "board_crop_quality": "pass",
                "marker_crop_quality": "pass",
                "marker_bbox": [10, 20, 30, 40],
                "selected_marker_zone": "right",
                "marker_crop_quality_gate": {"decision": "pass", "component_count": 1, "reasons": []},
            }
        )

        self.assertEqual(evidence["side_to_move"], "b")
        self.assertEqual(evidence["side_to_move_source"], "trusted_marker")
        self.assertEqual(evidence["side_to_move_evidence_tier"], "trusted")
        self.assertTrue(evidence["full_fen_allowed"])
        self.assertEqual(evidence["full_fen_blocker"], "")

    def test_text_inferred_counts_as_coverage_but_blocks_full_fen(self) -> None:
        evidence = resolve_side_to_move_evidence(
            {
                "side_to_move": "w",
                "side_to_move_status": "inferred",
                "side_to_move_evidence": "caption_text",
                "side_to_move_confidence": 0.81,
                "side_marker_status": "marker_missing",
            }
        )

        self.assertEqual(evidence["side_to_move_source"], "text_inferred")
        self.assertEqual(evidence["side_to_move_evidence_tier"], "inferred")
        self.assertFalse(evidence["full_fen_allowed"])
        self.assertEqual(evidence["full_fen_blocker"], "not_trusted_side_to_move")

    def test_pgn_inferred_counts_as_coverage_but_blocks_full_fen(self) -> None:
        evidence = resolve_side_to_move_evidence(
            {
                "side_to_move": "b",
                "side_to_move_status": "inferred",
                "side_to_move_evidence": "pgn_inferred",
                "side_to_move_confidence": 0.72,
            }
        )

        self.assertEqual(evidence["side_to_move_source"], "pgn_inferred")
        self.assertEqual(evidence["side_to_move_evidence_tier"], "inferred")
        self.assertFalse(evidence["full_fen_allowed"])

    def test_human_verified_requires_explicit_policy_for_full_fen(self) -> None:
        record = {
            "manual_visible_marker": "outline_triangle",
            "human_verified": True,
            "verification_source": "human_visual",
        }

        default = resolve_side_to_move_evidence(record)
        allowed = resolve_side_to_move_evidence(record, allow_human_verified_full_fen=True)

        self.assertEqual(default["side_to_move"], "w")
        self.assertEqual(default["side_to_move_source"], "human_verified")
        self.assertEqual(default["side_to_move_evidence_tier"], "verified")
        self.assertFalse(default["full_fen_allowed"])
        self.assertEqual(default["full_fen_blocker"], "human_verified_policy_required")
        self.assertTrue(allowed["full_fen_allowed"])
        self.assertEqual(allowed["full_fen_blocker"], "")

    def test_unknown_has_no_coverage_and_blocks_full_fen(self) -> None:
        evidence = resolve_side_to_move_evidence({"side_marker_status": "marker_missing"})

        self.assertEqual(evidence["side_to_move"], "unknown")
        self.assertEqual(evidence["side_to_move_source"], "unknown")
        self.assertEqual(evidence["side_to_move_evidence_tier"], "unknown")
        self.assertFalse(evidence["full_fen_allowed"])

    def test_dashboard_separates_coverage_from_trusted_marker_rate(self) -> None:
        report = build_side_to_move_coverage_dashboard(
            [
                {
                    "diagram_id": "trusted",
                    "side_to_move": "b",
                    "side_marker_status": "trusted_marker",
                    "board_crop_quality": "pass",
                    "marker_crop_quality": "pass",
                    "marker_bbox": [10, 20, 30, 40],
                    "marker_crop_quality_gate": {"decision": "pass"},
                },
                {
                    "diagram_id": "text",
                    "side_to_move": "w",
                    "side_to_move_status": "inferred",
                    "side_to_move_evidence": "caption",
                },
                {
                    "diagram_id": "pgn",
                    "side_to_move": "b",
                    "side_to_move_status": "inferred",
                    "side_to_move_evidence": "pgn",
                },
                {
                    "diagram_id": "human",
                    "manual_visible_marker": "filled_triangle",
                    "human_verified": True,
                    "verification_source": "human_visual",
                },
                {"diagram_id": "unknown", "side_marker_status": "marker_missing"},
            ]
        )

        summary = report["summary"]
        self.assertEqual(summary["diagram_count"], 5)
        self.assertEqual(summary["side_to_move_coverage_count"], 4)
        self.assertEqual(summary["trusted_marker_count"], 1)
        self.assertEqual(summary["side_to_move_coverage_rate"], 0.8)
        self.assertEqual(summary["trusted_marker_rate"], 0.2)
        self.assertEqual(summary["human_verified_rate"], 0.2)
        self.assertEqual(summary["text_inferred_rate"], 0.2)
        self.assertEqual(summary["pgn_inferred_rate"], 0.2)
        self.assertEqual(summary["unknown_rate"], 0.2)
        self.assertIn("Coverage Dashboard", side_to_move_coverage_dashboard_markdown(report))
        self.assertIn("trusted_marker", side_to_move_coverage_dashboard_html(report))

    def test_auto_flow_writes_side_to_move_coverage_dashboard_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "schema": "kindlemaster.semantic_chess_html.v1",
                    "pages": [
                        {
                            "page": 1,
                            "text_blocks": [],
                            "diagrams": [
                                {
                                    "diagram_id": "p001_d001",
                                    "page": 1,
                                    "image_path": "assets/diagrams/p001_d001.png",
                                    "fen": VALID_KINGS_FEN,
                                    "confidence": 0.99,
                                    "warnings": [],
                                    "side_to_move": "b",
                                    "side_to_move_status": "explicit",
                                    "side_to_move_evidence": "marker",
                                    "side_marker_symbol": "\u25bc",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_confidence": 0.96,
                                    "board_crop_quality": "pass",
                                    "board_crop_quality_gate": {"decision": "pass", "reasons": []},
                                    "marker_crop_quality": "pass",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/p001_d001_marker.png",
                                    "marker_bbox": [10.0, 20.0, 30.0, 40.0],
                                    "selected_marker_zone": "right",
                                    "marker_crop_quality_gate": {"decision": "pass", "component_count": 1, "reasons": []},
                                }
                            ],
                            "pgn_records": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )
            _write_json(
                out / "reports" / "chess_quality_dashboard.json",
                {"pages": 1, "diagrams_total": 1, "fen_accepted": 1, "pgn_total": 0, "accepted_pgn": 0},
            )

            payload = build_auto_chess_flow_artifacts(out)

            dashboard_path = out / "reports" / "chess_fen" / "side_to_move_coverage_dashboard.json"
            dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
            two_crop = json.loads((out / "reports" / "chess_fen" / "two_crop_quality_metrics.json").read_text(encoding="utf-8"))
            self.assertTrue(dashboard_path.is_file())
            self.assertTrue((out / "reports" / "chess_fen" / "side_to_move_coverage_dashboard.md").is_file())
            self.assertTrue((out / "reports" / "chess_fen" / "side_to_move_coverage_dashboard.html").is_file())
            self.assertIn("side_to_move_coverage_dashboard", payload["artifacts"])
            self.assertEqual(dashboard["items"][0]["side_to_move_source"], "trusted_marker")
            self.assertEqual(dashboard["summary"]["trusted_marker_rate"], 1.0)
            self.assertEqual(two_crop["items"][0]["side_to_move_source"], "trusted_marker")
            self.assertEqual(two_crop["items"][0]["side_to_move_evidence_tier"], "trusted")
            self.assertTrue(two_crop["items"][0]["full_fen_allowed"])


if __name__ == "__main__":
    unittest.main()
