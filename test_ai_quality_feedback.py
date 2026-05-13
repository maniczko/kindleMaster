from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_quality_feedback import maybe_record_ai_quality_feedback


class AIQualityFeedbackTests(unittest.TestCase):
    def test_feedback_recording_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = maybe_record_ai_quality_feedback(
                {"status": "reported"},
                original_filename="book.epub",
                language="pl",
                publication_profile="book_reflow",
                reports_dir=Path(temp_dir),
                env={},
            )

        self.assertEqual(result["status"], "skipped")

    def test_feedback_recording_writes_jsonl_and_snapshot(self) -> None:
        report = {
            "status": "accepted_pending_application",
            "provider": {"ocr_cleanup": "openai-quality", "toc_detection": "openai-quality"},
            "before_quality_score": 5.2,
            "after_quality_score": 5.2,
            "changed_fragment_count": 1,
            "changed_toc_entry_count": 2,
            "fallback_reasons": [],
            "learning_signals": {
                "candidate_fix_count": 3,
                "should_create_fixture": True,
                "recommended_actions": ["review_toc_segmentation_heuristics"],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            result = maybe_record_ai_quality_feedback(
                report,
                original_filename="C:/tmp/book.epub",
                language="pl",
                publication_profile="magazine_reflow",
                reports_dir=Path(temp_dir),
                env={"KINDLEMASTER_AI_FEEDBACK_RECORD": "1"},
            )
            jsonl_path = Path(result["jsonl_path"])
            snapshot_path = Path(result["snapshot_path"])
            line = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "recorded")
        self.assertEqual(line["original_filename"], "book.epub")
        self.assertFalse(line["self_modifying_code_allowed"])
        self.assertEqual(snapshot["learning_signals"]["candidate_fix_count"], 3)


if __name__ == "__main__":
    unittest.main()
