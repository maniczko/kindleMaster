from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import kindlemaster
from kindlemaster import (
    BROWSER_TESTS,
    CORPUS_TESTS,
    QUICK_TESTS,
    RELEASE_TESTS,
    RUNTIME_TESTS,
    _json_text,
    _run_bootstrap,
    _run_convert,
    _run_tests,
)
from premium_tools import detect_toolchain


class KindleMasterEntrypointTests(unittest.TestCase):
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
        self.assertIn("test_browser_polling_e2e.py", RUNTIME_TESTS)
        self.assertIn("test_browser_privacy_diagnostics.py", RUNTIME_TESTS)
        self.assertIn("test_runtime_waitress_smoke.py", RUNTIME_TESTS)
        self.assertIn("test_corpus_gate.py", CORPUS_TESTS)
        self.assertIn("test_premium_corpus_smoke.py", CORPUS_TESTS)

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
            "ocrmypdf": False,
        }

        def fake_module_available(name: str) -> bool:
            return module_presence.get(name, False)

        with patch("premium_tools._module_available", side_effect=fake_module_available):
            with patch("premium_tools._command_available", return_value=False):
                with patch("premium_tools.find_java_executable", return_value=None):
                    with patch("premium_tools.find_tesseract_executable", return_value=Path("C:/tools/tesseract.exe")):
                        with patch("premium_tools.find_ocrmypdf_executable", return_value=None):
                            with patch("premium_tools.find_qpdf_executable", return_value=None):
                                with patch("premium_tools.find_ghostscript_executable", return_value=None):
                                    with patch("premium_tools.find_tessdata_dir", return_value=Path("C:/tools/tessdata")):
                                        with patch("premium_tools.list_tesseract_languages", return_value=["eng", "pol"]):
                                            with patch("premium_tools.find_epubcheck_jar", return_value=None):
                                                with patch("premium_tools.find_pdfbox_jar", return_value=None):
                                                    with patch("premium_tools.find_playwright_chromium_executable", return_value=None):
                                                        toolchain = detect_toolchain()

        self.assertEqual(toolchain["bootstrap"]["profiles"]["runtime_only"]["status"], "supported")
        self.assertEqual(toolchain["bootstrap"]["profiles"]["developer"]["status"], "supported")
        self.assertIn(
            "python -m playwright install chromium",
            toolchain["bootstrap"]["profiles"]["developer"]["manual_steps"],
        )
        self.assertEqual(toolchain["verification_surfaces"]["quick"]["status"], "supported")
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

    def test_doctor_command_routes_to_toolchain_detection(self) -> None:
        payload = {"verification_surfaces": {"quick": {"status": "supported"}}}
        with patch("premium_tools.detect_toolchain", return_value=payload) as doctor_mock:
            with patch.object(kindlemaster, "_print_json") as print_mock:
                with patch.object(sys, "argv", ["kindlemaster.py", "doctor"]):
                    exit_code = kindlemaster.main()

        self.assertEqual(exit_code, 0)
        doctor_mock.assert_called_once()
        print_mock.assert_called_once_with(payload)

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
                        "full",
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
            mode="full",
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

    def test_run_serve_uses_resolved_defaults_when_port_and_debug_are_not_provided(self) -> None:
        stdout = io.StringIO()

        with patch("app_runtime_services.resolve_server_port", return_value=5401):
            with patch("app_runtime_services.resolve_debug_mode", return_value=True):
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


if __name__ == "__main__":
    unittest.main()
