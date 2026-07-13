from __future__ import annotations

import html
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from chess_side_to_move_fusion import (
    caption_evidence_candidates,
    exact_verified_label_candidates,
    fuse_side_to_move_candidates,
    layout_prior_candidate,
    pgn_evidence_candidates,
)


SIDE_TO_MOVE_SOURCES = {
    "trusted_marker",
    "human_verified",
    "text_inferred",
    "pgn_inferred",
    "conflict",
    "unknown",
}
SIDE_TO_MOVE_TIERS = {"trusted", "verified", "inferred", "conflict", "unknown"}
TRUSTED_MARKER_STATUSES = {"trusted_marker", "trusted_side_marker", "trusted"}
HUMAN_VERIFICATION_SOURCES = {"human", "human_visual", "human_manual", "legacy_human_visual"}
FULL_FEN_BLOCKER_NOT_TRUSTED = "not_trusted_side_to_move"
FULL_FEN_BLOCKER_HUMAN_POLICY = "human_verified_policy_required"
FULL_FEN_BLOCKER_CONFLICT = "side_to_move_evidence_conflict"
MARKER_SEMANTIC_STATUSES = {"trusted", "review", "missing"}
MARKER_OWNERSHIP_STATUSES = {"assigned", "ambiguous", "unassigned"}
BOARD_PLACEMENT_STATUSES = {"accepted", "review"}
MARKER_CONFLICT_WARNINGS = {
    "side_to_move_evidence_conflict",
    "side_to_move_marker_local_conflict",
    "side_to_move_marker_multi_region_conflict",
    "side_to_move_marker_ambiguous",
    "side_to_move_marker_local_ambiguous",
}
MARKER_REVIEW_REASONS = {
    "marker_conflict",
    "marker_missing",
    "multiple",
    "multiple_candidates",
    "unclear",
    "unclear_symbol",
    "ambiguous",
    "conflict",
}


