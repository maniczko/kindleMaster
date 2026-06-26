from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import SQUARE_NAMES, compare_fen, fen_placement_to_square_map  # noqa: E402
from scripts.export_chess_fen_square_debug_artifacts import export_square_debug_artifacts  # noqa: E402

SCHEMA = "kindlemaster.chess_fen.square_debug_review_manifest.v1"
DEFAULT_CROP_ROOTS = (Path("reference_inputs/chess_fen/crops"),)


def build_square_debug_review_manifest(
    report_path: str | Path,
    *,
    output_dir: str | Path,
    crop_roots: Iterable[str | Path] = DEFAULT_CROP_ROOTS,
    top_n: int = 3,
) -> dict[str, Any]:
    report = Path(report_path)
    output = Path(output_dir)
    records = _extract_records(_load_json(report))
    roots = [Path(root) for root in crop_roots]
    cases: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        case_id = _case_id(record, index)
        case_dir = output / "cases" / _safe_slug(case_id)
        crop_path, crop_resolution = _resolve_crop_path(record, report_path=report, crop_roots=roots)
        squares = _square_rows_from_record(record)
        artifact_payload: dict[str, Any] | None = None
        if crop_path:
            artifact_payload = export_square_debug_artifacts(crop_path, squares, case_dir, case_id=case_id, top_n=top_n)
        cases.append(
            _case_manifest(
                record,
                index=index,
                source_report=report,
                case_id=case_id,
                case_dir=case_dir,
                crop_path=crop_path,
                crop_resolution=crop_resolution,
                squares=squares,
                artifact_payload=artifact_payload,
            )
        )

    payload = {
        "schema": SCHEMA,
        "status": "ok",
        "source_report": str(report),
        "output_dir": str(output),
        "case_count": len(cases),
        "available_crop_count": sum(1 for case in cases if case["board_crop"]["status"] == "available"),
        "unavailable_crop_count": sum(1 for case in cases if case["board_crop"]["status"] == "unavailable"),
        "square_entry_count": sum(len(case["squares"]) for case in cases),
        "unavailable_square_entry_count": sum(
            1 for case in cases for square in case["squares"] if square["square_crop"]["status"] == "unavailable"
        ),
        "ai_evidence_count": sum(1 for case in cases if case.get("ai_fen")),
        "cases": cases,
        "policy": {
            "artifact_only": True,
            "strict_acceptance": "not_modified_by_manifest_builder",
            "ai_evidence": "review_only",
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _case_manifest(
    record: Mapping[str, Any],
    *,
    index: int,
    source_report: Path,
    case_id: str,
    case_dir: Path,
    crop_path: Path | None,
    crop_resolution: dict[str, Any],
    squares: list[dict[str, Any]],
    artifact_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    candidate_fen = _candidate_fen(record)
    ai_fen = _ai_fen(record)
    artifact_squares = _artifact_square_rows(artifact_payload)
    square_entries = _square_entries(squares, artifact_squares, crop_available=bool(crop_path))
    grid_overlay = _available_file(artifact_payload, "grid_overlay_path") if artifact_payload else _unavailable("crop_unavailable")
    return {
        "case_id": case_id,
        "diagram_id": _diagram_id(record, index),
        "page": record.get("page", record.get("page_number")),
        "status": str(record.get("status") or ""),
        "runtime_status": str(record.get("runtime_status") or ""),
        "primary_blocker": str(record.get("primary_blocker") or ""),
        "primary_category": str(record.get("primary_category") or ""),
        "board_crop": _available_path(crop_path) if crop_path else _unavailable(crop_resolution.get("reason", "crop_path_unresolved")),
        "grid_overlay": grid_overlay,
        "squares": square_entries,
        "candidate_fen": candidate_fen,
        "selected_placement": str(record.get("selected_placement") or ""),
        "ai_fen": ai_fen,
        "candidate_diff": _candidate_diff(candidate_fen, ai_fen, record),
        "warnings": _string_list(record.get("warnings")),
        "blockers": _string_list(record.get("all_blockers") or record.get("blockers")),
        "provenance": {
            "source_report": str(source_report),
            "source_index": index,
            "crop_resolution": crop_resolution,
        },
        "strict_output": {
            "status": "not_promoted",
            "reason": "square_debug_artifact_only",
        },
    }


def _square_entries(
    source_squares: list[dict[str, Any]],
    artifact_squares: dict[str, Mapping[str, Any]],
    *,
    crop_available: bool,
) -> list[dict[str, Any]]:
    source_by_square = {str(row.get("square") or ""): row for row in source_squares}
    entries: list[dict[str, Any]] = []
    for square in SQUARE_NAMES:
        source = source_by_square.get(square, {})
        artifact = artifact_squares.get(square, {})
        square_path = str(artifact.get("square_crop_path") or "")
        entries.append(
            {
                "square": square,
                "piece": str(source.get("piece") or artifact.get("piece") or ""),
                "confidence": _availability_value(source.get("confidence")),
                "alternatives": _alternatives(source.get("alternatives") or artifact.get("alternatives")),
                "warnings": _string_list(source.get("warnings") or artifact.get("warnings")),
                "square_crop": _available_path(Path(square_path)) if square_path else _unavailable("square_crop_unavailable" if crop_available else "board_crop_unavailable"),
            }
        )
    return entries


def _square_rows_from_record(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    explicit = record.get("squares")
    if isinstance(explicit, list):
        rows = [dict(item) for item in explicit if isinstance(item, Mapping) and str(item.get("square") or "")]
        if rows:
            return _complete_square_rows(rows)
    placement = str(record.get("selected_placement") or "")
    if not placement:
        placement = _candidate_fen(record).split()[0] if _candidate_fen(record) else ""
    try:
        square_map = fen_placement_to_square_map(placement)
    except ValueError:
        square_map = {}
    return [
        {
            "square": square,
            "piece": square_map.get(square, ""),
            "confidence": record.get("confidence"),
            "alternatives": [],
            "warnings": _string_list(record.get("warnings")),
        }
        for square in SQUARE_NAMES
    ]


def _complete_square_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_square = {str(row.get("square") or ""): dict(row) for row in rows}
    return [{"square": square, **by_square.get(square, {})} for square in SQUARE_NAMES]


def _resolve_crop_path(
    record: Mapping[str, Any],
    *,
    report_path: Path,
    crop_roots: list[Path],
) -> tuple[Path | None, dict[str, Any]]:
    raw = str(record.get("crop_path") or record.get("source_crop_path") or "").strip()
    candidates: list[Path] = []
    if raw:
        raw_path = Path(raw)
        candidates.append(raw_path)
        if not raw_path.is_absolute():
            candidates.append(report_path.parent / raw_path)
            candidates.extend(root / raw_path for root in crop_roots)
    filename = _diagram_filename(record)
    if filename:
        candidates.extend(_candidate_crop_paths(filename, crop_roots))
    for candidate in candidates:
        if candidate.exists():
            return candidate, {"status": "resolved", "strategy": "existing_path_or_filename", "path": str(candidate)}
    return None, {"status": "unavailable", "reason": "crop_path_unresolved", "requested": raw or filename}


def _candidate_crop_paths(filename: str, crop_roots: list[Path]) -> list[Path]:
    stem = Path(filename).stem
    page_match = re.search(r"p(\d{3})", stem)
    index_match = re.search(r"_(\d{1,2})$", stem)
    patterns = [f"*{stem}*.png"]
    if page_match and index_match:
        page = page_match.group(1)
        index = int(index_match.group(1))
        patterns.extend([f"*p{page}*runtime_{index:02d}.png", f"*p{page}*c{index}.png", f"*p{page}*_{index:02d}.png"])
    matches: list[Path] = []
    for root in crop_roots:
        if not root.exists():
            continue
        for pattern in patterns:
            matches.extend(sorted(root.rglob(pattern)))
    return _dedupe_paths(matches)


def _candidate_diff(candidate_fen: str, ai_fen: str, record: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("square_diffs"), list):
        return {"source": "record.square_diffs", "square_diffs": record.get("square_diffs")}
    if candidate_fen and ai_fen:
        return {"source": "candidate_vs_ai", **compare_fen(candidate_fen, ai_fen)}
    return {"source": "unavailable", "reason": "second_candidate_missing", "square_diffs": []}


def _extract_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    roots: list[Any] = [payload]
    for key in ("quality_report", "chess_fen", "summary", "report"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            roots.append(value)
            nested = value.get("chess_fen")
            if isinstance(nested, Mapping):
                roots.append(nested)
    for root in roots:
        for key in ("items", "records", "cases", "diagrams", "review_items", "fen_candidates"):
            value = root.get(key) if isinstance(root, Mapping) else None
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _candidate_fen(record: Mapping[str, Any]) -> str:
    for key in ("selected_value", "candidate_fen", "full_fen", "fen"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _ai_fen(record: Mapping[str, Any]) -> str:
    for key in ("ai_fen", "ai_consensus_fen", "ai_candidate_fen", "ai_suggested_fen"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _case_id(record: Mapping[str, Any], index: int) -> str:
    return _diagram_id(record, index)


def _diagram_id(record: Mapping[str, Any], index: int) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return f"record:{index}"


def _diagram_filename(record: Mapping[str, Any]) -> str:
    for key in ("filename", "crop_filename"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    diagram_id = str(record.get("diagram_id") or "")
    return diagram_id.split(":", 1)[1] if ":" in diagram_id else ""


def _artifact_square_rows(payload: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return {}
    squares = payload.get("squares")
    if not isinstance(squares, list):
        return {}
    return {str(row.get("square") or ""): row for row in squares if isinstance(row, Mapping)}


def _available_file(payload: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    value = str(payload.get(key) or "").strip() if isinstance(payload, Mapping) else ""
    return _available_path(Path(value)) if value else _unavailable(f"{key}_missing")


def _available_path(path: Path | None) -> dict[str, Any]:
    return {"status": "available", "path": str(path) if path else ""}


def _unavailable(reason: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": str(reason or "unavailable")}


def _availability_value(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return _unavailable("confidence_unavailable")
    try:
        return {"status": "available", "value": round(float(value), 4)}
    except (TypeError, ValueError):
        return _unavailable("confidence_invalid")


def _alternatives(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        return [_unavailable("alternatives_unavailable")]
    return [dict(item) if isinstance(item, Mapping) else {"value": item} for item in value]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "case"


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            result.append(path)
            seen.add(key)
    return result


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a square-level FEN debug manifest for review diagnostics.")
    parser.add_argument("--report", default="reports/chess_fen/fundamenty_marker_rule_recovery_review_diagnostics.json")
    parser.add_argument("--output-dir", default="output/chess_fen/square_debug_review")
    parser.add_argument("--crop-root", action="append", default=[])
    parser.add_argument("--top-n", type=int, default=3)
    args = parser.parse_args(argv)
    roots = args.crop_root or [str(root) for root in DEFAULT_CROP_ROOTS]
    payload = build_square_debug_review_manifest(args.report, output_dir=args.output_dir, crop_roots=roots, top_n=args.top_n)
    print(json.dumps({key: payload[key] for key in ("schema", "status", "case_count", "available_crop_count", "unavailable_crop_count")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
