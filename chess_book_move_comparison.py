from __future__ import annotations

import html
import json
import re
import time
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Mapping


BOOK_MOVE_COMPARISON_SCHEMA = "kindlemaster.chess_engine.book_move_comparison.v1"
BOOK_MOVE_COMPARISON_DATA_SCHEMA = "kindlemaster.chess_engine.book_move_comparison_data.v1"
BOOK_MOVE_RECORD_SCHEMA = "kindlemaster.chess_engine.book_move_comparison_record.v1"

ACCEPTED_FEN_STATUSES = {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"}
ACCEPTED_PGN_STATUSES = {"PGN_MACHINE_ACCEPTED", "PGN_MACHINE_REPAIRED", "SOLUTION_LINE_ACCEPTED"}


def build_book_move_engine_comparison(
    out_dir: str | Path,
    *,
    diagrams: Iterable[Mapping[str, Any]] | None = None,
    fen_payload: Mapping[str, Any] | None = None,
    engine_payload: Mapping[str, Any] | None = None,
    pgn_payload: Mapping[str, Any] | None = None,
    pgn_records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write audit-only book-move vs engine-best comparison artifacts.

    This layer never changes FEN/PGN acceptance. A legal book move that differs
    from the engine best line is review evidence, not an automatic book error.
    """

    out = Path(out_dir)
    report_dir = out / "reports" / "chess_engine"
    data_dir = out / "data"
    report_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    source_book = _read_optional_json(out / "data" / "book.json")
    diagram_rows = [dict(row) for row in diagrams] if diagrams is not None else _diagrams_from_book(source_book)
    fen = dict(fen_payload or _read_optional_json(out / "fen" / "fen_candidates.json"))
    engine = dict(
        engine_payload
        or _read_optional_json(out / "data" / "engine_analysis.json")
        or _read_optional_json(report_dir / "engine_analysis.json")
    )
    pgn = dict(pgn_payload or _read_optional_json(out / "pgn" / "pgn_candidates.json"))
    source_pgn_records = (
        [dict(row) for row in pgn_records]
        if pgn_records is not None
        else [dict(row) for row in source_book.get("pgn_records") or [] if isinstance(row, Mapping)]
    )

    started = time.time()
    records = _comparison_records(
        diagram_rows=diagram_rows,
        fen_payload=fen,
        engine_payload=engine,
        pgn_payload=pgn,
        pgn_records=source_pgn_records,
    )
    summary = _summary(records)
    payload = {
        "schema": BOOK_MOVE_COMPARISON_SCHEMA,
        "generated_at": _iso_utc(started),
        "summary": summary,
        "items": records,
    }
    data_payload = {
        "schema": BOOK_MOVE_COMPARISON_DATA_SCHEMA,
        "generated_at": payload["generated_at"],
        "summary": summary,
        "items": records,
    }

    json_path = report_dir / "book_move_comparison.json"
    md_path = report_dir / "book_move_comparison.md"
    html_path = report_dir / "book_move_comparison.html"
    data_path = data_dir / "book_move_comparison.json"
    _write_json(json_path, payload)
    md_path.write_text(_comparison_markdown(payload), encoding="utf-8")
    html_path.write_text(_comparison_html(payload), encoding="utf-8")
    _write_json(data_path, data_payload)
    return {
        "report": payload,
        "paths": {
            "book_move_comparison": json_path,
            "book_move_comparison_md": md_path,
            "book_move_comparison_html": html_path,
            "book_move_comparison_data": data_path,
        },
    }


def _comparison_records(
    *,
    diagram_rows: list[dict[str, Any]],
    fen_payload: Mapping[str, Any],
    engine_payload: Mapping[str, Any],
    pgn_payload: Mapping[str, Any],
    pgn_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diagrams_by_id = {_diagram_id(row, index): dict(row) for index, row in enumerate(diagram_rows, start=1)}
    engine_by_id = {
        str(row.get("diagram_id") or ""): dict(row)
        for row in engine_payload.get("items") or []
        if isinstance(row, Mapping) and str(row.get("diagram_id") or "")
    }
    source_pgn_by_id = {
        str(row.get("record_id") or row.get("id") or ""): dict(row)
        for row in pgn_records
        if str(row.get("record_id") or row.get("id") or "")
    }
    accepted_pgn_items = [
        dict(row)
        for row in pgn_payload.get("items") or []
        if isinstance(row, Mapping)
        and str(row.get("runtime_status") or row.get("status") or "") in ACCEPTED_PGN_STATUSES
        and str(row.get("selected_value") or "").strip()
    ]
    records: list[dict[str, Any]] = []
    for index, item in enumerate(fen_payload.get("items") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        diagram_id = str(item.get("id") or item.get("diagram_id") or f"diagram-{index}")
        fen_status = str(item.get("runtime_status") or item.get("status") or "")
        if fen_status not in ACCEPTED_FEN_STATUSES:
            continue
        fen = str(item.get("selected_value") or item.get("fen") or "").strip()
        if not fen:
            continue
        diagram = diagrams_by_id.get(diagram_id, {})
        engine = engine_by_id.get(diagram_id, {})
        pgn_item, source_record = _best_book_move_item(
            diagram_id=diagram_id,
            diagram=diagram,
            fen_item=dict(item),
            pgn_items=accepted_pgn_items,
            source_pgn_by_id=source_pgn_by_id,
        )
        records.append(_comparison_record(diagram_id, dict(item), engine, pgn_item, source_record, fen))
    return records


def _comparison_record(
    diagram_id: str,
    fen_item: Mapping[str, Any],
    engine: Mapping[str, Any],
    pgn_item: Mapping[str, Any] | None,
    source_record: Mapping[str, Any] | None,
    fen: str,
) -> dict[str, Any]:
    engine_status = str(engine.get("engine_status") or "missing")
    engine_best_uci = str(engine.get("best_move_uci") or "")
    engine_best_san = str(engine.get("best_move_san") or "")
    base = {
        "schema": BOOK_MOVE_RECORD_SCHEMA,
        "diagram_id": diagram_id,
        "page": _first_non_empty(fen_item.get("page"), engine.get("page"), (source_record or {}).get("page"), (source_record or {}).get("source_page"), 0),
        "fen": fen,
        "book_move_raw": "",
        "book_move_san": "",
        "book_move_uci": "",
        "engine_best_move_san": engine_best_san,
        "engine_best_move_uci": engine_best_uci,
        "match_status": "",
        "score_before": engine.get("score_cp"),
        "score_after_book_move": None,
        "score_after_engine_move": _engine_best_score(engine),
        "requires_review": True,
        "review_reason": "",
        "source_pgn_id": str((pgn_item or {}).get("id") or ""),
        "source_pgn_status": str((pgn_item or {}).get("runtime_status") or (pgn_item or {}).get("status") or ""),
        "source_type": str((pgn_item or {}).get("source_type") or ""),
    }
    if engine_status != "ok" or not engine_best_uci:
        return {
            **base,
            "match_status": "engine_unavailable",
            "review_reason": str(engine.get("skip_reason") or engine_status or "engine_analysis_missing"),
        }
    if not pgn_item:
        return {**base, "match_status": "no_book_move", "review_reason": "no_accepted_book_move_for_diagram"}

    move_text = _book_move_source_text(pgn_item, source_record)
    parsed = _parse_first_book_move(fen, move_text)
    if parsed.get("status") == "missing":
        return {**base, "match_status": "no_book_move", "review_reason": "book_move_not_parseable"}
    if parsed.get("status") == "illegal":
        return {
            **base,
            "book_move_raw": str(parsed.get("raw") or ""),
            "book_move_san": str(parsed.get("san") or ""),
            "book_move_uci": str(parsed.get("uci") or ""),
            "match_status": "book_move_illegal",
            "review_reason": str(parsed.get("reason") or "book_move_illegal_from_accepted_fen"),
        }
    book_uci = str(parsed.get("uci") or "")
    match_status = "exact_match" if book_uci == engine_best_uci else "book_move_legal_but_not_best"
    return {
        **base,
        "book_move_raw": str(parsed.get("raw") or ""),
        "book_move_san": str(parsed.get("san") or ""),
        "book_move_uci": book_uci,
        "match_status": match_status,
        "requires_review": match_status != "exact_match",
        "review_reason": "" if match_status == "exact_match" else "book_move_is_legal_but_differs_from_engine_best",
    }


def _best_book_move_item(
    *,
    diagram_id: str,
    diagram: Mapping[str, Any],
    fen_item: Mapping[str, Any],
    pgn_items: list[dict[str, Any]],
    source_pgn_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not pgn_items:
        return None, None
    fen = str(fen_item.get("selected_value") or "").strip()
    diagram_keys = _record_keys({**dict(diagram), **dict(fen_item), "id": diagram_id, "diagram_id": diagram_id})
    scored: list[tuple[int, int, dict[str, Any], dict[str, Any] | None]] = []
    for index, item in enumerate(pgn_items):
        source = source_pgn_by_id.get(str(item.get("id") or "")) or {}
        keys = _record_keys({**dict(source), **item})
        score = 0
        if diagram_keys and diagram_keys.intersection(keys):
            score += 100
        if fen and str(item.get("source_fen") or "").strip() == fen:
            score += 50
        if int(_first_non_empty(item.get("page"), source.get("page"), source.get("source_page"), 0) or 0) == int(
            _first_non_empty(fen_item.get("page"), diagram.get("page"), 0) or 0
        ):
            score += 5
        if score > 0:
            scored.append((score, -index, item, dict(source) if source else None))
    if not scored:
        return None, None
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return scored[0][2], scored[0][3]


def _parse_first_book_move(fen: str, text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if not value:
        return {"status": "missing", "reason": "empty_book_move_text"}
    try:
        import chess  # type: ignore
        import chess.pgn  # type: ignore
    except Exception as exc:
        return {"status": "missing", "reason": f"python_chess_unavailable:{exc}"}
    try:
        accepted_board = chess.Board(fen)
    except Exception as exc:
        return {"status": "missing", "reason": f"invalid_fen:{exc}"}
    for candidate in _pgn_parse_candidates(fen, value):
        game = chess.pgn.read_game(StringIO(candidate))
        if game is None or getattr(game, "errors", None):
            continue
        source_board = game.board()
        moves = list(game.mainline_moves())
        if not moves:
            continue
        move = moves[0]
        raw_san = ""
        try:
            raw_san = source_board.san(move)
        except Exception:
            raw_san = move.uci()
        if move in accepted_board.legal_moves:
            return {"status": "legal", "raw": raw_san, "san": accepted_board.san(move), "uci": move.uci()}
        try:
            parsed = accepted_board.parse_san(raw_san)
            return {"status": "legal", "raw": raw_san, "san": accepted_board.san(parsed), "uci": parsed.uci()}
        except Exception:
            return {"status": "illegal", "raw": raw_san, "san": raw_san, "uci": move.uci(), "reason": "book_move_illegal_from_accepted_fen"}
    return {"status": "missing", "reason": "book_move_not_parseable"}


def _pgn_parse_candidates(fen: str, value: str) -> list[str]:
    normalized = str(value or "").strip()
    candidates: list[str] = []
    if re.search(r"(?m)^\s*\[[A-Za-z0-9_]+\s+", normalized):
        candidates.append(normalized)
    movetext = _strip_pgn_headers(normalized)
    if movetext:
        candidates.append(
            "\n".join(
                [
                    '[Event "Book move candidate"]',
                    '[Site "?"]',
                    '[Date "????.??.??"]',
                    '[Round "?"]',
                    '[White "?"]',
                    '[Black "?"]',
                    '[Result "*"]',
                    '[SetUp "1"]',
                    f'[FEN "{fen}"]',
                    "",
                    movetext,
                ]
            )
        )
    return candidates


def _strip_pgn_headers(value: str) -> str:
    lines = []
    for line in str(value or "").splitlines():
        if re.match(r"^\s*\[[A-Za-z0-9_]+\s+", line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _book_move_source_text(pgn_item: Mapping[str, Any] | None, source_record: Mapping[str, Any] | None) -> str:
    for value in [
        (pgn_item or {}).get("selected_value"),
        (source_record or {}).get("pgn"),
        (source_record or {}).get("movetext"),
        (source_record or {}).get("visible_review_text"),
        (source_record or {}).get("raw_text"),
        (pgn_item or {}).get("value"),
    ]:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _record_keys(row: Mapping[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ("id", "diagram_id", "source_diagram", "label", "caption", "record_id"):
        normalized = _normalize_source_label(str(row.get(key) or ""))
        if normalized:
            keys.add(normalized)
    return keys


def _diagram_id(row: Mapping[str, Any], index: int) -> str:
    return str(row.get("diagram_id") or row.get("id") or f"diagram-{index}")


def _diagrams_from_book(book: Mapping[str, Any]) -> list[dict[str, Any]]:
    diagrams: list[dict[str, Any]] = []
    for page in book.get("pages") or []:
        if not isinstance(page, Mapping):
            continue
        page_number = int(page.get("page") or 0)
        for diagram in page.get("diagrams") or []:
            if isinstance(diagram, Mapping):
                diagrams.append({**dict(diagram), "page": int(diagram.get("page") or page_number)})
    return diagrams


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = _count_by(records, "match_status")
    return {
        "comparison_count": len(records),
        "exact_match_count": status_counts.get("exact_match", 0),
        "equivalent_move_count": status_counts.get("equivalent_move", 0),
        "book_move_legal_but_not_best_count": status_counts.get("book_move_legal_but_not_best", 0),
        "book_move_illegal_count": status_counts.get("book_move_illegal", 0),
        "no_book_move_count": status_counts.get("no_book_move", 0),
        "engine_unavailable_count": status_counts.get("engine_unavailable", 0),
        "requires_review_count": len([row for row in records if row.get("requires_review")]),
        "by_match_status": status_counts,
    }


def _comparison_markdown(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Book Move vs Engine Comparison",
        "",
        f"- comparisons: `{summary.get('comparison_count', 0)}`",
        f"- exact matches: `{summary.get('exact_match_count', 0)}`",
        f"- legal but not best: `{summary.get('book_move_legal_but_not_best_count', 0)}`",
        f"- illegal book moves: `{summary.get('book_move_illegal_count', 0)}`",
        f"- no book move: `{summary.get('no_book_move_count', 0)}`",
        f"- engine unavailable: `{summary.get('engine_unavailable_count', 0)}`",
        "",
        "| Diagram | Page | Book move | Engine best | Status | Review | Reason |",
        "| --- | ---: | --- | --- | --- | --- | --- |",
    ]
    for item in payload.get("items") or []:
        lines.append(
            "| {diagram} | {page} | {book} | {engine} | {status} | {review} | {reason} |".format(
                diagram=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                book=_md(str(item.get("book_move_san") or item.get("book_move_uci") or "")),
                engine=_md(str(item.get("engine_best_move_san") or item.get("engine_best_move_uci") or "")),
                status=_md(str(item.get("match_status") or "")),
                review=_md(str(bool(item.get("requires_review")))),
                reason=_md(str(item.get("review_reason") or "")),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _comparison_html(payload: Mapping[str, Any]) -> str:
    summary = payload.get("summary") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('diagram_id') or ''))}</td>"
        f"<td>{html.escape(str(item.get('page') or ''))}</td>"
        f"<td>{html.escape(str(item.get('book_move_san') or item.get('book_move_uci') or ''))}</td>"
        f"<td>{html.escape(str(item.get('engine_best_move_san') or item.get('engine_best_move_uci') or ''))}</td>"
        f"<td class='{html.escape(str(item.get('match_status') or ''), quote=True)}'>{html.escape(str(item.get('match_status') or ''))}</td>"
        f"<td>{html.escape(str(bool(item.get('requires_review'))))}</td>"
        f"<td>{html.escape(str(item.get('review_reason') or ''))}</td>"
        "</tr>"
        for item in payload.get("items") or []
    ) or "<tr><td colspan='7'>No accepted FEN positions were eligible for comparison.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Book Move vs Engine Comparison</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d7dde6; padding: .45rem .55rem; text-align: left; vertical-align: top; }}
    th {{ background: #eef2f7; }}
    .stats {{ display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }}
    .stat {{ border:1px solid #d7dde6; padding:.65rem .8rem; border-radius:.35rem; background:#fff; }}
    .exact_match {{ color:#146b3a; font-weight:700; }}
    .book_move_legal_but_not_best, .book_move_illegal, .no_book_move, .engine_unavailable {{ color:#9a1b1b; font-weight:700; }}
  </style>
</head>
<body>
  <h1>Book Move vs Engine Comparison</h1>
  <p>Audit-only comparison. A difference from the engine best move requires review; it is not an automatic book error.</p>
  <section class="stats">
    <div class="stat">Comparisons: <strong>{html.escape(str(summary.get('comparison_count', 0)))}</strong></div>
    <div class="stat">Exact: <strong>{html.escape(str(summary.get('exact_match_count', 0)))}</strong></div>
    <div class="stat">Legal but not best: <strong>{html.escape(str(summary.get('book_move_legal_but_not_best_count', 0)))}</strong></div>
    <div class="stat">Illegal: <strong>{html.escape(str(summary.get('book_move_illegal_count', 0)))}</strong></div>
    <div class="stat">No book move: <strong>{html.escape(str(summary.get('no_book_move_count', 0)))}</strong></div>
  </section>
  <table>
    <thead><tr><th>Diagram</th><th>Page</th><th>Book move</th><th>Engine best</th><th>Status</th><th>Review</th><th>Reason</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""


def _engine_best_score(engine: Mapping[str, Any]) -> Any:
    pv = engine.get("pv")
    if isinstance(pv, list) and pv and isinstance(pv[0], Mapping):
        first = pv[0]
        if first.get("mate") is not None:
            return first.get("mate")
        return first.get("score_cp")
    return engine.get("score_cp")


def _normalize_source_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _count_by(records: Iterable[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "")
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iso_utc(timestamp: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


def _md(value: str) -> str:
    return value.replace("|", "\\|")
