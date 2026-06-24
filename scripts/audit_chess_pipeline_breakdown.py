from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chess_fen_hardening import machine_accept_fen, validate_fen_detailed  # noqa: E402
from chess_pgn_extractor import (  # noqa: E402
    _is_exportable_pgn_record,
    annotate_records_with_replayed_fens,
    extract_chess_pgn_records_from_text,
)
from chess_position_recognizer import (  # noqa: E402
    board_crop_grid_diagnostics_from_image,
    recognize_chess_position_from_image,
    render_board_grid_overlay,
)
from scripts.validate_chess_audit_dataset import validate_chess_audit_dataset  # noqa: E402
from chess_board_geometry_cv import detect_board_quad_cv, render_cv_geometry_overlay, warp_board_quad_cv  # noqa: E402


DEFAULT_OUTPUT = Path("reports/chess_audit/latest")


def audit_chess_pipeline_breakdown(
    manifest_path: str | Path,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    dataset_dir = manifest_file.parent
    validation = validate_chess_audit_dataset(manifest_file)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig")) if manifest_file.exists() else {}
    fen_rows = _read_jsonl(dataset_dir / str(manifest.get("fen_ground_truth") or ""))
    pgn_rows = _read_jsonl(dataset_dir / str(manifest.get("pgn_ground_truth") or ""))
    negative_rows = _read_jsonl(dataset_dir / str(manifest.get("negative_samples") or ""))

    cases: list[dict[str, Any]] = []
    output = Path(output_dir)
    cases.extend(_audit_fen_case(row, dataset_dir=dataset_dir, output_dir=output) for row in fen_rows)
    cases.extend(_audit_pgn_case(row) for row in pgn_rows)
    cases.extend(_audit_negative_case(row, dataset_dir=dataset_dir) for row in negative_rows)

    fen_cases = [case for case in cases if case["case_type"] == "fen"]
    pgn_cases = [case for case in cases if case["case_type"] == "pgn"]
    negative_cases = [case for case in cases if case["case_type"] == "negative"]
    top_fen = Counter(case["top_fen_blocker"] for case in fen_cases if case.get("top_fen_blocker"))
    top_pgn = Counter(case["top_pgn_blocker"] for case in pgn_cases if case.get("top_pgn_blocker"))
    top_negative = Counter(case["top_negative_blocker"] for case in negative_cases if case.get("top_negative_blocker"))
    feasible_pgn = [case for case in pgn_cases if case.get("pgn_feasible")]

    summary = {
        "schema_version": "kindlemaster.chess_pipeline_audit.v1",
        "manifest_path": str(manifest_file),
        "dataset_validation_status": validation["status"],
        "dataset_validation_issue_count": validation["issue_count"],
        "dataset_release_readiness": validation.get("release_readiness", {}),
        "fen": {
            "case_count": len(fen_cases),
            "diagram_detected_count": sum(1 for case in fen_cases if case.get("diagram_detected")),
            "crop_present_count": sum(1 for case in fen_cases if case.get("crop_present")),
            "crop_correct_evidence_count": sum(1 for case in fen_cases if case.get("crop_correct_evidence_known")),
            "crop_correct_known_count": sum(1 for case in fen_cases if case.get("crop_correct_known")),
            "crop_problem_counts": dict(Counter(str(case.get("crop_problem_taxonomy") or "unknown") for case in fen_cases)),
            "grid_measured_count": sum(1 for case in fen_cases if case.get("grid_confidence") is not None),
            "grid_correct_known_count": sum(1 for case in fen_cases if case.get("grid_correct_known") is True),
            "grid_confidence_average": _average_float(case.get("grid_confidence") for case in fen_cases),
            "placement_exact_count": sum(1 for case in fen_cases if case.get("placement_exact")),
            "full_fen_syntax_valid_count": sum(1 for case in fen_cases if case.get("full_fen_syntax_valid")),
            "full_fen_legal_valid_count": sum(1 for case in fen_cases if case.get("full_fen_legal_valid")),
            "runtime_fen_present_count": sum(1 for case in fen_cases if case.get("runtime_fen_present")),
            "runtime_accepted_count": sum(1 for case in fen_cases if case.get("runtime_accepted")),
            "top_blockers": dict(top_fen),
        },
        "pgn": {
            "case_count": len(pgn_cases),
            "feasible_count": len(feasible_pgn),
            "infeasible_count": len(pgn_cases) - len(feasible_pgn),
            "ocr_text_present_count": sum(1 for case in feasible_pgn if case.get("ocr_text_present")),
            "candidate_blocks_found_count": sum(1 for case in feasible_pgn if int(case.get("candidate_blocks_found") or 0) > 0),
            "san_tokens_present_count": sum(1 for case in feasible_pgn if int(case.get("tokens_found") or 0) > 0),
            "san_token_count": sum(int(case.get("tokens_found") or 0) for case in feasible_pgn),
            "parse_clean_count": sum(1 for case in feasible_pgn if case.get("pgn_parse_clean")),
            "replay_legal_count": sum(1 for case in feasible_pgn if case.get("pgn_replay_legal")),
            "final_fen_present_count": sum(1 for case in feasible_pgn if case.get("final_fen_present")),
            "exportable_count": sum(1 for case in feasible_pgn if case.get("exportable_pgn")),
            "top_blockers": dict(top_pgn),
        },
        "negative": {
            "case_count": len(negative_cases),
            "evaluable_count": sum(1 for case in negative_cases if case.get("negative_evaluable")),
            "false_positive_candidate_count": sum(1 for case in negative_cases if case.get("false_positive_candidate")),
            "false_positive_runtime_count": sum(1 for case in negative_cases if case.get("false_positive_runtime")),
            "top_blockers": dict(top_negative),
        },
        "artifacts": {
            "audit_cases_jsonl": "audit_cases.jsonl",
            "audit_cases_csv": "audit_cases.csv",
            "overlays_dir": "overlays",
            "top_fen_blockers": "top_fen_blockers.json",
            "top_pgn_blockers": "top_pgn_blockers.json",
            "top_negative_blockers": "top_negative_blockers.json",
            "html_index": "html/index.html",
        },
    }

    _write_artifacts(output, summary, cases, top_fen, top_pgn, top_negative)
    return summary


def _audit_fen_case(row: dict[str, Any], *, dataset_dir: Path, output_dir: Path) -> dict[str, Any]:
    crop_path = dataset_dir / str(row.get("crop_path") or "")
    crop_present = crop_path.exists()
    crop_evidence_known, crop_correct_verified, crop_evidence = _crop_correctness_evidence(row)
    result: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {}
    cv_payload: dict[str, Any] = {}
    overlay_path = ""
    if crop_present:
        try:
            image_data = crop_path.read_bytes()
            diagnostics = board_crop_grid_diagnostics_from_image(image_data)
            overlay_target = output_dir / "overlays" / f"{_safe_id(str(row.get('id') or 'fen_case'))}_grid.png"
            overlay = render_board_grid_overlay(image_data, overlay_target)
            overlay_path = str(overlay.get("path") or "")
            cv_payload = _cv_geometry_payload(image_data, output_dir=output_dir, case_id=str(row.get("id") or "fen_case"))
            result = recognize_chess_position_from_image(image_data).to_dict()
        except Exception as exc:
            result = {
                "fen": "",
                "placement": "",
                "full_fen": "",
                "warnings": [f"recognizer_exception:{type(exc).__name__}"],
                "board_detected": False,
                "requires_review": True,
            }
    expected_placement = str(row.get("expected_placement") or "")
    generated_placement = str(result.get("placement") or "")
    full_fen = str(result.get("full_fen") or result.get("fen") or "")
    full_validation = validate_fen_detailed(full_fen) if full_fen else None
    machine = machine_accept_fen(result, context={}) if result else {"accepted": False, "blockers": []}
    warnings = [str(item) for item in result.get("warnings") or []]
    top_blocker = _fen_top_blocker(
        crop_exists=crop_path.exists(),
        board_detected=bool(result.get("board_detected")),
        placement_generated=generated_placement,
        placement_exact=bool(generated_placement and generated_placement == expected_placement),
        full_validation=full_validation,
        machine=machine,
        warnings=warnings,
    )
    return {
        "case_type": "fen",
        "id": row.get("id", ""),
        "source_pdf": row.get("source_pdf", ""),
        "page": row.get("page", 0),
        "diagram_detected": bool(result.get("board_detected")),
        "candidate_bbox": row.get("crop_expected_bbox"),
        "crop_path": str(crop_path),
        "crop_present": crop_present,
        "crop_correct_evidence_known": crop_evidence_known,
        "crop_correct_known": crop_correct_verified,
        "crop_correct_evidence": crop_evidence,
        "board_visual_pattern_detected": bool(result.get("board_detected")),
        "normalization_variant": diagnostics.get("normalization_variant", ""),
        "original_size": diagnostics.get("original_size"),
        "normalized_size": diagnostics.get("normalized_size"),
        "board_signal": diagnostics.get("board_signal", 0.0),
        "grid_confidence": diagnostics.get("grid_confidence", result.get("confidence", 0.0)),
        "crop_box_used": diagnostics.get("crop_box_used"),
        "crop_problem_taxonomy": diagnostics.get("crop_problem_taxonomy", "unknown"),
        "overlay_path": overlay_path,
        "cv_geometry": cv_payload if crop_present else {},
        "grid_correct_known": None,
        "placement_generated": generated_placement,
        "placement_exact": bool(generated_placement and generated_placement == expected_placement),
        "full_fen_generated": full_fen,
        "full_fen_syntax_valid": bool(full_validation and full_validation.is_syntax_valid),
        "full_fen_legal_valid": bool(full_validation and full_validation.is_legal_position),
        "runtime_fen_present": bool(result.get("fen")),
        "runtime_accepted": bool(machine.get("accepted")),
        "machine_acceptance_status": "accepted" if machine.get("accepted") else "rejected",
        "top_fen_blocker": top_blocker,
        "warnings": sorted(set([*warnings, *[str(item) for item in diagnostics.get("warnings") or []]])),
    }


def _crop_correctness_evidence(row: dict[str, Any]) -> tuple[bool, bool, str]:
    """Return explicit crop-geometry review evidence, not inferred crop availability."""
    boolean_fields = ("crop_correct", "crop_verified_correct", "crop_geometry_correct")
    for field in boolean_fields:
        if field in row:
            value = _coerce_bool(row.get(field))
            if value is not None:
                return True, value, field

    status_fields = ("crop_status", "crop_review_status", "crop_geometry_status")
    positive_values = {"correct", "verified_correct", "accepted", "pass", "passed"}
    negative_values = {"incorrect", "wrong", "bad", "failed", "needs_review", "review"}
    for field in status_fields:
        if field not in row:
            continue
        value = str(row.get(field) or "").strip().lower()
        if value in positive_values:
            return True, True, field
        if value in negative_values:
            return True, False, field
    return False, False, ""


def _cv_geometry_payload(image_data: bytes, *, output_dir: Path, case_id: str) -> dict[str, Any]:
    if os.environ.get("KINDLEMASTER_CHESS_CV_GEOMETRY", "").strip() not in {"1", "true", "TRUE", "yes", "on"}:
        return {"enabled": False}
    result = detect_board_quad_cv(image_data)
    payload = {"enabled": True, **result.to_dict()}
    overlay_target = output_dir / "overlays" / f"{_safe_id(case_id)}_cv_geometry.png"
    payload["overlay"] = render_cv_geometry_overlay(image_data, result, overlay_target)
    if result.found and result.quad:
        warped = warp_board_quad_cv(image_data, result.quad)
        if warped is not None:
            warped_target = output_dir / "overlays" / f"{_safe_id(case_id)}_cv_warp.png"
            warped.save(warped_target)
            payload["warped_path"] = str(warped_target)
    return payload


def _average_float(values: Any) -> float | None:
    numeric_values: list[float] = []
    for value in values:
        try:
            numeric_values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numeric_values:
        return None
    return round(sum(numeric_values) / len(numeric_values), 4)


def _audit_pgn_case(row: dict[str, Any]) -> dict[str, Any]:
    feasible = bool(row.get("pgn_feasible"))
    reason = str(row.get("pgn_feasibility_reason") or "")
    text = str(row.get("expected_pgn") or row.get("expected_movetext") or "")
    if not feasible:
        return {
            "case_type": "pgn",
            "id": row.get("id", ""),
            "source_pdf": row.get("source_pdf", ""),
            "page": row.get("page", 0),
            "pgn_feasible": False,
            "pgn_feasibility_reason": reason,
            "ocr_text_present": bool(text.strip()),
            "candidate_blocks_found": 0,
            "tokens_found": 0,
            "halfmove_count": 0,
            "pgn_generated": False,
            "pgn_parse_clean": False,
            "pgn_replay_legal": False,
            "final_fen_present": False,
            "exportable_pgn": False,
            "top_pgn_blocker": f"pgn_infeasible:{reason or 'unspecified'}",
            "warnings": [],
        }
    records = annotate_records_with_replayed_fens(extract_chess_pgn_records_from_text(text, page_num=int(row.get("page") or 0)))
    warnings = sorted({warning for record in records for warning in record.warnings})
    replay_legal = [record for record in records if record.final_fen]
    exportable = [record for record in records if _is_exportable_pgn_record(record)]
    halfmoves = sum(max(0, len(record.fen_snapshots)) for record in records)
    top_blocker = _pgn_top_blocker(records=records, text=text, warnings=warnings, exportable=bool(exportable))
    return {
        "case_type": "pgn",
        "id": row.get("id", ""),
        "source_pdf": row.get("source_pdf", ""),
        "page": row.get("page", 0),
        "pgn_feasible": True,
        "pgn_feasibility_reason": reason,
        "ocr_text_present": bool(text.strip()),
        "candidate_blocks_found": len(records),
        "tokens_found": sum(len(record.token_source) for record in records),
        "halfmove_count": halfmoves,
        "pgn_generated": bool(records),
        "pgn_parse_clean": bool(records and "pgn_parse_failed" not in warnings),
        "pgn_replay_legal": bool(replay_legal),
        "final_fen_present": any(bool(record.final_fen) for record in records),
        "exportable_pgn": bool(exportable),
        "top_pgn_blocker": top_blocker,
        "warnings": warnings,
    }


def _audit_negative_case(row: dict[str, Any], *, dataset_dir: Path) -> dict[str, Any]:
    crop_path_value = str(row.get("crop_path") or "").strip()
    crop_path = dataset_dir / crop_path_value if crop_path_value else None
    result: dict[str, Any] = {}
    warnings: list[str] = []
    if crop_path and crop_path.exists():
        try:
            result = recognize_chess_position_from_image(crop_path.read_bytes()).to_dict()
        except Exception as exc:
            warnings.append(f"recognizer_exception:{type(exc).__name__}")
            result = {"fen": "", "placement": "", "full_fen": "", "board_detected": False, "requires_review": True}
    false_positive_candidate = bool(
        result.get("board_detected")
        or result.get("placement")
        or result.get("full_fen")
        or result.get("fen")
    )
    false_positive_runtime = bool(result.get("fen"))
    top_blocker = _negative_top_blocker(
        has_crop_path=bool(crop_path_value),
        crop_exists=bool(crop_path and crop_path.exists()),
        false_positive_candidate=false_positive_candidate,
        false_positive_runtime=false_positive_runtime,
    )
    return {
        "case_type": "negative",
        "id": row.get("id", ""),
        "source_pdf": row.get("source_pdf", ""),
        "page": row.get("page", 0),
        "negative_reason": row.get("reason", ""),
        "crop_path": str(crop_path) if crop_path else "",
        "negative_evaluable": bool(crop_path and crop_path.exists()),
        "board_visual_pattern_detected": bool(result.get("board_detected")),
        "placement_generated": str(result.get("placement") or ""),
        "full_fen_generated": str(result.get("full_fen") or result.get("fen") or ""),
        "runtime_fen_present": bool(result.get("fen")),
        "false_positive_candidate": false_positive_candidate,
        "false_positive_runtime": false_positive_runtime,
        "top_negative_blocker": top_blocker,
        "warnings": sorted(set([*warnings, *[str(item) for item in result.get("warnings") or []]])),
    }


def _fen_top_blocker(
    *,
    crop_exists: bool,
    board_detected: bool,
    placement_generated: str,
    placement_exact: bool,
    full_validation: Any,
    machine: dict[str, Any],
    warnings: list[str],
) -> str:
    if not crop_exists:
        return "crop_missing"
    if not board_detected:
        return "diagram_not_detected"
    if not placement_generated:
        return "placement_missing"
    if not placement_exact:
        return "placement_mismatch"
    if not full_validation or not full_validation.is_syntax_valid:
        return "full_fen_syntax_invalid"
    if not full_validation.is_legal_position:
        return "full_fen_position_invalid"
    if not machine.get("accepted"):
        blockers = machine.get("blockers") or []
        if blockers:
            return str(blockers[0].get("code") or "machine_acceptance_rejected")
        if warnings:
            return warnings[0]
        return "machine_acceptance_rejected"
    return ""


def _pgn_top_blocker(*, records: list[Any], text: str, warnings: list[str], exportable: bool) -> str:
    if exportable:
        return ""
    if not text.strip():
        return "ocr_text_missing"
    if not records:
        return "candidate_blocks_missing"
    for blocker in (
        "unmapped_chess_glyphs",
        "illegal_san_token",
        "ambiguous_san_token",
        "pgn_replay_errors",
        "pgn_parse_failed",
        "pgn_no_legal_moves",
        "side_to_move_mismatch",
        "move_number_jump",
    ):
        if blocker in warnings:
            return blocker
    return warnings[0] if warnings else "not_exportable"


def _negative_top_blocker(
    *,
    has_crop_path: bool,
    crop_exists: bool,
    false_positive_candidate: bool,
    false_positive_runtime: bool,
) -> str:
    if not has_crop_path:
        return "negative_crop_missing"
    if not crop_exists:
        return "negative_crop_missing_on_disk"
    if false_positive_runtime:
        return "negative_runtime_false_positive"
    if false_positive_candidate:
        return "negative_candidate_false_positive_review_only"
    return "negative_correctly_rejected"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _safe_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value.strip())
    return safe or "case"


