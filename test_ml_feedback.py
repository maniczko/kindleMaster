from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ml_feedback import append_user_feedback, feedback_records_for_job, load_feedback_records, summarize_feedback_records


class MlFeedbackTests(unittest.TestCase):
    def test_user_feedback_writes_verified_training_record_and_ledger_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "reports/ml/feedback/conversion_feedback.jsonl"
            job = {
                "job_id": "job-feedback-ui",
                "filename": "sample.pdf",
                "source_type": "pdf",
                "source_path": str(root / "sample.pdf"),
                "output_path": str(root / "sample.epub"),
                "metadata": {
                    "source_analysis": {
                        "profile": "book_reflow",
                        "confidence": 0.91,
                        "page_count": 12,
                        "text_pages": 12,
                        "scanned_pages": 0,
                        "image_pages": 0,
                        "text_heavy": True,
                        "layout_heavy": False,
                    }
                },
            }

            record = append_user_feedback(
                job_id="job-feedback-ui",
                job=job,
                feedback={
                    "status": "accepted",
                    "quality_label": "good",
                    "route_label": "book_reflow",
                    "issue_tags": ["toc", "layout"],
                    "reviewer": "operator",
                    "notes": "Readable note stays in feedback, not in ledger event text.",
                    "include_in_training": True,
                },
                event_path=log_path,
                ledger_repo_root=root,
            )

            self.assertTrue(log_path.is_file())
            self.assertEqual(record["dataset"]["reason"], "ready")
            self.assertTrue(record["dataset"]["include_in_route_training"])
            self.assertEqual(record["learning_ledger"]["status"], "recorded")
            saved = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(saved["record_id"], record["record_id"])
            self.assertEqual(saved["learning_ledger"]["status"], "recorded")
            self.assertNotIn("Readable note", (root / "reports/ml/learning_ledger/learning_events.jsonl").read_text(encoding="utf-8"))

            records, skipped = feedback_records_for_job("job-feedback-ui", log_paths=[log_path])
            self.assertFalse(skipped)
            self.assertEqual(records[0]["route_label"], "book_reflow")
            self.assertTrue(records[0]["include_in_training"])

    def test_training_intent_requires_route_quality_tags_and_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError) as caught:
                append_user_feedback(
                    job_id="job-invalid",
                    job={"metadata": {}, "source_type": "pdf"},
                    feedback={"status": "accepted", "include_in_training": True},
                    event_path=root / "feedback.jsonl",
                    ledger_repo_root=root,
                )
            message = str(caught.exception)
            self.assertIn("missing_or_invalid_route_label", message)
            self.assertIn("missing_or_invalid_quality_label", message)
            self.assertIn("missing_issue_tags", message)
            self.assertIn("missing_reviewer", message)

    def test_summary_separates_product_signal_from_training_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "feedback.jsonl"
            append_user_feedback(
                job_id="job-signal",
                job={"metadata": {}, "source_type": "pdf"},
                feedback={"status": "needs_review", "quality_label": "unknown", "issue_tags": ["ocr"]},
                event_path=log_path,
                ledger_repo_root=root,
            )

            records, skipped = load_feedback_records(log_paths=[log_path])
            summary = summarize_feedback_records(records, skipped)

            self.assertEqual(summary["feedback_record_count"], 1)
            self.assertEqual(summary["product_signal_count"], 1)
            self.assertEqual(summary["training_eligible_count"], 0)
            self.assertEqual(summary["by_status"]["needs_review"], 1)
            self.assertEqual(summary["by_issue_tag"]["ocr"], 1)


if __name__ == "__main__":
    unittest.main()
