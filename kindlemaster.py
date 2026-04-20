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
    "test_pdf_runtime_flow.py",
    "test_kindlemaster_entrypoint.py",
    "test_docx_conversion.py",
    "test_app_docx_conversion.py",
    "test_epub_validation.py",
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Standard operational entrypoint for KindleMaster.")
    subparsers = parser.add_subparsers(dest="command")

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Install runtime and dev/test dependencies.")
    bootstrap_parser.add_argument("--runtime-only", action="store_true")

    subparsers.add_parser("doctor", help="Print detected toolchain and validator availability.")
    subparsers.add_parser("prepare-reference-inputs", help="Copy curated reference fixtures into reference_inputs/.")

    serve_parser = subparsers.add_parser("serve", help="Run the local KindleMaster web app.")
    serve_parser.add_argument("--port", type=int, default=None)
    serve_parser.add_argument("--debug", action="store_true")

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

    test_parser = subparsers.add_parser("test", help="Run standard KindleMaster test suites.")
    test_parser.add_argument("--suite", choices=("quick", "release", "full"), default="quick")

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

        print(json.dumps(detect_toolchain(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "prepare-reference-inputs":
        from scripts.prepare_reference_inputs import prepare_reference_inputs

        print(json.dumps(prepare_reference_inputs(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        return _run_serve(port=args.port, debug=args.debug)
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
        print(json.dumps(payload, ensure_ascii=False, indent=2))
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
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["summary"]["overall_status"] != "failed" else 1
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
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        if args.workflow_command == "verify":
            payload = run_workflow_verify(
                args.input_path,
                run_id=args.run_id,
                reports_root=args.reports_root,
                output_root=args.output_root,
            )
            print(json.dumps(payload, ensure_ascii=False, indent=2))
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

    print(json.dumps(detect_toolchain(), ensure_ascii=False, indent=2))
    return 0


def _run_serve(*, port: int | None, debug: bool) -> int:
    from app import LOCALHOST, _resolve_debug_mode, _resolve_server_port, app

    effective_port = port if port is not None else _resolve_server_port()
    effective_debug = debug or _resolve_debug_mode()
    print(f"Starting KindleMaster on http://{LOCALHOST}:{effective_port} (debug={effective_debug})", flush=True)
    app.run(debug=effective_debug, host=LOCALHOST, port=effective_port)
    return 0


def _run_tests(suite: str) -> int:
    if suite == "full":
        command: Sequence[str] = [sys.executable, "-m", "unittest", "discover", "-p", "test*.py"]
    elif suite == "release":
        command = [sys.executable, "-m", "unittest", *RELEASE_TESTS]
    else:
        command = [sys.executable, "-m", "unittest", *QUICK_TESTS]
    return subprocess.run(command, check=False).returncode


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


def _run_convert(
    *,
    input_path: str,
    output_path: str,
    language: str,
    profile: str,
    heading_repair: bool,
    report_json: str,
) -> int:
    from converter import ConversionConfig, convert_document_to_epub_with_report
    from epub_heading_repair import repair_epub_headings_and_toc

    resolved_input = Path(input_path).resolve()
    if not resolved_input.exists():
        print(json.dumps({"error": f"Input not found: {resolved_input}"}, ensure_ascii=False, indent=2))
        return 1

    resolved_output = Path(output_path).resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)

    result = convert_document_to_epub_with_report(
        str(resolved_input),
        config=ConversionConfig(profile=profile, language=language, prefer_fixed_layout=profile == "preserve-layout"),
        original_filename=resolved_input.name,
    )
    epub_bytes = result["epub_bytes"]
    heading_repair_report = {
        "status": "skipped",
        "release_status": "unavailable",
        "toc_entries_before": 0,
        "toc_entries_after": 0,
        "headings_removed": 0,
        "manual_review_count": 0,
        "epubcheck_status": "unavailable",
        "error": "",
    }

    if heading_repair:
        try:
            heading_repair_result = repair_epub_headings_and_toc(
                epub_bytes,
                title_hint=str((result.get("document_summary", {}) or {}).get("title", "") or ""),
                author_hint=str((result.get("document_summary", {}) or {}).get("author", "") or ""),
                language_hint=language,
                publication_profile=profile,
            )
            heading_repair_report = {
                "status": "applied",
                "release_status": heading_repair_result.summary.get("release_status", "unavailable"),
                "toc_entries_before": heading_repair_result.summary.get("toc_entries_before", 0),
                "toc_entries_after": heading_repair_result.summary.get("toc_entries_after", 0),
                "headings_removed": heading_repair_result.summary.get("headings_removed", 0),
                "manual_review_count": heading_repair_result.summary.get("manual_review_count", 0),
                "epubcheck_status": heading_repair_result.summary.get("epubcheck_status", "unavailable"),
                "error": "",
            }
            if heading_repair_result.epubcheck.get("status") == "failed":
                messages = heading_repair_result.epubcheck.get("messages", []) or []
                heading_repair_report["status"] = "failed"
                heading_repair_report["error"] = next((str(message) for message in messages if str(message).strip()), "Heading/TOC repair failed.")
            else:
                epub_bytes = heading_repair_result.epub_bytes
        except Exception as exc:
            heading_repair_report["status"] = "failed"
            heading_repair_report["error"] = str(exc)

    resolved_output.write_bytes(epub_bytes)

    payload = {
        **result,
        "output_path": str(resolved_output),
        "heading_repair": heading_repair_report,
    }
    payload["epub_bytes"] = f"<{len(epub_bytes)} bytes>"
    payload = _json_safe(payload)
    if report_json:
        report_path = Path(report_json).resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
