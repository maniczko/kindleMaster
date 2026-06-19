from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Mapping

from chess_reading_order_audit import (
    HIGH_SEVERITY_WARNINGS,
    READING_ORDER_SCHEMA_VERSION,
    ChessReadingOrderReport,
    audit_chess_reading_order,
    write_chess_reading_order_report,
)
from scripts.export_chess_fen_accepted_audit import write_chess_fen_accepted_audit_artifacts


PYTHON_CHESS_BLOCKER = "python_chess_unavailable"


def check_python_chess_available() -> dict[str, Any]:
    """Return an explicit dependency status for strict chess automation."""
    try:
        importlib.import_module("chess")
    except Exception as error:
        return _missing_python_chess_payload(
            error_code="python_chess_missing",
            detail=str(error) or error.__class__.__name__,
            chess_available=False,
            chess_pgn_available=False,
        )
    try:
        importlib.import_module("chess.pgn")
    except Exception as error:
        return _missing_python_chess_payload(
            error_code="python_chess_pgn_missing",
            detail=str(error) or error.__class__.__name__,
            chess_available=True,
            chess_pgn_available=False,
        )
    return {
        "status": "available",
        "available": True,
        "chess_available": True,
        "chess_pgn_available": True,
        "error_code": "",
        "missing_requirements": [],
        "blockers": [],
        "warnings": [],
        "manual_review_required": False,
    }


def strict_python_chess_preflight() -> dict[str, Any]:
    availability = check_python_chess_available()
    if availability.get("available"):
        return {
            "status": "ok",
            "python_chess": availability,
            "blockers": [],
            "missing_requirements": [],
        }
    return {
        "status": "failed",
        "error_code": availability.get("error_code") or "python_chess_missing",
        "python_chess": availability,
        "blockers": list(availability.get("blockers") or [PYTHON_CHESS_BLOCKER]),
        "missing_requirements": list(availability.get("missing_requirements") or ["python-chess"]),
        "notes": [
            "Strict chess automation requires python-chess and chess.pgn for deterministic PGN replay.",
        ],
    }


def non_strict_python_chess_notice() -> dict[str, Any]:
    availability = check_python_chess_available()
    if availability.get("available"):
        return {
            "status": "ok",
            "python_chess": availability,
            "warnings": [],
            "manual_review_required": False,
        }
    return {
        "status": "degraded",
        "python_chess": availability,
        "warnings": list(availability.get("warnings") or [PYTHON_CHESS_BLOCKER]),
        "manual_review_required": True,
        "notes": [
            "Non-strict chess automation may continue, but PGN/FEN proof must remain manual review.",
        ],
    }


def build_auto_chess_flow_artifacts(
    auto_output: Mapping[str, Any] | None = None,
    *,
    pages: Any = None,
    diagram_records: Any = None,
    fen_candidates: Any = None,
    pgn_records: Any = None,
    final_html_path: str | Path | None = None,
    output_dir: str | Path = "artifacts",
    include_accepted_fen_audit: bool = True,
) -> dict[str, Any]:
    """Write report-first chess artifacts for auto chess output without mutating publication data."""
    payload = dict(auto_output or {})
    report = audit_chess_reading_order(
        pages=pages if pages is not None else payload.get("pages") or payload.get("layout_pages") or payload.get("book_pages"),
        diagram_records=diagram_records if diagram_records is not None else payload.get("diagram_records") or payload.get("diagrams"),
        fen_candidates=fen_candidates if fen_candidates is not None else payload.get("fen_candidates"),
        pgn_records=pgn_records if pgn_records is not None else payload.get("pgn_records"),
        final_html_path=final_html_path if final_html_path is not None else payload.get("final_html_path") or payload.get("html_path"),
    )
    paths = write_chess_reading_order_report(report, output_dir)
    artifact_rows = [
        {
            "key": "html_reading_order_report_json",
            "filename": "html_reading_order_report.json",
            "path": paths["json"],
            "content_type": "application/json; charset=utf-8",
        },
        {
            "key": "html_reading_order_report_html",
            "filename": "html_reading_order_report.html",
            "path": paths["html"],
            "content_type": "text/html; charset=utf-8",
        },
    ]
    accepted_audit_summary: dict[str, Any] | None = None
    if include_accepted_fen_audit:
        audit_paths = write_chess_fen_accepted_audit_artifacts(
            payload,
            output_dir,
            report_path=str(payload.get("report_path") or payload.get("quality_report_path") or ""),
        )
        summary_path = Path(audit_paths["summary_json"])
        accepted_audit_summary = _load_json_file(summary_path)
        accepted_audit_summary["summary_path"] = audit_paths["summary_json"]
        artifact_rows.extend(
            [
                {
                    "key": "fen_accepted_audit_queue_json",
                    "filename": "accepted_audit_queue.json",
                    "path": audit_paths["queue_json"],
                    "content_type": "application/json; charset=utf-8",
                },
                {
                    "key": "fen_accepted_audit_queue_jsonl",
                    "filename": "accepted_audit_queue.jsonl",
                    "path": audit_paths["queue_jsonl"],
                    "content_type": "application/x-ndjson; charset=utf-8",
                },
                {
                    "key": "fen_accepted_audit_summary_json",
                    "filename": "accepted_audit_summary.json",
                    "path": audit_paths["summary_json"],
                    "content_type": "application/json; charset=utf-8",
                },
                {
                    "key": "fen_accepted_audit_review_html",
                    "filename": "accepted_audit_review.html",
                    "path": audit_paths["review_html"],
                    "content_type": "text/html; charset=utf-8",
                },
            ]
        )
    return {
        "status": "ok",
        "reading_order_report": report.to_dict(),
        "accepted_fen_audit_summary": accepted_audit_summary or {},
        "artifacts": artifact_rows,
    }


