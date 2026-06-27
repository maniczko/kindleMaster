from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping


ACCEPTED_PLACEMENT_STATUSES = {"FEN_PLACEMENT_MACHINE_ACCEPTED", "FEN_PLACEMENT_CORPUS_VERIFIED"}
ACCEPTED_FULL_FEN_STATUSES = {"FEN_MACHINE_ACCEPTED", "FEN_MACHINE_REPAIRED", "FEN_CORPUS_VERIFIED"}


def build_side_marker_blocker_attribution(
    records: Iterable[Mapping[str, Any]],
    *,
    source_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [_side_marker_blocker_row(record, source_gate=source_gate) for record in records if isinstance(record, Mapping)]
    counts = Counter(str(row.get("primary_side_marker_blocker") or "unknown") for row in rows)
    total = len(rows)
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        code = str(row.get("primary_side_marker_blocker") or "unknown")
        if len(samples[code]) >= 10:
            continue
        samples[code].append({"diagram_id": row.get("diagram_id") or "", "page": row.get("page") or ""})
    return {
        "schema": "kindlemaster.chess_fen.side_marker_blocker_attribution.v1",
        "source_html": _source_gate_summary(source_gate),
        "summary": {
            "diagram_count": total,
            "side_unknown_count": len([row for row in rows if row.get("side_to_move_unknown")]),
            "trusted_marker_not_propagated_count": counts.get("trusted_marker_not_propagated", 0),
            "placement_blocks_full_fen_count": counts.get("placement_blocks_full_fen", 0),
            "by_primary_side_marker_blocker": dict(sorted(counts.items())),
            "rates_by_primary_side_marker_blocker": {
                code: round(count / total, 4) if total else 0.0 for code, count in sorted(counts.items())
            },
            "samples_by_primary_side_marker_blocker": dict(sorted(samples.items())),
        },
        "items": rows,
    }


def side_marker_blocker_attribution_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    source_html = report.get("source_html") or {}
    lines = [
        "# Side Marker Blocker Attribution",
        "",
        f"- diagrams: {summary.get('diagram_count', 0)}",
        f"- side unknown: {summary.get('side_unknown_count', 0)}",
        f"- trusted marker not propagated: {summary.get('trusted_marker_not_propagated_count', 0)}",
        f"- placement blocks full FEN: {summary.get('placement_blocks_full_fen_count', 0)}",
        f"- source HTML final reader: {source_html.get('used_as_final_reader', False)}",
        "",
        "## Blocker Counts",
        "",
        "| Blocker | Count | Rate | Samples |",
        "| --- | ---: | ---: | --- |",
    ]
    counts = summary.get("by_primary_side_marker_blocker") or {}
    rates = summary.get("rates_by_primary_side_marker_blocker") or {}
    samples = summary.get("samples_by_primary_side_marker_blocker") or {}
    for code, count in counts.items():
        sample_text = ", ".join(
            f"{item.get('diagram_id')}@p{item.get('page')}" for item in samples.get(code, []) if item.get("diagram_id")
        )
        lines.append(f"| {_md(code)} | {count} | {rates.get(code, 0.0)} | {_md(sample_text)} |")
    lines.extend(
        [
            "",
            "## Items",
            "",
            "| Diagram | Page | Primary blocker | Side | Marker status | Placement | Full FEN |",
            "| --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report.get("items") or []:
        lines.append(
            "| {diagram} | {page} | {blocker} | {side} | {marker} | {placement} | {full} |".format(
                diagram=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                blocker=_md(str(item.get("primary_side_marker_blocker") or "")),
                side=_md(str(item.get("side_to_move") or "")),
                marker=_md(str(item.get("side_marker_status") or "")),
                placement=_md(str(item.get("placement_status") or "")),
                full=_md(str(item.get("full_fen_status") or "")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _side_marker_blocker_row(record: Mapping[str, Any], *, source_gate: Mapping[str, Any] | None) -> dict[str, Any]:
    side = str(_first(record, "side_to_move", "side_to_move_code", "side") or "unknown").lower()
    marker_status = str(_first(record, "side_marker_status", "marker_status") or "").lower()
    placement_status = str(_first(record, "placement_status", "placement_runtime_status") or "")
    full_fen_status = str(_first(record, "full_fen_status", "full_fen_runtime_status", "runtime_status") or "")
    source_image_path = str(_first(record, "source_image_path", "image_path", "crop_path") or "")
    board_crop_path = str(_first(record, "board_crop_path") or "")
    side_marker_crop_path = str(_first(record, "side_marker_crop_path") or "")
    side_unknown = side not in {"w", "b", "white", "black"}
    trusted_marker = marker_status.startswith("trusted_") or marker_status == "trusted_marker"
    placement_accepted = placement_status in ACCEPTED_PLACEMENT_STATUSES or bool(record.get("placement_accepted"))
    full_fen_accepted = full_fen_status in ACCEPTED_FULL_FEN_STATUSES or bool(record.get("full_fen_accepted"))
    has_board_bbox = bool(_first(record, "board_bbox", "bbox", "pixel_bbox", "pixel_bbox_xyxy"))
    has_board_crop = _truthy(record.get("has_board_crop")) or bool(board_crop_path)
    has_marker_crop = _truthy(record.get("has_side_marker_crop")) or bool(side_marker_crop_path)
    has_marker_probe = bool(
        record.get("side_marker_assignment_trace")
        or marker_status
        or str(_first(record, "side_marker_source", "side_marker_symbol") or "").strip().strip("?")
    )
    primary = _primary_side_marker_blocker(
        source_gate=source_gate,
        source_image_path=source_image_path,
        has_board_bbox=has_board_bbox,
        has_board_crop=has_board_crop,
        has_marker_probe=has_marker_probe,
        has_marker_crop=has_marker_crop,
        marker_status=marker_status,
        trusted_marker=trusted_marker,
        side_unknown=side_unknown,
        placement_accepted=placement_accepted,
        full_fen_accepted=full_fen_accepted,
    )
    return {
        "diagram_id": _first(record, "diagram_id", "id") or "",
        "page": _first(record, "page", "page_number") or "",
        "primary_side_marker_blocker": primary,
        "side_to_move": side,
        "side_to_move_unknown": side_unknown,
        "side_marker_status": marker_status or "marker_missing",
        "side_marker_symbol": _first(record, "side_marker_symbol") or "",
        "board_crop_path": board_crop_path,
        "side_marker_crop_path": side_marker_crop_path,
        "debug_overlay_path": _first(record, "debug_overlay_path") or "",
        "source_image_path": source_image_path,
        "placement_status": placement_status,
        "placement_accepted": placement_accepted,
        "full_fen_status": full_fen_status,
        "full_fen_accepted": full_fen_accepted,
        "acceptance_blocker_codes": list(record.get("acceptance_blocker_codes") or []),
    }


def _primary_side_marker_blocker(
    *,
    source_gate: Mapping[str, Any] | None,
    source_image_path: str,
    has_board_bbox: bool,
    has_board_crop: bool,
    has_marker_probe: bool,
    has_marker_crop: bool,
    marker_status: str,
    trusted_marker: bool,
    side_unknown: bool,
    placement_accepted: bool,
    full_fen_accepted: bool,
) -> str:
    if _source_html_overwrites_reader(source_gate):
        return "source_html_overwrite"
    if not source_image_path and not has_board_crop:
        return "diagram_image_missing"
    if not has_board_bbox and not has_board_crop:
        return "board_bbox_missing"
    if trusted_marker and side_unknown:
        return "trusted_marker_not_propagated"
    if not has_marker_probe:
        return "marker_probe_not_run"
    if "conflict" in marker_status or "multi" in marker_status:
        return "marker_classifier_conflict"
    if "ambiguous" in marker_status or "noisy" in marker_status:
        return "marker_classifier_ambiguous"
    if marker_status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker", "inferred_only"}:
        return "marker_classifier_missing"
    if not has_marker_crop and not trusted_marker:
        return "marker_crop_not_generated"
    if trusted_marker and not placement_accepted and not full_fen_accepted:
        return "placement_blocks_full_fen"
    if trusted_marker and placement_accepted and not full_fen_accepted:
        return "full_fen_review_required"
    if side_unknown:
        return "side_unknown_unattributed"
    return "no_side_marker_blocker"


def _source_html_overwrites_reader(source_gate: Mapping[str, Any] | None) -> bool:
    if not source_gate or not source_gate.get("used_as_final_reader"):
        return False
    summary = source_gate.get("summary") or {}
    return int(summary.get("side_unknown_count") or 0) > 0 or int(summary.get("source_fen_or_marker_evidence_count") or 0) == 0


def _source_gate_summary(source_gate: Mapping[str, Any] | None) -> dict[str, Any]:
    if not source_gate:
        return {"used_as_final_reader": False, "decision": "", "side_unknown_count": 0}
    summary = source_gate.get("summary") or {}
    return {
        "used_as_final_reader": bool(source_gate.get("used_as_final_reader")),
        "source_html_evidence_only": bool(source_gate.get("source_html_evidence_only")),
        "decision": str(source_gate.get("decision") or ""),
        "side_unknown_count": int(summary.get("side_unknown_count") or 0),
        "source_fen_or_marker_evidence_count": int(summary.get("source_fen_or_marker_evidence_count") or 0),
    }


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
