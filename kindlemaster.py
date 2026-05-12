from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

TEST_FILE_PATTERN = "test*.py"

QUICK_TESTS = [
    "test_agent_config_contracts.py",
    "test_skill_contracts.py",
    "test_skill_guardrails.py",
    "test_github_ready_enforcement.py",
    "test_project_status.py",
    "test_pdf_runtime_flow.py",
    "test_kindlemaster_entrypoint.py",
    "test_premium_tools.py",
    "test_flat2_ui_template.py",
    "test_sprint4_ui_contracts.py",
    "test_browser_conversion_outcome_harness.py",
    "test_app_async_convert.py",
    "test_conversion_library.py",
    "test_app_runtime_services.py",
    "test_runtime_job_adapter.py",
    "test_artifact_storage.py",
    "test_ai_ocr_cleanup.py",
    "test_ai_toc_detection.py",
    "test_ai_quality_intelligence.py",
    "test_ai_quality_feedback.py",
    "test_openai_quality_provider.py",
    "test_app_quality_state_route.py",
    "test_sentry_observability.py",
    "test_docx_conversion.py",
    "test_app_docx_conversion.py",
    "test_epub_validation.py",
    "test_chess_fix.py",
    "test_chess_diagram_visual_quality.py",
    "test_chess_notation_regression.py",
    "test_converter_publication_budget.py",
    "test_fixed_layout_render_budget.py",
    "test_converter_fixed_layout_budget_enforcement.py",
    "test_publication_analysis.py",
    "test_publication_pipeline.py",
    "test_quality_state_service.py",
    "test_quality_cockpit_issues.py",
    "test_quality_cockpit_preview.py",
    "test_sprint1_quality_gates.py",
    "test_run_smoke_tests.py",
    "test_magazine_kindle_reflow.py",
    "test_smoke_chess_quality.py",
    "test_conversion_cleanup_ttl_contract.py",
    "test_vat_fixture_contracts.py",
    "test_prepare_reference_inputs_ocr_fixture.py",
    "test_reference_inputs_document_like_fixture.py",
    "test_epub_text_artifacts.py",
    "test_text_normalization.py",
    "test_converter_text_cleanup.py",
    "test_semantic_epub_cleanup.py",
    "test_epub_reference_repair.py",
    "test_epub_heading_repair.py",
]

RELEASE_TESTS = [
    "test_toc_segmentation.py",
    "test_epub_quality_recovery.py",
    "test_release_quality_recovery.py",
    "test_epub_release_pipeline.py",
    "test_app_heading_repair.py",
]

RELEASE_TIMEOUT_RETURN_CODE = 124
RELEASE_STEP_TIMEOUTS_SECONDS = {
    "release-units": 300,
    "corpus-units": 240,
    "corpus-gate-standard": 1800,
    "browser-followup": 300,
    "runtime-followup": 480,
}

CORPUS_TESTS = [
    "test_premium_corpus_smoke.py",
    "test_premium_corpus_smoke_batches.py",
    "test_corpus_gate.py",
    "test_golden_epub_regression.py",
]

BROWSER_TESTS = [
    "test_browser_polling_runtime_harness.py",
]

RUNTIME_TESTS = [
    "test_runtime_waitress_smoke.py",
    "test_browser_polling_e2e.py",
    "test_sprint2_playwright_smoke.py",
    "test_browser_privacy_diagnostics.py",
    "test_ui_state_screenshot_pack.py",
]

DISCOVER_ONLY_TESTS = [
    "test_conversion_api_contracts.py",
    "test_converter_metadata_cover.py",
    "test_full_magazine.py",
    "test_integration.py",
    "test_local_hostname_contract.py",
    "test_magazine_conversion.py",
    "test_babok_dense_handbook_quality.py",
    "test_premium_reflow.py",
    "test_premium_reflow_tables.py",
    "test_quality_report_markdown.py",
    "test_quality_reporting.py",
    "test_workflow_runner.py",
]

