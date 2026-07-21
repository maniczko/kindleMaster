from __future__ import annotations

import json
import unittest

from supabase_evidence_review import SupabaseEvidenceReviewClient
from supabase_library import SupabaseLibraryConfig


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.responses: list[object] = []

    def queue(self, response: object) -> None:
        self.responses.append(response)

    def __call__(self, url: str, *, method: str = "GET", headers=None, body=None, expect_json=True):
        self.calls.append({"url": url, "method": method, "headers": dict(headers or {}), "body": body})
        return self.responses.pop(0) if self.responses else {}


class SupabaseEvidenceReviewTests(unittest.TestCase):
    def _client(self, transport: FakeTransport) -> SupabaseEvidenceReviewClient:
        return SupabaseEvidenceReviewClient(
            SupabaseLibraryConfig(
                enabled=True,
                configured=True,
                url="https://project.supabase.co",
                service_role_key="service-role-secret",
            ),
            transport=transport,
        )

    def _row(self) -> dict:
        return {
            "canonical_diagram_fingerprint": "dfp_" + "1" * 32,
            "source_document_sha256": "a" * 64,
            "source_profile": "fixed-edition",
            "page": 1,
            "revision": 0,
        }

    def test_import_uses_backend_rpc_and_service_role(self) -> None:
        transport = FakeTransport()
        transport.queue({"artifact_id": "artifact-1", "row_count": 1})
        client = self._client(transport)

        client.import_queue(
            artifact_id="artifact-1",
            owner_user_id="",
            source_document_sha256="a" * 64,
            source_profile="fixed-edition",
            rows=[self._row()],
            summary={"total": 1, "open": 1},
        )

        call = transport.calls[0]
        payload = json.loads(call["body"].decode("utf-8"))
        self.assertIn("/rest/v1/rpc/import_chess_evidence_review_queue", call["url"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer service-role-secret")
        self.assertEqual(payload["p_rows"][0]["canonical_diagram_fingerprint"], "dfp_" + "1" * 32)

    def test_save_sends_expected_revision(self) -> None:
        transport = FakeTransport()
        transport.queue({"revision": 4, "row": self._row()})
        client = self._client(transport)

        result = client.save_item(
            artifact_id="artifact-1",
            source_document_sha256="a" * 64,
            source_profile="fixed-edition",
            canonical_diagram_fingerprint="dfp_" + "1" * 32,
            expected_revision=3,
            row=self._row(),
        )

        payload = json.loads(transport.calls[0]["body"].decode("utf-8"))
        self.assertEqual(payload["p_expected_revision"], 3)
        self.assertEqual(result["revision"], 4)

    def test_load_rejects_partial_database_state(self) -> None:
        transport = FakeTransport()
        transport.queue(
            [
                {
                    "artifact_id": "artifact-1",
                    "source_document_sha256": "a" * 64,
                    "source_profile": "fixed-edition",
                    "row_count": 2,
                }
            ]
        )
        transport.queue([{"row_payload": self._row(), "revision": 0, "label_status": "open"}])
        client = self._client(transport)

        with self.assertRaisesRegex(RuntimeError, "row_count_mismatch"):
            client.load_review(artifact_id="artifact-1")

    def test_load_item_reads_only_requested_record(self) -> None:
        transport = FakeTransport()
        transport.queue(
            [{
                "source_document_sha256": "a" * 64,
                "source_profile": "fixed-edition",
                "row_payload": self._row(),
                "revision": 3,
                "label_status": "verified_visible",
            }]
        )
        client = self._client(transport)

        result = client.load_item(
            artifact_id="artifact-1",
            canonical_diagram_fingerprint="dfp_" + "1" * 32,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["row"]["revision"], 3)
        self.assertIn("canonical_diagram_fingerprint=eq.dfp_", transport.calls[0]["url"])
        self.assertNotIn("order=", transport.calls[0]["url"])


if __name__ == "__main__":
    unittest.main()
