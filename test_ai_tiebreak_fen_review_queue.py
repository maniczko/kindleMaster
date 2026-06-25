from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_ai_tiebreak_fen_review_queue import (
    MISSING_ARTIFACT,
    build_ai_tiebreak_fen_review_queue,
    write_markdown,
)


FEN_A = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
FEN_B = "4k3/8/8/8/8/8/4Q3/4K3 w - - 0 1"
FEN_C = "4k3/8/8/8/8/3R4/4Q3/4K3 w - - 0 1"
FEN_D = "4k3/8/8/8/8/3R4/4Q3/R3K3 w - - 0 1"


class AiTiebreakFenReviewQueueTests(unittest.TestCase):
    def test_two_fens_differing_on_one_square_produce_one_square_diff(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "page": 10,
                    "filename": "d1.png",
                    "ai_category": "ai_tie_break_resolved",
                    "ai_selected_fen": FEN_B,
                    "candidate_fens": [FEN_A, FEN_B],
                    "tie_break_reason": "ai_selected_between_template_and_model",
                }
            ]
        )

        self.assertEqual(payload["summary"]["queue_count"], 1)
        record = payload["records"][0]
        self.assertEqual(record["diagram_id"], "d1")
        self.assertEqual(record["ai_selected_fen"], FEN_B)
        self.assertEqual(record["candidate_fens"], [FEN_A, FEN_B])
        self.assertEqual(record["conflict_count"], 1)
        self.assertEqual(record["square_diffs"][0]["square"], "e2")
        self.assertEqual(record["square_diffs"][0]["candidate_a"], "empty")
        self.assertEqual(record["square_diffs"][0]["candidate_b"], "Q")
        self.assertEqual(record["square_diffs"][0]["ai_selected"], "Q")
        self.assertTrue(record["requires_human_verification"])
        self.assertEqual(record["recommended_action"], "verify_conflict_squares")
        self.assertEqual(record["verification_priority"], "fastest_to_verify")
        self.assertNotEqual(record.get("status"), "strict_accepted")

    def test_multiple_differences_produce_correct_conflict_count(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "ai_category": "ai_tie_break_resolved",
                    "ai_selected_fen": FEN_C,
                    "candidate_fens": [FEN_A, FEN_C],
                }
            ]
        )

        record = payload["records"][0]
        self.assertEqual(record["conflict_count"], 2)
        self.assertEqual({diff["square"] for diff in record["square_diffs"]}, {"d3", "e2"})
        self.assertEqual(record["verification_priority"], "fastest_to_verify")

    def test_missing_candidate_fens_produce_missing_artifact(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "ai_category": "ai_tie_break_resolved",
                    "ai_selected_fen": FEN_A,
                }
            ]
        )

        record = payload["records"][0]
        self.assertEqual(record["status"], MISSING_ARTIFACT)
        self.assertEqual(record["code"], "candidate_fens_missing")
        self.assertEqual(record["square_diffs"], [])
        self.assertIn("candidate_fens_missing", record["blockers"])
        self.assertEqual(record["blocker_items"][0]["category"], "metadata")
        self.assertEqual(payload["summary"]["by_category"]["metadata"], 1)

    def test_identical_candidate_fens_produce_missing_conflict_artifact(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "ai_category": "ai_tie_break_resolved",
                    "ai_selected_fen": FEN_A,
                    "candidate_fens": [FEN_A, FEN_A],
                }
            ]
        )

        record = payload["records"][0]
        self.assertEqual(record["status"], MISSING_ARTIFACT)
        self.assertEqual(record["code"], "candidate_conflicts_missing")
        self.assertEqual(record["square_diffs"], [])

    def test_ai_selected_fen_not_in_candidates_creates_blocker(self) -> None:
        payload = self._build(
            [
                {
                    "diagram_id": "d1",
                    "ai_category": "ai_tie_break_resolved",
                    "ai_selected_fen": FEN_D,
                    "candidate_fens": [FEN_A, FEN_B],
                }
            ]
        )

        record = payload["records"][0]
        self.assertIn("ai_selection_not_in_candidates", record["blockers"])
        self.assertIn("ai_review_only", {blocker["category"] for blocker in record["blocker_items"]})
        self.assertEqual(payload["summary"]["ai_selection_not_in_candidates_count"], 1)

    def test_known_forty_six_tiebreak_records_are_queued_and_other_ai_categories_excluded(self) -> None:
        records = [
            {
                "diagram_id": f"d{index:03d}",
                "page": index,
                "filename": f"d{index:03d}.png",
                "ai_category": "ai_tie_break_resolved",
                "ai_selected_fen": FEN_B,
                "candidate_fens": [FEN_A, FEN_B],
            }
            for index in range(46)
        ]
        records.extend(
            [
                {"diagram_id": "consensus", "ai_category": "ai_consensus", "ai_selected_fen": FEN_A, "candidate_fens": [FEN_A, FEN_B]},
                {"diagram_id": "best", "ai_category": "ai_best_effort", "ai_selected_fen": FEN_A, "candidate_fens": [FEN_A, FEN_B]},
                {"diagram_id": "unreadable", "ai_category": "ai_unreadable", "ai_selected_fen": FEN_A, "candidate_fens": [FEN_A, FEN_B]},
            ]
        )

        payload = self._build(records)

        self.assertEqual(payload["summary"]["queue_count"], 46)
        self.assertEqual(payload["summary"]["skipped"]["ai_consensus"], 1)
        self.assertEqual(payload["summary"]["skipped"]["ai_best_effort"], 1)
        self.assertEqual(payload["summary"]["skipped"]["ai_unreadable"], 1)

    def test_currently_strict_accepted_tiebreak_record_is_not_requeued(self) -> None:
        payload = self._build(
            [{"diagram_id": "d1", "ai_category": "ai_tie_break_resolved", "ai_selected_fen": FEN_B, "candidate_fens": [FEN_A, FEN_B]}],
            current_records=[{"diagram_id": "d1", "runtime_status": "FEN_MACHINE_ACCEPTED", "selected_value": FEN_B, "requires_review": False}],
        )

        self.assertEqual(payload["summary"]["queue_count"], 0)
        self.assertEqual(payload["summary"]["skipped"]["already_strict_accepted"], 1)

    def test_markdown_sorts_cases_by_conflict_count_lowest_first_and_highlights_fastest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_md = root / "queue.md"
            payload = {
                "ai_coverage_path": "ai.json",
                "current_report_path": "current.json",
                "summary": {"queue_count": 2, "missing_artifact_count": 0, "fastest_to_verify_count": 1},
                "records": [
                    {
                        "diagram_id": "many",
                        "page": 2,
                        "crop_path": "many.png",
                        "conflict_count": 4,
                        "blockers": [],
                        "verification_priority": "standard_review",
                        "recommended_action": "verify_conflict_squares",
                    },
                    {
                        "diagram_id": "one",
                        "page": 1,
                        "crop_path": "one.png",
                        "conflict_count": 1,
                        "blockers": [],
                        "verification_priority": "fastest_to_verify",
                        "recommended_action": "verify_conflict_squares",
                    },
                ],
            }

            write_markdown(payload, output_md)
            text = output_md.read_text(encoding="utf-8")

        self.assertLess(text.index("one"), text.index("many"))
        self.assertIn("FASTEST_TO_VERIFY", text)

    def _build(self, ai_records: list[dict[str, object]], current_records: list[dict[str, object]] | None = None) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ai_report = root / "ai.json"
            current_report = root / "current.json"
            ai_report.write_text(json.dumps({"records": ai_records}), encoding="utf-8")
            current_report.write_text(json.dumps({"records": current_records or []}), encoding="utf-8")
            return build_ai_tiebreak_fen_review_queue(ai_report, current_report)


if __name__ == "__main__":
    unittest.main()
