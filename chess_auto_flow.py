from __future__ import annotations

import html
import json
import shutil
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

from chess_fen_hardening import validate_fen_detailed

PIPELINE_STATUSES = {
    "AUTO_SUCCESS",
    "AUTO_SUCCESS_WITH_REPAIRS",
    "AUTO_FAILED_WITH_REASON",
    "MANUAL_REVIEW_AVAILABLE",
}

FEN_ACCEPTED_STATUSES = {"FEN_AUTO_ACCEPTED", "FEN_AUTO_REPAIRED"}
PGN_ACCEPTED_STATUSES = {"PGN_AUTO_ACCEPTED", "PGN_AUTO_REPAIRED"}


def run_auto_chess_process(
    pdf_path: str | Path,
    *,
    out_dir: str | Path,
    mode: str = "auto",
    html_path: str | Path | None = None,
    quality_profile: str = "default",
    render_pages: bool = False,
    with_ai: bool = False,
    dry_run_ai: bool = False,
    ai_limit: int = 0,
    ai_pgn_limit: int = 30,
    diagram_page_ranges: str = "",
    glyph_mapping_file: str | Path | None = None,
) -> dict[str, Any]:
    """Run the front-door chess flow and map existing chess-study outputs.

    The heavy extraction still lives in chess-study. This function adds the
    promised single-command contract, canonical artifact tree and strict export
    status without letting AI candidates promote FEN/PGN.
    """

    from chess_study_export import (
        build_ai_assisted_quality_eval,
        build_ai_fen_candidates,
        build_ai_pgn_candidates,
        run_chess_study_export,
    )

    out = Path(out_dir)
    pdf = Path(pdf_path)
    source_html = Path(html_path) if html_path else None
    try:
        stage_payload = run_chess_study_export(
            pdf,
            html_path=source_html,
            out_dir=out,
            quality_profile=quality_profile,
            render_pages=render_pages,
            diagram_page_ranges=diagram_page_ranges,
            glyph_mapping_file=glyph_mapping_file,
        )
        ai_payloads: dict[str, Any] = {}
        if with_ai:
            ai_payloads["fen"] = build_ai_fen_candidates(out, limit=ai_limit, dry_run=dry_run_ai)
            ai_payloads["pgn"] = build_ai_pgn_candidates(
                out,
                glyph_mapping_file=Path(glyph_mapping_file) if glyph_mapping_file else None,
                limit=ai_pgn_limit,
                dry_run=dry_run_ai,
            )
            ai_payloads["quality"] = build_ai_assisted_quality_eval(out)
        payload = build_auto_chess_flow_artifacts(
            out,
            mode=mode,
            source_pdf=pdf,
            source_html=source_html,
            stage_payload=stage_payload,
            ai_payloads=ai_payloads,
        )
    except Exception as exc:
        payload = _failed_process_payload(
            out,
            mode=mode,
            source_pdf=pdf,
            source_html=source_html,
            error=f"{exc.__class__.__name__}: {exc}",
        )

    if mode == "auto-strict" and payload.get("status") != "AUTO_SUCCESS":
        payload = {**payload, "status": "AUTO_FAILED_WITH_REASON", "strict_failed": True}
        _write_json(Path(out_dir) / "auto_chess_flow.json", payload)
    return payload


