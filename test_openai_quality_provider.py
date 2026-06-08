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

    def test_magazine_review_uses_compact_bounded_context(self) -> None:
        calls = []

        def fake_transport(_url, _headers, payload, _timeout):
            calls.append(payload)
            return {
                "output_text": json.dumps(
                    {
                        "confidence": 0.81,
                        "suspected_bad_reading_order": [],
                        "truncated_titles": [
                            {
                                "href": "chapter.xhtml",
                                "observed_title": "AI as a",
                                "suggested_title": "AI as a Mentor",
                                "evidence": "ends with preposition",
                                "confidence": 0.78,
                            },
                            {
                                "href": "invented.xhtml",
                                "observed_title": "Invented",
                                "suggested_title": "Invented Link",
                                "evidence": "model invented this href",
                                "confidence": 0.99,
                            }
                        ],
                        "toc_missing_articles": [],
                        "non_content_misclassified": [],
                        "ocr_cleanup_candidates": [],
                        "suggested_fixture_tags": ["magazine_title_truncated"],
                    }
                )
            }

        provider = OpenAIQualityProvider(
            OpenAIQualityConfig(api_key="sk-test", max_input_chars=1800),
            transport=fake_transport,
        )
        result = provider.review_magazine(
            {
                "suspicious_fragments": ["X" * 5000],
                "epub_bytes": "FULL_EPUB_BYTES_SHOULD_NOT_LEAK",
                "pdf_bytes": "FULL_PDF_BYTES_SHOULD_NOT_LEAK",
                "toc_entries": [{"label": "Article", "href": "chapter.xhtml"}],
                "article_map": {
                    "editorial_article_count": 1,
                    "articles": [{"href": "chapter.xhtml", "title": "Y" * 1000, "kind": "article", "toc_matched": False}],
                },
                "image_metrics": {"low_resolution_image_count": 2, "raw_image_data": "RAW_IMAGE_BYTES_SHOULD_NOT_LEAK"},
                "premium_issues": [{"code": "toc_lead_used_as_title", "severity": "review", "message": "M" * 1000}],
            }
        )
        sent_user_content = calls[0]["input"][1]["content"]
        sent_context = json.loads(sent_user_content)

        self.assertEqual(result["confidence"], 0.81)
        self.assertEqual(result["truncated_titles"][0]["href"], "chapter.xhtml")
        self.assertEqual(result["truncated_titles"][0]["suggested_title"], "AI as a Mentor")
        self.assertEqual(len(result["truncated_titles"]), 1)
        self.assertLessEqual(len(sent_user_content), 1800)
        self.assertNotIn("FULL_EPUB_BYTES_SHOULD_NOT_LEAK", sent_user_content)
        self.assertNotIn("FULL_PDF_BYTES_SHOULD_NOT_LEAK", sent_user_content)
        self.assertNotIn("RAW_IMAGE_BYTES_SHOULD_NOT_LEAK", sent_user_content)
        self.assertLessEqual(len(sent_context["suspicious_fragments"][0]["text"]), 420)
        self.assertLessEqual(len(sent_context["article_map"][0]["title"]), 180)
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")

    def test_dense_handbook_review_uses_compact_bounded_context(self) -> None:
        calls = []

        def fake_transport(_url, _headers, payload, _timeout):
            calls.append(payload)
            return {
                "output_text": json.dumps(
                    {
                        "confidence": 0.83,
                        "toc_debris": [
                            {"href": "chapter.xhtml#step", "label": "Step 1.", "evidence": "procedural debris", "confidence": 0.8},
                            {"href": "invented.xhtml", "label": "Invented", "evidence": "bad href", "confidence": 0.99},
                        ],
                        "heading_noise": [],
                        "text_artifact_reviews": [
                            {
                                "fragment_index": 0,
                                "before": "ignored by sanitizer",
                                "classification": "legal_structure",
                                "evidence": ".1 Purpose is a dense handbook label",
                                "confidence": 0.77,
                            }
                        ],
                        "oversized_chapters": [],
                        "suggested_fixture_tags": ["dense_handbook_toc_noise"],
                    }
                )
            }

        provider = OpenAIQualityProvider(
            OpenAIQualityConfig(api_key="sk-test", max_input_chars=1800),
            transport=fake_transport,
        )
        result = provider.review_dense_handbook(
            {
                "epub_bytes": "FULL_EPUB_BYTES_SHOULD_NOT_LEAK",
                "pdf_bytes": "FULL_PDF_BYTES_SHOULD_NOT_LEAK",
                "toc_entries": [{"label": "Step 1.", "href": "chapter.xhtml#step", "level": 2}],
                "heading_noise_samples": [{"label": ".1 Strengths", "href": "chapter.xhtml#strengths"}],
                "text_artifact_fragments": [{"index": 0, "text": "X" * 5000}],
                "chapter_stats": [{"href": "chapter.xhtml", "title": "Techniques", "word_count": 41000}],
                "premium_issues": [{"code": "dense_handbook_toc_noise", "message": "M" * 1000}],
            }
        )
        sent_user_content = calls[0]["input"][1]["content"]
        sent_context = json.loads(sent_user_content)

        self.assertEqual(result["confidence"], 0.83)
        self.assertEqual(result["toc_debris"], [{"href": "chapter.xhtml#step", "label": "Step 1.", "evidence": "procedural debris", "confidence": 0.8}])
        self.assertEqual(result["text_artifact_reviews"][0]["before"], sent_context["text_artifact_fragments"][0]["text"])
        self.assertLessEqual(len(sent_user_content), 1800)
        self.assertNotIn("FULL_EPUB_BYTES_SHOULD_NOT_LEAK", sent_user_content)
        self.assertNotIn("FULL_PDF_BYTES_SHOULD_NOT_LEAK", sent_user_content)
        self.assertLessEqual(len(sent_context["text_artifact_fragments"][0]["text"]), 420)
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")


if __name__ == "__main__":
    unittest.main()
