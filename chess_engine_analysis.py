from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from chess_engine_service import analyze_fen as _analyze_fen

ENGINE_ANALYSIS_REPORT_SCHEMA = "kindlemaster.chess_engine.analysis_artifacts.v1"
ENGINE_ANALYSIS_DATA_SCHEMA = "kindlemaster.chess_engine.analysis_data.v1"
ENGINE_RECORD_SCHEMA = "kindlemaster.chess_engine.diagram_analysis.v1"

ACCEPTED_FEN_STATUSES = {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"}
TRUSTED_SIDE_MARKER_STATUSES = {
    "trusted_marker",
    "trusted_caption",
    "trusted_exact_label",
    "trusted_verified_label",
    "trusted_safe_search_region_marker",
}


def build_engine_analysis_artifacts(
    out_dir: str | Path,
    diagrams: Iterable[Mapping[str, Any]],
    fen_payload: Mapping[str, Any] | None = None,
    *,
    analyze_fen_fn: Callable[..., Mapping[str, Any]] | None = None,
    limit_ms: int = 100,
    depth: int | None = None,
    multipv: int = 1,
    cache_enabled: bool = True,
) -> dict[str, Any]:
    """Write engine-analysis reports for accepted, trusted chess diagrams."""

    out = Path(out_dir)
    report_dir = out / "reports" / "chess_engine"
    data_dir = out / "data"
    report_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    analyzer = analyze_fen_fn or _analyze_fen
    fen_by_id = _fen_items_by_diagram_id(fen_payload or {})
    records: list[dict[str, Any]] = []
    started = time.time()
    for index, diagram in enumerate(diagrams, start=1):
        record = _engine_record_for_diagram(
            diagram,
            index,
            fen_by_id=fen_by_id,
            analyze_fen_fn=analyzer,
            limit_ms=limit_ms,
            depth=depth,
            multipv=multipv,
            cache_enabled=cache_enabled,
            cache_path=out / "cache" / "chess_engine_analysis_cache.jsonl",
        )
        records.append(record)

    summary = _summary(records)
    payload = {
        "schema": ENGINE_ANALYSIS_REPORT_SCHEMA,
        "generated_at": _iso_utc(started),
        "summary": summary,
        "items": records,
    }
    data_payload = {
        "schema": ENGINE_ANALYSIS_DATA_SCHEMA,
        "generated_at": payload["generated_at"],
        "summary": summary,
        "items": records,
    }
    json_path = report_dir / "engine_analysis.json"
    jsonl_path = report_dir / "engine_analysis.jsonl"
    md_path = report_dir / "engine_analysis.md"
    html_path = report_dir / "engine_analysis.html"
    data_path = data_dir / "engine_analysis.json"
    _write_json(json_path, payload)
    _write_jsonl(jsonl_path, records)
    md_path.write_text(_engine_analysis_markdown(payload), encoding="utf-8")
    html_path.write_text(_engine_analysis_html(payload), encoding="utf-8")
    _write_json(data_path, data_payload)
    return {
        "report": payload,
        "paths": {
            "engine_analysis": json_path,
            "engine_analysis_jsonl": jsonl_path,
            "engine_analysis_md": md_path,
            "engine_analysis_html": html_path,
            "engine_analysis_data": data_path,
        },
    }


def _engine_record_for_diagram(
    diagram: Mapping[str, Any],
    index: int,
    *,
    fen_by_id: Mapping[str, Mapping[str, Any]],
    analyze_fen_fn: Callable[..., Mapping[str, Any]],
    limit_ms: int,
    depth: int | None,
    multipv: int,
    cache_enabled: bool,
    cache_path: Path,
) -> dict[str, Any]:
    diagram_id = _diagram_id(diagram, index)
    fen_item = dict(fen_by_id.get(diagram_id) or {})
    fen = str(
        _first_non_empty(
            fen_item.get("selected_value"),
            fen_item.get("fen"),
            diagram.get("fen"),
            diagram.get("full_fen"),
            diagram.get("fen_candidate"),
        )
        or ""
    ).strip()
    fen_status = str(
        _first_non_empty(
            fen_item.get("full_fen_runtime_status"),
            fen_item.get("runtime_status"),
            fen_item.get("status"),
            diagram.get("full_fen_status"),
            diagram.get("runtime_status"),
            diagram.get("status"),
            diagram.get("validation_status"),
        )
        or ""
    )
    side_marker_status = str(
        _first_non_empty(fen_item.get("side_marker_status"), diagram.get("side_marker_status"), "marker_missing")
    )
    side_to_move = str(_first_non_empty(fen_item.get("side_to_move"), diagram.get("side_to_move"), _fen_side(fen)) or "")
    base = {
        "schema": ENGINE_RECORD_SCHEMA,
        "diagram_id": diagram_id,
        "page": _first_non_empty(fen_item.get("page"), diagram.get("page"), diagram.get("page_number"), 0),
        "fen": fen,
        "side_to_move": side_to_move,
        "fen_status": fen_status,
        "side_marker_status": side_marker_status,
        "engine": "",
        "engine_version": "",
        "engine_status": "skipped",
        "skip_reason": "",
        "best_move_uci": "",
        "best_move_san": "",
        "score_cp": None,
        "mate": None,
        "pv": [],
        "depth": depth,
        "elapsed_ms": 0,
        "cache_hit": False,
        "warnings": list(_list_values(fen_item.get("warnings"), diagram.get("warnings"))),
    }
    if fen_status not in ACCEPTED_FEN_STATUSES:
        return {**base, "skip_reason": "fen_not_accepted"}
    if not _trusted_side_marker_status(side_marker_status):
        return {**base, "skip_reason": "side_to_move_not_trusted"}
    if not _valid_fen(fen):
        return {**base, "engine_status": "skipped", "skip_reason": "invalid_fen"}

    try:
        analysis = dict(
            analyze_fen_fn(
                fen,
                limit_ms=limit_ms,
                depth=depth,
                multipv=multipv,
                cache_enabled=cache_enabled,
                cache_path=cache_path,
            )
        )
    except Exception as exc:
        return {**base, "engine_status": "failed", "skip_reason": "engine_failed", "warnings": base["warnings"] + [str(exc)]}

    status = str(analysis.get("status") or "failed")
    skip_reason = "" if status == "ok" else status
    return {
        **base,
        "side_to_move": str(analysis.get("side_to_move") or side_to_move),
        "engine_status": status,
        "skip_reason": skip_reason,
        "engine": str(analysis.get("engine") or ""),
        "engine_version": str(analysis.get("engine_version") or ""),
        "best_move_uci": str(analysis.get("best_move_uci") or ""),
        "best_move_san": str(analysis.get("best_move_san") or ""),
        "score_cp": analysis.get("score_cp"),
        "mate": analysis.get("mate"),
        "pv": list(analysis.get("pv") or []),
        "depth": analysis.get("depth"),
        "elapsed_ms": int(analysis.get("elapsed_ms") or 0),
        "cache_hit": bool((analysis.get("cache") or {}).get("hit")),
        "warnings": base["warnings"] + list(analysis.get("warnings") or []),
    }


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    analyzed = [row for row in records if row.get("engine_status") == "ok"]
    skipped = [row for row in records if row.get("engine_status") != "ok"]
    return {
        "diagram_count": len(records),
        "eligible_count": len([row for row in records if not row.get("skip_reason") or row.get("skip_reason") in {"engine_unavailable", "timeout", "failed"}]),
        "analyzed_count": len(analyzed),
        "skipped_count": len(skipped),
        "engine_unavailable_count": len([row for row in records if row.get("engine_status") == "engine_unavailable" or row.get("skip_reason") == "engine_unavailable"]),
        "invalid_fen_count": len([row for row in records if row.get("skip_reason") == "invalid_fen" or row.get("engine_status") == "invalid_fen"]),
        "timeout_count": len([row for row in records if row.get("engine_status") == "timeout"]),
        "cache_hit_count": len([row for row in records if row.get("cache_hit")]),
        "mate_found_count": len([row for row in analyzed if row.get("mate") is not None]),
        "by_engine_status": _count_by(records, "engine_status"),
        "by_skip_reason": _count_by(records, "skip_reason"),
    }


def _engine_analysis_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Chess Engine Analysis",
        "",
        f"- diagrams: `{summary.get('diagram_count', 0)}`",
        f"- eligible: `{summary.get('eligible_count', 0)}`",
        f"- analyzed: `{summary.get('analyzed_count', 0)}`",
        f"- skipped: `{summary.get('skipped_count', 0)}`",
        f"- engine unavailable: `{summary.get('engine_unavailable_count', 0)}`",
        f"- invalid FEN: `{summary.get('invalid_fen_count', 0)}`",
        f"- timeouts: `{summary.get('timeout_count', 0)}`",
        "",
        "| Diagram | Page | FEN status | Marker | Engine | Skip reason | Best move | Score |",
        "| --- | ---: | --- | --- | --- | --- | --- | ---: |",
    ]
    for item in payload.get("items") or []:
        score = item.get("mate")
        score_text = f"mate {score}" if score is not None else str(item.get("score_cp") if item.get("score_cp") is not None else "")
        lines.append(
            "| {diagram} | {page} | {fen_status} | {marker} | {engine} | {skip} | {move} | {score} |".format(
                diagram=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                fen_status=_md(str(item.get("fen_status") or "")),
                marker=_md(str(item.get("side_marker_status") or "")),
                engine=_md(str(item.get("engine_status") or "")),
                skip=_md(str(item.get("skip_reason") or "")),
                move=_md(str(item.get("best_move_san") or item.get("best_move_uci") or "")),
                score=_md(score_text),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _engine_analysis_html(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('diagram_id') or ''))}</td>"
        f"<td>{html.escape(str(item.get('page') or ''))}</td>"
        f"<td>{html.escape(str(item.get('fen_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('side_marker_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('engine_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('skip_reason') or ''))}</td>"
        f"<td>{html.escape(str(item.get('best_move_san') or item.get('best_move_uci') or ''))}</td>"
        f"<td>{html.escape(str(item.get('score_cp') if item.get('score_cp') is not None else ''))}</td>"
        f"<td>{html.escape(str(item.get('mate') if item.get('mate') is not None else ''))}</td>"
        "</tr>"
        for item in payload.get("items") or []
    ) or "<tr><td colspan='9'>No diagrams found.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Chess Engine Analysis</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d7dde6; padding: .45rem .55rem; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    .stats {{ display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }}
    .stat {{ border:1px solid #d7dde6; padding:.65rem .8rem; border-radius:.35rem; background:#fff; }}
  </style>
</head>
<body>
  <h1>Chess Engine Analysis</h1>
  <p>Engine analysis is generated only for accepted full FEN diagrams with trusted side-to-move evidence.</p>
  <section class="stats">
    <div class="stat">Diagrams: <strong>{html.escape(str(summary.get('diagram_count', 0)))}</strong></div>
    <div class="stat">Eligible: <strong>{html.escape(str(summary.get('eligible_count', 0)))}</strong></div>
    <div class="stat">Analyzed: <strong>{html.escape(str(summary.get('analyzed_count', 0)))}</strong></div>
    <div class="stat">Skipped: <strong>{html.escape(str(summary.get('skipped_count', 0)))}</strong></div>
    <div class="stat">Engine unavailable: <strong>{html.escape(str(summary.get('engine_unavailable_count', 0)))}</strong></div>
    <div class="stat">Timeouts: <strong>{html.escape(str(summary.get('timeout_count', 0)))}</strong></div>
  </section>
  <table>
    <thead><tr><th>Diagram</th><th>Page</th><th>FEN status</th><th>Marker</th><th>Engine</th><th>Skip reason</th><th>Best move</th><th>CP</th><th>Mate</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""


def _fen_items_by_diagram_id(fen_payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item.get("id") or item.get("diagram_id") or ""): item
        for item in fen_payload.get("items") or []
        if isinstance(item, Mapping) and str(item.get("id") or item.get("diagram_id") or "")
    }


def _valid_fen(fen: str) -> bool:
    try:
        import chess  # type: ignore

        return chess.Board(fen).is_valid()
    except Exception:
        return False


def _trusted_side_marker_status(status: str) -> bool:
    value = str(status or "")
    return value in TRUSTED_SIDE_MARKER_STATUSES or value.startswith("trusted_")


def _fen_side(fen: str) -> str:
    parts = str(fen or "").split()
    return parts[1] if len(parts) > 1 and parts[1] in {"w", "b"} else ""


def _diagram_id(diagram: Mapping[str, Any], index: int) -> str:
    return str(diagram.get("diagram_id") or diagram.get("id") or f"diagram-{index}")


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _list_values(*values: Any) -> list[Any]:
    rows: list[Any] = []
    for value in values:
        if isinstance(value, list):
            rows.extend(value)
        elif value:
            rows.append(value)
    return rows


def _count_by(records: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _iso_utc(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _md(value: str) -> str:
    return value.replace("|", "\\|")
