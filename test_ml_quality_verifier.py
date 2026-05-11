from __future__ import annotations

import unittest

from ml_quality_verifier import build_ai_quality_verification


class MlQualityVerifierTests(unittest.TestCase):
    def test_blocks_when_premium_scoring_reports_blockers(self) -> None:
        payload = build_ai_quality_verification(
            premium_scoring={
                "status": "failed",
                "technical_valid": True,
                "kindle_ready": False,
                "premium_ready": False,
                "premium_score": 4.8,
                "issue_counts": {"blocker": 1},
                "issues": [
                    {
                        "severity": "blocker",
                        "code": "language_label_contamination",
                        "message": "English EPUB contains Polish structural labels.",
                        "source": "language",
                    }
                ],
            },
            quality_report={"validation_status": "passed"},
            analysis={"profile": "magazine_reflow", "route_decision": {"mode": "shadow"}},
            quality_gate_mode="draft",
        )

        self.assertEqual(payload["decision"], "block")
        self.assertEqual(payload["status"], "failed")
        self.assertIn("premium-blockers", payload["reason_codes"])
        self.assertEqual(payload["top_issues"][0]["code"], "language_label_contamination")
        self.assertEqual(len(payload["features_hash"]), 16)

    def test_missing_model_file_falls_back_without_hiding_premium_blockers(self) -> None:
        payload = build_ai_quality_verification(
            premium_scoring={
                "status": "failed",
                "technical_valid": False,
                "kindle_ready": False,
                "premium_score": 1.0,
                "issue_counts": {"blocker": 1},
                "issues": [{"severity": "blocker", "code": "technical_epub_unreadable"}],
            },
            model_path="models/missing-quality-verifier.json",
        )

        self.assertEqual(payload["decision"], "block")
        self.assertEqual(payload["model_version"], "quality-verifier-v1-bootstrap")
        self.assertIn("technical-invalid", payload["reason_codes"])


if __name__ == "__main__":
    unittest.main()
