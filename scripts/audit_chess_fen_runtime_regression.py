from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BASELINE = Path("reports/chess_fen/fundamenty_pgn_board_legalization.json")
DEFAULT_SIDE_EVIDENCE = Path("reports/chess_fen/fundamenty_side_evidence.json")
DEFAULT_CURRENT = Path("reports/chess_fen/fundamenty_main_audit.json")
DEFAULT_OUTPUT_JSON = Path("reports/chess_fen/fen_runtime_regression_audit.json")
DEFAULT_OUTPUT_MD = Path("reports/chess_fen/fen_runtime_regression_audit.md")


def audit_chess_fen_runtime_regression(
    *,
    baseline_path: str | Path = DEFAULT_BASELINE,
    side_evidence_path: str | Path = DEFAULT_SIDE_EVIDENCE,
    current_path: str | Path = DEFAULT_CURRENT,
    output_json: str | Path = DEFAULT_OUTPUT_JSON,
    output_md: str | Path = DEFAULT_OUTPUT_MD,
) -> dict[str, Any]:
    reports = [
        _load_report("baseline", Path(baseline_path)),
        _load_report("side_evidence", Path(side_evidence_path)),
        _load_report("current", Path(current_path)),
    ]
    transitions = {
        "baseline_to_side_evidence": _transition_report(reports[0], reports[1]),
        "side_evidence_to_current": _transition_report(reports[1], reports[2]),
        "baseline_to_current": _transition_report(reports[0], reports[2]),
    }
    payload = {
        "schema_version": "kindlemaster.chess_fen_runtime_regression_audit.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "conclusion": _conclusion(transitions),
            "reports": [_report_summary(report) for report in reports],
        },
        "transitions": transitions,
    }
    output_json_path = Path(output_json)
    output_md_path = Path(output_md)
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md_path.write_text(_markdown(payload), encoding="utf-8")
    return payload


