from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any


PGN_RUNTIME_ACCEPTED_STATUSES = {"PGN_MACHINE_ACCEPTED", "PGN_MACHINE_REPAIRED", "SOLUTION_LINE_ACCEPTED"}
SOLUTION_TYPES = {"EXERCISE_SOLUTION", "TACTICAL_LINE"}


def classify_source_type(record: dict[str, Any]) -> str:
    text = " ".join(str(record.get(key) or "") for key in ["label", "raw_text", "visible_review_text", "pgn", "annotated_pgn"])
    if re.search(r"\bEx\.\s*\d{1,2}[-.]\d{1,2}\b", text, flags=re.IGNORECASE):
        return "EXERCISE_SOLUTION"
    if re.search(r"\bDiagram\s+\d{1,2}[-.]\d{1,2}\b", text, flags=re.IGNORECASE):
        return "TACTICAL_LINE"
    if "[Event" in text:
        return "FULL_GAME"
    if re.search(r"\b\d{1,3}\s*\.\s*(?:\.\.)?\s*[KQRBN]?[a-hO0]", text):
        return "COMMENTARY_WITH_MOVES"
    return "UNKNOWN"


def extract_solution_movetext(record: dict[str, Any]) -> str:
    for key in ["movetext", "annotated_pgn", "pgn", "visible_review_text", "raw_text"]:
        value = str(record.get(key) or "").strip()
        if not value:
            continue
        if "[Event" in value:
            return value.split("\n\n", 1)[-1].strip()
        cleaned = _strip_source_label(value)
        if re.search(r"\d+\s*\.", cleaned):
            return cleaned
    return ""


def build_solution_pgn_from_fen(source_fen: str, movetext: str, *, record: dict[str, Any] | None = None) -> str:
    record = record or {}
    source_page = str(record.get("page") or record.get("source_page") or record.get("logical_page") or "?")
    source_diagram = str(record.get("diagram_id") or record.get("source_diagram") or record.get("label") or record.get("id") or "?")
    normalized = _normalize_movetext(movetext)
    headers = [
        '[Event "Exercise Solution"]',
        '[Site "?"]',
        '[Date "????.??.??"]',
        '[Round "?"]',
        '[White "?"]',
        '[Black "?"]',
        '[Result "*"]',
        '[SetUp "1"]',
        f'[FEN "{source_fen}"]',
        f'[SourcePage "{source_page}"]',
        f'[SourceDiagram "{_escape_tag(source_diagram)}"]',
    ]
    return "\n".join(headers) + "\n\n" + normalized.strip() + (" *" if not normalized.strip().endswith("*") else "") + "\n"


def repair_pgn_tokens_with_legal_replay(record: dict[str, Any], source_fen: str) -> dict[str, Any]:
    source_type = classify_source_type(record)
    movetext = extract_solution_movetext(record)
    if not movetext:
        return _repair_result(record, source_type, "", source_fen, accepted=False, blockers=["movetext_missing"])
    base_pgn = build_solution_pgn_from_fen(source_fen, movetext, record=record) if source_type in SOLUTION_TYPES else _ensure_full_pgn(record)
    replay = _replay_pgn(base_pgn)
    if replay["valid"]:
        return _repair_result(record, source_type, base_pgn, source_fen, accepted=True, final_fen=replay.get("final_fen"))
    for candidate_movetext, replacements in _token_repair_candidates(movetext):
        candidate_pgn = build_solution_pgn_from_fen(source_fen, candidate_movetext, record=record) if source_type in SOLUTION_TYPES else _ensure_full_pgn({**record, "pgn": candidate_movetext})
        replay = _replay_pgn(candidate_pgn)
        if replay["valid"]:
            return _repair_result(
                record,
                source_type,
                candidate_pgn,
                source_fen,
                accepted=True,
                final_fen=replay.get("final_fen"),
                replacements=replacements,
            )
    return _repair_result(record, source_type, base_pgn, source_fen, accepted=False, blockers=[replay.get("error") or "pgn_replay_failed"])


def accept_solution_line_if_legal(record: dict[str, Any], source_fen: str) -> dict[str, Any]:
    if not source_fen:
        return _repair_result(record, classify_source_type(record), "", "", accepted=False, blockers=["source_fen_missing"])
    return repair_pgn_tokens_with_legal_replay(record, source_fen)


