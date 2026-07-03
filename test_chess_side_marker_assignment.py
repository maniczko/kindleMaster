from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import fitz
from PIL import Image, ImageDraw

from chess_auto_flow import build_auto_chess_flow_artifacts
from chess_study_export import _attach_pdf_side_marker_evidence_to_study_diagrams
from chess_fen_hardening import machine_accept_fen, machine_accept_placement
from chess_position_recognizer import ChessFenResult, summarize_chess_fen_results
from chess_side_marker_learning import (
    REVIEW_ONLY_POLICY,
    build_side_marker_learning_artifacts,
    side_marker_learning_review_html,
)
from converter import ConversionConfig, chess_fen_html_attrs, chess_side_marker_html
from pymupdf_chess_extractor import (
    ScanChessSideToMoveEvidence,
    _apply_scan_chess_side_to_move_context_evidence,
    _apply_scan_chess_two_crop_quality_gate,
    _chess_diagram_record_from_image,
    _infer_scan_chess_side_to_move_marker_evidence,
    _scan_chess_board_crop_quality,
    _scan_chess_marker_crop_quality,
    _scan_chess_marker_search_zones,
    _scan_chess_side_marker_probe_payloads,
    _scan_chess_two_crop_review_artifacts,
    classify_scan_chess_side_marker_crop,
    extract_scanned_chess_pdf_with_support,
)


VALID_FEN = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
VALID_PLACEMENT = VALID_FEN.split()[0]


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_image_pdf(path: Path, image: Image.Image) -> None:
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    doc = fitz.open()
    try:
        page = doc.new_page(width=image.width, height=image.height)
        page.insert_image(fitz.Rect(0, 0, image.width, image.height), stream=payload.getvalue())
        doc.save(path)
    finally:
        doc.close()


