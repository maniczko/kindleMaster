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


SCHEMA = "kindlemaster.chess_fen.best_strict_accepted_baseline.v1"


def export_best_strict_accepted_baseline(report_path: str | Path, *, output_path: str | Path) -> dict[str, Any]:
    source = Path(report_path)
    report = _load_json(source)
    records = _baseline_records(report, report_source=str(source))
    payload = {
        "schema": SCHEMA,
        "report_source": str(source),
        "accepted_count": len(records),
        "records": records,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _baseline_records(report: Mapping[str, Any], *, report_source: str) -> list[dict[str, Any]]:
    diff_cases = _diff_cases(report)
    if diff_cases:
        return _baseline_records_from_diff(diff_cases, report_source=report_source)
    records: list[dict[str, Any]] = []
    for record in _extract_records(report):
        if not _is_strict_accepted(record):
            continue
        records.append(_baseline_record_from_report(record, report_source=report_source))
    return sorted(records, key=lambda item: (_int_or_zero(item.get("page")), str(item.get("diagram_id") or "")))


def _baseline_records_from_diff(cases: list[Mapping[str, Any]], *, report_source: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for case in cases:
        if str(case.get("previous_status") or "").lower() != "strict_accepted":
            continue
        selected_value = str(case.get("previous_selected_value") or case.get("previous_candidate_fen") or "").strip()
        if not selected_value:
            continue
        records.append(
            {
                "diagram_id": str(case.get("diagram_id") or ""),
                "page": _int_or_zero(case.get("page")),
                "selected_value": selected_value,
                "runtime_status": str(case.get("previous_runtime_status") or case.get("previous_status") or ""),
                "source": str(case.get("previous_status") or "strict_report_diff"),
                "crop_hash": str(case.get("crop_hash") or case.get("source_crop_hash") or ""),
                "report_source": report_source,
            }
        )
    return sorted(records, key=lambda item: (_int_or_zero(item.get("page")), str(item.get("diagram_id") or "")))


def _baseline_record_from_report(record: Mapping[str, Any], *, report_source: str) -> dict[str, Any]:
    return {
        "diagram_id": _diagram_id(record),
        "page": _int_or_zero(record.get("page", record.get("page_number", 0))),
        "selected_value": _selected_value(record),
        "runtime_status": str(record.get("runtime_status") or record.get("status") or ""),
        "source": str(record.get("source") or record.get("method") or record.get("label_source") or ""),
        "crop_hash": str(record.get("crop_hash") or record.get("source_crop_hash") or record.get("crop_sha256") or ""),
        "report_source": report_source,
    }


def _load_json(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, Mapping):
        raise ValueError("report must be a JSON object")
    return payload


def _extract_records(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for root in _candidate_roots(report):
        for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
            value = root.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _diff_cases(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = report.get("cases")
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _candidate_roots(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    roots: list[Mapping[str, Any]] = [report]
    for key in ("summary", "quality_report", "chess_fen"):
        value = report.get(key)
        if isinstance(value, Mapping):
            roots.append(value)
            nested = value.get("chess_fen")
            if isinstance(nested, Mapping):
                roots.append(nested)
    return roots


def _diagram_id(record: Mapping[str, Any]) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _selected_value(record: Mapping[str, Any]) -> str:
    return str(record.get("selected_value") or record.get("fen") or record.get("full_fen") or record.get("candidate_fen") or "").strip()


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export best-known strict accepted FEN baseline cases.")
    parser.add_argument("report_json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        payload = export_best_strict_accepted_baseline(args.report_json, output_path=args.output)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "failed", "error": error.__class__.__name__, "message": str(error)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"status": "ok", "accepted_count": payload["accepted_count"], "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
