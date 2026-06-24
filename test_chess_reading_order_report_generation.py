from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_chess_reading_order_audit import generate_chess_reading_order_audit, main


class ChessReadingOrderReportGenerationTests(unittest.TestCase):
    def test_generates_standard_reading_order_artifacts_from_conversion_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "conversion_report.json"
            source.write_text(
                json.dumps(
                    {
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    {
                                        "page": 7,
                                        "filename": "diagram-p007-01.png",
                                        "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                                        "requires_review": False,
                                        "bbox": [20, 100, 200, 280],
                                    }
                                ]
                            },
                            "chess_pgn": {
                                "records": [
                                    {
                                        "id": "pgn-7-1",
                                        "source_pages": [7],
                                        "status": "accepted",
                                        "movetext": "1. Ke2 *",
                                        "warnings": [],
                                    }
                                ]
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            summary = generate_chess_reading_order_audit(source, output_dir=root / "reports")
            payload = json.loads((root / "reports/html_reading_order_report.json").read_text(encoding="utf-8"))

            self.assertEqual(summary["status"], "passed_with_warnings")
            self.assertEqual(summary["high_severity_warning_count"], 0)
            self.assertTrue(Path(summary["json"]).exists())
            self.assertTrue(Path(summary["html"]).exists())
            self.assertEqual(summary["diagram_record_count"], 1)
            self.assertEqual(summary["pgn_record_count"], 1)
            self.assertEqual(payload["schema_version"], "kindlemaster.chess_reading_order_audit.v1")

    def test_cli_exits_zero_for_warning_only_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "conversion_report.json"
            source.write_text(
                json.dumps(
                    {
                        "quality_report": {
                            "chess_fen": {"records": [{"page": 1, "filename": "diagram.png", "requires_review": True}]},
                            "chess_pgn": {"records": []},
                        }
                    }
                ),
                encoding="utf-8",
            )

            import sys

            old_argv = sys.argv
            try:
                sys.argv = [
                    "generate_chess_reading_order_audit.py",
                    str(source),
                    "--output-dir",
                    str(root / "reports"),
                ]
                exit_code = main()
            finally:
                sys.argv = old_argv

        self.assertEqual(exit_code, 0)

    def test_review_pgn_warnings_are_preserved_as_visible_review_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "conversion_report.json"
            source.write_text(
                json.dumps(
                    {
                        "quality_report": {
                            "chess_fen": {
                                "records": [
                                    {
                                        "page": 3,
                                        "filename": "diagram-p003-01.png",
                                        "requires_review": True,
                                    }
                                ]
                            },
                            "chess_pgn": {
                                "records": [
                                    {
                                        "id": "pgn-review",
                                        "source_pages": [3],
                                        "status": "requires_review",
                                        "raw_text": "1. Qh5",
                                        "warnings": ["pgn_replay_failed"],
                                    }
                                ]
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            summary = generate_chess_reading_order_audit(source, output_dir=root / "reports")
            payload = json.loads((root / "reports/html_reading_order_report.json").read_text(encoding="utf-8"))

        warning_codes = {warning["code"] for warning in payload["warnings"]}
        self.assertNotIn("review_pgn_visible_without_reason", warning_codes)
        self.assertEqual(summary["high_severity_warning_count"], 0)


if __name__ == "__main__":
    unittest.main()
