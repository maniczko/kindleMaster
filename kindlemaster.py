from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

QUICK_TESTS = [
    "test_skill_contracts.py",
    "test_skill_guardrails.py",
    "test_github_ready_enforcement.py",
    "test_project_status.py",
    "test_pdf_runtime_flow.py",
    "test_kindlemaster_entrypoint.py",
    "test_app_async_convert.py",
    "test_app_runtime_services.py",
    "test_docx_conversion.py",
    "test_app_docx_conversion.py",
    "test_epub_validation.py",
    "test_fixed_layout_render_budget.py",
    "test_converter_fixed_layout_budget_enforcement.py",
    "test_conversion_cleanup_ttl_contract.py",
    "test_vat_fixture_contracts.py",
    "test_prepare_reference_inputs_ocr_fixture.py",
    "test_reference_inputs_document_like_fixture.py",
    "test_text_normalization.py",
    "test_converter_text_cleanup.py",
    "test_semantic_epub_cleanup.py",
    "test_epub_reference_repair.py",
    "test_epub_heading_repair.py",
]

RELEASE_TESTS = QUICK_TESTS + [
    "test_toc_segmentation.py",
    "test_epub_quality_recovery.py",
    "test_release_quality_recovery.py",
    "test_epub_release_pipeline.py",
    "test_premium_corpus_smoke.py",
    "test_app_heading_repair.py",
]

CORPUS_TESTS = [
    "test_premium_corpus_smoke.py",
    "test_premium_corpus_smoke_batches.py",
    "test_corpus_gate.py",
]

BROWSER_TESTS = [
    "test_browser_polling_runtime_harness.py",
]

RUNTIME_TESTS = [
    "test_runtime_waitress_smoke.py",
    "test_browser_polling_e2e.py",
    "test_browser_privacy_diagnostics.py",
]


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
    convert_parser.add_argument("--report-json", default="")

    validate_parser = subparsers.add_parser("validate", help="Run EPUB validators on one or more EPUB files.")
    validate_parser.add_argument("epub_paths", nargs="+")
    validate_parser.add_argument("--reports-dir", default="reports/validators")

    smoke_parser = subparsers.add_parser("smoke", help="Run curated smoke tests.")
    smoke_parser.add_argument("--mode", choices=("quick", "full"), default="quick")
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

        _print_json(detect_toolchain())
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
    from premium_tools import detect_toolchain

    payload = detect_toolchain()
    payload["bootstrap_run"] = {
        "requested_profile": "runtime_only" if runtime_only else "developer",
        "installed_requirements_files": ["requirements.txt"] if runtime_only else ["requirements.txt", "requirements-dev.txt"],
        "notes": [
            "Use `python kindlemaster.py doctor` to re-check the local toolchain later without reinstalling packages.",
        ],
    }
    _print_json(payload)
    return 0


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
            [sys.executable, "-m", "unittest", *BROWSER_TESTS],
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
            [sys.executable, "-m", "unittest", *RUNTIME_TESTS],
            check=False,
            cwd=repo_root,
        ).returncode
    if suite == "corpus":
        commands: list[Sequence[str]] = [
            [sys.executable, "-m", "unittest", *CORPUS_TESTS],
            [sys.executable, "kindlemaster.py", "corpus"],
        ]
        for command in commands:
            completed = subprocess.run(command, check=False, cwd=repo_root)
            if completed.returncode != 0:
                return completed.returncode
        return 0
    if suite == "release":
        release_surface = verification_surfaces.get("release", {})
        if release_surface.get("status") == "unsupported":
            _print_json(
                {
                    "suite": "release",
                    "status": "unavailable",
                    "missing_requirements": release_surface.get("missing_requirements", []),
                    "notes": release_surface.get("notes", []),
                }
            )
            return 1
        commands: list[Sequence[str]] = [
            [sys.executable, "-m", "unittest", *RELEASE_TESTS],
            [sys.executable, "kindlemaster.py", "smoke", "--mode", "quick"],
            [sys.executable, "kindlemaster.py", "test", "--suite", "corpus"],
        ]
        optional_followups = release_surface.get("optional_followups", [])
        for followup in optional_followups:
            surface_name = followup.get("surface")
            status = followup.get("status")
            if surface_name == "browser" and status == "supported":
                commands.append([sys.executable, "-m", "unittest", *BROWSER_TESTS])
            if surface_name == "runtime" and status == "supported":
                commands.append([sys.executable, "-m", "unittest", *RUNTIME_TESTS])
        skipped_followups = [
            {
                "surface": followup.get("surface"),
                "missing_requirements": followup.get("missing_requirements", []),
            }
            for followup in optional_followups
            if followup.get("status") != "supported"
        ]
        if skipped_followups:
            _print_json(
                {
                    "suite": "release",
                    "status": "degraded",
                    "notes": release_surface.get("notes", []),
                    "skipped_optional_surfaces": skipped_followups,
                }
            )
        for command in commands:
            completed = subprocess.run(command, check=False, cwd=repo_root)
            if completed.returncode != 0:
                return completed.returncode
        return 0
    if suite == "full":
        command: Sequence[str] = [sys.executable, "-m", "unittest", "discover", "-p", "test*.py"]
    else:
        command = [sys.executable, "-m", "unittest", *QUICK_TESTS]
    return subprocess.run(command, check=False, cwd=repo_root).returncode


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
    report_json: str,
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
