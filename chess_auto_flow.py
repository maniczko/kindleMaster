from __future__ import annotations

import html
import importlib
import json
import re
import shutil
import time
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

from chess_fen_hardening import machine_accept_fen, validate_fen_detailed

PIPELINE_STATUSES = {
    "AUTO_SUCCESS",
    "AUTO_SUCCESS_WITH_REPAIRS",
    "AUTO_FAILED_WITH_REASON",
    "MANUAL_REVIEW_AVAILABLE",
}
PYTHON_CHESS_BLOCKER = "python_chess_unavailable"


def check_python_chess_available() -> dict[str, Any]:
    """Return an explicit dependency status for strict chess automation."""
    try:
        importlib.import_module("chess")
    except Exception as error:
        return _missing_python_chess_payload(
            error_code="python_chess_missing",
            detail=str(error) or error.__class__.__name__,
            chess_available=False,
            chess_pgn_available=False,
        )
    try:
        importlib.import_module("chess.pgn")
    except Exception as error:
        return _missing_python_chess_payload(
            error_code="python_chess_pgn_missing",
            detail=str(error) or error.__class__.__name__,
            chess_available=True,
            chess_pgn_available=False,
        )
    return {
        "status": "available",
        "available": True,
        "chess_available": True,
        "chess_pgn_available": True,
        "error_code": "",
        "missing_requirements": [],
        "blockers": [],
        "warnings": [],
        "manual_review_required": False,
    }


def strict_python_chess_preflight() -> dict[str, Any]:
    availability = check_python_chess_available()
    if availability.get("available"):
        return {
            "status": "ok",
            "python_chess": availability,
            "blockers": [],
            "missing_requirements": [],
        }
    return {
        "status": "failed",
        "error_code": availability.get("error_code") or "python_chess_missing",
        "python_chess": availability,
        "blockers": list(availability.get("blockers") or [PYTHON_CHESS_BLOCKER]),
        "missing_requirements": list(availability.get("missing_requirements") or ["python-chess"]),
        "notes": [
            "Strict chess automation requires python-chess and chess.pgn for deterministic PGN replay.",
        ],
    }


def non_strict_python_chess_notice() -> dict[str, Any]:
    availability = check_python_chess_available()
    if availability.get("available"):
        return {
            "status": "ok",
            "python_chess": availability,
            "warnings": [],
            "manual_review_required": False,
        }
    return {
        "status": "degraded",
        "python_chess": availability,
        "warnings": list(availability.get("warnings") or [PYTHON_CHESS_BLOCKER]),
        "manual_review_required": True,
        "notes": [
            "Non-strict chess automation may continue, but PGN/FEN proof must remain manual review.",
        ],
    }

FEN_ACCEPTED_STATUSES = {
    "FEN_AUTO_ACCEPTED",
    "FEN_AUTO_REPAIRED",
    "FEN_MACHINE_ACCEPTED",
    "FEN_MACHINE_REPAIRED",
    "FEN_CORPUS_VERIFIED",
}
PGN_ACCEPTED_STATUSES = {
    "PGN_AUTO_ACCEPTED",
    "PGN_AUTO_REPAIRED",
    "PGN_MACHINE_ACCEPTED",
    "PGN_MACHINE_REPAIRED",
    "SOLUTION_LINE_ACCEPTED",
}
FEN_RUNTIME_ACCEPTED_STATUSES = {"FEN_MACHINE_ACCEPTED", "FEN_MACHINE_REPAIRED", "FEN_CORPUS_VERIFIED"}
PGN_RUNTIME_ACCEPTED_STATUSES = {"PGN_MACHINE_ACCEPTED", "PGN_MACHINE_REPAIRED", "SOLUTION_LINE_ACCEPTED"}


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
    chess_fen_recognition_max_diagrams: str | int = "all",
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
        build_chess_pgn_review,
        build_chess_quality_dashboard,
        evaluate_fen_ensemble,
        preprocess_chess_board_crops,
        recognize_fen_local,
        render_semantic_source_reader,
        run_chess_study_export,
    )
    from chess_fen_ml_acceptance import build_fen_beam_candidates, build_runtime_template_candidates
    from chess_pgn_auto_repair import repair_and_accept_pgn_records

    out = Path(out_dir)
    pdf = Path(pdf_path)
    source_html = Path(html_path) if html_path else None
    stages: list[dict[str, Any]] = []
    try:
        stage_payload = _run_auto_stage(
            "run_chess_study_export",
            lambda: run_chess_study_export(
                pdf,
                html_path=source_html,
                out_dir=out,
                quality_profile=quality_profile,
                render_pages=render_pages,
                diagram_page_ranges=diagram_page_ranges,
                glyph_mapping_file=glyph_mapping_file,
            ),
            stages,
        )
        _run_auto_stage("preprocess_chess_board_crops", lambda: preprocess_chess_board_crops(out), stages)
        _run_auto_stage("recognize_fen_local", lambda: recognize_fen_local(out), stages)
        _run_auto_stage("evaluate_fen_ensemble", lambda: evaluate_fen_ensemble(out), stages)
        _run_auto_stage("generate_fen_template_candidates", lambda: build_runtime_template_candidates(out), stages)
        _run_auto_stage("generate_fen_beam_candidates", lambda: build_fen_beam_candidates(out), stages)
        _run_auto_stage(
            "build_auto_chess_flow_artifacts_before_apply",
            lambda: build_auto_chess_flow_artifacts(
                out,
                mode=mode,
                source_pdf=pdf,
                source_html=source_html,
                stage_payload={"stages": stages},
                ai_payloads={},
                chess_fen_recognition_max_diagrams=chess_fen_recognition_max_diagrams,
            ),
            stages,
        )
        _run_auto_stage("apply_runtime_accepted_fen", lambda: apply_runtime_accepted_fen(out), stages)
        _run_auto_stage("build_chess_pgn_review", lambda: build_chess_pgn_review(out, glyph_mapping_file=glyph_mapping_file), stages)
        _run_auto_stage("repair_and_accept_pgn_records", lambda: repair_and_accept_pgn_records(out), stages)
        _run_auto_stage("apply_runtime_accepted_pgn", lambda: apply_runtime_accepted_pgn(out), stages)
        _run_auto_stage("rebuild_semantic_export", lambda: render_semantic_source_reader(out), stages)
        _run_auto_stage("build_chess_quality_dashboard", lambda: build_chess_quality_dashboard(out), stages)
        ai_payloads: dict[str, Any] = {}
        if with_ai:
            ai_payloads["fen"] = _run_auto_stage(
                "build_ai_fen_candidates",
                lambda: build_ai_fen_candidates(out, limit=ai_limit, dry_run=dry_run_ai),
                stages,
            )
            ai_payloads["pgn"] = _run_auto_stage(
                "build_ai_pgn_candidates",
                lambda: build_ai_pgn_candidates(
                    out,
                    glyph_mapping_file=Path(glyph_mapping_file) if glyph_mapping_file else None,
                    limit=ai_pgn_limit,
                    dry_run=dry_run_ai,
                ),
                stages,
            )
            ai_payloads["quality"] = _run_auto_stage("build_ai_assisted_quality_eval", lambda: build_ai_assisted_quality_eval(out), stages)
        payload = build_auto_chess_flow_artifacts(
            out,
            mode=mode,
            source_pdf=pdf,
            source_html=source_html,
            stage_payload={"initial_export": stage_payload, "stages": stages},
            ai_payloads=ai_payloads,
            chess_fen_recognition_max_diagrams=chess_fen_recognition_max_diagrams,
        )
        payload["stage_results"] = stages
        _run_auto_stage("validate_auto_chess_output", lambda: validate_auto_chess_output(out, strict=mode == "auto-strict"), stages)
        payload["stage_results"] = stages
        _write_json(out / "auto_chess_flow.json", payload)
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


