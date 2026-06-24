from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kindlemaster
import premium_tools
from kindlemaster import (
    BROWSER_TESTS,
    CORPUS_TESTS,
    DISCOVER_ONLY_TESTS,
    QUALITY_CRITICAL_TESTS,
    QUICK_TESTS,
    RELEASE_TESTS,
    RUNTIME_TESTS,
    SUITE_REGISTRY,
    TEST_FILE_PATTERN,
    _json_text,
    _quality_critical_coverage_payload,
    _run_bootstrap,
    _run_convert,
    _run_tests,
)


class KindleMasterEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        premium_tools.clear_toolchain_cache()

    def tearDown(self) -> None:
        premium_tools.clear_toolchain_cache()

    def test_json_text_serializes_objects_and_preserves_unicode(self) -> None:
        class CustomPayload:
            def __str__(self) -> str:
                return "custom-object"

        payload = {
            "analysis": CustomPayload(),
            "message": "• poprawa jakości",
        }

        rendered = _json_text(payload)

        self.assertIn('"custom-object"', rendered)
        self.assertIn("• poprawa jakości", rendered)

    def test_json_text_decodes_utf8_bytes_without_python_bytes_repr(self) -> None:
        rendered = _json_text(
            {
                "extra_artifacts": [
                    {
                        "filename": "chess_games.html",
                        "data": "Kopiuj pełną notację".encode("utf-8"),
                    }
                ]
            }
        )

        self.assertIn("Kopiuj pełną notację", rendered)
        self.assertNotIn("\\xc5", rendered)
        self.assertNotIn("b'", rendered)

    def test_run_convert_writes_json_report_for_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "probe.pdf"
            input_path.write_bytes(b"%PDF-1.4\n% KindleMaster test probe\n")
            output_path = Path(temp_dir) / "probe.epub"
            report_path = Path(temp_dir) / "probe.json"
            fake_outcome = SimpleNamespace(
                epub_bytes=b"epub-bytes",
                result={
                    "source_type": "pdf",
                    "quality_report": {"validation_status": "passed"},
                    "document_summary": {"title": "Probe"},
                },
                heading_repair_report={"status": "skipped"},
            )
            with patch("app_runtime_services.run_document_conversion", return_value=fake_outcome) as conversion_mock:
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = _run_convert(
                        input_path=str(input_path),
                        output_path=str(output_path),
                        language="pl",
                        profile="auto-premium",
                        heading_repair=False,
                        domain_dictionary="docs/domain-dictionary-example.json",
                        report_json=str(report_path),
                    )
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(report_path.exists())
            self.assertEqual(output_path.read_bytes(), b"epub-bytes")
            conversion_mock.assert_called_once()
            request = conversion_mock.call_args.args[0]
            self.assertEqual(request.source_type, "pdf")
            self.assertEqual(request.language, "pl")
            self.assertEqual(request.route_model_mode, "shadow")
            self.assertEqual(request.quality_gate_mode, "draft")
            self.assertEqual(request.text_cleanup_domain_dictionary_path, "docs/domain-dictionary-example.json")
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_type"], "pdf")
            self.assertIn("quality_report", payload)
            self.assertIn("document_summary", payload)

    def test_quick_suite_stays_free_of_browser_runtime_dependencies(self) -> None:
        self.assertNotIn("test_browser_polling_e2e.py", QUICK_TESTS)
        self.assertNotIn("test_browser_privacy_diagnostics.py", QUICK_TESTS)
        self.assertNotIn("test_runtime_waitress_smoke.py", QUICK_TESTS)
        self.assertNotIn("test_skill_contracts.py", RELEASE_TESTS)
        self.assertNotIn("test_premium_corpus_smoke.py", RELEASE_TESTS)
        self.assertIn("test_browser_polling_runtime_harness.py", BROWSER_TESTS)
        self.assertIn("test_react_shell_browser_smoke.py", BROWSER_TESTS)
        self.assertIn("test_browser_polling_e2e.py", RUNTIME_TESTS)
        self.assertIn("test_browser_privacy_diagnostics.py", RUNTIME_TESTS)
        self.assertIn("test_runtime_waitress_smoke.py", RUNTIME_TESTS)
        self.assertIn("test_corpus_gate.py", CORPUS_TESTS)
        self.assertIn("test_premium_corpus_smoke.py", CORPUS_TESTS)

    def test_suite_registry_accounts_for_all_root_test_files(self) -> None:
        repo_root = Path(__file__).resolve().parent
        discovered_tests = {path.name for path in repo_root.glob(TEST_FILE_PATTERN) if path.is_file()}
        explicit_tests: set[str] = set()

        self.assertEqual(set(SUITE_REGISTRY), {"quick", "release", "corpus", "browser", "runtime", "quality-critical"})
        for suite_name, suite_tests in SUITE_REGISTRY.items():
            self.assertEqual(len(suite_tests), len(set(suite_tests)), suite_name)
            explicit_tests.update(suite_tests)

        discover_only_tests = set(DISCOVER_ONLY_TESTS)
        accounted_tests = explicit_tests | discover_only_tests

        self.assertFalse(discover_only_tests & explicit_tests)
        self.assertEqual(discovered_tests - accounted_tests, set())
        self.assertEqual(accounted_tests - discovered_tests, set())

    def test_quality_critical_suite_tracks_conversion_coverage_hotspots(self) -> None:
        self.assertIn("test_converter_text_cleanup.py", QUALITY_CRITICAL_TESTS)
        self.assertIn("test_semantic_epub_cleanup.py", QUALITY_CRITICAL_TESTS)
        self.assertIn("test_app_runtime_services.py", QUALITY_CRITICAL_TESTS)
        self.assertIn("test_epub_quality_recovery.py", QUALITY_CRITICAL_TESTS)

        coverage_json = {
            "totals": {"percent_covered": 72.31},
            "files": {
                "converter.py": {"summary": {"percent_covered": 43.01}},
                "text_normalization.py": {"summary": {"percent_covered": 65.5}},
                "kindle_semantic_cleanup.py": {"summary": {"percent_covered": 75.3}},
            },
        }

        payload = _quality_critical_coverage_payload(
            coverage_json,
            total_threshold=70.0,
            converter_threshold=40.0,
            text_normalization_threshold=65.0,
            semantic_cleanup_threshold=70.0,
        )

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["missing_actions"], [])
        self.assertEqual(payload["thresholds"]["total"], 70.0)
        self.assertEqual(payload["files"]["converter.py"]["coverage"], 43.01)
        self.assertEqual(payload["files"]["text_normalization.py"]["coverage"], 65.5)
        self.assertEqual(payload["files"]["kindle_semantic_cleanup.py"]["coverage"], 75.3)

        failed = _quality_critical_coverage_payload(
            coverage_json,
            total_threshold=75.0,
            converter_threshold=45.0,
            text_normalization_threshold=66.0,
            semantic_cleanup_threshold=80.0,
        )
        self.assertEqual(failed["status"], "failed")
        self.assertIn("total coverage 72.31% is below 75.0%", failed["missing_actions"])
        self.assertIn("converter.py coverage 43.01% is below 45.0%", failed["missing_actions"])
        self.assertIn("text_normalization.py coverage 65.5% is below 66.0%", failed["missing_actions"])
        self.assertIn("kindle_semantic_cleanup.py coverage 75.3% is below 80.0%", failed["missing_actions"])

    def test_browser_harness_file_does_not_import_playwright(self) -> None:
        harness_source = Path("test_browser_polling_runtime_harness.py").read_text(encoding="utf-8")
        self.assertNotIn("playwright.sync_api", harness_source)

    def test_detect_toolchain_formalizes_bootstrap_profiles_and_optional_surfaces(self) -> None:
        module_presence = {
            "flask": True,
            "fitz": True,
            "ebooklib": True,
            "PIL": True,
            "bs4": True,
            "lxml": True,
            "docx": True,
            "pdfplumber": True,
            "wordfreq": True,
            "pyphen": True,
            "rfc3986": True,
            "tldextract": True,
            "pytest": True,
            "coverage": True,
            "playwright": True,
            "waitress": True,
            "sklearn": True,
            "chess": True,
            "chess.pgn": True,
            "pytesseract": True,
            "cv2": True,
            "ocrmypdf": False,
        }

        def fake_module_available(name: str) -> bool:
            return module_presence.get(name, False)

        with tempfile.TemporaryDirectory() as temp_dir:
            isolated_env = {
                "KINDLEMASTER_USER_PROFILE_PATH": str(Path(temp_dir) / "profile.json"),
                "KINDLEMASTER_EMAIL_DELIVERY": "0",
                "KINDLEMASTER_SMTP_HOST": "",
                "KINDLEMASTER_SMTP_USERNAME": "",
                "KINDLEMASTER_SMTP_PASSWORD": "",
                "KINDLEMASTER_SMTP_FROM": "",
            }
            with (
                patch.dict(os.environ, isolated_env, clear=False),
                patch("premium_tools._module_available", side_effect=fake_module_available),
                patch("premium_tools._command_available", return_value=False),
                patch("premium_tools.find_java_executable", return_value=None),
                patch("premium_tools.find_tesseract_executable", return_value=Path("C:/tools/tesseract.exe")),
                patch("premium_tools.find_ocrmypdf_executable", return_value=None),
                patch("premium_tools.find_qpdf_executable", return_value=None),
                patch("premium_tools.find_ghostscript_executable", return_value=None),
                patch("premium_tools.find_tessdata_dir", return_value=Path("C:/tools/tessdata")),
                patch("premium_tools.list_tesseract_languages", return_value=["eng", "pol"]),
                patch("premium_tools.find_epubcheck_jar", return_value=None),
                patch("premium_tools.find_pdfbox_jar", return_value=None),
                patch("premium_tools.find_playwright_chromium_executable", return_value=None),
                patch("supabase_auth.DEFAULT_SUPABASE_ENV_FILES", ()),
                patch("supabase_library.DEFAULT_SUPABASE_ENV_FILES", ()),
            ):
                toolchain = premium_tools.detect_toolchain()

        self.assertEqual(toolchain["bootstrap"]["profiles"]["runtime_only"]["status"], "supported")
        self.assertEqual(toolchain["bootstrap"]["profiles"]["developer"]["status"], "supported")
        self.assertIn(
            "python -m playwright install chromium",
            toolchain["bootstrap"]["profiles"]["developer"]["manual_steps"],
        )
        self.assertEqual(toolchain["verification_surfaces"]["quick"]["status"], "supported")
        self.assertEqual(toolchain["verification_surfaces"]["quality-critical"]["status"], "supported")
        self.assertEqual(toolchain["verification_surfaces"]["corpus"]["status"], "supported")
        self.assertEqual(toolchain["verification_surfaces"]["browser"]["status"], "unsupported")
        self.assertEqual(toolchain["verification_surfaces"]["runtime"]["status"], "unsupported")
        self.assertEqual(toolchain["verification_surfaces"]["release"]["status"], "degraded")
        self.assertIn(
            "Chromium browser",
            toolchain["verification_surfaces"]["browser"]["missing_requirements"],
        )
        self.assertEqual(toolchain["conversion_capabilities"]["ocr_pipeline"]["status"], "degraded")
        self.assertEqual(toolchain["conversion_capabilities"]["epubcheck_validation"]["status"], "unavailable")
        self.assertIn(
            "Install a Java runtime and ensure `java` is on PATH.",
            toolchain["conversion_capabilities"]["epubcheck_validation"]["manual_steps"],
        )
        self.assertIn(
            "Download EPUBCheck and set EPUBCHECK_JAR to the epubcheck*.jar path, or place the jar under tools/.",
            toolchain["conversion_capabilities"]["epubcheck_validation"]["manual_steps"],
        )
        self.assertIn(
            "Install a Java runtime and ensure `java` is on PATH.",
            toolchain["conversion_capabilities"]["pdfbox_extraction"]["manual_steps"],
        )
        self.assertIn(
            "Set PDFBOX_JAR to pdfbox-app*.jar, or place the jar under tools/.",
            toolchain["conversion_capabilities"]["pdfbox_extraction"]["manual_steps"],
        )
        self.assertEqual(toolchain["conversion_capabilities"]["email_delivery"]["status"], "unavailable")
        self.assertIn(
            "Set KINDLEMASTER_EMAIL_DELIVERY=1 and configure KINDLEMASTER_SMTP_HOST, KINDLEMASTER_SMTP_USERNAME, KINDLEMASTER_SMTP_PASSWORD, and KINDLEMASTER_SMTP_FROM.",
            toolchain["conversion_capabilities"]["email_delivery"]["manual_steps"],
        )
        self.assertEqual(toolchain["conversion_capabilities"]["cloud_account_library"]["status"], "unavailable")
        self.assertIn(
            "Set KINDLEMASTER_AUTH_PROVIDER=supabase, SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SERVICE_ROLE_KEY, and SUPABASE_ARTIFACT_BUCKET.",
            toolchain["conversion_capabilities"]["cloud_account_library"]["manual_steps"],
        )

    def test_detect_toolchain_marks_release_and_corpus_unsupported_without_python_chess(self) -> None:
        def fake_module_available(name: str) -> bool:
            if name in {"chess", "chess.pgn"}:
                return False
            return True

        with (
            patch("premium_tools._module_available", side_effect=fake_module_available),
            patch("premium_tools._command_available", return_value=False),
            patch("premium_tools.find_java_executable", return_value=None),
            patch("premium_tools.find_tesseract_executable", return_value=Path("C:/tools/tesseract.exe")),
            patch("premium_tools.find_ocrmypdf_executable", return_value=None),
            patch("premium_tools.find_qpdf_executable", return_value=None),
            patch("premium_tools.find_ghostscript_executable", return_value=None),
            patch("premium_tools.find_tessdata_dir", return_value=Path("C:/tools/tessdata")),
            patch("premium_tools.list_tesseract_languages", return_value=["eng", "pol"]),
            patch("premium_tools.find_epubcheck_jar", return_value=None),
            patch("premium_tools.find_pdfbox_jar", return_value=None),
            patch("premium_tools.find_playwright_chromium_executable", return_value=Path("C:/chromium/chrome.exe")),
        ):
            toolchain = premium_tools.detect_toolchain(refresh=True)

        self.assertEqual(toolchain["verification_surfaces"]["corpus"]["status"], "unsupported")
        self.assertEqual(toolchain["verification_surfaces"]["release"]["status"], "unsupported")
        self.assertIn("python-chess", toolchain["verification_surfaces"]["corpus"]["missing_requirements"])
        self.assertIn("python-chess", toolchain["verification_surfaces"]["release"]["missing_requirements"])

    def test_run_tests_release_skips_optional_surfaces_when_unavailable(self) -> None:
        toolchain = {
            "verification_surfaces": {
                "release": {
                    "status": "degraded",
                    "notes": ["optional follow-ups skipped"],
                    "optional_followups": [
                        {
                            "surface": "browser",
                            "status": "unsupported",
                            "missing_requirements": ["Chromium browser"],
                        },
                        {
                            "surface": "runtime",
                            "status": "unsupported",
                            "missing_requirements": ["Chromium browser"],
                        },
                    ],
                }
            }
        }

        bounded_results = []

        def fake_run(command, *, cwd, label, timeout_seconds):
            bounded_results.append((label, command, timeout_seconds))
            return {
                "label": label,
                "command": list(command),
                "status": "passed",
                "returncode": 0,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": 0.01,
            }

        with patch("premium_tools.detect_toolchain", return_value=toolchain):
            with patch("kindlemaster._print_json") as print_json:
                with patch("kindlemaster._write_governance_artifact"):
                    with patch("kindlemaster._run_bounded_command", side_effect=fake_run):
                        with patch("kindlemaster._load_corpus_gate_summary", return_value={"overall_status": "passed"}):
                            exit_code = _run_tests("release")

        self.assertEqual(exit_code, 0)
        executed_commands = [command for _, command, _ in bounded_results]
        self.assertEqual(
            executed_commands,
            [
                [sys.executable, "-m", "unittest", *RELEASE_TESTS],
                [sys.executable, "-m", "unittest", *CORPUS_TESTS],
                [sys.executable, "kindlemaster.py", "corpus", "--proof-profile", "standard"],
            ],
        )
        print_json.assert_called_once()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["suite"], "release")
        self.assertEqual(payload["status"], "passed_with_warnings")
        self.assertEqual(payload["warning_reasons"], ["optional_followups_skipped"])

    def test_run_tests_release_can_use_ci_corpus_proof_profile(self) -> None:
        toolchain = {
            "verification_surfaces": {
                "release": {
                    "status": "supported",
                    "notes": ["ci release"],
                    "optional_followups": [],
                }
            }
        }

        bounded_results = []

        def fake_run(command, *, cwd, label, timeout_seconds):
            bounded_results.append((label, command, timeout_seconds))
            return {
                "label": label,
                "command": list(command),
                "status": "passed",
                "returncode": 0,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": 0.01,
            }

        with patch.dict(os.environ, {"KINDLEMASTER_RELEASE_PROOF_PROFILE": "ci"}):
            with patch("premium_tools.detect_toolchain", return_value=toolchain):
                with patch("kindlemaster._print_json") as print_json:
                    with patch("kindlemaster._write_governance_artifact"):
                        with patch("kindlemaster._run_bounded_command", side_effect=fake_run):
                            with patch("kindlemaster._load_corpus_gate_summary", return_value={"overall_status": "passed"}):
                                exit_code = _run_tests("release")

        self.assertEqual(exit_code, 0)
        executed_commands = [command for _, command, _ in bounded_results]
        self.assertEqual(
            executed_commands,
            [
                [sys.executable, "-m", "unittest", *RELEASE_TESTS],
                [sys.executable, "-m", "unittest", *CORPUS_TESTS],
                [sys.executable, "kindlemaster.py", "corpus", "--proof-profile", "ci"],
            ],
        )
        self.assertEqual(bounded_results[2][0], "corpus-gate-ci")
        payload = print_json.call_args.args[0]
        self.assertIn("Release corpus gate is using `ci` proof profile.", payload["notes"])

    def test_run_tests_corpus_executes_unittests_then_corpus_gate(self) -> None:
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run_mock:
            exit_code = _run_tests("corpus")

        self.assertEqual(exit_code, 0)
        executed_commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(
            executed_commands,
            [
                [sys.executable, "-m", "unittest", *CORPUS_TESTS],
                [sys.executable, "kindlemaster.py", "corpus"],
            ],
        )

    def test_run_tests_full_uses_unittest_discover_diagnostic_lane(self) -> None:
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run_mock:
            exit_code = _run_tests("full")

        self.assertEqual(exit_code, 0)
        run_mock.assert_called_once()
        self.assertEqual(
            run_mock.call_args.args[0],
            [sys.executable, "-m", "unittest", "discover", "-p", TEST_FILE_PATTERN],
        )
        self.assertEqual(run_mock.call_args.kwargs["cwd"], Path(kindlemaster.__file__).resolve().parent)

    def test_run_tests_quick_writes_governance_artifact(self) -> None:
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run_mock:
            with patch("kindlemaster._write_governance_artifact") as artifact_mock:
                exit_code = _run_tests("quick")

        self.assertEqual(exit_code, 0)
        run_mock.assert_called_once()
        artifact_mock.assert_called_once()
        self.assertEqual(artifact_mock.call_args.kwargs["lane"], "quick")
        payload = artifact_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["suite"], "quick")
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["returncode"], 0)
        self.assertEqual(payload["command"], "python kindlemaster.py test --suite quick")

    def test_doctor_command_routes_to_toolchain_detection(self) -> None:
        payload = {"verification_surfaces": {"quick": {"status": "supported"}}}
        with patch("premium_tools.detect_toolchain", return_value=payload) as doctor_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(kindlemaster, "_write_governance_artifact") as artifact_mock:
                    with patch.object(sys, "argv", ["kindlemaster.py", "doctor"]):
                        exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        doctor_mock.assert_called_once()
        print_mock.assert_called_once_with(payload)
        artifact_mock.assert_called_once()
        self.assertEqual(artifact_mock.call_args.kwargs["lane"], "doctor")
        self.assertEqual(artifact_mock.call_args.kwargs["payload"]["command"], "python kindlemaster.py doctor")

    def test_prepare_reference_inputs_command_routes_to_bootstrap_script(self) -> None:
        payload = {"manifest": "reference_inputs/manifest.json", "case_count": 3}
        with patch("scripts.prepare_reference_inputs.prepare_reference_inputs", return_value=payload) as prepare_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(sys, "argv", ["kindlemaster.py", "prepare-reference-inputs"]):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        prepare_mock.assert_called_once()
        print_mock.assert_called_once_with(payload)

    def test_smoke_command_routes_to_runner_and_preserves_filters(self) -> None:
        payload = {"summary": {"overall_status": "passed"}}
        with patch("scripts.run_smoke_tests.run_smoke_tests", return_value=payload) as smoke_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "kindlemaster.py",
                        "smoke",
                        "--mode",
                        "micro",
                        "--manifest",
                        "reference_inputs/manifest.json",
                        "--output-dir",
                        "out",
                        "--reports-dir",
                        "reports",
                        "--case",
                        "ocr",
                    ],
                ):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        smoke_mock.assert_called_once_with(
            manifest_path="reference_inputs/manifest.json",
            mode="micro",
            output_dir="out",
            reports_dir="reports",
            case_filters=["ocr"],
        )
        print_mock.assert_called_once_with(payload)

    def test_validate_command_returns_failure_for_failed_validator_payload(self) -> None:
        payload = {"overall_status": "failed", "reports": []}
        with patch("scripts.run_epub_validators.run_epub_validators", return_value=payload) as validate_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(
                    sys,
                    "argv",
                    ["kindlemaster.py", "validate", "a.epub", "b.epub", "--reports-dir", "reports/validators"],
                ):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 1)
        validate_mock.assert_called_once_with(["a.epub", "b.epub"], reports_dir="reports/validators")
        print_mock.assert_called_once_with(payload)

    def test_ml_dataset_command_passes_feedback_logs_to_builder(self) -> None:
        payload = {"status": "insufficient_data", "feedback_route_example_count": 1}
        with patch("scripts.build_ml_datasets.build_ml_datasets", return_value=payload) as dataset_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "kindlemaster.py",
                        "ml",
                        "dataset",
                        "--manifest",
                        "manifest.json",
                        "--labels",
                        "labels.json",
                        "--reports-root",
                        "reports",
                        "--output-dir",
                        "reports/ml/datasets",
                        "--feedback-log",
                        "reports/ml/feedback/conversion_feedback.jsonl",
                    ],
                ):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        dataset_mock.assert_called_once_with(
            manifest_path="manifest.json",
            labels_path="labels.json",
            reports_root="reports",
            output_dir="reports/ml/datasets",
            feedback_log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
            fail_on_collisions=False,
            min_examples_per_class=25,
        )
        print_mock.assert_called_once_with(payload)

    def test_ml_sample_reference_command_routes_to_sampler(self) -> None:
        payload = {"status": "dry_run", "created_count": 2}
        with patch("scripts.sample_reference_inputs.sample_reference_inputs", return_value=payload) as sample_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "kindlemaster.py",
                        "ml",
                        "sample-reference",
                        "--manifest",
                        "manifest.json",
                        "--labels",
                        "labels.json",
                        "--input-type",
                        "pdf",
                        "--output-dir",
                        "reference_inputs/pdf_samples",
                        "--max-pages",
                        "60",
                        "--min-pages",
                        "120",
                        "--min-size-bytes",
                        "1000",
                        "--dry-run",
                    ],
                ):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        sample_mock.assert_called_once_with(
            manifest_path="manifest.json",
            labels_path="labels.json",
            input_types=("pdf",),
            output_dir="reference_inputs/pdf_samples",
            max_pages=60,
            min_pages=120,
            min_size_bytes=1000,
            dry_run=True,
        )
        print_mock.assert_called_once_with(payload)

    def test_ml_feedback_command_logs_and_exports_without_training(self) -> None:
        logged = {"status": "logged", "record_id": "fb_1"}
        exported = {"status": "exported", "route_example_count": 1}
        with patch("ml_feedback.append_conversion_feedback_from_report", return_value=logged) as log_mock:
            with patch("ml_feedback.export_feedback_datasets", return_value=exported) as export_mock:
                with patch.object(kindlemaster, "_print_json") as print_mock:
                    with patch.object(
                        sys,
                        "argv",
                        [
                            "kindlemaster.py",
                            "ml",
                            "feedback",
                            "--report-json",
                            "reports/conversion.json",
                            "--log",
                            "reports/ml/feedback/conversion_feedback.jsonl",
                            "--source",
                            "inputs/source.pdf",
                            "--output",
                            "output/source.epub",
                            "--case-id",
                            "case-1",
                            "--feedback-status",
                            "accepted",
                            "--quality-label",
                            "usable",
                            "--quality-score",
                            "4",
                            "--route-label",
                            "book_reflow",
                            "--issue-tag",
                            "toc",
                            "--notes",
                            "Looks usable.",
                            "--reviewer",
                            "operator",
                            "--export-dir",
                            "reports/ml/feedback",
                        ],
                    ):
                        exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        log_mock.assert_called_once_with(
            report_path="reports/conversion.json",
            log_path="reports/ml/feedback/conversion_feedback.jsonl",
            source_path="inputs/source.pdf",
            output_path="output/source.epub",
            case_id="case-1",
            feedback_status="accepted",
            quality_label="usable",
            quality_score="4",
            route_label="book_reflow",
            issue_tags=["toc"],
            notes="Looks usable.",
            reviewer="operator",
            include_in_training=False,
        )
        export_mock.assert_called_once_with(
            log_paths=["reports/ml/feedback/conversion_feedback.jsonl"],
            output_dir="reports/ml/feedback",
        )
        payload = print_mock.call_args.args[0]
        self.assertEqual(payload["status"], "completed")
        self.assertFalse(payload["online_learning"])
        self.assertEqual(payload["actions"], ["log", "export"])

    def test_ml_promote_command_requires_candidate_and_uses_promotion_gate(self) -> None:
        payload = {"status": "blocked", "error": "promotion_gates_failed"}
        with patch("scripts.train_route_classifier.promote_route_classifier", return_value=payload) as promote_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(
                    sys,
                    "argv",
                    [
                        "kindlemaster.py",
                        "ml",
                        "promote",
                        "--candidate",
                        "models/candidates/route_classifier_candidate.json",
                        "--model",
                        "models/route_classifier_v1.json",
                        "--corpus-report",
                        "reports/corpus/premium_corpus_smoke_report.json",
                    ],
                ):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 1)
        promote_mock.assert_called_once_with(
            candidate_path="models/candidates/route_classifier_candidate.json",
            model_path="models/route_classifier_v1.json",
            corpus_report_path="reports/corpus/premium_corpus_smoke_report.json",
        )
        print_mock.assert_called_once_with(payload)

    def test_audit_command_builds_release_audit_invocation(self) -> None:
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run_mock:
            with patch.object(
                sys,
                "argv",
                [
                    "kindlemaster.py",
                    "audit",
                    "book.epub",
                    "--output-dir",
                    "out",
                    "--reports-dir",
                    "reports",
                    "--language",
                    "pl",
                    "--title",
                    "Title",
                    "--author",
                    "Author",
                    "--description",
                    "Desc",
                    "--publication-profile",
                    "book",
                    "--strict-premium",
                ],
            ):
                exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run_mock.call_args.args[0],
            [
                sys.executable,
                "scripts/run_release_audit.py",
                "book.epub",
                "--output-dir",
                "out",
                "--reports-dir",
                "reports",
                "--language",
                "pl",
                "--title",
                "Title",
                "--author",
                "Author",
                "--description",
                "Desc",
                "--publication-profile",
                "book",
                "--strict-premium",
            ],
        )

    def test_workflow_commands_route_to_workflow_runner(self) -> None:
        with patch("workflow_runner.run_workflow_baseline", return_value={"status": "baseline"}) as baseline_mock:
            with patch("workflow_runner.run_workflow_verify", return_value={"status": "passed_with_warnings"}) as verify_mock:
                with patch.object(kindlemaster, "_print_json") as print_mock:
                    with patch.object(
                        sys,
                        "argv",
                        [
                            "kindlemaster.py",
                            "workflow",
                            "baseline",
                            "input.pdf",
                            "--change-area",
                            "corpus",
                            "--reports-root",
                            "reports/workflows",
                            "--output-root",
                            "output/workflows",
                        ],
                    ):
                        baseline_exit = kindlemaster.main()
                    with patch.object(
                        sys,
                        "argv",
                        [
                            "kindlemaster.py",
                            "workflow",
                            "verify",
                            "input.pdf",
                            "--run-id",
                            "run-1",
                            "--reports-root",
                            "reports/workflows",
                            "--output-root",
                            "output/workflows",
                        ],
                    ):
                        verify_exit = kindlemaster.main()

        self.assertEqual(baseline_exit, 0)
        self.assertEqual(verify_exit, 0)
        baseline_mock.assert_called_once_with(
            "input.pdf",
            change_area="corpus",
            reports_root="reports/workflows",
            output_root="output/workflows",
        )
        verify_mock.assert_called_once_with(
            "input.pdf",
            run_id="run-1",
            reports_root="reports/workflows",
            output_root="output/workflows",
        )
        self.assertEqual(print_mock.call_count, 2)

    def test_run_tests_browser_reports_unavailable_when_surface_missing(self) -> None:
        toolchain = {
            "verification_surfaces": {
                "browser": {
                    "status": "unsupported",
                    "missing_requirements": ["Chromium browser"],
                    "notes": ["Install Playwright browser support first."],
                }
            }
        }

        with patch("premium_tools.detect_toolchain", return_value=toolchain):
            with patch("kindlemaster._print_json") as print_json:
                with patch("kindlemaster.subprocess.run") as run_mock:
                    exit_code = _run_tests("browser")

        self.assertEqual(exit_code, 1)
        run_mock.assert_not_called()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["suite"], "browser")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["missing_requirements"], ["Chromium browser"])

    def test_run_tests_runtime_reports_unavailable_when_surface_missing(self) -> None:
        toolchain = {
            "verification_surfaces": {
                "runtime": {
                    "status": "unsupported",
                    "missing_requirements": ["Waitress", "Chromium browser"],
                    "notes": ["Runtime gate needs the live HTTP stack."],
                }
            }
        }

        with patch("premium_tools.detect_toolchain", return_value=toolchain):
            with patch("kindlemaster._print_json") as print_json:
                with patch("kindlemaster.subprocess.run") as run_mock:
                    exit_code = _run_tests("runtime")

        self.assertEqual(exit_code, 1)
        run_mock.assert_not_called()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["suite"], "runtime")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["missing_requirements"], ["Waitress", "Chromium browser"])

    def test_process_auto_strict_fails_when_python_chess_is_missing(self) -> None:
        def fake_import(name: str):
            if name == "chess":
                raise ImportError("missing chess")
            return __import__(name)

        with patch("chess_auto_flow.importlib.import_module", side_effect=fake_import):
            with patch.object(sys, "argv", ["kindlemaster.py", "process", "--mode", "auto-strict"]):
                with patch("kindlemaster._print_json") as print_json:
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 1)
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error_code"], "python_chess_missing")
        self.assertIn("python_chess_unavailable", payload["blockers"])

    def test_process_non_strict_reports_manual_review_when_python_chess_is_missing(self) -> None:
        def fake_import(name: str):
            if name == "chess":
                raise ImportError("missing chess")
            return __import__(name)

        with patch("chess_auto_flow.importlib.import_module", side_effect=fake_import):
            with patch.object(sys, "argv", ["kindlemaster.py", "process", "--mode", "auto"]):
                with patch("kindlemaster._print_json") as print_json:
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["status"], "requires_review")
        self.assertTrue(payload["manual_review_required"])
        self.assertIn("python_chess_unavailable", payload["warnings"])

    def test_validate_strict_fails_before_validators_when_python_chess_is_missing(self) -> None:
        def fake_import(name: str):
            if name == "chess.pgn":
                raise ImportError("missing chess.pgn")
            return __import__(name)

        with patch("chess_auto_flow.importlib.import_module", side_effect=fake_import):
            with patch.object(sys, "argv", ["kindlemaster.py", "validate", "output.epub", "--strict"]):
                with patch("kindlemaster._print_json") as print_json:
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 1)
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["overall_status"], "failed")
        self.assertEqual(payload["error_code"], "python_chess_pgn_missing")
        self.assertIn("python_chess_unavailable", payload["blockers"])

    def test_run_tests_corpus_reports_blocker_when_python_chess_is_missing(self) -> None:
        def fake_import(name: str):
            if name == "chess":
                raise ImportError("missing chess")
            return __import__(name)

        with patch("chess_auto_flow.importlib.import_module", side_effect=fake_import):
            with patch("kindlemaster._print_json") as print_json:
                with patch("kindlemaster.subprocess.run") as run_mock:
                    exit_code = _run_tests("corpus")

        self.assertEqual(exit_code, 1)
        run_mock.assert_not_called()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["suite"], "corpus")
        self.assertEqual(payload["status"], "unavailable")
        self.assertIn("python_chess_unavailable", payload["blockers"])

    def test_run_tests_release_reports_blocker_when_python_chess_is_missing(self) -> None:
        def fake_import(name: str):
            if name == "chess.pgn":
                raise ImportError("missing chess.pgn")
            return __import__(name)

        toolchain = {
            "verification_surfaces": {
                "release": {
                    "status": "supported",
                    "notes": ["release proof"],
                    "optional_followups": [],
                }
            }
        }

        with patch("chess_auto_flow.importlib.import_module", side_effect=fake_import):
            with patch("premium_tools.detect_toolchain", return_value=toolchain):
                with patch("kindlemaster._print_json") as print_json:
                    with patch("kindlemaster._write_governance_artifact"):
                        with patch("kindlemaster._run_bounded_command") as bounded_mock:
                            exit_code = _run_tests("release")

        self.assertEqual(exit_code, 1)
        bounded_mock.assert_not_called()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["suite"], "release")
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["error_code"], "python_chess_pgn_missing")
        self.assertIn("python_chess_unavailable", payload["blockers"])

    def test_run_tests_release_appends_supported_optional_followups(self) -> None:
        toolchain = {
            "verification_surfaces": {
                "release": {
                    "status": "supported",
                    "notes": ["optional follow-ups enabled"],
                    "optional_followups": [
                        {"surface": "browser", "status": "supported"},
                        {"surface": "runtime", "status": "supported"},
                    ],
                }
            }
        }

        bounded_results = []

        def fake_run(command, *, cwd, label, timeout_seconds):
            bounded_results.append((label, command, timeout_seconds))
            return {
                "label": label,
                "command": list(command),
                "status": "passed",
                "returncode": 0,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": 0.01,
            }

        with patch("premium_tools.detect_toolchain", return_value=toolchain):
            with patch("kindlemaster._print_json") as print_json:
                with patch("kindlemaster._write_governance_artifact"):
                    with patch("kindlemaster._run_bounded_command", side_effect=fake_run):
                        with patch("kindlemaster._load_corpus_gate_summary", return_value={"overall_status": "passed_with_warnings"}):
                            exit_code = _run_tests("release")

        self.assertEqual(exit_code, 0)
        executed_commands = [command for _, command, _ in bounded_results]
        self.assertEqual(
            executed_commands,
            [
                [sys.executable, "-m", "unittest", *RELEASE_TESTS],
                [sys.executable, "-m", "unittest", *CORPUS_TESTS],
                [sys.executable, "kindlemaster.py", "corpus", "--proof-profile", "standard"],
                [sys.executable, "-m", "unittest", *BROWSER_TESTS],
                [sys.executable, "-m", "unittest", *RUNTIME_TESTS],
            ],
        )
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["status"], "passed_with_warnings")
        self.assertEqual(payload["warning_reasons"], ["corpus_gate_passed_with_warnings"])

    def test_run_tests_release_stops_on_bounded_step_timeout(self) -> None:
        toolchain = {
            "verification_surfaces": {
                "release": {
                    "status": "supported",
                    "notes": ["bounded release"],
                    "optional_followups": [],
                }
            }
        }

        def fake_run(command, *, cwd, label, timeout_seconds):
            return {
                "label": label,
                "command": list(command),
                "status": "timed_out",
                "returncode": kindlemaster.RELEASE_TIMEOUT_RETURN_CODE,
                "timeout_seconds": timeout_seconds,
                "elapsed_seconds": timeout_seconds,
            }

        with patch("premium_tools.detect_toolchain", return_value=toolchain):
            with patch("kindlemaster._print_json") as print_json:
                with patch("kindlemaster._write_governance_artifact"):
                    with patch("kindlemaster._run_bounded_command", side_effect=fake_run):
                        exit_code = _run_tests("release")

        self.assertEqual(exit_code, 1)
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["suite"], "release")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failed_step"], "release-units")
        self.assertEqual(payload["steps"][0]["status"], "timed_out")

    def test_run_bootstrap_runtime_only_keeps_dev_requirements_out_of_install_plan(self) -> None:
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run_mock:
            with patch("premium_tools.detect_toolchain", return_value={"bootstrap": {"profiles": {}}}):
                with patch("kindlemaster._print_json") as print_json:
                    exit_code = _run_bootstrap(runtime_only=True)

        self.assertEqual(exit_code, 0)
        executed_commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(
            executed_commands,
            [
                [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
                [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
            ],
        )
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["bootstrap_run"]["requested_profile"], "runtime_only")
        self.assertEqual(payload["bootstrap_run"]["installed_requirements_files"], ["requirements.txt"])

    def test_run_bootstrap_developer_installs_git_hooks_when_not_skipped(self) -> None:
        hook_payload = {"status": "installed", "hooks_path": ".githooks"}
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with patch("premium_tools.detect_toolchain", return_value={"bootstrap": {"profiles": {}}}):
                with patch("scripts.install_git_hooks.install_git_hooks", return_value=hook_payload) as hook_mock:
                    with patch("kindlemaster._print_json") as print_json:
                        with patch.dict(os.environ, {}, clear=True):
                            exit_code = _run_bootstrap(runtime_only=False)

        self.assertEqual(exit_code, 0)
        hook_mock.assert_called_once()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["bootstrap_run"]["git_hooks"], hook_payload)

    def test_run_bootstrap_skips_git_hooks_in_ci_or_when_requested(self) -> None:
        with patch("kindlemaster.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            with patch("premium_tools.detect_toolchain", return_value={"bootstrap": {"profiles": {}}}):
                with patch("scripts.install_git_hooks.install_git_hooks") as hook_mock:
                    with patch("kindlemaster._print_json") as print_json:
                        with patch.dict(os.environ, {"CI": "true"}, clear=True):
                            exit_code = _run_bootstrap(runtime_only=False)

        self.assertEqual(exit_code, 0)
        hook_mock.assert_not_called()
        payload = print_json.call_args.args[0]
        self.assertEqual(payload["bootstrap_run"]["git_hooks"]["status"], "skipped")
        self.assertEqual(payload["bootstrap_run"]["git_hooks"]["reason"], "ci")

    def test_run_serve_uses_resolved_defaults_when_port_and_debug_are_not_provided(self) -> None:
        stdout = io.StringIO()

        with patch("app_runtime_services.resolve_server_port", return_value=5401):
            with patch("app_runtime_services.resolve_debug_mode", return_value=True):
                with patch("app_runtime_services.resolve_server_host", return_value="127.0.0.1"):
                    with patch("app_runtime_services.serve_http_app", return_value=0) as serve_mock:
                        with contextlib.redirect_stdout(stdout):
                            exit_code = kindlemaster._run_serve(
                                port=None,
                                debug=False,
                                runtime="flask",
                            )

        self.assertEqual(exit_code, 0)
        self.assertIn("http://kindlemaster.localhost:5401/", stdout.getvalue())
        self.assertIn("runtime=flask, debug=True", stdout.getvalue())
        self.assertEqual(serve_mock.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(serve_mock.call_args.kwargs["port"], 5401)
        self.assertTrue(serve_mock.call_args.kwargs["debug"])
        self.assertEqual(serve_mock.call_args.kwargs["runtime"], "flask")

    def test_agents_first_class_commands_track_kindlemaster_parser(self) -> None:
        source = Path("kindlemaster.py").read_text(encoding="utf-8")
        documented_commands = set(
            re.findall(r'^\s*(?:\w+\s*=\s*)?subparsers\.add_parser\("([^"]+)"', source, flags=re.MULTILINE)
        )

        self.assertTrue(documented_commands)

        agents_text = Path("AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Control-Plane Source-of-Truth Matrix", agents_text)
        for command in sorted(documented_commands):
            self.assertIn(f"- `{command}`", agents_text)

    def test_control_plane_authority_markers_exist_in_docs(self) -> None:
        readme_text = Path("README.md").read_text(encoding="utf-8")
        codex_readme_text = Path(".codex/README.md").read_text(encoding="utf-8")
        codex_config_text = Path(".codex/config.toml").read_text(encoding="utf-8")

        self.assertIn("## Authority Map", readme_text)
        self.assertIn("AGENTS.md` is the canonical human-readable authority map", readme_text)
        self.assertIn("reports/workflows/<run_id>/", readme_text)

        self.assertIn("## Authority Map", codex_readme_text)
        self.assertIn("control-plane source-of-truth matrix", codex_readme_text)
        self.assertIn("Convenience Command Mirror", codex_readme_text)

        self.assertIn("Control-plane authority map:", codex_config_text)
        self.assertIn("generated output/ and reports/ artifacts are derived evidence", codex_config_text)
        self.assertIn('model = "gpt-5.5"', codex_config_text)
        self.assertIn('model_reasoning_effort = "xhigh"', codex_config_text)
        self.assertIn('approval_policy = "on-request"', codex_config_text)
        self.assertIn('@playwright/mcp@0.0.70', codex_config_text)
        self.assertIn('[plugins."browser-use@openai-bundled"]', codex_config_text)
        self.assertIn('[plugins."openai-developers@openai-curated"]', codex_config_text)
        self.assertNotIn("@playwright/mcp@latest", codex_config_text)


if __name__ == "__main__":
    unittest.main()
