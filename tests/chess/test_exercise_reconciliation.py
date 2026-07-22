from __future__ import annotations

import unittest

from chess_exercise_reconciliation import (
    RECONCILIATION_SCHEMA,
    normalize_identity_text,
    reconcile_exercise_solution_pairs,
)


class ChessExerciseReconciliationTests(unittest.TestCase):
    def test_title_normalization_is_case_punctuation_and_diacritic_insensitive(self) -> None:
        self.assertEqual(
            normalize_identity_text("  Iwańczuk — Kasparow, Łódź  1994 "),
            "iwanczuk kasparow lodz 1994",
        )

    def test_exact_number_and_normalized_title_create_unique_match(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [
                {
                    "exercise_id": "exercise-301",
                    "printed_number": 301,
                    "raw_title": "Karpov – Kasparov, Moscow 1985",
                    "players": ["Karpov", "Kasparov"],
                    "location": "Moscow",
                    "year": 1985,
                }
            ],
            [
                {
                    "solution_id": "solution-301",
                    "solution_number": 301,
                    "solution_title": "  Karpov - Kasparov,  Moscow 1985 ",
                    "players": ["Karpov", "Kasparov"],
                    "location": "Moscow",
                    "year": 1985,
                }
            ],
        )

        decision = report.decisions[0]
        self.assertEqual(report.schema, RECONCILIATION_SCHEMA)
        self.assertEqual(decision.status, "normalized")
        self.assertTrue(decision.accepted)
        self.assertFalse(decision.production_blocked)
        self.assertEqual(decision.selected_solution_id, "solution-301")
        self.assertGreaterEqual(decision.score, 0.9)

    def test_title_mismatch_blocks_even_when_number_matches(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [{"exercise_id": "301", "printed_number": 301, "raw_title": "Karpov - Kasparov, Moscow 1985"}],
            [{"solution_id": "301", "solution_number": 301, "solution_title": "Tal - Botvinnik, Riga 1960"}],
        )

        decision = report.decisions[0]
        self.assertEqual(decision.status, "mismatch")
        self.assertTrue(decision.production_blocked)
        self.assertEqual(decision.blocking_reason, "normalized_title_mismatch")
        self.assertIn("normalized_title_mismatch", decision.alternatives[0].blocking_mismatches)

    def test_high_similarity_title_is_reported_but_not_auto_swapped(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [
                {
                    "exercise_id": "exercise-422",
                    "printed_number": 422,
                    "raw_title": "Ivanchuk - Anand, Linares 1992",
                }
            ],
            [
                {
                    "solution_id": "solution-422",
                    "solution_number": 422,
                    "solution_title": "Ivanchuk - Anand, Linares 1993",
                }
            ],
        )

        decision = report.decisions[0]
        self.assertEqual(decision.status, "mismatch")
        self.assertFalse(decision.accepted)
        self.assertIn("high_similarity_title_alternative", decision.alternatives[0].evidence)
        self.assertGreater(decision.alternatives[0].title_similarity, 0.9)

    def test_duplicate_canonical_candidates_remain_ambiguous(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [{"exercise_id": "exercise-500", "printed_number": 500, "raw_title": "A - B, Berlin 2001"}],
            [
                {"solution_id": "candidate-a", "solution_number": 500, "solution_title": "A - B, Berlin 2001"},
                {"solution_id": "candidate-b", "solution_number": 500, "solution_title": "A - B, Berlin 2001"},
            ],
        )

        decision = report.decisions[0]
        self.assertEqual(decision.status, "ambiguous")
        self.assertTrue(decision.production_blocked)
        self.assertEqual(len(decision.alternatives), 2)
        self.assertEqual(report.to_dict()["summary"]["ambiguous_count"], 1)

    def test_swapped_ids_are_reconciled_by_canonical_identity(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [
                {"exercise_id": "exercise-301", "printed_number": 301, "raw_title": "Alpha - Beta, Oslo 2000"},
                {"exercise_id": "exercise-302", "printed_number": 302, "raw_title": "Gamma - Delta, Paris 2001"},
            ],
            [
                {
                    "solution_id": "exercise-302",
                    "solution_number": 301,
                    "solution_title": "Alpha - Beta, Oslo 2000",
                    "book_line": "1. Rxe6+",
                },
                {
                    "solution_id": "exercise-301",
                    "solution_number": 302,
                    "solution_title": "Gamma - Delta, Paris 2001",
                    "book_line": "1... Qh2+",
                },
            ],
        )

        first, second = report.decisions
        self.assertEqual(first.selected_solution_id, "exercise-302")
        self.assertEqual(second.selected_solution_id, "exercise-301")
        self.assertTrue(first.reassigned)
        self.assertTrue(second.reassigned)
        self.assertFalse(report.production_blocked)

    def test_same_id_fallback_preserves_legacy_data_but_blocks_release(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [{"exercise_id": "ex_12_9", "source_page": 12}],
            [{"exercise_id": "ex_12_9", "solution_page": 98, "book_line": "23. Rxe6+"}],
        )

        decision = report.decisions[0]
        self.assertEqual(decision.status, "legacy_id")
        self.assertTrue(decision.usable_with_review)
        self.assertTrue(decision.production_blocked)
        self.assertEqual(decision.blocking_reason, "canonical_identity_incomplete")

    def test_one_solution_cannot_be_claimed_by_two_exercises(self) -> None:
        report = reconcile_exercise_solution_pairs(
            [
                {"exercise_id": "a", "printed_number": 700, "raw_title": "A - B, Rome 2010"},
                {"exercise_id": "b", "printed_number": 700, "raw_title": "A - B, Rome 2010"},
            ],
            [{"solution_id": "one", "solution_number": 700, "solution_title": "A - B, Rome 2010"}],
        )

        self.assertEqual([decision.status for decision in report.decisions], ["ambiguous", "ambiguous"])
        self.assertTrue(report.production_blocked)


if __name__ == "__main__":
    unittest.main()