SUITE_REGISTRY: dict[str, Sequence[str]] = {
    "quick": QUICK_TESTS,
    "release": RELEASE_TESTS,
    "corpus": CORPUS_TESTS,
    "browser": BROWSER_TESTS,
    "runtime": RUNTIME_TESTS,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Standard operational entrypoint for KindleMaster.")
    subparsers = parser.add_subparsers(dest="command")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Install the supported Python bootstrap profile (runtime-only or developer).",
    )
    bootstrap_parser.add_argument("--runtime-only", action="store_true")

    subparsers.add_parser(
        "doctor",
        help="Print the supported-vs-optional local toolchain matrix and detected availability.",
    )
    subparsers.add_parser("prepare-reference-inputs", help="Copy curated reference fixtures into reference_inputs/.")

    serve_parser = subparsers.add_parser("serve", help="Run the local KindleMaster web app.")
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--debug", action="store_true")
    serve_parser.add_argument("--runtime", choices=("flask", "waitress"), default="flask")

    convert_parser = subparsers.add_parser("convert", help="Convert a PDF or DOCX file to EPUB.")
    convert_parser.add_argument("input_path")
    convert_parser.add_argument("--output", required=True)
    convert_parser.add_argument("--language", default="pl")
    convert_parser.add_argument("--profile", default="auto-premium")
    convert_parser.add_argument("--heading-repair", action="store_true")
    convert_parser.add_argument("--domain-dictionary", default="")
    convert_parser.add_argument("--report-json", default="")

    validate_parser = subparsers.add_parser("validate", help="Run EPUB validators on one or more EPUB files.")
    validate_parser.add_argument("epub_paths", nargs="+")
    validate_parser.add_argument("--reports-dir", default="reports/validators")

    smoke_parser = subparsers.add_parser("smoke", help="Run curated smoke tests.")
    smoke_parser.add_argument("--mode", choices=("micro", "quick", "full"), default="quick")
    smoke_parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    smoke_parser.add_argument("--output-dir", default="output/smoke")
    smoke_parser.add_argument("--reports-dir", default="reports/smoke")
    smoke_parser.add_argument("--case", action="append", default=[])

    corpus_parser = subparsers.add_parser("corpus", help="Run the standard corpus-wide proof gate.")
    corpus_parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    corpus_parser.add_argument("--output-root", default="output/corpus")
    corpus_parser.add_argument("--reports-root", default="reports/corpus")
    corpus_parser.add_argument("--proof-profile", choices=("standard", "full"), default="standard")
    corpus_parser.add_argument("--smoke-case", action="append", default=[])
    corpus_parser.add_argument("--premium-case", action="append", default=[])

    status_parser = subparsers.add_parser("status", help="Generate a derived project status from existing evidence artifacts.")
    status_parser.add_argument("--repo-root", default=".")
    status_parser.add_argument("--reports-root", default="reports")
    status_parser.add_argument("--output-json", default="reports/project_status.json")
    status_parser.add_argument("--output-md", default="reports/project_status.md")

    test_parser = subparsers.add_parser("test", help="Run standard KindleMaster test suites.")
    test_parser.add_argument("--suite", choices=("quick", "release", "full", "browser", "runtime", "corpus"), default="quick")

    audit_parser = subparsers.add_parser("audit", help="Run release audit on an EPUB.")
    audit_parser.add_argument("epub_path")
    audit_parser.add_argument("--output-dir", default="output")
    audit_parser.add_argument("--reports-dir", default="reports")
    audit_parser.add_argument("--language", default="")
    audit_parser.add_argument("--title", default="")
    audit_parser.add_argument("--author", default="")
    audit_parser.add_argument("--description", default="")
    audit_parser.add_argument("--publication-profile", default="")
    audit_parser.add_argument("--strict-premium", action="store_true")

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Run the standard engineering workflow: reproduce, isolate, validate, and compare.",
    )
    workflow_subparsers = workflow_parser.add_subparsers(dest="workflow_command")

    workflow_baseline = workflow_subparsers.add_parser("baseline", help="Create baseline artifacts for a change workflow.")
    workflow_baseline.add_argument("input_path")
    workflow_baseline.add_argument("--change-area", required=True, choices=("app", "converter", "reference", "heading", "text", "semantic", "pipeline", "corpus"))
    workflow_baseline.add_argument("--reports-root", default="reports/workflows")
    workflow_baseline.add_argument("--output-root", default="output/workflows")

    workflow_verify = workflow_subparsers.add_parser("verify", help="Verify a workflow run against an existing baseline.")
    workflow_verify.add_argument("input_path")
    workflow_verify.add_argument("--run-id", required=True)
    workflow_verify.add_argument("--reports-root", default="reports/workflows")
    workflow_verify.add_argument("--output-root", default="output/workflows")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "bootstrap":
        return _run_bootstrap(runtime_only=args.runtime_only)
    if args.command == "doctor":
        from premium_tools import detect_toolchain

        started = time.perf_counter()
        payload = detect_toolchain(refresh=True)
        _write_governance_artifact(
            lane="doctor",
            payload=_governance_artifact_payload(
                command="python kindlemaster.py doctor",
                status=_derive_doctor_artifact_status(payload),
                returncode=0,
                started=started,
                notes=["Toolchain and agent-readiness detection completed."],
                extra={
                    "verification_surfaces": payload.get("verification_surfaces", {}),
                    "agent_readiness": payload.get("agent_readiness", {}),
                    "payload": payload,
                },
            ),
        )
        _print_json(payload)
        return 0
    if args.command == "prepare-reference-inputs":
        from scripts.prepare_reference_inputs import prepare_reference_inputs

        _print_json(prepare_reference_inputs())
        return 0
    if args.command == "serve":
        return _run_serve(port=args.port, debug=args.debug, runtime=args.runtime)
    if args.command == "convert":
        return _run_convert(
            input_path=args.input_path,
            output_path=args.output,
            language=args.language,
            profile=args.profile,
            heading_repair=args.heading_repair,
            domain_dictionary=args.domain_dictionary,
            report_json=args.report_json,
        )
    if args.command == "validate":
        from scripts.run_epub_validators import run_epub_validators

        payload = run_epub_validators(args.epub_paths, reports_dir=args.reports_dir)
        _print_json(payload)
        return 0 if payload["overall_status"] != "failed" else 1
    if args.command == "smoke":
        from scripts.run_smoke_tests import run_smoke_tests

        payload = run_smoke_tests(
            manifest_path=args.manifest,
            mode=args.mode,
            output_dir=args.output_dir,
            reports_dir=args.reports_dir,
            case_filters=args.case,
        )
        _print_json(payload)
        return 0 if payload["summary"]["overall_status"] != "failed" else 1
    if args.command == "corpus":
        from scripts.run_corpus_gate import run_corpus_gate

        payload = run_corpus_gate(
            manifest_path=args.manifest,
            output_root=args.output_root,
            reports_root=args.reports_root,
            proof_profile=args.proof_profile,
            smoke_case_filters=args.smoke_case,
            premium_case_filters=args.premium_case,
        )
        _print_json(payload)
        return 0 if payload.get("overall_status") != "failed" else 1
    if args.command == "status":
        from scripts.generate_project_status import generate_project_status

        payload = generate_project_status(
            repo_root=args.repo_root,
            reports_root=args.reports_root,
            output_json=args.output_json,
            output_md=args.output_md,
        )
        _print_json(payload)
        return 0 if payload.get("overall_status") != "failed" else 1
    if args.command == "test":
        return _run_tests(args.suite)
    if args.command == "audit":
        command = [
            sys.executable,
            "scripts/run_release_audit.py",
            args.epub_path,
            "--output-dir",
            args.output_dir,
            "--reports-dir",
            args.reports_dir,
        ]
        if args.language:
            command.extend(["--language", args.language])
        if args.title:
            command.extend(["--title", args.title])
        if args.author:
            command.extend(["--author", args.author])
        if args.description:
            command.extend(["--description", args.description])
        if args.publication_profile:
            command.extend(["--publication-profile", args.publication_profile])
        if args.strict_premium:
            command.append("--strict-premium")
        return subprocess.run(command, check=False).returncode
    if args.command == "workflow":
        from workflow_runner import run_workflow_baseline, run_workflow_verify

        if args.workflow_command == "baseline":
            payload = run_workflow_baseline(
                args.input_path,
                change_area=args.change_area,
                reports_root=args.reports_root,
                output_root=args.output_root,
            )
            _print_json(payload)
            return 0
        if args.workflow_command == "verify":
            payload = run_workflow_verify(
                args.input_path,
                run_id=args.run_id,
                reports_root=args.reports_root,
                output_root=args.output_root,
            )
            _print_json(payload)
            return 0 if payload.get("status") in {"passed", "passed_with_warnings"} else 1
        workflow_parser.print_help()
        return 1
    parser.print_help()
    return 0


