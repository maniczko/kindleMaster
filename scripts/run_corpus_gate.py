from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from premium_corpus_smoke import (
    FOCUSED_OUTPUT_ROUTES,
    build_output_assertions,
    classify_focus_routes,
    inspect_epub,
    run_premium_corpus_smoke,
)
from scripts.evaluate_chess_fen_corpus import (
    DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    evaluate_chess_fen_corpus,
)
from scripts.run_smoke_tests import run_smoke_tests

STANDARD_SMOKE_FILTERS = [
    "ocr_probe_pdf",
    "scan_probe_epub",
    "simple_report_docx",
    "list_table_image_docx",
]

STANDARD_PREMIUM_FILTERS = [
    "document-like-report",
    "ocr_stress_scan",
    "magazine_layout",
    "diagram_training_book",
]

CI_SMOKE_FILTERS = list(STANDARD_SMOKE_FILTERS)

CI_PREMIUM_FILTERS = [
    "document-like-report",
]


def _derive_corpus_gate_status(*statuses: str, smoke_status: str = "", premium_status: str = "") -> str:
    merged = {status for status in (*statuses, smoke_status, premium_status) if status}
    if "failed" in merged:
        return "failed"
    if "passed_with_warnings" in merged:
        return "passed_with_warnings"
    return "passed"


def _effective_premium_status_for_gate(
    premium: dict[str, Any],
    *,
    proof_profile: str,
    output_assertion_status: str,
) -> str:
    raw_status = str(
        premium.get("overall_status")
        or (premium.get("overall") or {}).get("overall_status")
        or "failed"
    )
    if raw_status != "passed_with_warnings":
        return raw_status
    if proof_profile != "standard" or output_assertion_status != "passed":
        return raw_status

    overall = premium.get("overall") or {}
    grade_counts = overall.get("grade_counts") or {}
    has_active_quality_warnings = bool(overall.get("blocker_counts") or overall.get("warning_counts"))
    has_review_grades = int(grade_counts.get("fail", 0) or 0) > 0 or int(
        grade_counts.get("pass_with_review", 0) or 0
    ) > 0
    if has_active_quality_warnings or has_review_grades:
        return raw_status

    # Standard corpus combines PDF premium proof with smoke/output assertions
    # for DOCX/EPUB routes. A partial premium-only scope should not keep the
    # corpus gate yellow once focused output routes are fully covered and all
    # remaining quality warnings have been explicitly accepted as P2.
    if overall.get("proof_scope") == "partial":
        return "passed"
    return raw_status


def _derive_output_assertion_status(output_assertions: dict[str, Any]) -> str:
    if output_assertions.get("status") == "not_evaluated":
        return "passed"
    if output_assertions.get("failed_routes"):
        return "failed"
    if output_assertions.get("skipped_routes") or output_assertions.get("analysis_only_routes"):
        return "passed_with_warnings"
    focus_routes = output_assertions.get("focus_routes") or {}
    if any(item.get("status") == "covered_with_warnings" for item in focus_routes.values()):
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
    if proof_profile in {"standard", "ci"}:
        return list(standard_filters)
    return None


def _default_smoke_filters_for_profile(proof_profile: str) -> list[str]:
    if proof_profile == "ci":
        return list(CI_SMOKE_FILTERS)
    return list(STANDARD_SMOKE_FILTERS)


def _default_premium_filters_for_profile(proof_profile: str) -> list[str]:
    if proof_profile == "ci":
        return list(CI_PREMIUM_FILTERS)
    return list(STANDARD_PREMIUM_FILTERS)


def _fen_min_profile_count_for_proof_profile(proof_profile: str, explicit_count: int | None) -> int:
    if explicit_count is not None:
        return max(0, int(explicit_count))
    if proof_profile == "ci":
        env_value = os.environ.get("KINDLEMASTER_CI_FEN_MIN_PROFILE_COUNT", "").strip()
        if env_value:
            try:
                return max(0, int(env_value))
            except ValueError:
                return 1
        return 1
    if proof_profile in {"standard", "full"}:
        return 2
    return 1


