from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openai_quality_provider import (
    OpenAIQualityConfig,
    OpenAIQualityProvider,
    build_openai_quality_provider_from_env,
    openai_quality_configuration_status,
)


class OpenAIQualityProviderTests(unittest.TestCase):
    def test_provider_is_disabled_without_opt_in_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = build_openai_quality_provider_from_env(env={"OPENAI_API_KEY": "sk-test"}, cwd=temp_dir)

        self.assertIsNone(provider)

    def test_configuration_loads_ignored_env_file_without_exposing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env.local").write_text(
                "OPENAI_API_KEY=sk-local-test\nKINDLEMASTER_OPENAI_QUALITY=1\n",
                encoding="utf-8",
            )

            status = openai_quality_configuration_status(cwd=root, env={"KINDLEMASTER_OPENAI_QUALITY": "1"})
            provider = build_openai_quality_provider_from_env(cwd=root, env={"KINDLEMASTER_OPENAI_QUALITY": "1"})

        self.assertTrue(status["enabled"])
        self.assertTrue(status["api_key_present"])
        self.assertIsNotNone(provider)

    def test_ocr_cleanup_uses_structured_response(self) -> None:
        calls = []

        def fake_transport(url, headers, payload, timeout):
            calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
            return {
                "output": [
                    {
                        "content": [
                            {
                                "text": json.dumps(
                                    {
                                        "text": "Broken fragment has Business Analysis Planning.",
                                        "confidence": 0.93,
                                    }
                                )
                            }
                        ]
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        provider = OpenAIQualityProvider(OpenAIQualityConfig(api_key="sk-test"), transport=fake_transport)
        result = provider.cleanup_fragment("Broken fragment has Busi- nessAnalysisPlanning.")

        self.assertEqual(result.text, "Broken fragment has Business Analysis Planning.")
        self.assertEqual(result.confidence, 0.93)
        self.assertTrue(calls[0]["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(calls[0]["payload"]["text"]["format"]["type"], "json_schema")

    def test_toc_detection_drops_ai_href_not_present_in_candidates(self) -> None:
        def fake_transport(_url, _headers, _payload, _timeout):
            return {
                "output_text": json.dumps(
                    {
                        "confidence": 0.88,
                        "reasoning": "Only one candidate has a valid href.",
                        "entries": [
                            {"label": "Real Chapter", "href": "chapter.xhtml", "confidence": 0.9, "level": 1},
                            {"label": "Invented", "href": "missing.xhtml", "confidence": 0.9, "level": 1},
                        ],
                    }
                )
            }

        provider = OpenAIQualityProvider(OpenAIQualityConfig(api_key="sk-test"), transport=fake_transport)
        result = provider.detect_toc(
            {
                "toc_entries": [{"label": "Real Chapter", "href": "chapter.xhtml"}],
                "sample_text": "Real Chapter text",
            }
        )

        self.assertEqual(result.confidence, 0.88)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].href, "chapter.xhtml")


if __name__ == "__main__":
    unittest.main()
