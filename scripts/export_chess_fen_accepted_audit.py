from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


CRITICAL_WARNING_MARKERS = (
    "king_count_invalid",
    "fen_invalid",
    "fen_parse_failed",
    "fen_position_invalid",
)
HIGH_WARNING_MARKERS = (
    "low_confidence",
    "sparse_position_confidence_below_threshold",
    "annotation_cross_marker_suppressed",
    "external_fen_conflicts_with_local",
)


def export_chess_fen_accepted_audit(
    report_path: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a compact false-positive risk summary for accepted/high-confidence FEN records."""
    report = json.loads(Path(report_path).read_text(encoding="utf-8-sig"))
    summary, _queue = build_chess_fen_accepted_audit(report, report_path=str(report_path))
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_chess_fen_accepted_audit(
    report: Any,
    *,
    report_path: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build accepted/high-confidence FEN audit summary and queue rows."""
    records = list(_iter_chess_fen_records(report))
    accepted_records = [record for record in records if _record_is_accepted_fen(record)]
    risk_rows = [_risk_row(record) for record in accepted_records]
    critical_count = sum(1 for row in risk_rows if row["risk_level"] == "critical")
    high_count = sum(1 for row in risk_rows if row["risk_level"] == "high")
    medium_count = sum(1 for row in risk_rows if row["risk_level"] == "medium")
    low_count = sum(1 for row in risk_rows if row["risk_level"] == "low")
    summary = {
        "status": "ok" if accepted_records else "not_applicable",
        "report_path": str(report_path),
        "accepted_count": len(accepted_records),
        "audited_count": len(risk_rows),
        "exported_count": len(risk_rows),
        "critical_risk_count": critical_count,
        "high_risk_count": high_count,
        "medium_risk_count": medium_count,
        "low_risk_count": low_count,
        "risk_rows": [row for row in risk_rows if row["risk_level"] in {"critical", "high"}],
    }
    if not accepted_records:
        summary["reason"] = "no_accepted_fen_records"
    return summary, risk_rows


def write_chess_fen_accepted_audit_artifacts(
    report: Any,
    output_dir: str | Path,
    *,
    report_path: str = "",
) -> dict[str, str]:
    """Write accepted FEN audit artifacts; this is report-only and never mutates publication data."""
    output_root = Path(output_dir) / "fen_accepted_audit"
    output_root.mkdir(parents=True, exist_ok=True)
    summary, queue = build_chess_fen_accepted_audit(report, report_path=report_path)

    queue_json = output_root / "accepted_audit_queue.json"
    queue_jsonl = output_root / "accepted_audit_queue.jsonl"
    summary_json = output_root / "accepted_audit_summary.json"
    review_html = output_root / "accepted_audit_review.html"

    queue_json.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    queue_jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue),
        encoding="utf-8",
    )
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    review_html.write_text(_render_audit_review_html(summary, queue), encoding="utf-8")
    return {
        "queue_json": str(queue_json),
        "queue_jsonl": str(queue_jsonl),
        "summary_json": str(summary_json),
        "review_html": str(review_html),
    }


def _iter_chess_fen_records(report: Any) -> list[dict[str, Any]]:
    if not isinstance(report, dict):
        return []
    candidates = [
        (((report.get("quality_report") or {}).get("chess_fen") or {}).get("records")),
        ((report.get("chess_fen") or {}).get("records")),
        report.get("records"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [row for row in candidate if isinstance(row, dict)]
    return []


def _record_is_accepted_fen(record: dict[str, Any]) -> bool:
    return bool(str(record.get("fen") or "").strip() and not bool(record.get("requires_review")))


def _risk_row(record: dict[str, Any]) -> dict[str, Any]:
    warnings = [str(warning) for warning in record.get("warnings") or []]
    crop_path = str(record.get("crop_path") or record.get("source_crop_path") or "").strip()
    crop_exists = bool(crop_path and Path(crop_path).exists())
    if crop_path and not crop_exists:
        warnings = sorted({*warnings, "accepted_audit_crop_missing"})
    elif not crop_path:
        warnings = sorted({*warnings, "accepted_audit_crop_path_missing"})
    confidence = _float(record.get("confidence"), default=0.0)
    level = "low"
    if any(any(marker in warning for marker in CRITICAL_WARNING_MARKERS) for warning in warnings):
        level = "critical"
    elif "accepted_audit_crop_missing" in warnings or "accepted_audit_crop_path_missing" in warnings:
        level = "high"
    elif any(any(marker in warning for marker in HIGH_WARNING_MARKERS) for warning in warnings):
        level = "high"
    elif confidence and confidence < 0.90:
        level = "medium"
    return {
        "id": record.get("id") or record.get("filename") or "",
        "page": record.get("page"),
        "filename": record.get("filename", ""),
        "fen": record.get("fen", ""),
        "confidence": confidence,
        "method": record.get("method", ""),
        "risk_level": level,
        "warnings": warnings,
        "crop_path": crop_path,
        "crop_exists": crop_exists,
    }


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _render_audit_review_html(summary: dict[str, Any], queue: list[dict[str, Any]]) -> str:
    rows = []
    for row in queue:
        warnings = ", ".join(str(warning) for warning in row.get("warnings") or [])
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('risk_level') or ''))}</td>"
            f"<td>{html.escape(str(row.get('page') or ''))}</td>"
            f"<td>{html.escape(str(row.get('filename') or row.get('id') or ''))}</td>"
            f"<td><code>{html.escape(str(row.get('fen') or ''))}</code></td>"
            f"<td>{html.escape(str(row.get('confidence') or ''))}</td>"
            f"<td>{html.escape(warnings)}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan=\"6\">No accepted FEN records to audit.</td></tr>"
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Accepted FEN Audit</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}"
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px}"
        "th{background:#f3f4f6;text-align:left}code{white-space:nowrap}</style>"
        "</head><body>"
        "<h1>Accepted FEN Audit</h1>"
        f"<p>Status: <strong>{html.escape(str(summary.get('status') or ''))}</strong>. "
        f"Accepted: {html.escape(str(summary.get('accepted_count') or 0))}. "
        f"Critical: {html.escape(str(summary.get('critical_risk_count') or 0))}. "
        f"High: {html.escape(str(summary.get('high_risk_count') or 0))}.</p>"
        "<table><thead><tr><th>Risk</th><th>Page</th><th>Record</th><th>FEN</th><th>Confidence</th><th>Warnings</th></tr></thead>"
        f"<tbody>{body}</tbody></table>"
        "</body></html>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export accepted chess FEN false-positive audit summary.")
    parser.add_argument("report")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    summary = export_chess_fen_accepted_audit(args.report, output_path=args.output or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] in {"ok", "not_applicable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
