from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from premium_corpus_smoke import run_premium_corpus_smoke
from scripts.run_smoke_tests import run_smoke_tests

STANDARD_SMOKE_FILTERS = [
    "ocr_probe_pdf",
    "scan_probe_epub",
    "simple_report_docx",
]

STANDARD_PREMIUM_FILTERS = [
    "document-like-report",
    "large-diagram-corpus",
]


def _derive_corpus_gate_status(*, smoke_status: str, premium_status: str) -> str:
    if "failed" in {smoke_status, premium_status}:
        return "failed"
    if "passed_with_warnings" in {smoke_status, premium_status}:
        return "passed_with_warnings"
    return "passed"


def _resolve_case_filters(
    *,
    proof_profile: str,
    explicit_filters: list[str] | None,
    standard_filters: list[str],
) -> list[str] | None:
    if explicit_filters:
        return list(explicit_filters)
    if proof_profile == "standard":
        return list(standard_filters)
    return None


def _build_corpus_gate_markdown(payload: dict[str, Any]) -> str:
    smoke = payload["smoke"]
    premium = payload["premium_corpus"]
    benchmark = payload.get("benchmark") or {}
    lines = [
        "# KindleMaster Corpus Gate",
        "",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Proof profile: `{payload['proof_profile']}`",
        f"- Smoke status: `{(smoke.get('summary') or {}).get('overall_status', 'unknown')}`",
        f"- Premium corpus status: `{(premium.get('overall') or {}).get('overall_status', 'unknown')}`",
        "",
        "## Derived Summary",
        "",
        f"- Smoke cases run: `{(smoke.get('summary') or {}).get('cases_run', 0)}`",
        f"- Premium converted cases: `{(premium.get('overall') or {}).get('converted_case_count', 0)}`",
        f"- Premium analysis-only cases: `{(premium.get('overall') or {}).get('analysis_only_case_count', 0)}`",
        f"- Premium grade counts: `{json.dumps((premium.get('overall') or {}).get('grade_counts', {}), ensure_ascii=False)}`",
        f"- Premium blockers: `{json.dumps((premium.get('overall') or {}).get('blocker_counts', {}), ensure_ascii=False)}`",
        f"- Premium warnings: `{json.dumps((premium.get('overall') or {}).get('warning_counts', {}), ensure_ascii=False)}`",
        "",
        "## Benchmark",
        "",
        f"- Total elapsed: `{benchmark.get('total_elapsed_seconds', 0)}` seconds",
        f"- Smoke elapsed: `{benchmark.get('smoke_elapsed_seconds', 0)}` seconds",
        f"- Premium elapsed: `{benchmark.get('premium_elapsed_seconds', 0)}` seconds",
        f"- Classes covered: `{benchmark.get('class_count', 0)}`",
        f"- Slowest smoke cases: `{json.dumps(benchmark.get('slowest_smoke_cases', []), ensure_ascii=False)}`",
        "",
        "## Reports",
        "",
        f"- Smoke JSON: `{payload['artifacts']['smoke_json']}`",
        f"- Smoke Markdown: `{payload['artifacts']['smoke_md']}`",
        f"- Premium corpus JSON: `{payload['artifacts']['premium_json']}`",
        f"- Premium corpus Markdown: `{payload['artifacts']['premium_md']}`",
        "",
    ]
    return "\n".join(lines)


