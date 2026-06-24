from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from openai_chess_fen_reviewer import POLICY_ACKNOWLEDGEMENT
from scripts.build_chess_side_marker_ai_review_requests import build_side_marker_ai_review_requests
from scripts.import_chess_side_marker_ai_review import import_side_marker_ai_review
from scripts.run_chess_side_marker_ai_review_requests import run_side_marker_ai_review_requests


class ChessSideMarkerAiReviewTests(unittest.TestCase):
    def test_builds_review_only_vision_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image_path = root / "context.png"
            Image.new("RGB", (16, 16), "white").save(image_path)
            draft = root / "draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "case-1",
                        "context_crop_path": str(image_path),
                        "side_marker_candidates": [{"role": "top_right", "detected_side": "b"}],
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "requests.jsonl"

            summary = build_side_marker_ai_review_requests(draft, output_jsonl=output)
            request = json.loads(output.read_text(encoding="utf-8").strip())

            self.assertEqual(summary["request_count"], 1)
            self.assertEqual(request["custom_id"], "case-1")
            self.assertEqual(request["method"], "POST")
            self.assertIn("review evidence only", request["body"]["instructions"])
            content = request["body"]["input"][0]["content"]
            self.assertEqual(content[1]["type"], "input_image")
            self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))

    def test_import_review_response_preserves_human_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft = root / "draft.jsonl"
            draft.write_text(
                json.dumps(
                    {
                        "id": "case-1",
                        "human_side_to_move": "",
                        "human_verified": False,
                        "verified_by": "",
                        "verified_at": "",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            responses = root / "responses.jsonl"
            responses.write_text(
                json.dumps(
                    {
                        "custom_id": "case-1",
                        "response": {
                            "body": {
                                "output_text": json.dumps(
                                    {
                                        "id": "case-1",
                                        "side_to_move": "b",
                                        "marker_source": "visual_marker",
                                        "marker_role": "bottom_right",
                                        "marker_symbol": "",
                                        "confidence": 0.91,
                                        "evidence_level": "clear",
                                        "requires_human_review": False,
                                        "reason": "Visible filled marker in bottom-right probe.",
                                        "policy_acknowledgement": POLICY_ACKNOWLEDGEMENT,
                                        "human_verified": True,
                                    }
                                )
                            }
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "reviewed.jsonl"

            summary = import_side_marker_ai_review(draft, responses, output_jsonl=output)
            row = json.loads(output.read_text(encoding="utf-8").strip())

            self.assertEqual(summary["matched_response_count"], 1)
            self.assertEqual(row["ai_reviewed_side_to_move"], "b")
            self.assertEqual(row["ai_reviewed_marker_role"], "bottom_right")
            self.assertTrue(row["ai_reviewed_requires_human_review"])
            self.assertIn("ai_requires_human_review_forced", row["ai_reviewed_issues"])
            self.assertIn("ai_authoritative_field_ignored", row["ai_reviewed_issues"])
            self.assertFalse(row["human_verified"])
            self.assertEqual(row["human_side_to_move"], "")

    def test_runner_disabled_without_openai_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requests = root / "requests.jsonl"
            requests.write_text("", encoding="utf-8")
            output = root / "responses.jsonl"

            summary = run_side_marker_ai_review_requests(requests, output_jsonl=output, cwd=root)

            self.assertEqual(summary["status"], "disabled")
            self.assertEqual(summary["response_count"], 0)
            self.assertTrue(output.with_suffix(".summary.json").is_file())


if __name__ == "__main__":
    unittest.main()
