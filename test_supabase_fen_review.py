from __future__ import annotations

import json
import unittest

from supabase_fen_review import SupabaseFenReviewClient
from supabase_library import SupabaseLibraryConfig


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[object] = []

    def queue(self, response: object) -> None:
        self.responses.append(response)

    def __call__(self, url: str, *, method: str = "GET", headers=None, body=None, expect_json=True):
        self.calls.append(
            {
                "url": url,
                "method": method,
                "headers": dict(headers or {}),
                "body": body,
                "expect_json": expect_json,
            }
        )
        return self.responses.pop(0) if self.responses else {}


class SupabaseFenReviewTests(unittest.TestCase):
    def _config(self) -> SupabaseLibraryConfig:
        return SupabaseLibraryConfig(
            enabled=True,
            configured=True,
            url="https://project.supabase.co",
            service_role_key="service-role-secret",
        )

    def _row(self) -> dict:
        return {
            "artifact_id": "artifact-1",
            "diagram_id": "p001-d1",
            "diagram_fingerprint": "1" * 64,
            "source_document_sha256": "a" * 64,
            "square_labels": [""] * 64,
            "label_status": "needs_piece_labels",
            "id": "p001-d1",
            "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
            "square_diff_ack": True,
            "source_pdf": "C:/private/source.pdf",
            "crop_path": "C:/private/crop.png",
        }

    def test_load_review_reads_session_and_queryable_label_payloads(self) -> None:
        transport = FakeTransport()
        transport.queue(
            [
                {
                    "artifact_id": "artifact-1",
                    "source_document_sha256": "a" * 64,
                    "schema_version": "kindlemaster.fen_review_progress.v1",
                    "status": "active",
                    "summary": {"total": 1, "pending": 1},
                    "row_count": 1,
                    "revision": 4,
                    "saved_at": "2026-07-16T12:00:00Z",
                }
            ]
        )
        transport.queue([{"row_payload": self._row()}])
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        result = client.load_review(artifact_id="artifact-1")

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["storage"], "database")
        self.assertEqual(result["rows"][0]["diagram_id"], "p001-d1")
        self.assertEqual(result["revision"], 4)
        self.assertIn("/rest/v1/chess_fen_review_sessions?", transport.calls[0]["url"])
        self.assertIn("/rest/v1/chess_fen_review_labels?", transport.calls[1]["url"])
        self.assertEqual(transport.calls[0]["headers"]["Authorization"], "Bearer service-role-secret")

    def test_load_review_rejects_partial_database_state(self) -> None:
        transport = FakeTransport()
        transport.queue(
            [
                {
                    "artifact_id": "artifact-1",
                    "source_document_sha256": "a" * 64,
                    "row_count": 2,
                    "summary": {},
                }
            ]
        )
        transport.queue([{"row_payload": self._row()}])
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        with self.assertRaisesRegex(RuntimeError, "row_count_mismatch"):
            client.load_review(artifact_id="artifact-1")

    def test_save_review_uses_transactional_rpc_with_full_rows(self) -> None:
        transport = FakeTransport()
        transport.queue(
            {
                "artifact_id": "artifact-1",
                "saved_at": "2026-07-16T12:01:00Z",
                "row_count": 1,
                "summary": {"total": 1, "pending": 1},
                "storage": "database",
            }
        )
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        result = client.save_review(
            artifact_id="artifact-1",
            source_document_sha256="a" * 64,
            rows=[self._row()],
            summary={"total": 1, "pending": 1},
        )

        call = transport.calls[0]
        body = json.loads(call["body"].decode("utf-8"))
        self.assertIn("/rest/v1/rpc/save_chess_fen_review", call["url"])
        self.assertEqual(body["p_rows"][0]["diagram_fingerprint"], "1" * 64)
        self.assertEqual(len(body["p_rows"][0]["square_labels"]), 64)
        self.assertEqual(body["p_rows"][0]["id"], "p001-d1")
        self.assertEqual(body["p_expected_revision"], 0)
        self.assertEqual(body["p_action"], "save")
        self.assertEqual(body["p_change_source"], "autosave")
        self.assertTrue(body["p_rows"][0]["square_diff_ack"])
        self.assertNotIn("source_pdf", body["p_rows"][0])
        self.assertNotIn("crop_path", body["p_rows"][0])
        self.assertEqual(result["storage"], "database")
        self.assertEqual(result["submitted_count"], 1)

    def test_load_review_reuses_latest_owned_source_session(self) -> None:
        transport = FakeTransport()
        transport.queue([])
        transport.queue(
            [
                {
                    "artifact_id": "artifact-old",
                    "source_document_sha256": "a" * 64,
                    "schema_version": "kindlemaster.fen_review_progress.v2",
                    "status": "complete",
                    "summary": {"total": 1, "pending": 0},
                    "row_count": 1,
                    "revision": 8,
                    "saved_at": "2026-07-16T12:00:00Z",
                }
            ]
        )
        transport.queue([{"row_payload": self._row()}])
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        result = client.load_review(
            artifact_id="artifact-new",
            source_document_sha256="a" * 64,
            owner_user_id="11111111-1111-1111-1111-111111111111",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["reused_from_artifact_id"], "artifact-old")
        self.assertIn("owner_user_id=eq.11111111-1111-1111-1111-111111111111", transport.calls[1]["url"])
        self.assertIn("artifact_id=eq.artifact-old", transport.calls[2]["url"])

    def test_load_review_recovers_source_bound_ownerless_legacy_session(self) -> None:
        transport = FakeTransport()
        transport.queue([])
        transport.queue([])
        transport.queue(
            [
                {
                    "artifact_id": "artifact-legacy",
                    "source_document_sha256": None,
                    "schema_version": "kindlemaster.fen_review_progress.v1",
                    "status": "complete",
                    "summary": {"total": 1, "pending": 0},
                    "row_count": 1,
                    "revision": 0,
                    "saved_at": "2026-07-16T12:00:00Z",
                }
            ]
        )
        transport.queue([{"job_id": "artifact-legacy"}])
        transport.queue([{"row_payload": self._row()}])
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        result = client.load_review(
            artifact_id="artifact-new",
            source_document_sha256="a" * 64,
            owner_user_id="11111111-1111-1111-1111-111111111111",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["reused_from_artifact_id"], "artifact-legacy")
        self.assertEqual(result["source_document_sha256"], "a" * 64)
        self.assertIn("source_document_sha256=is.null", transport.calls[2]["url"])
        self.assertIn("owner_user_id=is.null", transport.calls[2]["url"])
        self.assertIn("job_id=eq.artifact-legacy", transport.calls[3]["url"])
        self.assertIn("user_id=eq.11111111-1111-1111-1111-111111111111", transport.calls[3]["url"])
        self.assertIn("artifact_id=eq.artifact-legacy", transport.calls[4]["url"])

    def test_load_review_limits_current_artifact_to_owner_or_unclaimed_legacy_session(self) -> None:
        transport = FakeTransport()
        transport.queue([])
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        result = client.load_review(
            artifact_id="artifact-1",
            owner_user_id="11111111-1111-1111-1111-111111111111",
        )

        self.assertIsNone(result)
        self.assertIn("owner_user_id.eq.11111111-1111-1111-1111-111111111111", transport.calls[0]["url"])
        self.assertIn("owner_user_id.is.null", transport.calls[0]["url"])

    def test_load_review_does_not_reuse_legacy_session_owned_by_another_user(self) -> None:
        transport = FakeTransport()
        transport.queue([])
        transport.queue([])
        transport.queue(
            [
                {
                    "artifact_id": "artifact-legacy",
                    "source_document_sha256": None,
                    "row_count": 1,
                    "summary": {},
                }
            ]
        )
        transport.queue([])
        client = SupabaseFenReviewClient(self._config(), transport=transport)

        result = client.load_review(
            artifact_id="artifact-new",
            source_document_sha256="a" * 64,
            owner_user_id="11111111-1111-1111-1111-111111111111",
        )

        self.assertIsNone(result)
        self.assertEqual(len(transport.calls), 4)
        self.assertIn("conversion_jobs", transport.calls[3]["url"])


if __name__ == "__main__":
    unittest.main()
