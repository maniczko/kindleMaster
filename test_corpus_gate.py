from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from scripts.run_corpus_gate import run_corpus_gate


class CorpusGateTests(unittest.TestCase):
    def test_corpus_gate_merges_full_smoke_and_premium_reports(self) -> None:
        smoke_payload = {
            "summary": {
                "cases_run": 4,
                "overall_status": "passed_with_warnings",
            }
        }
        premium_payload = {
            "overall": {
                "converted_case_count": 3,
                "analysis_only_case_count": 1,
                "grade_counts": {"pass_with_review": 1, "pass": 2},
                "blocker_counts": {},
                "warning_counts": {"heading_manual_review": 1},
                "overall_status": "passed_with_warnings",
            }
        }
        smoke_payload["summary"]["benchmark"] = {
            "classes": ["ocr_probe", "docx_structured_report"],
            "slowest_cases": [
                {
                    "id": "ocr_probe_pdf",
                    "document_class": "ocr_probe",
                    "elapsed_seconds": 0.25,
                    "validation_status": "passed",
                    "fallback_mode": "premium",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir) / "reports"
            output_root = Path(temp_dir) / "output"
            with patch("scripts.run_corpus_gate.run_smoke_tests", return_value=smoke_payload) as smoke_mock:
                with patch("scripts.run_corpus_gate.run_premium_corpus_smoke", return_value=premium_payload) as premium_mock:
                    payload = run_corpus_gate(
                        manifest_path="reference_inputs/manifest.json",
                        output_root=output_root,
                        reports_root=reports_root,
                    )

            self.assertEqual(payload["overall_status"], "passed_with_warnings")
            self.assertEqual(payload["proof_profile"], "standard")
            self.assertTrue((reports_root / "corpus_gate.json").exists())
            self.assertTrue((reports_root / "corpus_gate.md").exists())

            persisted = json.loads((reports_root / "corpus_gate.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["overall_status"], "passed_with_warnings")
            self.assertEqual(persisted["artifacts"]["smoke_json"], str(reports_root / "smoke" / "smoke_full.json"))
            self.assertEqual(persisted["artifacts"]["premium_json"], str(reports_root / "premium_corpus_smoke_report.json"))
            self.assertIn("benchmark", persisted)
            self.assertEqual(persisted["benchmark"]["class_count"], 2)

        smoke_mock.assert_called_once()
        premium_mock.assert_called_once()

    def test_corpus_gate_fails_when_any_underlying_gate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = None
            with patch(
                "scripts.run_corpus_gate.run_smoke_tests",
                return_value={"summary": {"overall_status": "failed", "cases_run": 2}},
            ):
                with patch(
                    "scripts.run_corpus_gate.run_premium_corpus_smoke",
                    return_value={"overall": {"overall_status": "passed", "converted_case_count": 2, "analysis_only_case_count": 0, "grade_counts": {}, "blocker_counts": {}, "warning_counts": {}}},
                ):
                    payload = run_corpus_gate(
                        output_root=Path(temp_dir) / "output",
                        reports_root=Path(temp_dir) / "reports",
                    )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["overall_status"], "failed")

    def test_corpus_gate_full_profile_disables_standard_case_filters(self) -> None:
        smoke_payload = {"summary": {"overall_status": "passed", "cases_run": 6}}
        premium_payload = {
            "overall_status": "passed",
            "overall": {
                "overall_status": "passed",
                "converted_case_count": 4,
                "analysis_only_case_count": 2,
                "grade_counts": {"pass": 4},
                "blocker_counts": {},
                "warning_counts": {},
                "proof_scope": "complete",
                "source_mode": "manifest-backed",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir) / "reports"
            output_root = Path(temp_dir) / "output"
            with patch("scripts.run_corpus_gate.run_smoke_tests", return_value=smoke_payload) as smoke_mock:
                with patch("scripts.run_corpus_gate.run_premium_corpus_smoke", return_value=premium_payload) as premium_mock:
                    payload = run_corpus_gate(
                        output_root=output_root,
                        reports_root=reports_root,
                        proof_profile="full",
                    )

        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["proof_profile"], "full")
        self.assertIsNone(smoke_mock.call_args.kwargs["case_filters"])
        self.assertIsNone(premium_mock.call_args.kwargs["case_filters"])

    def test_corpus_gate_persists_stable_derived_status_evidence(self) -> None:
        smoke_payload = {
            "summary": {
                "cases_run": 3,
                "overall_status": "passed",
            }
        }
        premium_payload = {
            "overall_status": "passed_with_warnings",
            "overall": {
                "converted_case_count": 2,
                "analysis_only_case_count": 1,
                "grade_counts": {"pass": 1, "pass_with_review": 1},
                "blocker_counts": {},
                "warning_counts": {"partial_proof": 1},
                "overall_status": "passed_with_warnings",
                "proof_scope": "partial",
                "source_mode": "manifest-backed",
            },
            "corpus_source": {
                "source_mode": "manifest-backed",
                "eligible_manifest_cases": 6,
                "skipped_manifest_cases": 2,
                "skipped_case_labels": ["simple_report_docx (docx)", "scan_probe_epub (epub)"],
                "fallback_used": False,
                "fallback_reason": "",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir) / "reports"
            output_root = Path(temp_dir) / "output"
            with patch("scripts.run_corpus_gate.run_smoke_tests", return_value=smoke_payload):
                with patch("scripts.run_corpus_gate.run_premium_corpus_smoke", return_value=premium_payload):
                    payload = run_corpus_gate(
                        output_root=output_root,
                        reports_root=reports_root,
                    )

            persisted = json.loads((reports_root / "corpus_gate.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["overall_status"], "passed_with_warnings")
        self.assertEqual(persisted["premium_corpus"]["overall"]["proof_scope"], "partial")
        self.assertEqual(persisted["premium_corpus"]["overall"]["source_mode"], "manifest-backed")
        self.assertEqual(persisted["premium_corpus"]["corpus_source"]["eligible_manifest_cases"], 6)
        self.assertEqual(
            persisted["premium_corpus"]["corpus_source"]["skipped_case_labels"],
            ["simple_report_docx (docx)", "scan_probe_epub (epub)"],
        )

    def test_corpus_gate_markdown_surfaces_derived_summary_for_status_readers(self) -> None:
        smoke_payload = {
            "summary": {
                "cases_run": 5,
                "overall_status": "passed",
            }
        }
        premium_payload = {
            "overall_status": "passed_with_warnings",
            "overall": {
                "converted_case_count": 4,
                "analysis_only_case_count": 1,
                "grade_counts": {"pass": 3, "pass_with_review": 1},
                "blocker_counts": {"metadata_placeholder": 1},
                "warning_counts": {"partial_proof": 1},
                "overall_status": "passed_with_warnings",
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            reports_root = Path(temp_dir) / "reports"
            output_root = Path(temp_dir) / "output"
            with patch("scripts.run_corpus_gate.run_smoke_tests", return_value=smoke_payload):
                with patch("scripts.run_corpus_gate.run_premium_corpus_smoke", return_value=premium_payload):
                    run_corpus_gate(
                        output_root=output_root,
                        reports_root=reports_root,
                    )

            markdown = (reports_root / "corpus_gate.md").read_text(encoding="utf-8")

        self.assertIn("# KindleMaster Corpus Gate", markdown)
        self.assertIn("Overall status: `passed_with_warnings`", markdown)
        self.assertIn("Proof profile: `standard`", markdown)
        self.assertIn("Smoke cases run: `5`", markdown)
        self.assertIn("Premium converted cases: `4`", markdown)
        self.assertIn('Premium blockers: `{"metadata_placeholder": 1}`', markdown)
        self.assertIn("## Benchmark", markdown)
        self.assertIn(str(reports_root / "smoke" / "smoke_full.json"), markdown)
        self.assertIn(str(reports_root / "premium_corpus_smoke_report.json"), markdown)

    def test_kindlemaster_corpus_command_routes_to_standard_gate(self) -> None:
        payload = {
            "overall_status": "passed",
            "smoke": {"summary": {"overall_status": "passed", "cases_run": 2}},
            "premium_corpus": {"overall": {"overall_status": "passed", "converted_case_count": 2}},
            "artifacts": {},
        }

        with patch("scripts.run_corpus_gate.run_corpus_gate", return_value=payload) as gate_mock, patch.object(
            kindlemaster,
            "_print_json",
        ) as print_mock, patch.object(
            sys,
            "argv",
            [
                "kindlemaster.py",
                "corpus",
                "--manifest",
                "reference_inputs/manifest.json",
                "--output-root",
                "output/corpus",
                "--reports-root",
                "reports/corpus",
                "--smoke-case",
                "ocr",
                "--premium-case",
                "report",
            ],
        ):
            exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        gate_mock.assert_called_once_with(
            manifest_path="reference_inputs/manifest.json",
            output_root="output/corpus",
            reports_root="reports/corpus",
            proof_profile="standard",
            smoke_case_filters=["ocr"],
            premium_case_filters=["report"],
        )
        print_mock.assert_called_once_with(payload)

    def test_kindlemaster_corpus_command_can_request_full_proof_profile(self) -> None:
        payload = {
            "overall_status": "passed",
            "smoke": {"summary": {"overall_status": "passed", "cases_run": 4}},
            "premium_corpus": {"overall": {"overall_status": "passed", "converted_case_count": 4}},
            "artifacts": {},
        }

        with patch("scripts.run_corpus_gate.run_corpus_gate", return_value=payload) as gate_mock, patch.object(
            kindlemaster,
            "_print_json",
        ), patch.object(
            sys,
            "argv",
            [
                "kindlemaster.py",
                "corpus",
                "--proof-profile",
                "full",
            ],
        ):
            exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        gate_mock.assert_called_once_with(
            manifest_path="reference_inputs/manifest.json",
            output_root="output/corpus",
            reports_root="reports/corpus",
            proof_profile="full",
            smoke_case_filters=[],
            premium_case_filters=[],
        )


if __name__ == "__main__":
    unittest.main()
