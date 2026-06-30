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
    review_status = _review_status(learning, payload)
    rows = (payload.get("queue") or {}).get("items") or []
    cards = "\n".join(_review_card(row, index) for index, row in enumerate(rows, start=1))
    has_cards = bool(cards)
    content = cards if has_cards else _empty_review_state(summary, learning, payload, review_status)
    toolbar = _review_toolbar() if has_cards else ""
    return f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Oznaczanie markerów ruchu - KindleMaster</title>
  <style>
    :root {{
      --bg:#f5f7fb; --surface:#ffffff; --ink:#172033; --muted:#5d6878;
      --line:#d9e0ea; --primary:#2456c2; --primary-ink:#ffffff;
      --warn:#8a5a00; --bad:#b42318; --good:#157347; --soft:#eef3f8;
      --focus:rgba(36,86,194,.24);
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0; background:var(--bg); color:var(--ink);
      font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size:15px; line-height:1.5;
    }}
    header {{ border-bottom:1px solid var(--line); background:rgba(245,247,251,.97); position:sticky; top:0; z-index:10; }}
    .bar {{ max-width:1440px; margin:0 auto; padding:18px clamp(16px,3vw,32px); display:grid; gap:14px; }}
    .title-row {{ display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }}
    h1 {{ margin:0; font-size:1.45rem; line-height:1.2; letter-spacing:0; }}
    .meta {{ margin:5px 0 0; color:var(--muted); font-size:.94rem; max-width:850px; }}
    .guide {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }}
    .guide-step {{ border:1px solid var(--line); border-radius:8px; background:var(--surface); padding:10px 12px; }}
    .guide-step strong {{ display:block; font-size:.9rem; }}
    .guide-step span {{ display:block; margin-top:2px; color:var(--muted); font-size:.82rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(5,minmax(124px,1fr)); gap:8px; }}
    .stat {{ min-height:64px; border:1px solid var(--line); border-radius:8px; background:var(--surface); padding:10px 12px; }}
    .stat span {{ display:block; color:var(--muted); font-size:.76rem; }}
    .stat strong {{ display:block; margin-top:4px; font-size:1.08rem; overflow-wrap:anywhere; }}
    main {{ max-width:1440px; margin:0 auto; padding:18px clamp(16px,3vw,32px) 42px; }}
    .toolbar {{ border:1px solid var(--line); border-radius:8px; background:var(--surface); padding:12px; margin-bottom:14px; display:flex; align-items:center; justify-content:space-between; gap:12px; }}
    .toolbar-text strong {{ display:block; }}
    .toolbar-text span {{ display:block; color:var(--muted); font-size:.86rem; }}
    .toolbar-actions {{ display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; align-items:start; }}
    article {{ border:1px solid var(--line); border-radius:8px; background:var(--surface); overflow:hidden; }}
    .head {{ padding:14px 14px 10px; display:flex; align-items:flex-start; justify-content:space-between; gap:12px; border-bottom:1px solid var(--line); }}
    h2 {{ margin:0; font-size:1rem; line-height:1.3; overflow-wrap:anywhere; }}
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
    .body {{ padding:12px 14px 14px; display:grid; gap:14px; }}
    dl {{ display:grid; grid-template-columns:minmax(132px,auto) 1fr; gap:6px 10px; margin:0; font-size:.88rem; }}
    dt {{ color:var(--muted); font-weight:700; }}
    dd {{ margin:0; overflow-wrap:anywhere; }}
    code {{ font-family:"Cascadia Mono","Courier New",monospace; border:1px solid #dbe4ff; background:#eef2ff; border-radius:6px; padding:1px 4px; overflow-wrap:anywhere; word-break:break-word; }}
    form {{ display:grid; gap:12px; }}
    fieldset {{ margin:0; border:1px solid var(--line); border-radius:8px; padding:11px; min-width:0; }}
    legend {{ padding:0 4px; font-weight:800; font-size:.9rem; }}
    .hint {{ color:var(--muted); font-size:.84rem; margin:3px 0 9px; }}
    .choice-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }}
    .choice {{ display:grid; grid-template-columns:auto 1fr; gap:8px; align-items:start; min-height:44px; border:1px solid var(--line); border-radius:8px; padding:8px; background:#fff; cursor:pointer; }}
    .choice:hover {{ border-color:#b8c7de; background:#f8fbff; }}
    .choice input {{ margin-top:3px; }}
    .choice strong {{ display:block; font-size:.9rem; }}
    .choice span {{ display:block; color:var(--muted); font-size:.79rem; }}
    .field-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }}
    .field {{ display:grid; gap:5px; }}
    .field label, .checkline span {{ color:var(--muted); font-size:.79rem; font-weight:700; }}
    input[type="text"], textarea {{
      width:100%; border:1px solid var(--line); border-radius:8px; padding:9px; background:#fff; color:var(--ink);
      font:inherit; min-height:42px;
    }}
    textarea {{ min-height:90px; resize:vertical; font-family:"Cascadia Mono","Courier New",monospace; font-size:.79rem; line-height:1.42; }}
    .notes {{ font-family:inherit; font-size:.88rem; }}
    .checkline {{ display:flex; align-items:flex-start; gap:9px; min-height:44px; }}
    .checkline input {{ margin-top:5px; }}
    .template {{ border-top:1px solid var(--line); padding-top:10px; display:grid; gap:8px; }}
    .template-row {{ display:flex; align-items:center; justify-content:space-between; gap:8px; }}
    .template-row label {{ display:block; color:var(--muted); font-size:.78rem; font-weight:800; }}
    .json-output {{ min-height:134px; }}
    button {{
      min-height:42px; border:1px solid var(--line); border-radius:8px; padding:8px 12px;
      background:#fff; color:var(--ink); font-weight:800; cursor:pointer;
    }}
    button.primary {{ background:var(--primary); color:var(--primary-ink); border-color:var(--primary); }}
    button:hover {{ border-color:#b8c7de; background:#f8fbff; }}
    button.primary:hover {{ background:#1e48a8; border-color:#1e48a8; }}
    input:focus-visible, textarea:focus-visible, button:focus-visible, .choice:focus-within {{ outline:3px solid var(--focus); outline-offset:2px; border-color:var(--primary); }}
    .empty-state {{ border:1px solid var(--line); background:var(--surface); border-radius:8px; padding:22px; max-width:920px; min-width:0; }}
    .empty-state h2 {{ font-size:1.12rem; margin-bottom:8px; }}
    .empty-state p {{ margin:0 0 12px; color:var(--muted); }}
    .empty-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:12px; }}
    .empty-block {{ border:1px solid var(--line); border-radius:8px; padding:12px; background:#fbfcfe; min-width:0; }}
    .empty-block.alert {{ border-color:rgba(180,35,24,.35); background:rgba(180,35,24,.06); }}
    .empty-block.alert h3 {{ color:var(--bad); }}
    .empty-block h3 {{ margin:0 0 8px; font-size:.94rem; }}
    .empty-block ul {{ margin:0; padding-left:19px; color:var(--muted); }}
    pre {{ margin:8px 0 0; max-width:100%; min-width:0; white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; border:1px solid #dbe4ff; background:#eef2ff; border-radius:8px; padding:10px; font-size:.82rem; }}
    @media (max-width: 980px) {{
      header {{ position:static; }}
      .grid {{ grid-template-columns:1fr; }}
      .stats {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
      .guide {{ grid-template-columns:1fr; }}
      .toolbar {{ align-items:flex-start; flex-direction:column; }}
      .toolbar-actions {{ justify-content:flex-start; }}
    }}
    @media (max-width: 640px) {{
      .title-row {{ display:block; }}
      .stats, .empty-grid, .field-grid, .choice-grid {{ grid-template-columns:1fr; }}
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
      <div class="title-row">
        <div>
          <h1>Oznaczanie markerów ruchu</h1>
          <p class="meta">Uzupełniasz tylko marker przy diagramie: <strong>△ = białe mają ruch</strong>, <strong>▼ = czarne mają ruch</strong>. Etykiety uczą i oceniają logikę markera; nie publikują automatycznie pełnego FEN.</p>
        </div>
      </div>
      <section class="guide" aria-label="Jak oznaczać">
        <div class="guide-step"><strong>1. Sprawdź crop markera</strong><span>Patrz na mały wycinek obok planszy, nie na układ figur.</span></div>
        <div class="guide-step"><strong>2. Wybierz widoczny znak</strong><span>△ oznacza ruch białych, ▼ oznacza ruch czarnych; nie zgaduj przy szumie.</span></div>
        <div class="guide-step"><strong>3. Skopiuj JSONL</strong><span>Zaznacz “sprawdzone przez człowieka”, pobierz JSONL i użyj go jako ręczne etykiety.</span></div>
      </section>
      <section class="stats" aria-label="Podsumowanie kolejki oznaczania">
        <div class="stat"><span>Pozycje w kolejce</span><strong>{_h(summary.get('queue_count', 0))}</strong></div>
        <div class="stat"><span>Ręczne etykiety</span><strong>{_h(summary.get('manual_label_count', 0))}</strong></div>
        <div class="stat"><span>Użyteczne etykiety</span><strong>{_h(summary.get('usable_manual_label_count', 0))}</strong></div>
        <div class="stat"><span>Minimum do kalibracji</span><strong>{_h(summary.get('min_verified_labels', MIN_VERIFIED_LABELS))}</strong></div>
        <div class="stat"><span>Status</span><strong>{_h(review_status)}</strong></div>
      </section>
    </div>
  </header>
  <main>
    {toolbar}
    <section class="grid">{content}</section>
  </main>
  {_review_script() if has_cards else ""}
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


def _review_toolbar() -> str:
    return """<section class="toolbar" aria-label="Akcje dla etykiet">
  <div class="toolbar-text">
    <strong>Eksport etykiet</strong>
    <span>Formularz zapisuje podgląd w przeglądarce i generuje plik JSONL do dalszego uczenia/evaluacji.</span>
  </div>
  <div class="toolbar-actions">
    <button type="button" class="copy-all">Kopiuj wszystkie JSONL</button>
    <button type="button" class="primary download-labels">Pobierz labels.jsonl</button>
  </div>
</section>"""


def _empty_review_state(
    summary: Mapping[str, Any],
    learning: Mapping[str, Any],
    payload: Mapping[str, Any],
    review_status: str,
) -> str:
    learning_summary = learning.get("summary") or {}
    source_count = learning_summary.get("source_record_count", summary.get("record_count", 0))
    status = review_status or str(learning.get("status") or "UNKNOWN")
    input_blocker = _input_blocker_notice(payload)
    return f"""<section class="empty-state" aria-labelledby="empty-title">
  <h2 id="empty-title">Brak diagramów do oznaczenia</h2>
  <p>Nie ma teraz pól do wypełnienia, bo kolejka markerów ma <strong>{_h(summary.get('queue_count', 0))}</strong> pozycji, a raport źródłowy widzi <strong>{_h(source_count)}</strong> rekordów. To nie jest formularz do ręcznego wpisywania FEN od zera; najpierw system musi wygenerować crop planszy i crop markera.</p>
  {input_blocker}
  <div class="empty-grid">
    <div class="empty-block">
      <h3>Co uruchomić</h3>
      <p>Użyj aktualnego worktree z obsługą komendy <code>process</code> i prawdziwego PDF-a:</p>
      <pre>cd C:\\Users\\user\\.codex\\worktrees\\kindlemaster-main-localhost
python kindlemaster.py process "C:\\ścieżka\\do\\pliku.pdf" --out "output\\marker_review\\book" --mode auto --render-pages</pre>
    </div>
    <div class="empty-block">
      <h3>Co sprawdzić po konwersji</h3>
      <ul>
        <li><code>reports/chess_fen/side_marker_learning_queue.jsonl</code> powinien mieć wiersze do oznaczenia.</li>
        <li><code>reports/chess_fen/two_crop_quality_metrics.json</code> pokaże, czy powstały cropy planszy i markera.</li>
        <li>Jeśli PowerShell pokazuje <code>invalid choice: process</code>, uruchamiasz stary checkout, nie aktualny main/worktree.</li>
      </ul>
    </div>
    {_empty_status_explanation(status, summary)}
    <div class="empty-block">
      <h3>Czego nie wpisywać ręcznie</h3>
      <ul>
        <li>Nie zgaduj strony ruchu z pozycji na szachownicy.</li>
        <li>Nie wpisuj pełnego FEN jako substytutu markera.</li>
        <li>Nie oznaczaj AI-only jako etykiety treningowej człowieka.</li>
      </ul>
    </div>
  </div>
</section>"""


def _review_status(learning: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    input_status = _input_blocker_status(payload)
    if input_status:
        return input_status
    return str(learning.get("status") or "UNKNOWN")


def _input_blocker_status(payload: Mapping[str, Any]) -> str:
    _, failed_reasons = _first_stage_failure(payload)
    if not failed_reasons:
        return ""
    joined = " | ".join(failed_reasons)
    if "FileNotFoundError" in joined or "no such file" in joined.lower():
        return "INPUT_PDF_MISSING"
    return "INPUT_EXTRACTION_BLOCKED"


def _empty_status_explanation(status: str, summary: Mapping[str, Any]) -> str:
    if status == "INPUT_PDF_MISSING":
        return """<div class="empty-block">
      <h3>Dlaczego status to INPUT_PDF_MISSING</h3>
      <p>Nie ma nic do sprawdzenia, bo system nie dostał istniejącego PDF-a. Najpierw uruchom proces na prawdziwym pliku PDF; dopiero wtedy powstaną strony, cropy diagramów i cropy markerów do oznaczania.</p>
    </div>"""
    if status == "INPUT_EXTRACTION_BLOCKED":
        return """<div class="empty-block">
      <h3>Dlaczego status to INPUT_EXTRACTION_BLOCKED</h3>
      <p>Eksport zatrzymał się przed wygenerowaniem kolejki markerów. Najpierw trzeba usunąć błąd wejścia lub ekstrakcji widoczny powyżej, potem wrócić do oznaczania.</p>
    </div>"""
    return f"""<div class="empty-block">
      <h3>Dlaczego status to {_h(status)}</h3>
      <p>Do kalibracji potrzeba co najmniej <strong>{_h(summary.get('min_verified_labels', MIN_VERIFIED_LABELS))}</strong> ręcznie sprawdzonych etykiet. Gdy kolejka jest pusta, najpierw naprawiamy wejście albo detekcję diagramów/cropów, dopiero potem oznaczamy.</p>
    </div>"""


def _first_stage_failure(payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    stages = [stage for stage in payload.get("stage_results") or [] if isinstance(stage, Mapping)]
    for stage in stages:
        reasons = [str(reason) for reason in stage.get("failure_reasons") or [] if str(reason)]
        if reasons:
            return str(stage.get("name") or ""), reasons
    return "", []


def _input_blocker_notice(payload: Mapping[str, Any]) -> str:
    failed_stage, failed_reasons = _first_stage_failure(payload)
    if not failed_reasons:
        return ""

    source_pdf = str(payload.get("source_pdf") or "").strip()
    joined = " | ".join(failed_reasons)
    if "FileNotFoundError" in joined or "no such file" in joined.lower():
        return f"""<div class="empty-block alert">
    <h3>Problem z wejściem: PDF nie został znaleziony</h3>
    <p>Etap <code>{_h(failed_stage or 'run_chess_study_export')}</code> nie mógł otworzyć pliku <code>{_h(source_pdf or 'brak ścieżki')}</code>. W tym stanie system nie ma stron ani diagramów do pokazania.</p>
    <p>Podmień przykładową ścieżkę w komendzie na pełną ścieżkę do istniejącego PDF-a, najlepiej przeciągając plik do PowerShella po wpisaniu <code>python kindlemaster.py process </code>.</p>
  </div>"""

    return f"""<div class="empty-block alert">
    <h3>Problem z wejściem albo ekstrakcją</h3>
    <p>Etap <code>{_h(failed_stage or 'unknown')}</code> zgłosił: <code>{_h(joined)}</code>. Najpierw usuń tę blokadę, potem wróć do oznaczania markerów.</p>
  </div>"""


def _review_card(row: Mapping[str, Any], index: int) -> str:
    priority = int(row.get("priority") or 999)
    status = str(row.get("system_side_marker_status") or "")
    badge_class = "good" if status.startswith("trusted") else ("bad" if "conflict" in status else "warn")
    template = _label_template_row(row)
    template_json = json.dumps(template, ensure_ascii=False, indent=2)
    safe_id = _dom_id(row.get("diagram_id"), index)
    marker_name = f"marker-{safe_id}"
    side_name = f"side-{safe_id}"
    storage_key = f"kindlemaster.side_marker_label.{row.get('diagram_id') or index}"
    return f"""<article class="review-card" data-index="{index}" data-storage-key="{_attr(storage_key)}" data-template="{_json_attr(template)}">
  <div class="head">
    <div><h2>{_h(row.get('diagram_id'))}</h2><div class="page">Page {_h(row.get('page'))}</div></div>
    <div class="badges">
      <span class="badge">P{priority}</span>
      <span class="badge {badge_class}">{_h(status)}</span>
      <span class="badge warn">{_h(row.get('primary_side_marker_blocker') or 'no_blocker')}</span>
    </div>
  </div>
  <div class="media-grid">
    {_figure('Crop planszy', row.get('board_crop_path'), row.get('diagram_id'), 'board')}
    {_figure('Crop markera', row.get('side_marker_crop_path'), row.get('diagram_id'), 'marker')}
    {_figure('Debug overlay', row.get('debug_overlay_path'), row.get('diagram_id'), 'overlay')}
  </div>
  <div class="body">
    <dl>
      <dt>System sugeruje</dt><dd>{_h(row.get('system_side_to_move') or 'unknown')}</dd>
      <dt>Symbol systemu</dt><dd>{_h(row.get('system_side_marker_symbol') or 'brak')}</dd>
      <dt>Status placement</dt><dd>{_h(row.get('placement_status'))}</dd>
      <dt>Status pełnego FEN</dt><dd>{_h(row.get('full_fen_status'))}</dd>
      <dt>Policy</dt><dd>{_h(REVIEW_ONLY_POLICY)}</dd>
    </dl>
    <form class="label-form">
      <fieldset>
        <legend>Co widać w cropie markera?</legend>
        <p class="hint">Wybierz tylko to, co naprawdę widać. Przy szumie albo kilku znakach zostaw rekord do przeglądu.</p>
        <div class="choice-grid">
          {_radio(marker_name, 'outline_triangle', '△ pusty trójkąt', 'białe mają ruch (w)')}
          {_radio(marker_name, 'filled_triangle', '▼ pełny trójkąt', 'czarne mają ruch (b)')}
          {_radio(marker_name, 'none', 'Brak markera', 'w cropie nie ma wiarygodnego znaku')}
          {_radio(marker_name, 'unclear', 'Nieczytelny / szum', 'nie da się bezpiecznie rozpoznać')}
          {_radio(marker_name, 'multiple', 'Kilka markerów', 'rekord konfliktowy, nie promować')}
          {_radio(marker_name, 'bad_crop', 'Zły crop', 'marker jest ucięty albo wycinek jest błędny')}
        </div>
      </fieldset>
      <fieldset>
        <legend>Kto ma ruch?</legend>
        <p class="hint">Pole ustawia się automatycznie po wyborze △ albo ▼. Zmień tylko wtedy, gdy formularz nie odzwierciedla markera.</p>
        <div class="choice-grid">
          {_radio(side_name, 'w', 'Białe', 'manual_side_to_move = w')}
          {_radio(side_name, 'b', 'Czarne', 'manual_side_to_move = b')}
          {_radio(side_name, 'unknown', 'Nie wiadomo', 'brak bezpiecznej etykiety')}
        </div>
      </fieldset>
      <div class="field-grid">
        <div class="field">
          <label for="location-{safe_id}">Położenie markera (opcjonalnie)</label>
          <input id="location-{safe_id}" class="marker-location" type="text" placeholder="np. pod planszą, prawa strona">
        </div>
        <div class="field">
          <label for="bbox-{safe_id}">BBox markera (opcjonalnie)</label>
          <input id="bbox-{safe_id}" class="marker-bbox" type="text" placeholder="np. x,y,w,h">
        </div>
      </div>
      <div class="field">
        <label for="notes-{safe_id}">Notatka</label>
        <textarea id="notes-{safe_id}" class="notes" placeholder="np. marker częściowo ucięty, ale pusty trójkąt jest czytelny"></textarea>
      </div>
      <div class="field-grid">
        <label class="checkline">
          <input class="human-verified" type="checkbox">
          <span>Sprawdzone wzrokowo przez człowieka. Bez tego etykieta nie będzie użyteczna do kalibracji.</span>
        </label>
        <div class="field">
          <label for="verified-by-{safe_id}">Kto sprawdził</label>
          <input id="verified-by-{safe_id}" class="verified-by" type="text" placeholder="np. PM">
          <input class="verified-at" type="hidden" value="">
        </div>
      </div>
    </form>
    <div class="template">
      <div class="template-row">
        <label for="label-{safe_id}">Gotowy wiersz JSONL</label>
        <button type="button" class="copy-row">Kopiuj ten wiersz</button>
      </div>
      <textarea id="label-{safe_id}" class="json-output" spellcheck="false" readonly>{_h(template_json)}</textarea>
    </div>
  </div>
</article>"""


def _radio(name: str, value: str, label: str, detail: str) -> str:
    return f"""<label class="choice">
  <input type="radio" name="{_attr(name)}" value="{_attr(value)}">
  <span><strong>{_h(label)}</strong><span>{_h(detail)}</span></span>
</label>"""


def _review_script() -> str:
    policy = json.dumps(REVIEW_ONLY_POLICY, ensure_ascii=False)
    return """<script>
(function () {
  const POLICY = __POLICY__;
  const markerToSide = { outline_triangle: "w", filled_triangle: "b" };
  const markerToStatus = {
    outline_triangle: "verified",
    filled_triangle: "verified",
    none: "verified",
    unclear: "verified",
    multiple: "verified",
    bad_crop: "verified"
  };

  function radioValue(form, prefix) {
    const checked = form.querySelector('input[name^="' + prefix + '-"]:checked');
    return checked ? checked.value : "";
  }

  function setRadio(form, prefix, value) {
    const inputs = form.querySelectorAll('input[name^="' + prefix + '-"]');
    inputs.forEach((input) => {
      input.checked = input.value === value;
    });
  }

  function stateFromForm(card) {
    const form = card.querySelector(".label-form");
    return {
      marker: radioValue(form, "marker"),
      side: radioValue(form, "side"),
      location: form.querySelector(".marker-location").value.trim(),
      bbox: form.querySelector(".marker-bbox").value.trim(),
      notes: form.querySelector(".notes").value.trim(),
      verified: form.querySelector(".human-verified").checked,
      verifiedBy: form.querySelector(".verified-by").value.trim(),
      verifiedAt: form.querySelector(".verified-at").value
    };
  }

  function applyState(card, state) {
    const form = card.querySelector(".label-form");
    if (!form || !state) return;
    setRadio(form, "marker", state.marker || "");
    setRadio(form, "side", state.side || "");
    form.querySelector(".marker-location").value = state.location || "";
    form.querySelector(".marker-bbox").value = state.bbox || "";
    form.querySelector(".notes").value = state.notes || "";
    form.querySelector(".human-verified").checked = Boolean(state.verified);
    form.querySelector(".verified-by").value = state.verifiedBy || "";
    form.querySelector(".verified-at").value = state.verifiedAt || "";
  }

  function buildRow(card) {
    const template = JSON.parse(card.dataset.template || "{}");
    const state = stateFromForm(card);
    const row = Object.assign({}, template);
    const inferredSide = markerToSide[state.marker] || "";

    row.manual_visible_marker = state.marker || "";
    row.manual_marker_shape = state.marker || "";
    row.manual_side_to_move = state.side && state.side !== "unknown" ? state.side : inferredSide;
    row.manual_marker_location = state.location;
    row.manual_marker_bbox = state.bbox;
    row.manual_notes = state.notes;
    row.human_verified = Boolean(state.verified);
    row.verification_source = state.verified ? "human_visual" : "";
    row.verified_by = state.verified ? state.verifiedBy : "";
    row.verified_at = state.verified ? state.verifiedAt : "";
    row.label_status = state.verified && markerToStatus[state.marker] ? "verified" : "needs_manual_marker";
    row.accepted_for_runtime = false;
    row.accepted_for_corpus = false;
    row.policy = POLICY;
    return row;
  }

  function writePreview(card, persist) {
    const form = card.querySelector(".label-form");
    const verified = form.querySelector(".human-verified");
    const verifiedAt = form.querySelector(".verified-at");
    const marker = radioValue(form, "marker");

    if (markerToSide[marker]) {
      setRadio(form, "side", markerToSide[marker]);
    }
    if (verified.checked && !verifiedAt.value) {
      verifiedAt.value = new Date().toISOString();
    }
    if (!verified.checked) {
      verifiedAt.value = "";
    }

    const row = buildRow(card);
    card.querySelector(".json-output").value = JSON.stringify(row);
    if (persist) {
      try {
        window.localStorage.setItem(card.dataset.storageKey, JSON.stringify(stateFromForm(card)));
      } catch (error) {
        // Local storage is best-effort only; JSONL preview remains the source of export.
      }
    }
  }

  function allJsonl() {
    return Array.from(document.querySelectorAll(".review-card"))
      .map((card) => {
        writePreview(card, false);
        return card.querySelector(".json-output").value;
      })
      .filter(Boolean)
      .join("\\n") + "\\n";
  }

  function copyText(text, button) {
    const done = () => {
      const original = button.textContent;
      button.textContent = "Skopiowano";
      window.setTimeout(() => { button.textContent = original; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
    } else {
      fallbackCopy(text, done);
    }
  }

  function fallbackCopy(text, done) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
    done();
  }

  document.querySelectorAll(".review-card").forEach((card) => {
    try {
      const saved = window.localStorage.getItem(card.dataset.storageKey);
      if (saved) applyState(card, JSON.parse(saved));
    } catch (error) {
      // Ignore corrupted local drafts and keep the generated template.
    }
    card.querySelectorAll("input, textarea.notes").forEach((control) => {
      control.addEventListener("input", () => writePreview(card, true));
      control.addEventListener("change", () => writePreview(card, true));
    });
    card.querySelector(".copy-row").addEventListener("click", (event) => {
      writePreview(card, true);
      copyText(card.querySelector(".json-output").value + "\\n", event.currentTarget);
    });
    writePreview(card, false);
  });

  const copyAll = document.querySelector(".copy-all");
  if (copyAll) {
    copyAll.addEventListener("click", (event) => copyText(allJsonl(), event.currentTarget));
  }

  const download = document.querySelector(".download-labels");
  if (download) {
    download.addEventListener("click", () => {
      const blob = new Blob([allJsonl()], { type: "application/jsonl;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "side_marker_manual_labels.jsonl";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    });
  }
})();
</script>""".replace("__POLICY__", policy)


def _json_attr(value: Mapping[str, Any]) -> str:
    return _attr(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _dom_id(value: Any, fallback: int) -> str:
    raw = str(value or f"row-{fallback}").strip().lower()
    safe = "".join(char if char.isalnum() else "-" for char in raw)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or f"row-{fallback}"


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
