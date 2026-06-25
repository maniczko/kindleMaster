from __future__ import annotations

import unittest

from chess_fen_blockers import (
    BLOCKER_CATEGORIES,
    categorize_blocker,
    classify_blocker_category,
    count_blocker_categories,
    empty_blocker_category_counts,
)


class ChessFenBlockerCategoryTests(unittest.TestCase):
    def test_required_category_template_is_stable(self) -> None:
        self.assertEqual(
            empty_blocker_category_counts(),
            {
                "crop_grid": 0,
                "recognition": 0,
                "placement": 0,
                "full_fen_validation": 0,
                "source_policy": 0,
                "confidence": 0,
                "metadata": 0,
                "ai_review_only": 0,
                "pgn": 0,
                "runtime_dependency": 0,
                "unknown": 0,
            },
        )
        self.assertEqual(len(BLOCKER_CATEGORIES), 11)

    def test_maps_required_fen_blocker_categories(self) -> None:
        cases = {
            "board_grid_not_detected": "crop_grid",
            "piece_template_confidence_below_threshold": "recognition",
            "placement_candidate_missing": "placement",
            "fen_must_have_six_fields": "full_fen_validation",
            "ai_review_only_source": "source_policy",
            "confidence_below_runtime_threshold": "confidence",
            "source_crop_hash_missing": "metadata",
            "ai_tie_break_resolved": "ai_review_only",
            "pgn_parse_failed": "pgn",
            "python_chess_unavailable": "runtime_dependency",
            "not_yet_known_code": "unknown",
        }

        for code, category in cases.items():
            with self.subTest(code=code):
                self.assertEqual(classify_blocker_category(code), category)

    def test_preserves_unknown_code_and_adds_category_to_blocker_dict(self) -> None:
        blocker = categorize_blocker({"code": "future_blocker_code", "message": "keep this"})

        self.assertEqual(blocker["code"], "future_blocker_code")
        self.assertEqual(blocker["message"], "keep this")
        self.assertEqual(blocker["category"], "unknown")

    def test_counts_categories_with_zero_template(self) -> None:
        counts = count_blocker_categories(["board_grid_not_detected", "python_chess_invalid_position"])

        self.assertEqual(counts["crop_grid"], 1)
        self.assertEqual(counts["full_fen_validation"], 1)
        self.assertEqual(counts["unknown"], 0)


if __name__ == "__main__":
    unittest.main()
