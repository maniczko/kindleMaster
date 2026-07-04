from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import app as app_module
from learning_ledger import (
    DEFAULT_EVENTS_PATH,
    PRIVACY_PAYLOAD,
    build_conversion_learning_index,
    record_user_behavior_signal,
    summarize_user_behavior_signals,
)
from scripts.build_ml_datasets import build_ml_datasets


class UserBehaviorSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = app_module.app.test_client()
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

    def test_weak_signal_event_is_indexed_without_training_label_or_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            record = record_user_behavior_signal(
                conversion_id="job-weak",
                event_type="send_to_kindle_clicked",
                artifact_type="epub",
                view_mode="reader",
                job=_ready_job("job-weak", text_excerpt="private book text must not enter ledger"),
                repo_root=root,
            )

            events = [
                json.loads(line)
                for line in (root / DEFAULT_EVENTS_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            index = build_conversion_learning_index(events_path=root / DEFAULT_EVENTS_PATH)
            summary = summarize_user_behavior_signals(events_path=root / DEFAULT_EVENTS_PATH)

        self.assertEqual(record["status"], "recorded")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "send_to_kindle_clicked")
        self.assertEqual(event["source"], "user_action")
        self.assertEqual(event["signal_strength"], "medium")
        self.assertFalse(event["training_label"])
        self.assertFalse(event["training_eligible"])
        self.assertEqual(event["privacy"], PRIVACY_PAYLOAD)
        self.assertNotIn("private book text", json.dumps(event, ensure_ascii=False))
        self.assertEqual(index["conversions"]["job-weak"]["weak_signal_count"], 1)
        self.assertEqual(index["conversions"]["job-weak"]["weak_signal_types"]["send_to_kindle_clicked"], 1)
        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["per_job"]["job-weak"]["artifact_type_counts"]["epub"], 1)
        self.assertEqual(summary["training_label_true_count"], 0)
        self.assertEqual(summary["training_eligible_true_count"], 0)

    def test_behavior_signal_endpoint_records_user_action_without_training_label(self) -> None:
        job_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        self._register_ready_job(job_id)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.dict(os.environ, {"KINDLEMASTER_LEARNING_LEDGER_ROOT": str(root)}):
                response = self.client.post(
                    f"/learning/behavior/{job_id}",
                    json={
                        "event_type": "reader_mode_changed",
                        "artifact_type": "final_pdf_two_crop_reader",
                        "view_mode": "study",
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            self.assertEqual(payload["behavior_signal"]["event_type"], "reader_mode_changed")
            self.assertFalse(payload["behavior_signal"]["training_label"])
            self.assertFalse(payload["behavior_signal"]["training_eligible"])
            events = [
                json.loads(line)
                for line in (root / DEFAULT_EVENTS_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(events[0]["artifact_type"], "final_pdf_two_crop_reader")
            self.assertEqual(events[0]["view_mode"], "study")
            self.assertFalse(events[0]["training_label"])

    def test_dataset_dashboard_exposes_weak_signals_without_using_them_as_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps({"cases": {}}), encoding="utf-8")
            record_user_behavior_signal(
                conversion_id="dashboard-job",
                event_type="artifact_downloaded",
                artifact_type="epub",
                job=_ready_job("dashboard-job"),
                repo_root=root,
            )
            record_user_behavior_signal(
                conversion_id="dashboard-job",
                event_type="diagnostics_opened",
                artifact_type="pdf_layout_preview",
                job=_ready_job("dashboard-job"),
                repo_root=root,
            )

            payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                repo_root=root,
                write_ledger=False,
            )

            self.assertEqual(payload["route_example_count"], 0)
            self.assertEqual(payload["feedback_route_example_count"], 0)
            self.assertEqual(payload["weak_signal_metrics"]["event_count"], 2)
            self.assertEqual(payload["weak_signal_metrics"]["training_label_true_count"], 0)
            latest = json.loads((root / "reports/ml/datasets/latest_readiness.json").read_text(encoding="utf-8"))
            weak_component = latest["components"]["weak_behavior_signals"]
            self.assertEqual(weak_component["status"], "collecting")
            self.assertEqual(weak_component["event_type_counts"]["artifact_downloaded"], 1)
            self.assertEqual(weak_component["artifact_type_counts"]["pdf_layout_preview"], 1)
            self.assertFalse(weak_component["promotion_allowed"])
            html = (root / "reports/ml/datasets/latest_readiness.html").read_text(encoding="utf-8")
            self.assertIn("weak_behavior_signals", html)
            self.assertIn("training_label_true=0", html)

    def _register_ready_job(self, job_id: str) -> None:
        created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        with app_module._CONVERSION_JOBS_LOCK:
            app_module._CONVERSION_JOBS[job_id] = _ready_job(job_id, created_at=created_at)


def _ready_job(job_id: str, *, created_at: str | None = None, text_excerpt: str = "") -> dict[str, object]:
    stamp = created_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "job_id": job_id,
        "status": "ready",
        "message": "EPUB gotowy.",
        "source_type": "pdf",
        "filename": f"{job_id}.pdf",
        "source_path": "",
        "output_path": "",
        "download_name": f"{job_id}.epub",
        "created_at": stamp,
        "updated_at": stamp,
        "text_excerpt": text_excerpt,
        "metadata": {
            "learning_ledger": {"input_fingerprint": f"sha256:{job_id}"},
            "source_analysis": {
                "profile": "book_reflow",
                "page_count": 4,
                "text_pages": 4,
                "scanned_pages": 0,
                "image_pages": 0,
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
