from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import (  # noqa: E402
    KNOWN_BAD_EXPECTED_FENS,
    crop_sha256,
    has_square_diff_ack,
    infer_verification_source,
    is_ai_only_verification_source,
    is_human_verification_source,
    square_level_fen_diff,
)
from chess_position_recognizer import validate_fen  # noqa: E402


REVIEW_ONLY_STATUSES = {"needs_manual_fen", "placeholder", "draft", "review_required"}


def validate_chess_fen_labels(labels_path: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(labels_path)
    issues: list[dict[str, Any]] = []
    if not path.exists():
        result = {
            "status": "failed",
            "labels_path": str(path),
            "label_count": 0,
            "valid_label_count": 0,
            "issue_count": 1,
            "issues": [{"line": 0, "id": "", "code": "labels_file_missing"}],
        }
        _write_output(result, output_path)
        return result

    raw_lines = path.read_text(encoding="utf-8-sig").splitlines()
    rows: list[dict[str, Any]] = []
    row_line_numbers: list[int] = []
    for line_number, line in enumerate(raw_lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            issues.append({"line": line_number, "id": "", "code": "invalid_json"})
            continue
        if not isinstance(record, dict):
            issues.append({"line": line_number, "id": "", "code": "record_must_be_object"})
            continue
        rows.append(record)
        row_line_numbers.append(line_number)
        issues.extend(_record_issues(record, line_number=line_number))

    if not rows:
        issues.append({"line": 0, "id": "", "code": "no_label_records"})

    row_line_set = set(row_line_numbers)
    issue_lines = {int(issue.get("line") or 0) for issue in issues if int(issue.get("line") or 0) > 0}
    valid_label_count = max(0, len(rows) - len({line for line in issue_lines if line in row_line_set}))
    result = {
        "status": "passed" if rows and not issues else "failed",
        "labels_path": str(path),
        "label_count": len(rows),
        "valid_label_count": valid_label_count,
        "issue_count": len(issues),
        "issues": issues,
    }
    _write_output(result, output_path)
    return result


def _record_issues(record: dict[str, Any], *, line_number: int) -> list[dict[str, Any]]:
    record_id = str(record.get("id") or "")
    issues: list[dict[str, Any]] = []
    fen = str(record.get("fen") or "").strip()
    if not fen:
        issues.append(_issue(line_number, record_id, "fen_missing"))
    else:
        is_valid, fen_warnings = validate_fen(fen)
        if not is_valid:
            issues.append(_issue(line_number, record_id, "fen_invalid", warnings=fen_warnings))
        elif record_id in KNOWN_BAD_EXPECTED_FENS:
            known_diffs = square_level_fen_diff(KNOWN_BAD_EXPECTED_FENS[record_id], fen)
            if any(diff["square"] == "e5" for diff in known_diffs):
                issues.append(_issue(line_number, record_id, "known_bad_square_mismatch", square_diffs=known_diffs))

    raw_crop_path = str(record.get("crop_path") or record.get("source_crop_path") or "").strip()
    crop_path = Path(raw_crop_path) if raw_crop_path else None
    crop_exists = bool(crop_path and crop_path.exists())
    if not raw_crop_path:
        issues.append(_issue(line_number, record_id, "crop_path_missing"))
    elif crop_path is None or not crop_exists:
        issues.append(_issue(line_number, record_id, "crop_path_missing_on_disk", crop_path=raw_crop_path))

    verified_by = str(record.get("verified_by") or "").strip()
    if not verified_by:
        issues.append(_issue(line_number, record_id, "verified_by_missing"))

    verified_at = str(record.get("verified_at") or "").strip()
    if not verified_at:
        issues.append(_issue(line_number, record_id, "verified_at_missing"))
    elif not re.match(r"^\d{4}-\d{2}-\d{2}", verified_at):
        issues.append(_issue(line_number, record_id, "verified_at_invalid", verified_at=verified_at))

    label_status = str(record.get("label_status") or "").strip().lower()
    if not label_status:
        issues.append(_issue(line_number, record_id, "label_status_missing"))
    elif label_status != "verified":
        issues.append(_issue(line_number, record_id, "label_status_not_verified", label_status=label_status))
    if label_status in REVIEW_ONLY_STATUSES:
        issues.append(_issue(line_number, record_id, "review_only_label_status", label_status=label_status))

    notes = str(record.get("notes") or "").strip().lower()
    if "placeholder" in notes or "fill fen manually" in notes:
        issues.append(_issue(line_number, record_id, "placeholder_notes"))

    verification_source = infer_verification_source(record)
    is_legacy_manual = verification_source == "legacy_human_visual"
    if not verification_source:
        issues.append(_issue(line_number, record_id, "verification_source_missing"))
    elif is_ai_only_verification_source(verification_source):
        issues.append(_issue(line_number, record_id, "ai_only_verification_source", verification_source=verification_source))
    elif not is_human_verification_source(verification_source):
        issues.append(_issue(line_number, record_id, "verification_source_not_human", verification_source=verification_source))

    if not is_legacy_manual and record.get("human_verified") is not True:
        issues.append(_issue(line_number, record_id, "human_verified_missing"))

    if not is_legacy_manual and not has_square_diff_ack(record):
        issues.append(_issue(line_number, record_id, "square_diff_ack_missing"))

    if record.get("ai_requires_review") is True:
        issues.append(_issue(line_number, record_id, "ai_review_unresolved"))
    if isinstance(record.get("ai_ambiguous_squares"), list) and record.get("ai_ambiguous_squares"):
        issues.append(_issue(line_number, record_id, "ai_ambiguous_squares_unresolved"))
    if isinstance(record.get("ambiguous_squares"), list) and record.get("ambiguous_squares"):
        issues.append(_issue(line_number, record_id, "ambiguous_squares_unresolved"))
    for key in ("unresolved_ambiguity", "human_rejected", "review_required", "requires_review", "needs_review"):
        if record.get(key) is True:
            issues.append(_issue(line_number, record_id, "review_flag_unresolved", flag=key))

    declared_hash = str(record.get("crop_sha256") or "").strip().lower()
    if crop_exists:
        actual_hash = crop_sha256(crop_path)
        if not declared_hash:
            if not is_legacy_manual:
                issues.append(_issue(line_number, record_id, "crop_sha256_missing"))
        elif declared_hash != actual_hash:
            issues.append(_issue(line_number, record_id, "crop_sha256_mismatch"))

    if record.get("ai_assisted") and not is_legacy_manual and str(record.get("label_source") or "").startswith("ai_"):
        issues.append(_issue(line_number, record_id, "ai_suggestion_promoted_without_manual_fen"))

    return issues


def _issue(line_number: int, record_id: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"line": line_number, "id": record_id, "code": code, **extra}


def _write_output(result: dict[str, Any], output_path: str | Path | None) -> None:
    if not output_path:
        return
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate manually verified chess FEN label JSONL files.")
    parser.add_argument("labels")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    result = validate_chess_fen_labels(args.labels, output_path=args.output or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