def _load_report(label: str, path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    chess_fen = (payload.get("quality_report") or {}).get("chess_fen") or payload.get("chess_fen") or {}
    records = [record for record in chess_fen.get("records") or [] if isinstance(record, dict)]
    return {
        "label": label,
        "path": str(path),
        "available": path.exists(),
        "diagram_count": int(chess_fen.get("diagram_count") or len(records) or 0),
        "fen_count": int(chess_fen.get("fen_count") or sum(1 for record in records if _accepted(record))),
        "manual_review_count": int(chess_fen.get("manual_review_count") or sum(1 for record in records if not _accepted(record))),
        "records": {_record_key(record): record for record in records},
    }


def _transition_report(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    stable_accepted = 0
    stable_review = 0
    before_records = before["records"]
    after_records = after["records"]
    for key, before_record in before_records.items():
        after_record = after_records.get(key)
        before_accepted = _accepted(before_record)
        after_accepted = _accepted(after_record or {})
        if before_accepted and after_accepted:
            stable_accepted += 1
        elif not before_accepted and not after_accepted:
            stable_review += 1
        elif before_accepted and not after_accepted:
            regressions.append(_regression_case(key, before_record, after_record))
        elif not before_accepted and after_accepted:
            improvements.append(_improvement_case(key, before_record, after_record))
    buckets = Counter(case["bucket"] for case in regressions)
    return {
        "from": before["label"],
        "to": after["label"],
        "accepted_delta": after["fen_count"] - before["fen_count"],
        "before_accepted_count": before["fen_count"],
        "after_accepted_count": after["fen_count"],
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
        "stable_accepted_count": stable_accepted,
        "stable_review_count": stable_review,
        "regression_buckets": dict(buckets),
        "regression_samples": regressions[:50],
        "improvement_samples": improvements[:25],
    }


def _regression_case(key: str, before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    after = after or {}
    return {
        "key": key,
        "page": after.get("page", before.get("page")),
        "filename": after.get("filename", before.get("filename")),
        "bucket": _regression_bucket(before, after),
        "before_method": before.get("method", ""),
        "after_method": after.get("method", ""),
        "before_fen": before.get("fen", ""),
        "after_fen": after.get("fen", ""),
        "after_full_fen": after.get("full_fen", ""),
        "before_side_to_move_status": before.get("side_to_move_status", ""),
        "after_side_to_move_status": after.get("side_to_move_status", ""),
        "before_side_to_move_evidence": before.get("side_to_move_evidence", ""),
        "after_side_to_move_evidence": after.get("side_to_move_evidence", ""),
        "before_warnings": sorted(str(item) for item in before.get("warnings") or []),
        "after_warnings": sorted(str(item) for item in after.get("warnings") or []),
    }


def _improvement_case(key: str, before: dict[str, Any], after: dict[str, Any] | None) -> dict[str, Any]:
    after = after or {}
    return {
        "key": key,
        "page": after.get("page", before.get("page")),
        "filename": after.get("filename", before.get("filename")),
        "method": after.get("method", ""),
        "fen": after.get("fen", ""),
        "warnings": sorted(str(item) for item in after.get("warnings") or []),
    }


def _regression_bucket(before: dict[str, Any], after: dict[str, Any]) -> str:
    if not after:
        return "record_missing_in_after_report"
    before_warnings = {str(item) for item in before.get("warnings") or []}
    after_warnings = {str(item) for item in after.get("warnings") or []}
    before_side_status = str(before.get("side_to_move_status") or "")
    after_side_status = str(after.get("side_to_move_status") or "")
    if "side_to_move_inferred" in after_warnings:
        return "side_to_move_inferred_gate"
    if (
        before_side_status == "explicit"
        and after_side_status != "explicit"
    ) or (
        {"side_to_move_marker_detected", "side_to_move_caption_detected"} & after_warnings
        and after_side_status != "explicit"
    ):
        return "side_to_move_evidence_lost_or_not_applied"
    if "verified_exact_crop_label_used" in before_warnings and "verified_exact_crop_label_used" not in after_warnings:
        return "verified_exact_label_not_reused"
    if str(before.get("placement") or "") and str(after.get("placement") or "") and before.get("placement") != after.get("placement"):
        return "placement_changed"
    if not after.get("placement"):
        return "placement_missing"
    if not after.get("board_detected"):
        return "diagram_not_detected"
    if any(warning.endswith("king_count_invalid") for warning in after_warnings):
        return "king_count_invalid"
    if after.get("requires_review") is True:
        return "review_gate_without_specific_bucket"
    return "unknown_regression"


def _accepted(record: dict[str, Any]) -> bool:
    return bool(record.get("fen")) and record.get("requires_review") is not True


def _record_key(record: dict[str, Any]) -> str:
    filename = str(record.get("filename") or "").strip()
    if filename:
        return filename
    page = str(record.get("page") or "").strip()
    diagram = str(record.get("diagram_index") or record.get("index") or "").strip()
    return f"page:{page}:diagram:{diagram}"


def _report_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "label": report["label"],
        "path": report["path"],
        "available": report["available"],
        "diagram_count": report["diagram_count"],
        "fen_count": report["fen_count"],
        "manual_review_count": report["manual_review_count"],
        "record_count": len(report["records"]),
    }


def _conclusion(transitions: dict[str, Any]) -> str:
    side_to_current = transitions["side_evidence_to_current"]
    buckets = side_to_current.get("regression_buckets") or {}
    if buckets.get("side_to_move_evidence_lost_or_not_applied"):
        return "regression_is_real_and_dominated_by_side_to_move_evidence_mapping"
    if side_to_current.get("regression_count"):
        return "regression_is_real_but_requires_bucket_review"
    return "no_accepted_to_review_regression_detected_between_side_evidence_and_current"


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Chess FEN Runtime Regression Audit",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- conclusion: `{payload['summary']['conclusion']}`",
        "",
        "## Reports",
        "",
    ]
    for report in payload["summary"]["reports"]:
        lines.append(
            f"- `{report['label']}`: FEN `{report['fen_count']}/{report['diagram_count']}`, "
            f"review `{report['manual_review_count']}`, path `{report['path']}`"
        )
    lines.extend(["", "## Transitions", ""])
    for name, transition in payload["transitions"].items():
        lines.extend(
            [
                f"### {name}",
                "",
                f"- accepted delta: `{transition['accepted_delta']}`",
                f"- accepted-to-review regressions: `{transition['regression_count']}`",
                f"- review-to-accepted improvements: `{transition['improvement_count']}`",
                f"- buckets: `{transition['regression_buckets']}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit accepted-to-review FEN regressions between runtime reports.")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--side-evidence", default=str(DEFAULT_SIDE_EVIDENCE))
    parser.add_argument("--current", default=str(DEFAULT_CURRENT))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()
    payload = audit_chess_fen_runtime_regression(
        baseline_path=args.baseline,
        side_evidence_path=args.side_evidence,
        current_path=args.current,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
