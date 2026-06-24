from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_chess_audit_dataset import validate_chess_audit_dataset


PGN_CANONICAL_FIELDS = (
    "id",
    "source_pdf",
    "page",
    "input_type",
    "pgn_feasible",
    "pgn_feasibility_reason",
    "expected_movetext",
    "expected_pgn",
    "linked_fen_id",
    "human_verified",
    "verified_by",
    "verified_at",
    "notes",
)
NEGATIVE_CANONICAL_FIELDS = (
    "id",
    "source_pdf",
    "page",
    "reason",
    "crop_path",
    "human_verified",
    "verified_by",
    "verified_at",
    "notes",
)


def apply_chess_audit_dataset_intake(
    *,
    target_dataset_dir: str | Path,
    pgn_draft: str | Path | None = None,
    negative_draft: str | Path | None = None,
    output_summary: str | Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    dataset_dir = Path(target_dataset_dir)
    labels_dir = dataset_dir / "labels"
    summary: dict[str, Any] = {
        "schema_version": "kindlemaster.chess_audit_dataset_intake_apply.v1",
        "target_dataset_dir": str(dataset_dir),
        "applied": bool(apply),
        "pgn": _merge_section(
            draft_path=Path(pgn_draft) if pgn_draft else None,
            target_path=labels_dir / "pgn_ground_truth.jsonl",
            canonical_fields=PGN_CANONICAL_FIELDS,
            section="pgn",
            apply=apply,
        ),
        "negative": _merge_section(
            draft_path=Path(negative_draft) if negative_draft else None,
            target_path=labels_dir / "negative_samples.jsonl",
            canonical_fields=NEGATIVE_CANONICAL_FIELDS,
            section="negative",
            apply=apply,
        ),
    }
    validation = validate_chess_audit_dataset(dataset_dir / "manifest.json")
    summary["post_validation"] = {
        "status": validation.get("status"),
        "issue_count": validation.get("issue_count"),
        "release_readiness": validation.get("release_readiness"),
    }
    if output_summary:
        output = Path(output_summary)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _merge_section(
    *,
    draft_path: Path | None,
    target_path: Path,
    canonical_fields: tuple[str, ...],
    section: str,
    apply: bool,
) -> dict[str, Any]:
    if draft_path is None:
        return {"draft_path": "", "target_path": str(target_path), "status": "not_requested", "accepted_rows": 0, "skipped_rows": 0, "conflict_rows": 0}
    draft_rows = _read_jsonl(draft_path)
    existing_rows = _read_jsonl(target_path)
    existing_by_id = {str(row.get("id") or ""): row for row in existing_rows if str(row.get("id") or "").strip()}
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for index, row in enumerate(draft_rows, start=1):
        record_id = str(row.get("id") or "").strip()
        if row.get("human_verified") is not True:
            skipped.append(_skip(index, record_id, "human_verified_missing"))
            continue
        if not str(row.get("verified_by") or "").strip():
            skipped.append(_skip(index, record_id, "verified_by_missing"))
            continue
        if not str(row.get("verified_at") or "").strip():
            skipped.append(_skip(index, record_id, "verified_at_missing"))
            continue
        candidate = {field: row.get(field, "") for field in canonical_fields if field in row}
        missing_reason = _canonical_missing_reason(candidate, section=section)
        if missing_reason:
            skipped.append(_skip(index, record_id, missing_reason))
            continue
        existing = existing_by_id.get(record_id)
        if existing and _canonical_fingerprint(existing, canonical_fields) != _canonical_fingerprint(candidate, canonical_fields):
            conflicts.append({"line": index, "id": record_id, "reason": "existing_id_conflict"})
            continue
        accepted.append(candidate)
    merged = list(existing_rows)
    existing_ids = {str(row.get("id") or "") for row in existing_rows}
    new_rows = [row for row in accepted if str(row.get("id") or "") not in existing_ids]
    merged.extend(new_rows)
    if apply:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _write_jsonl(target_path, merged)
    return {
        "draft_path": str(draft_path),
        "target_path": str(target_path),
        "status": "applied" if apply else "dry_run",
        "input_rows": len(draft_rows),
        "accepted_rows": len(accepted),
        "new_rows": len(new_rows),
        "skipped_rows": len(skipped),
        "conflict_rows": len(conflicts),
        "skipped": skipped,
        "conflicts": conflicts,
    }


def _canonical_missing_reason(row: dict[str, Any], *, section: str) -> str:
    if not str(row.get("id") or "").strip():
        return "id_missing"
    if not str(row.get("source_pdf") or "").strip():
        return "source_pdf_missing"
    if row.get("page") in (None, ""):
        return "page_missing"
    if section == "pgn":
        if str(row.get("input_type") or "") in {"full_game_text", "exercise_solution"} and row.get("pgn_feasible") is True:
            if not str(row.get("expected_movetext") or row.get("expected_pgn") or "").strip():
                return "expected_pgn_text_missing"
        if str(row.get("input_type") or "") == "diagram_only" and row.get("pgn_feasible") is not False:
            return "diagram_only_must_be_infeasible"
    if section == "negative" and not str(row.get("crop_path") or "").strip():
        return "crop_path_missing"
    return ""


def _canonical_fingerprint(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    return json.dumps({field: row.get(field, "") for field in fields}, ensure_ascii=False, sort_keys=True)


def _skip(line: int, record_id: str, reason: str) -> dict[str, Any]:
    return {"line": line, "id": record_id, "reason": reason}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely merge human-verified PGN/negative intake rows into the chess audit dataset.")
    parser.add_argument("--target-dataset-dir", default="reference_inputs/chess_fen/audit_2026_06")
    parser.add_argument("--pgn-draft", default="")
    parser.add_argument("--negative-draft", default="")
    parser.add_argument("--output-summary", default="")
    parser.add_argument("--apply", action="store_true", help="Write accepted rows to the target dataset. Without this flag, runs as dry-run.")
    args = parser.parse_args()
    summary = apply_chess_audit_dataset_intake(
        target_dataset_dir=args.target_dataset_dir,
        pgn_draft=args.pgn_draft or None,
        negative_draft=args.negative_draft or None,
        output_summary=args.output_summary or None,
        apply=args.apply,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["pgn"].get("conflict_rows") and not summary["negative"].get("conflict_rows") else 1


if __name__ == "__main__":
    raise SystemExit(main())
