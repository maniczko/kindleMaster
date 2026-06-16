from __future__ import annotations

import unittest

from scripts.import_fen_priority_review_batch import (
    CandidateRecord,
    MatchScore,
    _accept_match,
    _best_assignment,
    _caption_key,
    _caption_similarity,
    _placement_accuracy,
)


class ImportFenPriorityReviewBatchTests(unittest.TestCase):
    def test_caption_similarity_detects_numeric_caption_key(self) -> None:
        candidate = CandidateRecord(
            raw_index=1,
            reading_order=1,
            bbox=(0, 0, 100, 100),
            crop_bytes=b"",
            crop_sha256="0" * 64,
            recognized_fen="",
            recognized_placement="",
            confidence=0.0,
            requires_review=True,
            above_text="Diagram 11-11 A",
            below_text="",
        )

        similarity, key_exact = _caption_similarity("Diagram 11-11", candidate)

        self.assertTrue(key_exact)
        self.assertGreaterEqual(similarity, 1.0)
        self.assertEqual(_caption_key("Diagram 11-11"), "11-11")

    def test_placement_accuracy_reports_exact_match(self) -> None:
        fen = "6k1/5pp1/8/5P2/R6p/1P3n1P/3r4/6RK"
        self.assertEqual(_placement_accuracy(fen, fen), 1.0)
        self.assertLess(_placement_accuracy(fen, "8/8/8/8/8/8/8/8"), 0.9)

    def test_best_assignment_chooses_unique_global_maximum(self) -> None:
        matrix = [
            [
                MatchScore(10.0, 1.0, 1.0, True, True, 0),
                MatchScore(2.0, 0.0, 0.0, False, False, 0),
                MatchScore(1.0, 0.0, 0.0, False, False, 0),
            ],
            [
                MatchScore(9.0, 0.0, 0.0, False, False, 0),
                MatchScore(8.0, 0.0, 0.0, False, False, 0),
                MatchScore(1.0, 0.0, 0.0, False, False, 0),
            ],
        ]

        assignment = _best_assignment(matrix)

        self.assertEqual(assignment, [0, 1])

    def test_accept_match_requires_strong_evidence(self) -> None:
        accepted = _accept_match(
            MatchScore(
                score=3.0,
                placement_accuracy=0.90,
                caption_similarity=0.70,
                caption_key_exact=True,
                exact_fen=False,
                reading_order_penalty=0,
            ),
            margin=0.30,
        )
        rejected = _accept_match(
            MatchScore(
                score=1.5,
                placement_accuracy=0.62,
                caption_similarity=0.30,
                caption_key_exact=False,
                exact_fen=False,
                reading_order_penalty=0,
            ),
            margin=0.05,
        )

        self.assertTrue(accepted["accepted"])
        self.assertFalse(rejected["accepted"])


if __name__ == "__main__":
    unittest.main()
