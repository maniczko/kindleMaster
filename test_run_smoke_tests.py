from __future__ import annotations

import unittest

from scripts.run_smoke_tests import _build_case_benchmark, _build_smoke_summary, _effective_case_validation_status


class RunSmokeTestsStatusTests(unittest.TestCase):
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

    def test_non_strict_epub_release_failure_is_reported_as_warning_when_source_validates(self):
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

        self.assertEqual(_effective_case_validation_status(row), "passed_with_warnings")
        self.assertEqual(summary["overall_status"], "passed_with_warnings")
        self.assertEqual(summary["failed_cases"], 0)
        self.assertEqual(summary["warning_cases"], 1)
        self.assertEqual(benchmark["validation_status"], "passed_with_warnings")
        self.assertEqual(benchmark["release_audit_status"], "failed")


if __name__ == "__main__":
    unittest.main()
