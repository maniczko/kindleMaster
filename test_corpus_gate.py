from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import kindlemaster
from scripts.run_corpus_gate import (
    CI_PREMIUM_FILTERS,
    STANDARD_PREMIUM_FILTERS,
    _build_gate_output_assertions,
    _fen_min_profile_count_for_proof_profile,
    run_corpus_gate,
)


class CorpusGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fen_corpus_payload = {
            "status": "passed",
            "evaluated_case_count": 1,
            "font_board_candidate_profile_count": 0,
            "font_board_candidate_status": "not_configured",
            "font_board_candidate_failed_count": 0,
            "missing_profile_count": 0,
            "default_min_seed_label_count": 20,
            "overall_exact_fen_accuracy": 0.925,
            "total_false_positive_count": 0,
            "reasons": [],
            "cases": [
                {
                    "label_validation": {
                        "valid_label_count": 40,
                    }
                }
            ],
        }
        self._fen_corpus_patcher = patch(
            "scripts.run_corpus_gate.evaluate_chess_fen_corpus",
            return_value=self.fen_corpus_payload,
        )
        self.fen_corpus_mock = self._fen_corpus_patcher.start()
        self.addCleanup(self._fen_corpus_patcher.stop)

    def _build_epub_bytes(self, *, with_image: bool = False) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "EPUB/content.opf",
                """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Corpus fixture</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
</package>
""",
            )
            archive.writestr(
                "EPUB/nav.xhtml",
                """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml#intro">Intro</a></li></ol></nav></body>
</html>
""",
            )
            image_markup = '<img src="images/figure.png" alt="figure"/>' if with_image else ""
            archive.writestr(
                "EPUB/chapter.xhtml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="intro">Intro</h1><p>Fixture.</p>{image_markup}</body></html>
""",
            )
            if with_image:
                archive.writestr("EPUB/images/figure.png", b"\x89PNG\r\n\x1a\n")
        return output.getvalue()

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
            self.assertEqual(persisted["artifacts"]["fen_corpus_json"], str(reports_root / "fen_corpus_90.json"))
            self.assertEqual(persisted["fen_corpus"]["status"], "passed")
            self.assertEqual(persisted["fen_min_profile_count"], 2)
            self.assertEqual(persisted["fen_min_profile_count_source"], "proof_profile_default")
            self.assertIn("benchmark", persisted)
            self.assertEqual(persisted["benchmark"]["class_count"], 2)

        smoke_mock.assert_called_once()
        premium_mock.assert_called_once()
        self.fen_corpus_mock.assert_called_with(
            "reference_inputs/manifest.json",
            min_confidence=0.835,
            default_min_exact_accuracy=0.90,
            default_min_seed_label_count=20,
            min_profile_count=2,
            output_path=reports_root / "fen_corpus_90.json",
        )

    def test_fen_profile_count_defaults_are_release_grade_except_bounded_ci(self) -> None:
        self.assertEqual(_fen_min_profile_count_for_proof_profile("standard", None), 2)
        self.assertEqual(_fen_min_profile_count_for_proof_profile("full", None), 2)
        self.assertEqual(_fen_min_profile_count_for_proof_profile("ci", None), 1)
        self.assertEqual(_fen_min_profile_count_for_proof_profile("standard", 1), 1)
        self.assertEqual(_fen_min_profile_count_for_proof_profile("standard", 3), 3)

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

    def test_corpus_gate_fails_when_fen_corpus_gate_fails(self) -> None:
        self.fen_corpus_mock.return_value = {
            "status": "failed",
            "evaluated_case_count": 1,
            "font_board_candidate_profile_count": 1,
            "font_board_candidate_status": "review_ready",
            "font_board_candidate_failed_count": 0,
            "missing_profile_count": 1,
            "overall_exact_fen_accuracy": 0.925,
            "total_false_positive_count": 0,
            "reasons": ["manifest has 1 chess FEN profile(s), below required minimum 2"],
            "next_required_actions": ["add 1 real scanned chess FEN profile(s) to reach min_profile_count=2"],
            "cases": [],
        }
        smoke_payload = {"summary": {"overall_status": "passed", "cases_run": 2}}
        premium_payload = {
            "overall": {
                "overall_status": "passed",
                "converted_case_count": 2,
                "analysis_only_case_count": 0,
                "grade_counts": {"pass": 2},
                "blocker_counts": {},
                "warning_counts": {},
            }
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("scripts.run_corpus_gate.run_smoke_tests", return_value=smoke_payload):
                with patch("scripts.run_corpus_gate.run_premium_corpus_smoke", return_value=premium_payload):
                    payload = run_corpus_gate(
                        output_root=Path(temp_dir) / "output",
                        reports_root=Path(temp_dir) / "reports",
                        fen_min_profile_count=2,
                    )
                    markdown = (Path(temp_dir) / "reports" / "corpus_gate.md").read_text(encoding="utf-8")

        self.assertEqual(payload["overall_status"], "failed")
        self.assertEqual(payload["fen_corpus"]["missing_profile_count"], 1)
        self.assertIn("FEN next actions", markdown)
        self.assertIn("add 1 real scanned chess FEN profile", markdown)
        self.assertIn("FEN font-board candidate profiles", markdown)
        self.assertIn("FEN font-board candidate status", markdown)
        self.assertIn("review_ready", markdown)
        self.fen_corpus_mock.assert_called_with(
            "reference_inputs/manifest.json",
            min_confidence=0.835,
            default_min_exact_accuracy=0.90,
            default_min_seed_label_count=20,
            min_profile_count=2,
            output_path=Path(temp_dir) / "reports" / "fen_corpus_90.json",
        )

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

    def test_standard_premium_filters_raise_the_converted_slice_beyond_one_case(self) -> None:
        self.assertIn("document-like-report", STANDARD_PREMIUM_FILTERS)
        self.assertIn("ocr_stress_scan", STANDARD_PREMIUM_FILTERS)
        self.assertIn("magazine_layout", STANDARD_PREMIUM_FILTERS)
        self.assertIn("diagram_training_book", STANDARD_PREMIUM_FILTERS)
        self.assertNotIn("large-diagram-corpus", STANDARD_PREMIUM_FILTERS)
        self.assertGreaterEqual(len(STANDARD_PREMIUM_FILTERS), 4)

    def test_ci_premium_filters_keep_release_runner_toolchain_bounded(self) -> None:
        self.assertEqual(CI_PREMIUM_FILTERS, ["document-like-report"])

    def test_gate_output_assertions_cover_focused_routes_from_real_epub_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            docx_epub = temp_root / "docx.epub"
            scan_epub = temp_root / "scan.epub"
            docx_epub.write_bytes(self._build_epub_bytes())
            scan_epub.write_bytes(self._build_epub_bytes(with_image=True))

            smoke_payload = {
                "cases": [
                    {
                        "id": "simple_report_docx",
                        "document_class": "docx_structured_report",
                        "input_type": "docx",
                        "output_epub": str(docx_epub),
                        "validation": {"summary": {"status": "passed"}},
                    },
                    {
                        "id": "scan_probe_epub",
                        "document_class": "scan_probe",
                        "input_type": "epub",
                        "path": str(scan_epub),
                        "validation": {"summary": {"status": "passed"}},
                    },
                ]
            }
            shared_stats = {
                "xhtml_count": 2,
                "image_count": 1,
                "nav_entries": 2,
                "package_language": "en",
                "heading_counts": {"h1": 1},
            }
            premium_payload = {
                "cases": [
                    {
                        "case_id": "magazine_layout_pdf",
                        "document_class": "magazine_layout",
                        "input_type": "pdf",
                        "mode": "convert-and-audit",
                        "quality": {"validation_status": "passed"},
                        "post_heading_epub_stats": shared_stats,
                    },
                    {
                        "case_id": "document_like_report_pdf",
                        "document_class": "document-like-report",
                        "input_type": "pdf",
                        "mode": "convert-and-audit",
                        "quality": {"validation_status": "passed"},
                        "post_heading_epub_stats": shared_stats,
                    },
                    {
                        "case_id": "diagram_training_book_pdf",
                        "document_class": "diagram_training_book",
                        "input_type": "pdf",
                        "mode": "convert-and-audit",
                        "quality": {"validation_status": "passed"},
                        "post_heading_epub_stats": shared_stats,
                    },
                ]
            }

            output_assertions = _build_gate_output_assertions(smoke=smoke_payload, premium=premium_payload)

        self.assertEqual(output_assertions["failed_routes"], [])
        self.assertEqual(output_assertions["skipped_routes"], [])
        self.assertEqual(output_assertions["covered_route_count"], 5)
        for route_payload in output_assertions["focus_routes"].values():
            self.assertIn(route_payload["status"], {"covered", "covered_with_warnings"})

    def test_gate_output_assertions_surface_pass_with_review_route_evidence(self) -> None:
        premium_payload = {
            "cases": [
                {
                    "case_id": "diagram_training_book_pdf",
                    "document_class": "diagram_training_book",
                    "input_type": "pdf",
                    "mode": "convert-and-audit",
                    "grade": "pass_with_review",
                    "quality": {"validation_status": "passed"},
                    "post_heading_epub_stats": {
                        "xhtml_count": 3,
                        "image_count": 4,
                        "nav_entries": 3,
                        "package_language": "en",
                        "heading_counts": {"h1": 1},
                    },
                }
            ]
        }

        output_assertions = _build_gate_output_assertions(smoke={"cases": []}, premium=premium_payload)

        self.assertEqual(output_assertions["focus_routes"]["diagram_chess"]["status"], "covered_with_warnings")
        self.assertEqual(output_assertions["failed_routes"], [])

    def test_gate_output_assertions_use_post_heading_recovered_validation(self) -> None:
        premium_payload = {
            "cases": [
                {
                    "case_id": "magazine_layout_pdf",
                    "document_class": "magazine_layout",
                    "input_type": "pdf",
                    "mode": "convert-and-audit",
                    "grade": "pass_with_review",
                    "quality": {"validation_status": "failed"},
                    "heading_repair": {"status": "applied", "epubcheck_status": "passed"},
                    "output_assertions": [
                        {
                            "id": "content_xhtml_present",
                            "route": "base",
                            "status": "passed",
                            "severity": "blocker",
                            "detail": "xhtml_count=36",
                        },
                        {
                            "id": "validation_not_failed",
                            "route": "base",
                            "status": "passed",
                            "severity": "blocker",
                            "detail": "validation_status=passed",
                        },
                    ],
                }
            ]
        }

        output_assertions = _build_gate_output_assertions(smoke={"cases": []}, premium=premium_payload)

        route = output_assertions["focus_routes"]["magazine_layout_heavy"]
        self.assertEqual(route["status"], "covered_with_warnings")
        self.assertEqual(route["cases"][0]["validation_status"], "passed")
        self.assertEqual(route["cases"][0]["raw_validation_status"], "failed")
        self.assertEqual(output_assertions["failed_routes"], [])

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

    def test_standard_gate_passes_when_only_accepted_p2_warnings_remain(self) -> None:
        smoke_payload = {
            "summary": {
                "cases_run": 4,
                "overall_status": "passed",
            }
        }
        premium_payload = {
            "overall_status": "passed_with_warnings",
            "overall": {
                "converted_case_count": 4,
                "analysis_only_case_count": 0,
                "grade_counts": {"pass": 4},
                "blocker_counts": {},
                "warning_counts": {},
                "accepted_warning_counts": {"heading_manual_review": 2},
                "overall_status": "passed_with_warnings",
                "proof_scope": "partial",
                "source_mode": "manifest-backed",
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

        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["effective_premium_status"], "passed")

    def test_standard_gate_passes_for_clean_partial_premium_scope_covered_by_smoke(self) -> None:
        smoke_payload = {
            "summary": {
                "cases_run": 4,
                "overall_status": "passed",
            }
        }
        premium_payload = {
            "overall_status": "passed_with_warnings",
            "overall": {
                "converted_case_count": 4,
                "analysis_only_case_count": 0,
                "grade_counts": {"pass": 4},
                "blocker_counts": {},
                "warning_counts": {},
                "accepted_warning_counts": {},
                "overall_status": "passed_with_warnings",
                "proof_scope": "partial",
                "source_mode": "manifest-backed",
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

        self.assertEqual(payload["overall_status"], "passed")
        self.assertEqual(payload["effective_premium_status"], "passed")

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
        self.assertIn("FEN corpus status: `passed`", markdown)
        self.assertIn("FEN min profiles required: `2`", markdown)
        self.assertIn("FEN min seed labels/profile: `20`", markdown)
        self.assertIn("FEN valid seed labels: `40`", markdown)
        self.assertIn(str(reports_root / "fen_corpus_90.json"), markdown)

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
            fen_min_profile_count=None,
            fen_min_seed_label_count=20,
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
            fen_min_profile_count=None,
            fen_min_seed_label_count=20,
        )

    def test_kindlemaster_corpus_command_can_request_ci_proof_profile(self) -> None:
        payload = {
            "overall_status": "passed_with_warnings",
            "smoke": {"summary": {"overall_status": "passed", "cases_run": 4}},
            "premium_corpus": {"overall": {"overall_status": "passed", "converted_case_count": 1}},
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
                "ci",
            ],
        ):
            exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        gate_mock.assert_called_once_with(
            manifest_path="reference_inputs/manifest.json",
            output_root="output/corpus",
            reports_root="reports/corpus",
            proof_profile="ci",
            smoke_case_filters=[],
            premium_case_filters=[],
            fen_min_profile_count=None,
            fen_min_seed_label_count=20,
        )

    def test_kindlemaster_corpus_command_can_request_fen_profile_count(self) -> None:
        payload = {
            "overall_status": "failed",
            "smoke": {"summary": {"overall_status": "passed", "cases_run": 4}},
            "premium_corpus": {"overall": {"overall_status": "passed", "converted_case_count": 4}},
            "fen_corpus": {"status": "failed", "missing_profile_count": 1},
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
                "--fen-min-profile-count",
                "2",
            ],
        ):
            exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 1)
        gate_mock.assert_called_once_with(
            manifest_path="reference_inputs/manifest.json",
            output_root="output/corpus",
            reports_root="reports/corpus",
            proof_profile="standard",
            smoke_case_filters=[],
            premium_case_filters=[],
            fen_min_profile_count=2,
            fen_min_seed_label_count=20,
        )

    def test_kindlemaster_corpus_command_can_request_fen_seed_label_count(self) -> None:
        payload = {
            "overall_status": "failed",
            "smoke": {"summary": {"overall_status": "passed", "cases_run": 4}},
            "premium_corpus": {"overall": {"overall_status": "passed", "converted_case_count": 4}},
            "fen_corpus": {"status": "failed", "failed_case_count": 1},
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
                "--fen-min-seed-label-count",
                "30",
            ],
        ):
            exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 1)
        gate_mock.assert_called_once_with(
            manifest_path="reference_inputs/manifest.json",
            output_root="output/corpus",
            reports_root="reports/corpus",
            proof_profile="standard",
            smoke_case_filters=[],
            premium_case_filters=[],
            fen_min_profile_count=None,
            fen_min_seed_label_count=30,
        )


if __name__ == "__main__":
    unittest.main()
