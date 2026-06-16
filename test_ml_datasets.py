from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ml_feedback import append_conversion_feedback_from_report, export_feedback_datasets
from scripts.build_ml_datasets import build_feature_collision_report, build_ml_datasets


class MlDatasetBuilderTests(unittest.TestCase):
    def test_builder_emits_route_jsonl_and_explicit_skip_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "inputs").mkdir()
            pdf_path = root / "inputs" / "book.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            manifest = {
                "cases": [
                    {
                        "id": "book_case",
                        "input_type": "pdf",
                        "document_class": "book",
                        "language": "en",
                        "target_path": "inputs/book.pdf",
                    },
                    {"id": "missing_docx", "input_type": "docx", "target_path": "inputs/missing.docx"},
                    {"id": "epub_case", "input_type": "epub", "target_path": "inputs/book.epub"},
                ]
            }
            labels = {
                "cases": {
                    "book_case": {"route_label": "book_reflow"},
                    "missing_docx": {"route_label": "docx_reflow"},
                }
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps(labels), encoding="utf-8")

            payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                repo_root=root,
                pdf_analyzer=lambda _path: SimpleNamespace(
                    profile="book_reflow",
                    confidence=0.91,
                    page_count=4,
                    text_pages=4,
                    scanned_pages=0,
                    image_pages=0,
                    has_toc=True,
                    has_tables=False,
                    has_diagrams=False,
                    has_meaningful_images=False,
                    estimated_columns=1,
                    heading_density=0.4,
                    font_consistency=0.9,
                    layout_heavy=False,
                    text_heavy=True,
                ),
            )

            route_lines = (root / "reports" / "ml" / "datasets" / "route_examples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()

            self.assertEqual(payload["route_example_count"], 1)
            self.assertEqual(len(route_lines), 1)
            self.assertEqual(json.loads(route_lines[0])["label"], "book_reflow")
            self.assertEqual(payload["status"], "insufficient_data")
            self.assertIn("missing_input", {item["reason"] for item in payload["skipped"]})
            self.assertIn("unsupported_input_type:epub", {item["reason"] for item in payload["skipped"]})

    def test_feedback_log_extends_route_dataset_when_training_intent_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = root / "reports" / "conversion.json"
            report_path.parent.mkdir(parents=True)
            conversion_report = {
                "source_type": "pdf",
                "source_path": "inputs/magazine.pdf",
                "output_path": "output/magazine.epub",
                "analysis": {
                    "profile": "book_reflow",
                    "confidence": 0.58,
                    "page_count": 8,
                    "text_pages": 8,
                    "scanned_pages": 0,
                    "image_pages": 5,
                    "has_toc": False,
                    "has_tables": False,
                    "has_diagrams": False,
                    "has_meaningful_images": True,
                    "estimated_columns": 2,
                    "heading_density": 0.1,
                    "font_consistency": 0.7,
                    "layout_heavy": True,
                    "text_heavy": False,
                    "route_decision": {
                        "heuristic_profile": "book_reflow",
                        "heuristic_confidence": 0.58,
                        "selected_profile": "book_reflow",
                        "mode": "shadow",
                        "override_used": False,
                        "model_version": "test-model",
                    },
                },
                "quality_report": {
                    "validation_status": "passed_with_warnings",
                    "warnings": ["layout review needed"],
                    "final_output_size_bytes": 1234,
                },
                "document_summary": {"language": "en", "profile": "book_reflow"},
            }
            report_path.write_text(json.dumps(conversion_report), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps({"cases": {}}), encoding="utf-8")

            log_payload = append_conversion_feedback_from_report(
                report_path=report_path,
                log_path="reports/ml/feedback/conversion_feedback.jsonl",
                repo_root=root,
                case_id="magazine_feedback",
                feedback_status="accepted",
                quality_label="usable",
                quality_score=4,
                route_label="magazine_reflow",
                issue_tags=["layout", "toc"],
                notes="Operator selected magazine route for future training data.",
                reviewer="qa",
                include_in_training=True,
                created_at="2026-05-11T10:00:00Z",
            )
            export_payload = export_feedback_datasets(
                log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
                output_dir="reports/ml/feedback",
                repo_root=root,
            )
            dataset_payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                feedback_log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
                repo_root=root,
            )

            route_lines = (root / "reports" / "ml" / "datasets" / "route_examples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            feedback_lines = (root / "reports" / "ml" / "feedback" / "route_feedback_examples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()

            self.assertEqual(log_payload["status"], "logged")
            self.assertTrue(log_payload["include_in_route_training"])
            self.assertEqual(export_payload["status"], "exported")
            self.assertEqual(export_payload["route_example_count"], 1)
            self.assertEqual(dataset_payload["feedback_record_count"], 1)
            self.assertEqual(dataset_payload["feedback_route_example_count"], 1)
            self.assertFalse(dataset_payload["feedback_skipped"])
            self.assertEqual(len(route_lines), 1)
            self.assertEqual(len(feedback_lines), 1)
            route_example = json.loads(route_lines[0])
            self.assertEqual(route_example["case_id"], "magazine_feedback")
            self.assertEqual(route_example["label"], "magazine_reflow")
            self.assertEqual(route_example["feedback_status"], "accepted")
            self.assertTrue(route_example["features"]["layout_heavy"])
            self.assertFalse(route_example["features"]["text_heavy"])

    def test_builder_detects_feature_hash_label_collisions(self) -> None:
        report = build_feature_collision_report(
            [
                {
                    "case_id": "same_features_book",
                    "label": "book_reflow",
                    "features_hash": "abc123",
                    "features": {"input_type": "pdf", "text_heavy": True},
                },
                {
                    "case_id": "same_features_magazine",
                    "label": "magazine_reflow",
                    "features_hash": "abc123",
                    "features": {"input_type": "pdf", "text_heavy": True},
                },
            ]
        )

        self.assertEqual(report["status"], "blocked_feature_collision")
        self.assertEqual(report["collision_count"], 1)
        self.assertEqual(report["collisions"][0]["labels"], ["book_reflow", "magazine_reflow"])

    def test_feedback_route_label_without_training_intent_stays_audit_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = root / "reports" / "conversion.json"
            report_path.parent.mkdir(parents=True)
            report_path.write_text(
                json.dumps(
                    {
                        "source_type": "pdf",
                        "analysis": {
                            "profile": "book_reflow",
                            "confidence": 0.75,
                            "page_count": 3,
                            "text_pages": 3,
                            "scanned_pages": 0,
                            "image_pages": 0,
                            "text_heavy": True,
                            "layout_heavy": False,
                        },
                        "quality_report": {"validation_status": "passed"},
                    }
                ),
                encoding="utf-8",
            )

            log_payload = append_conversion_feedback_from_report(
                report_path=report_path,
                log_path="reports/ml/feedback/conversion_feedback.jsonl",
                repo_root=root,
                case_id="audit_only",
                quality_label="usable",
                route_label="book_reflow",
                issue_tags=["route"],
                reviewer="qa",
            )
            export_payload = export_feedback_datasets(
                log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
                output_dir="reports/ml/feedback",
                repo_root=root,
            )

            self.assertEqual(log_payload["status"], "logged")
            self.assertFalse(log_payload["include_in_route_training"])
            self.assertEqual(log_payload["dataset_reason"], "not_marked_for_training")
            self.assertEqual(export_payload["route_example_count"], 0)


if __name__ == "__main__":
    unittest.main()
