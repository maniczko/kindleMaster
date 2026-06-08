from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from kindlemaster import SUITE_REGISTRY

CORE_CONVERSION_COVERAGE_SOURCE = [
    "converter",
    "docx_conversion",
    "text_cleanup_engine",
    "text_normalization",
    "kindle_semantic_cleanup",
    "epub_validation",
]

CORE_CONVERSION_COVERAGE_TESTS = [
    "test_docx_conversion.py",
    "test_converter_text_cleanup.py",
    "test_kindle_semantic_cleanup_coverage_boost.py",
    "test_semantic_epub_cleanup.py",
    "test_text_normalization.py",
    "test_epub_validation.py",
]

GOVERNANCE_COVERAGE_TESTS = [
    "test_agent_config_contracts.py",
    "test_github_ready_enforcement.py",
    "test_kindlemaster_entrypoint.py",
    "test_project_status.py",
    "test_skill_contracts.py",
    "test_skill_guardrails.py",
]


def _split_csv_items(values: list[str]) -> list[str]:
    out: list[str] = []
    for raw in values:
        out.extend(item.strip() for item in raw.split(",") if item.strip())
    return out


def _run(command: Sequence[str], *, cwd: Path = ROOT_DIR) -> int:
    return subprocess.run(list(command), check=False, cwd=cwd).returncode


def _coverage_run_command(*, tests: list[str], source: list[str] | None = None, include: list[str] | None = None) -> list[str]:
    command: list[str] = [sys.executable, "-m", "coverage", "run"]
    if source:
        command.append(f"--source={','.join(source)}")
    if include:
        command.append(f"--include={','.join(include)}")
    command += ["-m", "unittest", *tests]
    return command


def _coverage_report_command(*, include: list[str] | None = None, fail_under: float | None = None) -> list[str]:
    command = [sys.executable, "-m", "coverage", "report", "--show-missing"]
    if include:
        command.append(f"--include={','.join(include)}")
    if fail_under is not None:
        command.append(f"--fail-under={fail_under}")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic coverage for selected test targets.")
    parser.add_argument(
        "--suite",
        choices=("quick", "core", "governance", "custom"),
        default="quick",
        help="Named target suite to run (default: quick).",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Coverage --source patterns (comma-separated); repeats supported.",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Coverage --include patterns for report (comma-separated); repeats supported.",
    )
    parser.add_argument("--fail-under", type=float, default=None, help="Enforce minimum report percentage.")
    parser.add_argument("--xml", default="", help="Write coverage XML report to this file path.")
    parser.add_argument("tests", nargs="*", help="Custom tests for --suite custom.")

    args = parser.parse_args()
    include = _split_csv_items(args.include)
    source = _split_csv_items(args.source)

    if args.suite == "quick":
        tests = list(SUITE_REGISTRY["quick"])
    elif args.suite == "core":
        tests = CORE_CONVERSION_COVERAGE_TESTS
        if not source:
            source = CORE_CONVERSION_COVERAGE_SOURCE
    elif args.suite == "governance":
        tests = GOVERNANCE_COVERAGE_TESTS
    else:
        if not args.tests:
            parser.error("--suite custom requires positional test names (e.g. test_x.py ...)")
        tests = args.tests

    _run([sys.executable, "-m", "coverage", "erase"])
    run_command = _coverage_run_command(tests=tests, source=source or None, include=include or None)
    if _run(run_command) != 0:
        return 1

    if args.xml:
        xml_path = args.xml.strip()
        if xml_path:
            if _run([sys.executable, "-m", "coverage", "xml", "-o", xml_path]) != 0:
                return 1

    report_command = _coverage_report_command(include=include or None, fail_under=args.fail_under)
    return _run(report_command)


if __name__ == "__main__":
    raise SystemExit(main())
