from __future__ import annotations

import html
import json
import time
from pathlib import Path
from typing import Any, Iterable, Mapping


ENGINE_HINTS_REPORT_SCHEMA = "kindlemaster.chess_engine.study_hints.v1"
ENGINE_HINTS_DATA_SCHEMA = "kindlemaster.chess_engine.study_hints_data.v1"
ENGINE_HINT_RECORD_SCHEMA = "kindlemaster.chess_engine.study_hint.v1"
ENGINE_HINT_SOURCE = "engine_rule_based_v1"


def build_engine_study_hints(
    engine_analysis: Mapping[str, Any] | None,
    board_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build non-spoiling Study Mode hints from existing engine analysis rows."""

    rows = engine_analysis.get("items") if isinstance(engine_analysis, Mapping) else []
    context_by_id = _context_by_diagram_id(board_context or {})
    items = [_hint_record(dict(row), context_by_id.get(str(row.get("diagram_id") or ""))) for row in rows or [] if isinstance(row, Mapping)]
    summary = _summary(items)
    return {
        "schema": ENGINE_HINTS_REPORT_SCHEMA,
        "generated_at": _iso_utc(time.time()),
        "source": ENGINE_HINT_SOURCE,
        "summary": summary,
        "items": items,
    }


def build_engine_study_hint_artifacts(
    out_dir: str | Path,
    engine_analysis: Mapping[str, Any] | None,
    board_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out = Path(out_dir)
    report_dir = out / "reports" / "chess_engine"
    data_dir = out / "data"
    report_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    report = build_engine_study_hints(engine_analysis, board_context)
    data_payload = {
        "schema": ENGINE_HINTS_DATA_SCHEMA,
        "generated_at": report["generated_at"],
        "source": report["source"],
        "summary": report["summary"],
        "items": report["items"],
    }
    json_path = report_dir / "engine_hints.json"
    md_path = report_dir / "engine_hints.md"
    html_path = report_dir / "engine_hints.html"
    data_path = data_dir / "engine_hints.json"
    _write_json(json_path, report)
    md_path.write_text(_engine_hints_markdown(report), encoding="utf-8")
    html_path.write_text(_engine_hints_html(report), encoding="utf-8")
    _write_json(data_path, data_payload)
    return {
        "report": report,
        "paths": {
            "engine_hints": json_path,
            "engine_hints_md": md_path,
            "engine_hints_html": html_path,
            "engine_hints_data": data_path,
        },
    }


def _hint_record(row: Mapping[str, Any], context: Mapping[str, Any] | None) -> dict[str, Any]:
    diagram_id = str(row.get("diagram_id") or "")
    base = {
        "schema": ENGINE_HINT_RECORD_SCHEMA,
        "diagram_id": diagram_id,
        "page": row.get("page") or (context or {}).get("page") or 0,
        "hint_status": "unavailable",
        "hint_level_1": "",
        "hint_level_2": "",
        "full_reveal_available": False,
        "source": ENGINE_HINT_SOURCE,
        "unavailable_reason": "",
        "move_features": {},
        "best_move_san": "",
        "best_move_uci": "",
        "score_cp": row.get("score_cp"),
        "mate": row.get("mate"),
        "pv": row.get("pv") or [],
        "engine_status": row.get("engine_status") or "",
        "skip_reason": row.get("skip_reason") or "",
        "fen_status": row.get("fen_status") or "",
        "side_marker_status": row.get("side_marker_status") or "",
    }
    if str(row.get("engine_status") or "") != "ok":
        reason = str(row.get("skip_reason") or row.get("engine_status") or "engine_analysis_unavailable")
        return {**base, "unavailable_reason": reason}
    best_move_uci = str(row.get("best_move_uci") or "").strip()
    best_move_san = str(row.get("best_move_san") or "").strip()
    if not best_move_uci and not best_move_san:
        return {**base, "unavailable_reason": "best_move_missing"}

    features = _move_features(row, context)
    if features.get("invalid_move"):
        return {**base, "unavailable_reason": "invalid_engine_move", "move_features": features}
    hint_1, hint_2 = _hint_texts(row, features)
    return {
        **base,
        "hint_status": "available",
        "hint_level_1": hint_1,
        "hint_level_2": hint_2,
        "full_reveal_available": True,
        "unavailable_reason": "",
        "move_features": features,
        "best_move_san": best_move_san,
        "best_move_uci": best_move_uci,
    }


def _move_features(row: Mapping[str, Any], context: Mapping[str, Any] | None) -> dict[str, Any]:
    fen = str(row.get("fen") or (context or {}).get("fen") or "").strip()
    uci = str(row.get("best_move_uci") or "").strip()
    features: dict[str, Any] = {
        "is_check": False,
        "is_capture": False,
        "is_promotion": False,
        "is_decisive_score": _is_decisive_score(row),
        "is_mate": row.get("mate") is not None,
    }
    if not fen or not uci:
        return features
    try:
        import chess  # type: ignore
    except Exception:
        return {**features, "feature_probe_unavailable": True}
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return {**features, "invalid_move": True}
        features.update(
            {
                "is_check": bool(board.gives_check(move)),
                "is_capture": bool(board.is_capture(move)),
                "is_promotion": bool(move.promotion),
            }
        )
    except Exception:
        return {**features, "invalid_move": True}
    return features


def _hint_texts(row: Mapping[str, Any], features: Mapping[str, Any]) -> tuple[str, str]:
    if features.get("is_mate"):
        level_1 = "There may be a forced mate."
    elif features.get("is_decisive_score"):
        level_1 = "There may be a decisive tactical idea."
    elif features.get("is_check") or features.get("is_capture"):
        level_1 = "Look for a forcing move."
    else:
        level_1 = "Look for the most forcing candidate before checking the engine line."

    if features.get("is_check"):
        level_2 = "Look for a forcing check."
    elif features.get("is_capture"):
        level_2 = "Consider a forcing capture."
    elif features.get("is_promotion"):
        level_2 = "Consider whether promotion changes the position immediately."
    elif row.get("mate") is not None or features.get("is_decisive_score"):
        level_2 = "Focus on forcing moves that keep the initiative."
    else:
        level_2 = "Compare candidate moves by threats, safety, and activity."
    return level_1, level_2


def _is_decisive_score(row: Mapping[str, Any]) -> bool:
    if row.get("mate") is not None:
        return True
    try:
        return abs(int(row.get("score_cp"))) >= 300
    except (TypeError, ValueError):
        return False


def _summary(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(item) for item in items]
    available = [item for item in rows if item.get("hint_status") == "available"]
    unavailable = [item for item in rows if item.get("hint_status") != "available"]
    return {
        "hint_count": len(rows),
        "available_count": len(available),
        "unavailable_count": len(unavailable),
        "full_reveal_available_count": len([item for item in available if item.get("full_reveal_available")]),
        "by_status": _count_by(rows, "hint_status"),
        "by_unavailable_reason": _count_by(unavailable, "unavailable_reason"),
        "source": ENGINE_HINT_SOURCE,
    }


def _context_by_diagram_id(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("items") or payload.get("diagrams") or []
    return {
        str(row.get("diagram_id") or row.get("id") or ""): row
        for row in rows
        if isinstance(row, Mapping) and str(row.get("diagram_id") or row.get("id") or "")
    }


def _engine_hints_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Chess Engine Study Hints",
        "",
        f"- source: `{payload.get('source', ENGINE_HINT_SOURCE)}`",
        f"- hints: `{summary.get('hint_count', 0)}`",
        f"- available: `{summary.get('available_count', 0)}`",
        f"- unavailable: `{summary.get('unavailable_count', 0)}`",
        "",
        "| Diagram | Page | Status | Hint 1 | Hint 2 | Reason |",
        "| --- | ---: | --- | --- | --- | --- |",
    ]
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            "| {diagram} | {page} | {status} | {hint1} | {hint2} | {reason} |".format(
                diagram=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                status=_md(str(item.get("hint_status") or "")),
                hint1=_md(str(item.get("hint_level_1") or "")),
                hint2=_md(str(item.get("hint_level_2") or "")),
                reason=_md(str(item.get("unavailable_reason") or "")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _engine_hints_html(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('diagram_id') or ''))}</td>"
        f"<td>{html.escape(str(item.get('page') or ''))}</td>"
        f"<td>{html.escape(str(item.get('hint_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('hint_level_1') or ''))}</td>"
        f"<td>{html.escape(str(item.get('hint_level_2') or ''))}</td>"
        f"<td>{html.escape(str(item.get('unavailable_reason') or ''))}</td>"
        "</tr>"
        for item in payload.get("items") or []
        if isinstance(item, Mapping)
    ) or "<tr><td colspan='6'>No engine hints found.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Chess Engine Study Hints</title>
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
  <h1>Chess Engine Study Hints</h1>
  <p>Rule-based hints are derived from engine analysis and keep the best move behind an explicit reveal.</p>
  <section class="stats">
    <div class="stat">Hints: <strong>{html.escape(str(summary.get('hint_count', 0)))}</strong></div>
    <div class="stat">Available: <strong>{html.escape(str(summary.get('available_count', 0)))}</strong></div>
    <div class="stat">Unavailable: <strong>{html.escape(str(summary.get('unavailable_count', 0)))}</strong></div>
  </section>
  <table>
    <thead><tr><th>Diagram</th><th>Page</th><th>Status</th><th>Hint 1</th><th>Hint 2</th><th>Reason</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""


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


def _md(value: str) -> str:
    return value.replace("|", "\\|")
