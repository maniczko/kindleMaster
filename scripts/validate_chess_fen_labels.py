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

    notes = str(record.get("notes") or "").strip().lower()
    if "placeholder" in notes or "fill fen manually" in notes:
        issues.append(_issue(line_number, record_id, "placeholder_notes"))

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
