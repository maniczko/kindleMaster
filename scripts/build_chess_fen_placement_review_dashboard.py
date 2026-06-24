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
        items.append(
            {
                "id": item_id,
                "page": item.get("page"),
                "status": item.get("status") or "",
                "runtime_status": item.get("runtime_status") or "",
                "placement_runtime_status": item.get("placement_runtime_status") or "",
                "source_image_path": source_image,
                "image_src": _safe_relative_image_src(root, source_image),
                "predicted_placement": predicted,
                "expected_placement": expected,
                "square_diff_count": len(diffs),
                "square_diffs": diffs[:24],
                "blockers": blockers,
                "next_action": item.get("next_action") or "",
            }
        )
    summary = {
        "item_count": len(items),
        "placement_machine_accepted": len([item for item in items if item["placement_runtime_status"] == "FEN_PLACEMENT_MACHINE_ACCEPTED"]),
        "full_machine_accepted": len([item for item in items if item["runtime_status"] == "FEN_MACHINE_ACCEPTED"]),
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
  <title>Chess FEN Placement Review</title>
  <style>
    :root {{
      --ink:#20170e; --muted:#76614d; --paper:#fffaf2; --panel:#f3e5d1;
      --line:#d7c0a0; --accent:#9a4b18; --good:#1f6b46; --warn:#9a5d00;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:linear-gradient(135deg,#ead8bd,#fff8ed 38%,#dfc39d); font-family:Georgia,'Times New Roman',serif; }}
    header {{ padding:2rem clamp(1rem,4vw,3rem); background:#21160d; color:#fff8ec; box-shadow:0 18px 40px rgba(42,25,9,.22); }}
    h1 {{ margin:0 0 .5rem; font-size:clamp(2rem,5vw,4rem); letter-spacing:-.04em; }}
    .lede {{ max-width:72ch; color:#ead8bd; line-height:1.55; }}
    .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:.75rem; margin-top:1.25rem; }}
    .stat {{ background:rgba(255,250,242,.1); border:1px solid rgba(255,250,242,.24); border-radius:18px; padding:.9rem 1rem; }}
    .stat strong {{ display:block; font-size:1.65rem; }}
    main {{ padding:1.25rem clamp(1rem,4vw,3rem) 3rem; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(310px,1fr)); gap:1rem; align-items:start; }}
    article {{ background:rgba(255,250,242,.92); border:1px solid var(--line); border-radius:22px; overflow:hidden; box-shadow:0 16px 38px rgba(82,50,16,.13); }}
    .media {{ position:relative; background:var(--panel); min-height:220px; display:grid; place-items:center; }}
    .media img {{ width:100%; max-height:360px; object-fit:contain; display:block; }}
    .media::after {{ content:""; position:absolute; inset:0; pointer-events:none; background:
      linear-gradient(to right, rgba(154,75,24,.45) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(154,75,24,.45) 1px, transparent 1px);
      background-size:12.5% 12.5%; }}
    .no-img {{ color:var(--muted); padding:2rem; text-align:center; }}
    .body {{ padding:1rem; }}
    .title {{ display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; }}
    h2 {{ font-size:1.1rem; margin:.1rem 0 .35rem; }}
    .badge {{ border:1px solid var(--line); border-radius:999px; padding:.18rem .55rem; font-size:.76rem; color:var(--muted); white-space:nowrap; }}
    .badge.good {{ color:var(--good); border-color:rgba(31,107,70,.35); background:rgba(31,107,70,.08); }}
    .badge.warn {{ color:var(--warn); border-color:rgba(154,93,0,.35); background:rgba(154,93,0,.08); }}
    dl {{ display:grid; grid-template-columns:auto 1fr; gap:.3rem .7rem; margin:.75rem 0; font-size:.9rem; }}
    dt {{ color:var(--muted); font-weight:700; }}
    dd {{ margin:0; overflow-wrap:anywhere; }}
    code {{ font-family:'Courier New',monospace; background:#f4e6d2; border-radius:6px; padding:.08rem .24rem; }}
    details {{ border-top:1px solid var(--line); padding:.75rem 0 0; margin-top:.75rem; }}
    summary {{ cursor:pointer; color:var(--accent); font-weight:700; }}
    .diffs,.blockers {{ display:flex; flex-wrap:wrap; gap:.35rem; margin-top:.5rem; }}
    .pill {{ font-size:.78rem; border-radius:999px; background:#f0dcc0; padding:.22rem .5rem; }}
    .empty {{ padding:2rem; background:var(--paper); border-radius:18px; }}
  </style>
</head>
<body>
  <header>
    <h1>Chess FEN Placement Review</h1>
    <p class="lede">Placement-level review separates visual board recognition from full six-field FEN acceptance. A placement can be useful evidence without becoming exportable FEN.</p>
    <section class="stats">
      <div class="stat"><span>Items</span><strong>{_h(summary.get('item_count'))}</strong></div>
      <div class="stat"><span>Placement accepted</span><strong>{_h(summary.get('placement_machine_accepted'))}</strong></div>
      <div class="stat"><span>Full FEN accepted</span><strong>{_h(summary.get('full_machine_accepted'))}</strong></div>
      <div class="stat"><span>With expected labels</span><strong>{_h(summary.get('items_with_expected'))}</strong></div>
    </section>
  </header>
  <main><section class="grid">{cards}</section></main>
</body>
</html>"""


def _render_card(item: dict[str, Any]) -> str:
    image_src = str(item.get("image_src") or "")
    image = f'<img src="{_h(image_src)}" alt="Board crop for {_h(item.get("id"))}">' if image_src else '<div class="no-img">No safe local crop preview available</div>'
    status = str(item.get("placement_runtime_status") or item.get("runtime_status") or "")
    badge_class = "good" if status == "FEN_PLACEMENT_MACHINE_ACCEPTED" else "warn"
    diffs = item.get("square_diffs") or []
    blockers = item.get("blockers") or []
    diff_html = "".join(
        f'<span class="pill">{_h(diff.get("square"))}: {_h(diff.get("expected"))} → {_h(diff.get("actual"))}</span>'
        for diff in diffs
    ) or '<span class="pill">No diffs or no expected placement</span>'
    blocker_html = "".join(
        f'<span class="pill">{_h(blocker.get("category"))}/{_h(blocker.get("code"))}</span>'
        for blocker in blockers
    ) or '<span class="pill">No blockers</span>'
    return f"""<article>
  <div class="media">{image}</div>
  <div class="body">
    <div class="title"><h2>{_h(item.get('id'))}</h2><span class="badge {badge_class}">{_h(status)}</span></div>
    <dl>
      <dt>Page</dt><dd>{_h(item.get('page'))}</dd>
      <dt>Status</dt><dd>{_h(item.get('status'))}</dd>
      <dt>Diffs</dt><dd>{_h(item.get('square_diff_count'))}</dd>
      <dt>Next</dt><dd>{_h(item.get('next_action'))}</dd>
      <dt>Predicted</dt><dd><code>{_h(item.get('predicted_placement'))}</code></dd>
      <dt>Expected</dt><dd><code>{_h(item.get('expected_placement'))}</code></dd>
    </dl>
    <details open><summary>Square diffs</summary><div class="diffs">{diff_html}</div></details>
    <details><summary>Blockers</summary><div class="blockers">{blocker_html}</div></details>
  </div>
</article>"""


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