def _run_bootstrap(*, runtime_only: bool) -> int:
    commands: list[list[str]] = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip"],
        [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
    ]
    if not runtime_only:
        commands.append([sys.executable, "-m", "pip", "install", "-r", "requirements-dev.txt"])
    for command in commands:
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            return completed.returncode
    git_hooks_payload = _maybe_install_git_hooks(runtime_only=runtime_only)

    from premium_tools import detect_toolchain

    payload = detect_toolchain(refresh=True)
    payload["bootstrap_run"] = {
        "requested_profile": "runtime_only" if runtime_only else "developer",
        "installed_requirements_files": ["requirements.txt"] if runtime_only else ["requirements.txt", "requirements-dev.txt"],
        "git_hooks": git_hooks_payload,
        "notes": [
            "Use `python kindlemaster.py doctor` to re-check the local toolchain later without reinstalling packages.",
        ],
    }
    _print_json(payload)
    return 0


def _maybe_install_git_hooks(*, runtime_only: bool) -> dict[str, Any]:
    if runtime_only:
        return {"status": "skipped", "reason": "runtime_only"}
    if os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}:
        return {"status": "skipped", "reason": "ci"}
    if os.environ.get("KINDLEMASTER_SKIP_GIT_HOOKS", "").strip():
        return {"status": "skipped", "reason": "env"}

    from scripts.install_git_hooks import install_git_hooks

    return install_git_hooks(Path(__file__).resolve().parent)


