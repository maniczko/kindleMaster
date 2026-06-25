from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import (
    compare_fen,
    placement_from_fen_or_placement,
    validate_fen_detailed,
)


MISSING_ARTIFACT = "MISSING_ARTIFACT"
AI_CONSENSUS = "ai_consensus"
EXCLUDED_AI_CATEGORIES = {"ai_best_effort", "ai_unreadable"}


def build_ai_consensus_fen_promotion_queue(
    ai_coverage_path: str | Path,
    current_report_path: str | Path,
) -> dict[str, Any]:
    ai_path = Path(ai_coverage_path)
    current_path = Path(current_report_path)
    ai_report = _load_json(ai_path)
    current_report = _load_json(current_path)
    current_by_id = _records_by_diagram_id(_extract_records(current_report))

    records: list[dict[str, Any]] = []
    skipped: dict[str, int] = {
        "ai_best_effort": 0,
        "ai_unreadable": 0,
        "non_consensus": 0,
        "invalid_ai_fen": 0,
        "already_strict_accepted": 0,
    }

    for index, ai_record in enumerate(_extract_records(ai_report)):
        category = _ai_category(ai_record)
        if category in EXCLUDED_AI_CATEGORIES:
            skipped[category] += 1
            continue
        if category != AI_CONSENSUS:
            skipped["non_consensus"] += 1
            continue

        ai_fen = _ai_fen(ai_record)
        validation = validate_fen_detailed(ai_fen)
        if not validation.normalized_fen or validation.errors:
            skipped["invalid_ai_fen"] += 1
            continue

        diagram_id = _diagram_id(ai_record, index)
        current = current_by_id.get(diagram_id, {})
        current_strict_status = _strict_status(current)
        if current_strict_status == "strict_accepted":
            skipped["already_strict_accepted"] += 1
            continue
        current_candidate = _candidate_fen(current)
        current_placement = _selected_placement(current)
        placement_diff = _placement_diff(validation.normalized_fen, current_placement)
        crop_path = _crop_path(ai_record, current)
        crop_status = "present" if crop_path != MISSING_ARTIFACT else MISSING_ARTIFACT
        page = _first_non_empty(ai_record.get("page"), ai_record.get("page_number"), current.get("page"), current.get("page_number"), 0)

        records.append(
            {
                "diagram_id": diagram_id,
                "page": _int_or_zero(page),
                "crop_path": crop_path,
                "crop_artifact_status": crop_status,
                "ai_fen": validation.normalized_fen,
                "ai_category": AI_CONSENSUS,
                "current_strict_status": current_strict_status,
                "current_candidate_fen": current_candidate,
                "current_selected_placement": current_placement,
                "placement_diff": placement_diff,
                "requires_human_verification": True,
                "recommended_action": "verify_ai_consensus_against_crop",
                "output_label_candidate": {
                    "fen": validation.normalized_fen,
                    "crop_sha256": "",
                    "verification_source": "human_visual_required",
                    "human_verified": False,
                    "square_diff_ack": False,
                    "label_status": "needs_verification",
                },
            }
        )

    records.sort(key=lambda item: (_int_or_zero(item.get("page")), str(item.get("diagram_id") or "")))
    return {
        "schema": "kindlemaster.chess_fen.ai_consensus_promotion_queue.v1",
        "ai_coverage_path": str(ai_path),
        "current_report_path": str(current_path),
        "summary": {
            "queue_count": len(records),
            "ai_consensus_count": len(records),
            "requires_human_verification_count": sum(1 for item in records if item.get("requires_human_verification") is True),
            "missing_artifact_count": sum(1 for item in records if item.get("crop_path") == MISSING_ARTIFACT),
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
    lines = [
        "# AI Consensus FEN Promotion Queue",
        "",
        f"AI coverage report: `{payload.get('ai_coverage_path', '')}`",
        f"Current FEN report: `{payload.get('current_report_path', '')}`",
        "",
        "## Summary",
        "",
        f"- Queue count: `{summary.get('queue_count', 0)}`",
        f"- Requires human verification: `{summary.get('requires_human_verification_count', 0)}`",
        f"- Missing crop artifacts: `{summary.get('missing_artifact_count', 0)}`",
        "",
        "## Records",
        "",
        "| Diagram | Page | Crop | Current status | Placement diff | Action |",
        "|---|---:|---|---|---:|---|",
    ]
    for record in records:
        if not isinstance(record, Mapping):
            continue
        lines.append(
            "| {diagram} | {page} | `{crop}` | `{status}` | {diff_count} | `{action}` |".format(
                diagram=_md(str(record.get("diagram_id") or "")),
                page=_md(str(record.get("page") or "")),
                crop=_md(str(record.get("crop_path") or "")),
                status=_md(str(record.get("current_strict_status") or "")),
                diff_count=len(record.get("placement_diff") or []),
                action=_md(str(record.get("recommended_action") or "")),
            )
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a release-safe review queue for AI consensus chess FEN candidates.")
    parser.add_argument("ai_coverage_json")
    parser.add_argument("current_report_json")
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--output-json")
    args = parser.parse_args(argv)

    try:
        payload = build_ai_consensus_fen_promotion_queue(args.ai_coverage_json, args.current_report_json)
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
        if value in {AI_CONSENSUS, *EXCLUDED_AI_CATEGORIES}:
            return value
    return ""


def _ai_fen(record: Mapping[str, Any]) -> str:
    for key in ("ai_fen", "fen", "candidate_fen", "selected_value", "value"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _candidate_fen(record: Mapping[str, Any]) -> str:
    for key in ("candidate_fen", "selected_value", "fen", "full_fen", "ai_fen", "value"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def _selected_placement(record: Mapping[str, Any]) -> str:
    for key in ("selected_placement", "placement", "placement_fen"):
        value = str(record.get(key) or "").strip()
        if value:
            return placement_from_fen_or_placement(value)
    fen = _candidate_fen(record)
    return placement_from_fen_or_placement(fen) if fen else ""


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


def _placement_diff(ai_fen: str, current_placement: str) -> list[dict[str, str]]:
    if not ai_fen or not current_placement:
        return []
    try:
        return compare_fen(ai_fen, current_placement).get("placement_diffs") or []
    except (ValueError, TypeError):
        return []


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