def _fen_min_profile_count_source(proof_profile: str, explicit_count: int | None) -> str:
    if explicit_count is not None:
        return "explicit"
    if proof_profile == "ci" and os.environ.get("KINDLEMASTER_CI_FEN_MIN_PROFILE_COUNT", "").strip():
        return "env:KINDLEMASTER_CI_FEN_MIN_PROFILE_COUNT"
    return "default"


def _enforce_fen_profile_count_policy(
    fen_corpus: dict[str, Any],
    *,
    required_count: int,
    source: str,
    proof_profile: str,
) -> dict[str, Any]:
    updated = dict(fen_corpus)
    evaluated_count = int(updated.get("evaluated_case_count", 0) or 0)
    missing_count = max(0, int(required_count) - evaluated_count)
    updated["min_profile_count"] = int(required_count)
    updated["min_profile_count_source"] = source
    updated["proof_profile"] = proof_profile
    updated["missing_profile_count"] = missing_count
    reasons = [str(reason) for reason in list(updated.get("reasons") or []) if str(reason).strip()]
    if missing_count:
        reason = f"manifest has {evaluated_count} chess FEN profile(s), below required minimum {required_count}"
        if reason not in reasons:
            reasons.append(reason)
    updated["reasons"] = reasons
    actions = [str(action) for action in list(updated.get("next_required_actions") or []) if str(action).strip()]
    if missing_count:
        action = (
            f"add {missing_count} real scanned chess FEN profile(s) to reach min_profile_count={required_count}; "
            "do not claim broad FEN generalization from a single committed profile"
        )
        if action not in actions:
            actions.append(action)
    updated["next_required_actions"] = actions
    if missing_count and updated.get("status") == "passed":
        updated["status"] = "failed"
    return updated


def _case_validation_status(row: dict[str, Any]) -> str:
    validation = row.get("validation") or {}
    return (validation.get("summary") or {}).get("status", "")


