from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
from chess_fen_review_store import FenReviewConflictError, FenReviewOwnershipError
from supabase_auth import SupabaseAuthConfig


class AppChessFenReviewPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app_module.app.test_client()

    def test_put_passes_revision_action_owner_and_change_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir)
            saved = {
                "status": "saved",
                "saved_at": "2026-07-20T10:00:00Z",
                "revision": 8,
                "session_status": "complete",
                "summary": {"total": 0, "pending": 0, "invalid": 0},
            }
            auth = app_module.AuthContext(
                authenticated=True,
                user_id="11111111-1111-1111-1111-111111111111",
            )
            with (
                patch.object(app_module, "_resolve_request_auth_context", return_value=auth),
                patch.object(app_module, "_get_conversion_job_for_auth", return_value={"user_id": auth.user_id}),
                patch.object(app_module, "_resolve_local_fen_review_dir", return_value=review_dir),
                patch("chess_fen_review_repository.ChessFenReviewRepository.save", return_value=saved) as save,
                patch(
                    "chess_fen_review_repository.ChessFenReviewRepository.load",
                    return_value={"session_status": "complete", "rows": []},
                ),
                patch.object(
                    app_module,
                    "_publish_verified_fen_review_artifacts",
                    return_value={"status": "published"},
                ) as publish,
            ):
                response = self.client.put(
                    "/convert/artifact/artifact-1/chess_fen_review_progress",
                    json={
                        "source_digest": "a" * 64,
                        "rows": [],
                        "expected_revision": 7,
                        "action": "close",
                        "change_source": "close",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["revision"], 8)
        self.assertEqual(save.call_args.kwargs["expected_revision"], 7)
        self.assertEqual(save.call_args.kwargs["action"], "close")
        self.assertEqual(save.call_args.kwargs["owner_user_id"], auth.user_id)
        self.assertEqual(save.call_args.kwargs["change_source"], "close")
        publish.assert_called_once()

    def test_post_publish_uses_closed_database_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            review_dir = Path(temp_dir)
            auth = app_module.AuthContext(authenticated=True, user_id="owner-a")
            review_payload = {
                "status": "ok",
                "session_status": "complete",
                "rows": [],
            }
            report = {"status": "published", "summary": {"fen_human_verified": 254}}
            with (
                patch.object(app_module, "_resolve_request_auth_context", return_value=auth),
                patch.object(
                    app_module,
                    "_get_conversion_job_for_auth",
                    return_value={"user_id": auth.user_id, "artifacts": {}},
                ),
                patch.object(app_module, "_resolve_local_fen_review_dir", return_value=review_dir),
                patch(
                    "chess_fen_review_repository.ChessFenReviewRepository.load",
                    return_value=review_payload,
                ),
                patch.object(
                    app_module,
                    "_publish_verified_fen_review_artifacts",
                    return_value=report,
                ) as publish,
            ):
                response = self.client.post(
                    "/convert/artifact/artifact-1/chess_fen_publish"
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["publication"]["summary"]["fen_human_verified"], 254)
        publish.assert_called_once_with(
            "artifact-1",
            {"user_id": auth.user_id, "artifacts": {}},
            review_dir=review_dir,
            review_payload=review_payload,
        )

    def test_reader_summary_separates_human_machine_and_unrecognized(self) -> None:
        positions = [
            {"status": "accepted", "fen": "fen-a", "fen_human_verified": True},
            {"status": "accepted", "fen": "fen-b", "fen_human_verified": False},
            {"status": "needs_review", "fen": "", "side_to_move": "unknown"},
        ]

        summary = app_module._reader_sidecar_summary(positions)

        self.assertEqual(summary["fen_human_verified"], 1)
        self.assertEqual(summary["fen_automatic"], 1)
        self.assertEqual(summary["fen_unrecognized"], 1)

    def test_stale_revision_returns_http_409_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth = app_module.AuthContext(authenticated=True, user_id="owner-a")
            with (
                patch.object(app_module, "_resolve_request_auth_context", return_value=auth),
                patch.object(app_module, "_get_conversion_job_for_auth", return_value={"status": "done"}),
                patch.object(app_module, "_resolve_local_fen_review_dir", return_value=Path(temp_dir)),
                patch(
                    "chess_fen_review_repository.ChessFenReviewRepository.save",
                    side_effect=FenReviewConflictError("stale revision"),
                ),
            ):
                response = self.client.put(
                    "/convert/artifact/artifact-1/chess_fen_review_progress",
                    json={"rows": [], "expected_revision": 2},
                )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "fen_review_revision_conflict")

    def test_database_progress_requires_login_when_supabase_auth_is_enabled(self) -> None:
        config = SupabaseAuthConfig(
            enabled=True,
            configured=True,
            url="https://project.supabase.co",
            publishable_key="public-key",
        )
        with (
            patch.object(app_module, "_resolve_request_auth_context", return_value=app_module.AuthContext()),
            patch.object(app_module, "load_supabase_auth_config", return_value=config),
        ):
            response = self.client.get("/convert/artifact/artifact-1/chess_fen_review_progress")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error_code"], "auth_required")

    def test_owner_mismatch_returns_http_403_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            auth = app_module.AuthContext(authenticated=True, user_id="owner-a")
            with (
                patch.object(app_module, "_resolve_request_auth_context", return_value=auth),
                patch.object(app_module, "_get_conversion_job_for_auth", return_value={"user_id": "owner-a"}),
                patch.object(app_module, "_resolve_local_fen_review_dir", return_value=Path(temp_dir)),
                patch(
                    "chess_fen_review_repository.ChessFenReviewRepository.save",
                    side_effect=FenReviewOwnershipError("owner mismatch"),
                ),
            ):
                response = self.client.put(
                    "/convert/artifact/artifact-1/chess_fen_review_progress",
                    json={"rows": [], "expected_revision": 1},
                )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error_code"], "fen_review_owner_mismatch")


if __name__ == "__main__":
    unittest.main()
