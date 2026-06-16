from __future__ import annotations

import json
import unittest

from deepseek_quality_provider import DeepSeekAuditConfig, DeepSeekAuditProvider
from chess_study_export import _normalize_ai_fen_candidate
from openai_chess_fen_reviewer import OpenAIChessFenReviewer
from openai_chess_pgn_reviewer import OpenAIChessPgnReviewer


class AiChessProviderTests(unittest.TestCase):
    def test_openai_fen_candidate_provider_builds_vision_payload_and_parses_json(self) -> None:
        captured = {}

        def fake_transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return {
                "id": "resp_1",
                "output_text": json.dumps(
                    {
                        "fen": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
                        "side_to_move": "w",
                        "confidence": 0.91,
                        "uncertain_squares": [],
                        "reason": "clear synthetic board",
                        "needs_review": False,
                    }
                ),
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }

        provider = OpenAIChessFenReviewer(api_key="sk-test", model="gpt-test", transport=fake_transport)
        result = provider.propose_chess_fen_from_crop(
            {
                "diagram_id": "d1",
                "page": 1,
                "image_data": b"png-bytes",
                "image_mime_type": "image/png",
            }
        )

        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["payload"]["model"], "gpt-test")
        content = captured["payload"]["input"][0]["content"]
        self.assertTrue(any(item.get("type") == "input_image" for item in content))
        self.assertEqual(result["status"], "ai_suggested")
        self.assertEqual(result["fen"], "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertFalse(result["mutates_fen"])

    def test_piece_placement_only_ai_fen_is_normalized_as_review_candidate(self) -> None:
        fen, warnings = _normalize_ai_fen_candidate("4k3/8/8/8/8/8/8/4K3", side_to_move="unknown")

        self.assertEqual(fen, "4k3/8/8/8/8/8/8/4K3 w - - 0 1")
        self.assertIn("ai_returned_piece_placement_only", warnings)
        self.assertIn("side_to_move_unknown_placeholder", warnings)

    def test_openai_pgn_provider_builds_responses_payload_and_parses_json(self) -> None:
        captured = {}

        def fake_transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["payload"] = payload
            return {
                "id": "resp_pgn",
                "output_text": json.dumps(
                    {
                        "candidate_pgn": '[Event "Source page 1"]\n[Site "?"]\n[Date "????.??.??"]\n[Round "?"]\n[White "?"]\n[Black "?"]\n[Result "*"]\n[SourcePage "1"]\n\n1. e4 *',
                        "confidence": 0.8,
                        "reason": "legal first move candidate",
                        "warnings": [],
                        "commentary_preserved": True,
                        "variations_preserved": True,
                    }
                ),
            }

        provider = OpenAIChessPgnReviewer(api_key="sk-test", model="gpt-pgn", transport=fake_transport)
        result = provider.propose_pgn_repair({"record_id": "p1", "raw_text": "1. @e4"})

        self.assertEqual(captured["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(captured["payload"]["model"], "gpt-pgn")
        self.assertEqual(result["status"], "ai_suggested")
        self.assertIn("1. e4", result["candidate_pgn"])
        self.assertFalse(result["mutates_output"])

    def test_deepseek_pgn_glyph_clusters_are_evidence_only(self) -> None:
        captured = {}

        def fake_transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["payload"] = payload
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "token_clusters": [{"token": "@e4", "count": 2}],
                                    "candidate_mappings": [{"token": "@e4", "candidates": ["e4"], "status": "draft"}],
                                    "near_accepted_records": [],
                                    "next_review_actions": ["manual confirmation required"],
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }

        provider = DeepSeekAuditProvider(
            DeepSeekAuditConfig(api_key="deepseek-test", model="deepseek-test"),
            transport=fake_transport,
        )
        result = provider.review_pgn_glyph_clusters({"candidates": [{"token": "@e4"}]})

        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})
        self.assertEqual(result["audit_type"], "pgn_glyph_clusters")
        self.assertTrue(result["evidence_only"])
        self.assertTrue(result["requires_human_confirmation"])
        self.assertFalse(result["mutates_output"])


if __name__ == "__main__":
    unittest.main()
