from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.generate_auto_strict_validation_report import generate_auto_strict_validation_report


class AutoStrictValidationReportTests(unittest.TestCase):
    def test_missing_out_dir_writes_failed_standard_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "reports" / "auto_strict_validation.json"

            payload = generate_auto_strict_validation_report(output_path=output)
            written = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(payload["overall_status"], "failed")
        self.assertFalse(payload["release_ready"])
        self.assertEqual(written["errors"][0]["code"], "auto_output_dir_missing")

    def test_out_dir_validation_result_is_written_to_standard_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            out = root / "auto"
            _write_minimal_auto_chess_output(out)
            output = root / "reports" / "auto_strict_validation.json"

            payload = generate_auto_strict_validation_report(out_dir=out, output_path=output)
            written = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(payload["overall_status"], "passed")
        self.assertTrue(payload["release_ready"])
        self.assertEqual(written["source_out_dir"], str(out))


def _write_minimal_auto_chess_output(out: Path) -> None:
    required_files = [
        "pages/pages.json",
        "layout/layout.json",
        "diagrams/diagrams.json",
        "fen/fen_candidates.json",
        "fen/fen_validation.json",
        "pgn/pgn_candidates.json",
        "pgn/pgn_validation.json",
        "repair/repair_attempts.json",
        "report/acceptance_blockers.json",
        "report/quality_report.json",
    ]
    for rel in required_files:
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"summary": {"fen_failed": 0, "pgn_failed": 0}} if rel == "report/quality_report.json" else {}
        path.write_text(json.dumps(payload), encoding="utf-8")
    text_blocks = out / "text" / "text_blocks.jsonl"
    text_blocks.parent.mkdir(parents=True, exist_ok=True)
    text_blocks.write_text("", encoding="utf-8")
    games = out / "export" / "games.pgn"
    games.parent.mkdir(parents=True, exist_ok=True)
    games.write_text("", encoding="utf-8")
    (out / "auto_chess_flow.json").write_text(
        json.dumps({"summary": {"fen_failed": 0, "pgn_failed": 0}}),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
