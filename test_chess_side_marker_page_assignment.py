from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw

import chess_study_export
from chess_study_export import _attach_pdf_side_marker_evidence_to_study_diagrams
from pymupdf_chess_extractor import (
    _scan_chess_page_marker_pipeline,
    _scan_chess_two_crop_review_artifacts,
)


REQUIRED_ASSIGNMENT_FIELDS = {
    "marker_candidate_id",
    "marker_candidate_bbox",
    "marker_candidate_crop_path",
    "marker_candidate_features",
    "marker_candidate_class",
    "marker_candidate_confidence",
    "marker_assignment_status",
    "marker_assignment_confidence",
    "marker_assignment_runner_up_margin",
    "marker_assignment_rejected_reasons",
}


def _page_with_two_boards() -> tuple[Image.Image, list[dict[str, object]]]:
    image = Image.new("RGB", (800, 500), "white")
    draw = ImageDraw.Draw(image)
    boards: list[dict[str, object]] = [
        {"diagram_id": "left", "bbox": [80, 120, 280, 320]},
        {"diagram_id": "right", "bbox": [480, 120, 680, 320]},
    ]
    for board in boards:
        x0, y0, x1, y1 = [int(value) for value in board["bbox"]]  # type: ignore[index]
        draw.rectangle((x0, y0, x1, y1), outline="black", width=3)
        for step in range(1, 8):
            x = round(x0 + (x1 - x0) * step / 8)
            y = round(y0 + (y1 - y0) * step / 8)
            draw.line((x, y0, x, y1), fill="black", width=1)
            draw.line((x0, y, x1, y), fill="black", width=1)
    draw.polygon([(170, 85), (158, 110), (182, 110)], fill="black")
    draw.polygon([(570, 85), (558, 110), (582, 110)], outline="black", width=3)
    return image, boards


