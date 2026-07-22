from __future__ import annotations

import unittest

from chess_verified_fen_reuse import (
    VerifiedFenReuseError,
    bind_complete_review_to_artifact,
)


SOURCE_SHA = "a" * 64


class ChessVerifiedFenReuseTests(unittest.TestCase):
    def _payload(self) -> dict:
        return {
            "session_status": "complete",
            "source_document_sha256": SOURCE_SHA,
            "reused_from_artifact_id": "old-artifact",
            "rows": [
                {
                    "artifact_id": "old-artifact",
                    "diagram_id": "diagram-1",
                    "source_document_sha256": SOURCE_SHA,
                    "human_verified": True,
                    "fen_human_verified": True,
                }
            ],
        }

    def test_rebinds_exact_source_rows_and_preserves_provenance(self) -> None:
        result = bind_complete_review_to_artifact(
            self._payload(),
            artifact_id="new-artifact",
            source_document_sha256=SOURCE_SHA,
        )

        self.assertEqual(result["session_status"], "complete")
        self.assertEqual(result["reused_from_artifact_id"], "old-artifact")
        self.assertEqual(result["rows"][0]["artifact_id"], "new-artifact")
        self.assertEqual(result["rows"][0]["source_review_artifact_id"], "old-artifact")

    def test_rejects_open_or_source_mismatched_review(self) -> None:
        for mutation, expected in (
            ({"session_status": "active"}, "review_session_not_complete"),
            ({"source_document_sha256": "b" * 64}, "source_document_sha256_mismatch"),
        ):
            with self.subTest(expected=expected):
                payload = self._payload()
                payload.update(mutation)
                with self.assertRaisesRegex(VerifiedFenReuseError, expected):
                    bind_complete_review_to_artifact(
                        payload,
                        artifact_id="new-artifact",
                        source_document_sha256=SOURCE_SHA,
                    )


if __name__ == "__main__":
    unittest.main()
