from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_chess_fen_strict_regression_gate import evaluate_strict_regression_gate, main


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"


class ChessFenStrictRegressionGateTests(unittest.TestCase):
    def test_real_180_report_fails_against_223_baseline(self) -> None:
        payload = json.loads(
            Path("reports/chess_fen/strict_regression_gate_marker_rule.json").read_text(encoding="utf-8")
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["baseline"]["best_known_strict_accepted"], 223)
        self.assertEqual(payload["candidate"]["strict_accepted"], 180)
        self.assertFalse(payload["baseline_update_candidate"])
        self.assertTrue(payload["blockers"])

    def test_equal_baseline_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._write_baseline(Path(tmp) / "baseline.json", best_known=3, case_count=3)
            report = self._write_report(Path(tmp) / "candidate.json", [self._strict_record(str(i)) for i in range(3)])

            payload = evaluate_strict_regression_gate(report, baseline_path=baseline)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["candidate"]["strict_accepted"], 3)
        self.assertFalse(payload["baseline_update_candidate"])

    def test_above_baseline_passes_and_suggests_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._write_baseline(Path(tmp) / "baseline.json", best_known=3, case_count=4)
            report = self._write_report(Path(tmp) / "candidate.json", [self._strict_record(str(i)) for i in range(4)])

            payload = evaluate_strict_regression_gate(report, baseline_path=baseline)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["candidate"]["strict_accepted"], 4)
        self.assertTrue(payload["baseline_update_candidate"])

    def test_placement_and_ai_only_records_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._write_baseline(Path(tmp) / "baseline.json", best_known=1, case_count=3)
            report = self._write_report(
                Path(tmp) / "candidate.json",
                [
                    self._strict_record("strict"),
                    self._strict_record("placement", runtime_status="FEN_PLACEMENT_MACHINE_ACCEPTED"),
                    self._strict_record("ai", status="ai_consensus"),
                    self._strict_record("machine-valid-without-value", fen="", runtime_status="FEN_MACHINE_VALID"),
                ],
            )

            payload = evaluate_strict_regression_gate(report, baseline_path=baseline)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["candidate"]["strict_accepted"], 1)

    def test_missing_baseline_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = self._write_report(Path(tmp) / "candidate.json", [self._strict_record("one")])
            output_json = Path(tmp) / "gate.json"

            exit_code = main([str(report), "--baseline", str(Path(tmp) / "missing.json"), "--output-json", str(output_json)])

            self.assertEqual(exit_code, 2)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"], "missing_input")
            self.assertIn("missing.json", payload["path"])

    def _write_baseline(self, path: Path, *, best_known: int, case_count: int) -> Path:
        path.write_text(
            json.dumps(
                {
                    "schema": "kindlemaster.chess_fen.strict_baseline.v1",
                    "corpus": "synthetic",
                    "case_count": case_count,
                    "best_known_report": "synthetic.json",
                    "best_known_strict_accepted": best_known,
                    "best_known_strict_rate": best_known / case_count,
                    "allowed_strict_drop": 0,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_report(self, path: Path, records: list[dict[str, object]]) -> Path:
        path.write_text(json.dumps({"quality_report": {"chess_fen": {"records": records}}}), encoding="utf-8")
        return path

    def _strict_record(
        self,
        diagram_id: str,
        *,
        fen: str = FULL_FEN,
        status: str = "FEN_MACHINE_ACCEPTED",
        runtime_status: str = "FEN_MACHINE_ACCEPTED",
    ) -> dict[str, object]:
        return {
            "diagram_id": diagram_id,
            "fen": fen,
            "requires_review": False,
            "status": status,
            "runtime_status": runtime_status,
        }


if __name__ == "__main__":
    unittest.main()
