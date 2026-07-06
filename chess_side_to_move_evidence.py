from __future__ import annotations

import html
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


SIDE_TO_MOVE_SOURCES = {
    "trusted_marker",
    "human_verified",
    "text_inferred",
    "pgn_inferred",
    "unknown",
}
SIDE_TO_MOVE_TIERS = {"trusted", "verified", "inferred", "unknown"}
TRUSTED_MARKER_STATUSES = {"trusted_marker", "trusted_side_marker", "trusted"}
HUMAN_VERIFICATION_SOURCES = {"human", "human_visual", "human_manual", "legacy_human_visual"}
FULL_FEN_BLOCKER_NOT_TRUSTED = "not_trusted_side_to_move"
FULL_FEN_BLOCKER_HUMAN_POLICY = "human_verified_policy_required"


def resolve_side_to_move_evidence(
    record: Mapping[str, Any],
    *,
    allow_human_verified_full_fen: bool = False,
) -> dict[str, Any]:
    """Return the normalized side-to-move evidence contract for one diagram."""
    runtime_side = _normalize_side(
        _first(record, "side_to_move", "side_to_move_detected", "side_to_move_code", "side")
    )
    if _is_trusted_marker_record(record, runtime_side=runtime_side):
        confidence = _first_float(
            record.get("side_marker_confidence"),
            record.get("side_to_move_evidence_confidence"),
            record.get("marker_classifier_confidence"),
            record.get("side_to_move_confidence"),
        )
        return _evidence_payload(
            side=runtime_side,
            source="trusted_marker",
            tier="trusted",
            confidence=confidence,
            full_fen_allowed=True,
            full_fen_blocker="",
            manual_review_required=False,
        )

    manual_side = _manual_side(record)
    if _is_human_verified(record) and manual_side in {"w", "b"}:
        return _evidence_payload(
            side=manual_side,
            source="human_verified",
            tier="verified",
            confidence=_first_float(record.get("side_to_move_confidence"), 1.0),
            full_fen_allowed=bool(allow_human_verified_full_fen),
            full_fen_blocker="" if allow_human_verified_full_fen else FULL_FEN_BLOCKER_HUMAN_POLICY,
            manual_review_required=not allow_human_verified_full_fen,
        )

    inferred_source = _inferred_source(record)
    if runtime_side in {"w", "b"} and inferred_source in {"text_inferred", "pgn_inferred"}:
        return _evidence_payload(
            side=runtime_side,
            source=inferred_source,
            tier="inferred",
            confidence=_first_float(
                record.get("side_to_move_evidence_confidence"),
                record.get("side_to_move_confidence"),
                record.get("confidence"),
            ),
            full_fen_allowed=False,
            full_fen_blocker=FULL_FEN_BLOCKER_NOT_TRUSTED,
            manual_review_required=True,
        )

    return _evidence_payload(
        side="unknown",
        source="unknown",
        tier="unknown",
        confidence=0.0,
        full_fen_allowed=False,
        full_fen_blocker=FULL_FEN_BLOCKER_NOT_TRUSTED,
        manual_review_required=True,
    )


def apply_side_to_move_evidence(
    record: Mapping[str, Any],
    *,
    allow_human_verified_full_fen: bool = False,
) -> dict[str, Any]:
    merged = dict(record)
    merged.update(
        resolve_side_to_move_evidence(
            merged,
            allow_human_verified_full_fen=allow_human_verified_full_fen,
        )
    )
    return merged


