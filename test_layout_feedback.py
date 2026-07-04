import json
import tempfile
import unittest
from pathlib import Path

from ml_feedback import append_user_feedback, summarize_layout_feedback_metrics
from scripts.build_ml_datasets import build_ml_datasets


class LayoutFeedbackTests(unittest.TestCase):
    def test_layout_metrics_summarize_view_modes_preview_usage_and_scores(self) -> None:
        records = [
            {
                "layout_feedback": {
                    "view_mode": "reader",
                    "original_preview_opened": True,
                    "feedback_label": "good",
                    "issue_tags": ["text_too_wide"],
                    "interaction_events": ["layout_view_selected", "reader_mode_selected", "original_preview_opened"],
                }
            },
            {
                "layout_feedback": {
                    "view_mode": "study",
                    "original_preview_opened": False,
                    "feedback_label": "partial",
                    "issue_tags": ["diagram_too_small", "exercise_cards_unclear"],
                    "interaction_events": ["layout_view_selected", "study_mode_selected", "layout_feedback_submitted"],
                }
            },
        ]

        metrics = summarize_layout_feedback_metrics(records)

        self.assertEqual(metrics["layout_feedback_record_count"], 2)
        self.assertEqual(metrics["preferred_view_mode_count"], {"reader": 1, "study": 1})
        self.assertEqual(metrics["original_preview_usage_rate"], 0.5)
        self.assertEqual(metrics["layout_issue_tag_counts"]["diagram_too_small"], 1)
        self.assertEqual(metrics["reader_mode_feedback_score"], 1.0)
        self.assertEqual(metrics["study_mode_feedback_score"], 0.5)
        self.assertIsNone(metrics["audit_mode_feedback_score"])
        self.assertFalse(metrics["online_learning"])

    def test_user_layout_feedback_normalizes_payload_and_rejects_book_text_from_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = _layout_job(text_excerpt="secret source text")

            record = append_user_feedback(
                job_id="layout-job",
                job=job,
                feedback={
                    "status": "accepted",
                    "quality_label": "good",
                    "route_label": "book_reflow",
                    "issue_tags": ["layout"],
                    "reviewer": "qa",
                    "notes": "Operator note is not part of layout ledger text.",
                    "include_in_training": False,
                    "layout_feedback": {
                        "artifact_type": "final_pdf_two_crop_reader",
                        "view_mode": "audit",
                        "block_type": "audit",
                        "screen_width_bucket": "tablet",
                        "original_preview_opened": True,
                        "feedback_label": "bad",
                        "issue_tags": ["too_much_diagnostics", "unknown_tag"],
                    },
                },
                event_path=root / "reports/ml/feedback/conversion_feedback.jsonl",
                ledger_repo_root=root,
            )

            layout = record["layout_feedback"]
            self.assertEqual(layout["view_mode"], "audit")
            self.assertEqual(layout["issue_tags"], ["too_much_diagnostics"])
            self.assertIn("audit_mode_selected", layout["interaction_events"])
            events_path = root / "reports/ml/learning_ledger/learning_events.jsonl"
            serialized_events = events_path.read_text(encoding="utf-8")
            self.assertNotIn("secret source text", serialized_events)
            self.assertNotIn("Operator note is not part", serialized_events)

    def test_dataset_readiness_dashboard_exposes_layout_feedback_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps({"cases": {}}), encoding="utf-8")
            append_user_feedback(
                job_id="layout-job",
                job=_layout_job(),
                feedback={
                    "status": "accepted",
                    "quality_label": "good",
                    "route_label": "book_reflow",
                    "issue_tags": ["layout"],
                    "reviewer": "qa",
                    "include_in_training": False,
                    "layout_feedback": {
                        "artifact_type": "conversion_reader",
                        "view_mode": "study",
                        "block_type": "exercise",
                        "screen_width_bucket": "desktop",
                        "original_preview_opened": True,
                        "feedback_label": "good",
                        "issue_tags": ["diagram_too_small"],
                    },
                },
                event_path=root / "reports/ml/feedback/conversion_feedback.jsonl",
                ledger_repo_root=root,
            )

            payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                feedback_log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
                repo_root=root,
                write_ledger=False,
            )

            self.assertEqual(payload["layout_feedback_metrics"]["layout_feedback_record_count"], 1)
            self.assertEqual(payload["layout_feedback_metrics"]["preferred_view_mode_count"]["study"], 1)
            latest = json.loads((root / "reports/ml/datasets/latest_readiness.json").read_text(encoding="utf-8"))
            layout_component = latest["components"]["layout_feedback"]
            self.assertEqual(layout_component["status"], "collecting")
            self.assertEqual(layout_component["preferred_view_mode_count"]["study"], 1)
            self.assertEqual(layout_component["layout_issue_tag_counts"]["diagram_too_small"], 1)
            html = (root / "reports/ml/datasets/latest_readiness.html").read_text(encoding="utf-8")
            self.assertIn("layout_feedback", html)
            self.assertIn("diagram_too_small=1", html)


def _layout_job(*, text_excerpt: str = "") -> dict[str, object]:
    return {
        "job_id": "layout-job",
        "source_type": "pdf",
        "filename": "layout.pdf",
        "text_excerpt": text_excerpt,
        "metadata": {
            "learning_ledger": {"input_fingerprint": "sha256:layout"},
            "source_analysis": {
                "profile": "book_reflow",
                "page_count": 8,
                "text_pages": 8,
                "scanned_pages": 0,
                "image_pages": 2,
                "has_toc": True,
                "has_tables": False,
                "has_diagrams": True,
                "has_meaningful_images": True,
                "estimated_columns": 1,
                "heading_density": 0.3,
                "font_consistency": 0.8,
                "layout_heavy": True,
                "text_heavy": True,
            },
            "premium_scoring": {"premium_score": 8.1, "status": "passed"},
        },
    }


if __name__ == "__main__":
    unittest.main()
