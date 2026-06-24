from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen
from openai_chess_fen_reviewer import POLICY_ACKNOWLEDGEMENT


def analyze_chess_ai_fen_recovery_plan(
    ai_autoread_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(ai_autoread_dir)
    target = Path(output_dir) if output_dir else source
    target.mkdir(parents=True, exist_ok=True)
    fen_rows = _read_jsonl(source / "ai_fen_readout.jsonl")
    requests = _requests_by_id(source / "ai_autoread_requests.jsonl")
    responses = _responses_by_id(source / "ai_autoread_responses.jsonl")

    cases = [_classify_case(row, responses.get(str(row.get("id") or ""), [])) for row in fen_rows]
    tie_break_requests = [
        _followup_request(
            requests.get(str(case["id"])),
            case,
            responses.get(str(case["id"]), []),
            variant="tie_break_high_reasoning",
        )
        for case in cases
        if case["recommendation"] == "run_tie_break_high_reasoning"
    ]
    retry_requests = [
        _followup_request(
            requests.get(str(case["id"])),
            case,
            responses.get(str(case["id"]), []),
            variant="enhanced_vision_retry",
        )
        for case in cases
        if case["recommendation"] == "run_enhanced_vision_retry"
    ]
    tie_break_requests = [request for request in tie_break_requests if request]
    retry_requests = [request for request in retry_requests if request]

    paths = {
        "cases_jsonl": target / "strict_fen_recovery_cases.jsonl",
        "summary_json": target / "strict_fen_recovery_plan.json",
        "summary_md": target / "strict_fen_recovery_plan.md",
        "tie_break_requests": target / "ai_fen_tie_break_requests.jsonl",
        "unreadable_retry_requests": target / "ai_fen_unreadable_retry_requests.jsonl",
    }
    summary = _summary(cases)
    summary.update(
        {
            "status": "ok",
            "mode": "ai_fen_recovery_plan",
            "release_safe": False,
            "source_dir": str(source),
            "output_dir": str(target),
            "case_count": len(cases),
            "tie_break_request_count": len(tie_break_requests),
            "unreadable_retry_request_count": len(retry_requests),
            "artifacts": {key: str(value) for key, value in paths.items()},
            "policy": "ai_autoread_evidence_only_no_runtime_promotion",
        }
    )
    _write_jsonl(paths["cases_jsonl"], cases)
    _write_jsonl(paths["tie_break_requests"], tie_break_requests)
    _write_jsonl(paths["unreadable_retry_requests"], retry_requests)
    paths["summary_json"].write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["summary_md"].write_text(_markdown(summary, cases), encoding="utf-8")
    return summary


def _classify_case(row: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    status = str(row.get("ai_readout_status") or "")
    ai_fen = str(row.get("ai_fen") or "").strip()
    placement = ai_fen.split()[0] if ai_fen else ""
    side = ai_fen.split()[1] if len(ai_fen.split()) >= 2 else str(row.get("ai_side_to_move") or "unknown")
    deterministic_placement = str(row.get("deterministic_placement") or "").strip()
    placement_matches = bool(placement and deterministic_placement and placement == deterministic_placement)
    fen_valid, fen_warnings = validate_fen(ai_fen) if ai_fen else (False, ["fen_missing"])
    markers = _marker_evidence(row)
    marker_sides = sorted({item["side"] for item in markers})
    marker_matches_ai = bool(side in {"w", "b"} and marker_sides == [side])
    marker_conflict = len(marker_sides) > 1
    if status == "strict_existing":
        recommendation = "already_strict_accepted"
    elif status == "ai_consensus" and fen_valid and placement_matches and marker_matches_ai:
        recommendation = "candidate_deterministic_marker_rule"
    elif status == "ai_consensus" and marker_conflict:
        recommendation = "needs_marker_conflict_review"
    elif status == "ai_consensus":
        recommendation = "needs_non_marker_evidence_or_exact_label"
    elif status == "ai_readout_conflict":
        recommendation = "run_tie_break_high_reasoning"
    elif status == "ai_tie_break_resolved":
        recommendation = "tie_break_evidence_needs_rule_or_exact_label"
    elif status == "ai_retry_resolved":
        recommendation = "retry_evidence_needs_rule_or_exact_label"
    elif status == "ai_readout_unreadable":
        recommendation = "run_enhanced_vision_retry"
    elif status == "ai_best_effort":
        recommendation = "exact_label_or_tie_break_required"
    else:
        recommendation = "manual_or_exact_label_required"
    return {
        "id": str(row.get("id") or ""),
        "page": row.get("page"),
        "filename": row.get("filename", ""),
        "ai_readout_status": status,
        "ai_fen": ai_fen,
        "ai_side_to_move": side,
        "fen_valid": fen_valid,
        "fen_warnings": fen_warnings,
        "deterministic_placement": deterministic_placement,
        "placement_matches_deterministic": placement_matches,
        "marker_count": len(markers),
        "marker_sides": marker_sides,
        "marker_roles": [item["role"] for item in markers],
        "marker_conflict": marker_conflict,
        "marker_matches_ai_side": marker_matches_ai,
        "recommendation": recommendation,
        "candidate_response_count": len(responses),
        "response_fens": sorted({str(item.get("ai_fen") or "") for item in responses if str(item.get("ai_fen") or "")}),
        "release_safe": False,
    }


def _marker_evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    markers = []
    for item in row.get("side_marker_candidates") or []:
        if not isinstance(item, dict):
            continue
        side = str(item.get("side_candidate") or item.get("detected_side") or "")
        if side not in {"w", "b"}:
            continue
        markers.append(
            {
                "role": str(item.get("role") or ""),
                "side": side,
                "shape": str(item.get("detected_shape") or ""),
                "score": _float(item.get("score")),
                "bbox": item.get("bbox") or [],
            }
        )
    return markers


def _followup_request(
    base_request: dict[str, Any] | None,
    case: dict[str, Any],
    responses: list[dict[str, Any]],
    *,
    variant: str,
) -> dict[str, Any] | None:
    if not base_request:
        return None
    request = json.loads(json.dumps(base_request))
    request["custom_id"] = f"fen::{case['id']}::{variant}"
    body = request.get("body") if isinstance(request.get("body"), dict) else {}
    body["instructions"] = _followup_instructions(variant)
    body["text"] = {
        "format": {
            "type": "json_schema",
            "name": "chess_ai_fen_followup_response",
            "schema": _response_schema(),
            "strict": True,
        }
    }
    input_items = body.get("input") if isinstance(body.get("input"), list) else []
    if input_items and isinstance(input_items[0], dict):
        content = input_items[0].get("content") if isinstance(input_items[0].get("content"), list) else []
        if content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
            try:
                context = json.loads(content[0]["text"])
            except json.JSONDecodeError:
                context = {}
            context.update(
                {
                    "variant": variant,
                    "previous_ai_candidates": responses,
                    "current_recommendation": case["recommendation"],
                    "policy_acknowledgement_required": POLICY_ACKNOWLEDGEMENT,
                }
            )
            content[0]["text"] = json.dumps(context, ensure_ascii=False)
    return request


def _followup_instructions(variant: str) -> str:
    if variant == "tie_break_high_reasoning":
        return (
            "Resolve a conflict between previous AI FEN reads for a chess diagram. "
            "Use the image, deterministic placement if present, marker probes, and previous candidates. "
            "Return exactly one full FEN only if visible evidence supports it; otherwise return ai_readout_unreadable. "
            "This is review-only evidence and must not claim human verification."
        )
    return (
        "Retry reading an unreadable chess diagram with extra care. Inspect the board crop and context for piece placement "
        "and side-to-move marker. Return a full FEN only when visible; otherwise return ai_readout_unreadable. "
        "This is review-only evidence and must not claim human verification."
    )


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "readout_status": {"type": "string", "enum": ["ai_readout_complete", "ai_readout_unreadable"]},
            "ai_fen": {"type": "string"},
            "placement": {"type": "string"},
            "side_to_move": {"type": "string", "enum": ["w", "b", "unknown"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string"},
            "policy_acknowledgement": {"type": "string", "enum": [POLICY_ACKNOWLEDGEMENT]},
        },
        "required": ["id", "readout_status", "ai_fen", "placement", "side_to_move", "confidence", "reason", "policy_acknowledgement"],
    }


def _requests_by_id(path: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for row in _read_jsonl(path):
        custom_id = str(row.get("custom_id") or "")
        parts = custom_id.split("::")
        if len(parts) >= 3 and parts[0] == "fen" and parts[2] == "direct_read":
            result[parts[1]] = row
    return result


def _responses_by_id(path: Path) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_jsonl(path):
        custom_id = str(row.get("custom_id") or "")
        parts = custom_id.split("::")
        if len(parts) < 3 or parts[0] != "fen":
            continue
        parsed = _parse_response(row)
        parsed["variant"] = parts[2]
        if parsed.get("ai_fen") or parsed.get("fen") or parsed.get("readout_status"):
            parsed["ai_fen"] = str(parsed.get("ai_fen") or parsed.get("fen") or "")
            groups[parts[1]].append(parsed)
    return groups


def _parse_response(row: dict[str, Any]) -> dict[str, Any]:
    if "readout_status" in row:
        return dict(row)
    body = row.get("body") if isinstance(row.get("body"), dict) else None
    response = row.get("response") if isinstance(row.get("response"), dict) else None
    if body is None and response is not None:
        body = response.get("body") if isinstance(response.get("body"), dict) else None
    if body is None:
        return {}
    text = body.get("output_text")
    if not isinstance(text, str):
        for item in body.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    text = content["text"]
                    break
    try:
        parsed = json.loads(str(text or ""))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_status = Counter(case["ai_readout_status"] for case in cases)
    by_recommendation = Counter(case["recommendation"] for case in cases)
    return {
        "strict_existing_count": by_status.get("strict_existing", 0),
        "ai_consensus_count": by_status.get("ai_consensus", 0),
        "ai_conflict_count": by_status.get("ai_readout_conflict", 0),
        "ai_unreadable_count": by_status.get("ai_readout_unreadable", 0),
        "ai_best_effort_count": by_status.get("ai_best_effort", 0),
        "status_counts": dict(by_status),
        "recommendation_counts": dict(by_recommendation),
        "candidate_deterministic_marker_rule_count": by_recommendation.get("candidate_deterministic_marker_rule", 0),
        "tie_break_required_count": by_recommendation.get("run_tie_break_high_reasoning", 0),
        "enhanced_retry_required_count": by_recommendation.get("run_enhanced_vision_retry", 0),
    }


def _markdown(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    lines = [
        "# AI FEN Strict Recovery Plan",
        "",
        "This report is audit/evidence only. It does not promote AI output to canonical FEN.",
        "",
        "## Summary",
        "",
        f"- Cases: {summary['case_count']}",
        f"- Strict existing: {summary['strict_existing_count']}",
        f"- AI consensus: {summary['ai_consensus_count']}",
        f"- Deterministic marker-rule candidates: {summary['candidate_deterministic_marker_rule_count']}",
        f"- Tie-break required: {summary['tie_break_required_count']}",
        f"- Enhanced retry required: {summary['enhanced_retry_required_count']}",
        "",
        "## Recommendations",
        "",
    ]
    for key, value in sorted(summary["recommendation_counts"].items()):
        lines.append(f"- `{key}`: {value}")
    examples = [case for case in cases if case["recommendation"] == "candidate_deterministic_marker_rule"][:20]
    if examples:
        lines.extend(["", "## First Marker-Rule Candidates", ""])
        for case in examples:
            lines.append(f"- `{case['id']}` page={case.get('page')} side={case['ai_side_to_move']} roles={case['marker_roles']}")
    return "\n".join(lines) + "\n"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze AI FEN readout rows and build a strict recovery work plan.")
    parser.add_argument("ai_autoread_dir")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)
    summary = analyze_chess_ai_fen_recovery_plan(args.ai_autoread_dir, output_dir=args.output_dir or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
