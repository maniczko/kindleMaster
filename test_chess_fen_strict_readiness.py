from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evaluate_chess_fen_strict_readiness import (
    MISSING_ARTIFACT,
    evaluate_chess_fen_strict_readiness,
    main,
)


FULL_FEN = "8/8/8/8/8/8/4K3/4k3 w - - 0 1"


class ChessFenStrictReadinessTests(unittest.TestCase):
    def test_latest_180_vs_best_223_is_regressed_and_not_release_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = self._summary_report(root / "latest.json", strict_accepted=180, case_count=377)
            best = self._summary_report(root / "best.json", strict_accepted=223, case_count=377)
            ai = self._ai_report(root / "ai.json", coverage=377)

            payload = evaluate_chess_fen_strict_readiness(
                latest_path=latest,
                best_path=best,
                ai_path=ai,
                output_json=root / "strict_readiness.json",
                output_md=root / "strict_readiness.md",
            )

        self.assertEqual(payload["schema"], "kindlemaster.chess_fen.strict_readiness.v1")
        self.assertEqual(payload["status"], "regressed")
        self.assertEqual(payload["strict"]["latest_accepted"], 180)
        self.assertEqual(payload["strict"]["best_accepted"], 223)
        self.assertEqual(payload["strict"]["delta"], -43)
        self.assertEqual(payload["strict"]["latest_rate"], 0.4775)
        self.assertEqual(payload["strict"]["best_rate"], 0.5915)
        self.assertFalse(payload["release_safe"]["can_release"])
        self.assertEqual(payload["release_safe"]["reason"], "strict_regression")
        self.assertEqual(payload["next_actions"][0], "recover_lost_strict_cases")

    def test_stable_and_improved_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            best = self._summary_report(root / "best.json", strict_accepted=10, case_count=20)
            ai = self._ai_report(root / "ai.json", coverage=20)

            stable = evaluate_chess_fen_strict_readiness(
                latest_path=self._summary_report(root / "stable.json", strict_accepted=10, case_count=20),
                best_path=best,
                ai_path=ai,
            )
            improved = evaluate_chess_fen_strict_readiness(
                latest_path=self._summary_report(root / "improved.json", strict_accepted=11, case_count=20),
                best_path=best,
                ai_path=ai,
            )

        self.assertEqual(stable["status"], "stable")
        self.assertTrue(stable["release_safe"]["can_release"])
        self.assertEqual(improved["status"], "improved")
        self.assertTrue(improved["release_safe"]["can_release"])

    def test_missing_ai_is_reported_without_blocking_strict_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = evaluate_chess_fen_strict_readiness(
                latest_path=self._summary_report(root / "latest.json", strict_accepted=223, case_count=377),
                best_path=self._summary_report(root / "best.json", strict_accepted=223, case_count=377),
                ai_path=root / "missing_ai.json",
            )

        self.assertEqual(payload["status"], "stable")
        self.assertEqual(payload["ai"]["status"], MISSING_ARTIFACT)
        self.assertTrue(payload["release_safe"]["can_release"])

    def test_missing_required_report_blocks_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = evaluate_chess_fen_strict_readiness(
                latest_path=root / "missing_latest.json",
                best_path=self._summary_report(root / "best.json", strict_accepted=223, case_count=377),
                ai_path=root / "missing_ai.json",
            )

        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["release_safe"]["can_release"])
        self.assertEqual(payload["inputs"]["latest"]["status"], MISSING_ARTIFACT)

    def test_ai_full_coverage_does_not_override_strict_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = evaluate_chess_fen_strict_readiness(
                latest_path=self._summary_report(root / "latest.json", strict_accepted=180, case_count=377),
                best_path=self._summary_report(root / "best.json", strict_accepted=223, case_count=377),
                ai_path=self._ai_report(root / "ai.json", coverage=377),
            )

        self.assertEqual(payload["ai"]["coverage"], 377)
        self.assertEqual(payload["ai"]["ai_consensus"], 90)
        self.assertFalse(payload["release_safe"]["can_release"])

    def test_placement_only_records_are_not_counted_as_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = root / "latest.json"
            best = root / "best.json"
            latest.write_text(
                json.dumps(
                    {
                        "records": [
                            self._record("strict", FULL_FEN, runtime_status="FEN_MACHINE_ACCEPTED", requires_review=False),
                            self._record("placement", FULL_FEN, runtime_status="FEN_PLACEMENT_MACHINE_ACCEPTED", requires_review=False),
                        ]
                    }
                ),
                encoding="utf-8",
            )
            best.write_text(json.dumps({"records": [self._record("strict", FULL_FEN, requires_review=False)]}), encoding="utf-8")

            payload = evaluate_chess_fen_strict_readiness(latest_path=latest, best_path=best, ai_path=root / "missing_ai.json")

        self.assertEqual(payload["strict"]["latest_accepted"], 1)
        self.assertEqual(payload["strict"]["best_accepted"], 1)
        self.assertEqual(payload["status"], "stable")

    def test_cli_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_json = root / "strict_readiness.json"
            output_md = root / "strict_readiness.md"
            with contextlib.redirect_stdout(io.StringIO()):
                exit_code = main(
                    [
                        "--latest",
                        str(self._summary_report(root / "latest.json", strict_accepted=180, case_count=377)),
                        "--best",
                        str(self._summary_report(root / "best.json", strict_accepted=223, case_count=377)),
                        "--ai",
                        str(self._ai_report(root / "ai.json", coverage=377)),
                        "--output-json",
                        str(output_json),
                        "--output-md",
                        str(output_md),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(output_json.read_text(encoding="utf-8"))["status"], "regressed")
            self.assertIn("Executive Summary", output_md.read_text(encoding="utf-8"))

    def _summary_report(self, path: Path, *, strict_accepted: int, case_count: int) -> Path:
        path.write_text(json.dumps({"summary": {"strict_accepted": strict_accepted, "case_count": case_count}}), encoding="utf-8")
        return path

    def _ai_report(self, path: Path, *, coverage: int) -> Path:
        path.write_text(
            json.dumps(
                {
                    "summary": {
                        "coverage": coverage,
                        "strict_existing": 223,
                        "ai_consensus": 90,
                        "ai_tie_break_resolved": 46,
                        "ai_unreadable": 17,
                        "ai_best_effort": 1,
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def _record(
        self,
        diagram_id: str,
        fen: str,
        *,
        runtime_status: str = "FEN_MACHINE_ACCEPTED",
        requires_review: bool = False,
    ) -> dict[str, object]:
        return {
            "diagram_id": diagram_id,
            "fen": fen,
            "runtime_status": runtime_status,
            "requires_review": requires_review,
        }


if __name__ == "__main__":
    unittest.main()