def _run_serve(*, port: int | None, debug: bool, runtime: str) -> int:
    from app import app
    from app_runtime_services import (
        LOCALHOST,
        build_local_app_url,
        resolve_debug_mode,
        resolve_server_port,
        serve_http_app,
    )

    effective_port = port if port is not None else resolve_server_port()
    effective_debug = debug or resolve_debug_mode()
    if runtime == "waitress":
        print(
            (
                f"Starting KindleMaster on {build_local_app_url(effective_port)} "
                f"(bind={LOCALHOST}, runtime=waitress, debug={effective_debug})"
            ),
            flush=True,
        )
        return serve_http_app(app, host=LOCALHOST, port=effective_port, debug=effective_debug, runtime=runtime)

    print(
        (
            f"Starting KindleMaster on {build_local_app_url(effective_port)} "
            f"(bind={LOCALHOST}, runtime=flask, debug={effective_debug})"
        ),
        flush=True,
    )
    return serve_http_app(app, host=LOCALHOST, port=effective_port, debug=effective_debug, runtime=runtime)


def _run_tests(suite: str) -> int:
    repo_root = Path(__file__).resolve().parent
    verification_surfaces: dict[str, Any] = {}
    if suite in {"browser", "runtime", "release"}:
        from premium_tools import detect_toolchain

        verification_surfaces = detect_toolchain().get("verification_surfaces", {})

    if suite == "browser":
        surface = verification_surfaces.get("browser", {})
        if surface.get("status") != "supported":
            _print_json(
                {
                    "suite": "browser",
                    "status": "unavailable",
                    "missing_requirements": surface.get("missing_requirements", []),
                    "notes": surface.get("notes", []),
                }
            )
            return 1
        return subprocess.run(
            [sys.executable, "-m", "unittest", *SUITE_REGISTRY["browser"]],
            check=False,
            cwd=repo_root,
        ).returncode
    if suite == "runtime":
        surface = verification_surfaces.get("runtime", {})
        if surface.get("status") != "supported":
            _print_json(
                {
                    "suite": "runtime",
                    "status": "unavailable",
                    "missing_requirements": surface.get("missing_requirements", []),
                    "notes": surface.get("notes", []),
                }
            )
            return 1
        return subprocess.run(
            [sys.executable, "-m", "unittest", *SUITE_REGISTRY["runtime"]],
            check=False,
            cwd=repo_root,
        ).returncode
    if suite == "corpus":
        commands: list[Sequence[str]] = [
            [sys.executable, "-m", "unittest", *SUITE_REGISTRY["corpus"]],
            [sys.executable, "kindlemaster.py", "corpus"],
        ]
        for command in commands:
            completed = subprocess.run(command, check=False, cwd=repo_root)
            if completed.returncode != 0:
                return completed.returncode
        return 0
    if suite == "release":
        return _run_release_suite(repo_root=repo_root, release_surface=verification_surfaces.get("release", {}))
    if suite == "full":
        command: Sequence[str] = [sys.executable, "-m", "unittest", "discover", "-p", TEST_FILE_PATTERN]
        return subprocess.run(command, check=False, cwd=repo_root).returncode
    if suite == "quick":
        command = [sys.executable, "-m", "unittest", *SUITE_REGISTRY["quick"]]
        started = time.perf_counter()
        completed = subprocess.run(command, check=False, cwd=repo_root)
        payload = _governance_artifact_payload(
            command="python kindlemaster.py test --suite quick",
            status="passed" if completed.returncode == 0 else "failed",
            returncode=completed.returncode,
            started=started,
            notes=["Quick suite completed."],
            extra={"suite": "quick"},
        )
        _write_governance_artifact(lane="quick", payload=payload, repo_root=repo_root)
        return completed.returncode
    else:
        command = [sys.executable, "-m", "unittest", *SUITE_REGISTRY["quick"]]
    return subprocess.run(command, check=False, cwd=repo_root).returncode


