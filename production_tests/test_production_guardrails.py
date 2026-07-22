from __future__ import annotations

import io
import multiprocessing
import tempfile
import time
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from flask import Flask, jsonify

from durable_job_queue import DurableJobDatabase
from production_guardrails import (
    InputPolicyError,
    ProductionGuardrailPolicy,
    SQLiteFixedWindowRateLimiter,
    install_production_guardrails,
    pseudonymous_owner_key,
    validate_upload_bytes,
)


def _consume_rate_limit_in_process(
    database_path: str,
    key: str,
    limit: int,
    start_event,
    result_queue,
) -> None:
    database = DurableJobDatabase(Path(database_path))
    limiter = SQLiteFixedWindowRateLimiter(database)
    if not start_event.wait(timeout=10):
        result_queue.put({"error": "start_timeout"})
        return
    try:
        decision = limiter.consume(key, limit=limit)
    except Exception as error:
        result_queue.put({"error": f"{error.__class__.__name__}: {error}"})
        return
    result_queue.put(
        {
            "allowed": decision.allowed,
            "remaining": decision.remaining,
            "retry_after_seconds": decision.retry_after_seconds,
        }
    )


class FakeQueue:
    def __init__(self, *, global_active: int = 0, owner_active: int = 0) -> None:
        self.global_active = global_active
        self.owner_active = owner_active

    def active_count(self, *, owner_key: str | None = None) -> int:
        return self.owner_active if owner_key is not None else self.global_active


class ProductionGuardrailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "runtime.sqlite3"
        self.database = DurableJobDatabase(self.database_path)
        self.route_calls = 0

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _minimal_docx() -> bytes:
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", "<w:document />")
        return payload.getvalue()

    def _guarded_app(
        self,
        *,
        policy: ProductionGuardrailPolicy,
        queue: FakeQueue | None = None,
    ) -> Flask:
        app = Flask(f"guardrail-test-{id(self)}-{time.time_ns()}")

        def json_error(message: str, **kwargs):
            payload = {
                "error": message,
                "error_code": kwargs["error_code"],
                "phase": kwargs["phase"],
                "retryable": kwargs["retryable"],
                **dict(kwargs.get("extra") or {}),
            }
            response = jsonify(payload)
            response.status_code = kwargs["status_code"]
            return response

        module = types.SimpleNamespace(
            app=app,
            UPLOAD_DIR=self.temp_dir.name,
            _resolve_request_auth_context=lambda: types.SimpleNamespace(
                authenticated=False,
                user_id="",
            ),
            _json_error=json_error,
        )
        install_production_guardrails(
            module,
            database=self.database,
            queue=queue or FakeQueue(),
            policy=policy,
        )

        @app.post("/convert/start")
        def convert_start():
            self.route_calls += 1
            return jsonify({"success": True}), 202

        return app

    def test_rate_limit_is_shared_in_sqlite(self) -> None:
        first = SQLiteFixedWindowRateLimiter(self.database)
        second = SQLiteFixedWindowRateLimiter(self.database)
        self.assertTrue(first.consume("owner:start", limit=2).allowed)
        self.assertTrue(second.consume("owner:start", limit=2).allowed)
        denied = first.consume("owner:start", limit=2)
        self.assertFalse(denied.allowed)
        self.assertGreaterEqual(denied.retry_after_seconds, 1)

    def test_rate_limit_is_atomic_across_processes(self) -> None:
        context = multiprocessing.get_context("spawn")
        start_event = context.Event()
        result_queue = context.Queue()
        processes = [
            context.Process(
                target=_consume_rate_limit_in_process,
                args=(str(self.database_path), "owner:shared:start", 2, start_event, result_queue),
            )
            for _ in range(3)
        ]
        for process in processes:
            process.start()
        start_event.set()
        results = [result_queue.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)

        self.assertTrue(all(process.exitcode == 0 for process in processes))
        self.assertEqual([result.get("error") for result in results if result.get("error")], [])
        self.assertEqual(sum(1 for result in results if result["allowed"]), 2)
        denied = [result for result in results if not result["allowed"]]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["remaining"], 0)
        self.assertGreaterEqual(denied[0]["retry_after_seconds"], 1)

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
        source_type = validate_upload_bytes(
            "book.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            self._minimal_docx(),
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

    def test_flask_limit_blocks_invalid_token_rotation(self) -> None:
        app = self._guarded_app(
            policy=ProductionGuardrailPolicy(
                guest_start_per_minute=1,
                global_active_jobs=10,
                guest_active_jobs=10,
                min_disk_free_bytes=1,
                min_disk_free_ratio=0,
            )
        )
        client = app.test_client()
        first = client.post(
            "/convert/start",
            data={"file": (io.BytesIO(self._minimal_docx()), "book.docx")},
            headers={"Authorization": "Bearer invalid-a"},
            content_type="multipart/form-data",
        )
        second = client.post(
            "/convert/start",
            data={"file": (io.BytesIO(self._minimal_docx()), "book.docx")},
            headers={"Authorization": "Bearer invalid-b"},
            content_type="multipart/form-data",
        )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.get_json()["error_code"], "rate_limit_exceeded")
        self.assertIn("Retry-After", second.headers)
        self.assertEqual(second.headers["X-RateLimit-Remaining"], "0")
        self.assertEqual(self.route_calls, 1)

    def test_flask_rejects_magic_mismatch_before_endpoint(self) -> None:
        app = self._guarded_app(
            policy=ProductionGuardrailPolicy(
                guest_start_per_minute=10,
                global_active_jobs=10,
                guest_active_jobs=10,
                min_disk_free_bytes=1,
                min_disk_free_ratio=0,
            )
        )
        response = app.test_client().post(
            "/convert/start",
            data={"file": (io.BytesIO(b"not a pdf"), "book.pdf")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["error_code"], "upload_magic_mismatch")
        self.assertEqual(self.route_calls, 0)

    def test_flask_global_capacity_returns_retryable_503(self) -> None:
        app = self._guarded_app(
            policy=ProductionGuardrailPolicy(
                guest_start_per_minute=10,
                global_active_jobs=2,
                guest_active_jobs=10,
                min_disk_free_bytes=1,
                min_disk_free_ratio=0,
            ),
            queue=FakeQueue(global_active=2),
        )
        response = app.test_client().post(
            "/convert/start",
            data={"file": (io.BytesIO(self._minimal_docx()), "book.docx")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["error_code"], "global_capacity_exceeded")
        self.assertEqual(response.headers["Retry-After"], "30")


if __name__ == "__main__":
    unittest.main()