def _draw_checkerboard(draw: ImageDraw.ImageDraw, bbox: list[float], *, outline_width: int = 2) -> None:
    x0, y0, x1, y1 = [int(round(value)) for value in bbox]
    side = min(x1 - x0, y1 - y0)
    cell = side / 8.0
    for row in range(8):
        for col in range(8):
            shade = "#d1d5db" if (row + col) % 2 else "#ffffff"
            left = int(round(x0 + col * cell))
            top = int(round(y0 + row * cell))
            right = int(round(x0 + (col + 1) * cell))
            bottom = int(round(y0 + (row + 1) * cell))
            draw.rectangle((left, top, right, bottom), fill=shade)
    for index in range(9):
        pos = int(round(x0 + index * cell))
        draw.line((pos, y0, pos, y0 + side), fill="black", width=1)
        pos_y = int(round(y0 + index * cell))
        draw.line((x0, pos_y, x0 + side, pos_y), fill="black", width=1)
    draw.rectangle((x0, y0, x0 + side, y0 + side), outline="black", width=outline_width)


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

    def test_marker_search_zones_follow_board_bbox_contract(self) -> None:
        board_bbox = [100.0, 120.0, 260.0, 280.0]
        zones = _scan_chess_marker_search_zones(board_bbox, (420, 420))

        self.assertEqual(set(zones), {"top", "right", "bottom", "left"})
        self.assertEqual(zones["top"], [90.0, 90.0, 270.0, 120.0])
        self.assertEqual(zones["right"], [260.0, 110.0, 290.0, 290.0])
        self.assertEqual(zones["bottom"], [90.0, 280.0, 270.0, 310.0])
        self.assertEqual(zones["left"], [70.0, 110.0, 100.0, 290.0])

    def test_two_crop_artifacts_separate_board_marker_and_search_preview(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        draw.rectangle(board_bbox, outline="black", width=3)
        draw.polygon([(308, 222), (336, 222), (322, 250)], fill="black")

        fields, files = _scan_chess_two_crop_review_artifacts(
            page,
            filename="diagram-1.png",
            board_bbox=board_bbox,
            side_marker_bbox=[302.0, 214.0, 342.0, 260.0],
        )
        paths = {str(item.get("path") or "") for item in files}

        self.assertEqual(fields["board_crop_quality"], "pass")
        self.assertEqual(fields["marker_crop_quality"], "pass")
        self.assertEqual(fields["side_to_move_detected"], "black")
        self.assertEqual(fields["selected_marker_zone"], "right")
        self.assertEqual(set(fields["marker_search_zones"]), {"top", "right", "bottom", "left"})
        self.assertEqual(fields["side_marker_review_crop_kind"], "detected_marker_bbox")
        self.assertFalse(fields["manual_review_required"])
        self.assertIn(fields["board_crop_path"], paths)
        self.assertIn(fields["side_marker_crop_path"], paths)
        self.assertIn(fields["side_marker_search_crop_path"], paths)

    def test_search_zone_outline_triangle_writes_tight_marker_crop(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        _draw_checkerboard(draw, board_bbox)
        draw.line([(324, 188), (306, 224), (342, 224), (324, 188)], fill="black", width=4, joint="curve")

        fields, files = _scan_chess_two_crop_review_artifacts(
            page,
            filename="outline-search.png",
            board_bbox=board_bbox,
            side_marker_bbox=None,
        )
        file_by_path = {str(item.get("path") or ""): item.get("data") for item in files}
        marker_png = Image.open(io.BytesIO(file_by_path[fields["side_marker_crop_path"]]))

        self.assertEqual(fields["marker_crop_quality"], "pass")
        self.assertEqual(fields["side_to_move_detected"], "white")
        self.assertEqual(fields["side_marker_review_crop_kind"], "detected_marker_bbox")
        self.assertEqual(fields["marker_search_zone_preview_path"], fields["side_marker_search_crop_path"])
        self.assertNotEqual(fields["marker_crop_bbox"], fields["marker_search_zone_preview_bbox"])
        self.assertLess(marker_png.width, 64)
        self.assertLess(marker_png.height, 64)

    def test_search_zone_filled_triangle_writes_tight_marker_crop(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        _draw_checkerboard(draw, board_bbox)
        draw.polygon([(324, 188), (306, 224), (342, 224)], fill="black")

        fields, _files = _scan_chess_two_crop_review_artifacts(
            page,
            filename="filled-search.png",
            board_bbox=board_bbox,
            side_marker_bbox=None,
        )

        self.assertEqual(fields["marker_crop_quality"], "pass")
        self.assertEqual(fields["side_to_move_detected"], "black")
        self.assertEqual(fields["selected_marker_zone"], "right")
        self.assertFalse(fields["manual_review_required"])

    def test_corner_marker_tight_crop_is_not_cut_off(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        _draw_checkerboard(draw, board_bbox)
        draw.polygon([(318, 154), (304, 184), (332, 184)], fill="black")

        fields, _files = _scan_chess_two_crop_review_artifacts(
            page,
            filename="corner-marker.png",
            board_bbox=board_bbox,
            side_marker_bbox=None,
        )

        self.assertEqual(fields["marker_crop_quality"], "pass")
        self.assertNotIn("marker_cut_off", fields["marker_crop_fail_reason"])
        self.assertEqual(fields["side_to_move_detected"], "black")

    def test_tight_board_crop_quality_passes_for_clean_8x8_board(self) -> None:
        page = Image.new("RGB", (320, 320), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [80.0, 60.0, 240.0, 220.0]
        _draw_checkerboard(draw, board_bbox)

        quality = _scan_chess_board_crop_quality(page, board_bbox)

        self.assertEqual(quality["decision"], "pass")
        self.assertEqual(quality["reasons"], [])
        self.assertAlmostEqual(quality["ratio"], 1.0)

    def test_board_crop_quality_rejects_coordinates_and_marker_context(self) -> None:
        page = Image.new("RGB", (430, 340), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 70.0, 260.0, 230.0]
        _draw_checkerboard(draw, board_bbox)
        draw.text((105, 236), "a b c d e f g h", fill="black")
        draw.text((82, 78), "8\n7\n6\n5\n4\n3\n2\n1", fill="black")
        draw.polygon([(276, 112), (302, 112), (289, 142)], fill="black")

        quality = _scan_chess_board_crop_quality(page, [76.0, 48.0, 322.0, 258.0])

        self.assertEqual(quality["decision"], "fail")
        self.assertIn("too_much_margin", quality["reasons"])
        self.assertIn("contains_coordinates", quality["reasons"])
        self.assertIn("contains_marker", quality["reasons"])

    def test_board_crop_quality_rejects_caption_text_context(self) -> None:
        page = Image.new("RGB", (340, 340), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [90.0, 60.0, 250.0, 220.0]
        _draw_checkerboard(draw, board_bbox)
        draw.text((94, 252), "Ex. 14", fill="black")

        quality = _scan_chess_board_crop_quality(page, [80.0, 48.0, 264.0, 286.0])

        self.assertEqual(quality["decision"], "fail")
        self.assertIn("too_much_margin", quality["reasons"])
        self.assertIn("contains_text", quality["reasons"])

    def test_board_crop_quality_rejects_neighbor_diagram_context(self) -> None:
        page = Image.new("RGB", (430, 300), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [80.0, 70.0, 240.0, 230.0]
        _draw_checkerboard(draw, board_bbox)
        _draw_checkerboard(draw, [270.0, 82.0, 390.0, 202.0], outline_width=1)

        quality = _scan_chess_board_crop_quality(page, [72.0, 58.0, 398.0, 238.0])

        self.assertEqual(quality["decision"], "fail")
        self.assertIn("too_much_margin", quality["reasons"])
        self.assertIn("contains_neighbor_diagram", quality["reasons"])

    def test_two_crop_artifacts_write_board_crop_from_tight_bbox_not_context(self) -> None:
        page = Image.new("RGB", (430, 340), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 70.0, 260.0, 230.0]
        context_bbox = [76.0, 48.0, 322.0, 284.0]
        _draw_checkerboard(draw, board_bbox)
        draw.text((105, 236), "a b c d e f g h", fill="black")
        draw.polygon([(276, 112), (302, 112), (289, 142)], fill="black")

        fields, files = _scan_chess_two_crop_review_artifacts(
            page,
            filename="diagram-context.png",
            board_bbox=context_bbox,
            side_marker_bbox=None,
        )
        file_by_path = {str(item.get("path") or ""): item.get("data") for item in files}
        board_png = Image.open(io.BytesIO(file_by_path[fields["board_crop_path"]]))

        self.assertEqual(fields["raw_board_candidate_bbox"], context_bbox)
        self.assertNotEqual(fields["board_bbox"], fields["raw_board_candidate_bbox"])
        self.assertEqual(fields["tight_board_bbox"], fields["board_bbox"])
        self.assertEqual(fields["board_crop_quality"], "pass")
        self.assertTrue(str(fields["debug_context_crop_path"]).endswith("_context.png"))
        self.assertLess(max(board_png.size), 190)
        self.assertEqual(board_png.width, board_png.height)

    def test_board_crop_quality_rejects_fragmentary_and_non_square_crops(self) -> None:
        page = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(page)
        _draw_checkerboard(draw, [0.0, 10.0, 130.0, 140.0])

        quality = _scan_chess_board_crop_quality(page, [0.0, 10.0, 130.0, 120.0])

        self.assertEqual(quality["decision"], "fail")
        self.assertIn("not_square", quality["reasons"])
        self.assertIn("cell_size_mismatch", quality["reasons"])
        self.assertIn("board_cut_off", quality["reasons"])

    def test_marker_crop_quality_blocks_edge_strip_and_multiple_markers(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        draw.rectangle(board_bbox, outline="black", width=3)
        draw.line((300, 170, 300, 370), fill="black", width=4)
        edge_quality = _scan_chess_marker_crop_quality(page, [296.0, 170.0, 306.0, 370.0], board_bbox)

        multi = Image.new("RGB", (180, 100), "white")
        multi_draw = ImageDraw.Draw(multi)
        multi_draw.line([(42, 18), (20, 70), (64, 70), (42, 18)], fill="black", width=4)
        multi_draw.polygon([(132, 18), (110, 70), (154, 70)], fill="black")
        page.paste(multi, (40, 20))
        multi_quality = _scan_chess_marker_crop_quality(page, [40.0, 20.0, 220.0, 120.0], board_bbox)

        self.assertEqual(edge_quality["decision"], "fail")
        self.assertIn("mostly_board_edge", edge_quality["reasons"])
        self.assertEqual(multi_quality["decision"], "fail")
        self.assertIn("multiple_candidates", multi_quality["reasons"])

    def test_marker_crop_quality_blocks_rank_numbers_file_letters_and_thin_strip(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        _draw_checkerboard(draw, board_bbox)
        for index, label in enumerate("87654321"):
            draw.text((82, 174 + index * 23), label, fill="black")
        draw.text((108, 383), "a b c d e f g h", fill="black")
        draw.line((302, 170, 302, 370), fill="black", width=3)

        rank_quality = _scan_chess_marker_crop_quality(page, [78.0, 170.0, 98.0, 370.0], board_bbox)
        file_quality = _scan_chess_marker_crop_quality(page, [100.0, 380.0, 300.0, 404.0], board_bbox)
        thin_quality = _scan_chess_marker_crop_quality(page, [300.0, 170.0, 306.0, 370.0], board_bbox)

        self.assertEqual(rank_quality["decision"], "fail")
        self.assertIn("mostly_rank_numbers", rank_quality["reasons"])
        self.assertEqual(file_quality["decision"], "fail")
        self.assertIn("mostly_file_letters", file_quality["reasons"])
        self.assertEqual(thin_quality["decision"], "fail")
        self.assertIn("too_narrow", thin_quality["reasons"])

    def test_multiple_search_zone_candidates_stay_manual_review(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = [100.0, 170.0, 300.0, 370.0]
        _draw_checkerboard(draw, board_bbox)
        draw.polygon([(324, 188), (306, 224), (342, 224)], fill="black")
        draw.line([(324, 290), (306, 326), (342, 326), (324, 290)], fill="black", width=4, joint="curve")

        fields, _files = _scan_chess_two_crop_review_artifacts(
            page,
            filename="multi-search.png",
            board_bbox=board_bbox,
            side_marker_bbox=None,
        )

        self.assertTrue(fields["manual_review_required"])
        self.assertEqual(fields["marker_crop_quality"], "fail")
        self.assertIn("multiple_candidates", fields["marker_crop_fail_reason"])
        self.assertIsNone(fields["side_to_move_detected"])

    def test_crop_quality_gate_blocks_trusted_side_when_marker_crop_fails(self) -> None:
        payload = {
            "fen": VALID_FEN.replace(" w ", " b "),
            "full_fen": VALID_FEN.replace(" w ", " b "),
            "placement": VALID_PLACEMENT,
            "confidence": 0.99,
            "side_to_move": "b",
            "side_to_move_status": "explicit",
            "side_to_move_evidence": "marker",
            "warnings": ["side_to_move_marker_detected"],
            "requires_review": False,
            "board_detected": True,
        }
        fields = {
            "board_crop_quality": "pass",
            "marker_crop_quality": "fail",
            "marker_crop_fail_reason": ["mostly_board_edge"],
        }

        gated = _apply_scan_chess_two_crop_quality_gate(payload, fields)

        self.assertEqual(gated["fen"], "")
        self.assertTrue(gated["requires_review"])
        self.assertEqual(gated["side_to_move"], "unknown")
        self.assertEqual(gated["side_marker_status"], "marker_missing")
        self.assertIn("marker_crop_quality_failed", gated["warnings"])

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
                    "side_to_move_status": "inferred",
                    "side_to_move_evidence": "inferred",
                    "warnings": ["side_to_move_inferred", "side_to_move_marker_probes_checked"],
                },
                {
                    "fen": VALID_FEN,
                    "requires_review": False,
                    "board_crop_path": "review/chess_fen/two_crop/d2_board.png",
                    "side_marker_crop_path": "review/chess_fen/two_crop/d2_marker.png",
                    "debug_overlay_path": "review/chess_fen/two_crop/d2_overlay.png",
                    "side_marker_status": "trusted_marker",
                    "side_to_move": "w",
                    "warnings": ["side_to_move_marker_probes_checked"],
                },
            ]
        )

        self.assertEqual(summary["diagram_count"], 2)
        self.assertEqual(summary["board_crop_count"], 2)
        self.assertEqual(summary["side_marker_crop_count"], 1)
        self.assertEqual(summary["debug_overlay_count"], 2)
        self.assertEqual(summary["side_marker_probe_checked_count"], 2)
        self.assertEqual(summary["trusted_marker_count"], 1)
        self.assertEqual(summary["marker_missing_count"], 1)
        self.assertEqual(summary["side_to_move_inferred_count"], 1)
        self.assertEqual(summary["side_unknown_count"], 1)

    def test_pdf_side_marker_crop_evidence_reaches_study_diagram_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            page = Image.new("RGB", (420, 440), "white")
            draw = ImageDraw.Draw(page)
            draw.rectangle((100, 170, 300, 370), outline="black", width=3)
            draw.polygon([(304, 222), (334, 222), (319, 250)], fill="black")
            pdf_path = root / "marker.pdf"
            _write_image_pdf(pdf_path, page)
            diagrams = [
                {
                    "diagram_id": "p001_d01",
                    "page": 1,
                    "pixel_bbox": [100, 170, 200, 200],
                    "fen_candidate": VALID_FEN,
                    "confidence": 0.99,
                    "fen_confidence": 0.99,
                    "warnings": ["side_to_move_inferred"],
                    "status": "needs_review",
                }
            ]

            summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                pdf_path,
                diagrams,
                out,
                dpi=72,
                min_confidence=0.90,
            )

            record = diagrams[0]
            marker_path_exists = (out / record["side_marker_crop_path"]).is_file()
            board_path_exists = (out / record["board_crop_path"]).is_file()
            overlay_path_exists = (out / record["debug_overlay_path"]).is_file()
            report = json.loads((out / "reports" / "chess_fen" / "side_marker_assignment.json").read_text(encoding="utf-8"))
            report_html = (out / "reports" / "chess_fen" / "side_marker_assignment.html").read_text(encoding="utf-8")
            metrics = json.loads((out / "reports" / "chess_fen" / "two_crop_quality_metrics.json").read_text(encoding="utf-8"))
            metrics_md = (out / "reports" / "chess_fen" / "two_crop_quality_metrics.md").read_text(encoding="utf-8")
            blockers = json.loads((out / "reports" / "chess_fen" / "side_marker_blocker_attribution.json").read_text(encoding="utf-8"))
            blockers_md = (out / "reports" / "chess_fen" / "side_marker_blocker_attribution.md").read_text(encoding="utf-8")

        self.assertEqual(record["side_to_move"], "b")
        self.assertEqual(record["side_to_move_status"], "explicit")
        self.assertEqual(record["side_to_move_evidence"], "marker")
        self.assertEqual(record["side_marker_symbol"], "\u25bc")
        self.assertEqual(record["side_marker_status"], "trusted_marker")
        self.assertEqual(record["status"], "accepted")
        self.assertTrue(record["fen"].endswith(" b - - 0 1"))
        self.assertTrue(marker_path_exists)
        self.assertTrue(board_path_exists)
        self.assertTrue(overlay_path_exists)
        self.assertEqual(summary["side_marker_crop_count"], 1)
        self.assertEqual(summary["trusted_marker_count"], 1)
        self.assertEqual(report["summary"]["trusted_marker_count"], 1)
        self.assertIn("trusted_marker", report_html)
        self.assertEqual(metrics["summary"]["board_crop_count"], 1)
        self.assertEqual(metrics["summary"]["side_marker_crop_count"], 1)
        self.assertEqual(metrics["summary"]["trusted_marker_count"], 1)
        self.assertEqual(metrics["accuracy"]["status"], "TRAINING_DATA_GAP")
        self.assertIn("TRAINING_DATA_GAP", metrics_md)
        self.assertEqual(blockers["schema"], "kindlemaster.chess_fen.side_marker_blocker_attribution.v1")
        self.assertEqual(blockers["summary"]["diagram_count"], 1)
        self.assertIn("Side Marker Blocker Attribution", blockers_md)

    def test_side_marker_probe_covers_outside_top_right_corner(self) -> None:
        page = Image.new("RGB", (420, 440), "white")
        draw = ImageDraw.Draw(page)
        board_bbox = (100.0, 170.0, 300.0, 370.0)
        draw.rectangle(board_bbox, outline="black", width=3)
        draw.polygon([(312, 176), (342, 176), (327, 205)], fill="black")

        payloads = _scan_chess_side_marker_probe_payloads(page, board_bbox)
        by_role = {str(payload.get("role")): payload for payload in payloads}
        outside = by_role["top_right_outside"]
        evidence = _infer_scan_chess_side_to_move_marker_evidence(page, board_bbox)
        trusted_roles = {
            str(payload.get("role"))
            for payload in (evidence.marker_candidates if evidence is not None else ())
            if payload.get("detected_side") == "b"
        }

        self.assertEqual(outside["marker_classifier_status"], "trusted_marker")
        self.assertEqual(outside["detected_side"], "b")
        self.assertEqual(outside["conflict_group"], "corner")
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.side, "b")
        self.assertIn("side_to_move_marker_detected", evidence.warnings)
        self.assertIn("top_right_outside", trusted_roles)

    def test_scanned_runtime_reports_side_marker_probe_counts_and_artifact_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "runtime-side-markers.pdf"
            page = Image.new("RGB", (620, 360), "white")
            draw = ImageDraw.Draw(page)
            first_board = (80, 130, 230, 280)
            second_board = (330, 130, 480, 280)
            for board in (first_board, second_board):
                draw.rectangle(board, outline="black", width=3)
                step = (board[2] - board[0]) / 8
                for index in range(1, 8):
                    x = round(board[0] + step * index)
                    y = round(board[1] + step * index)
                    draw.line((x, board[1], x, board[3]), fill="black", width=1)
                    draw.line((board[0], y, board[2], y), fill="black", width=1)
            draw.line([(242, 144), (274, 144), (258, 174), (242, 144)], fill="black", width=4)
            draw.polygon([(492, 144), (524, 144), (508, 174)], fill="black")
            _write_image_pdf(pdf_path, page)

            page_candidates = [
                {
                    "page_num": 0,
                    "candidates": [
                        {"bbox": first_board, "confidence": 0.98},
                        {"bbox": second_board, "confidence": 0.98},
                    ],
                }
            ]

            recognition = ChessFenResult(
                fen="",
                placement=VALID_PLACEMENT,
                full_fen=VALID_FEN,
                confidence=0.98,
                side_to_move="w",
                side_to_move_status="inferred",
                side_to_move_evidence="inferred",
                warnings=["side_to_move_inferred"],
                requires_review=True,
                board_detected=True,
                method="image-template-board",
            )

            with mock.patch("pymupdf_chess_extractor._scan_chess_page_candidates", return_value=page_candidates), mock.patch(
                "pymupdf_chess_extractor._scan_chess_selective_ocr_pages",
                return_value={"pages": {}, "summary": {}},
            ), mock.patch("pymupdf_chess_extractor._scan_chess_front_matter_metadata", return_value={}), mock.patch(
                "pymupdf_chess_extractor._resolve_chess_piece_template_dir",
                return_value="templates",
            ), mock.patch("pymupdf_chess_extractor.load_piece_templates", return_value={"probe": []}), mock.patch(
                "pymupdf_chess_extractor._recognize_scan_chess_candidate_bbox",
                return_value=recognition,
            ), mock.patch(
                "pymupdf_chess_extractor._scan_chess_confirm_final_rendered_crop_recognition",
                side_effect=lambda result, *args, **kwargs: result,
            ), mock.patch(
                "pymupdf_chess_extractor._scan_chess_apply_verified_crop_label",
                side_effect=lambda result, *args, **kwargs: result,
            ):
                result = extract_scanned_chess_pdf_with_support(
                    str(pdf_path),
                    ConversionConfig(chess_fen_apply_side_marker=True, chess_fen_min_confidence=0.90),
                )

            chess_fen_summary = result["metadata"]["chess_fen"]
            scan_audit = result["audit"]["scan_chess"]
            diagram_artifact = next(item for item in result["extra_artifacts"] if item["key"] == "chess_diagrams")
            zip_artifact = next(item for item in result["extra_artifacts"] if item["key"] == "chess_fen_two_crop_review_artifacts")
            diagrams_payload = json.loads(diagram_artifact["data"].decode("utf-8"))
            records = diagrams_payload["records"]
            with zipfile.ZipFile(io.BytesIO(zip_artifact["data"])) as archive:
                names = set(archive.namelist())

        self.assertEqual(chess_fen_summary["diagram_count"], 2)
        self.assertEqual(chess_fen_summary["side_marker_probe_checked_count"], 2)
        self.assertEqual(chess_fen_summary["side_marker_crop_count"], 2)
        self.assertEqual(chess_fen_summary["trusted_marker_count"], 2)
        self.assertEqual(chess_fen_summary["side_to_move_inferred_count"], 0)
        self.assertEqual(scan_audit["trusted_marker_count"], 2)
        self.assertEqual({record["side_marker_status"] for record in records}, {"trusted_marker"})
        self.assertEqual({record["side_to_move"] for record in records}, {"w", "b"})
        self.assertTrue(any(name.endswith("_board.png") for name in names))
        self.assertTrue(any(name.endswith("_marker.png") for name in names))
        self.assertTrue(any(name.endswith("_overlay.png") for name in names))

    def test_pdf_side_marker_conflict_remains_review_only_with_crop_trace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "out"
            page = Image.new("RGB", (420, 440), "white")
            draw = ImageDraw.Draw(page)
            draw.rectangle((100, 170, 300, 370), outline="black", width=3)
            draw.polygon([(210, 132), (254, 132), (232, 164)], fill="black")
            draw.line([(130, 132), (174, 132), (152, 164), (130, 132)], fill="black", width=4)
            pdf_path = root / "marker-conflict.pdf"
            _write_image_pdf(pdf_path, page)
            diagrams = [
                {
                    "diagram_id": "p001_d01",
                    "page": 1,
                    "pixel_bbox": [100, 170, 200, 200],
                    "fen_candidate": VALID_FEN,
                    "confidence": 0.99,
                    "fen_confidence": 0.99,
                    "warnings": ["side_to_move_inferred"],
                    "status": "needs_review",
                }
            ]

            summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                pdf_path,
                diagrams,
                out,
                dpi=72,
                min_confidence=0.90,
            )

            record = diagrams[0]
            marker_path_exists = (out / record["side_marker_crop_path"]).is_file()

        self.assertEqual(record["status"], "needs_review")
        self.assertEqual(record["side_to_move"], "unknown")
        self.assertEqual(record["side_marker_status"], "marker_conflict")
        self.assertIn("side_to_move_marker_local_conflict", record["warnings"])
        self.assertTrue(marker_path_exists)
        self.assertEqual(summary["marker_conflict_count"], 1)
        self.assertEqual(summary["trusted_marker_count"], 0)

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

    def test_auto_flow_writes_two_crop_quality_metrics_with_training_data_gap(self) -> None:
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
                                    "board_crop_path": "review/chess_fen/two_crop/diagram-1_board.png",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/diagram-1_marker.png",
                                    "debug_overlay_path": "review/chess_fen/two_crop/diagram-1_overlay.png",
                                    "fen": VALID_FEN,
                                    "placement": VALID_PLACEMENT,
                                    "full_fen": VALID_FEN,
                                    "confidence": 0.99,
                                    "warnings": [],
                                    "method": "image-template-board",
                                    "side_to_move": "w",
                                    "side_to_move_status": "explicit",
                                    "side_to_move_evidence": "trusted_marker",
                                    "side_marker_symbol": "\u25b3",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_source": "marker_crop",
                                    "side_marker_confidence": 0.94,
                                },
                                {
                                    "diagram_id": "diagram-2",
                                    "page": 1,
                                    "image_path": "assets/diagrams/diagram-2.png",
                                    "board_crop_path": "review/chess_fen/two_crop/diagram-2_board.png",
                                    "debug_overlay_path": "review/chess_fen/two_crop/diagram-2_overlay.png",
                                    "placement": VALID_PLACEMENT,
                                    "confidence": 0.99,
                                    "warnings": [],
                                    "method": "image-template-board",
                                    "side_marker_status": "marker_missing",
                                },
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )

            flow_payload = build_auto_chess_flow_artifacts(out)
            report_path = out / "reports" / "chess_fen" / "two_crop_quality_metrics.json"
            markdown_path = out / "reports" / "chess_fen" / "two_crop_quality_metrics.md"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        summary = report["summary"]
        self.assertEqual(summary["diagram_count"], 2)
        self.assertEqual(summary["board_crop_count"], 2)
        self.assertEqual(summary["side_marker_crop_count"], 1)
        self.assertEqual(summary["trusted_marker_count"], 1)
        self.assertEqual(summary["marker_missing_count"], 1)
        self.assertEqual(summary["placement_accepted_count"], 2)
        self.assertEqual(summary["full_fen_accepted_count"], 1)
        self.assertEqual(summary["blocked_by_marker_count"], 1)
        self.assertEqual(summary["blocked_by_placement_count"], 0)
        probe_quality = report["probe_quality_before_after"]
        self.assertEqual(probe_quality["status"], "TRAINING_DATA_GAP")
        self.assertEqual(probe_quality["after"]["marker_missing_count"], 1)
        self.assertEqual(probe_quality["after"]["marker_conflict_count"], 0)
        self.assertEqual(probe_quality["after"]["trusted_marker_count"], 1)
        self.assertEqual(probe_quality["after"]["full_fen_accepted_count"], 1)
        self.assertEqual(report["accuracy"]["status"], "TRAINING_DATA_GAP")
        self.assertIn("Probe Before/After", markdown)
        self.assertIn("TRAINING_DATA_GAP", markdown)
        self.assertIn("two_crop_quality_metrics", flow_payload["artifacts"])

    def test_auto_flow_uses_export_diagrams_when_book_model_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            out = Path(temp_dir)
            _write_json(out / "data" / "book.json", {"pages": [], "pgn_records": []})
            _write_json(
                out / "chess_diagrams.json",
                {
                    "status": "ok",
                    "page_count": 1,
                    "diagram_count": 1,
                    "diagrams": [
                        {
                            "diagram_id": "p010_d01",
                            "page": 10,
                            "board_crop_path": "review/chess_fen/two_crop/p010_d01_board.png",
                            "side_marker_crop_path": "review/chess_fen/two_crop/p010_d01_marker.png",
                            "debug_overlay_path": "review/chess_fen/two_crop/p010_d01_overlay.png",
                            "placement": VALID_PLACEMENT,
                            "full_fen": VALID_FEN,
                            "side_to_move": "b",
                            "side_marker_symbol": "\u25bc",
                            "side_marker_status": "trusted_marker",
                            "side_marker_confidence": 0.88,
                            "placement_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
                            "full_fen_status": "FEN_REVIEW_REQUIRED",
                        }
                    ],
                },
            )

            flow_payload = build_auto_chess_flow_artifacts(out)
            metrics = json.loads((out / "reports" / "chess_fen" / "two_crop_quality_metrics.json").read_text(encoding="utf-8"))
            queue = json.loads((out / "reports" / "chess_fen" / "side_marker_learning_queue.json").read_text(encoding="utf-8"))
            review_html = (out / "reports" / "chess_fen" / "side_marker_learning_review.html").read_text(encoding="utf-8")

        self.assertEqual(flow_payload["summary"]["diagrams_total"], 1)
        self.assertEqual(metrics["summary"]["diagram_count"], 1)
        self.assertEqual(metrics["summary"]["side_marker_crop_count"], 1)
        self.assertEqual(queue["items"][0]["diagram_id"], "p010_d01")
        self.assertIn("p010_d01_marker.png", review_html)

    def test_auto_flow_writes_side_marker_blocker_attribution(self) -> None:
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
                                    "diagram_id": "not-propagated",
                                    "page": 1,
                                    "image_path": "assets/diagrams/not-propagated.png",
                                    "board_crop_path": "review/chess_fen/two_crop/not-propagated_board.png",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/not-propagated_marker.png",
                                    "side_to_move": "unknown",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_symbol": "\u25b3",
                                    "placement_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                                    "full_fen_status": "FEN_REVIEW_REQUIRED",
                                },
                                {
                                    "diagram_id": "missing-marker",
                                    "page": 2,
                                    "image_path": "assets/diagrams/missing-marker.png",
                                    "board_crop_path": "review/chess_fen/two_crop/missing-marker_board.png",
                                    "side_to_move": "unknown",
                                    "side_marker_status": "marker_missing",
                                    "placement_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                                    "full_fen_status": "FEN_REVIEW_REQUIRED",
                                },
                                {
                                    "diagram_id": "placement-blocked",
                                    "page": 3,
                                    "image_path": "assets/diagrams/placement-blocked.png",
                                    "board_crop_path": "review/chess_fen/two_crop/placement-blocked_board.png",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/placement-blocked_marker.png",
                                    "side_to_move": "w",
                                    "side_marker_status": "trusted_marker",
                                    "side_marker_symbol": "\u25b3",
                                    "placement_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
                                    "full_fen_status": "FEN_REVIEW_REQUIRED",
                                },
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )

            flow_payload = build_auto_chess_flow_artifacts(out)
            report_path = out / "reports" / "chess_fen" / "side_marker_blocker_attribution.json"
            markdown_path = out / "reports" / "chess_fen" / "side_marker_blocker_attribution.md"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        by_id = {item["diagram_id"]: item for item in report["items"]}
        counts = report["summary"]["by_primary_side_marker_blocker"]
        self.assertEqual(by_id["not-propagated"]["primary_side_marker_blocker"], "trusted_marker_not_propagated")
        self.assertEqual(by_id["missing-marker"]["primary_side_marker_blocker"], "marker_classifier_missing")
        self.assertEqual(by_id["placement-blocked"]["primary_side_marker_blocker"], "placement_blocks_full_fen")
        self.assertEqual(counts["trusted_marker_not_propagated"], 1)
        self.assertEqual(counts["marker_classifier_missing"], 1)
        self.assertEqual(counts["placement_blocks_full_fen"], 1)
        self.assertEqual(report["summary"]["placement_blocks_full_fen_count"], 1)
        self.assertIn("side_marker_blocker_attribution", flow_payload["artifacts"])
        self.assertIn("trusted_marker_not_propagated", markdown)

    def test_auto_flow_writes_two_crop_benchmark_gap_without_ai_label_promotion(self) -> None:
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
                                    "diagram_id": "marker-label",
                                    "page": 1,
                                    "board_crop_path": "review/chess_fen/two_crop/marker-label_board.png",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/marker-label_marker.png",
                                    "expected_side_to_move": "w",
                                    "human_verified": True,
                                    "verification_source": "human",
                                },
                                {
                                    "diagram_id": "placement-label",
                                    "page": 1,
                                    "board_crop_path": "review/chess_fen/two_crop/placement-label_board.png",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/placement-label_marker.png",
                                    "expected_placement": VALID_PLACEMENT,
                                    "human_verified": True,
                                    "verification_source": "human",
                                },
                                {
                                    "diagram_id": "ai-only-label",
                                    "page": 1,
                                    "board_crop_path": "review/chess_fen/two_crop/ai-only-label_board.png",
                                    "side_marker_crop_path": "review/chess_fen/two_crop/ai-only-label_marker.png",
                                    "expected_side_to_move": "b",
                                    "expected_placement": VALID_PLACEMENT,
                                    "verification_source": "openai",
                                    "verified_by": "gpt-review",
                                    "verified_at": "2026-06-27T00:00:00Z",
                                    "label_status": "verified",
                                },
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )

            flow_payload = build_auto_chess_flow_artifacts(out)
            report_path = out / "reports" / "chess_fen" / "two_crop_benchmark_seed.json"
            markdown_path = out / "reports" / "chess_fen" / "two_crop_benchmark_seed.md"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        summary = report["summary"]
        self.assertEqual(report["status"], "TRAINING_DATA_GAP")
        self.assertFalse(report["manifest"]["created"])
        self.assertEqual(report["manifest"]["items"], [])
        self.assertEqual(summary["usable_record_count"], 2)
        self.assertEqual(summary["manifest_record_count"], 0)
        self.assertEqual(summary["marker_label_count"], 1)
        self.assertEqual(summary["placement_label_count"], 1)
        self.assertEqual(summary["both_label_count"], 0)
        self.assertEqual(summary["ai_only_excluded_count"], 1)
        self.assertEqual(summary["label_sources"], {"human": 2})
        self.assertEqual(len(report["available_records"]), 2)
        self.assertIn("TRAINING_DATA_GAP", markdown)
        self.assertIn("two_crop_benchmark_seed", flow_payload["artifacts"])

    def test_side_marker_learning_queue_prioritizes_blockers_and_keeps_labels_review_only(self) -> None:
        records = [
            {
                "diagram_id": "trusted",
                "page": 3,
                "board_crop_path": "review/chess_fen/two_crop/trusted_board.png",
                "side_marker_crop_path": "review/chess_fen/two_crop/trusted_marker.png",
                "side_to_move": "w",
                "side_marker_status": "trusted_marker",
            },
            {
                "diagram_id": "missing",
                "page": 1,
                "board_crop_path": "review/chess_fen/two_crop/missing_board.png",
                "side_marker_crop_path": "",
                "side_to_move": "unknown",
                "side_marker_status": "marker_missing",
            },
            {
                "diagram_id": "conflict",
                "page": 2,
                "board_crop_path": "review/chess_fen/two_crop/conflict_board.png",
                "side_marker_crop_path": "review/chess_fen/two_crop/conflict_marker.png",
                "side_to_move": "unknown",
                "side_marker_status": "marker_conflict",
            },
        ]
        blockers = {
            "items": [
                {"diagram_id": "trusted", "primary_side_marker_blocker": "no_side_marker_blocker"},
                {"diagram_id": "missing", "primary_side_marker_blocker": "marker_crop_not_generated"},
                {"diagram_id": "conflict", "primary_side_marker_blocker": "marker_classifier_conflict"},
            ]
        }

        payload = build_side_marker_learning_artifacts(records, blocker_report=blockers)
        queue = payload["queue"]["items"]
        template = payload["manual_label_template"][0]
        report = payload["learning_report"]

        self.assertEqual([row["diagram_id"] for row in queue], ["missing", "conflict", "trusted"])
        self.assertEqual(template["label_status"], "needs_manual_marker")
        self.assertFalse(template["human_verified"])
        self.assertFalse(template["accepted_for_runtime"])
        self.assertFalse(template["accepted_for_corpus"])
        self.assertEqual(template["policy"], REVIEW_ONLY_POLICY)
        self.assertEqual(report["status"], "TRAINING_DATA_GAP")

    def test_side_marker_learning_report_turns_human_labels_into_classifier_findings(self) -> None:
        records = [
            {
                "diagram_id": "missed",
                "page": 1,
                "side_to_move": "unknown",
                "side_marker_status": "marker_missing",
                "primary_side_marker_blocker": "marker_classifier_missing",
            },
            {
                "diagram_id": "conflict",
                "page": 2,
                "side_to_move": "unknown",
                "side_marker_status": "marker_conflict",
                "primary_side_marker_blocker": "marker_classifier_conflict",
            },
            {
                "diagram_id": "wrong-side",
                "page": 3,
                "side_to_move": "w",
                "side_marker_status": "trusted_marker",
                "primary_side_marker_blocker": "no_side_marker_blocker",
            },
        ]
        labels = [
            {
                "diagram_id": "missed",
                "manual_visible_marker": "outline_triangle",
                "manual_side_to_move": "w",
                "label_status": "verified",
                "human_verified": True,
            },
            {
                "diagram_id": "conflict",
                "manual_visible_marker": "filled_triangle",
                "manual_side_to_move": "b",
                "label_status": "verified",
                "verification_source": "human_visual",
            },
            {
                "diagram_id": "wrong-side",
                "manual_visible_marker": "filled_triangle",
                "manual_side_to_move": "b",
                "label_status": "verified",
                "human_verified": True,
            },
            {
                "diagram_id": "ai-only",
                "manual_visible_marker": "outline_triangle",
                "manual_side_to_move": "w",
                "label_status": "verified",
                "verification_source": "openai",
            },
        ]

        payload = build_side_marker_learning_artifacts(records, manual_labels=labels, min_verified_labels=2)
        report = payload["learning_report"]
        actions = {item["action"] for item in report["suggestions"]}

        self.assertEqual(report["status"], "READY_FOR_RULE_CALIBRATION")
        self.assertEqual(report["summary"]["usable_manual_label_count"], 3)
        self.assertEqual(report["summary"]["rejected_manual_label_count"], 1)
        self.assertEqual(report["confusion"]["false_negative_marker_count"], 1)
        self.assertEqual(report["confusion"]["conflict_resolvable_count"], 1)
        self.assertEqual(report["confusion"]["trusted_wrong_side_count"], 1)
        self.assertIn("expand marker probe coverage", actions)
        self.assertIn("improve marker-region arbitration", actions)
        self.assertIn("tighten trusted-marker promotion", actions)
        self.assertTrue(all(row["accepted_for_runtime"] is False for row in report["comparisons"]))
        self.assertEqual(report["label_rejections"][0]["code"], "ai_only_label_ignored")

    def test_auto_flow_writes_side_marker_learning_artifacts(self) -> None:
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
                                    "diagram_id": "marker-missing",
                                    "page": 1,
                                    "image_path": "assets/diagrams/missing.png",
                                    "board_crop_path": "review/chess_fen/two_crop/missing_board.png",
                                    "debug_overlay_path": "review/chess_fen/two_crop/missing_overlay.png",
                                    "placement": VALID_PLACEMENT,
                                    "confidence": 0.99,
                                    "side_to_move": "unknown",
                                    "side_marker_status": "marker_missing",
                                    "placement_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
                                    "full_fen_status": "FEN_REVIEW_REQUIRED",
                                }
                            ],
                            "pgn_records": [],
                            "text_blocks": [],
                        }
                    ],
                    "pgn_records": [],
                },
            )

            flow_payload = build_auto_chess_flow_artifacts(out)
            queue = json.loads((out / "reports" / "chess_fen" / "side_marker_learning_queue.json").read_text(encoding="utf-8"))
            template = (out / "reports" / "chess_fen" / "side_marker_learning_labels_template.jsonl").read_text(encoding="utf-8")
            report = json.loads((out / "reports" / "chess_fen" / "side_marker_learning_report.json").read_text(encoding="utf-8"))
            review_html = (out / "reports" / "chess_fen" / "side_marker_learning_review.html").read_text(encoding="utf-8")

        self.assertIn("side_marker_learning_report", flow_payload["artifacts"])
        self.assertEqual(queue["items"][0]["diagram_id"], "marker-missing")
        self.assertEqual(queue["items"][0]["policy"], REVIEW_ONLY_POLICY)
        self.assertIn('"accepted_for_runtime": false', template)
        self.assertEqual(report["status"], "TRAINING_DATA_GAP")
        self.assertIn("Oznaczanie markerów ruchu", review_html)
        self.assertIn("Co widać w cropie markera?", review_html)
        self.assertIn("△ pusty trójkąt", review_html)
        self.assertIn("▼ pełny trójkąt", review_html)
        self.assertIn("Kopiuj wszystkie JSONL", review_html)
        self.assertIn("Pobierz labels.jsonl", review_html)
        self.assertIn('"accepted_for_runtime": false', review_html)

    def test_side_marker_learning_review_empty_state_explains_next_action(self) -> None:
        payload = build_side_marker_learning_artifacts([])

        review_html = side_marker_learning_review_html(payload)

        self.assertIn("Brak diagramów do oznaczenia", review_html)
        self.assertIn("python kindlemaster.py process", review_html)
        self.assertIn("side_marker_learning_queue.jsonl", review_html)
        self.assertIn("two_crop_quality_metrics.json", review_html)
        self.assertIn("invalid choice: process", review_html)
        self.assertNotIn("No side-marker learning rows found", review_html)

    def test_side_marker_learning_review_empty_state_surfaces_missing_pdf(self) -> None:
        payload = {
            **build_side_marker_learning_artifacts([]),
            "source_pdf": r"C:\ścieżka\do\pliku.pdf",
            "stage_results": [
                {
                    "name": "run_chess_study_export",
                    "status": "failed",
                    "failure_reasons": [r"FileNotFoundError: no such file: 'C:\ścieżka\do\pliku.pdf'"],
                }
            ],
        }

        review_html = side_marker_learning_review_html(payload)

        self.assertIn("Problem z wejściem: PDF nie został znaleziony", review_html)
        self.assertIn(r"C:\ścieżka\do\pliku.pdf", review_html)
        self.assertIn("system nie ma stron ani diagramów do pokazania", review_html)
        self.assertIn("INPUT_PDF_MISSING", review_html)
        self.assertIn("Dlaczego status to INPUT_PDF_MISSING", review_html)
        self.assertNotIn("Dlaczego status to TRAINING_DATA_GAP", review_html)

    def test_side_marker_learning_review_rows_are_ready_despite_fen_model_gap(self) -> None:
        payload = {
            **build_side_marker_learning_artifacts(
                [
                    {
                        "diagram_id": "p010_d01",
                        "page": 10,
                        "board_crop_path": "review/chess_fen/two_crop/p010_d01_board.png",
                        "side_marker_crop_path": "review/chess_fen/two_crop/p010_d01_marker.png",
                        "side_marker_status": "marker_missing",
                    }
                ]
            ),
            "stage_results": [
                {
                    "name": "recognize_fen_local",
                    "status": "needs_review",
                    "failure_reasons": ["model_missing"],
                }
            ],
        }

        review_html = side_marker_learning_review_html(payload)

        self.assertIn("MARKER_REVIEW_READY", review_html)
        self.assertIn("p010_d01_marker.png", review_html)
        self.assertNotIn("INPUT_EXTRACTION_BLOCKED", review_html)


if __name__ == "__main__":
    unittest.main()
