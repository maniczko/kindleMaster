from __future__ import annotations

import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import app as app_module
from app import app


class AppFeedbackApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app.test_client()
        self.store_temp_dir = tempfile.TemporaryDirectory()
        self.original_conversion_job_store = app_module._CONVERSION_JOB_STORE
        app_module._CONVERSION_JOB_STORE = app_module.ConversionJobStore(
            app_module._CONVERSION_JOBS,
            app_module._CONVERSION_JOBS_LOCK,
            persistence_path=Path(self.store_temp_dir.name) / "conversion_jobs.json",
            active_statuses=app_module.ACTIVE_CONVERSION_JOB_STATUSES,
        )
        with app_module._CONVERSION_JOBS_LOCK:
            self.saved_jobs = {job_id: dict(job) for job_id, job in app_module._CONVERSION_JOBS.items()}
            app_module._CONVERSION_JOBS.clear()

    def tearDown(self) -> None:
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS.clear()
            app_module._CONVERSION_JOBS.update({job_id: dict(job) for job_id, job in self.saved_jobs.items()})
        app_module._CONVERSION_JOB_STORE = self.original_conversion_job_store
        self.store_temp_dir.cleanup()

    def _register_ready_job(self, job_id: str = "feedback-job") -> None:
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = {
                "job_id": job_id,
                "status": "ready",
                "message": "EPUB gotowy.",
                "source_type": "pdf",
                "filename": "sample.pdf",
                "source_path": "",
                "output_path": "",
                "download_name": "sample.epub",
                "created_at": created_at,
                "updated_at": created_at,
                "metadata": {
                    "source_analysis": {
                        "profile": "book_reflow",
                        "confidence": 0.9,
                        "page_count": 10,
                        "text_pages": 10,
                        "scanned_pages": 0,
                        "image_pages": 0,
                        "text_heavy": True,
                        "layout_heavy": False,
                    },
                    "premium_scoring": {"premium_score": 88, "status": "passed"},
                },
            }

    def test_feedback_post_get_and_summary_use_local_jsonl_and_ledger(self) -> None:
        self._register_ready_job()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "reports/ml/feedback/conversion_feedback.jsonl"
            with patch.dict(
                os.environ,
                {
                    "KINDLEMASTER_FEEDBACK_LOG": str(log_path),
                    "KINDLEMASTER_LEARNING_LEDGER_ROOT": str(root),
                },
            ):
                response = self.client.post(
                    "/convert/feedback/feedback-job",
                    json={
                        "status": "accepted",
                        "quality_label": "good",
                        "route_label": "book_reflow",
                        "issue_tags": ["toc", "layout"],
                        "reviewer": "operator",
                        "notes": "Dobre do uczenia po weryfikacji.",
                        "include_in_training": True,
                    },
                )

                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertTrue(payload["success"])
                self.assertTrue(payload["include_in_training"])
                self.assertEqual(payload["feedback_record"]["route_label"], "book_reflow")
                self.assertEqual(payload["learning_ledger"]["status"], "recorded")
                self.assertTrue(log_path.is_file())

                get_response = self.client.get("/convert/feedback/feedback-job")
                get_payload = get_response.get_json()
                self.assertEqual(get_response.status_code, 200)
                self.assertEqual(get_payload["feedback_count"], 1)
                self.assertEqual(get_payload["latest_feedback"]["status"], "accepted")
                self.assertTrue(get_payload["latest_feedback"]["include_in_training"])

                summary_response = self.client.get("/learning/feedback/summary")
                summary_payload = summary_response.get_json()
                self.assertEqual(summary_response.status_code, 200)
                self.assertEqual(summary_payload["summary"]["feedback_record_count"], 1)
                self.assertEqual(summary_payload["summary"]["training_eligible_count"], 1)

    def test_feedback_training_validation_returns_readable_missing_list(self) -> None:
        self._register_ready_job("feedback-invalid")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ,
                {
                    "KINDLEMASTER_FEEDBACK_LOG": str(Path(tmp) / "feedback.jsonl"),
                    "KINDLEMASTER_LEARNING_LEDGER_ROOT": tmp,
                },
            ):
                response = self.client.post(
                    "/convert/feedback/feedback-invalid",
                    json={"status": "accepted", "include_in_training": True},
                )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["error_code"], "invalid_training_feedback")
        self.assertIn("missing_or_invalid_route_label", payload["missing"])
        self.assertIn("missing_or_invalid_quality_label", payload["missing"])
        self.assertIn("missing_issue_tags", payload["missing"])
        self.assertIn("missing_reviewer", payload["missing"])

    def test_feedback_get_for_missing_job_returns_empty_state_without_browser_404(self) -> None:
        response = self.client.get("/convert/feedback/missing-job")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["feedback_count"], 0)
        self.assertEqual(payload["feedback_records"], [])
        self.assertEqual(payload["skipped"][0]["reason"], "conversion_job_missing")


if __name__ == "__main__":
    unittest.main()