def validate_auto_chess_output(auto_output: Mapping[str, Any] | ChessReadingOrderReport, *, strict: bool = False) -> dict[str, Any]:
    """Validate auto chess output without changing PGN/FEN acceptance rules."""
    report_payload = _extract_reading_order_report(auto_output)
    warnings = list(report_payload.get("warnings") or [])
    high_warnings = [
        warning
        for warning in warnings
        if warning.get("severity") == "high" or warning.get("code") in HIGH_SEVERITY_WARNINGS
    ]
    accepted_audit_summary = _extract_accepted_fen_audit_summary(auto_output)
    accepted_audit_critical_count = int(accepted_audit_summary.get("critical_risk_count") or 0)
    accepted_audit_high_count = int(accepted_audit_summary.get("high_risk_count") or 0)
    audit_warnings = []
    if accepted_audit_critical_count or accepted_audit_high_count:
        audit_warnings.append(
            {
                "code": "accepted_fen_audit_unresolved_high_or_critical_risks",
                "severity": "high",
                "critical_risk_count": accepted_audit_critical_count,
                "high_risk_count": accepted_audit_high_count,
            }
        )
    accepted_audit_failed = bool(strict and (accepted_audit_critical_count or accepted_audit_high_count))
    failed = bool(strict and high_warnings) or accepted_audit_failed
    blockers = []
    if strict and high_warnings:
        blockers.append("high_severity_reading_order_warnings")
    if accepted_audit_failed:
        blockers.append("accepted_fen_audit_unresolved_high_or_critical_risks")
    return {
        "status": "failed" if failed else "passed_with_warnings" if warnings or audit_warnings else "passed",
        "strict": bool(strict),
        "release_ready": not failed,
        "reading_order_status": report_payload.get("status") or "unknown",
        "reading_order_warning_count": len(warnings),
        "high_severity_reading_order_warning_count": len(high_warnings),
        "accepted_fen_audit_status": accepted_audit_summary.get("status") or "not_run",
        "accepted_fen_audit_critical_risk_count": accepted_audit_critical_count,
        "accepted_fen_audit_high_risk_count": accepted_audit_high_count,
        "accepted_fen_audit_artifact_path": accepted_audit_summary.get("artifact_path") or accepted_audit_summary.get("summary_path") or "",
        "warnings": [*warnings, *audit_warnings],
        "blockers": blockers,
    }


def _extract_reading_order_report(auto_output: Mapping[str, Any] | ChessReadingOrderReport) -> dict[str, Any]:
    if isinstance(auto_output, ChessReadingOrderReport):
        return auto_output.to_dict()
    payload = dict(auto_output or {})
    if payload.get("schema_version") == READING_ORDER_SCHEMA_VERSION:
        return payload
    report = payload.get("reading_order_report") or payload.get("html_reading_order_report") or {}
    if isinstance(report, ChessReadingOrderReport):
        return report.to_dict()
    if isinstance(report, Mapping):
        return dict(report)
    return {"schema_version": READING_ORDER_SCHEMA_VERSION, "status": "not_run", "warnings": []}


def _extract_accepted_fen_audit_summary(auto_output: Mapping[str, Any] | ChessReadingOrderReport) -> dict[str, Any]:
    if isinstance(auto_output, ChessReadingOrderReport):
        return {}
    payload = dict(auto_output or {})
    summary = payload.get("accepted_fen_audit_summary") or payload.get("fen_accepted_audit_summary") or {}
    if isinstance(summary, Mapping):
        return dict(summary)
    for artifact in list(payload.get("artifacts") or []):
        if not isinstance(artifact, Mapping):
            continue
        if artifact.get("key") != "fen_accepted_audit_summary_json":
            continue
        path = artifact.get("path")
        if path:
            loaded = _load_json_file(Path(str(path)))
            if loaded:
                loaded.setdefault("artifact_path", str(path))
                return loaded
    return {}


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _missing_python_chess_payload(
    *,
    error_code: str,
    detail: str,
    chess_available: bool,
    chess_pgn_available: bool,
) -> dict[str, Any]:
    return {
        "status": "missing",
        "available": False,
        "chess_available": chess_available,
        "chess_pgn_available": chess_pgn_available,
        "error_code": error_code,
        "detail": detail,
        "missing_requirements": ["python-chess"],
        "blockers": [PYTHON_CHESS_BLOCKER],
        "warnings": [PYTHON_CHESS_BLOCKER],
        "manual_review_required": True,
    }


__all__ = [
    "PYTHON_CHESS_BLOCKER",
    "check_python_chess_available",
    "build_auto_chess_flow_artifacts",
    "validate_auto_chess_output",
    "strict_python_chess_preflight",
    "non_strict_python_chess_notice",
]
