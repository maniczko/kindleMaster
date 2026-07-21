from __future__ import annotations

import unittest

from chess_fen_review_ui import render_fen_manual_review_html


class ChessFenReviewUiPersistenceTests(unittest.TestCase):
    def test_review_ui_sends_auth_revision_and_session_actions(self) -> None:
        row = {
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

        rendered = render_fen_manual_review_html([row], source_identity=row, artifact_id="artifact-1")

        self.assertIn('id="toggle-session"', rendered)
        self.assertIn("expected_revision:serverRevision", rendered)
        self.assertIn("change_source:", rendered)
        self.assertIn("Authorization:`Bearer ${token}`", rendered)
        self.assertIn("sessionStatus === 'complete' ? 'reopen' : 'close'", rendered)
        self.assertIn("revisionConflict", rendered)
        self.assertIn("Konflikt wersji", rendered)


if __name__ == "__main__":
    unittest.main()