def run_corpus_gate(
    *,
    manifest_path: str | Path = "reference_inputs/manifest.json",
    output_root: str | Path = "output/corpus",
    reports_root: str | Path = "reports/corpus",
    proof_profile: str = "standard",
    premium_output_json: str | Path | None = None,
    premium_output_md: str | Path | None = None,
    smoke_case_filters: list[str] | None = None,
    premium_case_filters: list[str] | None = None,
) -> dict[str, Any]:
    gate_started = time.perf_counter()
    resolved_output_root = Path(output_root).resolve()
    resolved_reports_root = Path(reports_root).resolve()
    resolved_output_root.mkdir(parents=True, exist_ok=True)
    resolved_reports_root.mkdir(parents=True, exist_ok=True)
    resolved_smoke_filters = _resolve_case_filters(
        proof_profile=proof_profile,
        explicit_filters=smoke_case_filters,
        standard_filters=STANDARD_SMOKE_FILTERS,
    )
    resolved_premium_filters = _resolve_case_filters(
        proof_profile=proof_profile,
        explicit_filters=premium_case_filters,
        standard_filters=STANDARD_PREMIUM_FILTERS,
    )

    smoke_output_dir = resolved_output_root / "smoke"
    smoke_reports_dir = resolved_reports_root / "smoke"
    smoke_started = time.perf_counter()
    smoke = run_smoke_tests(
        manifest_path=manifest_path,
        mode="full",
        output_dir=smoke_output_dir,
        reports_dir=smoke_reports_dir,
        case_filters=resolved_smoke_filters,
    )
    smoke_elapsed = time.perf_counter() - smoke_started

    premium_json_path = Path(premium_output_json) if premium_output_json is not None else resolved_reports_root / "premium_corpus_smoke_report.json"
    premium_md_path = Path(premium_output_md) if premium_output_md is not None else resolved_reports_root / "premium_corpus_smoke_report.md"
    premium_started = time.perf_counter()
    premium = run_premium_corpus_smoke(
        manifest_path=manifest_path,
        output_json=premium_json_path,
        output_md=premium_md_path,
        case_filters=resolved_premium_filters,
        progress=False,
    )
    premium_elapsed = time.perf_counter() - premium_started
    premium_status = premium.get("overall_status")
    if not premium_status:
        premium_status = (premium.get("overall") or {}).get("overall_status", "failed")

    overall_status = _derive_corpus_gate_status(
        smoke_status=(smoke.get("summary") or {}).get("overall_status", "failed"),
        premium_status=premium_status,
    )
    payload = {
        "overall_status": overall_status,
        "proof_profile": proof_profile,
        "smoke": smoke,
        "premium_corpus": premium,
        "benchmark": _build_gate_benchmark(
            smoke=smoke,
            premium=premium,
            total_elapsed_seconds=time.perf_counter() - gate_started,
            smoke_elapsed_seconds=smoke_elapsed,
            premium_elapsed_seconds=premium_elapsed,
        ),
        "artifacts": {
            "smoke_json": str(smoke_reports_dir / "smoke_full.json"),
            "smoke_md": str(smoke_reports_dir / "smoke_full.md"),
            "premium_json": str(premium_json_path),
            "premium_md": str(premium_md_path),
        },
    }
    (resolved_reports_root / "corpus_gate.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (resolved_reports_root / "corpus_gate.md").write_text(
        _build_corpus_gate_markdown(payload),
        encoding="utf-8",
    )
    return payload


def _build_gate_benchmark(
    *,
    smoke: dict[str, Any],
    premium: dict[str, Any],
    total_elapsed_seconds: float,
    smoke_elapsed_seconds: float,
    premium_elapsed_seconds: float,
) -> dict[str, Any]:
    smoke_benchmark = ((smoke.get("summary") or {}).get("benchmark") or {})
    premium_classes = {
        str(row.get("document_class", "") or "")
        for row in premium.get("cases", [])
        if row.get("document_class")
    }
    smoke_classes = set(smoke_benchmark.get("classes") or [])
    return {
        "total_elapsed_seconds": round(float(total_elapsed_seconds), 4),
        "smoke_elapsed_seconds": round(float(smoke_elapsed_seconds), 4),
        "premium_elapsed_seconds": round(float(premium_elapsed_seconds), 4),
        "class_count": len(smoke_classes | premium_classes),
        "classes": sorted(smoke_classes | premium_classes),
        "slowest_smoke_cases": list(smoke_benchmark.get("slowest_cases") or [])[:5],
        "premium_converted_case_count": (premium.get("overall") or {}).get("converted_case_count", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster corpus-wide quality and release gates.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--output-root", default="output/corpus")
    parser.add_argument("--reports-root", default="reports/corpus")
    parser.add_argument("--proof-profile", choices=("standard", "full"), default="standard")
    parser.add_argument("--smoke-case", action="append", default=[])
    parser.add_argument("--premium-case", action="append", default=[])
    args = parser.parse_args()

    payload = run_corpus_gate(
        manifest_path=args.manifest,
        output_root=args.output_root,
        reports_root=args.reports_root,
        proof_profile=args.proof_profile,
        smoke_case_filters=args.smoke_case,
        premium_case_filters=args.premium_case,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["overall_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
