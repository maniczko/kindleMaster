from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_blockers import categorize_blocker, sorted_category_counts
from chess_fen_hardening import fen_placement_to_square_map, validate_fen_detailed


MISSING_ARTIFACT = "MISSING_ARTIFACT"
AI_TIE_BREAK = "ai_tie_break_resolved"
EXCLUDED_AI_CATEGORIES = {"ai_consensus", "ai_best_effort", "ai_unreadable"}


def build_ai_tiebreak_fen_review_queue(
    ai_coverage_path: str | Path,
    current_report_path: str | Path,
) -> dict[str, Any]:
    ai_path = Path(ai_coverage_path)
    current_path = Path(current_report_path)
    ai_report = _load_json(ai_path)
    current_report = _load_json(current_path)
    current_by_id = _records_by_diagram_id(_extract_records(current_report))

    records: list[dict[str, Any]] = []
    by_category: dict[str, int] = {}
    skipped: dict[str, int] = {
        "ai_consensus": 0,
        "ai_best_effort": 0,
        "ai_unreadable": 0,
        "non_tiebreak": 0,
        "already_strict_accepted": 0,
    }

    for index, ai_record in enumerate(_extract_records(ai_report)):
        category = _ai_category(ai_record)
        if category in EXCLUDED_AI_CATEGORIES:
            skipped[category] += 1
            continue
        if category != AI_TIE_BREAK:
            skipped["non_tiebreak"] += 1
            continue

        diagram_id = _diagram_id(ai_record, index)
        current = current_by_id.get(diagram_id, {})
        if _strict_status(current) == "strict_accepted":
            skipped["already_strict_accepted"] += 1
            continue

        candidate_fens = _candidate_fens(ai_record)
        ai_selected_fen = _ai_selected_fen(ai_record, candidate_fens)
        blockers: list[str] = []
        status = "review_required"
        square_diffs: list[dict[str, str]] = []

        if len(candidate_fens) < 2:
            status = MISSING_ARTIFACT
            blockers.append("candidate_fens_missing")
        else:
            invalid_candidates = [fen for fen in candidate_fens if not _valid_full_fen(fen)]
            if invalid_candidates:
                status = MISSING_ARTIFACT
                blockers.append("candidate_fens_invalid")
            else:
                square_diffs = _square_conflicts(candidate_fens, ai_selected_fen)
                if not square_diffs:
                    status = MISSING_ARTIFACT
                    blockers.append("candidate_conflicts_missing")

        if ai_selected_fen and candidate_fens and ai_selected_fen not in candidate_fens:
            blockers.append("ai_selection_not_in_candidates")

        conflict_count = len(square_diffs)
        page = _first_non_empty(ai_record.get("page"), ai_record.get("page_number"), current.get("page"), current.get("page_number"), 0)
        record = {
            "diagram_id": diagram_id,
            "page": _int_or_zero(page),
            "crop_path": _crop_path(ai_record, current),
            "ai_selected_fen": ai_selected_fen,
            "candidate_fens": candidate_fens,
            "tie_break_reason": _tie_break_reason(ai_record),
            "square_diffs": square_diffs,
            "conflict_count": conflict_count,
            "requires_human_verification": True,
            "recommended_action": "verify_conflict_squares",
            "verification_priority": "fastest_to_verify" if 1 <= conflict_count <= 3 else "standard_review",
        }
        if status == MISSING_ARTIFACT:
            record["status"] = MISSING_ARTIFACT
            record["code"] = blockers[0] if blockers else "missing_artifact"
        if blockers:
            record["blockers"] = blockers
            record["blocker_items"] = [categorize_blocker(blocker) for blocker in blockers]
            for blocker in record["blocker_items"]:
                category_name = str(blocker.get("category") or "unknown")
                by_category[category_name] = by_category.get(category_name, 0) + 1
        records.append(record)

    records.sort(key=lambda item: (_artifact_sort(item), _int_or_zero(item.get("conflict_count")), _int_or_zero(item.get("page")), str(item.get("diagram_id") or "")))
    return {
        "schema": "kindlemaster.chess_fen.ai_tiebreak_review_queue.v1",
        "ai_coverage_path": str(ai_path),
        "current_report_path": str(current_path),
        "summary": {
            "queue_count": len(records),
            "ai_tie_break_count": len(records),
            "missing_artifact_count": sum(1 for item in records if item.get("status") == MISSING_ARTIFACT),
            "fastest_to_verify_count": sum(1 for item in records if item.get("verification_priority") == "fastest_to_verify"),
            "ai_selection_not_in_candidates_count": sum(
                1 for item in records if "ai_selection_not_in_candidates" in (item.get("blockers") or [])
            ),
            "by_category": sorted_category_counts(by_category),
            "skipped": skipped,
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
    sorted_records = sorted(
        [record for record in records if isinstance(record, Mapping)],
        key=lambda item: (_artifact_sort(item), _int_or_zero(item.get("conflict_count")), _int_or_zero(item.get("page")), str(item.get("diagram_id") or "")),
    )
    lines = [
        "# AI Tie-Break FEN Review Queue",
        "",
        f"AI coverage report: `{payload.get('ai_coverage_path', '')}`",
        f"Current FEN report: `{payload.get('current_report_path', '')}`",
        "",
        "## Summary",
        "",
        f"- Queue count: `{summary.get('queue_count', 0)}`",
        f"- Missing artifacts: `{summary.get('missing_artifact_count', 0)}`",
        f"- Fastest to verify (1-3 conflicts): `{summary.get('fastest_to_verify_count', 0)}`",
        "",
        "## Records",
        "",
        "| Priority | Diagram | Page | Conflicts | Crop | Blockers | Action |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for record in sorted_records:
        priority = str(record.get("verification_priority") or "")
        label = "FASTEST_TO_VERIFY" if priority == "fastest_to_verify" else priority or "standard_review"
        blockers = ", ".join(str(item) for item in record.get("blockers", []) or [])
        lines.append(
            "| `{priority}` | {diagram} | {page} | {conflicts} | `{crop}` | `{blockers}` | `{action}` |".format(
                priority=_md(label),
                diagram=_md(str(record.get("diagram_id") or "")),
                page=_md(str(record.get("page") or "")),
                conflicts=_int_or_zero(record.get("conflict_count")),
                crop=_md(str(record.get("crop_path") or "")),
                blockers=_md(blockers),
                action=_md(str(record.get("recommended_action") or "")),
            )
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a square-level review queue for AI tie-break FEN cases.")
    parser.add_argument("ai_coverage_json")
    parser.add_argument("current_report_json")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    try:
        payload = build_ai_tiebreak_fen_review_queue(args.ai_coverage_json, args.current_report_json)
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
        if value in {AI_TIE_BREAK, *EXCLUDED_AI_CATEGORIES}:
            return value
    return ""


def _candidate_fens(record: Mapping[str, Any]) -> list[str]:
    for key in ("candidate_fens", "fen_candidates", "candidates"):
        value = record.get(key)
        if not isinstance(value, list):
            continue
        result: list[str] = []
        for item in value:
            if isinstance(item, Mapping):
                fen = str(item.get("fen") or item.get("candidate_fen") or item.get("value") or "").strip()
            else:
                fen = str(item or "").strip()
            if fen:
                result.append(fen)
        if result:
            return result
    return []


def _ai_selected_fen(record: Mapping[str, Any], candidate_fens: list[str]) -> str:
    for key in ("ai_selected_fen", "selected_fen", "ai_fen", "selected_value", "candidate_fen", "fen"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return candidate_fens[0] if candidate_fens else ""


def _tie_break_reason(record: Mapping[str, Any]) -> str:
    for key in ("tie_break_reason", "ai_tie_break_reason", "reason"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    warnings = record.get("warnings")
    if isinstance(warnings, list) and warnings:
        return ", ".join(str(item) for item in warnings if str(item).strip())
    return AI_TIE_BREAK


def _square_conflicts(candidate_fens: list[str], ai_selected_fen: str) -> list[dict[str, str]]:
    maps = [fen_placement_to_square_map(fen) for fen in candidate_fens]
    selected_map = fen_placement_to_square_map(ai_selected_fen) if _valid_full_fen(ai_selected_fen) else {}
    conflicts: list[dict[str, str]] = []
    for square in maps[0]:
        pieces = [mapping.get(square, "") for mapping in maps]
        if len(set(pieces)) <= 1:
            continue
        conflicts.append(
            {
                "square": square,
                "candidate_a": pieces[0] or "empty",
                "candidate_b": pieces[1] or "empty",
                "ai_selected": selected_map.get(square, "") or "empty",
            }
        )
    return conflicts


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


def _valid_full_fen(value: str) -> bool:
    result = validate_fen_detailed(value)
    return bool(result.normalized_fen and not result.errors)


def _artifact_sort(record: Mapping[str, Any]) -> int:
    return 1 if record.get("status") == MISSING_ARTIFACT else 0


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
