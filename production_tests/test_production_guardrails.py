from __future__ import annotations

import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from durable_job_queue import DurableJobDatabase
from production_guardrails import (
    InputPolicyError,
    ProductionGuardrailPolicy,
    SQLiteFixedWindowRateLimiter,
    pseudonymous_owner_key,
    validate_upload_bytes,
)


class ProductionGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = DurableJobDatabase(Path(self.temp_dir.name) / "runtime.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_rate_limit_is_shared_in_sqlite(self) -> None:
        first = SQLiteFixedWindowRateLimiter(self.database)
        second = SQLiteFixedWindowRateLimiter(self.database)
        self.assertTrue(first.consume("owner:start", limit=2).allowed)
        self.assertTrue(second.consume("owner:start", limit=2).allowed)
        denied = first.consume("owner:start", limit=2)
        self.assertFalse(denied.allowed)
        self.assertGreaterEqual(denied.retry_after_seconds, 1)

    def test_rate_window_resets(self) -> None:
        limiter = SQLiteFixedWindowRateLimiter(self.database)
        self.assertTrue(limiter.consume("owner:start", limit=1, window_seconds=1).allowed)
        self.assertFalse(limiter.consume("owner:start", limit=1, window_seconds=1).allowed)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE rate_limit_windows SET window_started_at = ? WHERE key = ?",
                (time.time() - 2, "owner:start"),
            )
        self.assertTrue(limiter.consume("owner:start", limit=1, window_seconds=1).allowed)

    def test_unverified_tokens_never_receive_authenticated_limits(self) -> None:
        owner, authenticated = pseudonymous_owner_key(
            secret=b"x" * 32,
            authorization="Bearer very-secret-token",
        )
        self.assertFalse(authenticated)
        self.assertNotIn("very-secret-token", owner)
        self.assertTrue(owner.startswith("guest-auth:"))

    def test_fallback_identity_cannot_be_rotated_with_user_agent(self) -> None:
        first, _ = pseudonymous_owner_key(
            secret=b"x" * 32,
            remote_address="203.0.113.5",
            user_agent="agent-a",
        )
        second, _ = pseudonymous_owner_key(
            secret=b"x" * 32,
            remote_address="203.0.113.5",
            user_agent="agent-b",
        )
        self.assertEqual(first, second)

    def test_guest_capability_is_untrusted_by_default(self) -> None:
        policy = ProductionGuardrailPolicy.from_env({})
        self.assertFalse(policy.trust_guest_capability)
        enabled = ProductionGuardrailPolicy.from_env({"KINDLEMASTER_TRUST_GUEST_CAPABILITY": "1"})
        self.assertTrue(enabled.trust_guest_capability)

    def test_mime_and_magic_mismatch_is_rejected(self) -> None:
        policy = ProductionGuardrailPolicy(min_disk_free_bytes=1, min_disk_free_ratio=0)
        with self.assertRaises(InputPolicyError) as context:
            validate_upload_bytes("book.pdf", "application/pdf", b"not-a-pdf", policy)
        self.assertEqual(context.exception.code, "upload_magic_mismatch")

    def test_valid_minimal_docx_structure_passes(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "<w:document />")
        source_type = validate_upload_bytes(
            "book.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            payload.getvalue(),
            ProductionGuardrailPolicy(min_disk_free_bytes=1, min_disk_free_ratio=0),
        )
        self.assertEqual(source_type, "docx")

    def test_docx_path_traversal_is_rejected(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "<w:document />")
            archive.writestr("../escape.txt", "bad")
        with self.assertRaises(InputPolicyError) as context:
            validate_upload_bytes(
                "book.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                payload.getvalue(),
                ProductionGuardrailPolicy(min_disk_free_bytes=1, min_disk_free_ratio=0),
            )
        self.assertEqual(context.exception.code, "docx_path_traversal")

    def test_docx_expansion_ratio_is_rejected(self) -> None:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "A" * 200_000)
        with self.assertRaises(InputPolicyError) as context:
            validate_upload_bytes(
                "book.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                payload.getvalue(),
                ProductionGuardrailPolicy(
                    max_archive_ratio=2,
                    min_disk_free_bytes=1,
                    min_disk_free_ratio=0,
                ),
            )
        self.assertEqual(context.exception.code, "archive_expansion_limit")


if __name__ == "__main__":
    unittest.main()
