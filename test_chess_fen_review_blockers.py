from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_chess_fen_review_blockers import analyze_review_blockers, main


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"


class ChessFenReviewBlockerDiagnosticsTests(unittest.TestCase):
    def test_generated_latest_diagnostics_has_197_review_items(self) -> None:
        payload = json.loads(
            Path("reports/chess_fen/fundamenty_marker_rule_recovery_review_diagnostics.json").read_text(encoding="utf-8")
        )

        self.assertEqual(payload["summary"]["review_total"], 197)
        self.assertEqual(len(payload["items"]), 197)
        self.assertTrue(all(item["primary_category"] for item in payload["items"]))

    def test_classifies_core_blocker_categories(self) -> None:
        records = [
            self._review_record("ai", warnings=["ai_review_only_source"], source="ai_autoread"),
            self._review_record("crop", warnings=["board_grid_not_detected"]),
            self._review_record("recognition", warnings=["piece_template_confidence_below_threshold"]),
            self._review_record("validation", warnings=["python_chess_invalid_position"]),
            self._review_record("unknown", warnings=[]),
            self._accepted_record("accepted"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._write_report(Path(tmp) / "report.json", records)

            payload = analyze_review_blockers(report)

        by_id = {item["diagram_id"]: item for item in payload["items"]}
        self.assertNotIn("accepted", by_id)
        self.assertEqual(by_id["ai"]["primary_category"], "source_policy")
        self.assertEqual(by_id["crop"]["primary_category"], "crop_grid")
        self.assertEqual(by_id["recognition"]["primary_category"], "recognition")
        self.assertEqual(by_id["validation"]["primary_category"], "full_fen_validation")
        self.assertEqual(by_id["unknown"]["primary_category"], "unknown")
        self.assertEqual(by_id["unknown"]["primary_blocker"], "missing_blocker_data")
        self.assertTrue(by_id["unknown"]["missing_blocker_data"])
        self.assertTrue(all("category" in blocker for item in payload["items"] for blocker in item["blockers"]))
        self.assertIn("unknown", payload["summary"]["by_category"])

    def test_outputs_markdown_with_top_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._write_report(Path(tmp) / "report.json", [self._review_record("crop", warnings=["board_grid_not_detected"])])
            output_json = Path(tmp) / "diagnostics.json"
            output_md = Path(tmp) / "diagnostics.md"

            exit_code = main([str(report), "--output-json", str(output_json), "--output-md", str(output_md)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["review_total"], 1)
            markdown = output_md.read_text(encoding="utf-8")
            self.assertIn("Top 20 Categories", markdown)
            self.assertIn("Top 20 Blocker Codes", markdown)

    def _write_report(self, path: Path, records: list[dict[str, object]]) -> Path:
        path.write_text(json.dumps({"quality_report": {"chess_fen": {"records": records}}}), encoding="utf-8")
        return path

    def _review_record(
        self,
        diagram_id: str,
        *,
        warnings: list[str],
        source: str = "",
    ) -> dict[str, object]:
        record: dict[str, object] = {
            "diagram_id": diagram_id,
            "page": 1,
            "requires_review": True,
            "status": "requires_review",
            "runtime_status": "requires_review",
            "warnings": warnings,
            "placement": "8/8/8/8/8/8/4K3/4k3",
            "full_fen": FULL_FEN,
            "confidence": 0.5,
        }
        if source:
            record["source"] = source
        return record

    def _accepted_record(self, diagram_id: str) -> dict[str, object]:
        return {
            "diagram_id": diagram_id,
            "page": 1,
            "requires_review": False,
            "status": "FEN_MACHINE_ACCEPTED",
            "runtime_status": "FEN_MACHINE_ACCEPTED",
            "fen": FULL_FEN,
        }


if __name__ == "__main__":
    unittest.main()
