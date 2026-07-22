from __future__ import annotations

import json
import types
import unittest
from io import BytesIO
from unittest.mock import patch

from flask import Flask, jsonify, send_file

from production_security_events import install_admission_security_logging


class ProductionSecurityEventTests(unittest.TestCase):
    def test_denied_admission_logs_only_bounded_redacted_fields(self) -> None:
        app = Flask("security-event-test")
        module = types.SimpleNamespace(
            app=app,
            _resolve_request_auth_context=lambda: types.SimpleNamespace(
                authenticated=True,
                user_id="private-user-id",
            ),
        )
        install_admission_security_logging(module)

        @app.post("/convert/start")
        def start():
            return (
                jsonify(
                    {
                        "error_code": "rate_limit_exceeded",
                        "retryable": True,
                        "job_id": "secret-job-id",
                        "filename": "confidential.pdf",
                    }
                ),
                429,
            )

        with patch.object(app.logger, "warning") as warning:
            response = app.test_client().post(
                "/convert/start",
                headers={"Authorization": "Bearer secret-token"},
                environ_base={"REMOTE_ADDR": "203.0.113.10"},
            )

        self.assertEqual(response.status_code, 429)
        warning.assert_called_once()
        serialized = warning.call_args.args[0]
        event = json.loads(serialized)
        self.assertEqual(event["event"], "production_admission_denied")
        self.assertEqual(event["owner_class"], "authenticated")
        self.assertEqual(event["route_class"], "conversion_start")
        self.assertEqual(event["rule_code"], "rate_limit_exceeded")
        self.assertEqual(event["status_code"], 429)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("203.0.113.10", serialized)
        self.assertNotIn("secret-job-id", serialized)
        self.assertNotIn("confidential.pdf", serialized)
        self.assertNotIn("private-user-id", serialized)

    def test_job_identifier_is_not_logged_from_dynamic_read_route(self) -> None:
        app = Flask("security-route-redaction-test")
        module = types.SimpleNamespace(
            app=app,
            _resolve_request_auth_context=lambda: types.SimpleNamespace(authenticated=False),
        )
        install_admission_security_logging(module)

        @app.get("/convert/status/<job_id>")
        def status(job_id: str):
            return jsonify({"error_code": "rate_limit_exceeded", "retryable": True}), 429

        with patch.object(app.logger, "warning") as warning:
            app.test_client().get("/convert/status/sensitive-job-id")

        serialized = warning.call_args.args[0]
        event = json.loads(serialized)
        self.assertEqual(event["owner_class"], "guest")
        self.assertEqual(event["route_class"], "conversion_read")
        self.assertNotIn("sensitive-job-id", serialized)

    def test_unrelated_error_is_not_logged_as_admission_event(self) -> None:
        app = Flask("security-event-filter-test")
        module = types.SimpleNamespace(
            app=app,
            _resolve_request_auth_context=lambda: types.SimpleNamespace(authenticated=False),
        )
        install_admission_security_logging(module)

        @app.get("/health")
        def health():
            return jsonify({"error_code": "database_unavailable", "retryable": True}), 503

        with patch.object(app.logger, "warning") as warning:
            response = app.test_client().get("/health")

        self.assertEqual(response.status_code, 503)
        warning.assert_not_called()

    def test_streamed_json_artifact_is_not_parsed_as_api_response(self) -> None:
        app = Flask("security-event-json-download-test")
        module = types.SimpleNamespace(
            app=app,
            _resolve_request_auth_context=lambda: types.SimpleNamespace(authenticated=False),
        )
        install_admission_security_logging(module)

        @app.get("/convert/artifact/report")
        def report():
            return send_file(
                BytesIO(b'{"status":"ready"}'),
                mimetype="application/json",
                as_attachment=True,
                download_name="report.json",
            )

        with patch.object(app.logger, "warning") as warning:
            response = app.test_client().get("/convert/artifact/report")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ready"})
        warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
