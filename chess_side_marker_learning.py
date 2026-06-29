from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping


SCHEMA = "kindlemaster.chess_fen.side_marker_learning.v1"
QUEUE_SCHEMA = "kindlemaster.chess_fen.side_marker_learning_queue.v1"
MIN_VERIFIED_LABELS = 30

REVIEW_ONLY_POLICY = "manual_side_marker_labels_train_and_evaluate_only_no_direct_fen_publication"
HUMAN_SOURCES = {"human", "human_visual", "human_manual", "legacy_human_visual"}
AI_ONLY_SOURCES = {"ai", "ai_assist", "ai_candidate", "ai_review", "ai_review_only", "openai", "openai_review", "gpt"}

PRIORITY_BY_BLOCKER = {
    "marker_crop_not_generated": 0,
    "marker_classifier_missing": 1,
    "marker_classifier_conflict": 2,
    "marker_classifier_ambiguous": 3,
    "trusted_marker_not_propagated": 4,
    "placement_blocks_full_fen": 5,
    "side_unknown_unattributed": 6,
    "marker_probe_not_run": 7,
    "diagram_image_missing": 8,
    "board_bbox_missing": 9,
    "full_fen_review_required": 10,
    "no_side_marker_blocker": 11,
}


def build_side_marker_learning_artifacts(
    records: Iterable[Mapping[str, Any]],
    *,
    blocker_report: Mapping[str, Any] | None = None,
    assignment_report: Mapping[str, Any] | None = None,
    manual_labels: Iterable[Mapping[str, Any]] | None = None,
    min_verified_labels: int = MIN_VERIFIED_LABELS,
    max_queue_items: int = 200,
) -> dict[str, Any]:
    base_rows = _merged_rows(records, blocker_report=blocker_report, assignment_report=assignment_report)
    queue_rows = [_queue_row(row) for row in base_rows]
    queue_rows.sort(key=_queue_sort_key)
    if max_queue_items > 0:
        queue_rows = queue_rows[:max_queue_items]

    label_rows = [dict(row) for row in (manual_labels or []) if isinstance(row, Mapping)]
    usable_labels, label_rejections = _usable_labels(label_rows)
    learning_report = _learning_report(
        base_rows,
        usable_labels,
        rejected_labels=label_rejections,
        min_verified_labels=min_verified_labels,
    )
    template_rows = [_label_template_row(row) for row in queue_rows]
    return {
        "schema": SCHEMA,
        "policy": REVIEW_ONLY_POLICY,
        "summary": {
            "record_count": len(base_rows),
            "queue_count": len(queue_rows),
            "manual_label_count": len(label_rows),
            "usable_manual_label_count": len(usable_labels),
            "rejected_manual_label_count": len(label_rejections),
            "min_verified_labels": int(min_verified_labels),
        },
        "queue": {
            "schema": QUEUE_SCHEMA,
            "policy": REVIEW_ONLY_POLICY,
            "items": queue_rows,
        },
        "manual_label_template": template_rows,
        "learning_report": learning_report,
    }


