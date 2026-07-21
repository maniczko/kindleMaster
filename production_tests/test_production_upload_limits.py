from __future__ import annotations

import types
import unittest

from flask import Flask, jsonify, request

from production_upload_limits import install_upload_limit_policy


class ProductionUploadLimitTests(unittest.TestCase):
    def _app(self, *, max_upload_bytes: int, overhead_bytes: int) -> Flask:
        app = Flask(f"upload-limit-{max_upload_bytes}-{overhead_bytes}")

        def json_error(message: str, **kwargs):
            response = jsonify(
                {
                    "error": message,
                    "error_code": kwargs["error_code"],
                    "phase": kwargs["phase"],
                    "retryable": kwargs["retryable"],
                    **dict(kwargs.get("extra") or {}),
                }
            )
            response.status_code = kwargs["status_code"]
            return response

        module = types.SimpleNamespace(app=app, _json_error=json_error)
        install_upload_limit_policy(
            module,
            max_upload_bytes=max_upload_bytes,
            multipart_overhead_bytes=overhead_bytes,
        )

        @app.post("/convert/start")
        def start():
            request.get_data()
            return jsonify({"success": True}), 202

        return app

    def test_request_limit_includes_bounded_multipart_overhead(self) -> None:
        app = self._app(max_upload_bytes=10, overhead_bytes=5)
        self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 15)

    def test_oversized_request_returns_stable_json_error(self) -> None:
        app = self._app(max_upload_bytes=10, overhead_bytes=5)
        response = app.test_client().post(
            "/convert/start",
            data=b"x" * 16,
            content_type="application/octet-stream",
        )

        self.assertEqual(response.status_code, 413)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "upload_size_limit")
        self.assertEqual(payload["phase"], "upload")
        self.assertFalse(payload["retryable"])
        self.assertEqual(payload["max_upload_bytes"], 10)

    def test_request_at_limit_is_not_rejected_by_parser(self) -> None:
        app = self._app(max_upload_bytes=10, overhead_bytes=5)
        response = app.test_client().post(
            "/convert/start",
            data=b"x" * 15,
            content_type="application/octet-stream",
        )
        self.assertEqual(response.status_code, 202)


if __name__ == "__main__":
    unittest.main()
