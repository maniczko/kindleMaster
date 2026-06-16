from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ml_feedback import append_conversion_feedback_from_report, export_feedback_datasets
from scripts.build_ml_datasets import build_feature_collision_report, build_ml_datasets


class MlDatasetBuilderTests(unittest.TestCase):
    def test_reference_importer_adds_new_pdf_once_with_weak_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_root = root / "reference_inputs" / "pdf"
            input_root.mkdir(parents=True)
            pdf_path = input_root / "New Chess Workbook.pdf"
            pdf_path.write_bytes(b"%PDF-test")
            (root / "reference_inputs" / "manifest.json").write_text(
                json.dumps({"version": 2, "root_dir": ".", "cases": []}),
                encoding="utf-8",
            )
            (root / "reference_inputs" / "ml_labels.json").write_text(
                json.dumps({"version": 1, "classes": ["diagram_book_reflow"], "cases": {}}),
                encoding="utf-8",
            )

            first_payload = import_reference_inputs(repo_root=root, input_types=("pdf",))
            second_payload = import_reference_inputs(repo_root=root, input_types=("pdf",))

            manifest = json.loads((root / "reference_inputs" / "manifest.json").read_text(encoding="utf-8"))
            labels = json.loads((root / "reference_inputs" / "ml_labels.json").read_text(encoding="utf-8"))
            case = manifest["cases"][0]
            label = labels["cases"][case["id"]]

            self.assertEqual(first_payload["imported_count"], 1)
            self.assertEqual(second_payload["imported_count"], 0)
            self.assertEqual(second_payload["skipped"][0]["reason"], "already_in_manifest")
            self.assertEqual(len(manifest["cases"]), 1)
            self.assertEqual(case["document_class"], "diagram_training_book")
            self.assertEqual(label["route_label"], "diagram_book_reflow")
            self.assertEqual(label["label_quality"], "weak")

    def test_reference_sampler_creates_sample_case_and_preserves_source_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_root = root / "reference_inputs" / "pdf"
            input_root.mkdir(parents=True)
            source_path = input_root / "Large Book.pdf"
            original_bytes = b"source-pdf-bytes"
            source_path.write_bytes(original_bytes)
            manifest = {
                "version": 2,
                "root_dir": ".",
                "cases": [
                    {
                        "id": "large_book_pdf",
                        "document_class": "book_reference",
                        "input_type": "pdf",
                        "language": "en",
                        "target_path": "reference_inputs/pdf/Large Book.pdf",
                        "size_bytes": len(original_bytes),
                    }
                ],
            }
            labels = {"version": 1, "cases": {"large_book_pdf": {"route_label": "book_reflow", "label_quality": "weak"}}}
            (root / "reference_inputs" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "reference_inputs" / "ml_labels.json").write_text(json.dumps(labels), encoding="utf-8")

            def fake_write_sample(_source: Path, sample: Path, *, max_pages: int) -> None:
                sample.write_bytes(f"sample-{max_pages}".encode("ascii"))

            from unittest.mock import patch

            with (
                patch("scripts.sample_reference_inputs._read_pdf_page_count", return_value=320),
                patch("scripts.sample_reference_inputs._write_pdf_sample", side_effect=fake_write_sample),
            ):
                payload = sample_reference_inputs(repo_root=root, max_pages=80, min_pages=150)

            updated_manifest = json.loads((root / "reference_inputs" / "manifest.json").read_text(encoding="utf-8"))
            updated_labels = json.loads((root / "reference_inputs" / "ml_labels.json").read_text(encoding="utf-8"))
            source_case = updated_manifest["cases"][0]
            sample_case = updated_manifest["cases"][1]
            sample_label = updated_labels["cases"][sample_case["id"]]

            self.assertEqual(payload["created_count"], 1)
            self.assertEqual(source_path.read_bytes(), original_bytes)
            self.assertEqual(source_case["ml_training"], "full_corpus_only")
            self.assertEqual(source_case["sample_case_id"], sample_case["id"])
            self.assertEqual(sample_case["sample_of"], "large_book_pdf")
            self.assertEqual(sample_case["sample_pages"], 80)
            self.assertEqual(sample_case["target_path"], "reference_inputs/pdf_samples/large_book_sample_80p.pdf")
            self.assertEqual(sample_label["route_label"], "book_reflow")
            self.assertEqual(sample_label["label_quality"], "weak_sample")
            self.assertTrue((root / sample_case["target_path"]).exists())

    def test_manifest_pdf_docx_ml_cases_have_labels(self) -> None:
        manifest = json.loads(Path("reference_inputs/manifest.json").read_text(encoding="utf-8"))
        labels = json.loads(Path("reference_inputs/ml_labels.json").read_text(encoding="utf-8"))
        label_cases = labels.get("cases", {})
        missing = [
            case.get("id")
            for case in manifest.get("cases", [])
            if case.get("input_type") in {"pdf", "docx"} and case.get("id") not in label_cases
        ]

        self.assertEqual(missing, [])

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
                    "epubcheck_status": "passed_with_warnings",
                    "validation_tool": "epubcheck",
                    "warnings": ["layout review needed"],
                    "final_output_size_bytes": 1234,
                    "premium_scoring": {
                        "status": "passed_with_warnings",
                        "premium_score": 7.4,
                        "kindle_ready": True,
                        "premium_ready": False,
                        "issue_counts": {"review": 1},
                    },
                    "text_cleanup": {
                        "artifact_rate": {
                            "status": "passed",
                            "artifact_count": 2,
                            "artifact_rate_per_1000_words": 1.25,
                        }
                    },
                    "magazine_premium_quality": {
                        "toc_usefulness_ratio": 0.88,
                        "issue_toc_coverage": 0.75,
                        "nav_linear_editorial_coverage": 0.9,
                    },
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
            magazine_quality_lines = (
                root / "reports" / "ml" / "datasets" / "magazine_quality_examples.jsonl"
            ).read_text(encoding="utf-8").splitlines()

            self.assertEqual(log_payload["status"], "logged")
            self.assertTrue(log_payload["include_in_route_training"])
            self.assertEqual(export_payload["status"], "exported")
            self.assertEqual(export_payload["route_example_count"], 1)
            self.assertEqual(dataset_payload["feedback_record_count"], 1)
            self.assertEqual(dataset_payload["feedback_route_example_count"], 1)
            self.assertEqual(dataset_payload["magazine_quality_example_count"], 1)
            self.assertEqual(dataset_payload["quality_coverage"]["classes"]["magazine"]["accepted_count"], 1)
            self.assertEqual(dataset_payload["quality_coverage"]["classes"]["magazine"]["gap_to_minimum"], 9)
            self.assertFalse(dataset_payload["feedback_skipped"])
            self.assertEqual(len(route_lines), 1)
            self.assertEqual(len(feedback_lines), 1)
            self.assertEqual(len(magazine_quality_lines), 1)
            route_example = json.loads(route_lines[0])
            self.assertEqual(route_example["case_id"], "magazine_feedback")
            self.assertEqual(route_example["label"], "magazine_reflow")
            self.assertEqual(route_example["feedback_status"], "accepted")
            self.assertTrue(route_example["features"]["layout_heavy"])
            self.assertFalse(route_example["features"]["text_heavy"])
            quality_example = json.loads(magazine_quality_lines[0])
            self.assertEqual(quality_example["quality_label"], "usable")
            self.assertEqual(quality_example["final_label"], "usable")
            self.assertEqual(quality_example["feedback_status"], "accepted")
            self.assertEqual(quality_example["issue_tags"], ["layout", "toc"])
            self.assertEqual(quality_example["output_metrics"]["premium_score"], 7.4)
            self.assertEqual(quality_example["output_metrics"]["artifact_rate_per_1000_words"], 1.25)
            self.assertEqual(quality_example["output_metrics"]["toc_usefulness_ratio"], 0.88)
            self.assertEqual(quality_example["output_metrics"]["issue_toc_coverage"], 0.75)
            self.assertEqual(quality_example["output_metrics"]["article_coverage"], 0.9)
            self.assertEqual(quality_example["output_metrics"]["validation_status"], "passed_with_warnings")
            self.assertEqual(quality_example["output_metrics"]["epubcheck_status"], "passed_with_warnings")
            self.assertTrue(quality_example["source_features"]["layout_heavy"])
            self.assertTrue(quality_example["route_features"]["layout_heavy"])

    def test_magazine_quality_dataset_accepts_premium_and_legacy_good_labels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_path = root / "reports" / "premium_magazine.json"
            report_path.parent.mkdir(parents=True)
            conversion_report = {
                "source_type": "pdf",
                "source_path": "inputs/premium-magazine.pdf",
                "output_path": "output/premium-magazine.epub",
                "analysis": {
                    "profile": "magazine_reflow",
                    "confidence": 0.86,
                    "page_count": 12,
                    "text_pages": 12,
                    "scanned_pages": 0,
                    "image_pages": 9,
                    "has_toc": True,
                    "has_tables": False,
                    "has_diagrams": False,
                    "has_meaningful_images": True,
                    "estimated_columns": 2,
                    "heading_density": 0.32,
                    "font_consistency": 0.82,
                    "layout_heavy": True,
                    "text_heavy": False,
                    "route_decision": {
                        "heuristic_profile": "magazine_reflow",
                        "heuristic_confidence": 0.86,
                        "selected_profile": "magazine_reflow",
                        "mode": "shadow",
                    },
                },
                "quality_report": {
                    "validation_status": "passed",
                    "epubcheck_status": "passed",
                    "premium_scoring": {
                        "status": "passed",
                        "premium_score": 9.2,
                        "kindle_ready": True,
                        "premium_ready": True,
                    },
                    "magazine_premium_quality": {
                        "toc_usefulness_ratio": 0.96,
                        "issue_toc_coverage": 1.0,
                        "nav_linear_editorial_coverage": 0.95,
                    },
                },
                "document_summary": {"language": "en", "profile": "magazine_reflow"},
            }
            report_path.write_text(json.dumps(conversion_report), encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps({"cases": {}}), encoding="utf-8")

            append_conversion_feedback_from_report(
                report_path=report_path,
                log_path="reports/ml/feedback/conversion_feedback.jsonl",
                repo_root=root,
                case_id="legacy_good_magazine",
                feedback_status="accepted",
                quality_label="good",
                route_label="magazine_reflow",
                issue_tags=["premium-ready"],
                created_at="2026-05-11T10:00:00Z",
            )
            append_conversion_feedback_from_report(
                report_path=report_path,
                log_path="reports/ml/feedback/conversion_feedback.jsonl",
                repo_root=root,
                case_id="explicit_premium_magazine",
                feedback_status="accepted",
                quality_label="premium",
                route_label="magazine_reflow",
                issue_tags=["premium-ready"],
                created_at="2026-05-11T10:01:00Z",
            )

            payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                feedback_log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
                repo_root=root,
            )
            lines = (root / "reports" / "ml" / "datasets" / "magazine_quality_examples.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            rows = {json.loads(line)["case_id"]: json.loads(line) for line in lines}

            self.assertEqual(payload["magazine_quality_example_count"], 2)
            self.assertEqual(payload["quality_coverage"]["classes"]["magazine"]["accepted_count"], 2)
            self.assertEqual(payload["quality_coverage_status"], "insufficient_data")
            self.assertEqual(rows["legacy_good_magazine"]["quality_label"], "good")
            self.assertEqual(rows["legacy_good_magazine"]["final_label"], "premium")
            self.assertEqual(rows["explicit_premium_magazine"]["quality_label"], "premium")
            self.assertEqual(rows["explicit_premium_magazine"]["final_label"], "premium")
            self.assertEqual(rows["explicit_premium_magazine"]["output_metrics"]["premium_score"], 9.2)

    def test_builder_skips_non_ml_utf16_reports_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            reports_dir = root / "reports"
            reports_dir.mkdir(parents=True)
            (reports_dir / "validator.json").write_text(
                json.dumps({"summary": {"status": "passed"}}),
                encoding="utf-16",
            )
            (root / "manifest.json").write_text(json.dumps({"cases": []}), encoding="utf-8")
            (root / "labels.json").write_text(json.dumps({"cases": {}}), encoding="utf-8")

            payload = build_ml_datasets(
                manifest_path="manifest.json",
                labels_path="labels.json",
                reports_root="reports",
                output_dir="reports/ml/datasets",
                repo_root=root,
            )

            self.assertEqual(payload["status"], "insufficient_data")
            self.assertEqual(payload["heading_reference_example_count"], 0)


if __name__ == "__main__":
    unittest.main()
