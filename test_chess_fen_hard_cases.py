from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.analyze_chess_fen_review_blockers import analyze_review_blockers
from scripts.build_chess_fen_hard_cases import build_hard_case_report, main


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"


class ChessFenHardCaseReportTests(unittest.TestCase):
    def test_current_diagnostics_separate_crop_recovery_from_unresolved_crop_grid(self) -> None:
        diagnostics = json.loads(
            Path("reports/chess_fen/fundamenty_marker_rule_recovery_review_diagnostics.json").read_text(encoding="utf-8")
        )
        report = build_hard_case_report("reports/chess_fen/fundamenty_marker_rule_recovery_review_diagnostics.json")

        self.assertEqual(report["summary"]["review_total"], diagnostics["summary"]["review_total"])
        self.assertGreaterEqual(report["summary"]["crop_grid_unresolved_count"], 0)
        self.assertGreater(report["summary"]["crop_recovery_evidence_count"], 0)

    def test_delta_marks_crop_grid_blockers_decreased(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            baseline = self._write_diagnostics(
                tmp_path / "baseline.json",
                [
                    self._review_record("crop-a", ["board_grid_not_detected"]),
                    self._review_record("crop-b", ["partial_board_crop_without_dense_board_evidence"]),
                    self._review_record("recognition", ["piece_template_confidence_below_threshold"]),
                ],
            )
            current = self._write_diagnostics(
                tmp_path / "current.json",
                [
                    self._review_record("crop-a", ["board_grid_not_detected"]),
                    self._review_record("recognition", ["piece_template_confidence_below_threshold"]),
                ],
            )

            report = build_hard_case_report(current, baseline_path=baseline)

        self.assertEqual(report["baseline_summary"]["crop_grid_unresolved_count"], 2)
        self.assertEqual(report["summary"]["crop_grid_unresolved_count"], 1)
        self.assertEqual(report["delta"]["crop_grid_unresolved_count"], -1)
        self.assertTrue(report["crop_grid_blockers_decreased"])

    def test_reader_visible_crop_recovery_is_not_counted_as_unresolved_crop_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            diagnostics = self._write_diagnostics(
                Path(tmp) / "diagnostics.json",
                [
                    self._review_record(
                        "reader-recovery",
                        ["reader_visible_crop_fen_used", "side_to_move_inferred"],
                    )
                ],
            )
            report = build_hard_case_report(diagnostics)

        self.assertEqual(report["summary"]["crop_grid_unresolved_count"], 0)
        self.assertEqual(report["summary"]["crop_recovery_evidence_count"], 1)
        self.assertIn("crop_recovery_evidence", report["items"][0]["hard_case_tags"])
        self.assertEqual(report["items"][0]["non_blocking_crop_recovery_warnings"], ["reader_visible_crop_fen_used"])

    def test_cli_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            diagnostics = self._write_diagnostics(
                tmp_path / "diagnostics.json",
                [self._review_record("crop", ["board_grid_not_detected"])],
            )
            output_json = tmp_path / "hard_cases.json"
            output_md = tmp_path / "hard_cases.md"

            exit_code = main([str(diagnostics), "--output-json", str(output_json), "--output-md", str(output_md)])

            self.assertEqual(exit_code, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["crop_grid_unresolved_count"], 1)
            self.assertIn("Unresolved crop/grid blockers", output_md.read_text(encoding="utf-8"))

    def _write_diagnostics(self, path: Path, records: list[dict[str, object]]) -> Path:
        raw_report = path.with_suffix(".raw.json")
        raw_report.write_text(json.dumps({"quality_report": {"chess_fen": {"records": records}}}), encoding="utf-8")
        diagnostics = analyze_review_blockers(raw_report)
        path.write_text(json.dumps(diagnostics), encoding="utf-8")
        return path

    def _review_record(self, diagram_id: str, warnings: list[str]) -> dict[str, object]:
        return {
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


if __name__ == "__main__":
    unittest.main()
