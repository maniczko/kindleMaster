from __future__ import annotations

import unittest

from scripts.build_chess_piece_templates import PIECE_TEMPLATE_NAMES
from scripts.check_chess_fen_profile_ready import _profile_readiness_breakdown


class ChessFenProfileReadinessBreakdownTests(unittest.TestCase):
    def test_zero_labels_are_not_diagnostic_ready(self) -> None:
        result = _profile_readiness_breakdown(
            label_validation={"status": "failed", "valid_label_count": 0},
            template_summary={},
            evaluation={},
            holdout_evaluation={},
            accepted_audit_summary={},
            min_seed_labels=20,
            min_exact_accuracy=0.90,
            accepted_for_corpus=False,
            require_holdout=True,
            require_accepted_audit=True,
        )

        self.assertFalse(result["diagnostic_ready"])
        self.assertFalse(result["runtime_ready"])
        self.assertFalse(result["corpus_ready"])
        self.assertIn("add at least one valid human-verified label", result["next_actions"][0])

    def test_twenty_labels_remain_diagnostic_only_without_runtime_coverage(self) -> None:
        result = _profile_readiness_breakdown(
            label_validation={"status": "passed", "valid_label_count": 20},
            template_summary={"status": "ok", "label_counts": {"K-white": 20, "k-black": 20}},
            evaluation={"status": "passed", "exact_fen_accuracy": 1.0, "false_positive_count": 0},
            holdout_evaluation={"status": "passed", "holdout_eval": {"exact_fen_accuracy": 1.0, "false_positive_count": 0}},
            accepted_audit_summary={"status": "ok", "critical_risk_count": 0, "high_risk_count": 0},
            min_seed_labels=20,
            min_exact_accuracy=0.90,
            accepted_for_corpus=False,
            require_holdout=True,
            require_accepted_audit=True,
        )

        self.assertTrue(result["diagnostic_ready"])
        self.assertFalse(result["runtime_ready"])
        self.assertFalse(result["corpus_ready"])
        self.assertIn("Q-white", result["piece_coverage"]["missing_piece_templates"])

    def test_fifty_labels_with_complete_piece_coverage_are_runtime_ready(self) -> None:
        label_counts = {template: 1 for template in set(PIECE_TEMPLATE_NAMES.values())}
        result = _profile_readiness_breakdown(
            label_validation={"status": "passed", "valid_label_count": 50},
            template_summary={"status": "ok", "label_counts": label_counts},
            evaluation={"status": "passed", "exact_fen_accuracy": 0.95, "false_positive_count": 0},
            holdout_evaluation={"status": "passed", "holdout_eval": {"exact_fen_accuracy": 0.95, "false_positive_count": 0}},
            accepted_audit_summary={"status": "ok", "critical_risk_count": 0, "high_risk_count": 0},
            min_seed_labels=20,
            min_exact_accuracy=0.90,
            accepted_for_corpus=False,
            require_holdout=True,
            require_accepted_audit=True,
        )

        self.assertTrue(result["diagnostic_ready"])
        self.assertTrue(result["runtime_ready"])
        self.assertFalse(result["corpus_ready"])
        self.assertTrue(result["piece_coverage"]["complete_piece_coverage"])

    def test_accepted_for_corpus_controls_corpus_ready_level(self) -> None:
        label_counts = {template: 1 for template in set(PIECE_TEMPLATE_NAMES.values())}
        result = _profile_readiness_breakdown(
            label_validation={"status": "passed", "valid_label_count": 50},
            template_summary={"status": "ok", "label_counts": label_counts},
            evaluation={"status": "passed", "exact_fen_accuracy": 1.0, "false_positive_count": 0},
            holdout_evaluation={"status": "passed", "holdout_eval": {"exact_fen_accuracy": 1.0, "false_positive_count": 0}},
            accepted_audit_summary={"status": "ok", "critical_risk_count": 0, "high_risk_count": 0},
            min_seed_labels=20,
            min_exact_accuracy=0.90,
            accepted_for_corpus=True,
            require_holdout=True,
            require_accepted_audit=True,
        )

        self.assertTrue(result["corpus_ready"])


if __name__ == "__main__":
    unittest.main()
