from __future__ import annotations

import unittest

from chess_exercise_index import (
    build_source_exercise_index,
    canonical_exercise_id,
)
from chess_source_notation import replay_source_notation_blocks


STARTING_FEN = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/"
    "RNBQKBNR w KQkq - 0 1"
)


class ChessExerciseIndexTests(unittest.TestCase):
    def test_canonical_exercise_id_normalizes_printed_variants(self) -> None:
        self.assertEqual(canonical_exercise_id("Ex. 01-005"), "1-5")
        self.assertEqual(canonical_exercise_id("Exercise 3.12"), "3-12")
        self.assertEqual(canonical_exercise_id("3\u201314"), "3-14")
        self.assertEqual(canonical_exercise_id("Chapter 3"), "")

    def test_exact_source_assignment_selects_verified_diagram(self) -> None:
        payload = self._source_payload()
        index = build_source_exercise_index(
            payload,
            [self._diagram()],
            [
                {
                    "exercise_id": "Ex. 1-1",
                    "diagram_id": "diagram-1",
                    "status": "exact",
                    "source": "source_text_geometry",
                    "confidence": 1.0,
                    "auto_accepted": True,
                }
            ],
        )

        record = index["records"][0]
        self.assertEqual(record["resolution_status"], "exact")
        self.assertEqual(record["selected_diagram_id"], "diagram-1")
        self.assertEqual(record["selected_diagram_fingerprint"], "fp-1")
        self.assertTrue(record["selected_full_fen_trusted"])
        self.assertEqual(index["summary"]["review_queue_count"], 0)

    def test_vision_candidate_never_selects_diagram_by_itself(self) -> None:
        index = build_source_exercise_index(
            self._source_payload(),
            [self._diagram()],
            [
                {
                    "exercise_id": "1-1",
                    "diagram_id": "diagram-1",
                    "status": "candidate",
                    "source": "tesseract_label_crop",
                    "confidence": 0.99,
                    "auto_accepted": False,
                }
            ],
        )

        record = index["records"][0]
        self.assertEqual(record["resolution_status"], "candidate")
        self.assertEqual(record["selected_diagram_id"], "")
        self.assertEqual(index["summary"]["candidate_count"], 1)
        self.assertFalse(
            index["review_queue"][0]["model_route"]["auto_accept"]
        )

    def test_duplicate_resolved_assignment_is_a_conflict(self) -> None:
        assignments = [
            {
                "exercise_id": "1-1",
                "diagram_id": diagram_id,
                "status": "exact",
                "source": "source_text_geometry",
                "confidence": 1.0,
                "auto_accepted": True,
            }
            for diagram_id in ("diagram-1", "diagram-2")
        ]
        index = build_source_exercise_index(
            self._source_payload(),
            [
                self._diagram(),
                self._diagram(
                    diagram_id="diagram-2",
                    fingerprint="fp-2",
                ),
            ],
            assignments,
        )

        record = index["records"][0]
        self.assertEqual(record["resolution_status"], "conflict")
        self.assertIn("duplicate_resolved_diagrams", record["blockers"])
        self.assertEqual(record["selected_diagram_id"], "")

    def test_unassigned_diagrams_are_classified_by_page_evidence(
        self,
    ) -> None:
        unrelated = self._diagram(
            diagram_id="diagram-unrelated",
            fingerprint="fp-unrelated",
        )
        unrelated["page_number"] = 80
        index = build_source_exercise_index(
            self._source_payload(),
            [
                self._diagram(),
                self._diagram(
                    diagram_id="diagram-orphan",
                    fingerprint="fp-orphan",
                ),
                unrelated,
            ],
            [
                {
                    "exercise_id": "1-1",
                    "diagram_id": "diagram-1",
                    "source_page": 14,
                    "status": "exact",
                    "auto_accepted": True,
                }
            ],
        )

        queued = {
            item["diagram_id"]: item["review_type"]
            for item in index["review_queue"]
            if item["review_type"] in {
                "orphan_diagram",
                "unclassified_diagram",
            }
        }
        self.assertEqual(
            queued,
            {
                "diagram-orphan": "orphan_diagram",
                "diagram-unrelated": "unclassified_diagram",
            },
        )

    def test_replay_uses_index_and_rejects_loose_record_label(self) -> None:
        source = self._source_payload()
        diagram = self._diagram()
        diagram["exercise_id"] = "1-1"
        index = build_source_exercise_index(
            source,
            [diagram],
            [
                {
                    "exercise_id": "1-1",
                    "diagram_id": "diagram-1",
                    "status": "candidate",
                    "source": "tesseract_label_crop",
                    "confidence": 0.99,
                    "auto_accepted": False,
                }
            ],
        )

        replayed = replay_source_notation_blocks(
            source,
            [diagram],
            exercise_index=index,
        )
        block = replayed["pages"]["16"]["solution_blocks"][0]

        self.assertEqual(block["replay_status"], "review")
        self.assertIn("exercise_index_candidate", block["blockers"])
        self.assertFalse(block["accepted_pgn"])

    @staticmethod
    def _source_payload() -> dict[str, object]:
        return {
            "source_pdf_sha256": "a" * 64,
            "pages": {
                "16": {
                    "page_number": 16,
                    "solution_blocks": [
                        {
                            "exercise_id": "1-1",
                            "source_label": "Ex. 1-1",
                            "page_number": 16,
                            "bbox": [10, 20, 200, 80],
                            "notation_text": "1.e4 e5 2.Nf3 Nc6",
                            "blockers": [],
                        }
                    ],
                }
            },
        }

    @staticmethod
    def _diagram(
        *,
        diagram_id: str = "diagram-1",
        fingerprint: str = "fp-1",
    ) -> dict[str, object]:
        return {
            "id": diagram_id,
            "diagram_fingerprint": fingerprint,
            "page_number": 14,
            "bbox": [10, 30, 120, 140],
            "full_fen": STARTING_FEN,
            "fen_human_verified": True,
            "confirmed_diagram": True,
            "publication_included": True,
        }


if __name__ == "__main__":
    unittest.main()
