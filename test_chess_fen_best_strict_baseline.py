from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_best_strict_accepted_cases import check_best_strict_accepted_cases
from scripts.export_best_strict_accepted_baseline import export_best_strict_accepted_baseline


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"
OTHER_FEN = "8/8/8/8/8/8/4Q3/4k3 w - - 0 1"


class ChessFenBestStrictBaselineTests(unittest.TestCase):
    def test_export_includes_only_strict_accepted_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "best.json"
            output = root / "baseline.json"
            report.write_text(
                json.dumps(
                    {
                        "records": [
                            self._record("accepted", FULL_FEN, runtime_status="FEN_MACHINE_ACCEPTED", source="exact_label", crop_hash="a" * 64),
                            self._record("review", FULL_FEN, runtime_status="requires_review", requires_review=True),
                            self._record("placement", FULL_FEN, runtime_status="FEN_PLACEMENT_MACHINE_ACCEPTED"),
                            self._record("ai", FULL_FEN, runtime_status="ai_consensus"),
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = export_best_strict_accepted_baseline(report, output_path=output)

        self.assertEqual(payload["accepted_count"], 1)
        record = payload["records"][0]
        self.assertEqual(record["diagram_id"], "accepted")
        self.assertEqual(record["selected_value"], FULL_FEN)
        self.assertEqual(record["runtime_status"], "FEN_MACHINE_ACCEPTED")
        self.assertEqual(record["source"], "exact_label")
        self.assertEqual(record["crop_hash"], "a" * 64)

    def test_export_from_existing_diff_uses_previous_strict_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "diff.json"
            output = root / "baseline.json"
            report.write_text(
                json.dumps(
                    {
                        "schema": "kindlemaster.chess_fen.strict_report_diff.v1",
                        "cases": [
                            {
                                "diagram_id": "kept",
                                "page": 1,
                                "previous_status": "strict_accepted",
                                "previous_runtime_status": "FEN_MACHINE_ACCEPTED",
                                "previous_selected_value": FULL_FEN,
                            },
                            {"diagram_id": "review", "previous_status": "requires_review", "previous_selected_value": OTHER_FEN},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            payload = export_best_strict_accepted_baseline(report, output_path=output)

        self.assertEqual(payload["accepted_count"], 1)
        self.assertEqual(payload["records"][0]["diagram_id"], "kept")

    def test_baseline_case_present_and_strict_accepted_passes(self) -> None:
        payload = self._check(latest_records=[self._record("d1", FULL_FEN)], baseline_records=[self._baseline("d1", FULL_FEN)])

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["summary"]["passed_count"], 1)

    def test_baseline_case_missing_fails(self) -> None:
        payload = self._check(latest_records=[], baseline_records=[self._baseline("d1", FULL_FEN)])

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failures"][0]["reason"], "missing_latest_record")

    def test_baseline_case_now_review_fails(self) -> None:
        payload = self._check(
            latest_records=[self._record("d1", FULL_FEN, runtime_status="requires_review", requires_review=True)],
            baseline_records=[self._baseline("d1", FULL_FEN)],
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failures"][0]["reason"], "latest_not_strict_accepted")

    def test_placement_only_does_not_satisfy_baseline(self) -> None:
        payload = self._check(
            latest_records=[self._record("d1", FULL_FEN, runtime_status="FEN_PLACEMENT_MACHINE_ACCEPTED")],
            baseline_records=[self._baseline("d1", FULL_FEN)],
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failures"][0]["reason"], "latest_not_strict_accepted")

    def test_ai_only_does_not_satisfy_baseline(self) -> None:
        payload = self._check(
            latest_records=[self._record("d1", FULL_FEN, runtime_status="ai_consensus")],
            baseline_records=[self._baseline("d1", FULL_FEN)],
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failures"][0]["reason"], "latest_not_strict_accepted")

    def test_strict_fen_mismatch_fails_without_false_positive_exception(self) -> None:
        payload = self._check(latest_records=[self._record("d1", OTHER_FEN)], baseline_records=[self._baseline("d1", FULL_FEN)])

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failures"][0]["reason"], "strict_fen_mismatch")

    def test_previous_false_positive_with_square_diff_is_allowed(self) -> None:
        payload = self._check(
            latest_records=[],
            baseline_records=[
                {
                    **self._baseline("d1", FULL_FEN),
                    "previous_false_positive": {
                        "expected_fen": OTHER_FEN,
                        "previous_fen": FULL_FEN,
                        "current_fen": "",
                        "square_diff": [{"square": "e2", "expected": "Q", "previous": "K"}],
                        "reason": "manual audit found wrong previous label",
                    },
                }
            ],
        )

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["summary"]["allowed_previous_false_positive_count"], 1)

    def test_invalid_previous_false_positive_exception_fails(self) -> None:
        payload = self._check(
            latest_records=[],
            baseline_records=[{**self._baseline("d1", FULL_FEN), "previous_false_positive": {"reason": "missing evidence"}}],
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failures"][0]["reason"], "invalid_previous_false_positive_exception")

    def _check(self, *, latest_records: list[dict[str, object]], baseline_records: list[dict[str, object]]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "latest.json"
            baseline = root / "baseline.json"
            latest.write_text(json.dumps({"records": latest_records}), encoding="utf-8")
            baseline.write_text(json.dumps({"records": baseline_records}), encoding="utf-8")
            return check_best_strict_accepted_cases(latest, baseline_path=baseline)

    def _baseline(self, diagram_id: str, fen: str) -> dict[str, object]:
        return {
            "diagram_id": diagram_id,
            "page": 1,
            "selected_value": fen,
            "runtime_status": "FEN_MACHINE_ACCEPTED",
            "source": "exact_label",
            "crop_hash": "a" * 64,
            "report_source": "best.json",
        }

    def _record(
        self,
        diagram_id: str,
        fen: str,
        *,
        runtime_status: str = "FEN_MACHINE_ACCEPTED",
        requires_review: bool = False,
        source: str = "exact_label",
        crop_hash: str = "",
    ) -> dict[str, object]:
        return {
            "diagram_id": diagram_id,
            "page": 1,
            "fen": fen,
            "runtime_status": runtime_status,
            "requires_review": requires_review,
            "source": source,
            "crop_hash": crop_hash,
        }


if __name__ == "__main__":
    unittest.main()