def repair_and_accept_pgn_records(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    book = _load_book(out)
    fen_index = _accepted_fen_index(book)
    rows: list[dict[str, Any]] = []
    for record in book.get("pgn_records") or []:
        source_fen = _record_source_fen(record, fen_index)
        source_type = classify_source_type(record)
        if source_type in SOLUTION_TYPES:
            row = accept_solution_line_if_legal(record, source_fen)
        else:
            pgn = str(record.get("pgn") or record.get("annotated_pgn") or "").strip()
            replay = _replay_pgn(pgn)
            row = _repair_result(record, source_type, pgn, source_fen, accepted=bool(replay["valid"]), final_fen=replay.get("final_fen"))
        rows.append(row)
    review_dir = out / "review"
    reports_dir = out / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(review_dir / "pgn_auto_repair_candidates.jsonl", rows)
    _write_json(review_dir / "glyph_mapping_auto.json", build_auto_glyph_mapping_candidates(rows))
    accepted = [row for row in rows if row.get("runtime_status") in PGN_RUNTIME_ACCEPTED_STATUSES]
    payload = {
        "schema": "kindlemaster.pgn_auto_repair_eval.v1",
        "status": "ok",
        "record_count": len(rows),
        "accepted_count": len(accepted),
        "repairs_applied": len([row for row in accepted if row.get("replacements")]),
        "top_blockers": _top_blockers(rows),
    }
    _write_json(reports_dir / "pgn_auto_repair_eval.json", payload)
    _write_json(reports_dir / "glyph_mapping_auto_eval.json", {"status": "ok", "mappings": len(build_auto_glyph_mapping_candidates(rows).get("mappings") or [])})
    return payload


def apply_runtime_accepted_pgn(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    book = _load_book(out)
    rows = {str(row.get("record_id") or row.get("id") or ""): row for row in _read_jsonl(out / "review" / "pgn_auto_repair_candidates.jsonl")}
    applied = 0
    accepted_pgn: list[str] = []
    for record in book.get("pgn_records") or []:
        record_id = str(record.get("record_id") or record.get("id") or "")
        row = rows.get(record_id)
        if not row or row.get("runtime_status") not in PGN_RUNTIME_ACCEPTED_STATUSES:
            continue
        selected = str(row.get("selected_value") or "").strip()
        if not selected:
            continue
        record["pgn"] = selected
        record["status"] = "accepted"
        record["runtime_status"] = row.get("runtime_status")
        record["final_fen"] = row.get("final_fen") or ""
        record["acceptance_trace"] = row.get("acceptance_trace") or {}
        record["warnings"] = []
        accepted_pgn.append(selected)
        applied += 1
    for page in book.get("pages") or []:
        for record in page.get("pgn_records") or []:
            record_id = str(record.get("record_id") or record.get("id") or "")
            row = rows.get(record_id)
            if row and row.get("runtime_status") in PGN_RUNTIME_ACCEPTED_STATUSES and row.get("selected_value"):
                record["pgn"] = row["selected_value"]
                record["status"] = "accepted"
                record["runtime_status"] = row.get("runtime_status")
                record["final_fen"] = row.get("final_fen") or ""
    _write_json(out / "data" / "book.json", book)
    games = "\n\n".join(accepted_pgn).strip()
    (out / "data" / "games.pgn").write_text(games + ("\n" if games else ""), encoding="utf-8")
    payload = {"schema": "kindlemaster.pgn_apply_runtime_acceptance.v1", "status": "ok", "applied_count": applied}
    _write_json(out / "reports" / "pgn_apply_runtime_acceptance.json", payload)
    return payload


def build_auto_glyph_mapping_candidates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mappings: list[dict[str, Any]] = []
    for row in rows:
        for replacement in row.get("replacements") or []:
            mappings.append({"token": replacement.get("from"), "replacement": replacement.get("to"), "status": "auto_replay_proven", "record_id": row.get("record_id")})
    return {"schema": "kindlemaster.glyph_mapping_auto.v1", "mappings": mappings}


def apply_safe_glyph_mapping_if_replay_improves(record: dict[str, Any]) -> dict[str, Any]:
    return {"record_id": record.get("record_id") or record.get("id"), "applied": False, "reason": "handled_by_repair_pgn_tokens_with_legal_replay"}


def _repair_result(
    record: dict[str, Any],
    source_type: str,
    selected_value: str,
    source_fen: str,
    *,
    accepted: bool,
    final_fen: str = "",
    blockers: list[str] | None = None,
    replacements: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    runtime_status = "SOLUTION_LINE_ACCEPTED" if accepted and source_type in SOLUTION_TYPES else ("PGN_MACHINE_ACCEPTED" if accepted else "PGN_REVIEW_REQUIRED")
    return {
        "schema": "kindlemaster.pgn_auto_repair_candidate.v1",
        "record_id": record.get("record_id") or record.get("id"),
        "id": record.get("record_id") or record.get("id"),
        "page": int(record.get("page") or record.get("source_page") or record.get("logical_page") or 0),
        "source_type": source_type,
        "source_fen": source_fen,
        "runtime_status": runtime_status,
        "status": runtime_status,
        "selected_value": selected_value if accepted else "",
        "candidate_value": selected_value,
        "final_fen": final_fen,
        "acceptance_blockers": [{"code": blocker, "message": blocker} for blocker in blockers or []],
        "acceptance_trace": {"policy": "runtime_pgn_replay_acceptance_v1", "source_type": source_type},
        "replacements": replacements or [],
    }


def _strip_source_label(value: str) -> str:
    return re.sub(r"^(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\s*", "", value.strip(), flags=re.IGNORECASE)


def _normalize_movetext(value: str) -> str:
    text = value.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[-–—:;,\s]+", "", text)
    return text


def _token_repair_candidates(movetext: str) -> list[tuple[str, list[dict[str, str]]]]:
    replacements = [
        ("0-0-0", "O-O-O"),
        ("0-0", "O-O"),
        (" T", " R"),
        (" D", " Q"),
        (" L", " B"),
        (" S", " N"),
        ("I", "1"),
    ]
    rows: list[tuple[str, list[dict[str, str]]]] = []
    for source, target in replacements:
        if source in movetext:
            rows.append((movetext.replace(source, target), [{"from": source, "to": target}]))
    return rows


def _ensure_full_pgn(record: dict[str, Any]) -> str:
    value = str(record.get("pgn") or record.get("annotated_pgn") or "").strip()
    if "[Event" in value:
        return value
    return ""


def _replay_pgn(pgn_text: str) -> dict[str, Any]:
    if not str(pgn_text or "").strip():
        return {"valid": False, "error": "pgn_missing_or_not_embedded"}
    try:
        import chess.pgn  # type: ignore

        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            return {"valid": False, "error": "pgn_parse_failed"}
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
        return {"valid": True, "final_fen": board.fen()}
    except Exception as exc:
        return {"valid": False, "error": f"{exc.__class__.__name__}: {exc}"}


def _accepted_fen_index(book: dict[str, Any]) -> dict[str, str]:
    index: dict[str, str] = {}
    for diagram in [diagram for page in book.get("pages") or [] for diagram in page.get("diagrams") or []]:
        if str(diagram.get("validation_status") or diagram.get("status") or "") != "accepted":
            continue
        fen = str(diagram.get("fen") or "").strip()
        if not fen:
            continue
        for key in [diagram.get("id"), diagram.get("diagram_id"), diagram.get("caption"), diagram.get("label")]:
            normalized = _normalize_source_label(str(key or ""))
            if normalized:
                index[normalized] = fen
    return index


def _record_source_fen(record: dict[str, Any], index: dict[str, str]) -> str:
    for key in [record.get("diagram_id"), record.get("source_diagram"), record.get("label"), record.get("id")]:
        normalized = _normalize_source_label(str(key or ""))
        if normalized and normalized in index:
            return index[normalized]
    return ""


def _normalize_source_label(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower()).replace(".", "")


def _load_book(out: Path) -> dict[str, Any]:
    path = out / "data" / "book.json"
    if not path.is_file():
        return {"pages": [], "pgn_records": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _top_blockers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("acceptance_blockers") or []:
            code = str(blocker.get("code") or "")
            if code:
                counts[code] = counts.get(code, 0) + 1
    return [{"key": key, "count": count} for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _escape_tag(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', "'")