def _read_epub_stats_from_row(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    candidate = str(row.get("output_epub") or "")
    if not candidate and str(row.get("input_type", "")).lower() == "epub":
        candidate = str(row.get("path") or "")
    if not candidate:
        return None, "output_artifact_missing"
    path = Path(candidate)
    if not path.exists():
        return None, f"output_artifact_not_found:{candidate}"
    try:
        return inspect_epub(path.read_bytes()), ""
    except Exception as exc:
        return None, f"output_artifact_unreadable:{exc.__class__.__name__}"


def _roll_up_assertions(assertions: list[dict[str, str]], *, validation_status: str = "") -> str:
    if validation_status == "failed" or any(assertion.get("status") == "failed" for assertion in assertions):
        return "failed"
    if any(assertion.get("status") == "passed_with_warnings" for assertion in assertions):
        return "passed_with_warnings"
    if assertions:
        return "passed"
    return "skipped"


def _premium_effective_validation_status(row: dict[str, Any], assertions: list[dict[str, str]]) -> str:
    raw_status = str(((row.get("quality") or {}).get("validation_status")) or "")
    validation_assertion = next(
        (
            assertion
            for assertion in assertions
            if assertion.get("id") == "validation_not_failed" and assertion.get("status") == "passed"
        ),
        None,
    )
    heading = row.get("heading_repair") or {}
    heading_recovered = (
        raw_status == "failed"
        and str(heading.get("epubcheck_status") or "").lower() == "passed"
        and str(heading.get("status") or "").lower() in {"applied", "completed"}
    )
    if validation_assertion or heading_recovered:
        return "passed"
    return raw_status


def _build_smoke_output_evidence(row: dict[str, Any]) -> dict[str, Any]:
    document_class = str(row.get("document_class", "") or "")
    input_type = str(row.get("input_type", "") or "")
    stats, skip_reason = _read_epub_stats_from_row(row)
    validation_status = _case_validation_status(row)
    assertions = (
        build_output_assertions(
            document_class=document_class,
            input_type=input_type,
            stats=stats,
            validation_status=validation_status,
        )
        if stats is not None
        else [
            {
                "id": "output_artifact_available",
                "route": "base",
                "route_label": "base",
                "status": "failed",
                "severity": "blocker",
                "detail": skip_reason,
            }
        ]
    )
    return {
        "source": "smoke",
        "id": str(row.get("id", "") or row.get("path", "")),
        "document_class": document_class,
        "input_type": input_type,
        "status": _roll_up_assertions(assertions, validation_status=validation_status),
        "validation_status": validation_status,
        "focus_routes": list(classify_focus_routes(document_class, input_type)),
        "assertions": assertions,
        "skip_reason": skip_reason,
    }


def _build_premium_output_evidence(row: dict[str, Any]) -> dict[str, Any]:
    document_class = str(row.get("document_class", "") or "")
    input_type = str(row.get("input_type", "") or "pdf")
    assertions = list(row.get("output_assertions") or [])
    if not assertions and (row.get("post_heading_epub_stats") or row.get("epub_stats")):
        validation_status = str(((row.get("quality") or {}).get("validation_status")) or "")
        assertions = build_output_assertions(
            document_class=document_class,
            input_type=input_type,
            stats=row.get("post_heading_epub_stats") or row.get("epub_stats") or {},
            validation_status=validation_status,
        )
    else:
        validation_status = str(((row.get("quality") or {}).get("validation_status")) or "")
    raw_validation_status = validation_status
    validation_status = _premium_effective_validation_status(row, assertions)
    status = _roll_up_assertions(assertions, validation_status=validation_status)
    grade = str(row.get("grade", ""))
    if status == "passed" and grade == "pass_with_review":
        status = "passed_with_warnings"
    if grade == "fail":
        status = "failed"
    return {
        "source": "premium_corpus",
        "id": str(row.get("case_id", "") or row.get("file", "")),
        "document_class": document_class,
        "input_type": input_type,
        "status": status,
        "validation_status": validation_status,
        "raw_validation_status": raw_validation_status,
        "grade": grade,
        "focus_routes": list(classify_focus_routes(document_class, input_type)),
        "assertions": assertions,
        "skip_reason": "",
    }


def _build_gate_output_assertions(*, smoke: dict[str, Any], premium: dict[str, Any]) -> dict[str, Any]:
    if "cases" not in smoke and "cases" not in premium:
        return {
            "status": "not_evaluated",
            "focus_routes": {},
            "covered_route_count": 0,
            "failed_routes": [],
            "skipped_routes": [],
            "analysis_only_routes": [],
        }
    evidence: list[dict[str, Any]] = []
    analysis_only: list[dict[str, Any]] = []
    for row in smoke.get("cases", []) or []:
        routes = classify_focus_routes(str(row.get("document_class", "")), str(row.get("input_type", "")))
        if routes:
            evidence.append(_build_smoke_output_evidence(row))
    for row in premium.get("cases", []) or []:
        routes = classify_focus_routes(str(row.get("document_class", "")), str(row.get("input_type", "pdf")))
        if not routes:
            continue
        if row.get("mode") == "analysis-only":
            analysis_only.append(
                {
                    "id": str(row.get("case_id", "") or row.get("file", "")),
                    "document_class": str(row.get("document_class", "")),
                    "input_type": str(row.get("input_type", "pdf")),
                    "reason": str(row.get("analysis_only_reason") or row.get("notes") or "analysis_only"),
                    "focus_routes": list(routes),
                }
            )
        elif row.get("mode") == "convert-and-audit":
            evidence.append(_build_premium_output_evidence(row))

    focus_routes: dict[str, Any] = {}
    for route, label in FOCUSED_OUTPUT_ROUTES.items():
        route_evidence = [item for item in evidence if route in item.get("focus_routes", [])]
        route_analysis_only = [item for item in analysis_only if route in item.get("focus_routes", [])]
        if route_evidence:
            status = "covered"
            if any(item.get("status") == "failed" for item in route_evidence):
                status = "failed"
            elif any(item.get("status") == "passed_with_warnings" for item in route_evidence):
                status = "covered_with_warnings"
            reason = "real output assertions evaluated"
        elif route_analysis_only:
            status = "analysis_only"
            reason = "; ".join(item.get("reason", "analysis_only") for item in route_analysis_only)
        else:
            status = "skipped"
            reason = "no selected fixture produced output evidence in this proof profile"
        focus_routes[route] = {
            "label": label,
            "status": status,
            "reason": reason,
            "cases": route_evidence,
            "analysis_only_cases": route_analysis_only,
        }
    return {
        "focus_routes": focus_routes,
        "covered_route_count": sum(1 for item in focus_routes.values() if item["status"] in {"covered", "covered_with_warnings"}),
        "failed_routes": [route for route, item in focus_routes.items() if item["status"] == "failed"],
        "skipped_routes": [route for route, item in focus_routes.items() if item["status"] == "skipped"],
        "analysis_only_routes": [route for route, item in focus_routes.items() if item["status"] == "analysis_only"],
    }


def _build_corpus_gate_markdown(payload: dict[str, Any]) -> str:
    smoke = payload["smoke"]
    premium = payload["premium_corpus"]
    fen_corpus = payload.get("fen_corpus") or {}
    benchmark = payload.get("benchmark") or {}
    output_assertions = payload.get("output_assertions") or {}
    lines = [
        "# KindleMaster Corpus Gate",
        "",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Proof profile: `{payload['proof_profile']}`",
        f"- Smoke status: `{(smoke.get('summary') or {}).get('overall_status', 'unknown')}`",
        f"- Premium corpus status: `{(premium.get('overall') or {}).get('overall_status', 'unknown')}`",
        f"- FEN corpus status: `{fen_corpus.get('status', 'unknown')}`",
        f"- Effective premium status for gate: `{payload.get('effective_premium_status', (premium.get('overall') or {}).get('overall_status', 'unknown'))}`",
        "",
        "## Derived Summary",
        "",
        f"- Smoke cases run: `{(smoke.get('summary') or {}).get('cases_run', 0)}`",
        f"- Premium converted cases: `{(premium.get('overall') or {}).get('converted_case_count', 0)}`",
        f"- Premium analysis-only cases: `{(premium.get('overall') or {}).get('analysis_only_case_count', 0)}`",
        f"- Premium grade counts: `{json.dumps((premium.get('overall') or {}).get('grade_counts', {}), ensure_ascii=False)}`",
        f"- Premium blockers: `{json.dumps((premium.get('overall') or {}).get('blocker_counts', {}), ensure_ascii=False)}`",
        f"- Premium warnings: `{json.dumps((premium.get('overall') or {}).get('warning_counts', {}), ensure_ascii=False)}`",
        f"- Premium accepted P2 warnings: `{json.dumps((premium.get('overall') or {}).get('accepted_warning_counts', {}), ensure_ascii=False)}`",
        f"- FEN evaluated profiles: `{fen_corpus.get('evaluated_case_count', 0)}`",
        f"- FEN min profiles required: `{fen_corpus.get('min_profile_count', 0)}`",
        f"- FEN min profiles source: `{fen_corpus.get('min_profile_count_source', 'unknown')}`",
        f"- FEN missing profiles: `{fen_corpus.get('missing_profile_count', 0)}`",
        f"- FEN min seed labels/profile: `{fen_corpus.get('default_min_seed_label_count', 0)}`",
        f"- FEN valid seed labels: `{sum(int(((case.get('label_validation') or {}).get('valid_label_count') or 0)) for case in fen_corpus.get('cases', []))}`",
        f"- FEN overall exact accuracy: `{fen_corpus.get('overall_exact_fen_accuracy', 0)}`",
        f"- FEN false positives: `{fen_corpus.get('total_false_positive_count', 0)}`",
        f"- FEN font-board candidate profiles: `{fen_corpus.get('font_board_candidate_profile_count', 0)}`",
        f"- FEN font-board candidate status: `{fen_corpus.get('font_board_candidate_status', 'not_configured')}`",
        f"- FEN font-board candidate failures: `{fen_corpus.get('font_board_candidate_failed_count', 0)}`",
        f"- FEN reasons: `{json.dumps(fen_corpus.get('reasons', []), ensure_ascii=False)}`",
        f"- FEN next actions: `{json.dumps(fen_corpus.get('next_required_actions', []), ensure_ascii=False)}`",
        "",
        "## Benchmark",
        "",
        f"- Total elapsed: `{benchmark.get('total_elapsed_seconds', 0)}` seconds",
        f"- Smoke elapsed: `{benchmark.get('smoke_elapsed_seconds', 0)}` seconds",
        f"- Premium elapsed: `{benchmark.get('premium_elapsed_seconds', 0)}` seconds",
        f"- Classes covered: `{benchmark.get('class_count', 0)}`",
        f"- Slowest smoke cases: `{json.dumps(benchmark.get('slowest_smoke_cases', []), ensure_ascii=False)}`",
        f"- Slowest premium cases: `{json.dumps(benchmark.get('slowest_premium_cases', []), ensure_ascii=False)}`",
        f"- Slowest premium stages: `{json.dumps(benchmark.get('slowest_premium_stages', []), ensure_ascii=False)}`",
        "",
        "## Output Assertions",
        "",
        f"- Assertion status: `{payload.get('output_assertion_status', 'unknown')}`",
        f"- Covered focused routes: `{output_assertions.get('covered_route_count', 0)}`",
        f"- Failed focused routes: `{', '.join(output_assertions.get('failed_routes', [])) or 'none'}`",
        f"- Skipped focused routes: `{', '.join(output_assertions.get('skipped_routes', [])) or 'none'}`",
        "",
        "### Focus Routes",
        "",
    ]
    for route, label in FOCUSED_OUTPUT_ROUTES.items():
        route_payload = (output_assertions.get("focus_routes") or {}).get(route, {})
        lines.append(f"- {label}: `{route_payload.get('status', 'skipped')}` - {route_payload.get('reason', '')}")
    lines.extend(
        [
            "",
            "## Reports",
            "",
            f"- Smoke JSON: `{payload['artifacts']['smoke_json']}`",
            f"- Smoke Markdown: `{payload['artifacts']['smoke_md']}`",
            f"- Premium corpus JSON: `{payload['artifacts']['premium_json']}`",
            f"- Premium corpus Markdown: `{payload['artifacts']['premium_md']}`",
            f"- FEN corpus JSON: `{payload['artifacts']['fen_corpus_json']}`",
            "",
        ]
    )
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
    fen_min_profile_count: int | None = None,
    fen_min_seed_label_count: int = 20,
) -> dict[str, Any]:
    gate_started = time.perf_counter()
    resolved_output_root = Path(output_root).resolve()
    resolved_reports_root = Path(reports_root).resolve()
    resolved_output_root.mkdir(parents=True, exist_ok=True)
    resolved_reports_root.mkdir(parents=True, exist_ok=True)
    resolved_smoke_filters = _resolve_case_filters(
        proof_profile=proof_profile,
        explicit_filters=smoke_case_filters,
        standard_filters=_default_smoke_filters_for_profile(proof_profile),
    )
    resolved_premium_filters = _resolve_case_filters(
        proof_profile=proof_profile,
        explicit_filters=premium_case_filters,
        standard_filters=_default_premium_filters_for_profile(proof_profile),
    )
    resolved_fen_min_profile_count = _fen_min_profile_count_for_proof_profile(proof_profile, fen_min_profile_count)
    fen_min_profile_count_source = _fen_min_profile_count_source(proof_profile, fen_min_profile_count)

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
    fen_corpus_json_path = resolved_reports_root / "fen_corpus_90.json"
    fen_corpus = evaluate_chess_fen_corpus(
        manifest_path,
        min_confidence=DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
        default_min_exact_accuracy=0.90,
        default_min_seed_label_count=fen_min_seed_label_count,
        min_profile_count=resolved_fen_min_profile_count,
        output_path=fen_corpus_json_path,
    )
    fen_corpus = _enforce_fen_profile_count_policy(
        fen_corpus,
        required_count=resolved_fen_min_profile_count,
        source=fen_min_profile_count_source,
        proof_profile=proof_profile,
    )
    premium_status = premium.get("overall_status")
    if not premium_status:
        premium_status = (premium.get("overall") or {}).get("overall_status", "failed")
    output_assertions = _build_gate_output_assertions(smoke=smoke, premium=premium)
    output_assertion_status = _derive_output_assertion_status(output_assertions)
    effective_premium_status = _effective_premium_status_for_gate(
        premium,
        proof_profile=proof_profile,
        output_assertion_status=output_assertion_status,
    )

    overall_status = _derive_corpus_gate_status(
        smoke_status=(smoke.get("summary") or {}).get("overall_status", "failed"),
        premium_status=effective_premium_status,
    )
    overall_status = _derive_corpus_gate_status(
        smoke_status=overall_status,
        premium_status=output_assertion_status,
    )
    overall_status = _derive_corpus_gate_status(overall_status, str(fen_corpus.get("status") or "failed"))
    payload = {
        "overall_status": overall_status,
        "proof_profile": proof_profile,
        "smoke": smoke,
        "premium_corpus": premium,
        "fen_corpus": fen_corpus,
        "fen_profile_count_policy": {
            "min_profile_count": resolved_fen_min_profile_count,
            "source": fen_min_profile_count_source,
            "proof_profile": proof_profile,
        },
        "effective_premium_status": effective_premium_status,
        "output_assertions": output_assertions,
        "output_assertion_status": output_assertion_status,
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
            "fen_corpus_json": str(fen_corpus_json_path),
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
        "slowest_premium_cases": _slowest_premium_cases(premium),
        "slowest_premium_stages": _slowest_premium_stages(premium),
        "premium_converted_case_count": (premium.get("overall") or {}).get("converted_case_count", 0),
    }