def _run_auto_stage(name: str, fn: Any, stages: list[dict[str, Any]]) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = fn() or {}
        if not isinstance(payload, dict):
            payload = {"result": payload}
        elapsed = round(time.perf_counter() - started, 4)
        stage = {
            "name": name,
            "status": payload.get("status") or "ok",
            "counts": _stage_counts(payload),
            "applied_count": int(payload.get("applied_count") or payload.get("accepted_fen_changed") or payload.get("accepted_pgn_changed") or 0),
            "failure_reasons": _stage_failure_reasons(payload),
            "output_artifacts": _stage_artifacts(payload),
            "elapsed_seconds": elapsed,
        }
        stages.append(stage)
        return payload
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 4)
        stage = {
            "name": name,
            "status": "failed",
            "counts": {},
            "applied_count": 0,
            "failure_reasons": [f"{exc.__class__.__name__}: {exc}"],
            "output_artifacts": {},
            "elapsed_seconds": elapsed,
        }
        stages.append(stage)
        return {"status": "failed", "error": stage["failure_reasons"][0]}


def build_auto_chess_flow_artifacts(
    out_dir: str | Path,
    *,
    mode: str = "auto",
    source_pdf: str | Path | None = None,
    source_html: str | Path | None = None,
    stage_payload: dict[str, Any] | None = None,
    ai_payloads: dict[str, Any] | None = None,
    chess_fen_recognition_max_diagrams: str | int = "all",
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
    beam_fen_rows = _read_jsonl(out / "review" / "fen_beam_candidates.jsonl")
    ensemble_eval = _read_optional_json(out / "reports" / "fen_ensemble_eval.json")
    pgn_lattice_rows = _read_jsonl(out / "review" / "pgn_lattice_review.jsonl")

    page_payload = _canonical_pages(pages)
    layout_payload = _canonical_layout(pages)
    text_rows = _canonical_text_rows(pages)
    diagram_payload = {"schema": "kindlemaster.auto_chess.diagrams.v1", "diagrams": diagrams}
    fen_payload, fen_validation, fen_repairs = _canonical_fen(
        diagrams,
        ai_fen_rows,
        model_fen_rows,
        beam_fen_rows,
        ensemble_eval,
        max_diagrams=chess_fen_recognition_max_diagrams,
    )
    accepted_fen_by_source = _accepted_fen_by_source(diagrams, fen_payload)
    pgn_payload, pgn_validation, pgn_repairs = _canonical_pgn(
        pgn_records,
        pgn_lattice_rows,
        accepted_fen_by_source=accepted_fen_by_source,
    )
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
    acceptance_blockers = _acceptance_blockers_report(fen_payload, pgn_payload)
    status = _pipeline_status(summary, mode=mode)
    report = _quality_report(
        out,
        status=status,
        mode=mode,
        source_pdf=source_pdf,
        source_html=source_html,
        summary=summary,
        acceptance_blockers=acceptance_blockers,
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
    _write_json(dirs["report"] / "acceptance_blockers.json", acceptance_blockers)
    (dirs["report"] / "acceptance_blockers.html").write_text(_acceptance_blockers_html(acceptance_blockers), encoding="utf-8")
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
                "acceptance_blockers": dirs["report"] / "acceptance_blockers.json",
                "acceptance_blockers_html": dirs["report"] / "acceptance_blockers.html",
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
    if not _auto_chess_artifacts_current(out):
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
        "report/acceptance_blockers.json",
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
    if not _auto_chess_artifacts_current(out):
        build_auto_chess_flow_artifacts(out)
    report = _read_optional_json(out / "report" / "quality_report.json")
    if not report or "acceptance_blockers_summary" not in report:
        build_auto_chess_flow_artifacts(out)
        report = _read_optional_json(out / "report" / "quality_report.json")
    return report or _read_optional_json(out / "auto_chess_flow.json")


def review_auto_chess_output(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    review_dir = out / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    if not _auto_chess_artifacts_current(out):
        build_auto_chess_flow_artifacts(out)
    candidates = [
        ("FEN manual review", review_dir / "fen_manual_review.html"),
        ("FEN AI candidate review", review_dir / "fen_ai_candidate_review_batch.html"),
        ("FEN ensemble conflicts", review_dir / "fen_ensemble_conflicts.html"),
        ("PGN lattice review", review_dir / "pgn_lattice_review.csv"),
        ("Glyph mapping review", review_dir / "glyph_mapping_review.html"),
        ("PGN replay blockers", review_dir / "pgn_replay_blockers_top10.md"),
        ("Runtime acceptance blockers", out / "report" / "acceptance_blockers.html"),
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


def apply_runtime_accepted_fen(out_dir: str | Path) -> dict[str, Any]:
    from chess_fen_ml_acceptance import apply_runtime_accepted_fen as _apply

    return _apply(out_dir)


def apply_runtime_accepted_pgn(out_dir: str | Path) -> dict[str, Any]:
    from chess_pgn_auto_repair import apply_runtime_accepted_pgn as _apply

    payload = _apply(out_dir)
    try:
        from chess_study_export import build_chess_quality_dashboard, render_semantic_source_reader

        render_semantic_source_reader(Path(out_dir))
        build_chess_quality_dashboard(Path(out_dir))
    except Exception:
        pass
    return payload


def is_auto_chess_output(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_dir() and (
        (candidate / "auto_chess_flow.json").is_file()
        or (candidate / "data" / "book.json").is_file()
        or (candidate / "reports" / "chess_quality_dashboard.json").is_file()
    )


def _apply_fen_to_diagram(diagram: dict[str, Any], accepted: dict[str, dict[str, Any]]) -> bool:
    diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
    item = accepted.get(diagram_id)
    if not item:
        return False
    selected = str(item.get("selected_value") or "").strip()
    if not selected:
        return False
    diagram["fen"] = selected
    diagram["fen_candidate"] = selected
    diagram["validation_status"] = "accepted"
    diagram["status"] = "accepted"
    diagram["runtime_status"] = item.get("runtime_status") or "FEN_MACHINE_ACCEPTED"
    diagram["review_reason"] = ""
    diagram["acceptance_trace"] = item.get("acceptance_trace") or {}
    return True


def _stage_counts(payload: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "pages",
        "diagrams_total",
        "diagram_count",
        "prediction_count",
        "candidate_count",
        "accepted_count",
        "accepted_pgn",
        "pgn_total",
        "record_count",
        "applied_count",
        "machine_accepted_candidate_count",
    ]
    counts: dict[str, Any] = {}
    for key in keys:
        if key in payload:
            counts[key] = payload.get(key)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key in keys:
            if key in summary and key not in counts:
                counts[key] = summary.get(key)
    return counts


def _stage_failure_reasons(payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key in ["error", "reason"]:
        if payload.get(key):
            reasons.append(str(payload.get(key)))
    for key in ["top_blockers", "top_conflict_reasons"]:
        for row in payload.get(key) or []:
            label = row.get("key") or row.get("code")
            if label:
                reasons.append(str(label))
    return reasons[:20]


def _stage_artifacts(payload: dict[str, Any]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key, value in payload.items():
        if key.endswith("_path") or key.endswith("_html") or key.endswith("_dir") or key in {"index_html", "predictions_path", "data_path"}:
            artifacts[key] = str(value)
    if isinstance(payload.get("artifacts"), dict):
        artifacts.update({str(k): str(v) for k, v in payload["artifacts"].items()})
    return artifacts


def _auto_chess_artifacts_current(out: Path) -> bool:
    report = _read_optional_json(out / "report" / "quality_report.json")
    if not (out / "auto_chess_flow.json").is_file() or not report:
        return False
    if "acceptance_blockers_summary" not in report:
        return False
    if not (out / "report" / "acceptance_blockers.json").is_file():
        return False
    return True


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
    beam_rows: list[dict[str, Any]] | None = None,
    ensemble_eval: dict[str, Any] | None = None,
    *,
    max_diagrams: str | int = "all",
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ai_by_id = _rows_by_id(ai_rows, "diagram_id")
    model_by_id = _rows_by_id(model_rows, "diagram_id")
    beam_by_id = _rows_by_id(beam_rows or [], "diagram_id")
    ensemble_by_id = _rows_by_id(list((ensemble_eval or {}).get("accepted_candidates") or []), "diagram_id")
    candidates: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    max_count = _parse_max_diagrams(max_diagrams)
    for diagram_index, diagram in enumerate(diagrams, start=1):
        diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
        if max_count > 0 and diagram_index > max_count:
            selected = {
                "status": "FEN_REVIEW_REQUIRED",
                "runtime_status": "FEN_REVIEW_REQUIRED",
                "corpus_status": "not_corpus_verified",
                "acceptance_policy": "runtime_machine_acceptance_v1",
                "selected_value": None,
                "validation_errors": [{"code": "fen_recognition_limit_skipped", "message": "Diagram skipped by configured runtime FEN recognition limit."}],
                "acceptance_blockers": [{"code": "fen_recognition_limit_skipped", "message": "Diagram skipped by configured runtime FEN recognition limit."}],
                "acceptance_trace": {"diagram_index": diagram_index, "max_diagrams": max_count},
                "next_action": "increase_chess_fen_recognition_max_diagrams_or_review_manually",
            }
            candidates.append(
                {
                    "id": diagram_id,
                    "page": int(diagram.get("page") or 0),
                    "source_image_path": diagram.get("image_path") or diagram.get("crop_path") or "",
                    "status": selected["status"],
                    "runtime_status": selected["runtime_status"],
                    "corpus_status": selected["corpus_status"],
                    "acceptance_policy": selected["acceptance_policy"],
                    "candidate_values": [],
                    "selected_value": None,
                "validation_errors": selected["validation_errors"],
                "acceptance_blockers": selected["acceptance_blockers"],
                "acceptance_trace": selected["acceptance_trace"],
                "fen_semantic_status": _selected_fen_semantic_status(selected, []),
                "repair_attempts": [],
                "next_action": selected["next_action"],
            }
            )
            validation_rows.append(
                {
                    k: candidates[-1][k]
                    for k in [
                        "id",
                        "page",
                        "status",
                        "runtime_status",
                        "corpus_status",
                        "validation_errors",
                        "acceptance_blockers",
                        "next_action",
                    ]
                }
            )
            continue
        raw_candidates = _fen_raw_candidates(
            diagram,
            ai_by_id.get(diagram_id),
            model_by_id.get(diagram_id),
            beam_by_id.get(diagram_id),
            ensemble_by_id.get(diagram_id),
        )
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
                "runtime_status": selected["runtime_status"],
                "corpus_status": selected["corpus_status"],
                "acceptance_policy": selected["acceptance_policy"],
                "candidate_values": candidate_rows,
                "selected_value": selected.get("selected_value"),
                "validation_errors": selected.get("validation_errors", []),
                "acceptance_blockers": selected.get("acceptance_blockers", []),
                "acceptance_trace": selected.get("acceptance_trace", {}),
                "fen_semantic_status": _selected_fen_semantic_status(selected, candidate_rows),
                "repair_attempts": repair_rows,
                "next_action": selected["next_action"],
            }
        )
        validation_rows.append(
            {
                k: candidates[-1][k]
                for k in [
                    "id",
                    "page",
                    "status",
                    "runtime_status",
                    "corpus_status",
                    "validation_errors",
                    "acceptance_blockers",
                    "next_action",
                ]
            }
        )
    summary = _status_summary(candidates, accepted=FEN_ACCEPTED_STATUSES)
    skipped = [item for item in candidates if any(blocker.get("code") == "fen_recognition_limit_skipped" for blocker in item.get("acceptance_blockers") or [])]
    summary["recognition_limit"] = "all" if max_count <= 0 else max_count
    summary["skipped_diagram_count"] = len(skipped)
    summary["skipped_diagram_ids"] = [item.get("id") for item in skipped]
    summary["fen_semantic_status_counts"] = {
        status: len([item for item in candidates if item.get("fen_semantic_status") == status])
        for status in sorted({str(item.get("fen_semantic_status") or "") for item in candidates})
        if status
    }
    payload = {"schema": "kindlemaster.auto_chess.fen_candidates.v1", "items": candidates, "summary": summary}
    validation = {"schema": "kindlemaster.auto_chess.fen_validation.v1", "items": validation_rows, "summary": summary}
    return payload, validation, repairs


def _fen_raw_candidates(
    diagram: dict[str, Any],
    ai_row: dict[str, Any] | None,
    model_row: dict[str, Any] | None,
    beam_row: dict[str, Any] | None = None,
    ensemble_row: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, source in [("fen", "deterministic"), ("fen_candidate", "deterministic_candidate")]:
        fen = str(diagram.get(key) or "").strip()
        if fen:
            rows.append(
                {
                    "source": source,
                    "fen": fen,
                    "authoritative": source == "deterministic",
                    "confidence": _first_float(
                        diagram.get("confidence"),
                        diagram.get("fen_confidence"),
                        diagram.get("candidate_confidence"),
                    ),
                    "warnings": _string_list(
                        diagram.get("warnings"),
                        diagram.get("fen_warnings"),
                        diagram.get("review_warnings"),
                    ),
                    "method": diagram.get("method") or diagram.get("recognition_method") or source,
                    "squares": diagram.get("squares") or [],
                }
            )
    if ai_row:
        fen = str(ai_row.get("ai_fen_candidate") or ai_row.get("fen") or "").strip()
        if fen:
            rows.append(
                {
                    "source": "ai_review_only",
                    "fen": fen,
                    "authoritative": False,
                    "confidence": _first_float(ai_row.get("confidence"), ai_row.get("ai_confidence")),
                    "warnings": _string_list(ai_row.get("warnings"), ai_row.get("issues")),
                    "method": "ai_review_only",
                }
            )
    for deterministic_row in [beam_row, ensemble_row]:
        if deterministic_row:
            fen = str(
                deterministic_row.get("fen")
                or deterministic_row.get("fen_candidate")
                or deterministic_row.get("selected_value")
                or ""
            ).strip()
            if fen:
                evidence = deterministic_row.get("evidence") or deterministic_row.get("acceptance_trace") or {}
                rows.append(
                    {
                        "source": "deterministic_ensemble",
                        "fen": fen,
                        "authoritative": False,
                        "confidence": _first_float(
                            deterministic_row.get("confidence"),
                            deterministic_row.get("global_confidence"),
                            deterministic_row.get("score"),
                        ),
                        "warnings": _string_list(deterministic_row.get("warnings")),
                        "method": "deterministic_ensemble",
                        "squares": deterministic_row.get("squares") or [],
                        "evidence": evidence,
                        "source_crop_hash": deterministic_row.get("source_crop_hash") or evidence.get("source_crop_hash") or "",
                        "score_margin_to_second_candidate": deterministic_row.get("score_margin_to_second_candidate")
                        or evidence.get("score_margin_to_second_candidate"),
                        "changed_squares": deterministic_row.get("changed_squares") or [],
                    }
                )
    if model_row:
        fen = str(model_row.get("fen") or model_row.get("predicted_fen") or model_row.get("fen_candidate") or "").strip()
        if fen:
            rows.append(
                {
                    "source": "local_model_candidate",
                    "fen": fen,
                    "authoritative": False,
                    "confidence": _first_float(
                        model_row.get("confidence"),
                        model_row.get("global_confidence"),
                        model_row.get("score"),
                    ),
                    "warnings": _string_list(model_row.get("warnings")),
                    "method": model_row.get("method") or "local_model_candidate",
                    "squares": model_row.get("squares") or [],
                }
            )
    return rows


def _fen_candidate_row(candidate: dict[str, Any]) -> dict[str, Any]:
    validation = validate_fen_detailed(str(candidate.get("fen") or ""))
    machine = machine_accept_fen(candidate)
    return {
        "source": candidate.get("source") or "unknown",
        "value": candidate.get("fen") or "",
        "authoritative": bool(candidate.get("authoritative")),
        "confidence": _first_float(candidate.get("confidence")),
        "method": candidate.get("method") or candidate.get("source") or "unknown",
        "warnings": list(candidate.get("warnings") or []),
        "deterministic_valid": validation.is_legal_position and not validation.errors,
        "normalized_value": validation.normalized_fen,
        "errors": [asdict(error) for error in validation.errors],
        "validation_warnings": [asdict(warning) for warning in validation.warnings],
        "runtime_status": machine["runtime_status"],
        "fen_semantic_status": machine.get("fen_semantic_status"),
        "acceptance_policy": machine["acceptance_policy"],
        "acceptance_blockers": machine["acceptance_blockers"],
        "acceptance_trace": machine["acceptance_trace"],
    }


def _select_fen_status(diagram: dict[str, Any], candidate_rows: list[dict[str, Any]]) -> dict[str, Any]:
    deterministic = next((row for row in candidate_rows if row.get("source") == "deterministic"), None)
    human_verified = _is_human_verified_record(diagram)
    if human_verified and deterministic and deterministic.get("deterministic_valid"):
        return {
            "status": "FEN_CORPUS_VERIFIED",
            "runtime_status": "FEN_CORPUS_VERIFIED",
            "corpus_status": "corpus_verified",
            "acceptance_policy": "human_verified_exact_crop_label",
            "selected_value": deterministic.get("normalized_value"),
            "validation_errors": [],
            "acceptance_blockers": [],
            "acceptance_trace": {"source": deterministic.get("source"), "human_verified": True},
            "next_action": "export_allowed",
        }
    machine = next((row for row in candidate_rows if row.get("runtime_status") == "FEN_MACHINE_ACCEPTED"), None)
    if machine:
        return {
            "status": "FEN_MACHINE_ACCEPTED",
            "runtime_status": "FEN_MACHINE_ACCEPTED",
            "corpus_status": "not_corpus_verified",
            "acceptance_policy": "runtime_machine_acceptance_v1",
            "selected_value": machine.get("normalized_value"),
            "validation_errors": [],
            "acceptance_blockers": [],
            "acceptance_trace": machine.get("acceptance_trace") or {},
            "next_action": "export_allowed_runtime_machine",
        }
    if not candidate_rows:
        return {
            "status": "FEN_FAILED",
            "runtime_status": "FEN_FAILED",
            "corpus_status": "not_corpus_verified",
            "acceptance_policy": "runtime_machine_acceptance_v1",
            "selected_value": None,
            "validation_errors": [{"code": "fen_not_recognized", "message": "No FEN candidate was available."}],
            "acceptance_blockers": [{"code": "fen_not_recognized", "message": "No FEN candidate was available."}],
            "acceptance_trace": {},
            "next_action": "manual_review",
        }
    errors: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for row in candidate_rows:
        for error in row.get("errors") or []:
            errors.append({"source": row.get("source"), **error})
        for blocker in row.get("acceptance_blockers") or []:
            blockers.append({"source": row.get("source"), **blocker})
    if any(row.get("deterministic_valid") for row in candidate_rows):
        return {
            "status": "FEN_MACHINE_VALID",
            "runtime_status": "FEN_MACHINE_VALID",
            "corpus_status": "not_corpus_verified",
            "acceptance_policy": "runtime_machine_acceptance_v1",
            "selected_value": None,
            "validation_errors": errors,
            "acceptance_blockers": blockers or [{"code": "machine_acceptance_not_proven", "message": "A valid FEN exists, but runtime machine gate did not accept it."}],
            "acceptance_trace": {"candidate_count": len(candidate_rows)},
            "next_action": "resolve_machine_acceptance_blockers_or_human_verify",
        }
    return {
        "status": "FEN_FAILED",
        "runtime_status": "FEN_FAILED",
        "corpus_status": "not_corpus_verified",
        "acceptance_policy": "runtime_machine_acceptance_v1",
        "selected_value": None,
        "validation_errors": errors or [{"code": "fen_validation_failed", "message": "No candidate passed deterministic FEN validation."}],
        "acceptance_blockers": blockers or [{"code": "fen_validation_failed", "message": "No candidate passed deterministic FEN validation."}],
        "acceptance_trace": {"candidate_count": len(candidate_rows)},
        "next_action": "manual_review",
    }


def _selected_fen_semantic_status(selected: dict[str, Any], candidate_rows: list[dict[str, Any]]) -> str:
    trace = selected.get("acceptance_trace") if isinstance(selected.get("acceptance_trace"), dict) else {}
    from_trace = str(trace.get("fen_semantic_status") or "").strip()
    if from_trace:
        return from_trace
    selected_value = str(selected.get("selected_value") or "").strip()
    for row in candidate_rows:
        if selected_value and selected_value == str(row.get("normalized_value") or row.get("value") or "").strip():
            return str(row.get("fen_semantic_status") or "").strip()
    for row in candidate_rows:
        semantic = str(row.get("fen_semantic_status") or "").strip()
        if semantic:
            return semantic
    return ""


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
    *,
    accepted_fen_by_source: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    lattice_by_id = _rows_by_id(lattice_rows, "record_id")
    accepted_fen_by_source = accepted_fen_by_source or {}
    items: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        record_id = str(record.get("record_id") or record.get("id") or f"pgn_{index:04d}")
        pgn = str(record.get("pgn") or record.get("annotated_pgn") or "").strip()
        lattice = lattice_by_id.get(record_id) or {}
        replay = _pgn_replay_status(pgn)
        source_type = _classify_pgn_source(record, pgn, lattice)
        source_fen = _record_source_fen(record, accepted_fen_by_source)
        requires_source_fen = source_type in {"EXERCISE_SOLUTION", "TACTICAL_LINE"}
        source_status = str(record.get("status") or "requires_review")
        feasibility = _pgn_feasibility_for_record(record)
        blocking_errors = _pgn_errors(
            record,
            lattice,
            replay,
            source_type=source_type,
            source_fen=source_fen,
            requires_source_fen=requires_source_fen,
        )
        accepted = bool(replay["valid"] and not blocking_errors and source_status != "rejected")
        if accepted and source_type in {"EXERCISE_SOLUTION", "TACTICAL_LINE"}:
            status = "SOLUTION_LINE_ACCEPTED"
        elif accepted:
            status = "PGN_MACHINE_ACCEPTED"
        else:
            status = _review_pgn_status(pgn, replay, source_type=source_type)
        runtime_status = status
        validation_errors = [] if accepted else blocking_errors
        validation_stage = _pgn_validation_stage(
            pgn=pgn,
            replay=replay,
            blocking_errors=blocking_errors,
            source_fen=source_fen,
            requires_source_fen=requires_source_fen,
        )
        top_blocker = validation_errors[0]["code"] if validation_errors else ""
        item = {
            "id": record_id,
            "page": int(record.get("page") or record.get("source_page") or 0),
            "status": status,
            "runtime_status": runtime_status,
            "corpus_status": "not_corpus_verified",
            "acceptance_policy": "runtime_pgn_replay_acceptance_v1",
            "source_type": source_type,
            "source_fen": source_fen,
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
            "acceptance_blockers": validation_errors,
            "validation_stage": validation_stage,
            "top_blocker": top_blocker,
            "pgn_feasible": bool(feasibility["pgn_feasible"]),
            "pgn_feasibility_reason": feasibility["pgn_feasibility_reason"],
            "pgn_should_count_in_success_rate": bool(feasibility["pgn_should_count_in_success_rate"]),
            "acceptance_trace": {
                "source_status": source_status,
                "source_type": source_type,
                "requires_source_fen": requires_source_fen,
                "source_fen_available": bool(source_fen),
                "replay": replay,
            },
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
    feasible_items = [item for item in items if bool(item.get("pgn_should_count_in_success_rate"))]
    infeasible_items = [item for item in items if not bool(item.get("pgn_should_count_in_success_rate"))]
    summary["failed"] = len([item for item in feasible_items if item.get("status") not in PGN_ACCEPTED_STATUSES])
    summary["review_required"] = len(
        [
            item
            for item in feasible_items
            if item.get("status") not in PGN_ACCEPTED_STATUSES
            and str(item.get("runtime_status") or item.get("status") or "").endswith(("VALID", "REVIEW_REQUIRED", "PARSED", "CANDIDATE"))
        ]
    )
    summary["pgn_feasible_count"] = len(feasible_items)
    summary["pgn_infeasible_count"] = len(infeasible_items)
    summary["pgn_infeasible_reason_counts"] = {
        reason: len([item for item in infeasible_items if item.get("pgn_feasibility_reason") == reason])
        for reason in sorted({str(item.get("pgn_feasibility_reason") or "") for item in infeasible_items})
        if reason
    }
    summary["pgn_success_rate"] = _ratio(int(summary.get("runtime_machine_accepted") or 0), len(feasible_items))
    summary["validation_stage_counts"] = {
        stage: len([item for item in items if item.get("validation_stage") == stage])
        for stage in sorted({str(item.get("validation_stage") or "") for item in items})
        if stage
    }
    summary["top_blocker_counts"] = {
        blocker: len([item for item in items if item.get("top_blocker") == blocker])
        for blocker in sorted({str(item.get("top_blocker") or "") for item in items})
        if blocker
    }
    payload = {"schema": "kindlemaster.auto_chess.pgn_candidates.v1", "items": items, "summary": summary}
    validation = {"schema": "kindlemaster.auto_chess.pgn_validation.v1", "items": items, "summary": summary}
    return payload, validation, repairs


def _pgn_validation_stage(
    *,
    pgn: str,
    replay: dict[str, Any],
    blocking_errors: list[dict[str, Any]],
    source_fen: str,
    requires_source_fen: bool,
) -> str:
    if not pgn:
        return "no_pgn"
    if not replay.get("parsed"):
        return "parse_failed"
    if not replay.get("valid"):
        return "replay_failed"
    if requires_source_fen and not source_fen:
        return "source_fen_missing"
    if blocking_errors:
        return "warning_blocked"
    return "exportable"


def _pgn_feasibility_for_record(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("pgn_feasible"), bool):
        feasible = bool(record.get("pgn_feasible"))
        return {
            "pgn_feasible": feasible,
            "pgn_feasibility_reason": str(record.get("pgn_feasibility_reason") or ("full_game_text" if feasible else "insufficient_text")),
            "pgn_should_count_in_success_rate": bool(record.get("pgn_should_count_in_success_rate", feasible)),
        }
    pgn = str(record.get("pgn") or record.get("annotated_pgn") or "").strip()
    movetext = str(record.get("movetext") or "").strip()
    raw_text = str(record.get("raw_text") or record.get("text") or "").strip()
    combined = "\n".join(part for part in [pgn, movetext, raw_text] if part)
    if not combined:
        return {
            "pgn_feasible": False,
            "pgn_feasibility_reason": "insufficient_text",
            "pgn_should_count_in_success_rate": False,
        }
    has_move_signal = bool(re.search(r"\b\d{1,3}\.(?:\.\.)?\s*\S+", combined))
    has_diagram_only_signal = bool(re.search(r"(?i)^\s*(?:diagram|dia\.?)\s+\d+(?:[-.]\d+)?", combined))
    if has_move_signal or pgn:
        return {
            "pgn_feasible": True,
            "pgn_feasibility_reason": "full_game_text" if not has_diagram_only_signal else "exercise_solution_line",
            "pgn_should_count_in_success_rate": True,
        }
    if has_diagram_only_signal:
        return {
            "pgn_feasible": False,
            "pgn_feasibility_reason": "diagram_only",
            "pgn_should_count_in_success_rate": False,
        }
    return {
        "pgn_feasible": False,
        "pgn_feasibility_reason": "insufficient_movetext",
        "pgn_should_count_in_success_rate": False,
    }


def _review_pgn_status(pgn: str, replay: dict[str, Any], *, source_type: str = "UNKNOWN") -> str:
    if pgn and replay.get("parsed"):
        return "PGN_MACHINE_PARSED"
    if pgn:
        return "PGN_CANDIDATE"
    return "PGN_FAILED"


def _pgn_errors(
    record: dict[str, Any],
    lattice: dict[str, Any],
    replay: dict[str, Any],
    *,
    source_type: str = "UNKNOWN",
    source_fen: str = "",
    requires_source_fen: bool = False,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for warning in record.get("warnings") or lattice.get("warnings") or []:
        if str(warning) in {"continuation_record_merged"}:
            continue
        errors.append({"code": str(warning), "message": "Source PGN warning blocks strict export."})
    if not replay.get("valid"):
        errors.append({"code": "pgn_replay_failed", "message": replay.get("error") or "PGN parser/replay failed."})
    if lattice.get("unmapped_tokens"):
        errors.append({"code": "unmapped_ocr_tokens", "message": "OCR/glyph tokens require accepted mapping."})
    if source_type in {"EXERCISE_SOLUTION", "TACTICAL_LINE"} and requires_source_fen and not source_fen:
        errors.append({"code": "source_fen_not_machine_accepted", "message": "Diagram solution lines require accepted source FEN."})
    return errors


def _pgn_replay_status(pgn_text: str) -> dict[str, Any]:
    if not pgn_text.strip():
        return {"parsed": False, "valid": False, "error": "empty_pgn"}
    try:
        import chess.pgn  # type: ignore

        game = chess.pgn.read_game(StringIO(pgn_text))
        if game is None:
            return {"parsed": False, "valid": False, "error": "pgn_parser_returned_none"}
        if getattr(game, "errors", None):
            return {
                "parsed": True,
                "valid": False,
                "error": "; ".join(str(error) for error in game.errors[:3]),
            }
        board = game.board()
        for move in game.mainline_moves():
            if not board.is_legal(move):
                return {"parsed": True, "valid": False, "error": f"illegal_move:{move.uci()}"}
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
    fen_total = int(fen_summary.get("total") or 0)
    pgn_total = int(dashboard.get("pgn_total") or pgn_summary.get("total") or 0)
    pgn_feasible_count = (
        int(pgn_summary.get("pgn_feasible_count") or 0)
        if "pgn_feasible_count" in pgn_summary
        else int(pgn_summary.get("total") or 0)
    )
    summary = {
        "pages": int(dashboard.get("pages") or 0),
        "diagrams_total": int(dashboard.get("diagrams_total") or fen_total or 0),
        "fen_accepted": int(fen_summary.get("accepted") or dashboard.get("fen_accepted") or 0),
        "fen_machine_accepted": int(fen_summary.get("runtime_machine_accepted") or 0),
        "fen_corpus_verified": int(fen_summary.get("corpus_verified") or 0),
        "fen_review_required": int(fen_summary.get("review_required") or 0),
        "fen_failed": int(fen_summary.get("failed") or 0),
        "fen_recognition_limit": fen_summary.get("recognition_limit", "all"),
        "fen_skipped_diagram_count": int(fen_summary.get("skipped_diagram_count") or 0),
        "pgn_total": pgn_total,
        "pgn_feasible_count": pgn_feasible_count,
        "pgn_infeasible_count": int(pgn_summary.get("pgn_infeasible_count") or 0),
        "accepted_pgn": int(pgn_summary.get("accepted") or dashboard.get("accepted_pgn") or 0),
        "pgn_machine_accepted": int(pgn_summary.get("runtime_machine_accepted") or 0),
        "pgn_review_required": int(pgn_summary.get("review_required") or 0),
        "pgn_failed": int(pgn_summary.get("failed") or 0),
        "pgn_validation_stage_counts": dict(pgn_summary.get("validation_stage_counts") or {}),
        "pgn_top_blocker_counts": dict(pgn_summary.get("top_blocker_counts") or {}),
        "pgn_infeasible_reason_counts": dict(pgn_summary.get("pgn_infeasible_reason_counts") or {}),
        "fen_semantic_status_counts": dict(fen_summary.get("fen_semantic_status_counts") or {}),
        "repair_attempts": int((repair_payload.get("summary") or {}).get("attempted") or 0),
        "repairs_applied": int((repair_payload.get("summary") or {}).get("applied") or 0),
        "manual_review_items": int(fen_summary.get("failed") or 0) + int(pgn_summary.get("failed") or 0),
        "review_required_rate": _ratio(
            int(fen_summary.get("failed") or 0) + int(pgn_summary.get("failed") or 0),
            fen_total + pgn_feasible_count,
        ),
        "automatic_flow_success_rate": _ratio(
            int(fen_summary.get("runtime_machine_accepted") or 0) + int(pgn_summary.get("runtime_machine_accepted") or 0),
            fen_total + pgn_feasible_count,
        ),
        "ai_fen_candidates": int(dashboard.get("ai_fen_candidates") or 0),
        "ai_pgn_candidates": int(dashboard.get("ai_pgn_candidates") or 0),
    }
    summary["fen_breakdown"] = {
        "total_diagrams": fen_total,
        "accepted": int(summary["fen_accepted"]),
        "machine_accepted": int(summary["fen_machine_accepted"]),
        "corpus_verified": int(summary["fen_corpus_verified"]),
        "review_required": int(summary["fen_review_required"]),
        "failed_or_unaccepted": int(summary["fen_failed"]),
        "acceptance_rate": _ratio(int(summary["fen_machine_accepted"]) + int(summary["fen_corpus_verified"]), fen_total),
        "semantic_status_counts": dict(summary["fen_semantic_status_counts"]),
        "recognition_limit": summary["fen_recognition_limit"],
        "skipped_diagram_count": int(summary["fen_skipped_diagram_count"]),
    }
    summary["pgn_breakdown"] = {
        "total_records": pgn_total,
        "feasible_records": pgn_feasible_count,
        "infeasible_records": int(summary["pgn_infeasible_count"]),
        "accepted": int(summary["accepted_pgn"]),
        "machine_accepted": int(summary["pgn_machine_accepted"]),
        "review_required": int(summary["pgn_review_required"]),
        "failed_feasible_records": int(summary["pgn_failed"]),
        "acceptance_rate_on_feasible": _ratio(int(summary["pgn_machine_accepted"]), pgn_feasible_count),
        "validation_stage_counts": dict(summary["pgn_validation_stage_counts"]),
        "top_blocker_counts": dict(summary["pgn_top_blocker_counts"]),
        "infeasible_reason_counts": dict(summary["pgn_infeasible_reason_counts"]),
    }
    return summary


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
    acceptance_blockers: dict[str, Any],
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
        "acceptance_blockers_summary": acceptance_blockers.get("summary") or {},
        "acceptance_blockers_path": str(out_dir / "report" / "acceptance_blockers.json"),
        "stage_status": stage_payload.get("status") or stage_payload.get("overall_status") or "unknown",
        "ai_policy": "AI candidates are review-only and never directly accepted.",
        "ai_payloads": ai_payloads,
        "exports": {
            "games_pgn": str(out_dir / "export" / "games.pgn"),
            "html": str(out_dir / "index.html"),
        },
        "next_action": _next_action(status, blockers),
    }


def _acceptance_blockers_report(fen_payload: dict[str, Any], pgn_payload: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    code_counts: dict[str, int] = {}
    for kind, payload in [("fen", fen_payload), ("pgn", pgn_payload)]:
        for item in payload.get("items") or []:
            if item.get("status") in (FEN_ACCEPTED_STATUSES if kind == "fen" else PGN_ACCEPTED_STATUSES):
                continue
            blockers = list(item.get("acceptance_blockers") or item.get("validation_errors") or [])
            if not blockers:
                blockers = [{"code": f"{kind}_requires_review", "message": f"{kind.upper()} was not accepted by runtime gate."}]
            normalized_blockers = []
            for blocker in blockers:
                code = str(blocker.get("code") or "unknown_blocker")
                code_counts[code] = code_counts.get(code, 0) + 1
                normalized_blockers.append(blocker)
            items.append(
                {
                    "kind": kind,
                    "id": item.get("id"),
                    "page": item.get("page"),
                    "status": item.get("status"),
                    "runtime_status": item.get("runtime_status"),
                    "source_type": item.get("source_type"),
                    "next_action": item.get("next_action"),
                    "blockers": normalized_blockers,
                }
            )
    summary = {
        "total_blocked_items": len(items),
        "by_code": dict(sorted(code_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
    }
    return {"schema": "kindlemaster.auto_chess.acceptance_blockers.v1", "summary": summary, "items": items}


def _acceptance_blockers_html(report: dict[str, Any]) -> str:
    items = report.get("items") or []
    rows = []
    for item in items:
        blockers = ", ".join(str(blocker.get("code") or "") for blocker in item.get("blockers") or [])
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('kind') or ''))}</td>"
            f"<td>{html.escape(str(item.get('id') or ''))}</td>"
            f"<td>{html.escape(str(item.get('page') or ''))}</td>"
            f"<td>{html.escape(str(item.get('runtime_status') or item.get('status') or ''))}</td>"
            f"<td>{html.escape(str(blockers))}</td>"
            f"<td>{html.escape(str(item.get('next_action') or ''))}</td>"
            "</tr>"
        )
    table = "".join(rows) or '<tr><td colspan="6">No blockers.</td></tr>'
    summary = report.get("summary") or {}
    code_rows = "".join(
        f"<li><code>{html.escape(str(code))}</code>: {html.escape(str(count))}</li>"
        for code, count in (summary.get("by_code") or {}).items()
    ) or "<li>No blocker codes.</li>"
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Auto Chess Acceptance Blockers</title>
<style>
body{{font-family:Georgia,serif;margin:2rem;background:#f7f1e8;color:#21180f}}
table{{border-collapse:collapse;width:100%;background:#fff}}
td,th{{border:1px solid #dccbb4;padding:.45rem;vertical-align:top}}
code{{background:#efe2d1;border-radius:5px;padding:.08rem .24rem}}
</style>
<h1>Auto Chess Acceptance Blockers</h1>
<p>Blocked items: {html.escape(str(summary.get('total_blocked_items', 0)))}</p>
<h2>By code</h2><ul>{code_rows}</ul>
<table><thead><tr><th>Kind</th><th>ID</th><th>Page</th><th>Status</th><th>Blockers</th><th>Next action</th></tr></thead>
<tbody>{table}</tbody></table></html>"""


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


def _accepted_fen_by_source(diagrams: list[dict[str, Any]], fen_payload: dict[str, Any]) -> dict[str, str]:
    by_id = {str(item.get("id") or ""): item for item in fen_payload.get("items") or []}
    index: dict[str, str] = {}
    for diagram in diagrams:
        diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
        fen_item = by_id.get(diagram_id) or {}
        fen = str(fen_item.get("selected_value") or "").strip()
        if not fen or fen_item.get("status") not in FEN_ACCEPTED_STATUSES:
            continue
        for key in [
            diagram_id,
            diagram.get("label"),
            diagram.get("caption"),
            diagram.get("source_diagram"),
        ]:
            normalized = _normalize_source_label(str(key or ""))
            if normalized:
                index[normalized] = fen
    return index


def _record_source_fen(record: dict[str, Any], accepted_fen_by_source: dict[str, str]) -> str:
    for key in [
        record.get("diagram_id"),
        record.get("source_diagram"),
        record.get("label"),
        record.get("record_id"),
        record.get("id"),
    ]:
        normalized = _normalize_source_label(str(key or ""))
        if normalized and normalized in accepted_fen_by_source:
            return accepted_fen_by_source[normalized]
    return ""


def _classify_pgn_source(record: dict[str, Any], pgn: str, lattice: dict[str, Any]) -> str:
    text = " ".join(
        str(record.get(key) or lattice.get(key) or "")
        for key in [
            "label",
            "diagram_id",
            "source_diagram",
            "raw_text",
            "visible_review_text",
            "normalized_text",
        ]
    )
    combined = f"{text} {pgn}".strip()
    if re.search(r"\b(?:Ex\.?|Exercise|Solution)\s*\d{1,2}[-.]\d{1,2}\b", combined, flags=re.IGNORECASE):
        return "EXERCISE_SOLUTION"
    if re.search(r"\bDiagram\s*\d{1,2}[-.]\d{1,2}\b", combined, flags=re.IGNORECASE):
        return "TACTICAL_LINE"
    if re.search(r"^\s*\[(?:Event|White|Black|Site|Date|Result)\s+", pgn, flags=re.MULTILINE):
        return "FULL_GAME"
    if re.search(r"\b\d{1,3}\.(?:\.\.)?\s*\S+", combined):
        prose_letters = len(re.findall(r"[A-Za-z]{4,}", combined))
        move_tokens = len(re.findall(r"\b\d{1,3}\.(?:\.\.)?", combined))
        if prose_letters > move_tokens * 3:
            return "COMMENTARY_WITH_MOVES"
        return "GAME_FRAGMENT"
    return "UNKNOWN"


def _normalize_source_label(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _rows_by_id(rows: Iterable[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(field) or row.get("id") or "")
        if key:
            indexed[key] = row
    return indexed


def _is_human_verified_record(record: dict[str, Any]) -> bool:
    if record.get("human_verified") is True:
        return True
    source = str(record.get("verification_source") or "").strip().lower()
    if source in {"human", "human_visual", "human_manual", "legacy_human_visual"}:
        return True
    return bool(record.get("verified_by") and record.get("verified_at") and record.get("label_status") == "verified")


def _first_float(*values: Any) -> float:
    for value in values:
        if isinstance(value, dict):
            value = value.get("mean", value.get("global", value.get("score")))
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _string_list(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            result.append(value)
            continue
        if isinstance(value, dict):
            result.extend(str(item) for item in value.values() if str(item))
            continue
        try:
            result.extend(str(item) for item in value if str(item))
        except TypeError:
            result.append(str(value))
    return sorted(set(result))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _parse_max_diagrams(value: str | int) -> int:
    text = str(value or "").strip().lower()
    if not text or text in {"0", "all", "none", "unlimited"}:
        return 0
    try:
        return max(0, int(text))
    except ValueError:
        return 0


def _status_summary(items: list[dict[str, Any]], *, accepted: set[str]) -> dict[str, Any]:
    runtime_accepted = len(
        [
            item
            for item in items
            if item.get("runtime_status") in FEN_RUNTIME_ACCEPTED_STATUSES | PGN_RUNTIME_ACCEPTED_STATUSES
            or item.get("status") in accepted
        ]
    )
    corpus_verified = len([item for item in items if item.get("corpus_status") == "corpus_verified"])
    review_required = len(
        [
            item
            for item in items
            if item.get("status") not in accepted
            and str(item.get("runtime_status") or item.get("status") or "").endswith(("VALID", "REVIEW_REQUIRED", "PARSED", "CANDIDATE"))
        ]
    )
    return {
        "total": len(items),
        "accepted": len([item for item in items if item.get("status") in accepted]),
        "failed": len([item for item in items if item.get("status") not in accepted]),
        "runtime_machine_accepted": runtime_accepted,
        "corpus_verified": corpus_verified,
        "review_required": review_required,
        "by_status": {
            status: len([item for item in items if item.get("status") == status])
            for status in sorted({str(item.get("status") or "") for item in items})
            if status
        },
        "by_runtime_status": {
            status: len([item for item in items if item.get("runtime_status") == status])
            for status in sorted({str(item.get("runtime_status") or "") for item in items})
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
    fen_breakdown = summary.get("fen_breakdown") if isinstance(summary.get("fen_breakdown"), dict) else {}
    pgn_breakdown = summary.get("pgn_breakdown") if isinstance(summary.get("pgn_breakdown"), dict) else {}
    blockers = report.get("blockers") or []
    blocker_rows = "".join(
        f"<li><code>{html.escape(str(item.get('code')))}</code>: {html.escape(str(item.get('count')))}</li>"
        for item in blockers
    ) or "<li>No blockers.</li>"
    fen_rows = _breakdown_rows(
        [
            ("Total diagrams", fen_breakdown.get("total_diagrams")),
            ("Accepted", fen_breakdown.get("accepted")),
            ("Machine accepted", fen_breakdown.get("machine_accepted")),
            ("Review required", fen_breakdown.get("review_required")),
            ("Acceptance rate", fen_breakdown.get("acceptance_rate")),
            ("Semantic statuses", fen_breakdown.get("semantic_status_counts")),
        ]
    )
    pgn_rows = _breakdown_rows(
        [
            ("Total records", pgn_breakdown.get("total_records")),
            ("Feasible records", pgn_breakdown.get("feasible_records")),
            ("Infeasible records", pgn_breakdown.get("infeasible_records")),
            ("Accepted", pgn_breakdown.get("accepted")),
            ("Failed feasible records", pgn_breakdown.get("failed_feasible_records")),
            ("Acceptance rate on feasible", pgn_breakdown.get("acceptance_rate_on_feasible")),
            ("Validation stages", pgn_breakdown.get("validation_stage_counts")),
            ("Top blockers", pgn_breakdown.get("top_blocker_counts")),
            ("Infeasible reasons", pgn_breakdown.get("infeasible_reason_counts")),
        ]
    )
    tiles = "".join(
        f"<div class='tile'><strong>{html.escape(label)}</strong><span>{html.escape(str(value))}</span></div>"
        for label, value in [
            ("Status", report.get("status")),
            ("FEN accepted", summary.get("fen_accepted")),
            ("FEN machine", summary.get("fen_machine_accepted")),
            ("FEN corpus", summary.get("fen_corpus_verified")),
            ("FEN failed", summary.get("fen_failed")),
            ("PGN accepted", summary.get("accepted_pgn")),
            ("PGN machine", summary.get("pgn_machine_accepted")),
            ("PGN failed", summary.get("pgn_failed")),
            ("Review rate", summary.get("review_required_rate")),
            ("Auto success rate", summary.get("automatic_flow_success_rate")),
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
table{{border-collapse:collapse;margin:1rem 0 1.5rem;max-width:960px;width:100%;background:#fff;border:1px solid #dccbb4}}
th,td{{border-bottom:1px solid #eadcc8;padding:.55rem .7rem;text-align:left;vertical-align:top}}
th{{color:#7a5631;text-transform:uppercase;font-size:.78rem}}
code{{background:#efe2d1;border-radius:6px;padding:.1rem .25rem}}
</style>
<h1>KindleMaster Auto Chess Quality Report</h1>
<p>{html.escape(str(report.get("next_action") or ""))}</p>
<section class="grid">{tiles}</section>
<h2>FEN Breakdown</h2>
<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>{fen_rows}</tbody></table>
<h2>PGN Breakdown</h2>
<table><thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>{pgn_rows}</tbody></table>
<h2>Blockers</h2>
<ul>{blocker_rows}</ul>
<h2>Policy</h2>
<p>{html.escape(str(report.get("ai_policy") or ""))}</p>
</html>"""


def _breakdown_rows(rows: list[tuple[str, Any]]) -> str:
    return "".join(
        f"<tr><th>{html.escape(label)}</th><td><code>{html.escape(_format_report_value(value))}</code></td></tr>"
        for label, value in rows
    )


def _format_report_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value if value is not None else "")


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


def _missing_python_chess_payload(
    *,
    error_code: str,
    detail: str,
    chess_available: bool,
    chess_pgn_available: bool,
) -> dict[str, Any]:
    return {
        "status": "missing",
        "available": False,
        "chess_available": chess_available,
        "chess_pgn_available": chess_pgn_available,
        "error_code": error_code,
        "detail": detail,
        "missing_requirements": ["python-chess"],
        "blockers": [PYTHON_CHESS_BLOCKER],
        "warnings": [PYTHON_CHESS_BLOCKER],
        "manual_review_required": True,
    }
