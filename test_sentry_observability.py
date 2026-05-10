from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sentry_observability import (
    build_conversion_context,
    capture_conversion_exception,
    configure_sentry_backend,
)


class _FakeScope:
    def __init__(self) -> None:
        self.tags: dict[str, object] = {}
        self.contexts: dict[str, object] = {}

    def __enter__(self) -> "_FakeScope":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def set_tag(self, key: str, value: object) -> None:
        self.tags[key] = value

    def set_context(self, key: str, value: object) -> None:
        self.contexts[key] = value


class _FakeSentrySdk:
    def __init__(self) -> None:
        self.init_kwargs: dict[str, object] = {}
        self.captured_exception: BaseException | None = None
        self.scope = _FakeScope()

    def init(self, **kwargs: object) -> None:
        self.init_kwargs = dict(kwargs)

    def configure_scope(self) -> _FakeScope:
        return self.scope

    def capture_exception(self, error: BaseException) -> str:
        self.captured_exception = error
        return "event-abc"


class SentryObservabilityTests(unittest.TestCase):
    def test_configure_sentry_backend_uses_release_and_environment_tags(self) -> None:
        sentry_sdk = _FakeSentrySdk()

        result = configure_sentry_backend(
            env={
                "SENTRY_DSN": "https://example@sentry.invalid/1",
                "SENTRY_RELEASE": "kindlemaster@1.2.3",
                "SENTRY_ENVIRONMENT": "test",
            },
            sentry_sdk=sentry_sdk,
        )

        self.assertTrue(result["enabled"])
        self.assertEqual(sentry_sdk.init_kwargs["dsn"], "https://example@sentry.invalid/1")
        self.assertEqual(sentry_sdk.init_kwargs["release"], "kindlemaster@1.2.3")
        self.assertEqual(sentry_sdk.init_kwargs["environment"], "test")
        self.assertEqual(sentry_sdk.scope.tags["release"], "kindlemaster@1.2.3")
        self.assertEqual(sentry_sdk.scope.tags["environment"], "test")

    def test_configure_sentry_backend_noops_when_sdk_is_missing(self) -> None:
        real_import_module = importlib.import_module

        def fake_import_module(name: str, package: str | None = None) -> object:
            if name == "sentry_sdk":
                raise ModuleNotFoundError("sentry_sdk")
            return real_import_module(name, package)

        with patch("importlib.import_module", side_effect=fake_import_module):
            result = configure_sentry_backend(
                env={
                    "SENTRY_DSN": "https://example@sentry.invalid/1",
                    "SENTRY_RELEASE": "kindlemaster@1.2.3",
                    "SENTRY_ENVIRONMENT": "test",
                }
            )

        self.assertFalse(result["enabled"])
        self.assertEqual(result["reason"], "sentry_sdk_missing")

    def test_configure_sentry_backend_loads_local_env_file_without_overriding_process_env(self) -> None:
        sentry_sdk = _FakeSentrySdk()

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env.local"
            env_path.write_text(
                "\n".join(
                    [
                        "SENTRY_DSN=https://file-dsn@sentry.invalid/1",
                        "SENTRY_RELEASE=kindlemaster@file",
                        "SENTRY_ENVIRONMENT=local-file",
                    ]
                ),
                encoding="utf-8",
            )
            with patch("sentry_observability.DEFAULT_SENTRY_ENV_FILES", (str(env_path),)):
                with patch.dict(os.environ, {"SENTRY_RELEASE": "kindlemaster@process"}, clear=True):
                    result = configure_sentry_backend(sentry_sdk=sentry_sdk)

        self.assertTrue(result["enabled"])
        self.assertEqual(sentry_sdk.init_kwargs["dsn"], "https://file-dsn@sentry.invalid/1")
        self.assertEqual(sentry_sdk.init_kwargs["release"], "kindlemaster@process")
        self.assertEqual(sentry_sdk.init_kwargs["environment"], "local-file")

    def test_capture_conversion_exception_attaches_conversion_context(self) -> None:
        sentry_sdk = _FakeSentrySdk()
        error = RuntimeError("conversion exploded")
        context = build_conversion_context(
            job_id="job-1",
            source_type="pdf",
            input_type="pdf",
            profile="book_reflow",
            quality_score=8.4,
            premium_ready=False,
        )

        event_id = capture_conversion_exception(
            error,
            context=context,
            env={
                "SENTRY_DSN": "https://example@sentry.invalid/1",
                "SENTRY_RELEASE": "kindlemaster@1.2.3",
                "SENTRY_ENVIRONMENT": "production",
            },
            sentry_sdk=sentry_sdk,
        )

        self.assertEqual(event_id, "event-abc")
        self.assertIs(sentry_sdk.captured_exception, error)
        self.assertEqual(sentry_sdk.scope.tags["job_id"], "job-1")
        self.assertEqual(sentry_sdk.scope.tags["source_type"], "pdf")
        self.assertEqual(sentry_sdk.scope.tags["profile"], "book_reflow")
        self.assertEqual(sentry_sdk.scope.tags["release"], "kindlemaster@1.2.3")
        self.assertEqual(sentry_sdk.scope.tags["environment"], "production")
        self.assertEqual(sentry_sdk.scope.contexts["conversion"], context)


if __name__ == "__main__":
    unittest.main()
