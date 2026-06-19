from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_workflow import REVIEW_ONLY_WORKFLOW_STATES
from chess_position_recognizer import validate_fen


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

    raw_crop_path = str(record.get("crop_path") or record.get("source_crop_path") or "").strip()
    crop_path = Path(raw_crop_path) if raw_crop_path else None
    if not raw_crop_path:
        issues.append(_issue(line_number, record_id, "crop_path_missing"))
    elif crop_path is None or not crop_path.exists():
        issues.append(_issue(line_number, record_id, "crop_path_missing_on_disk", crop_path=raw_crop_path))
    else:
        expected_sha256 = str(record.get("crop_sha256") or record.get("sha256") or "").strip().lower()
        if not expected_sha256:
            issues.append(_issue(line_number, record_id, "crop_sha256_missing"))
        else:
            actual_sha256 = _sha256_file(crop_path)
            if actual_sha256 != expected_sha256:
                issues.append(
                    _issue(
                        line_number,
                        record_id,
                        "crop_sha256_mismatch",
                        expected_sha256=expected_sha256,
                        actual_sha256=actual_sha256,
                    )
                )

    if not _truthy(record.get("human_verified")):
        issues.append(_issue(line_number, record_id, "human_verified_missing"))
    if not _truthy(record.get("square_diff_ack")):
        issues.append(_issue(line_number, record_id, "square_diff_ack_missing"))
    verification_source = str(record.get("verification_source") or "").strip()
    if verification_source != "human_visual":
        issues.append(_issue(line_number, record_id, "verification_source_invalid", verification_source=verification_source))

    verified_by = str(record.get("verified_by") or "").strip()
    if not verified_by:
        issues.append(_issue(line_number, record_id, "verified_by_missing"))

    verified_at = str(record.get("verified_at") or "").strip()
    if not verified_at:
        issues.append(_issue(line_number, record_id, "verified_at_missing"))
    elif not re.match(r"^\d{4}-\d{2}-\d{2}", verified_at):
        issues.append(_issue(line_number, record_id, "verified_at_invalid", verified_at=verified_at))

    label_status = str(record.get("label_status") or "").strip().lower()
    if label_status in REVIEW_ONLY_STATUSES:
        issues.append(_issue(line_number, record_id, "review_only_label_status", label_status=label_status))

    workflow_state = str(record.get("workflow_state") or "").strip()
    if workflow_state in REVIEW_ONLY_WORKFLOW_STATES:
        issues.append(_issue(line_number, record_id, "review_only_workflow_state", workflow_state=workflow_state))

    notes = str(record.get("notes") or "").strip().lower()
    if "placeholder" in notes or "fill fen manually" in notes:
        issues.append(_issue(line_number, record_id, "placeholder_notes"))

    return issues


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "tak"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
