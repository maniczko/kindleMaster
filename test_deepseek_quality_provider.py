from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deepseek_quality_provider import (
    DeepSeekAuditConfig,
    DeepSeekAuditProvider,
    build_deepseek_audit_payload,
    build_deepseek_audit_provider_from_env,
    deepseek_audit_configuration_status,
)
from pymupdf_chess_extractor import _scan_chess_pgn_extra_artifacts


class DeepSeekQualityProviderTests(unittest.TestCase):
    def test_provider_disabled_without_audit_flag_even_with_key(self) -> None:
        provider = build_deepseek_audit_provider_from_env(
            env={"DEEPSEEK_API_KEY": "deepseek-test-key"},
            cwd=tempfile.gettempdir(),
        )

        self.assertIsNone(provider)

    def test_configuration_status_masks_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env.local").write_text(
                "\n".join(
                    [
                        "KINDLEMASTER_DEEPSEEK_AUDIT=1",
                        "DEEPSEEK_API_KEY=deepseek-local-secret",
                        "DEEPSEEK_BASE_URL=https://api.deepseek.com",
                        "DEEPSEEK_MODEL=deepseek-v4-flash",
                    ]
                ),
                encoding="utf-8",
            )

            status = deepseek_audit_configuration_status(cwd=root)

        self.assertTrue(status["enabled"])
        self.assertTrue(status["api_key_present"])
        self.assertNotIn("deepseek-local-secret", json.dumps(status))
        self.assertEqual(status["endpoint"], "/chat/completions")
        self.assertTrue(status["evidence_only"])

    def test_glyph_review_uses_chat_completions_json_payload(self) -> None:
        calls = []

        def fake_transport(url, headers, payload, timeout):
            calls.append({"url": url, "headers": headers, "payload": payload, "timeout": timeout})
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "evidence_only": True,
                                    "requires_human_confirmation": True,
                                    "glyph_clusters": [
                                        {"font": "CustomChess", "token": "\"'t!;>g4\"", "count": 1}
                                    ],
                                    "suspected_mappings": [],
                                    "false_positive_samples": [],
                                    "next_measurements": ["collect more samples per font"],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 12},
            }

        provider = DeepSeekAuditProvider(
            DeepSeekAuditConfig(api_key="deepseek-test", model="deepseek-v4-flash"),
            transport=fake_transport,
        )

        result = provider.review_glyph_diagnostics(
            {
                "source_title": "Glyph sample",
                "diagnostic_count": 1,
                "records": [
                    {
                        "record_id": "r1",
                        "diagnostics": [
                            {
                                "font_name": "CustomChess",
                                "raw_text": "1. e4 \"'t!;>g4\"",
                                "reasons": ["mojibake_token"],
                            }
                        ],
                    }
                ],
            }
        )

        self.assertTrue(result["evidence_only"])
        self.assertTrue(result["requires_human_confirmation"])
        self.assertFalse(result["mutates_output"])
        self.assertEqual(calls[0]["url"], "https://api.deepseek.com/chat/completions")
        self.assertTrue(calls[0]["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(calls[0]["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0]["payload"]["thinking"], {"type": "disabled"})
        self.assertIn("messages", calls[0]["payload"])

    def test_deepseek_audit_artifact_is_added_without_strict_pgn(self) -> None:
        def fake_transport(_url, _headers, payload, _timeout):
            audit_type = json.loads(payload["messages"][1]["content"])["audit_type"]
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "evidence_only": True,
                                    "requires_human_confirmation": True,
                                    "audit_type": audit_type,
                                    "glyph_clusters": [],
                                    "suspected_mappings": [],
                                    "layout_warnings": [],
                                    "next_measurements": [],
                                }
                            )
                        }
                    }
                ],
                "usage": {},
            }

        provider = DeepSeekAuditProvider(DeepSeekAuditConfig(api_key="deepseek-test"), transport=fake_transport)
        artifacts = _scan_chess_pgn_extra_artifacts(
            [],
            source_title="DeepSeek artifact",
            diagrams=[
                {
                    "id": "diagram-1",
                    "page_index": 0,
                    "page_number": 1,
                    "bbox": [10, 20, 110, 120],
                    "caption": "Diagram 1-2",
                    "image_data_uri": "data:image/png;base64,AA==",
                }
            ],
            deepseek_provider=provider,
        )

        by_key = {artifact["key"]: artifact for artifact in artifacts}
        self.assertIn("deepseek_audit", by_key)
        self.assertNotIn("chess_pgn", by_key)
        payload = json.loads(by_key["deepseek_audit"]["data"].decode("utf-8"))
        self.assertTrue(payload["evidence_only"])
        self.assertTrue(payload["requires_human_confirmation"])
        self.assertFalse(payload["mutates_output"])
        self.assertIn("chess_layout", payload["sections"])

    def test_build_payload_returns_none_without_audit_data(self) -> None:
        provider = DeepSeekAuditProvider(
            DeepSeekAuditConfig(api_key="deepseek-test"),
            transport=lambda *_args: {"choices": [{"message": {"content": "{}"}}]},
        )

        self.assertIsNone(build_deepseek_audit_payload(provider=provider, source_title="Empty"))


if __name__ == "__main__":
    unittest.main()