def _slowest_premium_cases(premium: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in premium.get("cases", []) or []
        if row.get("mode") == "convert-and-audit" and row.get("elapsed_seconds") is not None
    ]
    rows.sort(key=lambda row: float(row.get("elapsed_seconds") or 0), reverse=True)
    return [
        {
            "id": str(row.get("case_id", "") or row.get("file", "")),
            "document_class": str(row.get("document_class", "")),
            "elapsed_seconds": row.get("elapsed_seconds"),
            "duration_bucket": row.get("duration_bucket", "unknown"),
            "profile_hint": row.get("profile_hint", ""),
            "grade": row.get("grade", ""),
        }
        for row in rows[:5]
    ]


def _slowest_premium_stages(premium: dict[str, Any]) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    for row in premium.get("cases", []) or []:
        timing = row.get("timing_breakdown") or {}
        if not isinstance(timing, dict):
            continue
        for stage, elapsed in timing.items():
            if stage == "total":
                continue
            try:
                seconds = float(elapsed)
            except Exception:
                continue
            stages.append(
                {
                    "case_id": str(row.get("case_id", "") or row.get("file", "")),
                    "document_class": str(row.get("document_class", "")),
                    "stage": str(stage),
                    "elapsed_seconds": round(seconds, 4),
                }
            )
    stages.sort(key=lambda item: float(item.get("elapsed_seconds", 0.0)), reverse=True)
    return stages[:8]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster corpus-wide quality and release gates.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--output-root", default="output/corpus")
    parser.add_argument("--reports-root", default="reports/corpus")
    parser.add_argument("--proof-profile", choices=("standard", "full", "ci"), default="standard")
    parser.add_argument("--smoke-case", action="append", default=[])
    parser.add_argument("--premium-case", action="append", default=[])
    parser.add_argument("--fen-min-profile-count", type=int, default=None)
    parser.add_argument("--fen-min-seed-label-count", type=int, default=20)
    args = parser.parse_args()

    payload = run_corpus_gate(
        manifest_path=args.manifest,
        output_root=args.output_root,
        reports_root=args.reports_root,
        proof_profile=args.proof_profile,
        smoke_case_filters=args.smoke_case,
        premium_case_filters=args.premium_case,
        fen_min_profile_count=args.fen_min_profile_count,
        fen_min_seed_label_count=args.fen_min_seed_label_count,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["overall_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
