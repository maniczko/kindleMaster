from __future__ import annotations

import unittest

from chess_fen_workflow import (
    CHESS_FEN_WORKFLOW_SCHEMA_VERSION,
    CANDIDATE_DETECTED,
    DETERMINISTIC_CANDIDATE,
    MANUAL_DRAFT,
    PROFILE_READY,
    REVIEW_ONLY_WORKFLOW_STATES,
    candidate_workflow_state,
    profile_workflow_state,
    with_workflow_state,
)


class ChessFenWorkflowStateModelTests(unittest.TestCase):
    def test_candidate_workflow_state_tracks_presence_of_candidate_fen(self) -> None:
        self.assertEqual(candidate_workflow_state(""), CANDIDATE_DETECTED)
        self.assertEqual(
            candidate_workflow_state("4k3/8/8/8/8/8/8/4K3 w - - 0 1"),
            DETERMINISTIC_CANDIDATE,
        )

    def test_with_workflow_state_preserves_existing_fields(self) -> None:
        row = with_workflow_state({"id": "row-1", "fen": ""}, MANUAL_DRAFT)

        self.assertEqual(row["id"], "row-1")
        self.assertEqual(row["fen"], "")
        self.assertEqual(row["schema_version"], CHESS_FEN_WORKFLOW_SCHEMA_VERSION)
        self.assertEqual(row["workflow_state"], MANUAL_DRAFT)
        self.assertIn(MANUAL_DRAFT, REVIEW_ONLY_WORKFLOW_STATES)

    def test_profile_workflow_state_is_ready_only_for_ready_status(self) -> None:
        self.assertEqual(profile_workflow_state("ready"), PROFILE_READY)
        self.assertEqual(profile_workflow_state("failed"), "")


if __name__ == "__main__":
    unittest.main()
