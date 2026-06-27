from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import fen_to_cells, placement_from_fen_or_placement  # noqa: E402


SCHEMA = "kindlemaster.chess_fen.placement_review_dashboard.v1"


def build_chess_fen_placement_review_dashboard(
    out_dir: str | Path,
    *,
    expected_labels: str | Path | None = None,
    output_html: str | Path | None = None,
    output_json: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(out_dir)
    fen_payload = _read_json(root / "fen" / "fen_candidates.json")
    quality_payload = _read_json(root / "reports" / "chess_fen" / "board_detection_quality.json")
    expected_by_id, expected_by_crop = _load_expected_labels(expected_labels)
    items = []
    for item in fen_payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "")
        source_image = str(item.get("source_image_path") or "")
        expected = expected_by_id.get(item_id) or expected_by_crop.get(_normalize_path_key(source_image)) or ""
        predicted = str(item.get("selected_placement") or "")
        if not predicted:
            for candidate in item.get("candidate_values") or []:
                predicted = str(candidate.get("normalized_placement") or "")
                if predicted:
                    break
        diffs = _placement_diffs(expected, predicted)
        blockers = _blockers_with_categories(item)
        board_crop = str(item.get("board_crop_path") or source_image)
        marker_crop = str(item.get("side_marker_crop_path") or "")
        debug_overlay = str(item.get("debug_overlay_path") or "")
        placement_status = item.get("placement_runtime_status") or item.get("placement_status") or ""
        runtime_status = str(item.get("runtime_status") or "")
        full_fen_status = item.get("full_fen_runtime_status") or item.get("full_fen_status") or runtime_status or ""
        if full_fen_status == "FEN_PLACEMENT_MACHINE_ACCEPTED":
            full_fen_status = "FEN_REVIEW_REQUIRED"
        items.append(
            {
                "id": item_id,
                "page": item.get("page"),
                "status": item.get("status") or "",
                "runtime_status": item.get("runtime_status") or "",
                "placement_runtime_status": placement_status,
                "full_fen_runtime_status": full_fen_status,
                "source_image_path": source_image,
                "board_crop_path": board_crop,
                "side_marker_crop_path": marker_crop,
                "debug_overlay_path": debug_overlay,
                "image_src": _safe_relative_image_src(root, board_crop),
                "marker_image_src": _safe_relative_image_src(root, marker_crop),
                "debug_overlay_src": _safe_relative_image_src(root, debug_overlay),
                "predicted_placement": predicted,
                "expected_placement": expected,
                "square_diff_count": len(diffs),
                "square_diffs": diffs[:24],
                "blockers": blockers,
                "next_action": item.get("next_action") or "",
                "side_to_move": item.get("side_to_move") or "unknown",
                "side_marker_symbol": item.get("side_marker_symbol") or "?",
                "side_marker_status": item.get("side_marker_status") or "marker_missing",
                "side_marker_confidence": item.get("side_marker_confidence") or "",
                "fen_suppressed_reason": item.get("fen_suppressed_reason") or "",
            }
        )
    summary = {
        "item_count": len(items),
        "placement_machine_accepted": len([item for item in items if item["placement_runtime_status"] == "FEN_PLACEMENT_MACHINE_ACCEPTED"]),
        "full_machine_accepted": len([item for item in items if item["runtime_status"] == "FEN_MACHINE_ACCEPTED"]),
        "full_fen_accepted": len([item for item in items if item["full_fen_runtime_status"] in {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"}]),
        "marker_missing": len([item for item in items if item["side_marker_status"] in {"marker_missing", "inferred_only"}]),
        "marker_conflict": len(
            [
                item
                for item in items
                if item["side_marker_status"]
                in {"marker_conflict", "ambiguous_marker", "side_to_move_marker_local_conflict", "side_to_move_marker_local_ambiguous"}
            ]
        ),
        "placement_review_required": len([item for item in items if item["placement_runtime_status"] != "FEN_PLACEMENT_MACHINE_ACCEPTED"]),
        "items_with_expected": len([item for item in items if item["expected_placement"]]),
        "items_with_square_diffs": len([item for item in items if item["square_diff_count"] > 0]),
        "board_detection_quality_summary": quality_payload.get("summary") or {},
    }
    payload = {"schema": SCHEMA, "out_dir": str(root), "summary": summary, "items": items}
    html_path = Path(output_html) if output_html else root / "report" / "fen_placement_review.html"
    json_path = Path(output_json) if output_json else root / "report" / "fen_placement_review.json"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(_render_dashboard_html(payload), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "html": str(html_path), "json": str(json_path), "summary": summary}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_expected_labels(labels_path: str | Path | None) -> tuple[dict[str, str], dict[str, str]]:
    by_id: dict[str, str] = {}
    by_crop: dict[str, str] = {}
    if not labels_path:
        return by_id, by_crop
    path = Path(labels_path)
    if not path.exists():
        return by_id, by_crop
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        placement = placement_from_fen_or_placement(str(row.get("fen") or row.get("expected_fen") or ""))
        if not placement:
            continue
        for key in ("id", "diagram_id", "case_id"):
            row_id = str(row.get(key) or "")
            if row_id:
                by_id[row_id] = placement
        crop = str(row.get("crop_path") or row.get("source_image_path") or "")
        if crop:
            by_crop[_normalize_path_key(crop)] = placement
    return by_id, by_crop


def _normalize_path_key(value: str) -> str:
    return str(value or "").replace("\\", "/").lower()


def _safe_relative_image_src(root: Path, source_image: str) -> str:
    if not source_image:
        return ""
    source = Path(source_image)
    if not source.is_absolute():
        return "../" + source_image.replace("\\", "/")
    try:
        relative = source.relative_to(root)
        return "../" + str(relative).replace("\\", "/")
    except ValueError:
        return ""


def _placement_diffs(expected: str, actual: str) -> list[dict[str, str]]:
    if not expected or not actual:
        return []
    try:
        expected_cells = fen_to_cells(expected)
        actual_cells = fen_to_cells(actual)
    except ValueError:
        return [{"square": "", "expected": expected, "actual": actual}]
    diffs = []
    for index, (expected_piece, actual_piece) in enumerate(zip(expected_cells, actual_cells)):
        if expected_piece == actual_piece:
            continue
        diffs.append(
            {
                "square": f"{'abcdefgh'[index % 8]}{'87654321'[index // 8]}",
                "expected": expected_piece or "empty",
                "actual": actual_piece or "empty",
            }
        )
    return diffs


def _blockers_with_categories(item: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for blocker in item.get("acceptance_blockers") or item.get("validation_errors") or []:
        if not isinstance(blocker, dict):
            continue
        rows.append(
            {
                "code": str(blocker.get("code") or "unknown"),
                "category": str(blocker.get("category") or "unknown"),
                "message": str(blocker.get("message") or ""),
            }
        )
    return rows


def _render_dashboard_html(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    cards = "\n".join(_render_card(item) for item in payload.get("items") or []) or "<p class='empty'>No FEN placement items found.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chess FEN Two-Crop Review</title>
  <style>
    :root {{
      --bg:#f7f8fb; --surface:#ffffff; --surface-2:#eef2f7; --ink:#172033;
      --muted:#5b667a; --line:#d8dee9; --primary:#1d4ed8; --good:#167345;
      --warn:#9a5d00; --bad:#b42318; --shadow:0 10px 28px rgba(21,31,53,.08);
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--bg); font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ position:sticky; top:0; z-index:20; background:rgba(247,248,251,.96); border-bottom:1px solid var(--line); backdrop-filter:blur(10px); }}
    .bar {{ max-width:1440px; margin:0 auto; padding:1rem clamp(1rem,3vw,2rem); display:grid; gap:.9rem; }}
    .heading {{ display:flex; justify-content:space-between; align-items:flex-start; gap:1rem; }}
    h1 {{ margin:0; font-size:1.45rem; line-height:1.2; font-weight:760; }}
    .meta {{ margin:.25rem 0 0; color:var(--muted); font-size:.9rem; }}
    .stats {{ display:grid; grid-template-columns:repeat(6,minmax(120px,1fr)); gap:.55rem; }}
    .stat {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; padding:.65rem .75rem; min-width:0; }}
    .stat span {{ display:block; color:var(--muted); font-size:.76rem; line-height:1.2; }}
    .stat strong {{ display:block; margin-top:.2rem; font-size:1.2rem; line-height:1.1; }}
    .filters {{ display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; }}
    .filter {{ min-height:44px; display:inline-flex; align-items:center; gap:.45rem; padding:.45rem .7rem; background:var(--surface); border:1px solid var(--line); border-radius:8px; color:var(--ink); font-size:.9rem; cursor:pointer; }}
    .filter input {{ width:1rem; height:1rem; accent-color:var(--primary); }}
    .filter:focus-within {{ outline:3px solid rgba(29,78,216,.24); outline-offset:2px; border-color:var(--primary); }}
    main {{ max-width:1440px; margin:0 auto; padding:1rem clamp(1rem,3vw,2rem) 2.5rem; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1rem; align-items:start; }}
    article {{ background:var(--surface); border:1px solid var(--line); border-radius:8px; overflow:hidden; box-shadow:var(--shadow); }}
    .body {{ padding:1rem; }}
    .title {{ display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; margin-bottom:.8rem; }}
    h2 {{ font-size:1rem; line-height:1.25; margin:0; overflow-wrap:anywhere; }}
    .page {{ color:var(--muted); font-size:.84rem; margin-top:.18rem; }}
    .badge-row {{ display:flex; flex-wrap:wrap; gap:.35rem; justify-content:flex-end; }}
    .badge {{ border:1px solid var(--line); border-radius:999px; padding:.18rem .5rem; font-size:.76rem; color:var(--muted); white-space:nowrap; background:#f8fafc; }}
    .badge.good {{ color:var(--good); border-color:rgba(22,115,69,.32); background:rgba(22,115,69,.08); }}
    .badge.warn {{ color:var(--warn); border-color:rgba(154,93,0,.32); background:rgba(154,93,0,.08); }}
    .badge.bad {{ color:var(--bad); border-color:rgba(180,35,24,.32); background:rgba(180,35,24,.08); }}
    .artifacts {{ display:grid; grid-template-columns:1.2fr .72fr 1.2fr; gap:.65rem; padding:0 1rem 1rem; }}
    figure {{ margin:0; min-width:0; border:1px solid var(--line); border-radius:8px; background:var(--surface-2); overflow:hidden; }}
    figcaption {{ padding:.45rem .55rem; color:var(--muted); font-size:.76rem; border-bottom:1px solid var(--line); background:rgba(255,255,255,.68); }}
    .media {{ min-height:180px; display:grid; place-items:center; padding:.45rem; }}
    .media.marker {{ min-height:120px; }}
    .media img {{ width:100%; max-height:300px; object-fit:contain; display:block; }}
    .no-img {{ color:var(--muted); font-size:.85rem; padding:1rem; text-align:center; }}
    dl {{ display:grid; grid-template-columns:minmax(8rem,auto) 1fr; gap:.35rem .7rem; margin:.25rem 0 .85rem; font-size:.88rem; }}
    dt {{ color:var(--muted); font-weight:680; }}
    dd {{ margin:0; overflow-wrap:anywhere; }}
    code {{ font-family:"Cascadia Mono","Courier New",monospace; background:#eef2ff; border:1px solid #dbe4ff; border-radius:6px; padding:.08rem .24rem; }}
    details {{ border-top:1px solid var(--line); padding:.75rem 0 0; margin-top:.75rem; }}
    summary {{ min-height:36px; cursor:pointer; color:var(--primary); font-weight:700; }}
    summary:focus-visible {{ outline:3px solid rgba(29,78,216,.24); outline-offset:2px; border-radius:6px; }}
    .diffs,.blockers {{ display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.45rem; }}
    .pill {{ font-size:.78rem; border-radius:999px; background:#eef2f7; border:1px solid var(--line); padding:.22rem .5rem; }}
    .empty {{ padding:2rem; background:var(--surface); border:1px solid var(--line); border-radius:8px; }}
    .hidden {{ display:none !important; }}
    @media (max-width: 980px) {{
      header {{ position:static; }}
      .stats {{ grid-template-columns:repeat(3,minmax(0,1fr)); }}
      .grid {{ grid-template-columns:1fr; }}
    }}
    @media (max-width: 640px) {{
      .heading {{ display:block; }}
      .stats {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
      .artifacts {{ grid-template-columns:1fr; }}
      .title {{ display:block; }}
      .badge-row {{ justify-content:flex-start; margin-top:.55rem; }}
      dl {{ grid-template-columns:1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="bar">
      <div class="heading">
        <div><h1>Chess FEN Two-Crop Review</h1><p class="meta">Board, marker, overlay, placement, and full-FEN status</p></div>
        <div class="badge">{_h(payload.get('schema'))}</div>
      </div>
      <section class="stats" aria-label="Dashboard counts">
        <div class="stat"><span>Items</span><strong>{_h(summary.get('item_count'))}</strong></div>
        <div class="stat"><span>Placement accepted</span><strong>{_h(summary.get('placement_machine_accepted'))}</strong></div>
        <div class="stat"><span>Full FEN accepted</span><strong>{_h(summary.get('full_fen_accepted'))}</strong></div>
        <div class="stat"><span>Marker missing</span><strong>{_h(summary.get('marker_missing'))}</strong></div>
        <div class="stat"><span>Marker conflict</span><strong>{_h(summary.get('marker_conflict'))}</strong></div>
        <div class="stat"><span>Expected labels</span><strong>{_h(summary.get('items_with_expected'))}</strong></div>
      </section>
      <form class="filters" aria-label="Dashboard filters">
        <label class="filter"><input type="checkbox" data-filter="marker-missing">Marker missing</label>
        <label class="filter"><input type="checkbox" data-filter="marker-conflict">Marker conflict</label>
        <label class="filter"><input type="checkbox" data-filter="placement-review">Placement review</label>
        <label class="filter"><input type="checkbox" data-filter="full-fen-accepted">Full FEN accepted</label>
      </form>
    </div>
  </header>
  <main><section class="grid">{cards}</section></main>
  <script>
    (function() {{
      var inputs = Array.prototype.slice.call(document.querySelectorAll('[data-filter]'));
      var cards = Array.prototype.slice.call(document.querySelectorAll('[data-card]'));
      function activeFilters() {{ return inputs.filter(function(input) {{ return input.checked; }}).map(function(input) {{ return input.getAttribute('data-filter'); }}); }}
      function matches(card, filter) {{
        var marker = card.getAttribute('data-marker-status') || '';
        var placement = card.getAttribute('data-placement-status') || '';
        var fullFen = card.getAttribute('data-full-fen-status') || '';
        if (filter === 'marker-missing') return marker === 'marker_missing' || marker === 'inferred_only';
        if (filter === 'marker-conflict') return marker === 'marker_conflict' || marker === 'ambiguous_marker' || marker === 'side_to_move_marker_local_conflict' || marker === 'side_to_move_marker_local_ambiguous';
        if (filter === 'placement-review') return placement !== 'FEN_PLACEMENT_MACHINE_ACCEPTED';
        if (filter === 'full-fen-accepted') return fullFen === 'FEN_MACHINE_ACCEPTED' || fullFen === 'FEN_CORPUS_VERIFIED';
        return true;
      }}
      function applyFilters() {{
        var active = activeFilters();
        cards.forEach(function(card) {{
          var show = active.length === 0 || active.some(function(filter) {{ return matches(card, filter); }});
          card.classList.toggle('hidden', !show);
        }});
      }}
      inputs.forEach(function(input) {{ input.addEventListener('change', applyFilters); }});
      applyFilters();
    }})();
  </script>
</body>
</html>"""


def _render_card(item: dict[str, Any]) -> str:
    image = _render_artifact("Board crop", item.get("image_src"), item.get("id"), class_name="board")
    marker = _render_artifact("Marker crop", item.get("marker_image_src"), item.get("id"), class_name="marker")
    overlay = _render_artifact("Debug overlay", item.get("debug_overlay_src"), item.get("id"), class_name="overlay")
    status = str(item.get("placement_runtime_status") or item.get("runtime_status") or "")
    full_status = str(item.get("full_fen_runtime_status") or "")
    marker_status = str(item.get("side_marker_status") or "")
    badge_class = "good" if status == "FEN_PLACEMENT_MACHINE_ACCEPTED" else "warn"
    full_badge_class = "good" if full_status in {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"} else "warn"
    marker_badge_class = "good" if marker_status.startswith("trusted_") else ("bad" if "conflict" in marker_status else "warn")
    diffs = item.get("square_diffs") or []
    blockers = item.get("blockers") or []
    diff_html = "".join(
        f'<span class="pill">{_h(diff.get("square"))}: {_h(diff.get("expected"))} -&gt; {_h(diff.get("actual"))}</span>'
        for diff in diffs
    ) or '<span class="pill">No diffs or no expected placement</span>'
    blocker_html = "".join(
        f'<span class="pill">{_h(blocker.get("category"))}/{_h(blocker.get("code"))}</span>'
        for blocker in blockers
    ) or '<span class="pill">No blockers</span>'
    return f"""<article data-card data-marker-status="{_h(marker_status)}" data-placement-status="{_h(status)}" data-full-fen-status="{_h(full_status)}">
  <div class="body">
    <div class="title">
      <div><h2>{_h(item.get('id'))}</h2><div class="page">Page {_h(item.get('page'))}</div></div>
      <div class="badge-row">
        <span class="badge {badge_class}">{_h(status)}</span>
        <span class="badge {full_badge_class}">{_h(full_status)}</span>
        <span class="badge {marker_badge_class}">{_h(marker_status)}</span>
      </div>
    </div>
  </div>
  <div class="artifacts">{image}{marker}{overlay}</div>
  <div class="body">
    <dl>
      <dt>Status</dt><dd>{_h(item.get('status'))}</dd>
      <dt>Runtime</dt><dd>{_h(item.get('runtime_status'))}</dd>
      <dt>Placement</dt><dd>{_h(item.get('placement_runtime_status'))}</dd>
      <dt>Full FEN</dt><dd>{_h(item.get('full_fen_runtime_status'))}</dd>
      <dt>Diffs</dt><dd>{_h(item.get('square_diff_count'))}</dd>
      <dt>Side marker</dt><dd><strong>{_h(item.get('side_marker_symbol'))}</strong> {_h(item.get('side_marker_status'))} {_h(item.get('side_marker_confidence'))}</dd>
      <dt>Side</dt><dd>{_h(item.get('side_to_move'))}</dd>
      <dt>Suppressed</dt><dd>{_h(item.get('fen_suppressed_reason'))}</dd>
      <dt>Next</dt><dd>{_h(item.get('next_action'))}</dd>
      <dt>Predicted</dt><dd><code>{_h(item.get('predicted_placement'))}</code></dd>
      <dt>Expected</dt><dd><code>{_h(item.get('expected_placement'))}</code></dd>
    </dl>
    <details open><summary>Square diffs</summary><div class="diffs">{diff_html}</div></details>
    <details><summary>Blockers</summary><div class="blockers">{blocker_html}</div></details>
  </div>
</article>"""


def _render_artifact(label: str, src: Any, item_id: Any, *, class_name: str) -> str:
    image_src = str(src or "")
    if image_src:
        media = f'<img src="{_h(image_src)}" alt="{_h(label)} for {_h(item_id)}">'
    else:
        media = '<div class="no-img">Unavailable</div>'
    return f'<figure><figcaption>{_h(label)}</figcaption><div class="media {class_name}">{media}</div></figure>'


def _h(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build an HTML dashboard for chess FEN placement review.")
    parser.add_argument("out_dir")
    parser.add_argument("--expected-labels", default="")
    parser.add_argument("--output-html", default="")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args(argv)
    result = build_chess_fen_placement_review_dashboard(
        args.out_dir,
        expected_labels=args.expected_labels or None,
        output_html=args.output_html or None,
        output_json=args.output_json or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