def build_side_to_move_coverage_dashboard(
    records: Iterable[Mapping[str, Any]],
    *,
    allow_human_verified_full_fen: bool = False,
) -> dict[str, Any]:
    items = [
        _dashboard_item(record, allow_human_verified_full_fen=allow_human_verified_full_fen)
        for record in records
        if isinstance(record, Mapping)
    ]
    total = len(items)
    by_source = Counter(str(item.get("side_to_move_source") or "unknown") for item in items)
    by_tier = Counter(str(item.get("side_to_move_evidence_tier") or "unknown") for item in items)
    covered_count = len(
        [
            item
            for item in items
            if item.get("side_to_move") in {"w", "b"} and item.get("side_to_move_source") != "unknown"
        ]
    )
    trusted_count = int(by_tier.get("trusted", 0))
    full_fen_safe_count = len([item for item in items if item.get("full_fen_allowed")])
    manual_review_count = len([item for item in items if item.get("manual_review_required")])
    summary = {
        "diagram_count": total,
        "side_to_move_coverage_count": covered_count,
        "trusted_side_to_move_count": trusted_count,
        "trusted_marker_count": int(by_source.get("trusted_marker", 0)),
        "human_verified_count": int(by_source.get("human_verified", 0)),
        "text_inferred_count": int(by_source.get("text_inferred", 0)),
        "pgn_inferred_count": int(by_source.get("pgn_inferred", 0)),
        "unknown_count": int(by_source.get("unknown", 0)),
        "manual_review_required_count": manual_review_count,
        "full_fen_safe_acceptance_count": full_fen_safe_count,
        "side_to_move_coverage_rate": _rate(covered_count, total),
        "trusted_side_to_move_rate": _rate(trusted_count, total),
        "trusted_marker_rate": _rate(by_source.get("trusted_marker", 0), total),
        "human_verified_rate": _rate(by_source.get("human_verified", 0), total),
        "text_inferred_rate": _rate(by_source.get("text_inferred", 0), total),
        "pgn_inferred_rate": _rate(by_source.get("pgn_inferred", 0), total),
        "unknown_rate": _rate(by_source.get("unknown", 0), total),
        "manual_review_required_rate": _rate(manual_review_count, total),
        "full_fen_safe_acceptance_rate": _rate(full_fen_safe_count, total),
        "by_source": dict(sorted(by_source.items())),
        "by_evidence_tier": dict(sorted(by_tier.items())),
        "allow_human_verified_full_fen": bool(allow_human_verified_full_fen),
    }
    return {
        "schema": "kindlemaster.chess_fen.side_to_move_coverage_dashboard.v1",
        "summary": summary,
        "items": items,
    }


