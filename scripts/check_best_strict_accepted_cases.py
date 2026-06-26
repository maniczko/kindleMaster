from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.check_chess_fen_strict_regression_gate import _is_strict_accepted
from scripts.export_best_strict_accepted_baseline import _extract_records


SCHEMA = "kindlemaster.chess_fen.best_strict_accepted_check.v1"


def check_best_strict_accepted_cases(latest_report_path: str | Path, *, baseline_path: str | Path) -> dict[str, Any]:
    latest_path = Path(latest_report_path)
    baseline_file = Path(baseline_path)
    latest_report = _load_json(latest_path, label="latest_report")
    baseline = _load_json(baseline_file, label="baseline")
    latest_by_id = {_diagram_id(record): record for record in _extract_records(latest_report) if _diagram_id(record)}
    baseline_records = [record for record in baseline.get("records") or [] if isinstance(record, Mapping)]

    failures: list[dict[str, Any]] = []
    allowed_false_positives: list[dict[str, Any]] = []
    passed_count = 0

    for baseline_record in baseline_records:
        diagram_id = str(baseline_record.get("diagram_id") or "")
        latest_record = latest_by_id.get(diagram_id)
        if _is_allowed_previous_false_positive(baseline_record):
            allowed_false_positives.append({"diagram_id": diagram_id, "reason": _false_positive_payload(baseline_record).get("reason")})
            continue
        if baseline_record.get("previous_false_positive"):
            failures.append({"diagram_id": diagram_id, "reason": "invalid_previous_false_positive_exception"})
            continue
        if latest_record is None:
            failures.append({"diagram_id": diagram_id, "reason": "missing_latest_record"})
            continue
        if not _is_strict_accepted(latest_record):
            failures.append(
                {
                    "diagram_id": diagram_id,
                    "reason": "latest_not_strict_accepted",
                    "latest_status": str(latest_record.get("runtime_status") or latest_record.get("status") or ""),
                }
            )
            continue
        expected_fen = str(baseline_record.get("selected_value") or "").strip()
        current_fen = _selected_value(latest_record)
        if expected_fen and current_fen != expected_fen:
            failures.append(
                {
                    "diagram_id": diagram_id,
                    "reason": "strict_fen_mismatch",
                    "expected_fen": expected_fen,
                    "current_fen": current_fen,
                }
            )
            continue
        passed_count += 1

    payload = {
        "schema": SCHEMA,
        "status": "passed" if not failures else "failed",
        "latest_report": str(latest_path),
        "baseline_path": str(baseline_file),
        "summary": {
            "baseline_count": len(baseline_records),
            "passed_count": passed_count,
            "failure_count": len(failures),
            "allowed_previous_false_positive_count": len(allowed_false_positives),
        },
        "failures": failures,
        "allowed_previous_false_positives": allowed_false_positives,
    }
    return payload


def _is_allowed_previous_false_positive(record: Mapping[str, Any]) -> bool:
    if not record.get("previous_false_positive"):
        return False
    payload = _false_positive_payload(record)
    required_strings = ("expected_fen", "previous_fen", "reason")
    if any(not str(payload.get(key) or "").strip() for key in required_strings):
        return False
    if "current_fen" not in payload:
        return False
    square_diff = payload.get("square_diff")
    return isinstance(square_diff, list) and bool(square_diff)


def _false_positive_payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("previous_false_positive")
    if isinstance(value, Mapping):
        return value
    return record


def _load_json(path: Path, *, label: str) -> Mapping[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _diagram_id(record: Mapping[str, Any]) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _selected_value(record: Mapping[str, Any]) -> str:
    return str(record.get("selected_value") or record.get("fen") or record.get("full_fen") or record.get("candidate_fen") or "").strip()


def _emit_payload(payload: Mapping[str, Any], output_json: str | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text, file=sys.stderr if payload.get("status") != "passed" else sys.stdout)
    if output_json:
        output_path = Path(output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check latest report against best-known strict accepted FEN baseline.")
    parser.add_argument("latest_report_json")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)
    try:
        payload = check_best_strict_accepted_cases(args.latest_report_json, baseline_path=args.baseline)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        payload = {"schema": SCHEMA, "status": "failed", "error": error.__class__.__name__, "message": str(error)}
        _emit_payload(payload, args.output_json)
        return 2
    _emit_payload(payload, args.output_json)
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
