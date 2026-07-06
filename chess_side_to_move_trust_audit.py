from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


ACCEPTED_FULL_FEN_STATUSES = {"FEN_MACHINE_ACCEPTED", "FEN_MACHINE_REPAIRED", "FEN_CORPUS_VERIFIED"}
TRIANGLE_OUTLINE = "\u25b3"
TRIANGLE_FILLED = "\u25bc"


def build_side_to_move_diagnostic_report(
    records: Iterable[Mapping[str, Any]],
    *,
    source_gate: Mapping[str, Any] | None = None,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(artifact_root) if artifact_root else None
    source_artifact_missing = _source_gate_missing(source_gate)
    source_html = _source_gate_summary(source_gate)
    rows = [
        _audit_row(record, source_artifact_missing=source_artifact_missing, artifact_root=root)
        for record in records
        if isinstance(record, Mapping)
    ]
    total = len(rows)
    counts = Counter(str(row.get("primary_blocker") or "unknown") for row in rows)
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        blocker = str(row.get("primary_blocker") or "unknown")
        if len(samples[blocker]) < 10:
            samples[blocker].append({"diagram_id": row.get("diagram_id") or "", "page": row.get("page") or ""})
    summary = {
        "diagram_count": total,
        "side_unknown_count": len([row for row in rows if _normalize_side(row.get("side_to_move_detected")) == "unknown"]),
        "marker_search_zone_coverage_count": len([row for row in rows if int(row.get("marker_search_zone_count") or 0) > 0]),
        "marker_bbox_detection_count": len([row for row in rows if row.get("marker_bbox_exists")]),
        "marker_crop_generation_count": len([row for row in rows if row.get("side_marker_crop_exists")]),
        "marker_crop_quality_pass_count": len([row for row in rows if row.get("marker_crop_quality") == "pass"]),
        "trusted_marker_count": len([row for row in rows if row.get("trusted_marker")]),
        "side_to_move_coverage_count": len([row for row in rows if _normalize_side(row.get("side_to_move_detected")) in {"w", "b"}]),
        "marker_search_zone_coverage_rate": _rate(
            len([row for row in rows if int(row.get("marker_search_zone_count") or 0) > 0]),
            total,
        ),
        "marker_bbox_detection_rate": _rate(len([row for row in rows if row.get("marker_bbox_exists")]), total),
        "marker_crop_generation_rate": _rate(len([row for row in rows if row.get("side_marker_crop_exists")]), total),
        "marker_crop_quality_pass_rate": _rate(len([row for row in rows if row.get("marker_crop_quality") == "pass"]), total),
        "trusted_marker_rate": _rate(len([row for row in rows if row.get("trusted_marker")]), total),
        "side_to_move_coverage_rate": _rate(
            len([row for row in rows if _normalize_side(row.get("side_to_move_detected")) in {"w", "b"}]),
            total,
        ),
        "by_primary_blocker": dict(sorted(counts.items())),
        "rates_by_primary_blocker": {
            blocker: _rate(count, total) for blocker, count in sorted(counts.items())
        },
        "samples_by_primary_blocker": dict(sorted(samples.items())),
    }
    return {
        "schema": "kindlemaster.chess_fen.why_side_to_move_not_trusted.v1",
        "source_html": source_html,
        "summary": summary,
        "items": rows,
    }


def side_to_move_diagnostic_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Why Side To Move Is Not Trusted",
        "",
        f"- diagrams: {summary.get('diagram_count', 0)}",
        f"- side unknown: {summary.get('side_unknown_count', 0)}",
        f"- marker search zone coverage rate: {summary.get('marker_search_zone_coverage_rate', 0.0)}",
        f"- marker bbox detection rate: {summary.get('marker_bbox_detection_rate', 0.0)}",
        f"- marker crop generation rate: {summary.get('marker_crop_generation_rate', 0.0)}",
        f"- marker crop quality pass rate: {summary.get('marker_crop_quality_pass_rate', 0.0)}",
        f"- trusted marker rate: {summary.get('trusted_marker_rate', 0.0)}",
        f"- side-to-move coverage rate: {summary.get('side_to_move_coverage_rate', 0.0)}",
        "",
        "## Top Blockers",
        "",
        "| Primary blocker | Count | Rate | Samples |",
        "| --- | ---: | ---: | --- |",
    ]
    counts = summary.get("by_primary_blocker") or {}
    rates = summary.get("rates_by_primary_blocker") or {}
    samples = summary.get("samples_by_primary_blocker") or {}
    if counts:
        for blocker, count in counts.items():
            sample_text = ", ".join(
                f"{item.get('diagram_id')}@p{item.get('page')}"
                for item in samples.get(blocker, [])
                if item.get("diagram_id")
            )
            lines.append(f"| {_md(str(blocker))} | {count} | {rates.get(blocker, 0.0)} | {_md(sample_text)} |")
    else:
        lines.append("| none | 0 | 0.0 |  |")
    lines.extend(
        [
            "",
            "## Diagram Audit",
            "",
            "| Diagram | Page | Search zones | Marker bbox | Marker crop | Classifier | Side | Trusted | Primary blocker | Next action |",
            "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report.get("items") or []:
        lines.append(
            "| {id} | {page} | {zones} | {bbox} | {crop} | {status} | {side} | {trusted} | {blocker} | {action} |".format(
                id=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                zones=_md(str(item.get("marker_search_zone_count") or 0)),
                bbox="yes" if item.get("marker_bbox_exists") else "no",
                crop="yes" if item.get("side_marker_crop_exists") else "no",
                status=_md(str(item.get("side_marker_status") or "")),
                side=_md(str(item.get("side_to_move_detected") or "")),
                trusted="yes" if item.get("trusted_marker") else "no",
                blocker=_md(str(item.get("primary_blocker") or "")),
                action=_md(str(item.get("next_action") or "")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def side_to_move_diagnostic_html(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    cards = [
        ("Diagrams", summary.get("diagram_count", 0)),
        ("Side unknown", summary.get("side_unknown_count", 0)),
        ("Search zones", summary.get("marker_search_zone_coverage_rate", 0.0)),
        ("Marker bbox", summary.get("marker_bbox_detection_rate", 0.0)),
        ("Marker crops", summary.get("marker_crop_generation_rate", 0.0)),
        ("Crop pass", summary.get("marker_crop_quality_pass_rate", 0.0)),
        ("Trusted markers", summary.get("trusted_marker_rate", 0.0)),
        ("Side coverage", summary.get("side_to_move_coverage_rate", 0.0)),
    ]
    card_html = "\n".join(
        f"<div class='card'><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>"
        for label, value in cards
    )
    blocker_rows = "\n".join(
        "<tr>"
        f"<td><code>{html.escape(str(blocker))}</code></td>"
        f"<td>{html.escape(str(count))}</td>"
        f"<td>{html.escape(str((summary.get('rates_by_primary_blocker') or {}).get(blocker, 0.0)))}</td>"
        f"<td>{html.escape(_sample_text((summary.get('samples_by_primary_blocker') or {}).get(blocker, [])))}</td>"
        "</tr>"
        for blocker, count in (summary.get("by_primary_blocker") or {}).items()
    ) or "<tr><td colspan='4'>No blockers.</td></tr>"
    item_rows = "\n".join(_html_item_row(item) for item in report.get("items") or []) or (
        "<tr><td colspan='16'>No diagrams found.</td></tr>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Why Side To Move Is Not Trusted</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #111827; background: #f8fafc; }}
    h1 {{ margin-bottom: 0.25rem; }}
    p {{ color: #4b5563; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 18px 0; }}
    .card {{ background: #fff; border: 1px solid #dbe3ef; border-radius: 8px; padding: 12px; }}
    .card span {{ display: block; color: #64748b; font-size: 12px; }}
    .card strong {{ display: block; margin-top: 5px; font-size: 20px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; margin: 16px 0 28px; font-size: 13px; }}
    th, td {{ border: 1px solid #dbe3ef; padding: 7px 8px; vertical-align: top; text-align: left; }}
    th {{ background: #eef2f7; position: sticky; top: 0; }}
    code {{ background: #eef2f7; border-radius: 4px; padding: 1px 4px; }}
    .ok {{ color: #047857; font-weight: 700; }}
    .blocker {{ color: #b91c1c; font-weight: 700; }}
    .crop img {{ max-width: 90px; max-height: 90px; border: 1px solid #cbd5e1; background: #fff; display: block; margin-top: 4px; }}
  </style>
</head>
<body>
  <h1>Why Side To Move Is Not Trusted</h1>
  <p>Diagnostic audit only: this report explains the chain from board bbox to trusted side marker. It does not promote FEN, train a model, or loosen gates.</p>
  <section class="cards">{card_html}</section>
  <h2>Top Blockers</h2>
  <table>
    <thead><tr><th>Primary blocker</th><th>Count</th><th>Rate</th><th>Samples</th></tr></thead>
    <tbody>{blocker_rows}</tbody>
  </table>
  <h2>Per Diagram Chain</h2>
  <table>
    <thead><tr><th>Diagram</th><th>Page</th><th>Board quality</th><th>Board reason</th><th>Search zones</th><th>Selected zone</th><th>Marker bbox</th><th>Marker crop</th><th>Marker quality</th><th>Marker reason</th><th>Symbol</th><th>Side</th><th>Status</th><th>Trusted</th><th>Primary blocker</th><th>Next action</th></tr></thead>
    <tbody>{item_rows}</tbody>
  </table>
</body>
</html>
"""


def _audit_row(
    record: Mapping[str, Any],
    *,
    source_artifact_missing: bool,
    artifact_root: Path | None,
) -> dict[str, Any]:
    diagram_id = str(_first(record, "diagram_id", "id") or "")
    page = _first(record, "page", "page_number") or ""
    board_bbox = _bbox4(_first(record, "board_bbox", "tight_board_bbox", "bbox_xyxy", "bbox", "pixel_bbox_xyxy"))
    board_crop_path = str(_first(record, "board_crop_path", "source_crop") or "")
    board_crop_exists = _path_exists(board_crop_path, artifact_root)
    board_crop_quality = _quality_value(
        _first(record, "board_crop_quality", "tight_board_crop_status", "board_crop_status"),
        has_artifact=bool(board_bbox) or board_crop_exists,
        fail_reasons=record.get("board_crop_fail_reason") or record.get("board_crop_reason") or [],
    )
    board_crop_fail_reason = _reasons(record.get("board_crop_fail_reason") or record.get("board_crop_reason") or [])
    marker_search_zones = record.get("marker_search_zones") if isinstance(record.get("marker_search_zones"), Mapping) else {}
    marker_search_zone_count = len(marker_search_zones)
    if marker_search_zone_count == 0 and (
        _bbox4(record.get("marker_search_zone_preview_bbox"))
        or _path_exists(str(_first(record, "side_marker_search_crop_path", "marker_search_zone_preview_path") or ""), artifact_root)
    ):
        marker_search_zone_count = 1
    selected_marker_zone = str(_first(record, "selected_marker_zone") or "")
    if not selected_marker_zone and isinstance(marker_search_zones, Mapping) and marker_search_zones:
        selected_marker_zone = str(next(iter(marker_search_zones.keys())))
    marker_bbox = _bbox4(_first(record, "marker_bbox", "side_marker_bbox", "marker_crop_bbox"))
    side_marker_crop_path = str(_first(record, "side_marker_crop_path") or "")
    side_marker_crop_exists = _path_exists(side_marker_crop_path, artifact_root)
    marker_crop_quality = _quality_value(
        _first(record, "marker_crop_quality", "side_marker_crop_status"),
        has_artifact=side_marker_crop_exists,
        fail_reasons=record.get("marker_crop_fail_reason") or [],
    )
    marker_crop_fail_reason = _reasons(record.get("marker_crop_fail_reason") or [])
    detected_marker_symbol = _marker_symbol(_first(record, "detected_marker_symbol", "side_marker_symbol", "marker_symbol"))
    side_to_move_detected = _normalize_side(_first(record, "side_to_move_detected", "side_to_move", "side_to_move_code", "side"))
    side_marker_status = str(_first(record, "side_marker_status", "marker_status") or "marker_missing")
    status_lower = side_marker_status.lower()
    marker_status_trusted = status_lower == "trusted_marker" or status_lower.startswith("trusted_")
    trusted_marker = (
        marker_status_trusted
        and side_to_move_detected in {"w", "b"}
        and board_crop_quality == "pass"
        and marker_crop_quality == "pass"
    )
    placement_status = str(_first(record, "placement_status", "placement_runtime_status") or "")
    full_fen_status = str(_first(record, "full_fen_status", "full_fen_runtime_status", "runtime_status") or "")
    blocker = _primary_blocker(
        source_artifact_missing=source_artifact_missing,
        record=record,
        board_bbox_exists=bool(board_bbox),
        board_crop_quality=board_crop_quality,
        marker_search_zone_count=marker_search_zone_count,
        marker_bbox_exists=bool(marker_bbox),
        side_marker_crop_exists=side_marker_crop_exists,
        marker_crop_quality=marker_crop_quality,
        side_marker_status=status_lower,
        marker_status_trusted=marker_status_trusted,
        trusted_marker=trusted_marker,
        side_to_move_detected=side_to_move_detected,
        full_fen_status=full_fen_status,
    )
    return {
        "diagram_id": diagram_id,
        "page": page,
        "board_crop_path": board_crop_path,
        "board_crop_exists": board_crop_exists,
        "board_bbox": board_bbox,
        "board_bbox_exists": bool(board_bbox),
        "board_crop_quality": board_crop_quality,
        "board_crop_fail_reason": board_crop_fail_reason,
        "marker_search_zone_count": marker_search_zone_count,
        "selected_marker_zone": selected_marker_zone,
        "marker_search_zones": dict(marker_search_zones),
        "marker_bbox": marker_bbox,
        "marker_bbox_exists": bool(marker_bbox),
        "side_marker_crop_path": side_marker_crop_path,
        "side_marker_crop_exists": side_marker_crop_exists,
        "marker_crop_quality": marker_crop_quality,
        "marker_crop_fail_reason": marker_crop_fail_reason,
        "detected_marker_symbol": detected_marker_symbol,
        "side_to_move_detected": side_to_move_detected,
        "side_marker_status": side_marker_status or "marker_missing",
        "trusted_marker": trusted_marker,
        "placement_status": placement_status,
        "full_fen_status": full_fen_status,
        "primary_blocker": blocker,
        "next_action": _next_action(blocker),
        "side_marker_search_crop_path": str(_first(record, "side_marker_search_crop_path", "marker_search_zone_preview_path") or ""),
        "side_marker_review_crop_path": str(_first(record, "side_marker_review_crop_path") or ""),
        "side_marker_review_crop_kind": str(_first(record, "side_marker_review_crop_kind") or ""),
        "debug_overlay_path": str(_first(record, "debug_overlay_path") or ""),
        "warnings": [str(warning) for warning in record.get("warnings") or [] if str(warning)],
        "acceptance_blocker_codes": _blocker_codes(record),
        "side_marker_assignment_trace": dict(record.get("side_marker_assignment_trace") or {}),
    }


def _primary_blocker(
    *,
    source_artifact_missing: bool,
    record: Mapping[str, Any],
    board_bbox_exists: bool,
    board_crop_quality: str,
    marker_search_zone_count: int,
    marker_bbox_exists: bool,
    side_marker_crop_exists: bool,
    marker_crop_quality: str,
    side_marker_status: str,
    marker_status_trusted: bool,
    trusted_marker: bool,
    side_to_move_detected: str,
    full_fen_status: str,
) -> str:
    blocker_codes = set(_blocker_codes(record))
    has_source_artifact = bool(_first(record, "source_image_path", "image_path", "crop_path", "board_crop_path", "source_crop"))
    if source_artifact_missing and not board_bbox_exists and not has_source_artifact:
        return "source_artifact_missing"
    if not board_bbox_exists:
        return "board_bbox_missing"
    if board_crop_quality == "fail":
        return "board_crop_quality_fail"
    if marker_search_zone_count <= 0:
        return "marker_search_zone_missing"
    if not marker_bbox_exists:
        return "marker_bbox_not_found"
    if not side_marker_crop_exists:
        return "marker_crop_not_generated"
    if marker_crop_quality == "fail":
        return "marker_crop_quality_fail"
    if "conflict" in side_marker_status or "multi" in side_marker_status:
        return "marker_classifier_conflict"
    if "ambiguous" in side_marker_status or "noisy" in side_marker_status:
        return "marker_classifier_ambiguous"
    if side_marker_status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker", "inferred_only"}:
        return "marker_classifier_missing"
    if marker_status_trusted and side_to_move_detected == "unknown":
        return "trusted_marker_not_propagated"
    if trusted_marker and full_fen_status and full_fen_status not in ACCEPTED_FULL_FEN_STATUSES:
        return "full_fen_gate_blocked"
    if (
        bool(record.get("manual_review_required"))
        or str(record.get("manual_review_reason") or "").strip()
        or "full_fen_blocked_by_marker_manual_review" in blocker_codes
    ):
        return "manual_review_required"
    if trusted_marker:
        return "no_blocker_trusted"
    return "manual_review_required"


def _next_action(blocker: str) -> str:
    return {
        "source_artifact_missing": "regenerate_source_artifacts",
        "board_bbox_missing": "fix_board_bbox_detection",
        "board_crop_quality_fail": "fix_board_crop_validation",
        "marker_search_zone_missing": "fix_marker_search_zone_generation",
        "marker_bbox_not_found": "fix_marker_bbox_detection",
        "marker_crop_not_generated": "fix_marker_crop_generation",
        "marker_crop_quality_fail": "fix_marker_crop_validation",
        "marker_classifier_missing": "fix_marker_classifier_missing_case",
        "marker_classifier_ambiguous": "manual_review_or_tighten_classifier_evidence",
        "marker_classifier_conflict": "manual_review_or_conflict_arbitration",
        "trusted_marker_not_propagated": "inspect_side_to_move_propagation",
        "full_fen_gate_blocked": "inspect_full_fen_acceptance_gate",
        "manual_review_required": "manual_review_required",
        "no_blocker_trusted": "no_action_marker_trusted",
    }.get(blocker, "manual_review_required")


def _quality_value(value: Any, *, has_artifact: bool, fail_reasons: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "ok", "accepted", "valid"}:
        return "pass"
    if text in {"fail", "failed", "blocked", "invalid", "reject", "rejected"}:
        return "fail"
    if _reasons(fail_reasons):
        return "fail"
    return "pass" if has_artifact else "missing"


def _reasons(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Mapping):
        return [str(key) for key, flag in value.items() if flag]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return []


def _bbox4(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return list(value)
    return []


def _path_exists(value: str, artifact_root: Path | None) -> bool:
    if not value:
        return False
    path = Path(value)
    if path.is_absolute():
        return path.is_file()
    if artifact_root:
        return (artifact_root / path).is_file()
    return True


def _marker_symbol(value: Any) -> str:
    text = str(value or "").strip()
    lowered = text.lower()
    if text in {TRIANGLE_OUTLINE, "\u25b2"} or lowered in {"outline_triangle", "outline", "triangle_outline"}:
        return TRIANGLE_OUTLINE
    if text == TRIANGLE_FILLED or lowered in {"filled_triangle", "filled", "triangle_filled"}:
        return TRIANGLE_FILLED
    if text and text not in {"?", "unknown", "none"}:
        return text
    return ""


def _normalize_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side in {"w", "white"}:
        return "w"
    if side in {"b", "black"}:
        return "b"
    return "unknown"


def _blocker_codes(record: Mapping[str, Any]) -> list[str]:
    codes = {str(code) for code in (record.get("acceptance_blocker_codes") or []) if str(code)}
    for key in ("acceptance_blockers", "placement_acceptance_blockers", "validation_errors"):
        for blocker in record.get(key) or []:
            if isinstance(blocker, Mapping) and blocker.get("code"):
                codes.add(str(blocker.get("code")))
            elif isinstance(blocker, str) and blocker:
                codes.add(blocker)
    return sorted(codes)


def _source_gate_missing(source_gate: Mapping[str, Any] | None) -> bool:
    if not source_gate:
        return False
    status = str(source_gate.get("status") or source_gate.get("decision") or source_gate.get("error_code") or "").lower()
    if "missing" in status or "not_found" in status or "failed" in status:
        return True
    return bool(source_gate.get("source_artifact_missing"))


def _source_gate_summary(source_gate: Mapping[str, Any] | None) -> dict[str, Any]:
    if not source_gate:
        return {"decision": "", "used_as_final_reader": False, "source_artifact_missing": False}
    return {
        "decision": _source_gate_decision(source_gate),
        "used_as_final_reader": bool(source_gate.get("used_as_final_reader")),
        "source_artifact_missing": _source_gate_missing(source_gate),
    }


def _source_gate_decision(source_gate: Mapping[str, Any]) -> str:
    status = str(source_gate.get("decision") or source_gate.get("status") or source_gate.get("error_code") or "").lower()
    if "missing" in status or "not_found" in status or "failed" in status or bool(source_gate.get("source_artifact_missing")):
        return "source_artifact_missing"
    if bool(source_gate.get("used_as_final_reader")):
        return "used_as_final_reader"
    if "evidence" in status:
        return "source_evidence_only"
    if status:
        return "source_gate_recorded"
    return ""


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _sample_text(samples: Any) -> str:
    if not isinstance(samples, list):
        return ""
    return ", ".join(
        f"{item.get('diagram_id')}@p{item.get('page')}"
        for item in samples
        if isinstance(item, Mapping) and item.get("diagram_id")
    )


def _html_item_row(item: Mapping[str, Any]) -> str:
    trusted_class = "ok" if item.get("trusted_marker") else "blocker"
    blocker_class = "ok" if item.get("primary_blocker") == "no_blocker_trusted" else "blocker"
    marker_crop = str(item.get("side_marker_crop_path") or "")
    crop_html = html.escape(marker_crop)
    if marker_crop:
        crop_html += f"<span class='crop'><img src='{html.escape(marker_crop, quote=True)}' alt='marker crop'></span>"
    return (
        "<tr>"
        f"<td><code>{html.escape(str(item.get('diagram_id') or ''))}</code></td>"
        f"<td>{html.escape(str(item.get('page') or ''))}</td>"
        f"<td>{html.escape(str(item.get('board_crop_quality') or ''))}</td>"
        f"<td>{html.escape(', '.join(str(reason) for reason in (item.get('board_crop_fail_reason') or [])))}</td>"
        f"<td>{html.escape(str(item.get('marker_search_zone_count') or 0))}</td>"
        f"<td>{html.escape(str(item.get('selected_marker_zone') or ''))}</td>"
        f"<td>{html.escape(json.dumps(item.get('marker_bbox') or [], ensure_ascii=False))}</td>"
        f"<td>{crop_html}</td>"
        f"<td>{html.escape(str(item.get('marker_crop_quality') or ''))}</td>"
        f"<td>{html.escape(', '.join(str(reason) for reason in (item.get('marker_crop_fail_reason') or [])))}</td>"
        f"<td>{html.escape(str(item.get('detected_marker_symbol') or ''))}</td>"
        f"<td>{html.escape(str(item.get('side_to_move_detected') or ''))}</td>"
        f"<td>{html.escape(str(item.get('side_marker_status') or ''))}</td>"
        f"<td class='{trusted_class}'>{'yes' if item.get('trusted_marker') else 'no'}</td>"
        f"<td class='{blocker_class}'><code>{html.escape(str(item.get('primary_blocker') or ''))}</code></td>"
        f"<td>{html.escape(str(item.get('next_action') or ''))}</td>"
        "</tr>"
    )
