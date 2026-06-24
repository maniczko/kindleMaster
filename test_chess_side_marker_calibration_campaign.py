from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from scripts.export_chess_side_marker_calibration_campaign import export_side_marker_calibration_campaign


class ChessSideMarkerCalibrationCampaignTests(unittest.TestCase):
    def test_exports_conflict_and_inferred_marker_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / "book.epub"
            crop_path = root / "crop.png"
            Image.new("RGB", (80, 80), "white").save(crop_path)
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.write(crop_path, "EPUB/images/scan_chess_p001_01.png")
                archive.write(crop_path, "EPUB/images/scan_chess_p002_01.png")

            report_path = root / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "output_path": str(epub_path),
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    {
                                        "page": 1,
                                        "filename": "scan_chess_p001_01.png",
                                        "requires_review": True,
                                        "placement": "8/8/8/8/8/8/8/8",
                                        "confidence": 0.91,
                                        "warnings": [
                                            "side_to_move_inferred",
                                            "side_to_move_marker_multi_region_conflict",
                                        ],
                                        "side_marker_candidates": [
                                            {"role": "top_right", "bbox": [1, 2, 20, 22], "detected_side": "w"},
                                            {"role": "bottom_right", "bbox": [1, 40, 20, 60], "detected_side": "b"},
                                        ],
                                    },
                                    {
                                        "page": 2,
                                        "filename": "scan_chess_p002_01.png",
                                        "requires_review": True,
                                        "placement": "8/8/8/8/8/8/8/8",
                                        "confidence": 0.88,
                                        "warnings": ["side_to_move_inferred"],
                                        "side_marker_candidates": [
                                            {"role": "caption_above", "bbox": [5, 5, 25, 25]},
                                        ],
                                    },
                                    {
                                        "page": 3,
                                        "filename": "accepted.png",
                                        "requires_review": False,
                                        "placement": "8/8/8/8/8/8/8/8",
                                        "warnings": [],
                                    },
                                ]
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = export_side_marker_calibration_campaign(report_path, output_dir=root / "campaign")
            draft_path = Path(summary["draft_path"])
            rows = [json.loads(line) for line in draft_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(summary["draft_count"], 2)
            self.assertEqual(summary["priority_counts"]["conflict"], 1)
            self.assertEqual(summary["priority_counts"]["inferred_no_candidate"], 1)
            self.assertEqual(rows[0]["review_priority"], "conflict")
            self.assertEqual(rows[0]["detected_marker_sides"], ["b", "w"])
            self.assertEqual(rows[0]["human_side_to_move"], "")
            self.assertFalse(rows[0]["human_verified"])
            self.assertTrue(Path(summary["review_sheet_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
