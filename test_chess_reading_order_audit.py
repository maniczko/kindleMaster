from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chess_reading_order_audit import (
    audit_chess_reading_order,
    write_chess_reading_order_report,
)


class ChessReadingOrderAuditTests(unittest.TestCase):
    def test_text_diagram_pgn_order_passes(self) -> None:
        report = audit_chess_reading_order(
            pages=[
                {
                    "page": 1,
                    "elements": [
                        {
                            "id": "ctx-1",
                            "type": "text",
                            "source_order": 1,
                            "text": "White to move finds the forcing continuation.",
                        },
                        {
                            "id": "diagram-1",
                            "type": "diagram",
                            "source_order": 2,
                            "text": "Diagram 1 FEN 8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                            "status": "accepted",
                        },
                        {
                            "id": "pgn-1",
                            "type": "pgn",
                            "source_order": 3,
                            "text": "1. Ke2 *",
                            "status": "accepted",
                        },
                    ],
                }
            ]
        )

        self.assertEqual(report.status, "passed")
        self.assertEqual(report.warnings, [])
        self.assertEqual(len(report.links), 1)
        self.assertEqual(report.links[0].diagram_id, "diagram-1")
        self.assertEqual(report.links[0].pgn_id, "pgn-1")

    def test_diagram_without_fen_or_pgn_warns(self) -> None:
        report = audit_chess_reading_order(
            pages=[
                {
                    "page": 1,
                    "elements": [
                        {"id": "diagram-1", "type": "diagram", "source_order": 1, "text": "Diagram 1"},
                    ],
                }
            ]
        )

        self.assertIn("diagram_without_pgn_or_fen", self._warning_codes(report))

    def test_pgn_source_page_mismatch_warns(self) -> None:
        report = audit_chess_reading_order(
            pages=[
                {
                    "page": 2,
                    "elements": [
                        {"id": "ctx-2", "type": "text", "source_order": 1, "text": "Solution text."},
                        {"id": "pgn-2", "type": "pgn", "source_order": 2, "text": "1. e4 *", "status": "accepted"},
                    ],
                }
            ],
            fen_candidates=[{"page": 1, "fen": "8/8/8/8/8/8/4K3/4k3 w - - 0 1"}],
        )

        self.assertIn("pgn_source_page_mismatch", self._warning_codes(report))
        self.assertEqual(report.status, "failed")

    def test_review_only_pgn_visible_without_reason_warns(self) -> None:
        report = audit_chess_reading_order(
            pages=[
                {
                    "page": 1,
                    "elements": [
                        {"id": "ctx-1", "type": "text", "source_order": 1, "text": "A tactical line follows."},
                        {
                            "id": "pgn-review",
                            "type": "pgn",
                            "source_order": 2,
                            "text": "1. Qh5",
                            "status": "requires_review",
                        },
                    ],
                }
            ]
        )

        self.assertIn("review_pgn_visible_without_reason", self._warning_codes(report))
        self.assertEqual(report.status, "failed")

    def test_local_paths_warn_and_written_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            html_path = Path(tmp) / "final.html"
            html_path.write_text("<html><body>file://C:/Users/user/private.png</body></html>", encoding="utf-8")

            report = audit_chess_reading_order(
                pages=[
                    {
                        "page": 1,
                        "elements": [
                            {"id": "ctx-1", "type": "text", "source_order": 1, "text": "http://127.0.0.1/debug"},
                        ],
                    }
                ],
                final_html_path=html_path,
            )
            paths = write_chess_reading_order_report(report, Path(tmp) / "reports")

            self.assertIn("local_path_leaked", self._warning_codes(report))
            self.assertEqual(report.status, "failed")
            self.assertTrue(Path(paths["json"]).exists())
            self.assertTrue(Path(paths["html"]).exists())
            payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "kindlemaster.chess_reading_order_audit.v1")

    @staticmethod
    def _warning_codes(report) -> set[str]:
        return {str(warning.get("code")) for warning in report.warnings}


if __name__ == "__main__":
    unittest.main()