def _run_release_suite(*, repo_root: Path, release_surface: dict[str, Any]) -> int:
    started = time.perf_counter()
    release_notes = _release_suite_notes(release_surface)
    if release_surface.get("status") == "unsupported":
        payload = {
            "suite": "release",
            "status": "unavailable",
            "missing_requirements": release_surface.get("missing_requirements", []),
            "notes": release_notes,
        }
        _write_governance_artifact(
            lane="release",
            payload=_governance_artifact_payload(
                command="python kindlemaster.py test --suite release",
                status="failed",
                returncode=1,
                started=started,
                notes=release_notes,
                extra=payload,
            ),
            repo_root=repo_root,
        )
        _print_json(payload)
        return 1

    commands: list[tuple[str, Sequence[str]]] = [
        ("release-units", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["release"]]),
        ("corpus-units", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["corpus"]]),
        (
            "corpus-gate-standard",
            [sys.executable, "kindlemaster.py", "corpus", "--proof-profile", "standard"],
        ),
    ]
    optional_followups = release_surface.get("optional_followups", [])
    for followup in optional_followups:
        surface_name = followup.get("surface")
        status = followup.get("status")
        if surface_name == "browser" and status == "supported":
            commands.append(("browser-followup", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["browser"]]))
        if surface_name == "runtime" and status == "supported":
            commands.append(("runtime-followup", [sys.executable, "-m", "unittest", *SUITE_REGISTRY["runtime"]]))

    skipped_followups = [
        {
            "surface": followup.get("surface"),
            "missing_requirements": followup.get("missing_requirements", []),
        }
        for followup in optional_followups
        if followup.get("status") != "supported"
    ]
    step_results: list[dict[str, Any]] = []
    for label, command in commands:
        result = _run_bounded_command(
            command,
            cwd=repo_root,
            label=label,
            timeout_seconds=RELEASE_STEP_TIMEOUTS_SECONDS.get(label, 300),
        )
        step_results.append(result)
        if result["returncode"] != 0:
            payload = {
                "suite": "release",
                "status": "failed",
                "failed_step": label,
                "steps": step_results,
                "notes": release_notes,
                "skipped_optional_surfaces": skipped_followups,
            }
            _write_governance_artifact(
                lane="release",
                payload=_governance_artifact_payload(
                    command="python kindlemaster.py test --suite release",
                    status="failed",
                    returncode=1,
                    started=started,
                    notes=release_notes,
                    extra=payload,
                ),
                repo_root=repo_root,
            )
            _print_json(payload)
            return 1

    corpus_summary = _load_corpus_gate_summary(repo_root / "reports" / "corpus" / "corpus_gate.json")
    warning_reasons: list[str] = []
    if corpus_summary.get("overall_status") == "passed_with_warnings":
        warning_reasons.append("corpus_gate_passed_with_warnings")
    if skipped_followups:
        warning_reasons.append("optional_followups_skipped")
    status = "passed_with_warnings" if warning_reasons else "passed"
    payload = {
        "suite": "release",
        "status": status,
        "warning_reasons": warning_reasons,
        "corpus_gate": corpus_summary,
        "steps": step_results,
        "notes": release_notes,
        "skipped_optional_surfaces": skipped_followups,
    }
    _write_governance_artifact(
        lane="release",
        payload=_governance_artifact_payload(
            command="python kindlemaster.py test --suite release",
            status=status,
            returncode=0,
            started=started,
            notes=release_notes,
            extra=payload,
        ),
        repo_root=repo_root,
    )
    _print_json(payload)
    return 0


