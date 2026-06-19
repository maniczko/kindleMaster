from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen  # noqa: E402
from openai_chess_fen_reviewer import FORBIDDEN_AUTHORITY_FIELDS, POLICY_ACKNOWLEDGEMENT  # noqa: E402


def import_chess_fen_label_assist(
    candidate_labels_path: str | Path,
    assist_responses_path: str | Path,
    *,
    output_dir: str | Path = "reports/chess_fen/label_assist_import/latest",
) -> dict[str, Any]:
    """Merge OpenAI/human label-assist responses into a review-only draft.

    The output deliberately does not create accepted corpus labels. Suggested
    FEN values are stored as `ai_suggested_fen`; `fen`, `verified_by`, and
    `verified_at` remain empty until a human verifies the position.
    """
    candidate_path = Path(candidate_labels_path)
    responses_path = Path(assist_responses_path)
    candidates = _read_jsonl(candidate_path)
    responses = _read_jsonl(responses_path)
    suggestions = {_response_id(row): _parse_response(row) for row in responses if _response_id(row)}

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    draft_rows: list[dict[str, Any]] = []
    unmatched_response_ids = set(suggestions)
    invalid_suggestion_count = 0
    approved_suggestion_count = 0
    ready_for_manual_verification_count = 0
    matched_response_count = 0

    for row in candidates:
        row_id = str(row.get("id") or "")
        suggestion = suggestions.get(row_id) or {}
        if row_id in suggestions:
            matched_response_count += 1
        if row_id:
            unmatched_response_ids.discard(row_id)
        suggested_fen = str(suggestion.get("suggested_fen") or suggestion.get("corrected_fen") or "").strip()
        is_valid, fen_warnings = validate_fen(suggested_fen) if suggested_fen else (False, ["fen_missing"])
        issues = _normalized_ai_issues(suggestion, suggested_fen_valid=is_valid, suggested_fen=bool(suggested_fen))
        ambiguous_squares = _string_list(suggestion.get("ambiguous_squares"))
        approved = bool(suggestion.get("approved")) or str(suggestion.get("review_opinion") or "") == "supports_candidate"
        requires_review = bool(suggestion.get("requires_review", True)) or bool(ambiguous_squares)
        review_opinion = str(suggestion.get("review_opinion") or "").strip() or _review_opinion(
            approved=approved,
            requires_review=requires_review,
            issues=issues,
            ambiguous_squares=ambiguous_squares,
        )
        if approved:
            approved_suggestion_count += 1
        if suggested_fen and not is_valid:
            invalid_suggestion_count += 1
        if approved and suggested_fen and is_valid and not requires_review:
            ready_for_manual_verification_count += 1

        draft_row = {
            "id": row_id,
            "source_pdf": row.get("source_pdf", ""),
            "page": row.get("page"),
            "diagram_index": row.get("diagram_index"),
            "crop_path": row.get("crop_path", ""),
            "aid_path": row.get("aid_path", ""),
            "fen": "",
            "ai_suggested_fen": suggested_fen if is_valid else "",
            "ai_approved": approved,
            "ai_review_opinion": review_opinion,
            "ai_requires_review": requires_review,
            "ai_confidence": _clamp(suggestion.get("confidence")),
            "ai_ambiguous_squares": ambiguous_squares,
            "ai_square_diffs": _square_diff_list(suggestion.get("square_diffs")),
            "ai_side_to_move": _side_to_move_evidence(suggestion.get("side_to_move")),
            "ai_cannot_verify_reason": str(suggestion.get("cannot_verify_reason") or ""),
            "ai_evidence_level": str(suggestion.get("evidence_level") or ""),
            "ai_crop_quality_notes": _string_list(suggestion.get("crop_quality_notes")),
            "ai_policy_acknowledgement": str(suggestion.get("policy_acknowledgement") or ""),
            "ai_issues": issues,
            "ai_notes": str(suggestion.get("notes") or ""),
            "ai_fen_warnings": fen_warnings if suggested_fen else [],
            "label_status": "needs_manual_fen",
            "verified_by": "",
            "verified_at": "",
            "human_verified": False,
            "notes": "AI label-assist suggestion only. Fill fen/verified_by/verified_at manually after checking the crop.",
            "accepted_for_corpus": False,
        }
        draft_rows.append(draft_row)

    draft_path = target / "manual_verification_draft.jsonl"
    summary_path = target / "label_assist_import_summary.json"
    _write_jsonl(draft_path, draft_rows)
    summary = {
        "status": "ok",
        "accepted_for_corpus": False,
        "candidate_labels": str(candidate_path),
        "assist_responses": str(responses_path),
        "manual_verification_draft": str(draft_path),
        "candidate_count": len(candidates),
        "response_count": len(responses),
        "matched_response_count": matched_response_count,
        "approved_suggestion_count": approved_suggestion_count,
        "ready_for_manual_verification_count": ready_for_manual_verification_count,
        "invalid_suggestion_count": invalid_suggestion_count,
        "ai_square_diff_count": sum(len(row.get("ai_square_diffs") or []) for row in draft_rows),
        "ai_cannot_verify_count": sum(1 for row in draft_rows if row.get("ai_cannot_verify_reason")),
        "ai_policy_acknowledgement_missing_count": sum(
            1 for row in draft_rows if "ai_policy_acknowledgement_missing" in (row.get("ai_issues") or [])
        ),
        "unmatched_response_ids": sorted(unmatched_response_ids),
        "policy": "ai_label_assist_review_only_requires_manual_verification",
        "next_actions": [
            "open manual_verification_draft.jsonl",
            "for each trusted suggestion copy ai_suggested_fen into fen after checking the crop",
            "fill verified_by and verified_at",
            "run scripts/validate_chess_fen_labels.py on the verified copy",
            "run profile readiness/corpus gates before adding it to the manifest",
        ],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _response_id(row: dict[str, Any]) -> str:
    custom_id = str(row.get("custom_id") or row.get("id") or "")
    if ":" in custom_id:
        return custom_id.rsplit(":", 1)[-1]
    return custom_id


def _parse_response(row: dict[str, Any]) -> dict[str, Any]:
    if {"approved", "corrected_fen", "requires_review"}.issubset(row):
        return row
    if {"review_opinion", "suggested_fen", "requires_review"}.issubset(row):
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
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str):
                return content["text"].strip()
    return ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _square_diff_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    diffs: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        diffs.append(
            {
                "square": str(item.get("square") or ""),
                "candidate_piece": str(item.get("candidate_piece") or ""),
                "observed_piece": str(item.get("observed_piece") or item.get("manual_piece") or ""),
                "confidence": _clamp(item.get("confidence")),
                "reason": str(item.get("reason") or ""),
            }
        )
    return diffs


