from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

import fitz

from scripts.chess_diagram_detection import (
    _build_board_detection_quality_records,
    _write_board_detection_quality_artifacts,
    detect_chess_diagrams,
)


class ChessDiagramDetectionTests(unittest.TestCase):
    def test_board_detection_quality_artifacts_summarize_synthetic_records(self) -> None:
        strict_records = [
            {
                "diagram_id": "accepted",
                "page": 1,
                "page_index": 0,
                "status": "accepted",
                "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "fen_candidate": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                "bbox": [1, 2, 30, 30],
                "bbox_xyxy": [1, 2, 31, 32],
                "pixel_bbox": [10, 20, 300, 300],
                "image_path": "accepted.webp",
                "image_href": "assets/diagrams/accepted.webp",
                "method": "synthetic",
                "grid_confidence": 0.9,
                "fen_confidence": 0.91,
                "confidence": 0.92,
                "warnings": [],
            },
            {
                "diagram_id": "review",
                "page": 2,
                "status": "needs_review",
                "fen": "",
                "fen_candidate": "",
                "reason": "fen_not_recognized",
                "warnings": ["board_grid_not_detected"],
            },
        ]
        low_confidence = [
            {
                "diagram_id": "low",
                "page": 3,
                "status": "needs_review",
                "reason": "review_only_low_confidence_candidate",
                "warnings": ["review_only_low_confidence_candidate"],
                "review_only": True,
            }
        ]

        rows = _build_board_detection_quality_records(strict_records, low_confidence)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["quality_gate_status"], "accepted_crop")
        self.assertEqual(rows[1]["quality_gate_status"], "needs_review")
        self.assertEqual(rows[1]["primary_quality_blocker"], "fen_not_recognized")
        self.assertEqual(rows[2]["candidate_kind"], "low_confidence_review")

        with tempfile.TemporaryDirectory() as temp_dir:
            result = _write_board_detection_quality_artifacts(Path(temp_dir), strict_records, low_confidence)
            payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
            jsonl_rows = Path(result["jsonl"]).read_text(encoding="utf-8").splitlines()

        self.assertEqual(payload["summary"]["total_candidates"], 3)
        self.assertEqual(payload["summary"]["accepted_crop_count"], 1)
        self.assertEqual(payload["summary"]["by_quality_gate_status"]["low_confidence_review"], 1)
        self.assertEqual(len(jsonl_rows), 3)

    def test_page_ranges_select_representative_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "pages.pdf"
            doc = fitz.open()
            for index in range(5):
                page = doc.new_page(width=180, height=120)
                page.insert_text((20, 40), f"Page {index + 1}", fontsize=10)
            doc.save(pdf_path)
            doc.close()

            manifest = detect_chess_diagrams(
                pdf_path,
                output_dir=root / "dist",
                dpi=72,
                pages=1,
                page_ranges="2-3,5",
                max_candidates_per_page=1,
                min_grid_confidence=0.99,
                template_dir="",
            )

        self.assertEqual(manifest["sampled_pages"], [2, 3, 5])
        self.assertEqual(manifest["page_ranges"], "2-3,5")
        self.assertEqual(manifest["diagram_count"], 0)

    def test_detects_reference_board_crop_as_review_diagram(self) -> None:
        crop_path = Path("reference_inputs/chess_fen/crops/fundamenty_1_1_scan_chess_p010_runtime_01.png")
        if not crop_path.is_file():
            self.skipTest("reference chess crop fixture is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "diagram.pdf"
            doc = fitz.open()
            page = doc.new_page(width=420, height=420)
            page.insert_image(fitz.Rect(50, 50, 370, 370), filename=str(crop_path))
            doc.save(pdf_path)
            doc.close()

            manifest = detect_chess_diagrams(
                pdf_path,
                output_dir=root / "dist",
                dpi=120,
                max_candidates_per_page=6,
                min_grid_confidence=0.30,
                template_dir="",
            )
            self.assertGreaterEqual(manifest["diagram_count"], 1)
            first = manifest["diagrams"][0]
            self.assertTrue(Path(first["image_path"]).is_file())
            review_dataset = manifest["review_dataset"]
            review_html = Path(review_dataset["html"])
            review_csv = Path(review_dataset["csv"])
            review_jsonl = Path(review_dataset["jsonl"])
            self.assertTrue(review_html.is_file())
            self.assertTrue(review_csv.is_file())
            self.assertTrue(review_jsonl.is_file())
            html_text = review_html.read_text(encoding="utf-8")
            csv_text = review_csv.read_text(encoding="utf-8")
            jsonl_text = review_jsonl.read_text(encoding="utf-8")

        self.assertEqual(first["page"], 1)
        self.assertEqual(first["status"], "needs_review")
        self.assertEqual(first["reason"], "fen_not_recognized")
        self.assertTrue(first["image_href"].endswith(".webp"))
        self.assertIn("correct_diagram", html_text)
        self.assertIn("false_positive", html_text)
        self.assertIn("cropped_diagram", html_text)
        self.assertIn("manual_label", csv_text)
        self.assertIn("candidate_type", csv_text)
        self.assertIn("thumbnail", csv_text)
        self.assertIn(first["diagram_id"], jsonl_text)

    def test_low_confidence_candidates_are_review_only(self) -> None:
        crop_path = Path("reference_inputs/chess_fen/crops/fundamenty_1_1_scan_chess_p010_runtime_01.png")
        if not crop_path.is_file():
            self.skipTest("reference chess crop fixture is not available")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "diagram.pdf"
            doc = fitz.open()
            page = doc.new_page(width=420, height=420)
            page.insert_image(fitz.Rect(50, 50, 370, 370), filename=str(crop_path))
            doc.save(pdf_path)
            doc.close()

            manifest = detect_chess_diagrams(
                pdf_path,
                output_dir=root / "dist",
                dpi=120,
                max_candidates_per_page=1,
                min_grid_confidence=0.98,
                template_dir="",
                include_low_confidence_review_candidates=True,
                low_confidence_min_grid_confidence=0.30,
                low_confidence_max_candidates_per_page=6,
            )
            review_jsonl = Path(manifest["review_dataset"]["jsonl"])
            review_rows = [line for line in review_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]

        self.assertEqual(manifest["accepted_fen_count"], 0)
        self.assertGreaterEqual(manifest["low_confidence_review_count"], 1)
        self.assertTrue(manifest["low_confidence_review_enabled"])
        self.assertTrue(manifest["low_confidence_review_candidates"])
        self.assertTrue(
            all(candidate.get("review_only") for candidate in manifest["low_confidence_review_candidates"])
        )
        self.assertTrue(
            all(not candidate.get("fen") for candidate in manifest["low_confidence_review_candidates"])
        )
        self.assertTrue(any("low_confidence_review" in row for row in review_rows))


if __name__ == "__main__":
    unittest.main()