def _release_suite_notes(release_surface: dict[str, Any]) -> list[str]:
    notes = [
        "Runs bounded release-specific unit shards plus the standard corpus gate.",
        "Does not duplicate the quick suite; run `python kindlemaster.py test --suite quick` before clean release claims.",
    ]
    if release_surface.get("optional_followups"):
        notes.append("Browser and runtime follow-up suites run only when their local toolchains are supported.")
    return notes


def _run_bounded_command(
    command: Sequence[str],
    *,
    cwd: Path,
    label: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    process = _start_process(command, cwd=cwd)
    try:
        returncode = process.wait(timeout=timeout_seconds)
        status = "passed" if returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        returncode = RELEASE_TIMEOUT_RETURN_CODE
        status = "timed_out"
    return {
        "label": label,
        "command": list(command),
        "status": status,
        "returncode": returncode,
        "timeout_seconds": timeout_seconds,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
    }


def _start_process(command: Sequence[str], *, cwd: Path) -> subprocess.Popen[Any]:
    kwargs: dict[str, Any] = {"cwd": cwd}
    if os.name == "nt":
        creation_flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if creation_flag:
            kwargs["creationflags"] = creation_flag
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass


def _load_corpus_gate_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "available": False,
            "overall_status": "unknown",
            "path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "overall_status": "unknown",
            "path": str(path),
            "error": str(exc),
        }
    premium = payload.get("premium_corpus") or {}
    premium_overall = premium.get("overall") or {}
    smoke = payload.get("smoke") or {}
    return {
        "available": True,
        "path": str(path),
        "overall_status": payload.get("overall_status", "unknown"),
        "proof_profile": payload.get("proof_profile", "unknown"),
        "smoke_status": (smoke.get("summary") or {}).get("overall_status", "unknown"),
        "premium_status": premium.get("overall_status") or premium_overall.get("overall_status", "unknown"),
        "grade_counts": premium_overall.get("grade_counts", {}),
        "blocker_counts": premium_overall.get("blocker_counts", {}),
        "warning_counts": premium_overall.get("warning_counts", {}),
        "benchmark": payload.get("benchmark", {}),
    }