def _write_artifacts(
    output: Path,
    summary: dict[str, Any],
    cases: list[dict[str, Any]],
    top_fen: Counter[str],
    top_pgn: Counter[str],
    top_negative: Counter[str],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "html").mkdir(exist_ok=True)
    (output / "audit_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "top_fen_blockers.json").write_text(json.dumps(dict(top_fen), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "top_pgn_blockers.json").write_text(json.dumps(dict(top_pgn), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "top_negative_blockers.json").write_text(json.dumps(dict(top_negative), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "audit_cases.jsonl").write_text(
        "".join(json.dumps(case, sort_keys=True) + "\n" for case in cases),
        encoding="utf-8",
    )
    _write_cases_csv(output / "audit_cases.csv", cases)
    (output / "html" / "index.html").write_text(_html_report(summary, cases), encoding="utf-8")


def _write_cases_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    fields = sorted({key for case in cases for key in case.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in cases:
            writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value for key, value in case.items()})


def _html_report(summary: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    fen = summary.get("fen") or {}
    pgn = summary.get("pgn") or {}
    negative = summary.get("negative") or {}
    release_readiness = summary.get("dataset_release_readiness") or {}
    artifacts = summary.get("artifacts") or {}
    rows = "\n".join(
        "<tr>"
        f"<td>{_html_escape(case.get('case_type',''))}</td>"
        f"<td>{_html_escape(case.get('id',''))}</td>"
        f"<td>{_html_escape(case.get('top_fen_blocker') or case.get('top_pgn_blocker') or case.get('top_negative_blocker') or '')}</td>"
        "</tr>"
        for case in cases
    )
    artifact_links = "\n".join(
        f'<li><a href="../{_html_escape(str(path))}">{_html_escape(str(name))}</a></li>'
        for name, path in sorted(artifacts.items())
        if name != "html_index"
    )
    return f"""<!doctype html>
<html lang=\"en\">
<meta charset=\"utf-8\">
<title>Chess Pipeline Audit</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.45; color: #17202a; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }}
th, td {{ border: 1px solid #d9dee7; padding: 0.45rem 0.6rem; text-align: left; vertical-align: top; }}
th {{ background: #eef3f8; }}
.grid {{ display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
.card {{ border: 1px solid #d9dee7; border-radius: 10px; padding: 1rem; background: #fbfcfe; }}
.note {{ color: #5b6472; }}
code {{ background: #edf2f7; padding: 0.1rem 0.25rem; border-radius: 4px; }}
pre {{ white-space: pre-wrap; background: #111827; color: #f9fafb; padding: 1rem; overflow: auto; }}
</style>
<body>
<h1>Chess Pipeline Audit</h1>
<p class=\"note\">FEN and PGN are measured separately. <code>diagram_only</code> cases are not counted as PGN failures.</p>
<section class=\"card\">
<h2>Dataset release readiness</h2>
{_html_metric_table([
    ("schema validation", summary.get("dataset_validation_status", "")),
    ("validation issues", summary.get("dataset_validation_issue_count", 0)),
    ("release status", release_readiness.get("status", "")),
    ("accepted for release proof", release_readiness.get("accepted_for_release_proof", False)),
])}
{_html_readiness_blockers(release_readiness.get("blockers") or [])}
</section>
<div class=\"grid\">
<section class=\"card\">
<h2>FEN funnel</h2>
{_html_metric_table([
    ("cases", fen.get("case_count", 0)),
    ("diagram detected", fen.get("diagram_detected_count", 0)),
    ("crop present", fen.get("crop_present_count", 0)),
    ("crop correctness evidence", fen.get("crop_correct_evidence_count", 0)),
    ("crop correct verified", fen.get("crop_correct_known_count", 0)),
    ("grid correct / measured", f"{fen.get('grid_correct_known_count', 0)} / {fen.get('grid_measured_count', 0)}"),
    ("avg grid confidence", fen.get("grid_confidence_average", "")),
    ("placement exact", fen.get("placement_exact_count", 0)),
    ("full FEN syntax-valid", fen.get("full_fen_syntax_valid_count", 0)),
    ("full FEN legal-valid", fen.get("full_fen_legal_valid_count", 0)),
    ("runtime FEN present", fen.get("runtime_fen_present_count", 0)),
    ("runtime accepted", fen.get("runtime_accepted_count", 0)),
])}
</section>
<section class=\"card\">
<h2>PGN funnel</h2>
{_html_metric_table([
    ("cases", pgn.get("case_count", 0)),
    ("feasible", pgn.get("feasible_count", 0)),
    ("infeasible", pgn.get("infeasible_count", 0)),
    ("OCR text present", pgn.get("ocr_text_present_count", 0)),
    ("candidate blocks", pgn.get("candidate_blocks_found_count", 0)),
    ("SAN token cases / total", f"{pgn.get('san_tokens_present_count', 0)} / {pgn.get('san_token_count', 0)}"),
    ("parse clean", pgn.get("parse_clean_count", 0)),
    ("replay legal", pgn.get("replay_legal_count", 0)),
    ("final FEN", pgn.get("final_fen_present_count", 0)),
    ("exportable", pgn.get("exportable_count", 0)),
])}
</section>
<section class=\"card\">
<h2>Negative samples</h2>
{_html_metric_table([
    ("cases", negative.get("case_count", 0)),
    ("evaluable", negative.get("evaluable_count", 0)),
    ("candidate false positives", negative.get("false_positive_candidate_count", 0)),
    ("runtime false positives", negative.get("false_positive_runtime_count", 0)),
])}
</section>
</div>
<h2>Top blockers</h2>
<div class=\"grid\">
<section class=\"card\"><h3>FEN</h3>{_html_blocker_list(fen.get("top_blockers") or {})}</section>
<section class=\"card\"><h3>PGN</h3>{_html_blocker_list(pgn.get("top_blockers") or {})}</section>
<section class=\"card\"><h3>Negative</h3>{_html_blocker_list(negative.get("top_blockers") or {})}</section>
</div>
<h2>Artifacts</h2>
<ul>{artifact_links}</ul>
<h2>Cases</h2>
<table>
<thead><tr><th>Type</th><th>ID</th><th>Top blocker</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<h2>Raw summary</h2>
<pre>{_html_escape(json.dumps(summary, indent=2, sort_keys=True))}</pre>
</body>
</html>
"""


def _html_escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _html_metric_table(rows: list[tuple[str, Any]]) -> str:
    body = "\n".join(f"<tr><th>{_html_escape(label)}</th><td>{_html_escape(value)}</td></tr>" for label, value in rows)
    return f"<table><tbody>{body}</tbody></table>"


def _html_blocker_list(blockers: dict[str, Any]) -> str:
    if not blockers:
        return '<p class=\"note\">none reported</p>'
    items = "\n".join(f"<li><code>{_html_escape(name)}</code>: {_html_escape(count)}</li>" for name, count in sorted(blockers.items()))
    return f"<ul>{items}</ul>"


def _html_readiness_blockers(blockers: list[Any]) -> str:
    if not blockers:
        return '<p class=\"note\">release-readiness blockers: none</p>'
    items = "\n".join(
        f"<li><code>{_html_escape((blocker or {}).get('code', 'blocker'))}</code>: {_html_escape((blocker or {}).get('message', ''))}</li>"
        for blocker in blockers
        if isinstance(blocker, dict)
    )
    return f"<h3>Release-readiness blockers</h3><ul>{items}</ul>"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit chess FEN/PGN pipeline stages on a diagnostic dataset.")
    parser.add_argument("manifest")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    summary = audit_chess_pipeline_breakdown(args.manifest, output_dir=args.output)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["dataset_validation_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
