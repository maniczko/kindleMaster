from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app_runtime_services import ConversionRequest, run_document_conversion
from learning_ledger import (
    DEFAULT_EVENTS_PATH,
    DEFAULT_INDEX_PATH,
    PRIVACY_PAYLOAD,
    build_conversion_learning_index,
    record_chess_benchmark_built,
    record_chess_label_added,
    record_chess_profile_promoted,
    record_dataset_built,
    record_model_evaluated,
    record_model_promotion_blocked,
    record_model_promoted,
    record_model_trained,
)
from ml_feedback import append_user_feedback


class LearningLedgerTests(unittest.TestCase):
    def test_fake_conversion_records_append_only_event_and_index_without_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "input.pdf"
            source.write_bytes(b"%PDF-1.4 fake private document bytes")
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                outcome = run_document_conversion(
                    ConversionRequest(
                        conversion_id="job-ledger-1",
                        source_path=str(source),
                        source_type="pdf",
                        original_filename="private-book.pdf",
                        profile="auto-premium",
                        language="en",
                        feedback_enabled=False,
                        quality_gate_mode="off",
                    ),
                    convert_impl=Mock(
                        return_value={
                            "epub_bytes": b"epub-bytes",
                            "source_type": "pdf",
                            "analysis": {
                                "profile": "book_reflow",
                                "confidence": 0.8,
                                "route_decision": {
                                    "mode": "shadow",
                                    "selected_profile": "book_reflow",
                                    "model_version": "route-v1",
                                    "input_features_hash": "features-123",
                                },
                            },
                            "quality_report": {
                                "validation_status": "passed",
                                "epubcheck_status": "passed",
                                "warnings": [],
                                "premium_scoring": {
                                    "premium_score": 8.5,
                                    "kindle_ready": True,
                                    "premium_ready": False,
                                },
                            },
                            "document_summary": {"profile": "book_reflow", "section_count": 3},
                        }
                    ),
                    heading_repair_impl=Mock(),
                )
            finally:
                os.chdir(old_cwd)

            events_path = root / DEFAULT_EVENTS_PATH
            index_path = root / DEFAULT_INDEX_PATH
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
            index = json.loads(index_path.read_text(encoding="utf-8"))

            self.assertEqual(outcome.metadata["learning_ledger"]["status"], "recorded")
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["schema"], "kindlemaster.learning_ledger.event.v1")
            self.assertEqual(event["event_type"], "conversion_completed")
            self.assertEqual(event["conversion_id"], "job-ledger-1")
            self.assertEqual(event["source_type"], "pdf")
            self.assertEqual(event["profile_requested"], "auto-premium")
            self.assertEqual(event["profile_selected"], "book_reflow")
            self.assertEqual(event["route_model_mode"], "shadow")
            self.assertEqual(event["route_model_version"], "route-v1")
            self.assertEqual(event["feature_hash"], "features-123")
            self.assertEqual(event["quality_score"], 8.5)
            self.assertEqual(event["privacy"], PRIVACY_PAYLOAD)
            self.assertNotIn("private document", json.dumps(event, ensure_ascii=False))
            self.assertIn("job-ledger-1", index["conversions"])
            self.assertEqual(index["conversions"]["job-ledger-1"]["event_types"]["conversion_completed"], 1)

    def test_user_feedback_event_links_to_conversion_and_training_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            job = {
                "job_id": "job-feedback",
                "source_type": "pdf",
                "filename": "book.pdf",
                "metadata": {
                    "learning_ledger": {"input_fingerprint": "sha256:abc"},
                    "source_analysis": {
                        "profile": "book_reflow",
                        "page_count": 5,
                        "text_pages": 5,
                        "scanned_pages": 0,
                        "image_pages": 0,
                        "has_toc": True,
                        "has_tables": False,
                        "has_diagrams": False,
                        "has_meaningful_images": False,
                        "estimated_columns": 1,
                        "heading_density": 0.4,
                        "font_consistency": 0.9,
                        "layout_heavy": False,
                        "text_heavy": True,
                        "route_decision": {
                            "mode": "shadow",
                            "selected_profile": "book_reflow",
                            "model_version": "route-v1",
                            "input_features_hash": "fh",
                        },
                    },
                    "premium_scoring": {"premium_score": 8.0, "status": "passed"},
                },
            }

            record = append_user_feedback(
                job_id="job-feedback",
                job=job,
                feedback={
                    "status": "accepted",
                    "quality_label": "usable",
                    "route_label": "book_reflow",
                    "issue_tags": ["layout"],
                    "reviewer": "qa",
                    "notes": "This note must not be copied into the ledger event.",
                    "include_in_training": True,
                },
                event_path=root / "reports/ml/feedback/conversion_feedback.jsonl",
                ledger_repo_root=root,
            )

            events = [
                json.loads(line)
                for line in (root / DEFAULT_EVENTS_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(record["learning_ledger"]["status"], "recorded")
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event["event_type"], "user_feedback_added")
            self.assertEqual(event["conversion_id"], "job-feedback")
            self.assertEqual(event["feedback_id"], record["record_id"])
            self.assertTrue(event["training_eligible"])
            self.assertEqual(event["dataset_reason"], "ready")
            self.assertEqual(event["feedback"]["notes_length"], len("This note must not be copied into the ledger event."))
            self.assertNotIn("This note must not", json.dumps(event, ensure_ascii=False))

    def test_dataset_training_and_promotion_events_are_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = root / "models" / "candidate.json"
            model.parent.mkdir(parents=True)
            model.write_text(json.dumps({"model_version": "route-candidate-1"}), encoding="utf-8")
            dataset = root / "reports" / "ml" / "datasets" / "route_examples.jsonl"
            dataset.parent.mkdir(parents=True)
            dataset.write_text("", encoding="utf-8")

            record_dataset_built(
                dataset_payload={
                    "dataset_version": "20260703-120000-unit",
                    "dataset_hash": "sha256:test",
                    "status": "ready",
                    "training_readiness_status": "ready",
                    "promotion_allowed": True,
                    "route_example_count": 40,
                    "feedback_record_count": 2,
                    "quality_feedback_example_count": 3,
                    "outputs": {"route_examples": str(dataset)},
                },
                repo_root=root,
            )
            record_model_trained(
                training_payload={
                    "status": "candidate_trained",
                    "model_path": str(model),
                    "metrics": {"accuracy": 0.91, "macro_f1": 0.9, "example_count": 40},
                },
                dataset_path=dataset,
                repo_root=root,
            )
            record_model_evaluated(
                evaluation_payload={"status": "evaluated", "accuracy": 0.92, "example_count": 40},
                dataset_path=dataset,
                model_path=model,
                repo_root=root,
            )
            record_model_promotion_blocked(
                promotion_payload={
                    "status": "promotion_blocked",
                    "dataset_version": "20260703-120000-unit",
                    "before_model_version": "route-current",
                    "candidate_model_version": "route-candidate-1",
                    "promotion_status": "blocked",
                    "promotion_decision": {"reasons": ["candidate_not_better"]},
                    "metric_delta": {"accuracy_delta": -0.01},
                    "corpus_gate_status": "not_run",
                },
                repo_root=root,
            )
            record_model_promoted(
                promotion_payload={
                    "status": "promoted",
                    "candidate_path": str(model),
                    "model_path": str(model),
                    "metric_gates": {"passed": True},
                    "corpus_gate": {"passed": True},
                },
                repo_root=root,
            )

            index = build_conversion_learning_index(events_path=root / DEFAULT_EVENTS_PATH)
            events = [
                json.loads(line)
                for line in (root / DEFAULT_EVENTS_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            dataset_event = events[0]
            self.assertEqual(index["event_type_counts"]["dataset_built"], 1)
            self.assertEqual(index["event_type_counts"]["model_trained"], 1)
            self.assertEqual(index["event_type_counts"]["model_evaluated"], 1)
            self.assertEqual(index["event_type_counts"]["model_promotion_blocked"], 1)
            self.assertEqual(index["event_type_counts"]["model_promoted"], 1)
            self.assertEqual(index["event_count"], 5)
            self.assertEqual(dataset_event["dataset_version"], "20260703-120000-unit")
            self.assertEqual(dataset_event["training_readiness_status"], "ready")
            self.assertTrue(dataset_event["promotion_allowed"])
            self.assertEqual(dataset_event["quality_feedback_count"], 3)

    def test_chess_learning_events_are_indexed_without_runtime_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            label = {
                "label_id": "cl-test",
                "diagram_id": "p010_d03",
                "label_type": "side_marker",
                "label_value": "black",
                "reviewer": "qa",
                "human_verified": True,
                "accepted_for_runtime": False,
                "bypasses_full_fen_gate": False,
            }

            label_event = record_chess_label_added(label_payload=label, repo_root=root)
            benchmark_event = record_chess_benchmark_built(
                benchmark_payload={
                    "status": "READY_FOR_BENCHMARK",
                    "dataset_version": "chess-20260703",
                    "source_files": ["reference_inputs/chess_fen/labels/side_marker_labels.jsonl"],
                    "summary": {
                        "usable_label_count": 30,
                        "label_type_counts": {"side_marker": 30},
                    },
                },
                repo_root=root,
            )
            promoted_event = record_chess_profile_promoted(
                promotion_payload={
                    "status": "promoted",
                    "dataset_version": "chess-20260703",
                    "profile_version_before": "profile-v1",
                    "profile_version": "profile-v2",
                },
                repo_root=root,
            )

            index = build_conversion_learning_index(events_path=root / DEFAULT_EVENTS_PATH)
            events = [
                json.loads(line)
                for line in (root / DEFAULT_EVENTS_PATH).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(label_event["status"], "recorded")
        self.assertEqual(benchmark_event["status"], "recorded")
        self.assertEqual(promoted_event["status"], "recorded")
        self.assertEqual(index["event_type_counts"]["chess_label_added"], 1)
        self.assertEqual(index["event_type_counts"]["chess_benchmark_built"], 1)
        self.assertEqual(index["event_type_counts"]["chess_profile_promoted"], 1)
        label_payload = events[0]
        self.assertEqual(label_payload["label_type"], "side_marker")
        self.assertFalse(label_payload["chess_metrics"]["accepted_for_runtime"])
        self.assertFalse(label_payload["chess_metrics"]["bypasses_full_fen_gate"])


if __name__ == "__main__":
    unittest.main()