def _side_to_move_evidence(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        side = str(value.get("value") or "unknown").strip().lower()
        evidence = str(value.get("evidence") or "none").strip().lower()
        return {
            "value": side if side in {"w", "b", "unknown"} else "unknown",
            "evidence": evidence if evidence in {"marker", "caption", "inferred", "none"} else "none",
            "confidence": _clamp(value.get("confidence")),
        }
    side = str(value or "unknown").strip().lower()
    return {
        "value": side if side in {"w", "b", "unknown"} else "unknown",
        "evidence": "inferred" if side in {"w", "b"} else "none",
        "confidence": 0.0,
    }


def _normalized_ai_issues(suggestion: dict[str, Any], *, suggested_fen_valid: bool, suggested_fen: bool) -> list[str]:
    issues = _string_list(suggestion.get("issues"))
    if suggested_fen and not suggested_fen_valid:
        issues.append("ai_suggested_fen_invalid")
    if _string_list(suggestion.get("ambiguous_squares")) and not bool(suggestion.get("requires_review", True)):
        issues.append("ambiguous_squares_require_manual_review")
    if str(suggestion.get("policy_acknowledgement") or "") != POLICY_ACKNOWLEDGEMENT:
        issues.append("ai_policy_acknowledgement_missing")
    if any(field in suggestion for field in FORBIDDEN_AUTHORITY_FIELDS):
        issues.append("ai_authoritative_field_ignored")
    return sorted(dict.fromkeys(issues))


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, number))


def _review_opinion(*, approved: bool, requires_review: bool, issues: list[str], ambiguous_squares: list[str]) -> str:
    if approved and not requires_review and not issues and not ambiguous_squares:
        return "supports_candidate"
    if issues or ambiguous_squares:
        return "flags_candidate"
    return "uncertain"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import OpenAI/human chess FEN label-assist responses as manual-verification drafts.")
    parser.add_argument("--candidate-labels", required=True)
    parser.add_argument("--assist-responses", required=True)
    parser.add_argument("--output-dir", default="reports/chess_fen/label_assist_import/latest")
    args = parser.parse_args()

    result = import_chess_fen_label_assist(
        args.candidate_labels,
        args.assist_responses,
        output_dir=args.output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
