from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def audit_side_marker_assignment(
    current_report: str | Path,
    *,
    baseline_report: str | Path | None = None,
    output_json: str | Path,
    output_md: str | Path | None = None,
) -> dict[str, Any]:
    current = _summary(_records(current_report))
    baseline = _summary(_records(baseline_report)) if baseline_report else {}
    recovered = current.get("local_assignment_records", [])
    payload = {
        "status": "ok",
        "mode": "side_marker_assignment_runtime_audit",
        "baseline_report": str(baseline_report or ""),
        "current_report": str(current_report),
        "baseline": baseline,
        "current": current,
        "accepted_delta": int(current.get("accepted_count", 0)) - int(baseline.get("accepted_count", 0) or 0),
        "review_delta": int(current.get("review_count", 0)) - int(baseline.get("review_count", 0) or 0),
        "local_assignment_accepted_count": sum(1 for row in recovered if not row.get("requires_review")),
        "local_assignment_records": recovered,
        "safety": {
            "accepted_inferred_count": current.get("accepted_inferred_count", 0),
            "runtime_symbol_mapping_used": False,
            "policy": "local_visual_marker_only_no_ocr_symbol_authority",
        },
    }
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        Path(output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(output_md).write_text(_markdown(payload), encoding="utf-8")
    return payload


def _records(report_path: str | Path | None) -> list[dict[str, Any]]:
    if not report_path:
        return []
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return list((((report.get("quality_report") or {}).get("chess_fen") or {}).get("records") or []))


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [record for record in records if not bool(record.get("requires_review"))]
    review = [record for record in records if bool(record.get("requires_review"))]
    warning_counts = Counter(
        str(warning)
        for record in records
        for warning in (record.get("warnings") or [])
        if str(warning)
    )
    side_status_counts = Counter(
        f"{record.get('side_to_move_status') or 'unknown'}:{record.get('side_to_move_evidence') or 'none'}"
        for record in records
    )
    local_assignment = [
        {
            "page": int(record.get("page") or 0),
            "filename": str(record.get("filename") or ""),
            "fen": str(record.get("fen") or ""),
            "requires_review": bool(record.get("requires_review")),
            "confidence": float(record.get("confidence") or 0.0),
            "warnings": [str(warning) for warning in (record.get("warnings") or [])],
            "side_to_move_evidence_source_bbox": record.get("side_to_move_evidence_source_bbox") or [],
        }
        for record in records
        if "side_to_move_marker_local_assignment_used" in {str(w) for w in (record.get("warnings") or [])}
    ]
    return {
        "record_count": len(records),
        "accepted_count": len(accepted),
        "review_count": len(review),
        "accepted_percent": round((len(accepted) / len(records) * 100.0) if records else 0.0, 2),
        "accepted_inferred_count": sum(
            1
            for record in accepted
            if record.get("side_to_move_status") == "inferred"
            or "side_to_move_inferred" in {str(w) for w in (record.get("warnings") or [])}
        ),
        "warning_counts": dict(warning_counts.most_common(40)),
        "side_status_counts": dict(side_status_counts),
        "local_assignment_count": len(local_assignment),
        "local_assignment_records": local_assignment,
    }


def _markdown(payload: dict[str, Any]) -> str:
    baseline = payload.get("baseline") or {}
    current = payload.get("current") or {}
    lines = [
        "# Side Marker Assignment Audit",
        "",
        f"- Baseline accepted: `{baseline.get('accepted_count', 0)}/{baseline.get('record_count', 0)}`",
        f"- Current accepted: `{current.get('accepted_count', 0)}/{current.get('record_count', 0)}` ({current.get('accepted_percent', 0)}%)",
        f"- Accepted delta: `{payload.get('accepted_delta')}`",
        f"- Review delta: `{payload.get('review_delta')}`",
        f"- Local assignment accepted: `{payload.get('local_assignment_accepted_count')}`",
        f"- Accepted inferred count: `{(payload.get('safety') or {}).get('accepted_inferred_count')}`",
        f"- OCR symbol authority used: `{(payload.get('safety') or {}).get('runtime_symbol_mapping_used')}`",
        "",
        "## Recovered Records",
        "",
        "| Page | Filename | Confidence | FEN |",
        "| ---: | --- | ---: | --- |",
    ]
    for row in payload.get("local_assignment_records") or []:
        lines.append(
            f"| {row.get('page')} | `{row.get('filename')}` | {row.get('confidence')} | `{row.get('fen')}` |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit local side-to-move marker assignment impact.")
    parser.add_argument("current_report")
    parser.add_argument("--baseline-report", default="")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", default="")
    args = parser.parse_args(argv)
    summary = audit_side_marker_assignment(
        args.current_report,
        baseline_report=args.baseline_report or None,
        output_json=args.output_json,
        output_md=args.output_md or None,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
