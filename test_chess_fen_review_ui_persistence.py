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
        self.assertIn('id="publish-fen" disabled', rendered)
        self.assertIn("expected_revision:serverRevision", rendered)
        self.assertIn("change_source:", rendered)
        self.assertIn("Authorization:`Bearer ${token}`", rendered)
        self.assertIn("sessionStatus === 'complete' ? 'reopen' : 'close'", rendered)
        self.assertIn("revisionConflict", rendered)
        self.assertIn("Konflikt wersji", rendered)
        self.assertIn("/chess_fen_publish", rendered)
        self.assertIn("publishFen.addEventListener('click',publishVerifiedFenToServer)", rendered)
        self.assertIn("summary.fen_human_verified", rendered)
        self.assertIn("summary.false_positive_candidates", rendered)
        self.assertIn('id="metric-rejected"', rendered)
        self.assertIn('id="metric-unreadable"', rendered)
        self.assertIn("Nowy diagram z etykiety", rendered)
        self.assertIn('class="verified-render-board"', rendered)
        self.assertIn("function renderVerifiedPreview(card,row,check)", rendered)
        self.assertIn("Kandydat odrzucony jako false positive.", rendered)

    def test_review_ui_distinguishes_login_from_server_failure(self) -> None:
        row = {
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

        rendered = render_fen_manual_review_html([row], source_identity=row, artifact_id="artifact-1")

        self.assertIn('id="auth-link"', rendered)
        self.assertIn("Zaloguj się, aby odczytywać i zapisywać bazę", rendered)
        self.assertIn("error.authRequired", rendered)
        self.assertIn("response.status === 401", rendered)
        self.assertIn("if (!storedAccessToken()) { authenticationRequired(false); return; }", rendered)
        self.assertIn("sessionStorage.getItem('kindlemaster.fen-review.access-token')", rendered)
        self.assertIn("let bridgedToken = ''", rendered)
        self.assertIn("function nestedAccessToken(value,depth=0)", rendered)
        self.assertIn("window.addEventListener('storage'", rendered)

    def test_closed_session_does_not_keep_stale_local_save_pending(self) -> None:
        row = {
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

        rendered = render_fen_manual_review_html([row], source_identity=row, artifact_id="artifact-1")

        self.assertIn("if (sessionStatus === 'complete') { serverSavePending = false; return; }", rendered)
        self.assertIn("const keepLocal = sessionStatus !== 'complete'", rendered)
        self.assertIn("if (!keepLocal) {\n          serverSavePending = false;", rendered)

    def test_review_ui_skips_auto_save_when_local_rows_match_server_rows(self) -> None:
        row = {
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

        rendered = render_fen_manual_review_html([row], source_identity=row, artifact_id="artifact-1")

        self.assertIn("function reviewRowsSignature(rows)", rendered)
        self.assertIn("const localRowsMatchServer = reviewRowsSignature(cards.map(rowFor))", rendered)
        self.assertIn("&& !localRowsMatchServer;", rendered)
        self.assertIn("const saveError = String(error.message", rendered)

    def test_review_ui_supports_placement_only_close_guard_and_image_retry(self) -> None:
        row = {
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
        }

        rendered = render_fen_manual_review_html([row], source_identity=row, artifact_id="artifact-1")

        self.assertIn('option value="placement_verified"', rendered)
        self.assertIn("['verified','placement_verified','rejected','unreadable']", rendered)
        self.assertIn("if (action === 'close')", rendered)
        self.assertIn("Uzupełnij przed zamknięciem", rendered)
        self.assertIn("function enableImageRetry()", rendered)
        self.assertIn("retryUrl.searchParams.set('km_retry','1')", rendered)


if __name__ == "__main__":
    unittest.main()