def build_auto_chess_flow_artifacts(
    out_dir: str | Path,
    *,
    mode: str = "auto",
    source_pdf: str | Path | None = None,
    source_html: str | Path | None = None,
    stage_payload: dict[str, Any] | None = None,
    ai_payloads: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = Path(out_dir)
    dirs = _ensure_auto_dirs(out)
    book = _read_optional_json(out / "data" / "book.json")
    diagrams_payload = _read_optional_json(out / "data" / "diagrams.json")
    dashboard = _load_or_build_dashboard(out)
    pages = list(book.get("pages") or [])
    diagrams = _extract_diagrams(book, diagrams_payload)
    pgn_records = list(book.get("pgn_records") or [])
    ai_fen_rows = _read_jsonl(out / "review" / "ai_fen_candidates.jsonl")
    model_fen_rows = _read_jsonl(out / "review" / "fen_model_predictions.jsonl")
    pgn_lattice_rows = _read_jsonl(out / "review" / "pgn_lattice_review.jsonl")

    page_payload = _canonical_pages(pages)
    layout_payload = _canonical_layout(pages)
    text_rows = _canonical_text_rows(pages)
    diagram_payload = {"schema": "kindlemaster.auto_chess.diagrams.v1", "diagrams": diagrams}
    fen_payload, fen_validation, fen_repairs = _canonical_fen(diagrams, ai_fen_rows, model_fen_rows)
    pgn_payload, pgn_validation, pgn_repairs = _canonical_pgn(pgn_records, pgn_lattice_rows)
    repair_payload = {
        "schema": "kindlemaster.auto_chess.repairs.v1",
        "repairs": fen_repairs + pgn_repairs,
        "summary": {
            "attempted": len(fen_repairs) + len(pgn_repairs),
            "applied": len([row for row in fen_repairs + pgn_repairs if row.get("applied")]),
        },
    }
    summary = _auto_summary(
        dashboard,
        fen_validation=fen_validation,
        pgn_validation=pgn_validation,
        repair_payload=repair_payload,
    )
    status = _pipeline_status(summary, mode=mode)
    report = _quality_report(
        out,
        status=status,
        mode=mode,
        source_pdf=source_pdf,
        source_html=source_html,
        summary=summary,
        stage_payload=stage_payload or {},
        ai_payloads=ai_payloads or {},
    )

    _write_json(dirs["pages"] / "pages.json", page_payload)
    _write_json(dirs["layout"] / "layout.json", layout_payload)
    _write_jsonl(dirs["text"] / "text_blocks.jsonl", text_rows)
    _write_json(dirs["diagrams"] / "diagrams.json", diagram_payload)
    _write_json(dirs["fen"] / "fen_candidates.json", fen_payload)
    _write_json(dirs["fen"] / "fen_validation.json", fen_validation)
    _write_json(dirs["pgn"] / "pgn_candidates.json", pgn_payload)
    _write_json(dirs["pgn"] / "pgn_validation.json", pgn_validation)
    _write_json(dirs["repair"] / "repair_attempts.json", repair_payload)
    _write_json(dirs["report"] / "quality_report.json", report)
    (dirs["report"] / "quality_report.html").write_text(_quality_report_html(report), encoding="utf-8")
    _copy_export_files(out, dirs["export"])

    payload = {
        "schema": "kindlemaster.auto_chess_flow.v1",
        "status": status,
        "mode": mode,
        "out_dir": str(out),
        "source_pdf": str(source_pdf or ""),
        "source_html": str(source_html or ""),
        "summary": summary,
        "artifacts": {
            name: str(path)
            for name, path in {
                "pages": dirs["pages"] / "pages.json",
                "layout": dirs["layout"] / "layout.json",
                "text": dirs["text"] / "text_blocks.jsonl",
                "diagrams": dirs["diagrams"] / "diagrams.json",
                "fen_candidates": dirs["fen"] / "fen_candidates.json",
                "fen_validation": dirs["fen"] / "fen_validation.json",
                "pgn_candidates": dirs["pgn"] / "pgn_candidates.json",
                "pgn_validation": dirs["pgn"] / "pgn_validation.json",
                "repairs": dirs["repair"] / "repair_attempts.json",
                "quality_report": dirs["report"] / "quality_report.json",
                "quality_report_html": dirs["report"] / "quality_report.html",
                "export_games_pgn": dirs["export"] / "games.pgn",
            }.items()
        },
        "strict_failed": bool(mode == "auto-strict" and status != "AUTO_SUCCESS"),
    }
    _write_json(out / "auto_chess_flow.json", payload)
    return payload


def validate_auto_chess_output(out_dir: str | Path, *, strict: bool = False) -> dict[str, Any]:
    out = Path(out_dir)
    if not out.is_dir():
        return {
            "schema": "kindlemaster.auto_chess_validation.v1",
            "overall_status": "failed",
            "errors": [{"code": "output_dir_missing", "message": f"Output directory does not exist: {out}"}],
            "warnings": [],
        }
    if not (out / "auto_chess_flow.json").is_file():
        build_auto_chess_flow_artifacts(out)
    flow = _read_optional_json(out / "auto_chess_flow.json")
    report = _read_optional_json(out / "report" / "quality_report.json")
    required = [
        "pages/pages.json",
        "layout/layout.json",
        "text/text_blocks.jsonl",
        "diagrams/diagrams.json",
        "fen/fen_candidates.json",
        "fen/fen_validation.json",
        "pgn/pgn_candidates.json",
        "pgn/pgn_validation.json",
        "repair/repair_attempts.json",
        "report/quality_report.json",
        "export/games.pgn",
    ]
    errors = [
        {"code": "missing_artifact", "path": rel}
        for rel in required
        if not (out / rel).is_file()
    ]
    summary = dict(flow.get("summary") or report.get("summary") or {})
    unresolved = int(summary.get("fen_failed") or 0) + int(summary.get("pgn_failed") or 0)
    if strict and unresolved > 0:
        errors.append(
            {
                "code": "strict_unresolved_chess_items",
                "message": "auto-strict requires all FEN/PGN items to be accepted or repaired.",
                "unresolved": unresolved,
            }
        )
    warnings = []
    if unresolved > 0:
        warnings.append(
            {
                "code": "manual_review_available",
                "message": "Some FEN/PGN items remain review-only.",
                "unresolved": unresolved,
            }
        )
    status = "failed" if errors else ("requires_review" if warnings else "passed")
    payload = {
        "schema": "kindlemaster.auto_chess_validation.v1",
        "overall_status": status,
        "strict": strict,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    _write_json(out / "report" / "validation_report.json", payload)
    return payload


def report_auto_chess_output(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    if not (out / "auto_chess_flow.json").is_file():
        build_auto_chess_flow_artifacts(out)
    report = _read_optional_json(out / "report" / "quality_report.json")
    if not report:
        build_auto_chess_flow_artifacts(out)
        report = _read_optional_json(out / "report" / "quality_report.json")
    return report or _read_optional_json(out / "auto_chess_flow.json")


def review_auto_chess_output(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    review_dir = out / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    if not (out / "auto_chess_flow.json").is_file():
        build_auto_chess_flow_artifacts(out)
    candidates = [
        ("FEN manual review", review_dir / "fen_manual_review.html"),
        ("FEN AI candidate review", review_dir / "fen_ai_candidate_review_batch.html"),
        ("FEN ensemble conflicts", review_dir / "fen_ensemble_conflicts.html"),
        ("PGN lattice review", review_dir / "pgn_lattice_review.csv"),
        ("Glyph mapping review", review_dir / "glyph_mapping_review.html"),
        ("PGN replay blockers", review_dir / "pgn_replay_blockers_top10.md"),
    ]
    items = [
        {"label": label, "path": str(path), "exists": path.is_file()}
        for label, path in candidates
    ]
    index_path = review_dir / "index.html"
    index_path.write_text(_review_index_html(items), encoding="utf-8")
    payload = {
        "schema": "kindlemaster.auto_chess_review.v1",
        "status": "ok",
        "review_index": str(index_path),
        "items": items,
    }
    _write_json(review_dir / "review_index.json", payload)
    return payload


def is_auto_chess_output(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_dir() and (
        (candidate / "auto_chess_flow.json").is_file()
        or (candidate / "data" / "book.json").is_file()
        or (candidate / "reports" / "chess_quality_dashboard.json").is_file()
    )


def _canonical_pages(pages: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for page in pages:
        rows.append(
            {
                "page": int(page.get("page") or page.get("page_number") or 0),
                "source_order": int(page.get("source_order") or 0),
                "text_blocks": len(page.get("text_blocks") or page.get("blocks") or []),
                "diagrams": [
                    str(item.get("diagram_id") or item.get("id") or "")
                    for item in page.get("diagrams") or []
                ],
                "pgn_records": [
                    str(item.get("record_id") or item.get("id") or "")
                    for item in page.get("pgn_records") or []
                ],
            }
        )
    return {"schema": "kindlemaster.auto_chess.pages.v1", "pages": rows}


def _canonical_layout(pages: list[dict[str, Any]]) -> dict[str, Any]:
    elements: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page.get("page") or 0)
        for block in page.get("text_blocks") or page.get("blocks") or []:
            elements.append(_layout_element("text", page_number, block))
        for diagram in page.get("diagrams") or []:
            elements.append(_layout_element("diagram", page_number, diagram))
        for record in page.get("pgn_records") or []:
            elements.append(_layout_element("pgn", page_number, record))
    elements.sort(key=lambda item: (item.get("page", 0), item.get("reading_order", 0), item.get("bbox") or [0, 0, 0, 0]))
    return {"schema": "kindlemaster.auto_chess.layout.v1", "elements": elements}


def _layout_element(kind: str, page_number: int, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": kind,
        "id": str(source.get("id") or source.get("diagram_id") or source.get("record_id") or ""),
        "page": page_number,
        "bbox": source.get("bbox") or [],
        "reading_order": int(source.get("reading_order") or source.get("source_order") or 0),
        "status": source.get("status") or source.get("validation_status") or "",
    }


def _canonical_text_rows(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page.get("page") or 0)
        for index, block in enumerate(page.get("text_blocks") or page.get("blocks") or [], start=1):
            text = str(block.get("text") or block.get("normalized_text") or "").strip()
            if not text:
                continue
            rows.append(
                {
                    "page": page_number,
                    "index": index,
                    "bbox": block.get("bbox") or [],
                    "reading_order": int(block.get("reading_order") or index),
                    "text": text,
                }
            )
    return rows


def _canonical_fen(
    diagrams: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ai_by_id = _rows_by_id(ai_rows, "diagram_id")
    model_by_id = _rows_by_id(model_rows, "diagram_id")
    candidates: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for diagram in diagrams:
        diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
        raw_candidates = _fen_raw_candidates(diagram, ai_by_id.get(diagram_id), model_by_id.get(diagram_id))
        candidate_rows = [_fen_candidate_row(item) for item in raw_candidates]
        selected = _select_fen_status(diagram, candidate_rows)
        repair_rows = _fen_repair_rows(diagram_id, raw_candidates)
        repairs.extend(repair_rows)
        candidates.append(
            {
                "id": diagram_id,
                "page": int(diagram.get("page") or 0),
                "source_image_path": diagram.get("image_path") or diagram.get("crop_path") or "",
                "status": selected["status"],
                "candidate_values": candidate_rows,
                "selected_value": selected.get("selected_value"),
                "validation_errors": selected.get("validation_errors", []),
                "repair_attempts": repair_rows,
                "next_action": selected["next_action"],
            }
        )
        validation_rows.append({k: candidates[-1][k] for k in ["id", "page", "status", "validation_errors", "next_action"]})
    summary = _status_summary(candidates, accepted=FEN_ACCEPTED_STATUSES)
    payload = {"schema": "kindlemaster.auto_chess.fen_candidates.v1", "items": candidates, "summary": summary}
    validation = {"schema": "kindlemaster.auto_chess.fen_validation.v1", "items": validation_rows, "summary": summary}
    return payload, validation, repairs


def _fen_raw_candidates(
    diagram: dict[str, Any],
    ai_row: dict[str, Any] | None,
    model_row: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, source in [("fen", "deterministic"), ("fen_candidate", "deterministic_candidate")]:
        fen = str(diagram.get(key) or "").strip()
        if fen:
            rows.append({"source": source, "fen": fen, "authoritative": source == "deterministic"})
    if ai_row:
        fen = str(ai_row.get("ai_fen_candidate") or ai_row.get("fen") or "").strip()
        if fen:
            rows.append({"source": "ai_review_only", "fen": fen, "authoritative": False})
    if model_row:
        fen = str(model_row.get("fen") or model_row.get("predicted_fen") or "").strip()
        if fen:
            rows.append({"source": "local_model_candidate", "fen": fen, "authoritative": False})
    return rows


def _fen_candidate_row(candidate: dict[str, Any]) -> dict[str, Any]:
    validation = validate_fen_detailed(str(candidate.get("fen") or ""))
    return {
        "source": candidate.get("source") or "unknown",
        "value": candidate.get("fen") or "",
        "authoritative": bool(candidate.get("authoritative")),
        "deterministic_valid": validation.is_legal_position and not validation.errors,
        "normalized_value": validation.normalized_fen,
        "errors": [asdict(error) for error in validation.errors],
        "warnings": [asdict(warning) for warning in validation.warnings],
    }


def _select_fen_status(diagram: dict[str, Any], candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    deterministic = next((row for row in candidate_rows if row.get("source") == "deterministic"), None)
    if diagram.get("validation_status") == "accepted" and deterministic and deterministic.get("deterministic_valid"):
        return {
            "status": "FEN_AUTO_ACCEPTED",
            "selected_value": deterministic.get("normalized_value"),
            "validation_errors": [],
            "next_action": "export_allowed",
        }
    if not candidate_rows:
        return {
            "status": "FEN_FAILED",
            "selected_value": None,
            "validation_errors": [{"code": "fen_not_recognized", "message": "No FEN candidate was available."}],
            "next_action": "manual_review",
        }
    errors = []
    for row in candidate_rows:
        for error in row.get("errors") or []:
            errors.append({"source": row.get("source"), **error})
    if any(row.get("deterministic_valid") for row in candidate_rows):
        return {
            "status": "FEN_VALID_POSITION",
            "selected_value": None,
            "validation_errors": errors,
            "next_action": "human_verify_before_export",
        }
    return {
        "status": "FEN_FAILED",
        "selected_value": None,
        "validation_errors": errors or [{"code": "fen_validation_failed", "message": "No candidate passed deterministic FEN validation."}],
        "next_action": "manual_review",
    }


def _fen_repair_rows(diagram_id: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        value = str(candidate.get("fen") or "")
        repaired = _repair_fen(value)
        if not repaired or repaired == value.strip():
            continue
        validation = validate_fen_detailed(repaired)
        rows.append(
            {
                "kind": "fen",
                "id": diagram_id,
                "source": candidate.get("source") or "unknown",
                "input": value,
                "output": repaired,
                "deterministic_valid": validation.is_legal_position and not validation.errors,
                "applied": False,
                "reason": "safe_candidate_normalization_only; requires original accepted gate or human verification",
            }
        )
    return rows


def _repair_fen(value: str) -> str:
    parts = " ".join(str(value or "").strip().split()).split()
    if len(parts) == 6:
        return " ".join(parts)
    if len(parts) == 4:
        return " ".join([*parts, "0", "1"])
    if len(parts) == 2:
        return " ".join([parts[0], parts[1], "-", "-", "0", "1"])
    return ""


def _canonical_pgn(
    records: list[dict[str, Any]],
    lattice_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    lattice_by_id = _rows_by_id(lattice_rows, "record_id")
    items: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        record_id = str(record.get("record_id") or record.get("id") or f"pgn_{index:04d}")
        pgn = str(record.get("pgn") or record.get("annotated_pgn") or "").strip()
        lattice = lattice_by_id.get(record_id) or {}
        replay = _pgn_replay_status(pgn)
        source_status = str(record.get("status") or "requires_review")
        accepted = source_status == "accepted" and replay["valid"]
        status = "PGN_AUTO_ACCEPTED" if accepted else _review_pgn_status(pgn, replay)
        validation_errors = [] if accepted else _pgn_errors(record, lattice, replay)
        item = {
            "id": record_id,
            "page": int(record.get("page") or record.get("source_page") or 0),
            "status": status,
            "candidate_values": [
                {
                    "source": "source_pgn",
                    "value": pgn,
                    "deterministic_valid": replay["valid"],
                    "first_error": replay.get("error"),
                }
            ]
            if pgn
            else [],
            "selected_value": pgn if accepted else None,
            "validation_errors": validation_errors,
            "repair_attempts": [],
            "next_action": "export_allowed" if accepted else "manual_review_or_mapping",
        }
        items.append(item)
        if not accepted and (record.get("raw_text") or record.get("movetext")):
            repairs.append(
                {
                    "kind": "pgn",
                    "id": record_id,
                    "input": str(record.get("raw_text") or record.get("movetext") or "")[:500],
                    "output": "",
                    "applied": False,
                    "reason": "requires lattice/glyph mapping and legal replay before export",
                }
            )
    summary = _status_summary(items, accepted=PGN_ACCEPTED_STATUSES)
    payload = {"schema": "kindlemaster.auto_chess.pgn_candidates.v1", "items": items, "summary": summary}
    validation = {"schema": "kindlemaster.auto_chess.pgn_validation.v1", "items": items, "summary": summary}
    return payload, validation, repairs


def _review_pgn_status(pgn: str, replay: dict[str, Any]) -> str:
    if pgn and replay.get("parsed"):
        return "PGN_PARSED"
    if pgn:
        return "PGN_CANDIDATE"
    return "PGN_FAILED"


def _pgn_errors(record: dict[str, Any], lattice: dict[str, Any], replay: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for warning in record.get("warnings") or lattice.get("warnings") or []:
        errors.append({"code": str(warning), "message": "Source PGN warning blocks strict export."})
    if not replay.get("valid"):
        errors.append({"code": "pgn_replay_failed", "message": replay.get("error") or "PGN parser/replay failed."})
    if lattice.get("unmapped_tokens"):
        errors.append({"code": "unmapped_ocr_tokens", "message": "OCR/glyph tokens require accepted mapping."})
    return errors or [{"code": "pgn_requires_review", "message": "PGN record is not accepted by strict replay gate."}]


def _pgn_replay_status(pgn_text: str) -> dict[str, Any]:
    if not pgn_text.strip():
        return {"parsed": False, "valid": False, "error": "empty_pgn"}
    try:
        import chess.pgn  # type: ignore

        game = chess.pgn.read_game(StringIO(pgn_text))
        if game is None:
            return {"parsed": False, "valid": False, "error": "pgn_parser_returned_none"}
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
        return {"parsed": True, "valid": True, "error": ""}
    except Exception as exc:
        return {"parsed": False, "valid": False, "error": f"{exc.__class__.__name__}: {exc}"}


def _auto_summary(
    dashboard: dict[str, Any],
    *,
    fen_validation: dict[str, Any],
    pgn_validation: dict[str, Any],
    repair_payload: dict[str, Any],
) -> dict[str, Any]:
    fen_summary = fen_validation.get("summary") or {}
    pgn_summary = pgn_validation.get("summary") or {}
    return {
        "pages": int(dashboard.get("pages") or 0),
        "diagrams_total": int(dashboard.get("diagrams_total") or fen_summary.get("total") or 0),
        "fen_accepted": int(fen_summary.get("accepted") or dashboard.get("fen_accepted") or 0),
        "fen_failed": int(fen_summary.get("failed") or 0),
        "pgn_total": int(dashboard.get("pgn_total") or pgn_summary.get("total") or 0),
        "accepted_pgn": int(pgn_summary.get("accepted") or dashboard.get("accepted_pgn") or 0),
        "pgn_failed": int(pgn_summary.get("failed") or 0),
        "repair_attempts": int((repair_payload.get("summary") or {}).get("attempted") or 0),
        "repairs_applied": int((repair_payload.get("summary") or {}).get("applied") or 0),
        "manual_review_items": int(fen_summary.get("failed") or 0) + int(pgn_summary.get("failed") or 0),
        "ai_fen_candidates": int(dashboard.get("ai_fen_candidates") or 0),
        "ai_pgn_candidates": int(dashboard.get("ai_pgn_candidates") or 0),
    }


def _pipeline_status(summary: dict[str, Any], *, mode: str) -> str:
    if int(summary.get("manual_review_items") or 0) > 0:
        return "AUTO_FAILED_WITH_REASON" if mode == "auto-strict" else "MANUAL_REVIEW_AVAILABLE"
    if int(summary.get("repairs_applied") or 0) > 0:
        return "AUTO_SUCCESS_WITH_REPAIRS"
    return "AUTO_SUCCESS"


def _quality_report(
    out_dir: Path,
    *,
    status: str,
    mode: str,
    source_pdf: str | Path | None,
    source_html: str | Path | None,
    summary: dict[str, Any],
    stage_payload: dict[str, Any],
    ai_payloads: dict[str, Any],
) -> dict[str, Any]:
    blockers = []
    if int(summary.get("fen_failed") or 0):
        blockers.append({"code": "fen_items_require_review", "count": int(summary.get("fen_failed") or 0)})
    if int(summary.get("pgn_failed") or 0):
        blockers.append({"code": "pgn_items_require_review", "count": int(summary.get("pgn_failed") or 0)})
    return {
        "schema": "kindlemaster.auto_chess_quality_report.v1",
        "status": status,
        "mode": mode,
        "source_pdf": str(source_pdf or ""),
        "source_html": str(source_html or ""),
        "summary": summary,
        "blockers": blockers,
        "stage_status": stage_payload.get("status") or stage_payload.get("overall_status") or "unknown",
        "ai_policy": "AI candidates are review-only and never directly accepted.",
        "ai_payloads": ai_payloads,
        "exports": {
            "games_pgn": str(out_dir / "export" / "games.pgn"),
            "html": str(out_dir / "index.html"),
        },
        "next_action": _next_action(status, blockers),
    }


def _next_action(status: str, blockers: list[dict[str, Any]]) -> str:
    if status == "AUTO_SUCCESS":
        return "export_ready"
    if status == "AUTO_SUCCESS_WITH_REPAIRS":
        return "review_repair_report_then_export"
    if blockers:
        return "open review artifacts and resolve listed FEN/PGN blockers"
    return "inspect quality report"


def _failed_process_payload(
    out_dir: Path,
    *,
    mode: str,
    source_pdf: Path,
    source_html: Path | None,
    error: str,
) -> dict[str, Any]:
    report_dir = out_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "kindlemaster.auto_chess_flow.v1",
        "status": "AUTO_FAILED_WITH_REASON",
        "mode": mode,
        "out_dir": str(out_dir),
        "source_pdf": str(source_pdf),
        "source_html": str(source_html or ""),
        "summary": {},
        "error": error,
        "strict_failed": True,
    }
    _write_json(out_dir / "auto_chess_flow.json", payload)
    _write_json(report_dir / "quality_report.json", payload)
    return payload


def _extract_diagrams(book: dict[str, Any], diagrams_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(diagrams_payload.get("diagrams"), list):
        return list(diagrams_payload.get("diagrams") or [])
    diagrams: list[dict[str, Any]] = []
    for page in book.get("pages") or []:
        page_number = int(page.get("page") or 0)
        for diagram in page.get("diagrams") or []:
            if isinstance(diagram, dict):
                diagrams.append({**diagram, "page": int(diagram.get("page") or page_number)})
    return diagrams


def _rows_by_id(rows: Iterable[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(field) or row.get("id") or "")
        if key:
            indexed[key] = row
    return indexed


def _status_summary(items: list[dict[str, Any]], *, accepted: set[str]) -> dict[str, Any]:
    return {
        "total": len(items),
        "accepted": len([item for item in items if item.get("status") in accepted]),
        "failed": len([item for item in items if item.get("status") not in accepted]),
        "by_status": {
            status: len([item for item in items if item.get("status") == status])
            for status in sorted({str(item.get("status") or "") for item in items})
            if status
        },
    }


def _ensure_auto_dirs(out: Path) -> dict[str, Path]:
    names = ["pages", "layout", "text", "diagrams", "fen", "pgn", "repair", "report", "export"]
    dirs = {name: out / name for name in names}
    for directory in dirs.values():
        directory.mkdir(parents=True, exist_ok=True)
    return dirs


def _load_or_build_dashboard(out: Path) -> dict[str, Any]:
    dashboard = _read_optional_json(out / "reports" / "chess_quality_dashboard.json")
    if dashboard:
        return dashboard
    try:
        from chess_study_export import build_chess_quality_dashboard

        return build_chess_quality_dashboard(out)
    except Exception:
        return {}


def _copy_export_files(out: Path, export_dir: Path) -> None:
    source_pgn = out / "data" / "games.pgn"
    target_pgn = export_dir / "games.pgn"
    if source_pgn.is_file():
        shutil.copyfile(source_pgn, target_pgn)
    else:
        target_pgn.write_text("", encoding="utf-8")
    source_html = out / "index.html"
    if source_html.is_file():
        shutil.copyfile(source_html, export_dir / "index.html")


def _quality_report_html(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    blockers = report.get("blockers") or []
    blocker_rows = "".join(
        f"<li><code>{html.escape(str(item.get('code')))}</code>: {html.escape(str(item.get('count')))}</li>"
        for item in blockers
    ) or "<li>No blockers.</li>"
    tiles = "".join(
        f"<div class='tile'><strong>{html.escape(label)}</strong><span>{html.escape(str(value))}</span></div>"
        for label, value in [
            ("Status", report.get("status")),
            ("FEN accepted", summary.get("fen_accepted")),
            ("FEN failed", summary.get("fen_failed")),
            ("PGN accepted", summary.get("accepted_pgn")),
            ("PGN failed", summary.get("pgn_failed")),
            ("Repairs attempted", summary.get("repair_attempts")),
        ]
    )
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>KindleMaster Auto Chess Quality Report</title>
<style>
body{{font-family:Georgia,serif;margin:2rem;background:#f7f1e8;color:#21180f}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1rem;max-width:960px}}
.tile{{background:#fff;border:1px solid #dccbb4;border-radius:14px;padding:1rem;box-shadow:0 10px 24px rgba(62,39,16,.08)}}
.tile strong{{display:block;font-size:.78rem;text-transform:uppercase;color:#7a5631}}
.tile span{{display:block;font:700 1.5rem ui-monospace,monospace;margin-top:.35rem}}
code{{background:#efe2d1;border-radius:6px;padding:.1rem .25rem}}
</style>
<h1>KindleMaster Auto Chess Quality Report</h1>
<p>{html.escape(str(report.get("next_action") or ""))}</p>
<section class="grid">{tiles}</section>
<h2>Blockers</h2>
<ul>{blocker_rows}</ul>
<h2>Policy</h2>
<p>{html.escape(str(report.get("ai_policy") or ""))}</p>
</html>"""


def _review_index_html(items: list[dict[str, Any]]) -> str:
    rows = "".join(
        "<li>"
        + (f"<a href='{html.escape(Path(item['path']).name)}'>{html.escape(str(item['label']))}</a>" if item.get("exists") else html.escape(str(item.get("label"))))
        + (" <span>available</span>" if item.get("exists") else " <span>missing</span>")
        + "</li>"
        for item in items
    )
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Chess Review Index</title>
<body><h1>Chess Review Index</h1><ul>{rows}</ul></body></html>"""


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