def _governance_artifact_payload(
    *,
    command: str,
    status: str,
    returncode: int,
    started: float,
    notes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "status": status,
        "returncode": returncode,
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "notes": notes or [],
        **(extra or {}),
    }


def _write_governance_artifact(
    *,
    lane: str,
    payload: dict[str, Any],
    repo_root: Path | None = None,
) -> Path:
    resolved_root = repo_root or Path(__file__).resolve().parent
    report_path = resolved_root / "reports" / "governance" / f"{lane}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_json_text(payload), encoding="utf-8")
    return report_path


def _derive_doctor_artifact_status(payload: dict[str, Any]) -> str:
    status_priority = {
        "supported": 0,
        "passed": 0,
        "degraded": 1,
        "passed_with_warnings": 1,
        "unsupported": 2,
        "failed": 2,
        "unavailable": 1,
    }
    statuses: list[str] = []
    surfaces = payload.get("verification_surfaces")
    if isinstance(surfaces, dict):
        for surface_name in ("quick", "corpus", "release"):
            surface = surfaces.get(surface_name)
            if isinstance(surface, dict):
                statuses.append(str(surface.get("status", "unavailable")))
    agent_readiness = payload.get("agent_readiness")
    if isinstance(agent_readiness, dict):
        statuses.append(str(agent_readiness.get("status", "unavailable")))

    if not statuses:
        return "passed"
    worst = max(statuses, key=lambda item: status_priority.get(item, 1))
    if status_priority.get(worst, 1) >= 2:
        return "failed"
    if status_priority.get(worst, 1) == 1:
        return "passed_with_warnings"
    return "passed"


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_text(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, indent=2)


def _print_json(value: Any) -> None:
    rendered = _json_text(value)
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write((rendered + "\n").encode("utf-8", errors="replace"))
        stream.flush()
        return
    print(rendered)


def _run_convert(
    *,
    input_path: str,
    output_path: str,
    language: str,
    profile: str,
    heading_repair: bool,
    domain_dictionary: str = "",
    report_json: str = "",
) -> int:
    from app_runtime_services import ConversionRequest, run_document_conversion
    from converter import convert_document_to_epub_with_report
    from epub_heading_repair import repair_epub_headings_and_toc

    resolved_input = Path(input_path).resolve()
    if not resolved_input.exists():
        _print_json({"error": f"Input not found: {resolved_input}"})
        return 1

    resolved_output = Path(output_path).resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    source_suffix = resolved_input.suffix.lower()
    source_type = source_suffix.lstrip(".") if source_suffix in {".pdf", ".docx"} else None
    outcome = run_document_conversion(
        ConversionRequest(
            source_path=str(resolved_input),
            source_type=source_type,
            original_filename=resolved_input.name,
            profile=profile,
            language=language,
            heading_repair_enabled=heading_repair,
            text_cleanup_domain_dictionary_path=domain_dictionary or None,
        ),
        convert_impl=convert_document_to_epub_with_report,
        heading_repair_impl=repair_epub_headings_and_toc,
    )

    resolved_output.write_bytes(outcome.epub_bytes)

    payload = {
        **outcome.result,
        "output_path": str(resolved_output),
        "heading_repair": outcome.heading_repair_report,
    }
    payload["epub_bytes"] = f"<{len(outcome.epub_bytes)} bytes>"
    payload = _json_safe(payload)
    if report_json:
        report_path = Path(report_json).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_json_text(payload), encoding="utf-8")
    _print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
