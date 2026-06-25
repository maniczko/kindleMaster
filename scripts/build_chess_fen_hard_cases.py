from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


MISSING_ARTIFACT = "MISSING_ARTIFACT"
HARD_AI_CATEGORIES = {"ai_unreadable", "ai_best_effort"}
EXCLUDED_AI_CATEGORIES = {"ai_consensus", "ai_tie_break_resolved"}


def build_chess_fen_hard_cases(
    ai_coverage_path: str | Path,
    current_report_path: str | Path,
) -> dict[str, Any]:
    ai_path = Path(ai_coverage_path)
    current_path = Path(current_report_path)
    ai_report = _load_json(ai_path)
    current_report = _load_json(current_path)
    current_by_id = _records_by_diagram_id(_extract_records(current_report))

    records: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    by_category: Counter[str] = Counter()

    for index, ai_record in enumerate(_extract_records(ai_report)):
        ai_category = _ai_category(ai_record)
        if ai_category in EXCLUDED_AI_CATEGORIES:
            skipped[ai_category] += 1
            continue
        if ai_category not in HARD_AI_CATEGORIES:
            skipped["non_hard_case"] += 1
            continue

        diagram_id = _diagram_id(ai_record, index)
        current = current_by_id.get(diagram_id, {})
        crop_path = _crop_path(ai_record, current)
        primary_blocker = _primary_blocker(ai_record, current, crop_path)
        primary_category = _primary_category(primary_blocker, ai_record, current)
        hard_case_type = _hard_case_type(ai_category, crop_path, primary_blocker, primary_category)
        recommended_action = _recommended_action(hard_case_type)

        record = {
            "diagram_id": diagram_id,
            "page": _int_or_zero(_first_non_empty(ai_record.get("page"), ai_record.get("page_number"), current.get("page"), current.get("page_number"), 0)),
            "ai_category": ai_category,
            "crop_path": crop_path,
            "current_strict_status": _strict_status(current),
            "primary_blocker": primary_blocker,
            "primary_category": primary_category,
            "hard_case_type": hard_case_type,
            "requires_manual_label": True,
            "requires_crop_repair": hard_case_type in {"crop_missing", "grid_failed", "low_resolution"},
            "requires_source_image_review": True,
            "recommended_action": recommended_action,
            "normal_metrics_segment": "excluded_hard_case",
        }
        if crop_path == MISSING_ARTIFACT:
            record["status"] = MISSING_ARTIFACT
            record["code"] = "crop_path_missing"
        records.append(record)
        by_type[hard_case_type] += 1
        by_category[ai_category] += 1

    records.sort(key=lambda item: (_int_or_zero(item.get("page")), str(item.get("diagram_id") or "")))
    return {
        "schema": "kindlemaster.chess_fen.hard_cases.v1",
        "ai_coverage_path": str(ai_path),
        "current_report_path": str(current_path),
        "summary": {
            "hard_case_count": len(records),
            "by_ai_category": dict(sorted(by_category.items())),
            "by_hard_case_type": dict(sorted(by_type.items())),
            "missing_artifact_count": sum(1 for item in records if item.get("status") == MISSING_ARTIFACT),
            "excluded_from_normal_metrics": True,
            "skipped": dict(sorted(skipped.items())),
        },
        "records": records,
    }


