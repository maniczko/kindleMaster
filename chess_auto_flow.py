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
from typing import Any, Iterable, Mapping

from chess_fen_hardening import machine_accept_fen, machine_accept_placement, placement_from_fen_or_placement, validate_fen_detailed
from chess_engine_analysis import build_engine_analysis_artifacts
from chess_side_marker_blockers import build_side_marker_blocker_attribution, side_marker_blocker_attribution_markdown
from chess_side_marker_learning import (
    build_side_marker_learning_artifacts,
    persisted_side_marker_learning_report,
    side_marker_learning_markdown,
    side_marker_learning_review_html,
)

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
    export_diagrams_payload = _read_optional_json(out / "chess_diagrams.json")
    dashboard = _load_or_build_dashboard(out)
    pages = list(book.get("pages") or [])
    diagrams = _extract_diagrams(
        book,
        diagrams_payload,
        export_diagrams_payload=export_diagrams_payload,
        export_diagram_rows=_read_jsonl(out / "diagrams.jsonl"),
    )
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
    side_marker_report = _side_marker_assignment_report(diagrams, fen_payload)
    two_crop_quality_metrics = _two_crop_quality_metrics_report(diagrams, fen_payload)
    source_gate = _read_optional_json(out / "reports" / "source_html_quality_gate.json")
    side_marker_blockers = build_side_marker_blocker_attribution(
        two_crop_quality_metrics.get("items") or [],
        source_gate=source_gate,
    )
    side_marker_learning = build_side_marker_learning_artifacts(
        two_crop_quality_metrics.get("items") or [],
        blocker_report=side_marker_blockers,
        assignment_report=side_marker_report,
    )
    two_crop_benchmark_seed = _two_crop_benchmark_seed_report(diagrams)
    accepted_fen_by_source = _accepted_fen_by_source(diagrams, fen_payload)
    engine_analysis = build_engine_analysis_artifacts(out, diagrams, fen_payload)
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
    chess_fen_report_dir = out / "reports" / "chess_fen"
    chess_fen_report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(chess_fen_report_dir / "side_marker_assignment.json", side_marker_report)
    (chess_fen_report_dir / "side_marker_assignment.md").write_text(_side_marker_assignment_markdown(side_marker_report), encoding="utf-8")
    (chess_fen_report_dir / "side_marker_assignment.html").write_text(_side_marker_assignment_html(side_marker_report), encoding="utf-8")
    _write_json(chess_fen_report_dir / "two_crop_quality_metrics.json", two_crop_quality_metrics)
    (chess_fen_report_dir / "two_crop_quality_metrics.md").write_text(
        _two_crop_quality_metrics_markdown(two_crop_quality_metrics),
        encoding="utf-8",
    )
    _write_json(chess_fen_report_dir / "side_marker_blocker_attribution.json", side_marker_blockers)
    (chess_fen_report_dir / "side_marker_blocker_attribution.md").write_text(
        side_marker_blocker_attribution_markdown(side_marker_blockers),
        encoding="utf-8",
    )
    side_marker_learning_report = persisted_side_marker_learning_report(side_marker_learning.get("learning_report") or {})
    side_marker_learning_payload = {
        **side_marker_learning,
        "learning_report": side_marker_learning_report,
        "source_pdf": str(source_pdf or ""),
        "source_html": str(source_html or ""),
        "stage_results": list((stage_payload or {}).get("stages") or []),
    }
    _write_json(chess_fen_report_dir / "side_marker_learning_queue.json", side_marker_learning["queue"])
    _write_jsonl(chess_fen_report_dir / "side_marker_learning_queue.jsonl", side_marker_learning["queue"]["items"])
    _write_jsonl(
        chess_fen_report_dir / "side_marker_learning_labels_template.jsonl",
        side_marker_learning["manual_label_template"],
    )
    _write_json(chess_fen_report_dir / "side_marker_learning_report.json", side_marker_learning_report)
    (chess_fen_report_dir / "side_marker_learning_report.md").write_text(
        side_marker_learning_markdown(side_marker_learning_report),
        encoding="utf-8",
    )
    (chess_fen_report_dir / "side_marker_learning_review.html").write_text(
        side_marker_learning_review_html(side_marker_learning_payload),
        encoding="utf-8",
    )
    _write_json(chess_fen_report_dir / "two_crop_benchmark_seed.json", two_crop_benchmark_seed)
    (chess_fen_report_dir / "two_crop_benchmark_seed.md").write_text(
        _two_crop_benchmark_seed_markdown(two_crop_benchmark_seed),
        encoding="utf-8",
    )
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
                "side_marker_assignment": chess_fen_report_dir / "side_marker_assignment.json",
                "side_marker_assignment_html": chess_fen_report_dir / "side_marker_assignment.html",
                "two_crop_quality_metrics": chess_fen_report_dir / "two_crop_quality_metrics.json",
                "two_crop_quality_metrics_md": chess_fen_report_dir / "two_crop_quality_metrics.md",
                "side_marker_blocker_attribution": chess_fen_report_dir / "side_marker_blocker_attribution.json",
                "side_marker_blocker_attribution_md": chess_fen_report_dir / "side_marker_blocker_attribution.md",
                "side_marker_learning_queue": chess_fen_report_dir / "side_marker_learning_queue.json",
                "side_marker_learning_queue_jsonl": chess_fen_report_dir / "side_marker_learning_queue.jsonl",
                "side_marker_learning_label_template": chess_fen_report_dir / "side_marker_learning_labels_template.jsonl",
                "side_marker_learning_report": chess_fen_report_dir / "side_marker_learning_report.json",
                "side_marker_learning_report_md": chess_fen_report_dir / "side_marker_learning_report.md",
                "side_marker_learning_review_html": chess_fen_report_dir / "side_marker_learning_review.html",
                "two_crop_benchmark_seed": chess_fen_report_dir / "two_crop_benchmark_seed.json",
                "two_crop_benchmark_seed_md": chess_fen_report_dir / "two_crop_benchmark_seed.md",
                **engine_analysis.get("paths", {}),
                "export_games_pgn": dirs["export"] / "games.pgn",
            }.items()
        },
        "engine_analysis": (engine_analysis.get("report") or {}).get("summary") or {},
        "engine_analysis_gate": engine_analysis.get("gate") or {},
        "side_marker_learning": side_marker_learning.get("summary") or {},
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
                "selected_placement": None,
                "placement_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
                "placement_runtime_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
                "placement_acceptance_policy": "runtime_placement_acceptance_v1",
                "placement_acceptance_blockers": [],
                "placement_acceptance_trace": {},
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
                    **_side_marker_fields(diagram),
                    "status": selected["status"],
                    "runtime_status": selected["runtime_status"],
                    "corpus_status": selected["corpus_status"],
                    "acceptance_policy": selected["acceptance_policy"],
                    "candidate_values": [],
                    "selected_value": None,
                    "selected_placement": selected.get("selected_placement"),
                    "placement_status": selected.get("placement_status"),
                    "placement_runtime_status": selected.get("placement_runtime_status"),
                    "full_fen_status": selected.get("full_fen_status"),
                    "full_fen_runtime_status": selected.get("full_fen_runtime_status"),
                    "placement_acceptance_policy": selected.get("placement_acceptance_policy"),
                    "placement_acceptance_blockers": selected.get("placement_acceptance_blockers", []),
                    "placement_acceptance_trace": selected.get("placement_acceptance_trace", {}),
                    "validation_errors": selected["validation_errors"],
                    "acceptance_blockers": selected["acceptance_blockers"],
                    "acceptance_trace": selected["acceptance_trace"],
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
                **_side_marker_fields(diagram),
                "status": selected["status"],
                "runtime_status": selected["runtime_status"],
                "corpus_status": selected["corpus_status"],
                "acceptance_policy": selected["acceptance_policy"],
                "candidate_values": candidate_rows,
                "selected_value": selected.get("selected_value"),
                "selected_placement": selected.get("selected_placement"),
                "placement_status": selected.get("placement_status"),
                "placement_runtime_status": selected.get("placement_runtime_status"),
                "full_fen_status": selected.get("full_fen_status"),
                "full_fen_runtime_status": selected.get("full_fen_runtime_status"),
                "placement_acceptance_policy": selected.get("placement_acceptance_policy"),
                "placement_acceptance_blockers": selected.get("placement_acceptance_blockers", []),
                "placement_acceptance_trace": selected.get("placement_acceptance_trace", {}),
                "validation_errors": selected.get("validation_errors", []),
                "acceptance_blockers": selected.get("acceptance_blockers", []),
                "acceptance_trace": selected.get("acceptance_trace", {}),
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
    summary.update(_fen_two_gate_summary(candidates))
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
                    **_side_marker_fields(diagram),
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
    placement = str(diagram.get("placement") or diagram.get("placement_fen") or "").strip()
    full_fen = str(diagram.get("full_fen") or "").strip()
    if placement or full_fen:
        rows.append(
            {
                "source": "deterministic_candidate",
                "fen": "",
                "placement": placement,
                "placement_fen": placement,
                "full_fen": full_fen,
                **_side_marker_fields(diagram),
                "authoritative": False,
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
                "method": diagram.get("method") or diagram.get("recognition_method") or "deterministic_candidate",
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
                        **_side_marker_fields({**diagram, **deterministic_row}),
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


def _side_marker_fields(source: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "side_to_move",
        "side_to_move_status",
        "side_to_move_evidence",
        "side_marker_symbol",
        "side_marker_status",
        "side_marker_source",
        "side_marker_bbox",
        "side_marker_confidence",
        "side_marker_assignment_trace",
        "strict_fen_side_evidence_trusted",
        "fen_suppressed_reason",
        "marker_crop_quality",
        "marker_crop_fail_reason",
        "marker_crop_quality_gate",
        "marker_bbox",
        "marker_crop_bbox",
        "selected_marker_zone",
    )
    return {key: source.get(key) for key in keys if source.get(key) not in (None, "")}


def _fen_candidate_row(candidate: dict[str, Any]) -> dict[str, Any]:
    validation = validate_fen_detailed(str(candidate.get("fen") or ""))
    machine = machine_accept_fen(candidate)
    placement_machine = machine_accept_placement(candidate)
    return {
        "source": candidate.get("source") or "unknown",
        "value": candidate.get("fen") or "",
        "placement_value": candidate.get("placement") or candidate.get("placement_fen") or placement_machine.get("normalized_placement") or "",
        "full_fen": candidate.get("full_fen") or "",
        "authoritative": bool(candidate.get("authoritative")),
        "confidence": _first_float(candidate.get("confidence")),
        "method": candidate.get("method") or candidate.get("source") or "unknown",
        "warnings": list(candidate.get("warnings") or []),
        **_side_marker_fields(candidate),
        "deterministic_valid": validation.is_legal_position and not validation.errors,
        "normalized_value": validation.normalized_fen,
        "errors": [asdict(error) for error in validation.errors],
        "validation_warnings": [asdict(warning) for warning in validation.warnings],
        "runtime_status": machine["runtime_status"],
        "full_fen_status": machine["runtime_status"],
        "full_fen_runtime_status": machine["runtime_status"],
        "acceptance_policy": machine["acceptance_policy"],
        "acceptance_blockers": machine["acceptance_blockers"],
        "acceptance_trace": machine["acceptance_trace"],
        "normalized_placement": placement_machine.get("normalized_placement"),
        "placement_valid": not any(
            blocker.get("code") in {"placement_candidate_missing", "invalid_rank_count", "invalid_rank_width", "invalid_rank_digit", "invalid_piece"}
            for blocker in placement_machine.get("acceptance_blockers") or []
        ),
        "placement_plausible": bool(placement_machine.get("normalized_placement")) and not any(
            blocker.get("code")
            in {
                "missing_white_king",
                "missing_black_king",
                "too_many_white_kings",
                "too_many_black_kings",
                "pawn_on_back_rank",
                "white_king_count_invalid",
                "black_king_count_invalid",
            }
            for blocker in placement_machine.get("acceptance_blockers") or []
        ),
        "placement_runtime_status": placement_machine["runtime_status"],
        "placement_acceptance_policy": placement_machine["acceptance_policy"],
        "placement_acceptance_blockers": placement_machine["acceptance_blockers"],
        "placement_acceptance_trace": placement_machine["acceptance_trace"],
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
            "selected_placement": deterministic.get("normalized_placement"),
            "placement_status": deterministic.get("placement_runtime_status"),
            "placement_runtime_status": deterministic.get("placement_runtime_status"),
            "full_fen_status": "FEN_CORPUS_VERIFIED",
            "full_fen_runtime_status": "FEN_CORPUS_VERIFIED",
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
            "selected_placement": machine.get("normalized_placement"),
            "placement_status": machine.get("placement_runtime_status"),
            "placement_runtime_status": machine.get("placement_runtime_status"),
            "full_fen_status": "FEN_MACHINE_ACCEPTED",
            "full_fen_runtime_status": "FEN_MACHINE_ACCEPTED",
            "placement_acceptance_policy": machine.get("placement_acceptance_policy"),
            "placement_acceptance_blockers": machine.get("placement_acceptance_blockers", []),
            "placement_acceptance_trace": machine.get("placement_acceptance_trace", {}),
            "validation_errors": [],
            "acceptance_blockers": [],
            "acceptance_trace": machine.get("acceptance_trace") or {},
            "next_action": "export_allowed_runtime_machine",
        }
    placement_machine = next(
        (row for row in candidate_rows if row.get("placement_runtime_status") == "FEN_PLACEMENT_MACHINE_ACCEPTED"),
        None,
    )
    if placement_machine:
        errors: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for row in candidate_rows:
            for error in row.get("errors") or []:
                errors.append({"source": row.get("source"), **error})
            for blocker in row.get("acceptance_blockers") or []:
                blockers.append({"source": row.get("source"), **blocker})
        blockers.append(
            {
                "source": placement_machine.get("source"),
                "code": "full_fen_metadata_not_accepted",
                "message": "Placement is machine accepted, but full FEN metadata is not runtime accepted.",
            }
        )
        return {
            "status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
            "runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
            "corpus_status": "not_corpus_verified",
            "acceptance_policy": "runtime_placement_acceptance_v1",
            "selected_value": None,
            "selected_placement": placement_machine.get("normalized_placement"),
            "placement_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
            "placement_runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED",
            "full_fen_status": "FEN_REVIEW_REQUIRED",
            "full_fen_runtime_status": "FEN_REVIEW_REQUIRED",
            "placement_acceptance_policy": placement_machine.get("placement_acceptance_policy"),
            "placement_acceptance_blockers": placement_machine.get("placement_acceptance_blockers", []),
            "placement_acceptance_trace": placement_machine.get("placement_acceptance_trace", {}),
            "validation_errors": errors,
            "acceptance_blockers": blockers,
            "acceptance_trace": {
                "candidate_count": len(candidate_rows),
                "placement_acceptance_trace": placement_machine.get("placement_acceptance_trace") or {},
            },
            "next_action": "resolve_full_fen_metadata_or_human_verify",
        }
    if not candidate_rows:
        return {
            "status": "FEN_FAILED",
            "runtime_status": "FEN_FAILED",
            "corpus_status": "not_corpus_verified",
            "acceptance_policy": "runtime_machine_acceptance_v1",
            "selected_value": None,
            "selected_placement": None,
            "placement_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
            "placement_runtime_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
            "full_fen_status": "FEN_REVIEW_REQUIRED",
            "full_fen_runtime_status": "FEN_REVIEW_REQUIRED",
            "placement_acceptance_policy": "runtime_placement_acceptance_v1",
            "placement_acceptance_blockers": [{"code": "placement_candidate_missing", "message": "No placement candidate was available."}],
            "placement_acceptance_trace": {},
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
            "selected_placement": None,
            "placement_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
            "placement_runtime_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
            "full_fen_status": "FEN_REVIEW_REQUIRED",
            "full_fen_runtime_status": "FEN_REVIEW_REQUIRED",
            "placement_acceptance_policy": "runtime_placement_acceptance_v1",
            "placement_acceptance_blockers": [],
            "placement_acceptance_trace": {},
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
        "selected_placement": None,
        "placement_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
        "placement_runtime_status": "FEN_PLACEMENT_REVIEW_REQUIRED",
        "full_fen_status": "FEN_REVIEW_REQUIRED",
        "full_fen_runtime_status": "FEN_REVIEW_REQUIRED",
        "placement_acceptance_policy": "runtime_placement_acceptance_v1",
        "placement_acceptance_blockers": [],
        "placement_acceptance_trace": {},
        "validation_errors": errors or [{"code": "fen_validation_failed", "message": "No candidate passed deterministic FEN validation."}],
        "acceptance_blockers": blockers or [{"code": "fen_validation_failed", "message": "No candidate passed deterministic FEN validation."}],
        "acceptance_trace": {"candidate_count": len(candidate_rows)},
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
    payload = {"schema": "kindlemaster.auto_chess.pgn_candidates.v1", "items": items, "summary": summary}
    validation = {"schema": "kindlemaster.auto_chess.pgn_validation.v1", "items": items, "summary": summary}
    return payload, validation, repairs


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
    return {
        "pages": int(dashboard.get("pages") or 0),
        "diagrams_total": int(dashboard.get("diagrams_total") or fen_summary.get("total") or 0),
        "fen_accepted": int(fen_summary.get("accepted") or dashboard.get("fen_accepted") or 0),
        "fen_machine_accepted": int(fen_summary.get("runtime_machine_accepted") or 0),
        "fen_placement_machine_accepted": int(fen_summary.get("placement_machine_accepted") or 0),
        "fen_placement_machine_accepted_rate": float(fen_summary.get("placement_machine_accepted_rate") or 0.0),
        "fen_full_machine_accepted": int(fen_summary.get("runtime_machine_accepted") or 0),
        "fen_full_machine_accepted_rate": _ratio(
            int(fen_summary.get("runtime_machine_accepted") or 0),
            int(fen_summary.get("total") or 0),
        ),
        "fen_full_review_required": int(fen_summary.get("failed") or 0),
        "fen_placement_review_required": int(fen_summary.get("placement_review_required") or 0),
        "automatic_placement_success_rate": float(fen_summary.get("placement_machine_accepted_rate") or 0.0),
        "fen_corpus_verified": int(fen_summary.get("corpus_verified") or 0),
        "fen_review_required": int(fen_summary.get("review_required") or 0),
        "fen_failed": int(fen_summary.get("failed") or 0),
        "fen_recognition_limit": fen_summary.get("recognition_limit", "all"),
        "fen_skipped_diagram_count": int(fen_summary.get("skipped_diagram_count") or 0),
        "pgn_total": int(dashboard.get("pgn_total") or pgn_summary.get("total") or 0),
        "accepted_pgn": int(pgn_summary.get("accepted") or dashboard.get("accepted_pgn") or 0),
        "pgn_machine_accepted": int(pgn_summary.get("runtime_machine_accepted") or 0),
        "pgn_review_required": int(pgn_summary.get("review_required") or 0),
        "pgn_failed": int(pgn_summary.get("failed") or 0),
        "repair_attempts": int((repair_payload.get("summary") or {}).get("attempted") or 0),
        "repairs_applied": int((repair_payload.get("summary") or {}).get("applied") or 0),
        "manual_review_items": int(fen_summary.get("failed") or 0) + int(pgn_summary.get("failed") or 0),
        "review_required_rate": _ratio(
            int(fen_summary.get("failed") or 0) + int(pgn_summary.get("failed") or 0),
            int(fen_summary.get("total") or 0) + int(pgn_summary.get("total") or 0),
        ),
        "automatic_flow_success_rate": _ratio(
            int(fen_summary.get("runtime_machine_accepted") or 0) + int(pgn_summary.get("runtime_machine_accepted") or 0),
            int(fen_summary.get("total") or 0) + int(pgn_summary.get("total") or 0),
        ),
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
    category_counts: dict[str, int] = {}
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
                category = _classify_acceptance_blocker(code, kind=kind)
                code_counts[code] = code_counts.get(code, 0) + 1
                category_counts[category] = category_counts.get(category, 0) + 1
                normalized_blockers.append({**blocker, "category": category})
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
        "by_category": dict(sorted(category_counts.items(), key=lambda pair: (-pair[1], pair[0]))),
    }
    return {"schema": "kindlemaster.auto_chess.acceptance_blockers.v1", "summary": summary, "items": items}


def _classify_acceptance_blocker(code: str, *, kind: str = "fen") -> str:
    normalized = str(code or "").strip()
    if normalized in {
        "board_grid_not_detected",
        "board_visual_pattern_not_detected",
        "partial_board_crop_without_dense_board_evidence",
        "image_board_requires_review",
        "review_crop_candidate_mismatch",
    }:
        return "crop_grid"
    if normalized in {
        "piece_template_confidence_below_threshold",
        "piece_template_set_incomplete",
        "queen_color_ambiguous_suppressed",
        "sparse_position_confidence_below_threshold",
        "no_square_alternatives",
        "square_alternatives_not_checked",
        "no_template_or_model_agreement",
        "score_margin_too_low",
        "score_margin_below_threshold",
    }:
        return "recognition"
    if normalized in {
        "placement_candidate_missing",
        "invalid_rank_count",
        "invalid_rank_width",
        "invalid_rank_digit",
        "invalid_piece",
        "missing_white_king",
        "missing_black_king",
        "too_many_white_kings",
        "too_many_black_kings",
        "pawn_on_back_rank",
        "white_king_count_invalid",
        "black_king_count_invalid",
    }:
        return "placement"
    if normalized in {
        "fen_must_have_six_fields",
        "python_chess_invalid_position",
        "python_chess_evidence_missing",
        "validate_fen_evidence_missing",
        "side_to_move_invalid",
        "castling_invalid",
        "castling_order_invalid",
        "en_passant_invalid",
        "move_counters_invalid",
        "fullmove_number_invalid",
        "full_fen_metadata_not_accepted",
    }:
        return "full_fen_validation"
    if normalized in {"ai_review_only_source", "non_deterministic_source", "fen_source_missing"}:
        return "source_policy"
    if normalized in {"confidence_below_runtime_threshold", "confidence_below_threshold"}:
        return "confidence"
    if normalized in {"source_crop_hash_missing", "template_profile_not_ready"}:
        return "metadata"
    if normalized in {
        "source_fen_not_machine_accepted",
        "pgn_parse_failed",
        "illegal_pgn",
        "unmapped_chess_glyphs",
        "pgn_replay_failed",
    } or kind == "pgn":
        return "pgn"
    if normalized in {"python_chess_unavailable", "python_chess_missing", "python_chess_pgn_missing"}:
        return "runtime_dependency"
    return "unknown"


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
    category_rows = "".join(
        f"<li><code>{html.escape(str(category))}</code>: {html.escape(str(count))}</li>"
        for category, count in (summary.get("by_category") or {}).items()
    ) or "<li>No blocker categories.</li>"
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
<h2>By category</h2><ul>{category_rows}</ul>
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


def _extract_diagrams(
    book: dict[str, Any],
    diagrams_payload: dict[str, Any],
    *,
    export_diagrams_payload: dict[str, Any] | None = None,
    export_diagram_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if isinstance(diagrams_payload.get("diagrams"), list) and diagrams_payload.get("diagrams"):
        return list(diagrams_payload.get("diagrams") or [])
    diagrams: list[dict[str, Any]] = []
    for page in book.get("pages") or []:
        page_number = int(page.get("page") or 0)
        for diagram in page.get("diagrams") or []:
            if isinstance(diagram, dict):
                diagrams.append({**diagram, "page": int(diagram.get("page") or page_number)})
    if diagrams:
        return diagrams
    if isinstance((export_diagrams_payload or {}).get("diagrams"), list):
        return list((export_diagrams_payload or {}).get("diagrams") or [])
    if export_diagram_rows:
        return list(export_diagram_rows)
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
    placement_machine_accepted = len(
        [item for item in items if item.get("placement_runtime_status") == "FEN_PLACEMENT_MACHINE_ACCEPTED"]
    )
    placement_review_required = len(
        [item for item in items if item.get("placement_runtime_status") == "FEN_PLACEMENT_REVIEW_REQUIRED"]
    )
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
        "placement_machine_accepted": placement_machine_accepted,
        "placement_machine_accepted_rate": _ratio(placement_machine_accepted, len(items)),
        "placement_review_required": placement_review_required,
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


def _fen_two_gate_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    def blocker_codes(item: dict[str, Any]) -> set[str]:
        codes = set()
        for key in ("acceptance_blockers", "validation_errors"):
            for blocker in item.get(key) or []:
                if isinstance(blocker, dict) and blocker.get("code"):
                    codes.add(str(blocker.get("code")))
        return codes

    marker_codes = {
        "full_fen_blocked_by_marker",
        "side_to_move_inferred",
        "inferred_only",
        "marker_missing",
        "marker_conflict",
        "ambiguous_marker",
        "multi_side",
        "side_to_move_marker_local_ambiguous",
        "side_to_move_marker_local_conflict",
        "side_to_move_marker_multi_region_conflict",
    }
    placement_codes = {
        "full_fen_blocked_by_placement",
        "placement_candidate_missing",
        "invalid_rank_count",
        "invalid_rank_width",
        "missing_white_king",
        "missing_black_king",
        "white_king_count_invalid",
        "black_king_count_invalid",
    }
    return {
        "placement_accepted_count": len(
            [item for item in items if item.get("placement_runtime_status") == "FEN_PLACEMENT_MACHINE_ACCEPTED"]
        ),
        "full_fen_accepted_count": len(
            [item for item in items if item.get("full_fen_runtime_status") in {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"}]
        ),
        "full_fen_blocked_by_marker_count": len([item for item in items if blocker_codes(item) & marker_codes]),
        "full_fen_blocked_by_placement_count": len([item for item in items if blocker_codes(item) & placement_codes]),
    }


def _diagram_record_id(record: dict[str, Any], index: int) -> str:
    return str(record.get("diagram_id") or record.get("id") or f"diagram-{index}")


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return ""


def _fen_items_by_diagram_id(fen_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id") or ""): item
        for item in fen_payload.get("items") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }


def _two_crop_blocker_codes(record: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for key in ("acceptance_blockers", "placement_acceptance_blockers"):
        for blocker in record.get(key) or []:
            if isinstance(blocker, dict) and blocker.get("code"):
                codes.add(str(blocker.get("code")))
    return codes


def _two_crop_quality_value(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"pass", "fail"} else "fail"


def _two_crop_fail_reasons(raw_reasons: Any, quality: str, fallback_reason: str) -> list[str]:
    reasons = [str(reason) for reason in (raw_reasons or []) if str(reason)]
    if quality == "fail" and not reasons:
        reasons.append(fallback_reason)
    return sorted(set(reasons))


def _two_crop_manual_review_reason(row: Mapping[str, Any]) -> str:
    existing = str(_first_non_empty(row.get("manual_review_reason")) or "")
    if existing:
        return existing
    board_reasons = {str(reason) for reason in (row.get("board_crop_fail_reason") or []) if str(reason)}
    marker_reasons = {str(reason) for reason in (row.get("marker_crop_fail_reason") or []) if str(reason)}
    marker_status = str(row.get("side_marker_status") or "")
    if board_reasons:
        return "bad_crop"
    if "multiple_candidates" in marker_reasons or "multiple" in marker_status:
        return "multiple"
    if "marker_missing" in marker_reasons or marker_status in {"marker_missing", "side_to_move_marker_missing", "missing", "no_marker"}:
        return "marker_missing"
    if "unclear_symbol" in marker_reasons or "ambiguous" in marker_status:
        return "unclear"
    if "conflict" in marker_status:
        return "marker_conflict"
    if marker_reasons:
        return "bad_crop"
    return ""


def _two_crop_quality_rows(diagrams: list[dict[str, Any]], fen_payload: dict[str, Any]) -> list[dict[str, Any]]:
    fen_by_id = _fen_items_by_diagram_id(fen_payload)
    marker_blocker_codes = {
        "full_fen_blocked_by_marker",
        "side_to_move_inferred",
        "inferred_only",
        "marker_missing",
        "marker_conflict",
        "ambiguous_marker",
        "multi_side",
        "side_to_move_marker_local_ambiguous",
        "side_to_move_marker_local_conflict",
        "side_to_move_marker_multi_region_conflict",
    }
    placement_blocker_codes = {
        "full_fen_blocked_by_placement",
        "placement_candidate_missing",
        "invalid_rank_count",
        "invalid_rank_width",
        "invalid_rank_digit",
        "invalid_piece",
        "missing_white_king",
        "missing_black_king",
        "white_king_count_invalid",
        "black_king_count_invalid",
        "pawn_on_back_rank",
    }
    rows: list[dict[str, Any]] = []
    for index, diagram in enumerate(diagrams, start=1):
        if not isinstance(diagram, dict):
            continue
        diagram_id = _diagram_record_id(diagram, index)
        fen_item = fen_by_id.get(diagram_id, {})
        merged = {**diagram, **fen_item}
        blocker_codes = _two_crop_blocker_codes(merged)
        marker_status = str(_first_non_empty(merged.get("side_marker_status"), "marker_missing"))
        placement_status = str(_first_non_empty(merged.get("placement_runtime_status"), merged.get("placement_status")))
        full_fen_status = str(
            _first_non_empty(merged.get("full_fen_runtime_status"), merged.get("full_fen_status"), merged.get("runtime_status"))
        )
        has_board_crop = bool(_first_non_empty(merged.get("board_crop_path")))
        has_marker_crop = bool(_first_non_empty(merged.get("side_marker_crop_path")))
        has_debug_overlay = bool(_first_non_empty(merged.get("debug_overlay_path")))
        board_crop_quality = _two_crop_quality_value(merged.get("board_crop_quality"))
        marker_crop_quality = _two_crop_quality_value(merged.get("marker_crop_quality"))
        board_crop_fail_reason = _two_crop_fail_reasons(
            merged.get("board_crop_fail_reason"),
            board_crop_quality,
            "board_crop_quality_missing" if has_board_crop else "board_crop_missing",
        )
        marker_crop_fail_reason = _two_crop_fail_reasons(
            merged.get("marker_crop_fail_reason"),
            marker_crop_quality,
            "marker_crop_quality_missing" if has_marker_crop else "marker_missing",
        )
        marker_missing = marker_status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker"}
        marker_conflict = "conflict" in marker_status or "multi" in marker_status
        marker_ambiguous = "ambiguous" in marker_status
        trusted_marker = marker_status.startswith("trusted_") or marker_status == "trusted_marker"
        placement_accepted = placement_status == "FEN_PLACEMENT_MACHINE_ACCEPTED"
        manual_review_raw = merged.get("manual_review_required")
        manual_review_required = (
            bool(manual_review_raw)
            if manual_review_raw is not None
            else board_crop_quality == "fail" or marker_crop_quality == "fail" or marker_missing or marker_conflict or marker_ambiguous
        )
        system_suggestion_mismatch = bool(
            "system_suggestion_mismatch" in blocker_codes
            or "system_suggestion_mismatch" in {str(warning) for warning in (merged.get("warnings") or []) if str(warning)}
        )
        row = {
            "board_crop_quality": board_crop_quality,
            "board_crop_fail_reason": board_crop_fail_reason,
            "marker_crop_quality": marker_crop_quality,
            "marker_crop_fail_reason": marker_crop_fail_reason,
            "manual_review_required": manual_review_required,
            "manual_review_reason": _two_crop_manual_review_reason(
                {
                    **merged,
                    "board_crop_fail_reason": board_crop_fail_reason,
                    "marker_crop_fail_reason": marker_crop_fail_reason,
                    "side_marker_status": marker_status,
                    "manual_review_reason": merged.get("manual_review_reason"),
                }
            )
            if manual_review_required
            else "",
            "system_suggestion_mismatch": system_suggestion_mismatch,
        }
        rows.append(
            {
                "diagram_id": diagram_id,
                "page": merged.get("page") or merged.get("page_number") or "",
                "board_crop_path": str(_first_non_empty(merged.get("board_crop_path"))),
                "side_marker_crop_path": str(_first_non_empty(merged.get("side_marker_crop_path"))),
                "side_marker_search_crop_path": str(_first_non_empty(merged.get("side_marker_search_crop_path"))),
                "marker_search_zone_preview_path": str(_first_non_empty(merged.get("marker_search_zone_preview_path"))),
                "marker_search_zone_preview_bbox": list(merged.get("marker_search_zone_preview_bbox") or []),
                "side_marker_review_crop_path": str(_first_non_empty(merged.get("side_marker_review_crop_path"))),
                "side_marker_review_crop_kind": str(_first_non_empty(merged.get("side_marker_review_crop_kind"))),
                "debug_overlay_path": str(_first_non_empty(merged.get("debug_overlay_path"))),
                "debug_context_crop_path": str(_first_non_empty(merged.get("debug_context_crop_path"))),
                "raw_board_candidate_bbox": list(merged.get("raw_board_candidate_bbox") or []),
                "tight_board_bbox": list(merged.get("tight_board_bbox") or []),
                "board_bbox": list(merged.get("board_bbox") or []),
                "has_board_crop": has_board_crop,
                "has_side_marker_crop": has_marker_crop,
                "has_side_marker_search_crop": bool(_first_non_empty(merged.get("side_marker_search_crop_path"))),
                "has_debug_overlay": has_debug_overlay,
                "board_crop_quality": row["board_crop_quality"],
                "board_crop_fail_reason": row["board_crop_fail_reason"],
                "board_crop_quality_gate": dict(merged.get("board_crop_quality_gate") or {}),
                "marker_search_zones": dict(merged.get("marker_search_zones") or {}),
                "selected_marker_zone": merged.get("selected_marker_zone"),
                "marker_bbox": list(merged.get("marker_bbox") or []),
                "marker_crop_bbox": list(merged.get("marker_crop_bbox") or []),
                "marker_crop_quality": row["marker_crop_quality"],
                "marker_crop_fail_reason": row["marker_crop_fail_reason"],
                "marker_crop_quality_gate": dict(merged.get("marker_crop_quality_gate") or {}),
                "side_to_move_detected": merged.get("side_to_move_detected"),
                "side_to_move_confidence": merged.get("side_to_move_confidence"),
                "manual_review_required": row["manual_review_required"],
                "manual_review_reason": row["manual_review_reason"],
                "system_suggestion_mismatch": row["system_suggestion_mismatch"],
                "side_marker_status": marker_status,
                "side_to_move": str(_first_non_empty(merged.get("side_to_move"), "unknown")),
                "trusted_marker": trusted_marker,
                "marker_missing": marker_missing,
                "marker_conflict": marker_conflict,
                "marker_ambiguous": marker_ambiguous,
                "placement_status": placement_status,
                "full_fen_status": full_fen_status,
                "blocked_by_marker": bool(blocker_codes & marker_blocker_codes)
                or marker_missing
                or marker_conflict
                or marker_ambiguous,
                "blocked_by_placement": "full_fen_blocked_by_placement" in blocker_codes
                or (not placement_accepted and bool(blocker_codes & placement_blocker_codes))
                or placement_status == "FEN_PLACEMENT_REVIEW_REQUIRED",
                "acceptance_blocker_codes": sorted(blocker_codes),
            }
        )
    return rows


def _two_crop_accuracy_data_gap(diagrams: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [diagram for diagram in diagrams if isinstance(diagram, dict) and _is_human_verified_record(diagram)]
    marker_label_count = len(
        [
            diagram
            for diagram in verified
            if _first_non_empty(diagram.get("expected_side_to_move"), diagram.get("side_to_move"), diagram.get("side_to_move_label"))
        ]
    )
    placement_label_count = len(
        [
            diagram
            for diagram in verified
            if _first_non_empty(diagram.get("expected_placement"), diagram.get("placement"), diagram.get("placement_fen"))
        ]
    )
    both_label_count = len(
        [
            diagram
            for diagram in verified
            if _first_non_empty(diagram.get("expected_side_to_move"), diagram.get("side_to_move"), diagram.get("side_to_move_label"))
            and _first_non_empty(diagram.get("expected_placement"), diagram.get("placement"), diagram.get("placement_fen"))
        ]
    )
    missing_data: list[dict[str, Any]] = []
    if marker_label_count <= 0:
        missing_data.append(
            {
                "field": "expected_side_to_move",
                "needed": "human_verified side-to-move labels",
                "available": marker_label_count,
            }
        )
    if placement_label_count <= 0:
        missing_data.append(
            {
                "field": "expected_placement",
                "needed": "human_verified board placement labels",
                "available": placement_label_count,
            }
        )
    return {
        "status": "TRAINING_DATA_GAP" if missing_data else "READY",
        "message": "TRAINING_DATA_GAP: accuracy requires human-verified side-to-move and placement labels."
        if missing_data
        else "Human-verified labels are available for accuracy measurement.",
        "human_verified_record_count": len(verified),
        "marker_label_count": marker_label_count,
        "placement_label_count": placement_label_count,
        "both_label_count": both_label_count,
        "missing_data": missing_data,
    }


def _two_crop_quality_metrics_report(diagrams: list[dict[str, Any]], fen_payload: dict[str, Any]) -> dict[str, Any]:
    rows = _two_crop_quality_rows(diagrams, fen_payload)
    board_fail_reasons = [
        str(reason)
        for row in rows
        for reason in (row.get("board_crop_fail_reason") or [])
        if str(reason)
    ]
    marker_fail_reasons = [
        str(reason)
        for row in rows
        for reason in (row.get("marker_crop_fail_reason") or [])
        if str(reason)
    ]
    diagram_count = len(rows)
    board_pass_count = len([row for row in rows if row.get("board_crop_quality") == "pass"])
    board_fail_count = len([row for row in rows if row.get("board_crop_quality") == "fail"])
    marker_pass_count = len([row for row in rows if row.get("marker_crop_quality") == "pass"])
    marker_fail_count = len([row for row in rows if row.get("marker_crop_quality") == "fail"])
    board_reason_breakdown = {reason: board_fail_reasons.count(reason) for reason in sorted(set(board_fail_reasons))}
    marker_reason_breakdown = {reason: marker_fail_reasons.count(reason) for reason in sorted(set(marker_fail_reasons))}
    manual_review_required_count = len([row for row in rows if row.get("manual_review_required")])
    system_suggestion_mismatch_count = len([row for row in rows if row.get("system_suggestion_mismatch")])
    summary = {
        "diagram_count": diagram_count,
        "board_crop_count": len([row for row in rows if row.get("has_board_crop")]),
        "board_crop_pass_count": board_pass_count,
        "board_crop_fail_count": board_fail_count,
        "board_crop_fail_reason_breakdown": board_reason_breakdown,
        "by_board_crop_fail_reason": board_reason_breakdown,
        "side_marker_crop_count": len([row for row in rows if row.get("has_side_marker_crop")]),
        "side_marker_search_crop_count": len([row for row in rows if row.get("has_side_marker_search_crop")]),
        "marker_search_zone_count": len([row for row in rows if row.get("marker_search_zones")]),
        "marker_search_zone_region_count": sum(len(row.get("marker_search_zones") or {}) for row in rows),
        "marker_bbox_count": len([row for row in rows if row.get("marker_bbox")]),
        "marker_crop_pass_count": marker_pass_count,
        "marker_crop_fail_count": marker_fail_count,
        "marker_crop_fail_reason_breakdown": marker_reason_breakdown,
        "by_marker_crop_fail_reason": marker_reason_breakdown,
        "manual_review_required_count": manual_review_required_count,
        "system_suggestion_mismatch_count": system_suggestion_mismatch_count,
        "board_crop_quality_pass_count": board_pass_count,
        "board_crop_quality_pass_rate": round(
            board_pass_count / diagram_count,
            4,
        )
        if diagram_count
        else 0.0,
        "board_crop_contains_coordinates_count": board_fail_reasons.count("contains_coordinates"),
        "board_crop_contains_marker_count": board_fail_reasons.count("contains_marker"),
        "marker_crop_quality_pass_count": marker_pass_count,
        "marker_crop_quality_pass_rate": round(
            marker_pass_count / diagram_count,
            4,
        )
        if diagram_count
        else 0.0,
        "marker_crop_missing_count": marker_fail_reasons.count("marker_missing"),
        "marker_crop_cut_off_count": marker_fail_reasons.count("marker_cut_off"),
        "marker_crop_mostly_board_edge_count": marker_fail_reasons.count("mostly_board_edge"),
        "marker_crop_mostly_coordinates_count": marker_fail_reasons.count("mostly_rank_numbers")
        + marker_fail_reasons.count("mostly_file_letters"),
        "trusted_marker_count": len([row for row in rows if row.get("trusted_marker")]),
        "marker_missing_count": len([row for row in rows if row.get("marker_missing")]),
        "marker_conflict_count": len([row for row in rows if row.get("marker_conflict")]),
        "side_to_move_auto_confident_rate": round(len([row for row in rows if row.get("trusted_marker")]) / diagram_count, 4)
        if diagram_count
        else 0.0,
        "side_to_move_manual_review_rate": round(
            len([row for row in rows if bool(row.get("manual_review_required", True))]) / diagram_count,
            4,
        )
        if diagram_count
        else 0.0,
        "side_to_move_auto_vs_manual_accuracy": None,
        "placement_accepted_count": len(
            [row for row in rows if row.get("placement_status") == "FEN_PLACEMENT_MACHINE_ACCEPTED"]
        ),
        "full_fen_accepted_count": len(
            [row for row in rows if row.get("full_fen_status") in {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"}]
        ),
        "blocked_by_marker_count": len([row for row in rows if row.get("blocked_by_marker")]),
        "blocked_by_placement_count": len([row for row in rows if row.get("blocked_by_placement")]),
    }
    return {
        "schema": "kindlemaster.chess_fen.two_crop_quality_metrics.v1",
        "summary": summary,
        "probe_quality_before_after": _two_crop_probe_before_after(summary),
        "accuracy": _two_crop_accuracy_data_gap(diagrams),
        "items": rows,
    }


def _two_crop_probe_before_after(summary: dict[str, Any]) -> dict[str, Any]:
    after = {
        "marker_missing_count": int(summary.get("marker_missing_count") or 0),
        "marker_conflict_count": int(summary.get("marker_conflict_count") or 0),
        "trusted_marker_count": int(summary.get("trusted_marker_count") or 0),
        "full_fen_accepted_count": int(summary.get("full_fen_accepted_count") or 0),
    }
    before = {
        "marker_missing_count": summary.get("baseline_marker_missing_count"),
        "marker_conflict_count": summary.get("baseline_marker_conflict_count"),
        "trusted_marker_count": summary.get("baseline_trusted_marker_count"),
        "full_fen_accepted_count": summary.get("baseline_full_fen_accepted_count"),
    }
    if not all(value is not None for value in before.values()):
        return {
            "status": "TRAINING_DATA_GAP",
            "message": "TRAINING_DATA_GAP: side-marker probe before/after requires matched baseline fixture counts.",
            "before": before,
            "after": after,
            "improvement": {
                "trusted_marker_count_delta": None,
                "marker_missing_count_delta": None,
                "marker_conflict_count_delta": None,
                "full_fen_accepted_count_delta": None,
            },
        }
    before_int = {key: int(value or 0) for key, value in before.items()}
    improvement = {
        "trusted_marker_count_delta": after["trusted_marker_count"] - before_int["trusted_marker_count"],
        "marker_missing_count_delta": after["marker_missing_count"] - before_int["marker_missing_count"],
        "marker_conflict_count_delta": after["marker_conflict_count"] - before_int["marker_conflict_count"],
        "full_fen_accepted_count_delta": after["full_fen_accepted_count"] - before_int["full_fen_accepted_count"],
    }
    status = (
        "improved"
        if improvement["trusted_marker_count_delta"] > 0
        or improvement["marker_missing_count_delta"] < 0
        or improvement["marker_conflict_count_delta"] < 0
        else "unchanged"
    )
    return {
        "status": status,
        "message": "Side-marker probe before/after counts are available.",
        "before": before_int,
        "after": after,
        "improvement": improvement,
    }


def _two_crop_quality_metrics_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    accuracy = report.get("accuracy") or {}
    probe_quality = report.get("probe_quality_before_after") or {}
    after_probe = probe_quality.get("after") or {}
    lines = [
        "# Chess FEN Two-Crop Quality Metrics",
        "",
        f"- diagrams: {summary.get('diagram_count', 0)}",
        f"- board crops: {summary.get('board_crop_count', 0)}",
        f"- side-marker crops: {summary.get('side_marker_crop_count', 0)}",
        f"- trusted markers: {summary.get('trusted_marker_count', 0)}",
        f"- marker missing: {summary.get('marker_missing_count', 0)}",
        f"- marker conflicts: {summary.get('marker_conflict_count', 0)}",
        f"- placement accepted: {summary.get('placement_accepted_count', 0)}",
        f"- full FEN accepted: {summary.get('full_fen_accepted_count', 0)}",
        f"- blocked by marker: {summary.get('blocked_by_marker_count', 0)}",
        f"- blocked by placement: {summary.get('blocked_by_placement_count', 0)}",
        "",
        "## Accuracy",
        "",
        f"- status: {accuracy.get('status', 'UNKNOWN')}",
        f"- human verified records: {accuracy.get('human_verified_record_count', 0)}",
        f"- side-to-move labels: {accuracy.get('marker_label_count', 0)}",
        f"- placement labels: {accuracy.get('placement_label_count', 0)}",
        f"- complete labels: {accuracy.get('both_label_count', 0)}",
        "",
        "## Probe Before/After",
        "",
        f"- status: {probe_quality.get('status', 'UNKNOWN')}",
        f"- message: {probe_quality.get('message', '')}",
        f"- after marker missing: {after_probe.get('marker_missing_count', 0)}",
        f"- after marker conflict: {after_probe.get('marker_conflict_count', 0)}",
        f"- after trusted marker: {after_probe.get('trusted_marker_count', 0)}",
        f"- after full FEN accepted: {after_probe.get('full_fen_accepted_count', 0)}",
        "",
        "## Top Reason Codes",
        "",
        "| Surface | Reason | Count |",
        "| --- | --- | ---: |",
    ]
    reason_rows = []
    for reason, count in (summary.get("by_board_crop_fail_reason") or {}).items():
        reason_rows.append(("board_crop", str(reason), int(count or 0)))
    for reason, count in (summary.get("by_marker_crop_fail_reason") or {}).items():
        reason_rows.append(("marker_crop", str(reason), int(count or 0)))
    for surface, reason, count in sorted(reason_rows, key=lambda item: (-item[2], item[0], item[1]))[:20]:
        lines.append(f"| {_md(surface)} | {_md(reason)} | {count} |")
    if not reason_rows:
        lines.append("| none | none | 0 |")
    if accuracy.get("status") == "TRAINING_DATA_GAP":
        lines.extend(["", f"TRAINING_DATA_GAP: {accuracy.get('message', '')}", "", "| Missing field | Needed | Available |", "| --- | --- | ---: |"])
        for item in accuracy.get("missing_data") or []:
            lines.append(
                "| {field} | {needed} | {available} |".format(
                    field=_md(str(item.get("field") or "")),
                    needed=_md(str(item.get("needed") or "")),
                    available=_md(str(item.get("available") or 0)),
                )
            )
    critical = [
        item
        for item in report.get("items") or []
        if item.get("manual_review_required") or item.get("board_crop_quality") == "fail" or item.get("marker_crop_quality") == "fail"
    ]
    lines.extend(
        [
            "",
            "## Critical Diagrams",
            "",
            "| Diagram | Page | Manual review reason | Board reasons | Marker reasons |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    if critical:
        for item in critical[:30]:
            lines.append(
                "| {id} | {page} | {review} | {board} | {marker} |".format(
                    id=_md(str(item.get("diagram_id") or "")),
                    page=_md(str(item.get("page") or "")),
                    review=_md(str(item.get("manual_review_reason") or "")),
                    board=_md(", ".join(str(reason) for reason in (item.get("board_crop_fail_reason") or []))),
                    marker=_md(", ".join(str(reason) for reason in (item.get("marker_crop_fail_reason") or []))),
                )
            )
    else:
        lines.append("| none |  |  |  |  |")
    lines.extend(
        [
            "",
            "| Diagram | Page | Board crop | Marker crop | Search crop | Board quality | Marker quality | Review crop | Marker status | Placement | Full FEN | Marker block | Placement block |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report.get("items") or []:
        lines.append(
            "| {id} | {page} | {board} | {marker} | {search} | {board_quality} | {marker_quality} | {review} | {marker_status} | {placement} | {full_fen} | {marker_block} | {placement_block} |".format(
                id=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                board="yes" if item.get("has_board_crop") else "no",
                marker="yes" if item.get("has_side_marker_crop") else "no",
                search="yes" if item.get("has_side_marker_search_crop") else "no",
                board_quality=_md(str(item.get("board_crop_quality") or "")),
                marker_quality=_md(str(item.get("marker_crop_quality") or "")),
                review=_md(str(item.get("side_marker_review_crop_kind") or "")),
                marker_status=_md(str(item.get("side_marker_status") or "")),
                placement=_md(str(item.get("placement_status") or "")),
                full_fen=_md(str(item.get("full_fen_status") or "")),
                marker_block="yes" if item.get("blocked_by_marker") else "no",
                placement_block="yes" if item.get("blocked_by_placement") else "no",
            )
        )
    return "\n".join(lines) + "\n"


TWO_CROP_BENCHMARK_MIN_RECORDS = 30
AI_ONLY_BENCHMARK_LABEL_SOURCES = {
    "ai",
    "ai_assist",
    "ai_candidate",
    "ai_review",
    "ai_review_only",
    "openai",
    "openai_review",
    "gpt",
}


def _benchmark_label_source(record: dict[str, Any]) -> str:
    source = str(
        _first_non_empty(
            record.get("label_source"),
            record.get("verification_source"),
            "human_verified_metadata" if _is_human_verified_record(record) else "",
            record.get("source"),
        )
    ).strip()
    return source or "unknown"


def _is_ai_only_benchmark_label_source(source: str) -> bool:
    normalized = _normalize_source_label(source).replace("-", "_")
    return normalized in AI_ONLY_BENCHMARK_LABEL_SOURCES


def _expected_side_to_move_label(record: dict[str, Any]) -> str:
    value = str(
        _first_non_empty(
            record.get("expected_side_to_move"),
            record.get("side_to_move_label"),
            record.get("side_to_move"),
        )
    ).strip().lower()
    if value in {"w", "white"}:
        return "w"
    if value in {"b", "black"}:
        return "b"
    return ""


def _expected_placement_label(record: dict[str, Any]) -> str:
    value = str(
        _first_non_empty(
            record.get("expected_placement"),
            record.get("expected_placement_fen"),
            record.get("placement"),
            record.get("placement_fen"),
            record.get("expected_fen"),
            record.get("fen"),
            record.get("full_fen"),
        )
    ).strip()
    if not value:
        return ""
    try:
        return placement_from_fen_or_placement(value)
    except Exception:
        return ""


def _two_crop_benchmark_seed_rows(diagrams: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    ai_only_excluded_count = 0
    for index, diagram in enumerate(diagrams, start=1):
        if not isinstance(diagram, dict):
            continue
        label_source = _benchmark_label_source(diagram)
        if _is_ai_only_benchmark_label_source(label_source):
            ai_only_excluded_count += 1
            continue
        if not _is_human_verified_record(diagram):
            continue
        board_crop_path = str(_first_non_empty(diagram.get("board_crop_path"))).strip()
        side_marker_crop_path = str(_first_non_empty(diagram.get("side_marker_crop_path"))).strip()
        expected_side_to_move = _expected_side_to_move_label(diagram)
        expected_placement = _expected_placement_label(diagram)
        if not board_crop_path or not side_marker_crop_path:
            continue
        if not expected_side_to_move and not expected_placement:
            continue
        rows.append(
            {
                "diagram_id": _diagram_record_id(diagram, index),
                "page": diagram.get("page") or diagram.get("page_number") or "",
                "board_crop_path": board_crop_path,
                "side_marker_crop_path": side_marker_crop_path,
                "expected_side_to_move": expected_side_to_move,
                "expected_placement": expected_placement,
                "label_source": label_source,
            }
        )
    return rows, ai_only_excluded_count


def _two_crop_benchmark_seed_report(diagrams: list[dict[str, Any]]) -> dict[str, Any]:
    rows, ai_only_excluded_count = _two_crop_benchmark_seed_rows(diagrams)
    marker_label_count = len([row for row in rows if row.get("expected_side_to_move")])
    placement_label_count = len([row for row in rows if row.get("expected_placement")])
    both_label_count = len([row for row in rows if row.get("expected_side_to_move") and row.get("expected_placement")])
    status = "READY" if len(rows) >= TWO_CROP_BENCHMARK_MIN_RECORDS else "TRAINING_DATA_GAP"
    source_counts = {
        source: len([row for row in rows if row.get("label_source") == source])
        for source in sorted({str(row.get("label_source") or "") for row in rows})
        if source
    }
    missing_data = []
    if status == "TRAINING_DATA_GAP":
        missing_data.append(
            {
                "field": "usable_human_verified_two_crop_records",
                "needed": TWO_CROP_BENCHMARK_MIN_RECORDS,
                "available": len(rows),
                "requires": [
                    "board_crop_path",
                    "side_marker_crop_path",
                    "human_verified label_source",
                    "expected_side_to_move or expected_placement",
                ],
            }
        )
    return {
        "schema": "kindlemaster.chess_fen.two_crop_benchmark_seed.v1",
        "status": status,
        "minimum_required_records": TWO_CROP_BENCHMARK_MIN_RECORDS,
        "summary": {
            "usable_record_count": len(rows),
            "manifest_record_count": len(rows) if status == "READY" else 0,
            "marker_label_count": marker_label_count,
            "placement_label_count": placement_label_count,
            "both_label_count": both_label_count,
            "ai_only_excluded_count": ai_only_excluded_count,
            "label_sources": source_counts,
        },
        "manifest": {
            "created": status == "READY",
            "items": rows if status == "READY" else [],
        },
        "available_records": rows,
        "training_data_gap": {
            "status": status,
            "message": "TRAINING_DATA_GAP: fewer than 30 usable human-verified two-crop records are available."
            if status == "TRAINING_DATA_GAP"
            else "",
            "missing_data": missing_data,
        },
    }


def _two_crop_benchmark_seed_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    gap = report.get("training_data_gap") or {}
    lines = [
        "# Chess FEN Two-Crop Benchmark Seed",
        "",
        f"- status: {report.get('status', 'UNKNOWN')}",
        f"- minimum required records: {report.get('minimum_required_records', TWO_CROP_BENCHMARK_MIN_RECORDS)}",
        f"- usable records: {summary.get('usable_record_count', 0)}",
        f"- manifest records: {summary.get('manifest_record_count', 0)}",
        f"- marker labels: {summary.get('marker_label_count', 0)}",
        f"- placement labels: {summary.get('placement_label_count', 0)}",
        f"- complete labels: {summary.get('both_label_count', 0)}",
        f"- AI-only labels excluded: {summary.get('ai_only_excluded_count', 0)}",
        "",
        "## Label Sources",
        "",
    ]
    sources = summary.get("label_sources") or {}
    if sources:
        for source, count in sources.items():
            lines.append(f"- {_md(str(source))}: {count}")
    else:
        lines.append("- none")
    if report.get("status") == "TRAINING_DATA_GAP":
        lines.extend(["", f"TRAINING_DATA_GAP: {gap.get('message', '')}", "", "| Missing field | Needed | Available |", "| --- | ---: | ---: |"])
        for item in gap.get("missing_data") or []:
            lines.append(
                "| {field} | {needed} | {available} |".format(
                    field=_md(str(item.get("field") or "")),
                    needed=_md(str(item.get("needed") or 0)),
                    available=_md(str(item.get("available") or 0)),
                )
            )
    lines.extend(
        [
            "",
            "| Diagram | Page | Board crop | Marker crop | Side label | Placement label | Source |",
            "| --- | ---: | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report.get("available_records") or []:
        lines.append(
            "| {id} | {page} | {board} | {marker} | {side} | {placement} | {source} |".format(
                id=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                board=_md(str(item.get("board_crop_path") or "")),
                marker=_md(str(item.get("side_marker_crop_path") or "")),
                side=_md(str(item.get("expected_side_to_move") or "")),
                placement=_md(str(item.get("expected_placement") or "")),
                source=_md(str(item.get("label_source") or "")),
            )
        )
    return "\n".join(lines) + "\n"


def _side_marker_assignment_report(diagrams: list[dict[str, Any]], fen_payload: dict[str, Any]) -> dict[str, Any]:
    fen_by_id = _fen_items_by_diagram_id(fen_payload)
    rows: list[dict[str, Any]] = []
    for index, diagram in enumerate(diagrams, start=1):
        if not isinstance(diagram, dict):
            continue
        diagram_id = _diagram_record_id(diagram, index)
        fen_item = fen_by_id.get(diagram_id, {})
        marker_status = str(diagram.get("side_marker_status") or fen_item.get("side_marker_status") or "marker_missing")
        marker_symbol = str(diagram.get("side_marker_symbol") or fen_item.get("side_marker_symbol") or "?")
        rows.append(
            {
                "diagram_id": diagram_id,
                "page": diagram.get("page") or diagram.get("page_number") or fen_item.get("page"),
                "source_image_path": diagram.get("image_path") or diagram.get("crop_path") or fen_item.get("source_image_path") or "",
                "side_to_move": str(diagram.get("side_to_move") or fen_item.get("side_to_move") or "unknown"),
                "side_marker_symbol": marker_symbol,
                "side_marker_status": marker_status,
                "side_marker_source": diagram.get("side_marker_source") or fen_item.get("side_marker_source") or "",
                "side_marker_bbox": diagram.get("side_marker_bbox") or fen_item.get("side_marker_bbox") or [],
                "side_marker_confidence": diagram.get("side_marker_confidence") or fen_item.get("side_marker_confidence") or "",
                "side_marker_assignment_trace": diagram.get("side_marker_assignment_trace") or fen_item.get("side_marker_assignment_trace") or {},
                "board_crop_path": diagram.get("board_crop_path") or fen_item.get("board_crop_path") or "",
                "side_marker_crop_path": diagram.get("side_marker_crop_path") or fen_item.get("side_marker_crop_path") or "",
                "marker_search_zone_preview_path": diagram.get("marker_search_zone_preview_path")
                or diagram.get("side_marker_search_crop_path")
                or fen_item.get("marker_search_zone_preview_path")
                or fen_item.get("side_marker_search_crop_path")
                or "",
                "debug_overlay_path": diagram.get("debug_overlay_path") or fen_item.get("debug_overlay_path") or "",
                "board_crop_quality": diagram.get("board_crop_quality") or fen_item.get("board_crop_quality") or "",
                "board_crop_fail_reason": diagram.get("board_crop_fail_reason") or fen_item.get("board_crop_fail_reason") or [],
                "marker_crop_quality": diagram.get("marker_crop_quality") or fen_item.get("marker_crop_quality") or "",
                "marker_crop_fail_reason": diagram.get("marker_crop_fail_reason") or fen_item.get("marker_crop_fail_reason") or [],
                "manual_review_required": bool(diagram.get("manual_review_required", fen_item.get("manual_review_required", True))),
                "manual_review_reason": diagram.get("manual_review_reason") or fen_item.get("manual_review_reason") or "",
                "runtime_status": fen_item.get("runtime_status") or diagram.get("runtime_status") or "",
                "placement_status": fen_item.get("placement_runtime_status") or diagram.get("placement_status") or "",
                "full_fen_status": fen_item.get("full_fen_runtime_status") or fen_item.get("runtime_status") or diagram.get("full_fen_status") or "",
                "fen_suppressed_reason": diagram.get("fen_suppressed_reason") or fen_item.get("fen_suppressed_reason") or "",
                "strict_fen_allowed": fen_item.get("runtime_status") == "FEN_MACHINE_ACCEPTED",
            }
        )
    summary = {
        "diagram_count": len(rows),
        "html_diagrams_with_visible_side_marker": len([row for row in rows if row.get("side_marker_symbol")]),
        "trusted_marker_assignments": len([row for row in rows if str(row.get("side_marker_status") or "").startswith("trusted_")]),
        "strict_full_fen_accepted": len([row for row in rows if row.get("strict_fen_allowed")]),
        "placement_accepted_count": len([row for row in rows if row.get("placement_status") == "FEN_PLACEMENT_MACHINE_ACCEPTED"]),
        "full_fen_accepted_count": len([row for row in rows if row.get("full_fen_status") in {"FEN_MACHINE_ACCEPTED", "FEN_CORPUS_VERIFIED"}]),
        "by_side_marker_status": {
            status: len([row for row in rows if row.get("side_marker_status") == status])
            for status in sorted({str(row.get("side_marker_status") or "") for row in rows})
            if status
        },
    }
    return {"schema": "kindlemaster.chess_fen.side_marker_assignment.v1", "summary": summary, "items": rows}


def _side_marker_assignment_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Chess FEN Side Marker Assignment",
        "",
        f"- diagrams: {summary.get('diagram_count', 0)}",
        f"- visible side markers: {summary.get('html_diagrams_with_visible_side_marker', 0)}",
        f"- trusted assignments: {summary.get('trusted_marker_assignments', 0)}",
        f"- placement accepted: {summary.get('placement_accepted_count', 0)}",
        f"- full FEN accepted: {summary.get('full_fen_accepted_count', 0)}",
        f"- strict full FEN accepted: {summary.get('strict_full_fen_accepted', 0)}",
        "",
        "| Diagram | Page | Marker | Status | Side | Placement | Full FEN | Runtime |",
        "| --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.get("items") or []:
        lines.append(
            "| {id} | {page} | {symbol} | {status} | {side} | {placement} | {full_fen} | {runtime} |".format(
                id=_md(str(item.get("diagram_id") or "")),
                page=_md(str(item.get("page") or "")),
                symbol=_md(str(item.get("side_marker_symbol") or "")),
                status=_md(str(item.get("side_marker_status") or "")),
                side=_md(str(item.get("side_to_move") or "")),
                placement=_md(str(item.get("placement_status") or "")),
                full_fen=_md(str(item.get("full_fen_status") or "")),
                runtime=_md(str(item.get("runtime_status") or "")),
            )
        )
    return "\n".join(lines) + "\n"


def _side_marker_assignment_html(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('diagram_id') or ''))}</td>"
        f"<td>{html.escape(str(item.get('page') or ''))}</td>"
        f"<td class='marker'>{html.escape(str(item.get('side_marker_symbol') or ''))}</td>"
        f"<td>{html.escape(str(item.get('side_marker_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('side_to_move') or ''))}</td>"
        f"<td>{html.escape(str(item.get('placement_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('full_fen_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('runtime_status') or ''))}</td>"
        f"<td>{html.escape(str(item.get('fen_suppressed_reason') or ''))}</td>"
        "</tr>"
        for item in report.get("items") or []
    ) or "<tr><td colspan='9'>No diagrams found.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Chess FEN Side Marker Assignment</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #d7dde6; padding: .45rem .55rem; text-align: left; }}
    th {{ background: #eef2f7; }}
    .marker {{ font-size: 1.35rem; font-weight: 700; }}
    .stats {{ display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }}
    .stat {{ border:1px solid #d7dde6; padding:.65rem .8rem; }}
  </style>
</head>
<body>
  <h1>Chess FEN Side Marker Assignment</h1>
  <section class="stats">
    <div class="stat">Diagrams: <strong>{html.escape(str(summary.get('diagram_count', 0)))}</strong></div>
    <div class="stat">Visible markers: <strong>{html.escape(str(summary.get('html_diagrams_with_visible_side_marker', 0)))}</strong></div>
    <div class="stat">Trusted: <strong>{html.escape(str(summary.get('trusted_marker_assignments', 0)))}</strong></div>
    <div class="stat">Placement accepted: <strong>{html.escape(str(summary.get('placement_accepted_count', 0)))}</strong></div>
    <div class="stat">Full FEN accepted: <strong>{html.escape(str(summary.get('full_fen_accepted_count', 0)))}</strong></div>
    <div class="stat">Strict FEN: <strong>{html.escape(str(summary.get('strict_full_fen_accepted', 0)))}</strong></div>
  </section>
  <table>
    <thead><tr><th>Diagram</th><th>Page</th><th>Marker</th><th>Status</th><th>Side</th><th>Placement</th><th>Full FEN</th><th>Runtime</th><th>Suppressed reason</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>"""


def _md(value: str) -> str:
    return value.replace("|", "\\|")


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
