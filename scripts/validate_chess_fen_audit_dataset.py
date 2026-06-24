from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


SCHEMA = "kindlemaster.chess_fen.audit_dataset.v1"
ALLOWED_SAMPLE_TYPES = {"false_positive", "cropped_board", "low_confidence_board", "negative_non_board"}
REQUIRED_REJECTION_REASONS = {
    "false_positive": {"false_positive_or_unrecognized", "board_visual_pattern_not_detected", "board_grid_not_detected"},
    "cropped_board": {"partial_board_crop_without_dense_board_evidence", "cropped_board"},
    "low_confidence_board": {"confidence_below_runtime_threshold", "piece_template_confidence_below_threshold"},
    "negative_non_board": {"board_grid_not_detected", "board_visual_pattern_not_detected", "image_board_requires_review"},
}


def validate_chess_fen_audit_dataset(dataset_path: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(dataset_path)
    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if not path.exists():
        payload = _payload(path, rows, [{"line": 0, "id": "", "code": "dataset_file_missing"}])
        _write_output(payload, output_path)
        return payload
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            issues.append({"line": line_number, "id": "", "code": "invalid_json"})
            continue
        if not isinstance(row, dict):
            issues.append({"line": line_number, "id": "", "code": "record_must_be_object"})
            continue
        rows.append(row)
        issues.extend(_record_issues(row, line_number=line_number))
    if not rows:
        issues.append({"line": 0, "id": "", "code": "no_audit_records"})
    payload = _payload(path, rows, issues)
    _write_output(payload, output_path)
    return payload


def _record_issues(row: dict[str, Any], *, line_number: int) -> list[dict[str, Any]]:
    record_id = str(row.get("id") or row.get("case_id") or "")
    issues: list[dict[str, Any]] = []
    sample_type = str(row.get("sample_type") or "").strip()
    if not record_id:
        issues.append(_issue(line_number, record_id, "id_missing"))
    if sample_type not in ALLOWED_SAMPLE_TYPES:
        issues.append(_issue(line_number, record_id, "sample_type_invalid", sample_type=sample_type))
    crop_path_value = str(row.get("crop_path") or "").strip()
    if not crop_path_value:
        issues.append(_issue(line_number, record_id, "crop_path_missing"))
    elif not Path(crop_path_value).exists():
        issues.append(_issue(line_number, record_id, "crop_path_missing_on_disk", crop_path=crop_path_value))
    expected_reason = str(row.get("expected_rejection_reason") or "").strip()
    if not expected_reason:
        issues.append(_issue(line_number, record_id, "expected_rejection_reason_missing"))
    elif sample_type in REQUIRED_REJECTION_REASONS and expected_reason not in REQUIRED_REJECTION_REASONS[sample_type]:
        issues.append(
            _issue(
                line_number,
                record_id,
                "expected_rejection_reason_mismatch",
                sample_type=sample_type,
                expected_rejection_reason=expected_reason,
            )
        )
    if sample_type in {"false_positive", "negative_non_board"} and str(row.get("expected_placement") or "").strip():
        issues.append(_issue(line_number, record_id, "negative_sample_must_not_have_expected_placement"))
    if sample_type in {"cropped_board", "low_confidence_board"} and not str(row.get("expected_placement") or "").strip():
        issues.append(_issue(line_number, record_id, "expected_placement_missing"))
    return issues


def _payload(path: Path, rows: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in rows:
        sample_type = str(row.get("sample_type") or "unknown")
        reason = str(row.get("expected_rejection_reason") or "unknown")
        by_type[sample_type] = by_type.get(sample_type, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "schema": SCHEMA,
        "status": "passed" if rows and not issues else "failed",
        "dataset_path": str(path),
        "record_count": len(rows),
        "issue_count": len(issues),
        "summary": {
            "by_sample_type": dict(sorted(by_type.items())),
            "by_expected_rejection_reason": dict(sorted(by_reason.items())),
        },
        "issues": issues,
    }


def _issue(line_number: int, record_id: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"line": line_number, "id": record_id, "code": code, **extra}


def _write_output(payload: dict[str, Any], output_path: str | Path | None) -> None:
    if not output_path:
        return
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate chess FEN false-positive/cropped-board audit dataset JSONL.")
    parser.add_argument("dataset")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    payload = validate_chess_fen_audit_dataset(args.dataset, output_path=args.output or None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
