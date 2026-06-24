import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

from scripts import export_chess_side_header_candidates as exporter


class ChessSideHeaderCandidatesTest(unittest.TestCase):
    def test_side_header_zone_extends_above_board_and_horizontally(self) -> None:
        bbox = exporter._side_header_zone_bbox([100, 200, 300, 400], (500, 700))

        self.assertEqual(bbox, (76, 150, 324, 200))

    def test_symbol_candidates_from_ocr_lines_extracts_header_symbols(self) -> None:
        lines = [
            {"text": "Diagram 1-1 Vv", "bbox": [10, 20, 100, 40], "confidence": 0.91},
            {"text": "Diagram 1-2 \u25b3", "bbox": [10, 50, 100, 70], "confidence": 0.82},
            {"text": "Caro-Kann Advance", "bbox": [10, 80, 140, 100], "confidence": 0.75},
        ]

        candidates = exporter._symbol_candidates_from_lines(lines)

        self.assertEqual([candidate["symbol"] for candidate in candidates], ["Vv", "\u25b3"])
        self.assertEqual(candidates[0]["source"], "ocr_line")

    def test_export_side_header_candidates_writes_audit_artifacts_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / "book.epub"
            board_name = "scan-chess-p001-g01.png"
            board = Image.new("RGB", (80, 80), "white")
            board_path = root / board_name
            board.save(board_path)
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.write(board_path, f"EPUB/images/{board_name}")

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
                                        "filename": board_name,
                                        "bbox": [100, 200, 300, 400],
                                        "requires_review": True,
                                        "warnings": ["side_to_move_inferred"],
                                        "placement": "8/8/8/8/8/8/8/4K2k",
                                        "full_fen": "8/8/8/8/8/8/8/4K2k w - - 0 1",
                                        "confidence": 0.88,
                                    }
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            output_dir = root / "out"
            source_pdf = root / "source.pdf"
            source_pdf.write_bytes(b"%PDF-1.4\n% synthetic test placeholder\n")

            with mock.patch.object(
                exporter,
                "_load_page_images",
                return_value={1: Image.new("RGB", (500, 700), "white")},
            ), mock.patch.object(
                exporter,
                "_ocr_header_lines",
                return_value=(
                    [
                        {
                            "text": "Diagram 1-1 Vv",
                            "bbox": [100, 170, 220, 190],
                            "confidence": 0.93,
                            "page": 1,
                        }
                    ],
                    "",
                ),
            ):
                summary = exporter.export_side_header_candidates(
                    report_path,
                    output_dir=output_dir,
                    source_pdf=source_pdf,
                )

            jsonl_path = output_dir / "side_header_candidates.jsonl"
            html_path = output_dir / "side_header_review.html"
            rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["header_crop_available_count"], 1)
            self.assertEqual(summary["symbol_candidate_count"], 1)
            self.assertTrue(html_path.is_file())
            self.assertEqual(rows[0]["header_crop_status"], "available")
            self.assertTrue(Path(rows[0]["header_crop_path"]).is_file())
            self.assertEqual(rows[0]["header_symbol_candidates"][0]["symbol"], "Vv")
            self.assertFalse(rows[0]["human_verified"])
            self.assertNotIn("fen", rows[0])

    def test_export_side_header_candidates_without_page_image_reports_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            epub_path = root / "book.epub"
            board_name = "scan-chess-p001-g01.png"
            board_path = root / board_name
            Image.new("RGB", (80, 80), "white").save(board_path)
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.write(board_path, f"EPUB/images/{board_name}")

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
                                        "filename": board_name,
                                        "bbox": [100, 200, 300, 400],
                                        "requires_review": True,
                                        "warnings": ["side_to_move_inferred"],
                                    }
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            summary = exporter.export_side_header_candidates(report_path, output_dir=root / "out")
            rows = [
                json.loads(line)
                for line in (root / "out" / "side_header_candidates.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(summary["record_count"], 1)
            self.assertEqual(summary["header_crop_available_count"], 0)
            self.assertEqual(rows[0]["header_crop_status"], "unavailable")
            self.assertEqual(rows[0]["ocr_line_geometry_warning"], "ocr_line_geometry_unavailable")


if __name__ == "__main__":
    unittest.main()
