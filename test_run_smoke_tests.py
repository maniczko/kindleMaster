from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.run_smoke_tests import (
    _build_benchmark_summary,
    _build_case_benchmark,
    _build_smoke_markdown,
    _build_smoke_summary,
    _effective_case_validation_status,
    _select_smoke_cases,
    run_smoke_tests,
)


def _minimal_epub_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "EPUB/chapter.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Smoke probe.</p></body></html>
""",
        )
    return buffer.getvalue()


def _size_policy() -> dict:
    return {
        "version": 1,
        "document_classes": {
            "micro_class": {
                "warn_bytes": 1_000_000,
                "hard_bytes": 2_000_000,
                "baseline_cases": ["micro_case"],
                "notes": "test budget",
            }
        },
        "render_budget_classes": {},
    }


class RunSmokeTestsStatusTests(unittest.TestCase):
    def test_micro_mode_falls_back_to_first_quick_case_without_manifest_marker(self):
        cases = [
            {"id": "full_case", "quick_smoke": False},
            {"id": "quick_case", "quick_smoke": True},
            {"id": "second_quick_case", "quick_smoke": True},
        ]

        selected = _select_smoke_cases(cases, mode="micro", filters=[])

        self.assertEqual([case["id"] for case in selected], ["quick_case"])

    def test_micro_mode_runs_explicit_micro_case_and_writes_reports(self):
        epub_bytes = _minimal_epub_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            for case_id in ("full_case", "quick_case", "micro_case"):
                (temp_root / f"{case_id}.epub").write_bytes(epub_bytes)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "output"
            reports_dir = temp_root / "reports"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "root_dir": ".",
                        "cases": [
                            {
                                "id": "full_case",
                                "document_class": "micro_class",
                                "input_type": "epub",
                                "language": "en",
                                "quick_smoke": False,
                                "target_path": str(temp_root / "full_case.epub"),
                            },
                            {
                                "id": "quick_case",
                                "document_class": "micro_class",
                                "input_type": "epub",
                                "language": "en",
                                "quick_smoke": True,
                                "target_path": str(temp_root / "quick_case.epub"),
                            },
                            {
                                "id": "micro_case",
                                "document_class": "micro_class",
                                "input_type": "epub",
                                "language": "en",
                                "quick_smoke": True,
                                "micro_smoke": True,
                                "target_path": str(temp_root / "micro_case.epub"),
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch("scripts.run_smoke_tests.load_size_budget_policy", return_value=_size_policy()):
                with patch(
                    "scripts.run_smoke_tests.validate_epub_path",
                    return_value={"summary": {"status": "passed"}},
                ) as validate_mock:
                    payload = run_smoke_tests(
                        manifest_path=manifest_path,
                        mode="micro",
                        output_dir=output_dir,
                        reports_dir=reports_dir,
                    )

            report_exists = (reports_dir / "smoke_micro.json").exists()
            markdown = (reports_dir / "smoke_micro.md").read_text(encoding="utf-8")

        self.assertEqual(payload["mode"], "micro")
        self.assertEqual(payload["summary"]["cases_run"], 1)
        self.assertEqual([case["id"] for case in payload["cases"]], ["micro_case"])
        self.assertTrue(report_exists)
        validate_mock.assert_called_once()
        benchmark = payload["cases"][0]["benchmark"]
        self.assertIn("input:epub", benchmark["profile_hint"])
        self.assertIn("class:micro_class", benchmark["profile_hint"])
        self.assertIn("duration_hint", benchmark)
        self.assertIn("Mode description", markdown)
        self.assertIn("Slowest cases", markdown)

    def test_full_epub_release_pass_reclassifies_source_validation_failure_as_warning(self):
        row = {
            "id": "dense_business_guide_epub",
            "validation": {"summary": {"status": "failed"}},
            "release_audit": {"decision": "pass"},
            "size_gate": {"status": "passed", "inspection": {"image_count": 0}},
            "epub_size_bytes": 1234,
        }

        summary = _build_smoke_summary([row])
        benchmark = _build_case_benchmark(row=row, elapsed_seconds=1.25)

        self.assertEqual(_effective_case_validation_status(row), "passed_with_warnings")
        self.assertEqual(summary["overall_status"], "passed_with_warnings")
        self.assertEqual(summary["failed_cases"], 0)
        self.assertEqual(summary["warning_cases"], 1)
        self.assertEqual(benchmark["validation_status"], "passed_with_warnings")
        self.assertEqual(benchmark["source_validation_status"], "failed")
        self.assertEqual(benchmark["release_audit_status"], "passed")

    def test_full_epub_release_failure_stays_failed_even_when_source_validation_passes(self):
        row = {
            "id": "dense_business_guide_epub",
            "release_strict": True,
            "validation": {"summary": {"status": "passed"}},
            "release_audit": {"decision": "fail"},
            "size_gate": {"status": "passed", "inspection": {"image_count": 0}},
            "epub_size_bytes": 1234,
        }

        summary = _build_smoke_summary([row])

        self.assertEqual(_effective_case_validation_status(row), "failed")
        self.assertEqual(summary["overall_status"], "failed")
        self.assertEqual(summary["failed_cases"], 1)

    def test_non_strict_epub_release_failure_is_accepted_when_source_validates(self):
        row = {
            "id": "scan_probe_epub",
            "release_strict": False,
            "validation": {"summary": {"status": "passed"}},
            "release_audit": {"decision": "fail"},
            "size_gate": {"status": "passed", "inspection": {"image_count": 2}},
            "epub_size_bytes": 4321,
        }

        summary = _build_smoke_summary([row])
        benchmark = _build_case_benchmark(row=row, elapsed_seconds=2.0)

        self.assertEqual(_effective_case_validation_status(row), "passed")
        self.assertEqual(summary["overall_status"], "passed")
        self.assertEqual(summary["failed_cases"], 0)
        self.assertEqual(summary["warning_cases"], 0)
        self.assertEqual(benchmark["validation_status"], "passed")
        self.assertEqual(benchmark["release_audit_status"], "failed")
        self.assertTrue(benchmark["release_audit_accepted"])
        self.assertEqual(
            benchmark["release_audit_acceptance_reason"],
            "accepted_p2_non_strict_probe_source_validation_passed",
        )

    def test_benchmark_surfaces_duration_and_profile_hints_for_slow_cases(self):
        row = {
            "id": "diagram_training_book_pdf",
            "document_class": "diagram_training_book",
            "input_type": "pdf",
            "analysis": {"profile": "diagram_book_reflow"},
            "quality_report": {"validation_status": "passed"},
            "validation": {"summary": {"status": "passed"}},
            "size_gate": {"status": "passed", "inspection": {"image_count": 12}},
            "epub_size_bytes": 2048,
        }

        benchmark = _build_case_benchmark(row=row, elapsed_seconds=45.25)
        row["benchmark"] = benchmark
        summary = _build_benchmark_summary([row], elapsed_seconds=45.25)
        markdown = _build_smoke_markdown(
            {
                "mode": "quick",
                "mode_description": "test mode",
                "summary": {
                    "cases_run": 1,
                    "overall_status": "passed",
                    "benchmark": summary,
                },
                "cases": [row],
            }
        )

        self.assertEqual(benchmark["duration_bucket"], "slow")
        self.assertIn("--case diagram_training_book_pdf", benchmark["duration_hint"])
        self.assertIn("--mode micro", benchmark["duration_hint"])
        self.assertIn("input:pdf", benchmark["profile_hint"])
        self.assertIn("class:diagram_training_book", benchmark["profile_hint"])
        self.assertIn("profile:diagram_book_reflow", benchmark["profile_hint"])
        self.assertEqual(benchmark["metrics_missing"], [])
        self.assertEqual(summary["slow_case_threshold_seconds"], 30.0)
        self.assertEqual(summary["slowest_cases"][0]["duration_bucket"], "slow")
        self.assertEqual(summary["slowest_cases"][0]["profile_hint"], benchmark["profile_hint"])
        self.assertIn("Slowest cases", markdown)
        self.assertIn("profile `input:pdf", markdown)
        self.assertIn("hint `slow:", markdown)


if __name__ == "__main__":
    unittest.main()
