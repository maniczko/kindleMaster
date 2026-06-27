from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from chess_auto_flow import build_auto_chess_flow_artifacts
from chess_fen_hardening import machine_accept_fen, machine_accept_placement
from chess_position_recognizer import summarize_chess_fen_results
from converter import chess_fen_html_attrs, chess_side_marker_html
from pymupdf_chess_extractor import (
    ScanChessSideToMoveEvidence,
    _apply_scan_chess_side_to_move_context_evidence,
    _chess_diagram_record_from_image,
    classify_scan_chess_side_marker_crop,
)


VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
VALID_PLACEMENT = VALID_FEN.split()[0]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class ChessSideMarkerAssignmentTests(unittest.TestCase):
    def test_marker_crop_classifier_maps_outline_and_filled_triangles(self) -> None:
        outline = Image.new("L", (80, 80), "white")
        draw_outline = ImageDraw.Draw(outline)
        draw_outline.line([(40, 12), (18, 56), (62, 56), (40, 12)], fill="black", width=4, joint="curve")
        filled = Image.new("L", (80, 80), "white")
        draw_filled = ImageDraw.Draw(filled)
        draw_filled.polygon([(40, 12), (18, 56), (62, 56)], fill="black")

        outline_result = classify_scan_chess_side_marker_crop(outline)
        filled_result = classify_scan_chess_side_marker_crop(filled)

        self.assertEqual(outline_result["status"], "trusted_marker")
        self.assertEqual(outline_result["side"], "w")
        self.assertEqual(outline_result["symbol"], "\u25b3")
        self.assertEqual(filled_result["status"], "trusted_marker")
        self.assertEqual(filled_result["side"], "b")
        self.assertEqual(filled_result["symbol"], "\u25bc")

    def test_marker_crop_classifier_keeps_missing_and_conflict_in_review(self) -> None:
        blank = Image.new("L", (80, 80), "white")
        conflict = Image.new("L", (130, 80), "white")
        draw = ImageDraw.Draw(conflict)
        draw.line([(34, 12), (16, 56), (54, 56), (34, 12)], fill="black", width=4, joint="curve")
        draw.polygon([(94, 12), (76, 56), (114, 56)], fill="black")

        missing_result = classify_scan_chess_side_marker_crop(blank)
        conflict_result = classify_scan_chess_side_marker_crop(conflict)

        self.assertEqual(missing_result["status"], "marker_missing")
        self.assertEqual(missing_result["side"], "")
        self.assertEqual(conflict_result["status"], "side_to_move_marker_local_conflict")
        self.assertEqual(conflict_result["side"], "")

    def test_trusted_marker_metadata_flows_to_html_attrs_and_badge(self) -> None:
        payload = {
            "fen": VALID_FEN,
            "full_fen": VALID_FEN,
            "placement": VALID_PLACEMENT,
            "confidence": 0.99,
            "method": "image-template-board",
            "side_to_move": "w",
            "side_to_move_status": "inferred",
            "side_to_move_evidence": "inferred",
            "warnings": ["side_to_move_inferred"],
            "requires_review": True,
            "board_detected": True,
        }
        evidence = ScanChessSideToMoveEvidence(
            side="b",
            source="marker",
            raw_text="top_right",
            confidence=0.91,
            source_bbox=(10.0, 20.0, 30.0, 40.0),
            warnings=("side_to_move_marker_detected", "side_to_move_marker_local_assignment_used"),
            marker_candidates=(
                {
                    "role": "top_right",
                    "bbox": [10, 20, 30, 40],
                    "detected_side": "b",
                    "distance_to_board": 4.0,
                    "score": 900.0,
                },
            ),
        )

        updated = _apply_scan_chess_side_to_move_context_evidence(payload, evidence, min_confidence=0.90)
        attrs = chess_fen_html_attrs({"fen_result": updated, "fen": updated.get("fen"), "fen_confidence": updated.get("confidence")})
        badge = chess_side_marker_html({"fen_result": updated})

        self.assertEqual(updated["side_to_move"], "b")
        self.assertEqual(updated["side_marker_symbol"], "\u25bc")
        self.assertEqual(updated["side_marker_status"], "trusted_marker")
        self.assertEqual(updated["side_marker_source"], "marker")
        self.assertEqual(updated["side_marker_bbox"], [10.0, 20.0, 30.0, 40.0])
        self.assertEqual(updated["side_marker_confidence"], 0.91)
        self.assertIn('data-side-to-move="b"', attrs)
        self.assertIn('data-side-marker-symbol="\u25bc"', attrs)
        self.assertIn('data-side-marker-status="trusted_marker"', attrs)
        self.assertIn("\u25bc", badge)

    def test_machine_acceptance_blocks_inferred_side_but_allows_placement(self) -> None:
        candidate = {
            "source": "deterministic",
            "fen": VALID_FEN,
            "placement": VALID_PLACEMENT,
            "confidence": 0.99,
            "warnings": ["side_to_move_inferred"],
            "side_to_move_status": "inferred",
            "side_to_move_evidence": "inferred",
            "side_marker_status": "inferred_only",
        }

        full = machine_accept_fen(candidate, {"min_confidence": 0.90})
        placement = machine_accept_placement(candidate, {"min_confidence": 0.90})

        self.assertEqual(full["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertIn("side_to_move_inferred", {blocker["code"] for blocker in full["acceptance_blockers"]})
        self.assertEqual(placement["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
        self.assertEqual(placement["selected_placement"], VALID_PLACEMENT)

    def test_full_fen_gate_requires_accepted_placement_and_trusted_marker(self) -> None:
        candidate = {
            "source": "deterministic",
            "fen": VALID_FEN,
            "placement": VALID_PLACEMENT,
            "confidence": 0.99,
            "warnings": [],
            "side_to_move": "w",
            "side_to_move_status": "explicit",
            "side_to_move_evidence": "marker",
        }

        missing_marker = machine_accept_fen({**candidate, "side_marker_status": "marker_missing"}, {"min_confidence": 0.90})
        trusted_marker = machine_accept_fen({**candidate, "side_marker_status": "trusted_marker"}, {"min_confidence": 0.90})
        placement = machine_accept_placement({**candidate, "side_marker_status": "marker_missing"}, {"min_confidence": 0.90})

        self.assertEqual(missing_marker["runtime_status"], "FEN_REVIEW_REQUIRED")
        self.assertIn("full_fen_blocked_by_marker", {blocker["code"] for blocker in missing_marker["acceptance_blockers"]})
        self.assertEqual(placement["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")
        self.assertEqual(trusted_marker["runtime_status"], "FEN_MACHINE_ACCEPTED")
        self.assertEqual(trusted_marker["acceptance_trace"]["placement_gate"]["runtime_status"], "FEN_PLACEMENT_MACHINE_ACCEPTED")

    def test_diagram_record_exposes_review_safe_missing_marker(self) -> None:
        record = _chess_diagram_record_from_image(
            {
                "data": b"not-a-real-png-but-enough-for-data-uri",
                "extension": "png",
                "bbox": (1.0, 2.0, 101.0, 102.0),
                "fen_result": {
                    "fen": "",
                    "full_fen": VALID_FEN,
                    "placement": VALID_PLACEMENT,
                    "confidence": 0.96,
                    "method": "image-template-board",
                    "warnings": ["side_to_move_inferred", "side_to_move_marker_probes_checked"],
                    "side_to_move": "w",
                    "side_to_move_status": "inferred",
                    "side_to_move_evidence": "inferred",
                    "requires_review": True,
                    "board_detected": True,
                    "fen_suppressed_reason": "side_to_move_inferred",
                    "board_crop_path": "review/chess_fen/two_crop/diagram-1_board.png",
                    "side_marker_crop_path": "",
                    "debug_overlay_path": "review/chess_fen/two_crop/diagram-1_overlay.png",
                    "board_bbox": [1.0, 2.0, 101.0, 102.0],
                },
            },
            diagram_id="diagram-1",
            caption="Diagram 1",
            page_num=0,
        )

        self.assertEqual(record["side_marker_symbol"], "?")
        self.assertEqual(record["side_marker_status"], "inferred_only")
        self.assertEqual(record["fen_suppressed_reason"], "side_to_move_inferred")
        self.assertEqual(record["board_crop_path"], "review/chess_fen/two_crop/diagram-1_board.png")
        self.assertEqual(record["debug_overlay_path"], "review/chess_fen/two_crop/diagram-1_overlay.png")
        self.assertEqual(record["board_bbox"], [1.0, 2.0, 101.0, 102.0])
        self.assertFalse(record["strict_fen_side_evidence_trusted"])

    def test_fen_summary_counts_two_crop_artifacts_without_requiring_marker(self) -> None:
        summary = summarize_chess_fen_results(
            [
                {
                    "fen": "",
                    "requires_review": True,
                    "board_crop_path": "review/chess_fen/two_crop/d1_board.png",
                    "debug_overlay_path": "review/chess_fen/two_crop/d1_overlay.png",
                    "side_marker_status": "marker_missing",
                },
                {
                    "fen": VALID_FEN,
                    "requires_review": False,
                    "board_crop_path": "review/chess_fen/two_crop/d2_board.png",
                    "side_marker_crop_path": "review/chess_fen/two_crop/d2_marker.png",
                    "debug_overlay_path": "review/chess_fen/two_crop/d2_overlay.png",
                    "side_marker_status": "trusted_marker",
                },
            ]
        )

        self.assertEqual(summary["diagram_count"], 2)
        self.assertEqual(summary["board_crop_count"], 2)
        self.assertEqual(summary["side_marker_crop_count"], 1)
        self.assertEqual(summary["debug_overlay_count"], 2)

    def test_auto_flow_writes_side_marker_assignment_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(
                out / "data" / "book.json",
                {
                    "pages": [
                        {
                            "page": 1,
                            "diagrams": [
                                {
                                    "diagram_id": "diagram-1",
                                    "page": 1,
                                    "image_path": "assets/diagrams/diagram-1.png",
                                    "fen": VALID_FEN,
                                    "placement": VALID_PLACEMENT,
                                    "full_fen": VALID_FEN,
                                    "confidence": 0.99,
                                    "warnings": [],
                                    "side_to_move": "w",
                                    "side_to_move_status": "explicit",
                                    "side_to_move_evidence": "marker",
                                    "side_marker_symbol": "\u25b3",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_source": "marker",
                                    "side_marker_confidence": 0.94,
                                }
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )

            build_auto_chess_flow_artifacts(out)
            report_path = out / "reports" / "chess_fen" / "side_marker_assignment.json"
            html_path = out / "reports" / "chess_fen" / "side_marker_assignment.html"
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            html = html_path.read_text(encoding="utf-8")

        self.assertEqual(payload["summary"]["diagram_count"], 1)
        self.assertEqual(payload["summary"]["html_diagrams_with_visible_side_marker"], 1)
        self.assertEqual(payload["summary"]["trusted_marker_assignments"], 1)
        self.assertEqual(payload["items"][0]["side_marker_symbol"], "\u25b3")
        self.assertIn("trusted_marker", html)


if __name__ == "__main__":
    unittest.main()
