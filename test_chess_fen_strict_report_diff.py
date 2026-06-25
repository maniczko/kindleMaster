from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.diff_chess_fen_strict_reports import diff_strict_reports, main


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"


class ChessFenStrictReportDiffTests(unittest.TestCase):
    def test_detects_lost_new_and_kept_strict_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp) / "previous.json"
            latest = Path(tmp) / "latest.json"
            self._write_report(
                previous,
                [
                    self._record("kept", fen=FULL_FEN, requires_review=False),
                    self._record("lost", fen=FULL_FEN, requires_review=False),
                    self._record("new", fen="", requires_review=True),
                ],
            )
            self._write_report(
                latest,
                [
                    self._record("kept", fen=FULL_FEN, requires_review=False),
                    self._record(
                        "lost",
                        fen="",
                        full_fen="8/8/8/8/8/8/4K3/4k3 w - - 0 1",
                        requires_review=True,
                        warnings=["side_to_move_inferred"],
                    ),
                    self._record("new", fen=FULL_FEN, requires_review=False),
                ],
            )

            payload = diff_strict_reports(previous, latest)

        self.assertEqual(payload["summary"]["previous_strict_accepted_count"], 2)
        self.assertEqual(payload["summary"]["latest_strict_accepted_count"], 2)
        self.assertEqual(payload["summary"]["lost_strict_count"], 1)
        self.assertEqual(payload["summary"]["new_strict_count"], 1)
        classifications = {case["diagram_id"]: case["classification"] for case in payload["cases"]}
        self.assertEqual(classifications["kept"], "kept_strict_accepted")
        self.assertEqual(classifications["lost"], "lost_strict_accepted")
        self.assertEqual(classifications["new"], "new_strict_accepted")

    def test_assigns_regression_categories_from_latest_blockers(self) -> None:
        cases = {
            "crop": ["board_grid_not_detected"],
            "recognition": ["piece_template_confidence_below_threshold"],
            "validation": ["python_chess_invalid_position"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp) / "previous.json"
            latest = Path(tmp) / "latest.json"
            self._write_report(
                previous,
                [self._record(key, fen=FULL_FEN, requires_review=False) for key in cases],
            )
            self._write_report(
                latest,
                [
                    self._record(key, fen="", full_fen=FULL_FEN, requires_review=True, warnings=warnings)
                    for key, warnings in cases.items()
                ],
            )

            payload = diff_strict_reports(previous, latest)

        categories = {case["diagram_id"]: case["primary_regression_category"] for case in payload["cases"]}
        self.assertEqual(categories["crop"], "crop_grid")
        self.assertEqual(categories["recognition"], "recognition")
        self.assertEqual(categories["validation"], "full_fen_validation")
        lost_cases = [case for case in payload["cases"] if case["classification"] == "lost_strict_accepted"]
        self.assertTrue(all("category" in blocker for case in lost_cases for blocker in case["latest_blocker_items"]))
        self.assertIn("unknown", payload["summary"]["lost_by_category"])

    def test_does_not_count_placement_or_ai_only_records_as_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp) / "previous.json"
            latest = Path(tmp) / "latest.json"
            self._write_report(
                previous,
                [
                    self._record(
                        "placement",
                        fen=FULL_FEN,
                        requires_review=False,
                        runtime_status="FEN_PLACEMENT_MACHINE_ACCEPTED",
                    ),
                    self._record("ai", fen=FULL_FEN, requires_review=False, status="ai_consensus"),
                    self._record("strict", fen=FULL_FEN, requires_review=False),
                ],
            )
            self._write_report(latest, [self._record("strict", fen=FULL_FEN, requires_review=False)])

            payload = diff_strict_reports(previous, latest)

        self.assertEqual(payload["summary"]["previous_strict_accepted_count"], 1)
        self.assertEqual(payload["summary"]["latest_strict_accepted_count"], 1)
        self.assertEqual(payload["summary"]["lost_strict_count"], 0)

    def test_cli_writes_outputs_and_missing_input_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            previous = Path(tmp) / "previous.json"
            latest = Path(tmp) / "latest.json"
            output_json = Path(tmp) / "diff.json"
            output_md = Path(tmp) / "diff.md"
            self._write_report(previous, [self._record("one", fen=FULL_FEN, requires_review=False)])
            self._write_report(latest, [self._record("one", fen="", requires_review=True)])

            exit_code = main([str(previous), str(latest), "--output-json", str(output_json), "--output-md", str(output_md)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_json.exists())
            self.assertTrue(output_md.exists())
            self.assertEqual(json.loads(output_json.read_text(encoding="utf-8"))["summary"]["lost_strict_count"], 1)
            self.assertIn("Lost Strict Accepted", output_md.read_text(encoding="utf-8"))

            missing_exit = main(
                [str(Path(tmp) / "missing.json"), str(latest), "--output-json", str(output_json), "--output-md", str(output_md)]
            )
            self.assertEqual(missing_exit, 2)

    def _write_report(self, path: Path, records: list[dict[str, object]]) -> None:
        path.write_text(json.dumps({"quality_report": {"chess_fen": {"records": records}}}), encoding="utf-8")

    def _record(
        self,
        diagram_id: str,
        *,
        fen: str,
        full_fen: str = "",
        requires_review: bool,
        status: str = "FEN_MACHINE_ACCEPTED",
        runtime_status: str = "FEN_MACHINE_ACCEPTED",
        warnings: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "diagram_id": diagram_id,
            "page": 1,
            "fen": fen,
            "full_fen": full_fen,
            "requires_review": requires_review,
            "status": status,
            "runtime_status": runtime_status,
            "warnings": warnings or [],
        }


if __name__ == "__main__":
    unittest.main()
