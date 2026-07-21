from __future__ import annotations

import types
import unittest
from unittest.mock import patch

import production_api


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[object, ...]] = []

    def info(self, *args: object) -> None:
        self.messages.append(args)


class ProductionApiConfigurationTests(unittest.TestCase):
    def test_configures_guardrails_only_after_durable_runtime(self) -> None:
        calls: list[str] = []
        queue = object()
        database = object()
        guardrail_policy = types.SimpleNamespace(max_upload_bytes=75)
        memory_policy = object()
        module = types.SimpleNamespace(
            app=types.SimpleNamespace(logger=FakeLogger()),
        )

        def install_runtime(target):
            calls.append("runtime")
            target._DURABLE_JOB_DATABASE = database
            return queue, {"migrated": 1, "preserved": 2, "failed": 0}

        def install_upload(target, *, max_upload_bytes):
            self.assertIs(target, module)
            self.assertEqual(max_upload_bytes, 75)
            calls.append("upload")
            return 80

        def install_memory(target, *, policy):
            self.assertIs(target, module)
            self.assertIs(policy, memory_policy)
            calls.append("memory")

        def install_guardrails(target, *, database: object, queue: object, policy):
            self.assertIs(target, module)
            self.assertIs(database, module._DURABLE_JOB_DATABASE)
            self.assertIs(queue, queue_object)
            self.assertIs(policy, guardrail_policy)
            calls.append("guardrails")

        def install_security(target):
            self.assertIs(target, module)
            calls.append("security")

        queue_object = queue
        with (
            patch("production_api.durable_runtime_enabled", return_value=True),
            patch("production_api.install_migrated_production_runtime", side_effect=install_runtime),
            patch("production_api.ProductionGuardrailPolicy.from_env", return_value=guardrail_policy),
            patch("production_api.MemoryAdmissionPolicy.from_env", return_value=memory_policy),
            patch("production_api.install_upload_limit_policy", side_effect=install_upload),
            patch("production_api.install_memory_admission_guard", side_effect=install_memory),
            patch("production_api.install_production_guardrails", side_effect=install_guardrails),
            patch("production_api.install_admission_security_logging", side_effect=install_security),
        ):
            result = production_api.configure_production_api(module)

        self.assertEqual(calls, ["runtime", "upload", "memory", "guardrails", "security"])
        self.assertEqual(
            result,
            {"migrated": 1, "preserved": 2, "failed": 0, "max_request_bytes": 80},
        )
        self.assertEqual(len(module.app.logger.messages), 1)

    def test_refuses_non_durable_production_api_mode(self) -> None:
        with patch("production_api.durable_runtime_enabled", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "requires KINDLEMASTER_DURABLE_RUNTIME=1"):
                production_api.configure_production_api(types.SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
