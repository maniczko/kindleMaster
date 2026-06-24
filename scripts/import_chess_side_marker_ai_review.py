from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from openai_chess_fen_reviewer import FORBIDDEN_AUTHORITY_FIELDS, POLICY_ACKNOWLEDGEMENT


def import_side_marker_ai_review(
    input_jsonl: str | Path,
    responses_jsonl: str | Path,
    *,
    output_jsonl: str | Path,
) -> dict[str, Any]:
    source = Path(input_jsonl)
    responses_path = Path(responses_jsonl)
    target = Path(output_jsonl)
    rows = _read_jsonl(source)
    responses = {_response_id(row): _parse_response(row) for row in _read_jsonl(responses_path) if _response_id(row)}
    output_rows: list[dict[str, Any]] = []
    matched = 0
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        parsed = responses.get(row_id) or {}
        updated = dict(row)
        if parsed:
            matched += 1
            _apply_ai_review(updated, parsed)
        else:
            updated.setdefault("ai_reviewed_status", "missing_response")
        # Preserve manual authority fields exactly.
        updated["human_verified"] = bool(row.get("human_verified") is True)
        updated["human_side_to_move"] = str(row.get("human_side_to_move") or "")
        updated["verified_by"] = str(row.get("verified_by") or "")
        updated["verified_at"] = str(row.get("verified_at") or "")
        output_rows.append(updated)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in output_rows), encoding="utf-8")
    status_counts = Counter(str(row.get("ai_reviewed_status") or "unknown") for row in output_rows)
    side_counts = Counter(str(row.get("ai_reviewed_side_to_move") or "none") for row in output_rows)
    issue_counts = Counter(issue for row in output_rows for issue in row.get("ai_reviewed_issues", []))
    summary = {
        "status": "ok",
        "input_jsonl": str(source),
        "responses_jsonl": str(responses_path),
        "output_jsonl": str(target),
        "row_count": len(rows),
        "matched_response_count": matched,
        "status_counts": dict(sorted(status_counts.items())),
        "side_counts": dict(sorted(side_counts.items())),
        "issue_counts": dict(issue_counts.most_common(30)),
        "policy": "ai_side_marker_review_only_no_human_verification",
    }
    target.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _apply_ai_review(row: dict[str, Any], parsed: dict[str, Any]) -> None:
    issues = _review_issues(parsed)
    side = str(parsed.get("side_to_move") or "unknown").strip().lower()
    if side not in {"w", "b", "unknown"}:
        side = "unknown"
        issues.append("ai_side_to_move_invalid")
    row["ai_reviewed_status"] = "reviewed"
    row["ai_reviewed_side_to_move"] = side
    row["ai_reviewed_marker_source"] = _enum(parsed.get("marker_source"), {"visual_marker", "ocr_symbol", "caption", "none", "ambiguous"}, "ambiguous")
    row["ai_reviewed_marker_role"] = str(parsed.get("marker_role") or "")
    row["ai_reviewed_marker_symbol"] = str(parsed.get("marker_symbol") or "")
    row["ai_reviewed_confidence"] = _clamp(parsed.get("confidence"))
    row["ai_reviewed_evidence_level"] = _enum(parsed.get("evidence_level"), {"clear", "ambiguous", "insufficient_crop", "no_marker"}, "ambiguous")
    row["ai_reviewed_requires_human_review"] = True
    row["ai_reviewed_reason"] = str(parsed.get("reason") or "")
    row["ai_reviewed_policy_acknowledgement"] = str(parsed.get("policy_acknowledgement") or "")
    row["ai_reviewed_issues"] = sorted(dict.fromkeys(issues))


def _review_issues(parsed: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if str(parsed.get("policy_acknowledgement") or "") != POLICY_ACKNOWLEDGEMENT:
        issues.append("ai_policy_acknowledgement_missing")
    if any(field in parsed for field in FORBIDDEN_AUTHORITY_FIELDS | {"fen", "canonical_fen", "human_verified"}):
        issues.append("ai_authoritative_field_ignored")
    if bool(parsed.get("requires_human_review")) is False:
        issues.append("ai_requires_human_review_forced")
    return issues


def _response_id(row: dict[str, Any]) -> str:
    custom_id = str(row.get("custom_id") or row.get("id") or "")
    if ":" in custom_id:
        return custom_id.rsplit(":", 1)[-1]
    return custom_id


def _parse_response(row: dict[str, Any]) -> dict[str, Any]:
    if {"side_to_move", "marker_source", "policy_acknowledgement"}.issubset(row):
        return row
    body = row.get("body") if isinstance(row.get("body"), dict) else None
    response = row.get("response") if isinstance(row.get("response"), dict) else None
    if body is None and response is not None:
        body = response.get("body") if isinstance(response.get("body"), dict) else None
    if body is None:
        return {}
    output_text = _extract_text(body)
    if not output_text:
        return {}
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_text(body: dict[str, Any]) -> str:
    direct = body.get("output_text")
    if isinstance(direct, str):
        return direct.strip()
    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                return content["text"].strip()
    return ""


def _enum(value: Any, allowed: set[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _clamp(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"Expected object row at {path}:{line_number}")
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import OpenAI side-marker review responses into ai_reviewed_* fields.")
    parser.add_argument("input_jsonl")
    parser.add_argument("responses_jsonl")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    summary = import_side_marker_ai_review(args.input_jsonl, args.responses_jsonl, output_jsonl=args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