def side_marker_learning_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    confusion = report.get("confusion") or {}
    lines = [
        "# Chess Side Marker Learning Report",
        "",
        f"- status: {report.get('status', 'UNKNOWN')}",
        f"- usable manual labels: {summary.get('usable_manual_label_count', 0)}",
        f"- minimum labels for calibration: {summary.get('min_verified_labels', MIN_VERIFIED_LABELS)}",
        f"- rejected manual labels: {summary.get('rejected_manual_label_count', 0)}",
        f"- false negatives: {confusion.get('false_negative_marker_count', 0)}",
        f"- false positives: {confusion.get('false_positive_marker_count', 0)}",
        f"- trusted wrong side: {confusion.get('trusted_wrong_side_count', 0)}",
        f"- conflict but manually clear: {confusion.get('conflict_resolvable_count', 0)}",
        "",
        "## Policy",
        "",
        f"- {report.get('policy', REVIEW_ONLY_POLICY)}",
        "- Manual labels feed training, evaluation, and blocker attribution only.",
        "- They do not directly publish full FEN or bypass marker trust gates.",
        "",
        "## Suggestions",
        "",
    ]
    suggestions = report.get("suggestions") or []
    if suggestions:
        for item in suggestions:
            lines.append(
                "- {action} ({count} labels, blocker `{blocker}`): {reason}".format(
                    action=_md(str(item.get("action") or "")),
                    count=item.get("count", 0),
                    blocker=_md(str(item.get("blocker") or "")),
                    reason=_md(str(item.get("reason") or "")),
                )
            )
    else:
        lines.append("- none yet; collect verified marker labels first.")
    lines.extend(
        [
            "",
            "## By Blocker",
            "",
            "| Blocker | Labels | Manual clear | Manual none | System matches | System misses | System conflicts |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for blocker, row in (report.get("by_blocker") or {}).items():
        lines.append(
            "| {blocker} | {labels} | {clear} | {none} | {matches} | {misses} | {conflicts} |".format(
                blocker=_md(str(blocker)),
                labels=row.get("label_count", 0),
                clear=row.get("manual_clear_marker_count", 0),
                none=row.get("manual_no_marker_count", 0),
                matches=row.get("system_match_count", 0),
                misses=row.get("system_missed_clear_marker_count", 0),
                conflicts=row.get("system_conflict_on_clear_marker_count", 0),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def persisted_side_marker_learning_report(report: Mapping[str, Any]) -> dict[str, Any]:
    rejection_counts = Counter(str(item.get("code") or "unknown") for item in report.get("label_rejections") or [])
    return {
        "schema": report.get("schema") or SCHEMA,
        "status": report.get("status") or "UNKNOWN",
        "policy": report.get("policy") or REVIEW_ONLY_POLICY,
        "summary": dict(report.get("summary") or {}),
        "confusion": dict(report.get("confusion") or {}),
        "by_blocker": dict(report.get("by_blocker") or {}),
        "suggestions": list(report.get("suggestions") or []),
        "label_rejection_counts": {key: int(value) for key, value in sorted(rejection_counts.items())},
        "training_data_gap": dict(report.get("training_data_gap") or {}),
    }


def side_marker_learning_review_html(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    learning = payload.get("learning_report") or {}
    cards = "\n".join(_review_card(row) for row in (payload.get("queue") or {}).get("items") or [])
    if not cards:
        cards = "<p class=\"empty\">No side-marker learning rows found.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chess Side Marker Learning Queue</title>
  <style>
    :root {{
      --bg:#f6f7f9; --surface:#ffffff; --ink:#172033; --muted:#5d6878;
      --line:#d9e0ea; --primary:#1d4ed8; --warn:#8a5a00; --bad:#b42318;
      --good:#157347; --soft:#eef3f8;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ border-bottom:1px solid var(--line); background:rgba(246,247,249,.96); position:sticky; top:0; z-index:10; }}
    .bar {{ max-width:1440px; margin:0 auto; padding:18px clamp(16px,3vw,32px); display:grid; gap:14px; }}
    h1 {{ margin:0; font-size:1.35rem; line-height:1.2; }}
    .meta {{ margin:4px 0 0; color:var(--muted); font-size:.92rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:8px; }}
    .stat {{ min-height:64px; border:1px solid var(--line); border-radius:8px; background:var(--surface); padding:10px 12px; }}
    .stat span {{ display:block; color:var(--muted); font-size:.76rem; }}
    .stat strong {{ display:block; margin-top:4px; font-size:1.14rem; }}
    main {{ max-width:1440px; margin:0 auto; padding:18px clamp(16px,3vw,32px) 40px; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; align-items:start; }}
    article {{ border:1px solid var(--line); border-radius:8px; background:var(--surface); overflow:hidden; }}
    .head {{ padding:14px 14px 10px; display:flex; align-items:flex-start; justify-content:space-between; gap:12px; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0; font-size:1rem; overflow-wrap:anywhere; }}
    .page {{ color:var(--muted); font-size:.82rem; margin-top:3px; }}
    .badges {{ display:flex; flex-wrap:wrap; gap:6px; justify-content:flex-end; }}
    .badge {{ border:1px solid var(--line); border-radius:999px; padding:3px 8px; color:var(--muted); background:#f8fafc; font-size:.75rem; white-space:nowrap; }}
    .badge.bad {{ color:var(--bad); border-color:rgba(180,35,24,.35); background:rgba(180,35,24,.08); }}
    .badge.warn {{ color:var(--warn); border-color:rgba(138,90,0,.35); background:rgba(138,90,0,.08); }}
    .badge.good {{ color:var(--good); border-color:rgba(21,115,71,.35); background:rgba(21,115,71,.08); }}
    .media-grid {{ display:grid; grid-template-columns:1.15fr .72fr 1.15fr; gap:8px; padding:12px 14px; background:#fbfcfe; }}
    figure {{ margin:0; min-width:0; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:var(--soft); }}
    figcaption {{ padding:7px 8px; color:var(--muted); font-size:.76rem; border-bottom:1px solid var(--line); background:rgba(255,255,255,.75); }}
    .media {{ min-height:148px; display:grid; place-items:center; padding:8px; }}
    .media.marker {{ min-height:104px; }}
    img {{ display:block; width:100%; max-height:260px; object-fit:contain; }}
    .no-img {{ color:var(--muted); font-size:.86rem; padding:14px; text-align:center; }}
    .body {{ padding:12px 14px 14px; }}
    dl {{ display:grid; grid-template-columns:minmax(132px,auto) 1fr; gap:6px 10px; margin:0 0 12px; font-size:.88rem; }}
    dt {{ color:var(--muted); font-weight:700; }}
    dd {{ margin:0; overflow-wrap:anywhere; }}
    code {{ font-family:"Cascadia Mono","Courier New",monospace; border:1px solid #dbe4ff; background:#eef2ff; border-radius:6px; padding:1px 4px; }}
    .template {{ border-top:1px solid var(--line); padding-top:10px; }}
    .template label {{ display:block; color:var(--muted); font-size:.78rem; font-weight:700; margin-bottom:5px; }}
    textarea {{ width:100%; min-height:96px; resize:vertical; border:1px solid var(--line); border-radius:8px; padding:9px; background:#fff; color:var(--ink); font-family:"Cascadia Mono","Courier New",monospace; font-size:.79rem; line-height:1.42; }}
    textarea:focus-visible {{ outline:3px solid rgba(29,78,216,.22); outline-offset:2px; border-color:var(--primary); }}
    .empty {{ padding:24px; border:1px solid var(--line); background:var(--surface); border-radius:8px; }}
    @media (max-width: 980px) {{
      header {{ position:static; }}
      .grid {{ grid-template-columns:1fr; }}
      .stats {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
    }}
    @media (max-width: 640px) {{
      .stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .head {{ display:block; }}
      .badges {{ justify-content:flex-start; margin-top:8px; }}
      .media-grid {{ grid-template-columns:1fr; }}
      dl {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div>
        <h1>Chess Side Marker Learning Queue</h1>
        <p class="meta">Manual labels train and evaluate marker logic. They do not directly publish full FEN.</p>
      </div>
      <section class="stats" aria-label="Learning queue summary">
        <div class="stat"><span>Queue</span><strong>{_h(summary.get('queue_count', 0))}</strong></div>
        <div class="stat"><span>Manual labels</span><strong>{_h(summary.get('manual_label_count', 0))}</strong></div>
        <div class="stat"><span>Usable labels</span><strong>{_h(summary.get('usable_manual_label_count', 0))}</strong></div>
        <div class="stat"><span>Status</span><strong>{_h(learning.get('status', 'UNKNOWN'))}</strong></div>
        <div class="stat"><span>Policy</span><strong>review-only</strong></div>
      </section>
    </div>
  </header>
  <main><section class="grid">{cards}</section></main>
</body>
</html>"""


def _merged_rows(
    records: Iterable[Mapping[str, Any]],
    *,
    blocker_report: Mapping[str, Any] | None,
    assignment_report: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    blockers = {
        str(item.get("diagram_id") or item.get("id") or ""): item
        for item in (blocker_report or {}).get("items", [])
        if isinstance(item, Mapping)
    }
    assignments = {
        str(item.get("diagram_id") or item.get("id") or ""): item
        for item in (assignment_report or {}).get("items", [])
        if isinstance(item, Mapping)
    }
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            continue
        diagram_id = str(record.get("diagram_id") or record.get("id") or f"diagram-{index}")
        merged = {**dict(record), **dict(assignments.get(diagram_id, {})), **dict(blockers.get(diagram_id, {}))}
        merged["diagram_id"] = diagram_id
        rows.append(merged)
    return rows


def _queue_row(record: Mapping[str, Any]) -> dict[str, Any]:
    blocker = str(record.get("primary_side_marker_blocker") or _fallback_blocker(record))
    status = str(record.get("side_marker_status") or "marker_missing")
    side = _normalize_side(record.get("side_to_move")) or "unknown"
    priority = PRIORITY_BY_BLOCKER.get(blocker, 50)
    if blocker == "no_side_marker_blocker":
        priority += 40
    elif _is_trusted_marker(record) and side in {"w", "b"}:
        priority += 20
    return {
        "schema": QUEUE_SCHEMA,
        "diagram_id": str(record.get("diagram_id") or ""),
        "page": record.get("page") or record.get("page_number") or "",
        "priority": priority,
        "primary_side_marker_blocker": blocker,
        "system_side_to_move": side,
        "system_side_marker_status": status,
        "system_side_marker_symbol": str(record.get("side_marker_symbol") or ""),
        "system_side_marker_confidence": record.get("side_marker_confidence") or "",
        "board_crop_path": str(record.get("board_crop_path") or ""),
        "side_marker_crop_path": str(record.get("side_marker_crop_path") or ""),
        "debug_overlay_path": str(record.get("debug_overlay_path") or ""),
        "placement_status": str(record.get("placement_status") or record.get("placement_runtime_status") or ""),
        "full_fen_status": str(record.get("full_fen_status") or record.get("full_fen_runtime_status") or record.get("runtime_status") or ""),
        "acceptance_blocker_codes": list(record.get("acceptance_blocker_codes") or []),
        "side_marker_assignment_trace": dict(record.get("side_marker_assignment_trace") or {}),
        "label_status": "needs_manual_marker",
        "manual_visible_marker": "",
        "manual_side_to_move": "",
        "manual_marker_shape": "",
        "manual_marker_location": "",
        "manual_marker_bbox": "",
        "manual_notes": "",
        "human_verified": False,
        "accepted_for_runtime": False,
        "accepted_for_corpus": False,
        "policy": REVIEW_ONLY_POLICY,
    }


def _label_template_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "diagram_id": row.get("diagram_id") or "",
        "page": row.get("page") or "",
        "board_crop_path": row.get("board_crop_path") or "",
        "side_marker_crop_path": row.get("side_marker_crop_path") or "",
        "debug_overlay_path": row.get("debug_overlay_path") or "",
        "system_side_to_move": row.get("system_side_to_move") or "unknown",
        "system_side_marker_status": row.get("system_side_marker_status") or "",
        "primary_side_marker_blocker": row.get("primary_side_marker_blocker") or "",
        "manual_visible_marker": "",
        "manual_side_to_move": "",
        "manual_marker_shape": "",
        "manual_marker_location": "",
        "manual_marker_bbox": "",
        "label_status": "needs_manual_marker",
        "human_verified": False,
        "verification_source": "",
        "verified_by": "",
        "verified_at": "",
        "manual_notes": "",
        "accepted_for_runtime": False,
        "accepted_for_corpus": False,
        "policy": REVIEW_ONLY_POLICY,
    }


def _learning_report(
    records: list[dict[str, Any]],
    labels_by_id: Mapping[str, dict[str, Any]],
    *,
    rejected_labels: list[dict[str, Any]],
    min_verified_labels: int,
) -> dict[str, Any]:
    rows_by_id = {str(row.get("diagram_id") or ""): row for row in records}
    comparisons = []
    by_blocker: dict[str, Counter[str]] = defaultdict(Counter)
    confusion = Counter()
    for diagram_id, label in labels_by_id.items():
        record = rows_by_id.get(diagram_id, {"diagram_id": diagram_id})
        comparison = _compare_label(record, label)
        comparisons.append(comparison)
        blocker = comparison["primary_side_marker_blocker"]
        by_blocker[blocker]["label_count"] += 1
        if comparison["manual_side_to_move"] in {"w", "b"}:
            by_blocker[blocker]["manual_clear_marker_count"] += 1
        if comparison["manual_marker_class"] in {"none", "not_marker"}:
            by_blocker[blocker]["manual_no_marker_count"] += 1
        if comparison["outcome"] == "match":
            by_blocker[blocker]["system_match_count"] += 1
            confusion["trusted_correct_count"] += 1
        elif comparison["outcome"] == "trusted_wrong_side":
            by_blocker[blocker]["system_wrong_side_count"] += 1
            confusion["trusted_wrong_side_count"] += 1
        elif comparison["outcome"] == "system_missed_clear_marker":
            by_blocker[blocker]["system_missed_clear_marker_count"] += 1
            confusion["false_negative_marker_count"] += 1
        elif comparison["outcome"] == "system_conflict_on_clear_marker":
            by_blocker[blocker]["system_conflict_on_clear_marker_count"] += 1
            confusion["conflict_resolvable_count"] += 1
        elif comparison["outcome"] == "trusted_but_manual_no_marker":
            by_blocker[blocker]["trusted_but_manual_no_marker_count"] += 1
            confusion["false_positive_marker_count"] += 1
        elif comparison["outcome"] == "manual_uncertain":
            confusion["manual_uncertain_count"] += 1
        else:
            confusion["other_count"] += 1

    usable_count = len(labels_by_id)
    status = "READY_FOR_RULE_CALIBRATION" if usable_count >= int(min_verified_labels) else "TRAINING_DATA_GAP"
    missing_data = []
    if status == "TRAINING_DATA_GAP":
        missing_data.append(
            {
                "field": "human_verified_side_marker_labels",
                "needed": int(min_verified_labels),
                "available": usable_count,
            }
        )
    by_blocker_payload = {
        blocker: {key: int(value) for key, value in sorted(counter.items())}
        for blocker, counter in sorted(by_blocker.items())
    }
    return {
        "schema": SCHEMA,
        "status": status,
        "policy": REVIEW_ONLY_POLICY,
        "summary": {
            "source_record_count": len(records),
            "usable_manual_label_count": usable_count,
            "rejected_manual_label_count": len(rejected_labels),
            "min_verified_labels": int(min_verified_labels),
            "matched_record_count": len([row for row in comparisons if row.get("record_found")]),
        },
        "confusion": {key: int(value) for key, value in sorted(confusion.items())},
        "by_blocker": by_blocker_payload,
        "suggestions": _suggestions(by_blocker_payload, confusion),
        "comparisons": comparisons,
        "label_rejections": rejected_labels,
        "training_data_gap": {
            "status": status,
            "message": "TRAINING_DATA_GAP: collect at least 30 human-verified side-marker labels before calibration."
            if status == "TRAINING_DATA_GAP"
            else "",
            "missing_data": missing_data,
        },
    }


def _compare_label(record: Mapping[str, Any], label: Mapping[str, Any]) -> dict[str, Any]:
    manual_side = _manual_side(label)
    manual_class = _manual_marker_class(label)
    system_side = _normalize_side(record.get("side_to_move")) or "unknown"
    marker_status = str(record.get("side_marker_status") or "marker_missing")
    blocker = str(record.get("primary_side_marker_blocker") or _fallback_blocker(record))
    trusted = _is_trusted_marker(record)
    conflict = "conflict" in marker_status or "multi" in marker_status
    ambiguous = "ambiguous" in marker_status or "noisy" in marker_status
    missing = marker_status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker", "inferred_only"}

    if manual_side not in {"w", "b"} and manual_class not in {"none", "not_marker"}:
        outcome = "manual_uncertain"
    elif manual_class in {"none", "not_marker"} and trusted:
        outcome = "trusted_but_manual_no_marker"
    elif manual_side in {"w", "b"} and trusted and system_side == manual_side:
        outcome = "match"
    elif manual_side in {"w", "b"} and trusted and system_side != manual_side:
        outcome = "trusted_wrong_side"
    elif manual_side in {"w", "b"} and conflict:
        outcome = "system_conflict_on_clear_marker"
    elif manual_side in {"w", "b"} and (missing or ambiguous):
        outcome = "system_missed_clear_marker"
    else:
        outcome = "other"

    return {
        "diagram_id": str(record.get("diagram_id") or label.get("diagram_id") or label.get("id") or ""),
        "record_found": bool(record.get("diagram_id")),
        "page": record.get("page") or label.get("page") or "",
        "primary_side_marker_blocker": blocker,
        "manual_side_to_move": manual_side or "unknown",
        "manual_marker_class": manual_class,
        "system_side_to_move": system_side,
        "system_side_marker_status": marker_status,
        "system_trusted_marker": trusted,
        "outcome": outcome,
        "accepted_for_runtime": False,
    }


def _usable_labels(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    usable: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        diagram_id = str(row.get("diagram_id") or row.get("id") or "").strip()
        if not diagram_id:
            rejected.append({"line": index, "code": "diagram_id_missing"})
            continue
        source = str(row.get("verification_source") or row.get("label_source") or "").strip().lower()
        if source in AI_ONLY_SOURCES:
            rejected.append({"line": index, "diagram_id": diagram_id, "code": "ai_only_label_ignored", "verification_source": source})
            continue
        if not _human_verified(row, source=source):
            rejected.append({"line": index, "diagram_id": diagram_id, "code": "human_verification_missing"})
            continue
        label_status = str(row.get("label_status") or "").strip().lower()
        if label_status not in {"verified", "human_verified", "accepted"}:
            rejected.append({"line": index, "diagram_id": diagram_id, "code": "label_status_not_verified", "label_status": label_status})
            continue
        if not (_manual_side(row) or _manual_marker_class(row) in {"none", "not_marker", "unclear", "multiple", "bad_crop"}):
            rejected.append({"line": index, "diagram_id": diagram_id, "code": "manual_marker_label_missing"})
            continue
        normalized = dict(row)
        normalized["accepted_for_runtime"] = False
        normalized["accepted_for_corpus"] = False
        normalized["policy"] = REVIEW_ONLY_POLICY
        usable[diagram_id] = normalized
    return usable, rejected


def _human_verified(row: Mapping[str, Any], *, source: str) -> bool:
    if row.get("human_verified") is True:
        return True
    if source in HUMAN_SOURCES:
        return True
    return bool(row.get("verified_by") and row.get("verified_at") and str(row.get("label_status") or "").lower() == "verified")


def _manual_side(row: Mapping[str, Any]) -> str:
    side = _normalize_side(
        row.get("manual_side_to_move")
        or row.get("expected_side_to_move")
        or row.get("side_to_move_label")
        or row.get("side_to_move")
    )
    if side:
        return side
    marker_class = _manual_marker_class(row)
    if marker_class in {"outline_triangle", "white_marker"}:
        return "w"
    if marker_class in {"filled_triangle", "black_marker"}:
        return "b"
    return ""


def _manual_marker_class(row: Mapping[str, Any]) -> str:
    value = str(
        row.get("manual_visible_marker")
        or row.get("manual_marker_shape")
        or row.get("expected_marker")
        or row.get("marker_label")
        or ""
    ).strip().lower()
    value = value.replace("-", "_").replace(" ", "_")
    aliases = {
        "white": "white_marker",
        "w": "white_marker",
        "outline": "outline_triangle",
        "outline_triangle": "outline_triangle",
        "triangle_outline": "outline_triangle",
        "empty_triangle": "outline_triangle",
        "black": "black_marker",
        "b": "black_marker",
        "filled": "filled_triangle",
        "filled_triangle": "filled_triangle",
        "triangle_filled": "filled_triangle",
        "none": "none",
        "no_marker": "none",
        "not_marker": "not_marker",
        "multiple": "multiple",
        "multi": "multiple",
        "unclear": "unclear",
        "ambiguous": "unclear",
        "bad_crop": "bad_crop",
        "skip": "unclear",
    }
    return aliases.get(value, value or "unclear")


def _normalize_side(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side in {"w", "white", "white_to_move"}:
        return "w"
    if side in {"b", "black", "black_to_move"}:
        return "b"
    return ""


def _is_trusted_marker(record: Mapping[str, Any]) -> bool:
    status = str(record.get("side_marker_status") or "").lower()
    side = _normalize_side(record.get("side_to_move"))
    return bool(side in {"w", "b"} and (status.startswith("trusted_") or status == "trusted_marker"))


def _fallback_blocker(record: Mapping[str, Any]) -> str:
    status = str(record.get("side_marker_status") or "").lower()
    if "conflict" in status or "multi" in status:
        return "marker_classifier_conflict"
    if "ambiguous" in status or "noisy" in status:
        return "marker_classifier_ambiguous"
    if status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker", "inferred_only"}:
        if not record.get("side_marker_crop_path"):
            return "marker_crop_not_generated"
        return "marker_classifier_missing"
    if _is_trusted_marker(record) and not str(record.get("full_fen_status") or record.get("full_fen_runtime_status") or ""):
        return "full_fen_review_required"
    return "no_side_marker_blocker"


def _queue_sort_key(row: Mapping[str, Any]) -> tuple[int, int, str]:
    priority = row.get("priority")
    return (
        _safe_int(priority, default=999),
        _safe_int(row.get("page"), default=0),
        str(row.get("diagram_id") or ""),
    )


def _safe_int(value: Any, *, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _suggestions(by_blocker: Mapping[str, Mapping[str, int]], confusion: Mapping[str, int]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for blocker, row in by_blocker.items():
        missed = int(row.get("system_missed_clear_marker_count") or 0)
        conflicts = int(row.get("system_conflict_on_clear_marker_count") or 0)
        wrong = int(row.get("system_wrong_side_count") or 0)
        false_positive = int(row.get("trusted_but_manual_no_marker_count") or 0)
        if blocker in {"marker_classifier_missing", "marker_crop_not_generated", "marker_probe_not_run"} and missed:
            suggestions.append(
                {
                    "blocker": blocker,
                    "count": missed,
                    "action": "expand marker probe coverage",
                    "reason": "Human labels show a clear marker where the deterministic probe missed it.",
                }
            )
        if blocker in {"marker_classifier_conflict", "side_unknown_unattributed"} and conflicts:
            suggestions.append(
                {
                    "blocker": blocker,
                    "count": conflicts,
                    "action": "improve marker-region arbitration",
                    "reason": "Human labels resolve cases currently treated as conflicts.",
                }
            )
        if blocker == "marker_classifier_ambiguous" and missed:
            suggestions.append(
                {
                    "blocker": blocker,
                    "count": missed,
                    "action": "calibrate contour and density thresholds",
                    "reason": "Human labels identify clear markers in cases classified as ambiguous.",
                }
            )
        if wrong or false_positive:
            suggestions.append(
                {
                    "blocker": blocker,
                    "count": wrong + false_positive,
                    "action": "tighten trusted-marker promotion",
                    "reason": "Trusted system predictions disagree with human labels and must stay gated.",
                }
            )
    suggestions.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("blocker") or "")))
    if not suggestions and int(confusion.get("trusted_correct_count") or 0) > 0:
        suggestions.append(
            {
                "blocker": "trusted_marker",
                "count": int(confusion.get("trusted_correct_count") or 0),
                "action": "preserve current trusted-marker gate",
                "reason": "Human labels agree with trusted marker assignments in the reviewed set.",
            }
        )
    return suggestions


def _review_card(row: Mapping[str, Any]) -> str:
    priority = int(row.get("priority") or 999)
    status = str(row.get("system_side_marker_status") or "")
    badge_class = "good" if status.startswith("trusted") else ("bad" if "conflict" in status else "warn")
    template = _label_template_row(row)
    template_json = json.dumps(template, ensure_ascii=False, indent=2)
    return f"""<article>
  <div class="head">
    <div><h2>{_h(row.get('diagram_id'))}</h2><div class="page">Page {_h(row.get('page'))}</div></div>
    <div class="badges">
      <span class="badge">P{priority}</span>
      <span class="badge {badge_class}">{_h(status)}</span>
      <span class="badge warn">{_h(row.get('primary_side_marker_blocker'))}</span>
    </div>
  </div>
  <div class="media-grid">
    {_figure('Board crop', row.get('board_crop_path'), row.get('diagram_id'), 'board')}
    {_figure('Marker crop', row.get('side_marker_crop_path'), row.get('diagram_id'), 'marker')}
    {_figure('Debug overlay', row.get('debug_overlay_path'), row.get('diagram_id'), 'overlay')}
  </div>
  <div class="body">
    <dl>
      <dt>System side</dt><dd>{_h(row.get('system_side_to_move'))}</dd>
      <dt>Marker symbol</dt><dd>{_h(row.get('system_side_marker_symbol'))}</dd>
      <dt>Placement</dt><dd>{_h(row.get('placement_status'))}</dd>
      <dt>Full FEN</dt><dd>{_h(row.get('full_fen_status'))}</dd>
      <dt>Policy</dt><dd>{_h(REVIEW_ONLY_POLICY)}</dd>
    </dl>
    <div class="template">
      <label for="label-{_attr(row.get('diagram_id'))}">JSONL row to fill</label>
      <textarea id="label-{_attr(row.get('diagram_id'))}" spellcheck="false">{_h(template_json)}</textarea>
    </div>
  </div>
</article>"""


def _figure(label: str, src: Any, diagram_id: Any, class_name: str) -> str:
    value = str(src or "")
    if value:
        media = f'<img src="{_attr(_relative_report_src(value))}" alt="{_attr(label)} for {_attr(diagram_id)}">'
    else:
        media = '<div class="no-img">Unavailable</div>'
    return f'<figure><figcaption>{_h(label)}</figcaption><div class="media {class_name}">{media}</div></figure>'


def _relative_report_src(value: str) -> str:
    normalized = value.replace("\\", "/")
    if normalized.startswith("../") or normalized.startswith("./") or "://" in normalized:
        return normalized
    if normalized.startswith("reports/"):
        return "../../" + normalized
    return "../../" + normalized


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _attr(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
