from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import premium_corpus_smoke as corpus_smoke


class PremiumCorpusSmokeBatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analysis_case = corpus_smoke.CorpusCase(Path("example/analysis.pdf"), "analysis-doc", analysis_only=True)
        self.case_a = corpus_smoke.CorpusCase(Path("example/a.pdf"), "class-a")
        self.case_b = corpus_smoke.CorpusCase(Path("example/b.pdf"), "class-b")
        self.case_c = corpus_smoke.CorpusCase(Path("example/c.pdf"), "class-c")

    def test_shard_and_limit_select_a_partial_batch(self) -> None:
        with patch.object(corpus_smoke, "ANALYSIS_ONLY", [self.analysis_case]), patch.object(
            corpus_smoke,
            "CORPUS",
            [self.case_a, self.case_b, self.case_c],
        ):
            selected_cases, selection = corpus_smoke._select_corpus_batch(
                case_filters=[],
                manifest_path=None,
                shard_count=2,
                shard_index=2,
                limit=1,
            )

        self.assertEqual([case.path.name for _, case in selected_cases], ["a.pdf"])
        self.assertEqual(selection.total_cases, 4)
        self.assertEqual(selection.matching_cases, 4)
        self.assertEqual(selection.selected_cases, 1)
        self.assertEqual(selection.skipped_cases, 3)
        self.assertEqual(selection.coverage_status, "partial")
        self.assertEqual(selection.shard_count, 2)
        self.assertEqual(selection.shard_index, 2)
        self.assertEqual(selection.limit, 1)
        self.assertIn("a.pdf (class-a)", selection.selected_case_labels)

    def test_persisted_report_carries_run_scope_for_partial_batches(self) -> None:
        with patch.object(corpus_smoke, "ANALYSIS_ONLY", [self.analysis_case]), patch.object(
            corpus_smoke,
            "CORPUS",
            [self.case_a, self.case_b, self.case_c],
        ):
            _, selection = corpus_smoke._select_corpus_batch(
                case_filters=["class"],
                manifest_path=None,
                shard_count=None,
                shard_index=None,
                limit=2,
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "report.json"
            md_path = Path(temp_dir) / "report.md"
            corpus_smoke._persist_reports(
                rows=[
                    {
                        "file": "a.pdf",
                        "document_class": "class-a",
                        "mode": "convert-and-audit",
                        "grade": "pass",
                        "blockers": [],
                        "warnings": [],
                    }
                ],
                json_path=json_path,
                md_path=md_path,
                batch_selection=selection,
            )

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")

        self.assertEqual(payload["overall_status"], "passed_with_warnings")
        self.assertEqual(payload["run_scope"]["coverage_status"], "partial")
        self.assertEqual(payload["run_scope"]["selected_cases"], 2)
        self.assertIn("## Run scope", markdown)
        self.assertIn("Coverage: `partial`", markdown)
        self.assertIn("Limit: `2`", markdown)
        self.assertIn("Overall status: `passed_with_warnings`", markdown)

    def test_manifest_backed_source_reports_skipped_non_pdf_inputs(self) -> None:
        manifest_payload = {
            "version": 2,
            "root_dir": ".",
            "cases": [
                {
                    "id": "report_pdf",
                    "document_class": "document-like-report",
                    "input_type": "pdf",
                    "target_path": "reference_inputs/pdf/document_like_report.pdf",
                    "notes": "PDF fixture for corpus proof.",
                },
                {
                    "id": "simple_report_docx",
                    "document_class": "docx_structured_report",
                    "input_type": "docx",
                    "target_path": "reference_inputs/docx/simple_report.docx",
                    "notes": "DOCX fixture remains smoke-only for this gate.",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            with patch.object(corpus_smoke, "ANALYSIS_ONLY", []):
                pool = corpus_smoke._build_case_pool(manifest_path=manifest_path)
                conversion_cases, source_summary = corpus_smoke._load_manifest_conversion_cases(manifest_path=manifest_path)

        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0][0], "convert-and-audit")
        self.assertEqual(pool[0][1].document_class, "document-like-report")
        self.assertEqual(len(conversion_cases), 1)
        self.assertEqual(source_summary.source_mode, "manifest-backed")
        self.assertEqual(source_summary.eligible_manifest_cases, 1)
        self.assertEqual(source_summary.skipped_manifest_cases, 1)
        self.assertEqual(source_summary.skipped_case_labels, ("simple_report_docx (docx)",))

    def test_manifest_backed_source_preserves_release_strict_flag(self) -> None:
        manifest_payload = {
            "version": 2,
            "root_dir": ".",
            "cases": [
                {
                    "id": "ocr_probe_pdf",
                    "document_class": "ocr_probe",
                    "input_type": "pdf",
                    "target_path": "reference_inputs/pdf/ocr_probe.pdf",
                    "notes": "Tiny OCR probe for the fastest conversion smoke.",
                    "release_strict": False,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            conversion_cases, source_summary = corpus_smoke._load_manifest_conversion_cases(manifest_path=manifest_path)

        self.assertEqual(source_summary.source_mode, "manifest-backed")
        self.assertEqual(len(conversion_cases), 1)
        self.assertFalse(conversion_cases[0].release_strict)

    def test_run_premium_corpus_smoke_marks_partial_proof_and_persists_source_summary(self) -> None:
        manifest_payload = {
            "version": 2,
            "root_dir": ".",
            "cases": [
                {
                    "id": "report_pdf_a",
                    "document_class": "document-like-report",
                    "input_type": "pdf",
                    "target_path": "reference_inputs/pdf/document_like_report.pdf",
                    "notes": "PDF fixture A.",
                },
                {
                    "id": "report_pdf_b",
                    "document_class": "ocr_stress_scan",
                    "input_type": "pdf",
                    "target_path": "reference_inputs/pdf/ocr_stress_scan.pdf",
                    "notes": "PDF fixture B.",
                },
                {
                    "id": "simple_report_docx",
                    "document_class": "docx_structured_report",
                    "input_type": "docx",
                    "target_path": "reference_inputs/docx/simple_report.docx",
                    "notes": "DOCX fixture remains smoke-only for this gate.",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            json_path = temp_root / "premium.json"
            md_path = temp_root / "premium.md"
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

            with patch.object(corpus_smoke, "ANALYSIS_ONLY", []), patch.object(
                corpus_smoke,
                "_run_conversion_case",
                side_effect=lambda case, **_kwargs: {
                    "file": case.path.name,
                    "document_class": case.document_class,
                    "mode": "convert-and-audit",
                    "grade": "pass",
                    "blockers": [],
                    "warnings": [],
                },
            ):
                payload = corpus_smoke.run_premium_corpus_smoke(
                    manifest_path=manifest_path,
                    output_json=json_path,
                    output_md=md_path,
                    limit=1,
                )

            persisted = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = md_path.read_text(encoding="utf-8")

        self.assertEqual(payload["overall_status"], "passed_with_warnings")
        self.assertEqual(payload["run_scope"]["coverage_status"], "partial")
        self.assertEqual(payload["corpus_source"]["source_mode"], "manifest-backed")
        self.assertEqual(payload["corpus_source"]["eligible_manifest_cases"], 2)
        self.assertEqual(payload["corpus_source"]["skipped_manifest_cases"], 1)
        self.assertEqual(persisted["overall_status"], "passed_with_warnings")
        self.assertEqual(persisted["corpus_source"]["skipped_case_labels"], ["simple_report_docx (docx)"])
        self.assertIn("Corpus source: `manifest-backed`", markdown)
        self.assertIn("Skipped inputs: `simple_report_docx (docx)`", markdown)


if __name__ == "__main__":
    unittest.main()