def resolve_marker_semantic_contract(record: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve marker meaning independently from board placement and full-FEN safety."""
    runtime_side = _normalize_side(
        _first(record, "marker_semantic_side", "side_to_move", "side_to_move_detected", "side")
    )
    raw_status = str(
        _first(record, "side_marker_status", "marker_status") or "marker_missing"
    ).strip().lower()
    ownership_status = _marker_ownership_status(record, raw_status=raw_status)
    marker_quality = _quality(
        _first(record, "marker_crop_quality", "side_marker_crop_status")
    )
    gate = (
        record.get("marker_crop_quality_gate")
        if isinstance(record.get("marker_crop_quality_gate"), Mapping)
        else {}
    )
    gate_decision = str(gate.get("decision") or "").strip().lower()
    marker_fail_reasons = {
        reason.lower()
        for reason in [
            *_string_values(record.get("marker_crop_fail_reason")),
            *_string_values(gate.get("reasons")),
        ]
    }
    warnings = {
        warning.lower()
        for warning in _string_values(record.get("warnings"))
    }
    manual_reason = str(record.get("manual_review_reason") or "").strip().lower()
    classifier_reason = str(record.get("marker_classifier_reason") or "").strip().lower()
    blocking_classifier_reasons = {
        "bad_crop",
        "multiple",
        "multiple_components",
        "unclear",
        "ambiguous",
        "conflict",
    }
    trusted = (
        runtime_side in {"w", "b"}
        and raw_status in TRUSTED_MARKER_STATUSES
        and ownership_status == "assigned"
        and marker_quality == "pass"
        and gate_decision in {"", "pass"}
        and not marker_fail_reasons
        and not (warnings & MARKER_CONFLICT_WARNINGS)
        and manual_reason not in MARKER_REVIEW_REASONS
        and classifier_reason not in blocking_classifier_reasons
        and _has_marker_crop_contract(record)
    )
    marker_present = bool(
        raw_status not in {"", "marker_missing", "missing", "none"}
        or _has_marker_crop_contract(record)
        or marker_quality != "missing"
    )
    semantic_status = "trusted" if trusted else "review" if marker_present else "missing"
    confidence = _first_float(
        record.get("marker_semantic_confidence"),
        record.get("side_marker_confidence"),
        record.get("marker_classifier_confidence"),
        record.get("side_to_move_confidence"),
    )
    placement_status = _board_placement_status(record)
    full_fen = _full_fen_contract(
        record,
        side_gate_allowed=trusted,
        side_blocker="marker_semantic_not_trusted",
        board_placement_status=placement_status,
    )
    return {
        "marker_semantic_status": semantic_status,
        "marker_semantic_side": runtime_side if trusted else "unknown",
        "marker_semantic_confidence": round(confidence, 4) if marker_present else 0.0,
        "marker_ownership_status": ownership_status,
        "board_placement_status": placement_status,
        **full_fen,
    }
def resolve_side_to_move_evidence(
    record: Mapping[str, Any],
    *,
    allow_human_verified_full_fen: bool = False,
    verified_labels: Iterable[Mapping[str, Any]] = (),
    source_document_sha256: str = "",
    source_profile_layout_prior: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Produce and fuse side-to-move evidence without bypassing FEN safety."""
    marker_contract = resolve_marker_semantic_contract(record)
    runtime_side = _normalize_side(
        _first(record, "side_to_move", "side_to_move_detected", "side_to_move_code", "side")
    )
    candidates: list[dict[str, Any]] = []
    if marker_contract["marker_semantic_status"] == "trusted":
        confidence = _first_float(
            record.get("side_marker_confidence"),
            record.get("side_to_move_evidence_confidence"),
            record.get("marker_classifier_confidence"),
            record.get("side_to_move_confidence"),
        )
        candidates.append(
            {
                "side": str(marker_contract.get("marker_semantic_side") or runtime_side),
                "source": "trusted_marker",
                "confidence": confidence or 0.95,
                "kind": "visual_marker",
                "support_only": False,
                "provenance": {
                    "marker_status": str(
                        _first(record, "side_marker_status", "marker_status") or ""
                    ),
                    "marker_classifier_version": str(
                        record.get("marker_classifier_version") or ""
                    ),
                    "marker_bbox": list(
                        _first(record, "marker_bbox", "marker_crop_bbox", "side_marker_bbox")
                        or []
                    ),
                },
            }
        )

    manual_side = _manual_side(record)
    if _is_human_verified(record) and manual_side in {"w", "b"}:
        candidates.append(
            {
                "side": manual_side,
                "source": "human_verified",
                "confidence": _first_float(record.get("side_to_move_confidence"), 1.0),
                "kind": "inline_human_verified",
                "support_only": False,
                "provenance": {
                    "verification_source": str(record.get("verification_source") or ""),
                    "verified_by": str(record.get("verified_by") or ""),
                    "verified_at": str(record.get("verified_at") or ""),
                },
            }
        )

    candidates.extend(caption_evidence_candidates(record))
    candidates.extend(pgn_evidence_candidates(record))
    inferred_source = _inferred_source(record)
    if runtime_side in {"w", "b"} and inferred_source in {"text_inferred", "pgn_inferred"}:
        candidates.append(
            {
                "side": runtime_side,
                "source": inferred_source,
                "confidence": _first_float(
                    record.get("side_to_move_evidence_confidence"),
                    record.get("side_to_move_confidence"),
                    record.get("confidence"),
                    0.78 if inferred_source == "text_inferred" else 0.76,
                ),
                "kind": "legacy_preclassified_evidence",
                "support_only": False,
                "provenance": {
                    "side_to_move_evidence": str(record.get("side_to_move_evidence") or ""),
                    "side_to_move_status": str(record.get("side_to_move_status") or ""),
                },
            }
        )
    exact_candidates, rejected_labels = exact_verified_label_candidates(
        record,
        verified_labels,
        source_document_sha256=source_document_sha256,
    )
    candidates.extend(exact_candidates)
    prior = layout_prior_candidate(
        record,
        source_profile_layout_prior=source_profile_layout_prior,
    )
    if prior:
        candidates.append(prior)
    fusion = fuse_side_to_move_candidates(
        candidates,
        rejected_evidence=rejected_labels,
    )
    source = str(fusion.get("source") or "unknown")
    if source == "trusted_marker":
        tier = "trusted"
        side_gate_allowed = True
        side_blocker = "marker_semantic_not_trusted"
        manual_review_required = False
    elif source == "human_verified":
        tier = "verified"
        side_gate_allowed = bool(allow_human_verified_full_fen)
        side_blocker = FULL_FEN_BLOCKER_HUMAN_POLICY
        manual_review_required = not allow_human_verified_full_fen
    elif source in {"text_inferred", "pgn_inferred"}:
        tier = "inferred"
        side_gate_allowed = False
        side_blocker = FULL_FEN_BLOCKER_NOT_TRUSTED
        manual_review_required = True
    elif source == "conflict":
        tier = "conflict"
        side_gate_allowed = False
        side_blocker = FULL_FEN_BLOCKER_CONFLICT
        manual_review_required = True
    else:
        tier = "unknown"
        side_gate_allowed = False
        side_blocker = FULL_FEN_BLOCKER_NOT_TRUSTED
        manual_review_required = True
    full_fen = _full_fen_contract(
        record,
        side_gate_allowed=side_gate_allowed,
        side_blocker=side_blocker,
        board_placement_status=str(marker_contract.get("board_placement_status") or "review"),
    )
    full_fen_allowed = bool(full_fen.get("full_fen_allowed"))
    return _evidence_payload(
        side=str(fusion.get("side") or "unknown"),
        source=source,
        tier=tier,
        confidence=float(fusion.get("confidence") or 0.0),
        full_fen_allowed=full_fen_allowed,
        full_fen_blocker=(
            ""
            if full_fen_allowed
            else str(full_fen.get("full_fen_blocker") or side_blocker)
        ),
        full_fen_blockers=list(full_fen.get("full_fen_blockers") or []),
        manual_review_required=manual_review_required,
        marker_contract=marker_contract,
        fusion_status=str(fusion.get("status") or "unknown"),
        primary_evidence=fusion.get("primary_evidence") or {},
        supporting_evidence=fusion.get("supporting_evidence") or [],
        conflicts=fusion.get("conflicts") or [],
    )


def apply_side_to_move_evidence(
    record: Mapping[str, Any],
    *,
    allow_human_verified_full_fen: bool = False,
    verified_labels: Iterable[Mapping[str, Any]] = (),
    source_document_sha256: str = "",
    source_profile_layout_prior: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    merged = dict(record)
    merged.update(
        resolve_side_to_move_evidence(
            merged,
            allow_human_verified_full_fen=allow_human_verified_full_fen,
            verified_labels=verified_labels,
            source_document_sha256=source_document_sha256,
            source_profile_layout_prior=source_profile_layout_prior,
        )
    )
    return merged


def build_side_to_move_coverage_dashboard(
    records: Iterable[Mapping[str, Any]],
    *,
    allow_human_verified_full_fen: bool = False,
    verified_labels: Iterable[Mapping[str, Any]] = (),
    source_document_sha256: str = "",
    source_profile_layout_prior: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    label_rows = [dict(row) for row in verified_labels if isinstance(row, Mapping)]
    items = [
        _dashboard_item(
            record,
            allow_human_verified_full_fen=allow_human_verified_full_fen,
            verified_labels=label_rows,
            source_document_sha256=source_document_sha256,
            source_profile_layout_prior=source_profile_layout_prior,
        )
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
        "unknown_count": len([item for item in items if item.get("side_to_move") == "unknown"]),
        "conflict_count": int(by_source.get("conflict", 0)),
        "manual_review_required_count": manual_review_count,
        "full_fen_safe_acceptance_count": full_fen_safe_count,
        "side_to_move_coverage_rate": _rate(covered_count, total),
        "trusted_side_to_move_rate": _rate(trusted_count, total),
        "trusted_marker_rate": _rate(by_source.get("trusted_marker", 0), total),
        "human_verified_rate": _rate(by_source.get("human_verified", 0), total),
        "text_inferred_rate": _rate(by_source.get("text_inferred", 0), total),
        "pgn_inferred_rate": _rate(by_source.get("pgn_inferred", 0), total),
        "unknown_rate": _rate(
            len([item for item in items if item.get("side_to_move") == "unknown"]),
            total,
        ),
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
    verified_labels: Iterable[Mapping[str, Any]],
    source_document_sha256: str,
    source_profile_layout_prior: Mapping[str, Any] | str | None,
) -> dict[str, Any]:
    evidence = resolve_side_to_move_evidence(
        record,
        allow_human_verified_full_fen=allow_human_verified_full_fen,
        verified_labels=verified_labels,
        source_document_sha256=source_document_sha256,
        source_profile_layout_prior=source_profile_layout_prior,
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
    full_fen_blockers: list[str],
    manual_review_required: bool,
    marker_contract: Mapping[str, Any],
    fusion_status: str,
    primary_evidence: Mapping[str, Any],
    supporting_evidence: Iterable[Mapping[str, Any]],
    conflicts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        **dict(marker_contract),
        "side_to_move": _normalize_side(side),
        "side_to_move_source": source if source in SIDE_TO_MOVE_SOURCES else "unknown",
        "side_to_move_confidence": round(max(0.0, min(float(confidence or 0.0), 1.0)), 4),
        "side_to_move_evidence_tier": tier if tier in SIDE_TO_MOVE_TIERS else "unknown",
        "full_fen_allowed": bool(full_fen_allowed),
        "full_fen_blocker": str(full_fen_blocker or ""),
        "full_fen_blockers": list(full_fen_blockers),
        "full_fen_review_required": not bool(full_fen_allowed),
        "manual_review_required": bool(manual_review_required),
        "side_to_move_fusion_status": str(fusion_status or "unknown"),
        "side_to_move_primary_evidence": dict(primary_evidence),
        "side_to_move_supporting_evidence": [
            dict(row) for row in supporting_evidence if isinstance(row, Mapping)
        ],
        "side_to_move_conflicts": [
            dict(row) for row in conflicts if isinstance(row, Mapping)
        ],
    }


def _is_trusted_marker_record(record: Mapping[str, Any], *, runtime_side: str) -> bool:
    contract = resolve_marker_semantic_contract(record)
    return runtime_side in {"w", "b"} and contract.get("marker_semantic_status") == "trusted"


def _marker_ownership_status(record: Mapping[str, Any], *, raw_status: str) -> str:
    explicit = str(record.get("marker_ownership_status") or "").strip().lower()
    if explicit in MARKER_OWNERSHIP_STATUSES:
        return explicit
    assignment = str(record.get("marker_assignment_status") or "").strip().lower()
    if assignment == "assigned":
        return "assigned"
    if "ambiguous" in assignment or "conflict" in assignment:
        return "ambiguous"
    if assignment == "unassigned":
        return "unassigned"
    if "conflict" in raw_status or "ambiguous" in raw_status or "multi" in raw_status:
        return "ambiguous"
    if raw_status in TRUSTED_MARKER_STATUSES and _has_marker_crop_contract(record):
        return "assigned"
    return "unassigned"


def _board_placement_status(record: Mapping[str, Any]) -> str:
    if _quality(record.get("board_crop_quality")) == "fail":
        return "review"
    explicit = str(record.get("board_placement_status") or "").strip().lower()
    if explicit in BOARD_PLACEMENT_STATUSES:
        return explicit
    statuses = {
        str(_first(record, "placement_runtime_status", "placement_status") or "").strip().lower(),
        str(_first(record, "full_fen_runtime_status", "full_fen_status", "runtime_status") or "")
        .strip()
        .lower(),
    }
    accepted = {
        "accepted",
        "fen_placement_machine_accepted",
        "fen_machine_accepted",
        "fen_corpus_verified",
        "full_fen_accepted",
    }
    return "accepted" if statuses & accepted else "review"


def _full_fen_contract(
    record: Mapping[str, Any],
    *,
    side_gate_allowed: bool,
    side_blocker: str,
    board_placement_status: str,
) -> dict[str, Any]:
    blockers: list[str] = []
    raw_blockers = record.get("full_fen_blockers")
    if isinstance(raw_blockers, (list, tuple, set)):
        for blocker in raw_blockers:
            if isinstance(blocker, Mapping):
                value = str(blocker.get("code") or blocker.get("reason") or "")
            else:
                value = str(blocker or "")
            if value:
                blockers.append(value)
    legacy_blocker = str(record.get("full_fen_blocker") or "").strip()
    if legacy_blocker:
        blockers.append(legacy_blocker)
    if not side_gate_allowed:
        blockers.append(side_blocker or FULL_FEN_BLOCKER_NOT_TRUSTED)
    if board_placement_status != "accepted":
        blockers.append("board_placement_not_accepted")
    if _quality(record.get("board_crop_quality")) == "fail":
        blockers.append("board_crop_quality_failed")
    full_fen_status = str(
        _first(record, "full_fen_runtime_status", "full_fen_status") or ""
    ).strip().lower()
    if full_fen_status in {
        "review",
        "review_required",
        "fen_review_required",
        "blocked",
        "failed",
    }:
        blockers.append("full_fen_gate_not_accepted")
    unique = list(dict.fromkeys(blockers))
    return {
        "full_fen_allowed": not unique,
        "full_fen_blockers": unique,
        "full_fen_blocker": unique[0] if unique else "",
    }


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


def _string_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, bytes)):
        values = [value]
    elif isinstance(value, Iterable) and not isinstance(value, Mapping):
        values = value
    else:
        values = [value]
    return [str(item).strip() for item in values if str(item).strip()]


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