def write_jsonl(payload: Mapping[str, Any], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    output.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def write_markdown(payload: Mapping[str, Any], output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), Mapping) else {}
    records = payload.get("records") if isinstance(payload.get("records"), list) else []
    lines = [
        "# Chess FEN Hard Cases",
        "",
        f"AI coverage report: `{payload.get('ai_coverage_path', '')}`",
        f"Current FEN report: `{payload.get('current_report_path', '')}`",
        "",
        "## Summary",
        "",
        f"- Hard cases: `{summary.get('hard_case_count', 0)}`",
        f"- Missing artifacts: `{summary.get('missing_artifact_count', 0)}`",
        f"- Excluded from normal recognizer metrics: `{summary.get('excluded_from_normal_metrics', False)}`",
        "",
        "## By Type",
        "",
    ]
    for hard_type, count in (summary.get("by_hard_case_type") or {}).items():
        lines.append(f"- `{_md(str(hard_type))}`: {count}")
    lines.extend(
        [
            "",
            "## Records",
            "",
            "| Diagram | Page | AI category | Type | Crop | Action |",
            "|---|---:|---|---|---|---|",
        ]
    )
    for record in records:
        if not isinstance(record, Mapping):
            continue
        lines.append(
            "| {diagram} | {page} | `{ai}` | `{hard_type}` | `{crop}` | `{action}` |".format(
                diagram=_md(str(record.get("diagram_id") or "")),
                page=_md(str(record.get("page") or "")),
                ai=_md(str(record.get("ai_category") or "")),
                hard_type=_md(str(record.get("hard_case_type") or "")),
                crop=_md(str(record.get("crop_path") or "")),
                action=_md(str(record.get("recommended_action") or "")),
            )
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a dedicated hard-case dataset for unreadable/best-effort chess FEN AI cases.")
    parser.add_argument("ai_coverage_json")
    parser.add_argument("current_report_json")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    try:
        payload = build_chess_fen_hard_cases(args.ai_coverage_json, args.current_report_json)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": exc.__class__.__name__, "message": str(exc)}, indent=2), file=sys.stderr)
        return 2

    write_jsonl(payload, args.output_jsonl)
    write_markdown(payload, args.output_md)
    if args.output_json:
        output_json = Path(args.output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", **payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_records(report: Any) -> list[Mapping[str, Any]]:
    if not isinstance(report, Mapping):
        return []
    roots: list[Any] = [report]
    for key in ("quality_report", "quality", "summary", "chess_fen"):
        value = report.get(key)
        if isinstance(value, Mapping):
            roots.append(value)
            chess_fen = value.get("chess_fen")
            if isinstance(chess_fen, Mapping):
                roots.append(chess_fen)
    for root in roots:
        if not isinstance(root, Mapping):
            continue
        for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
            value = root.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _records_by_diagram_id(records: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {_diagram_id(record, index): record for index, record in enumerate(records)}


def _diagram_id(record: Mapping[str, Any], index: int) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    page = _first_non_empty(record.get("page"), record.get("page_number"), "")
    filename = str(record.get("filename") or record.get("crop_filename") or Path(str(record.get("crop_path") or "")).name).strip()
    if page not in ("", None) and filename:
        return f"p{page}:{filename}"
    return f"record:{index}"


def _ai_category(record: Mapping[str, Any]) -> str:
    for key in ("ai_category", "category", "status", "runtime_status", "source", "method"):
        value = str(record.get(key) or "").strip().lower()
        if value in {*HARD_AI_CATEGORIES, *EXCLUDED_AI_CATEGORIES}:
            return value
    return ""


def _crop_path(*records: Mapping[str, Any]) -> str:
    for record in records:
        for key in ("crop_path", "source_crop_path", "image_path", "artifact_path"):
            value = str(record.get(key) or "").strip()
            if value:
                return value
        filename = str(record.get("filename") or record.get("crop_filename") or "").strip()
        if filename:
            return f"reference_inputs/chess_fen/crops/{filename}"
    return MISSING_ARTIFACT


def _primary_blocker(ai_record: Mapping[str, Any], current_record: Mapping[str, Any], crop_path: str) -> str:
    if crop_path == MISSING_ARTIFACT:
        return "crop_path_missing"
    for record in (current_record, ai_record):
        blockers = _blockers(record)
        if blockers:
            return blockers[0]
    category = _ai_category(ai_record)
    return category or "unknown"


def _blockers(record: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("primary_blocker", "blockers", "blocking_warnings", "errors", "warnings"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def _primary_category(primary_blocker: str, ai_record: Mapping[str, Any], current_record: Mapping[str, Any]) -> str:
    explicit = str(current_record.get("primary_category") or ai_record.get("primary_category") or "").strip()
    if explicit:
        return explicit
    text = " ".join([primary_blocker, *_blockers(ai_record), *_blockers(current_record)]).lower()
    if any(token in text for token in ("crop_missing", "crop_path_missing", "missing_artifact")):
        return "crop_missing"
    if any(token in text for token in ("grid", "board_not_detected", "bbox", "partial_board")):
        return "grid_failed"
    if any(token in text for token in ("low_resolution", "blur", "resolution", "pixel")):
        return "low_resolution"
    if any(token in text for token in ("ambiguous", "piece", "queen", "king_count", "recognition")):
        return "ambiguous_piece"
    return "unknown"


def _hard_case_type(ai_category: str, crop_path: str, primary_blocker: str, primary_category: str) -> str:
    if crop_path == MISSING_ARTIFACT:
        return "crop_missing"
    if primary_category in {"crop_missing", "grid_failed", "low_resolution", "ambiguous_piece"}:
        return primary_category
    if ai_category == "ai_unreadable":
        return "unreadable"
    if ai_category == "ai_best_effort":
        return "best_effort"
    if "crop" in primary_blocker:
        return "crop_missing"
    return "unknown"


def _recommended_action(hard_case_type: str) -> str:
    return {
        "unreadable": "source_image_review",
        "best_effort": "manual_label_required",
        "crop_missing": "repair_or_regenerate_crop",
        "grid_failed": "inspect_crop_grid_geometry",
        "low_resolution": "inspect_source_resolution_or_rescan",
        "ambiguous_piece": "manual_square_label_review",
        "unknown": "investigate_raw_record",
    }.get(hard_case_type, "investigate_raw_record")


def _strict_status(record: Mapping[str, Any]) -> str:
    if not record:
        return "missing_current_record"
    status_blob = " ".join(
        str(record.get(key) or "").strip().lower()
        for key in ("status", "runtime_status", "method", "source", "label_source")
    )
    selected = str(record.get("selected_value") or "").strip()
    if record.get("requires_review") is False and len(selected.split()) == 6:
        return "strict_accepted"
    if "fen_machine_accepted" in status_blob or "verified_exact_crop_label_used" in status_blob:
        return "strict_accepted"
    if record.get("requires_review") is True:
        return "requires_review"
    return str(record.get("runtime_status") or record.get("status") or "unknown")


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _md(value: str) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