class ChessSideMarkerPageAssignmentTests(unittest.TestCase):
    def test_review_candidate_gets_crop_before_classifier_trust(self) -> None:
        image, boards = _page_with_two_boards()
        ambiguous = {
            "status": "side_to_move_marker_local_ambiguous",
            "shape": "unclear_triangle",
            "side": "",
            "confidence": 0.31,
            "classifier_version": "synthetic",
        }
        with patch(
            "pymupdf_chess_extractor.classify_scan_chess_side_marker_crop",
            return_value=ambiguous,
        ):
            pipeline = _scan_chess_page_marker_pipeline(
                image,
                boards[:1],
                page_number=1,
            )

        self.assertGreaterEqual(pipeline["marker_candidate_count"], 1)
        candidate = pipeline["candidates"][0]
        self.assertTrue(REQUIRED_ASSIGNMENT_FIELDS.issubset(candidate))
        self.assertEqual(candidate["marker_candidate_class"], "unclear_triangle")
        self.assertNotEqual(candidate["marker_candidate_classifier_status"], "trusted_marker")
        self.assertTrue(candidate["marker_candidate_crop_path"])
        crop_file = next(
            item
            for item in pipeline["files"]
            if item["path"] == candidate["marker_candidate_crop_path"]
        )
        self.assertGreater(len(crop_file["data"]), 100)

        assignment = pipeline["assignments"][0]
        with (
            patch(
                "pymupdf_chess_extractor._scan_chess_best_marker_zone_candidate",
                side_effect=AssertionError("per-board candidate detection must not run"),
            ),
            patch(
                "pymupdf_chess_extractor.classify_scan_chess_side_marker_crop",
                return_value=ambiguous,
            ),
        ):
            fields, files = _scan_chess_two_crop_review_artifacts(
                image,
                filename="left.png",
                board_bbox=boards[0]["bbox"],
                side_marker_bbox=None,
                marker_assignment=assignment,
            )

        self.assertEqual(fields["marker_candidate_id"], candidate["marker_candidate_id"])
        self.assertTrue(fields["side_marker_crop_path"])
        self.assertEqual(fields["marker_crop_quality"], "fail")
        self.assertTrue(any(item["path"] == fields["side_marker_crop_path"] for item in files))

    def test_two_boards_receive_distinct_candidates_with_global_cost_matrix(self) -> None:
        image, boards = _page_with_two_boards()

        pipeline = _scan_chess_page_marker_pipeline(image, boards, page_number=4)

        assignments = pipeline["assignments"]
        assigned_ids = [
            item["marker_candidate_id"]
            for item in assignments
            if item["marker_assignment_status"] != "unassigned"
        ]
        self.assertEqual(len(assignments), 2)
        self.assertEqual(len(assigned_ids), 2)
        self.assertEqual(len(set(assigned_ids)), 2)
        self.assertEqual(pipeline["summary"]["duplicate_marker_ownership_count"], 0)
        self.assertEqual(pipeline["summary"]["marker_candidate_assignment_rate"], 1.0)
        self.assertTrue(all(item["marker_assignment_runner_up_margin"] > 0 for item in assignments))
        self.assertEqual(len(pipeline["cost_matrix"]), 2)
        self.assertEqual(
            {item["diagram_id"]: item["marker_candidate_class"] for item in assignments},
            {"left": "filled_triangle", "right": "outline_triangle"},
        )

    def test_shared_marker_is_owned_once_and_ambiguous_ownership_stays_review_only(self) -> None:
        image = Image.new("RGB", (700, 450), "white")
        draw = ImageDraw.Draw(image)
        boards = [
            {"diagram_id": "left", "bbox": [100, 140, 300, 340]},
            {"diagram_id": "right", "bbox": [320, 140, 520, 340]},
        ]
        draw.polygon([(310, 190), (298, 215), (322, 215)], fill="black")

        pipeline = _scan_chess_page_marker_pipeline(image, boards, page_number=2)

        owned = [
            item
            for item in pipeline["assignments"]
            if item.get("marker_candidate_id")
        ]
        self.assertEqual(len(pipeline["candidates"]), 1)
        self.assertEqual(len(owned), 1)
        self.assertEqual(len({item["marker_candidate_id"] for item in owned}), 1)
        self.assertEqual(pipeline["summary"]["duplicate_marker_ownership_count"], 0)
        self.assertEqual(owned[0]["marker_assignment_status"], "needs_review_ambiguous_ownership")
        self.assertIn(
            "ownership_margin_too_small",
            owned[0]["marker_assignment_rejected_reasons"],
        )

    def test_study_pipeline_runs_detection_once_for_multi_board_page(self) -> None:
        image, boards = _page_with_two_boards()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "page.png"
            image.save(image_path)
            pdf_path = root / "study.pdf"
            document = fitz.open()
            page = document.new_page(width=image.width, height=image.height)
            page.insert_image(page.rect, filename=str(image_path))
            document.save(pdf_path)
            document.close()
            diagrams = [
                {
                    "diagram_id": str(board["diagram_id"]),
                    "page": 1,
                    "pixel_bbox": [
                        board["bbox"][0],  # type: ignore[index]
                        board["bbox"][1],  # type: ignore[index]
                        board["bbox"][2] - board["bbox"][0],  # type: ignore[index,operator]
                        board["bbox"][3] - board["bbox"][1],  # type: ignore[index,operator]
                    ],
                    "bbox": list(board["bbox"]),
                    "side_to_move": "unknown",
                    "side_to_move_status": "unknown",
                    "side_to_move_evidence": "none",
                    "warnings": ["side_to_move_inferred"],
                    "placement": "4k3/8/8/8/8/8/8/4K3",
                    "fen": "",
                    "status": "needs_review",
                }
                for board in boards
            ]

            with patch(
                "chess_study_export._scan_chess_page_marker_pipeline",
                wraps=chess_study_export._scan_chess_page_marker_pipeline,
            ) as page_pipeline:
                summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
                    pdf_path,
                    diagrams,
                    root / "out",
                    dpi=72,
                    min_confidence=0.5,
                )

            report = json.loads(
                (root / "out" / "reports" / "chess_fen" / "page_marker_assignment.json").read_text(
                    encoding="utf-8"
                )
            )
            candidate_files_exist = all(
                (root / "out" / item["marker_candidate_crop_path"]).is_file()
                for item in diagrams
            )

        page_pipeline.assert_called_once()
        self.assertEqual(summary["page_marker_detection_run_count"], 1)
        self.assertEqual(summary["duplicate_marker_ownership_count"], 0)
        self.assertEqual(report["summary"]["marker_candidate_assigned_count"], 2)
        self.assertEqual(len({item["marker_candidate_id"] for item in diagrams}), 2)
        self.assertTrue(all(REQUIRED_ASSIGNMENT_FIELDS.issubset(item) for item in diagrams))
        self.assertTrue(candidate_files_exist)


if __name__ == "__main__":
    unittest.main()
