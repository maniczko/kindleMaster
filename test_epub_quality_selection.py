from __future__ import annotations

import unittest
from unittest.mock import patch

from epub_quality_selection import select_epub_by_quality


def _scoring(
    *,
    score: float,
    kindle_ready: bool,
    release_verdict: str,
    blocker_count: int = 0,
    technical_valid: bool = True,
    issues: list[dict] | None = None,
) -> dict:
    return {
        "status": "passed" if kindle_ready and not blocker_count else "failed",
        "technical_valid": technical_valid,
        "kindle_ready": kindle_ready,
        "premium_ready": score >= 9.0 and kindle_ready and not blocker_count,
        "premium_score": score,
        "release_verdict": release_verdict,
        "issue_counts": {"blocker": blocker_count},
        "issues": issues or [],
    }


class EpubQualitySelectionTests(unittest.TestCase):
    def test_rejects_candidate_when_quality_regresses(self) -> None:
        baseline = _scoring(score=9.1, kindle_ready=True, release_verdict="ready_with_review")
        candidate = _scoring(
            score=7.0,
            kindle_ready=False,
            release_verdict="release_blocked",
            blocker_count=1,
            issues=[{"severity": "blocker", "code": "magazine_non_content_chapter"}],
        )

        with patch("epub_quality_selection.score_epub_premium_quality", side_effect=[baseline, candidate]):
            result = select_epub_by_quality(
                b"baseline",
                b"candidate",
                baseline_label="pre_recovery",
                candidate_label="recovered",
                baseline_epubcheck={"status": "passed"},
                candidate_epubcheck={"status": "passed"},
            )

        self.assertEqual(result.selected_bytes, b"baseline")
        self.assertEqual(result.selected_scoring, baseline)
        self.assertEqual(result.report["status"], "rejected")
        self.assertEqual(result.report["selected_stage"], "pre_recovery")
        self.assertEqual(result.report["rejected_stage"], "recovered")
        self.assertIn("recovery_rejected_due_to_quality_regression", result.report["reason_codes"])
        self.assertIn("quality_monotonic_regression", result.report["reason_codes"])
        self.assertIn("premium_score_regressed", result.report["reason_codes"])
        self.assertIn("new_magazine_non_content_chapter", result.report["reason_codes"])
        self.assertLess(result.report["candidate_score"], result.report["baseline_score"])

    def test_accepts_candidate_without_hard_regression(self) -> None:
        baseline = _scoring(
            score=6.2,
            kindle_ready=False,
            release_verdict="release_blocked",
            blocker_count=1,
        )
        candidate = _scoring(score=8.0, kindle_ready=True, release_verdict="ready_with_review")

        with patch("epub_quality_selection.score_epub_premium_quality", side_effect=[baseline, candidate]):
            result = select_epub_by_quality(
                b"baseline",
                b"candidate",
                baseline_label="pre_recovery",
                candidate_label="recovered",
                baseline_epubcheck={"status": "passed"},
                candidate_epubcheck={"status": "passed"},
            )

        self.assertEqual(result.selected_bytes, b"candidate")
        self.assertEqual(result.selected_scoring, candidate)
        self.assertEqual(result.report["status"], "accepted")
        self.assertEqual(result.report["selected_stage"], "recovered")
        self.assertEqual(result.report["rejected_stage"], "")
        self.assertTrue(result.report["selected_is_recovered"])
        self.assertIn("premium_score_improved", result.report["reason_codes"])
        self.assertIn("kindle_ready_improved", result.report["reason_codes"])


if __name__ == "__main__":
    unittest.main()
