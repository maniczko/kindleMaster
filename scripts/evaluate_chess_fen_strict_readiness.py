from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.check_chess_fen_strict_regression_gate import count_strict_accepted


SCHEMA = "kindlemaster.chess_fen.strict_readiness.v1"
MISSING_ARTIFACT = "MISSING_ARTIFACT"
AI_CATEGORIES = ("ai_consensus", "ai_tie_break_resolved", "ai_unreadable", "ai_best_effort")


def evaluate_chess_fen_strict_readiness(
    *,
    latest_path: str | Path,
    best_path: str | Path,
    ai_path: str | Path,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict[str, Any]:
    latest = _load_required(Path(latest_path))
    best = _load_required(Path(best_path))
    latest_payload = latest.get("payload") if isinstance(latest.get("payload"), Mapping) else {}
    best_payload = best.get("payload") if isinstance(best.get("payload"), Mapping) else {}
    latest_count = _strict_count(latest_payload)
    best_count = _strict_count(best_payload)
    coverage = max(_case_count(latest_payload), _case_count(best_payload), 0)
    delta = latest_count - best_count

    missing_required = [item for item in (latest, best) if item.get("status") == MISSING_ARTIFACT]
    if missing_required:
        status = "blocked"
    elif latest_count < best_count:
        status = "regressed"
    elif latest_count == best_count:
        status = "stable"
    else:
        status = "improved"

    release_reason = "ok"
    if status == "blocked":
        release_reason = "missing_required_report"
    elif status == "regressed":
        release_reason = "strict_regression"

    payload = {
        "schema": SCHEMA,
        "status": status,
        "inputs": {
            "latest": _input_summary(latest),
            "best": _input_summary(best),
            "ai": _input_summary(_load_optional(Path(ai_path))),
        },
        "strict": {
            "latest_accepted": latest_count,
            "best_accepted": best_count,
            "delta": delta,
            "latest_rate": _ratio(latest_count, coverage),
            "best_rate": _ratio(best_count, coverage),
            "coverage": coverage,
        },
        "ai": _ai_readout(Path(ai_path), strict_existing=best_count, fallback_coverage=coverage),
        "release_safe": {
            "can_release": status in {"stable", "improved"},
            "reason": release_reason,
        },
        "next_actions": _next_actions(status),
    }
    if output_json:
        _write_json(payload, Path(output_json))
    if output_md:
        _write_markdown(payload, Path(output_md))
    return payload


def _strict_count(report: Mapping[str, Any]) -> int:
    records = _extract_records(report)
    if records:
        return count_strict_accepted(report)
    for root in _candidate_roots(report):
        for key in (
            "strict_accepted",
            "latest_strict_accepted_count",
            "previous_strict_accepted_count",
            "best_known_strict_accepted",
            "best_accepted",
            "latest_accepted",
        ):
            value = root.get(key)
            if value not in (None, ""):
                return _int_or_zero(value)
    return 0


def _case_count(report: Mapping[str, Any]) -> int:
    records = _extract_records(report)
    if records:
        return len(records)
    for root in _candidate_roots(report):
        for key in ("case_count", "coverage", "total", "total_count", "total_fen_items"):
            value = root.get(key)
            if value not in (None, ""):
                return _int_or_zero(value)
    return 0


def _ai_readout(ai_path: Path, *, strict_existing: int, fallback_coverage: int) -> dict[str, Any]:
    evidence = _load_optional(ai_path)
    if evidence.get("status") == MISSING_ARTIFACT:
        return {
            "status": MISSING_ARTIFACT,
            "coverage": 0,
            "strict_existing": strict_existing,
            "ai_consensus": 0,
            "ai_tie_break_resolved": 0,
            "ai_unreadable": 0,
            "ai_best_effort": 0,
        }
    payload = evidence.get("payload") if isinstance(evidence.get("payload"), Mapping) else {}
    records = _extract_records(payload)
    counts = {category: 0 for category in AI_CATEGORIES}
    for record in records:
        category = _ai_category(record)
        if category in counts:
            counts[category] += 1
    for root in _candidate_roots(payload):
        for category in AI_CATEGORIES:
            if root.get(category) not in (None, ""):
                counts[category] = _int_or_zero(root.get(category))
            count_key = f"{category}_count"
            if root.get(count_key) not in (None, ""):
                counts[category] = _int_or_zero(root.get(count_key))
    coverage = _first_positive(
        *(
            _int_or_zero(root.get(key))
            for root in _candidate_roots(payload)
            for key in ("coverage", "total", "total_count", "case_count", "record_count")
        ),
        len(records),
        sum(counts.values()),
        fallback_coverage,
    )
    strict_from_ai = _first_positive(
        *(
            _int_or_zero(root.get(key))
            for root in _candidate_roots(payload)
            for key in ("strict_existing", "strict_existing_count", "best_known_strict_accepted")
        ),
        strict_existing,
    )
    return {"coverage": coverage, "strict_existing": strict_from_ai, **counts}


def _ai_category(record: Mapping[str, Any]) -> str:
    for key in ("ai_category", "category", "status", "runtime_status", "source", "method"):
        value = str(record.get(key) or "").strip().lower()
        if value in AI_CATEGORIES:
            return value
    serialized = json.dumps(record, ensure_ascii=False).lower()
    for category in AI_CATEGORIES:
        if category in serialized:
            return category
    return ""


def _extract_records(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for root in _candidate_roots(report):
        for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
            value = root.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _candidate_roots(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    roots: list[Mapping[str, Any]] = [report]
    for key in ("summary", "strict", "candidate", "baseline", "ai", "quality_report", "chess_fen"):
        value = report.get(key)
        if isinstance(value, Mapping):
            roots.append(value)
            nested = value.get("chess_fen")
            if isinstance(nested, Mapping):
                roots.append(nested)
    return roots


def _next_actions(status: str) -> list[str]:
    if status == "regressed":
        return [
            "recover_lost_strict_cases",
            "build_ai_consensus_verification_queue",
            "build_tiebreak_square_review_queue",
        ]
    if status == "blocked":
        return ["restore_missing_required_reports", "rerun_strict_readiness_report"]
    if status == "improved":
        return ["review_baseline_update_candidate", "keep_ai_review_only_until_human_verified"]
    return ["keep_ai_review_only_until_human_verified", "run_release_regression_gate"]


def _load_required(path: Path) -> dict[str, Any]:
    evidence = _load_optional(path)
    if evidence.get("status") == MISSING_ARTIFACT:
        return evidence
    return evidence


def _load_optional(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"status": MISSING_ARTIFACT, "path": str(path), "payload": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        return {"status": MISSING_ARTIFACT, "path": str(path), "error": f"invalid_json:{error}", "payload": {}}
    if not isinstance(payload, Mapping):
        return {"status": MISSING_ARTIFACT, "path": str(path), "error": "json_root_not_object", "payload": {}}
    return {"status": "available", "path": str(path), "payload": payload}


def _input_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    result = {"status": evidence.get("status", ""), "path": evidence.get("path", "")}
    if evidence.get("error"):
        result["error"] = evidence.get("error")
    return result


def _write_json(payload: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_markdown(payload: Mapping[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    strict = payload.get("strict") if isinstance(payload.get("strict"), Mapping) else {}
    release_safe = payload.get("release_safe") if isinstance(payload.get("release_safe"), Mapping) else {}
    ai = payload.get("ai") if isinstance(payload.get("ai"), Mapping) else {}
    lines = [
        "# Chess FEN Strict Readiness",
        "",
        "## Executive Summary",
        "",
        f"- Status: `{payload.get('status', '')}`",
        f"- Release safe: `{release_safe.get('can_release', False)}` ({release_safe.get('reason', '')})",
        f"- Latest strict accepted: `{strict.get('latest_accepted', 0)}`",
        f"- Best strict accepted: `{strict.get('best_accepted', 0)}`",
        f"- Delta: `{strict.get('delta', 0)}`",
        "",
        "## AI Readout",
        "",
        f"- Coverage: `{ai.get('coverage', 0)}`",
        f"- Strict existing: `{ai.get('strict_existing', 0)}`",
        f"- AI consensus: `{ai.get('ai_consensus', 0)}`",
        f"- AI tie-break resolved: `{ai.get('ai_tie_break_resolved', 0)}`",
        f"- AI unreadable: `{ai.get('ai_unreadable', 0)}`",
        f"- AI best effort: `{ai.get('ai_best_effort', 0)}`",
        "",
        "## Next Actions",
        "",
    ]
    for action in payload.get("next_actions") or []:
        lines.append(f"- `{action}`")
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _ratio(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(part / total, 4)


def _first_positive(*values: int) -> int:
    for value in values:
        if value > 0:
            return value
    return 0


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate release-safe chess FEN strict readiness.")
    parser.add_argument("--latest", required=True)
    parser.add_argument("--best", required=True)
    parser.add_argument("--ai", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)
    payload = evaluate_chess_fen_strict_readiness(
        latest_path=args.latest,
        best_path=args.best,
        ai_path=args.ai,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 2 if payload["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
