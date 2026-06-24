from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chess_reading_order_audit import audit_chess_reading_order, write_chess_reading_order_report


def generate_chess_reading_order_audit(
    report_json: str | Path,
    *,
    output_dir: str | Path = "reports",
    final_html_path: str | Path | None = None,
) -> dict[str, Any]:
    report_path = Path(report_json)
    payload = json.loads(report_path.read_text(encoding="utf-8-sig"))
    quality = payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else payload
    chess_fen = quality.get("chess_fen") if isinstance(quality.get("chess_fen"), dict) else {}
    chess_pgn = quality.get("chess_pgn") if isinstance(quality.get("chess_pgn"), dict) else {}

    diagram_records = _diagram_records(chess_fen.get("records") or [])
    pgn_records = _pgn_records(chess_pgn.get("records") or [])
    report = audit_chess_reading_order(
        diagram_records=diagram_records,
        fen_candidates=diagram_records,
        pgn_records=pgn_records,
        final_html_path=final_html_path,
    )
    paths = write_chess_reading_order_report(report, output_dir)
    summary = {
        "schema_version": "kindlemaster.chess_reading_order_audit_generation.v1",
        "status": report.status,
        "source_report": str(report_path),
        "output_dir": str(output_dir),
        "json": paths["json"],
        "html": paths["html"],
        "diagram_record_count": len(diagram_records),
        "pgn_record_count": len(pgn_records),
        "warning_count": len(report.warnings),
        "high_severity_warning_count": len(report.high_severity_warnings()),
    }
    return summary


def _diagram_records(records: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for index, record in enumerate(record for record in records if isinstance(record, dict)):
        item = dict(record)
        item.setdefault("id", item.get("filename") or f"diagram-{index + 1}")
        item.setdefault("source_order", _source_order(item, index))
        if item.get("fen"):
            item.setdefault("caption", f"{item.get('filename') or item['id']} FEN {item.get('fen')}")
        else:
            item.setdefault("caption", str(item.get("filename") or item["id"]))
        normalized.append(item)
    return sorted(normalized, key=lambda item: (_safe_int(item.get("page")), _safe_int(item.get("source_order")), str(item.get("id"))))


def _pgn_records(records: list[Any]) -> list[dict[str, Any]]:
    normalized = []
    for index, record in enumerate(record for record in records if isinstance(record, dict)):
        item = dict(record)
        item.setdefault("id", f"pgn-{index + 1}")
        item.setdefault("source_order", _pgn_source_order(item, index))
        normalized.append(item)
    return sorted(normalized, key=lambda item: (_first_page(item), _safe_int(item.get("source_order")), str(item.get("id"))))


def _source_order(record: dict[str, Any], fallback: int) -> int:
    bbox = record.get("bbox")
    if isinstance(bbox, list) and len(bbox) >= 4:
        return int(_safe_float(bbox[1]) * 10000 + _safe_float(bbox[0]))
    return fallback


def _pgn_source_order(record: dict[str, Any], fallback: int) -> int:
    explicit = record.get("source_order") or record.get("reading_order")
    if explicit is not None:
        return _safe_int(explicit, fallback)
    # Put solution text after diagrams on the same page when only page-level data is known.
    return 100000 + fallback


def _first_page(record: dict[str, Any]) -> int:
    source_pages = record.get("source_pages")
    if isinstance(source_pages, list) and source_pages:
        return _safe_int(source_pages[0])
    return _safe_int(record.get("page"))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate report-first Chess HTML/PGN reading-order audit from a conversion report JSON.")
    parser.add_argument("report_json")
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--final-html", default="")
    args = parser.parse_args()
    summary = generate_chess_reading_order_audit(
        args.report_json,
        output_dir=args.output_dir,
        final_html_path=args.final_html or None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if int(summary.get("high_severity_warning_count") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