def side_to_move_coverage_dashboard_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Side To Move Coverage Dashboard",
        "",
        "Audit report only: coverage sources are separated from trusted marker evidence and full-FEN safety.",
        "",
        f"- diagrams: {summary.get('diagram_count', 0)}",
        f"- side-to-move coverage rate: {summary.get('side_to_move_coverage_rate', 0.0)}",
        f"- trusted side-to-move rate: {summary.get('trusted_side_to_move_rate', 0.0)}",
        f"- trusted marker rate: {summary.get('trusted_marker_rate', 0.0)}",
        f"- human verified rate: {summary.get('human_verified_rate', 0.0)}",
        f"- text inferred rate: {summary.get('text_inferred_rate', 0.0)}",
        f"- PGN inferred rate: {summary.get('pgn_inferred_rate', 0.0)}",
        f"- unknown rate: {summary.get('unknown_rate', 0.0)}",
        f"- manual review required rate: {summary.get('manual_review_required_rate', 0.0)}",
        f"- full-FEN safe acceptance rate: {summary.get('full_fen_safe_acceptance_rate', 0.0)}",
        "",
        "## Source Breakdown",
        "",
        "| Source | Count | Rate |",
        "| --- | ---: | ---: |",
    ]
    for source, count in (summary.get("by_source") or {}).items():
        lines.append(f"| {_md(str(source))} | {count} | {_rate(int(count or 0), int(summary.get('diagram_count') or 0))} |")
    if not summary.get("by_source"):
        lines.append("| unknown | 0 | 0.0 |")
    lines.extend(
        [
            "",
            "## Diagrams",
            "",
            "| Diagram | Page | Side | Source | Tier | Confidence | Full FEN allowed | Blocker |",
            "| --- | ---: | --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for item in report.get("items") or []:
        lines.append(
            "| {diagram} | {page} | {side} | {source} | {tier} | {confidence} | {allowed} | {blocker} |".format(
                diagram=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                side=_md(str(item.get("side_to_move") or "unknown")),
                source=_md(str(item.get("side_to_move_source") or "unknown")),
                tier=_md(str(item.get("side_to_move_evidence_tier") or "unknown")),
                confidence=item.get("side_to_move_confidence", 0.0),
                allowed="yes" if item.get("full_fen_allowed") else "no",
                blocker=_md(str(item.get("full_fen_blocker") or "")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def side_to_move_coverage_dashboard_html(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    cards = [
        ("Diagrams", summary.get("diagram_count", 0)),
        ("Coverage", summary.get("side_to_move_coverage_rate", 0.0)),
        ("Trusted side", summary.get("trusted_side_to_move_rate", 0.0)),
        ("Trusted marker", summary.get("trusted_marker_rate", 0.0)),
        ("Human verified", summary.get("human_verified_rate", 0.0)),
        ("Text inferred", summary.get("text_inferred_rate", 0.0)),
        ("PGN inferred", summary.get("pgn_inferred_rate", 0.0)),
        ("Unknown", summary.get("unknown_rate", 0.0)),
        ("Full-FEN safe", summary.get("full_fen_safe_acceptance_rate", 0.0)),
    ]
    card_html = "\n".join(
        f"<div class='card'><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>"
        for label, value in cards
    )
    source_rows = "\n".join(
        "<tr>"
        f"<td><code>{html.escape(str(source))}</code></td>"
        f"<td>{html.escape(str(count))}</td>"
        f"<td>{html.escape(str(_rate(int(count or 0), int(summary.get('diagram_count') or 0))))}</td>"
        "</tr>"
        for source, count in (summary.get("by_source") or {}).items()
    ) or "<tr><td colspan='3'>No records.</td></tr>"
    item_rows = "\n".join(_html_item_row(item) for item in report.get("items") or []) or (
        "<tr><td colspan='11'>No diagrams found.</td></tr>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Side To Move Coverage Dashboard</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #111827; background: #f8fafc; }}
    h1 {{ margin-bottom: 0.25rem; }}
    p {{ color: #4b5563; max-width: 900px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 18px 0; }}
    .card {{ background: #fff; border: 1px solid #dbe3ef; border-radius: 8px; padding: 12px; }}
    .card span {{ display: block; color: #64748b; font-size: 12px; }}
    .card strong {{ display: block; margin-top: 5px; font-size: 20px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; margin: 16px 0 28px; font-size: 13px; }}
    th, td {{ border: 1px solid #dbe3ef; padding: 7px 8px; vertical-align: top; text-align: left; }}
    th {{ background: #eef2f7; position: sticky; top: 0; }}
    code {{ background: #eef2f7; border-radius: 4px; padding: 1px 4px; }}
    .trusted {{ color: #047857; font-weight: 700; }}
    .verified {{ color: #1d4ed8; font-weight: 700; }}
    .inferred {{ color: #92400e; font-weight: 700; }}
    .unknown {{ color: #b91c1c; font-weight: 700; }}
    .allowed {{ color: #047857; font-weight: 700; }}
    .blocked {{ color: #b91c1c; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Side To Move Coverage Dashboard</h1>
  <p>This audit separates side-to-move coverage from trusted marker evidence. Text/PGN inferred values can improve coverage, but they do not allow full FEN by default.</p>
  <section class="cards">{card_html}</section>
  <h2>Source Breakdown</h2>
  <table>
    <thead><tr><th>Source</th><th>Count</th><th>Rate</th></tr></thead>
    <tbody>{source_rows}</tbody>
  </table>
  <h2>Per Diagram Evidence</h2>
  <table>
    <thead><tr><th>Diagram</th><th>Page</th><th>Side</th><th>Source</th><th>Tier</th><th>Confidence</th><th>Marker status</th><th>Marker quality</th><th>Full FEN allowed</th><th>Blocker</th><th>Review</th></tr></thead>
    <tbody>{item_rows}</tbody>
  </table>
</body>
</html>
"""


def _dashboard_item(
    record: Mapping[str, Any],
    *,
    allow_human_verified_full_fen: bool,
) -> dict[str, Any]:
    evidence = resolve_side_to_move_evidence(
        record,
        allow_human_verified_full_fen=allow_human_verified_full_fen,
    )
    return {
        "diagram_id": str(_first(record, "diagram_id", "id") or ""),
        "page": _first(record, "page", "page_number") or "",
        "side_marker_status": str(_first(record, "side_marker_status", "marker_status") or ""),
        "marker_crop_quality": str(_first(record, "marker_crop_quality", "side_marker_crop_status") or ""),
        "manual_review_required": bool(evidence.get("manual_review_required")),
        "warnings": [str(warning) for warning in record.get("warnings") or [] if str(warning)],
        "acceptance_blocker_codes": [
            str(code) for code in record.get("acceptance_blocker_codes") or [] if str(code)
        ],
        **evidence,
    }


def _evidence_payload(
    *,
    side: str,
    source: str,
    tier: str,
    confidence: float,
    full_fen_allowed: bool,
    full_fen_blocker: str,
    manual_review_required: bool,
) -> dict[str, Any]:
    return {
        "side_to_move": _normalize_side(side),
        "side_to_move_source": source if source in SIDE_TO_MOVE_SOURCES else "unknown",
        "side_to_move_confidence": round(max(0.0, min(float(confidence or 0.0), 1.0)), 4),
        "side_to_move_evidence_tier": tier if tier in SIDE_TO_MOVE_TIERS else "unknown",
        "full_fen_allowed": bool(full_fen_allowed),
        "full_fen_blocker": str(full_fen_blocker or ""),
        "manual_review_required": bool(manual_review_required),
    }


def _is_trusted_marker_record(record: Mapping[str, Any], *, runtime_side: str) -> bool:
    if runtime_side not in {"w", "b"}:
        return False
    status = str(_first(record, "side_marker_status", "marker_status") or "").strip().lower()
    if status not in TRUSTED_MARKER_STATUSES:
        return False
    if _truthy(record.get("manual_review_required")):
        return False
    if _quality(record.get("board_crop_quality")) != "pass":
        return False
    if _quality(record.get("marker_crop_quality")) != "pass":
        return False
    gate = record.get("marker_crop_quality_gate") if isinstance(record.get("marker_crop_quality_gate"), Mapping) else {}
    decision = str(gate.get("decision") or "").strip().lower()
    if decision and decision != "pass":
        return False
    classifier_reason = str(record.get("marker_classifier_reason") or "").strip().lower()
    blocking_reasons = {"bad_crop", "multiple", "multiple_components", "unclear", "ambiguous", "conflict"}
    if classifier_reason in blocking_reasons:
        return False
    if not _has_marker_crop_contract(record):
        return False
    return True


def _has_marker_crop_contract(record: Mapping[str, Any]) -> bool:
    if _first(record, "side_marker_crop_path", "marker_crop_path"):
        return True
    marker_bbox = _first(record, "marker_bbox", "marker_crop_bbox", "side_marker_bbox")
    return isinstance(marker_bbox, (list, tuple)) and len(marker_bbox) == 4


def _manual_side(record: Mapping[str, Any]) -> str:
    side = _normalize_side(
        _first(
            record,
            "manual_side_to_move",
            "expected_side_to_move",
            "side_to_move_label",
            "label_side_to_move",
            "side_to_move",
        )
    )
    if side in {"w", "b"}:
        return side
    marker = str(
        _first(record, "manual_visible_marker", "visible_marker", "marker_label", "label", "manual_marker")
    ).strip().lower()
    if marker in {"outline_triangle", "outline", "triangle_outline", "△", "white", "w"}:
        return "w"
    if marker in {"filled_triangle", "filled", "triangle_filled", "▼", "black", "b"}:
        return "b"
    return "unknown"


def _is_human_verified(record: Mapping[str, Any]) -> bool:
    if record.get("human_verified") is True:
        return True
    source = str(record.get("verification_source") or "").strip().lower()
    if source in HUMAN_VERIFICATION_SOURCES:
        return True
    return bool(record.get("verified_by") and record.get("verified_at") and record.get("label_status") == "verified")


def _inferred_source(record: Mapping[str, Any]) -> str:
    text = " ".join(
        [
            str(record.get("side_to_move_source") or ""),
            str(record.get("side_to_move_status") or ""),
            str(record.get("side_to_move_evidence") or ""),
            str(record.get("side_marker_source") or ""),
            " ".join(str(warning) for warning in record.get("warnings") or [] if str(warning)),
            " ".join(str(code) for code in record.get("acceptance_blocker_codes") or [] if str(code)),
        ]
    ).lower()
    if "text" in text or "caption" in text or "ocr" in text:
        return "text_inferred"
    if "pgn" in text or "movetext" in text or "book_move" in text or "move_number" in text:
        return "pgn_inferred"
    return "unknown"


def _quality(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "ok", "accepted", "valid"}:
        return "pass"
    if text in {"fail", "failed", "blocked", "invalid", "reject", "rejected"}:
        return "fail"
    return "missing"


def _normalize_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side in {"w", "white"}:
        return "w"
    if side in {"b", "black"}:
        return "b"
    return "unknown"


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _first_float(*values: Any) -> float:
    for value in values:
        try:
            if value in (None, ""):
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _rate(count: int, total: int) -> float:
    return round(int(count or 0) / total, 4) if total else 0.0


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _html_item_row(item: Mapping[str, Any]) -> str:
    tier = str(item.get("side_to_move_evidence_tier") or "unknown")
    allowed_class = "allowed" if item.get("full_fen_allowed") else "blocked"
    return (
        "<tr>"
        f"<td><code>{html.escape(str(item.get('diagram_id') or ''))}</code></td>"
        f"<td>{html.escape(str(item.get('page') or ''))}</td>"
        f"<td>{html.escape(str(item.get('side_to_move') or 'unknown'))}</td>"
        f"<td><code>{html.escape(str(item.get('side_to_move_source') or 'unknown'))}</code></td>"
        f"<td class='{html.escape(tier, quote=True)}'>{html.escape(tier)}</td>"
        f"<td>{html.escape(str(item.get('side_to_move_confidence') or 0.0))}</td>"
        f"<td>{html.escape(str(item.get('side_marker_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('marker_crop_quality') or ''))}</td>"
        f"<td class='{allowed_class}'>{'yes' if item.get('full_fen_allowed') else 'no'}</td>"
        f"<td><code>{html.escape(str(item.get('full_fen_blocker') or ''))}</code></td>"
        f"<td>{'yes' if item.get('manual_review_required') else 'no'}</td>"
        "</tr>"
    )
