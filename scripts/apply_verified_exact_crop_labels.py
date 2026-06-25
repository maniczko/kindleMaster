from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen


DEFAULT_TARGET_VERIFIED_LABELS = Path("reference_inputs/chess_fen/labels/fundamenty_verified_crop_labels.jsonl")
DEFAULT_TARGET_CROPS_DIR = Path("reference_inputs/chess_fen/crops/imported_exact_review")
DEFAULT_REPORTS_DIR = Path("reports/chess_fen/imported_exact_review")


def apply_verified_exact_crop_labels(
    filled_draft_jsonl: str | Path,
    *,
    target_labels: str | Path = DEFAULT_TARGET_VERIFIED_LABELS,
    target_crops_dir: str | Path = DEFAULT_TARGET_CROPS_DIR,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
) -> dict[str, Any]:
    draft_path = Path(filled_draft_jsonl)
    labels_path = Path(target_labels)
    crops_dir = Path(target_crops_dir)
    report_root = Path(reports_dir)
    crops_dir.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)

    draft_rows = _read_jsonl(draft_path)
    existing_rows = _read_jsonl(labels_path)
    existing_by_digest = {
        str(row.get("sha256") or "").strip().lower(): row
        for row in existing_rows
        if _looks_like_sha256(row.get("sha256"))
    }

    accepted_by_digest = dict(existing_by_digest)
    new_hash_rows = 0
    updated_same_hash_rows = 0
    stale_refresh_rows = 0
    conflict_rows = 0
    skipped_rows = 0
    accepted_details: list[dict[str, Any]] = []
    conflict_details: list[dict[str, Any]] = []
    skipped_details: list[dict[str, Any]] = []

    for row in draft_rows:
        prepared, skip_reason = _prepare_verified_exact_label_row(row, target_crops_dir=crops_dir)
        row_id = str(row.get("id") or "")
        if prepared is None:
            skipped_rows += 1
            skipped_details.append({"id": row_id, "reason": skip_reason})
            continue

        digest = str(prepared.get("sha256") or "").strip().lower()
        fen = str(prepared.get("fen") or "").strip()
        existing = accepted_by_digest.get(digest)
        if existing is not None:
            existing_fen = str(existing.get("fen") or "").strip()
            if existing_fen and existing_fen != fen:
                conflict_rows += 1
                conflict_details.append(
                    {
                        "id": row_id,
                        "sha256": digest,
                        "existing_fen": existing_fen,
                        "incoming_fen": fen,
                        "reason": "sha256_fen_conflict",
                    }
                )
                continue
            updated_same_hash_rows += 1
        else:
            new_hash_rows += 1

        if bool(row.get("stale_exact_label")):
            stale_refresh_rows += 1
        accepted_by_digest[digest] = prepared
        accepted_details.append(
            {
                "id": row_id,
                "sha256": digest,
                "page": prepared.get("page"),
                "filename": prepared.get("filename"),
                "stale_exact_label": bool(row.get("stale_exact_label")),
            }
        )

    merged_rows = _merge_exact_rows(existing_rows, accepted_by_digest)
    _write_jsonl(labels_path, merged_rows)

    summary = {
        "status": "ok",
        "filled_draft_jsonl": str(draft_path),
        "target_labels": str(labels_path),
        "target_crops_dir": str(crops_dir),
        "existing_count": len(existing_rows),
        "draft_row_count": len(draft_rows),
        "accepted_row_count": len(accepted_details),
        "merged_count": len(merged_rows),
        "new_hash_rows": new_hash_rows,
        "updated_same_hash_rows": updated_same_hash_rows,
        "stale_refresh_rows": stale_refresh_rows,
        "conflict_rows": conflict_rows,
        "skipped_rows": skipped_rows,
        "accepted_details": accepted_details,
        "conflict_details": conflict_details,
        "skipped_details": skipped_details,
    }
    summary_path = report_root / f"{draft_path.stem}_apply_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _prepare_verified_exact_label_row(
    row: dict[str, Any],
    *,
    target_crops_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    if bool(row.get("human_rejected")):
        return None, "human_rejected"
    if not bool(row.get("human_verified")):
        return None, "manual_approval_missing"

    fen = str(row.get("fen") or "").strip()
    if not fen:
        return None, "fen_missing"
    fen_valid, fen_warnings = validate_fen(fen)
    if not fen_valid:
        return None, "fen_invalid:" + ",".join(fen_warnings)

    verified_by = str(row.get("verified_by") or "").strip()
    if not verified_by:
        return None, "verified_by_missing"
    verified_at = str(row.get("verified_at") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", verified_at):
        return None, "verified_at_invalid"

    crop_path = Path(str(row.get("crop_path") or "").strip())
    if not crop_path.is_file():
        return None, "crop_path_missing_on_disk"

    crop_bytes = crop_path.read_bytes()
    digest = hashlib.sha256(crop_bytes).hexdigest()
    destination = target_crops_dir / crop_path.name
    if crop_path.resolve() != destination.resolve():
        shutil.copyfile(crop_path, destination)

    notes = str(row.get("notes") or "").strip()
    if notes:
        notes = notes + " Imported through exact-crop label campaign."
    else:
        notes = "Imported through exact-crop label campaign."

    return (
        {
            "id": str(row.get("id") or f"verified_exact_{Path(crop_path.name).stem}"),
            "source_pdf": str(row.get("source_pdf") or ""),
            "page": int(row.get("page") or 0),
            "filename": str(row.get("filename") or crop_path.name),
            "sha256": digest,
            "crop_sha256": digest,
            "fen": fen,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "verification_source": "human_visual",
            "human_verified": True,
            "label_status": "verified",
            "source_crop_path": str(destination),
            "notes": notes,
        },
        "",
    )


def _merge_exact_rows(existing_rows: list[dict[str, Any]], accepted_by_digest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in existing_rows:
        digest = str(row.get("sha256") or "").strip().lower()
        if digest and digest in accepted_by_digest and digest not in seen:
            merged.append(accepted_by_digest[digest])
            seen.add(digest)
        elif not digest:
            merged.append(row)
    for digest, row in accepted_by_digest.items():
        if digest and digest not in seen:
            merged.append(row)
            seen.add(digest)
    return merged


def _looks_like_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply human-verified exact-crop labels into runtime exact-label JSONL.")
    parser.add_argument("filled_draft_jsonl")
    parser.add_argument("--target-labels", default=str(DEFAULT_TARGET_VERIFIED_LABELS))
    parser.add_argument("--target-crops-dir", default=str(DEFAULT_TARGET_CROPS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    args = parser.parse_args()

    result = apply_verified_exact_crop_labels(
        args.filled_draft_jsonl,
        target_labels=args.target_labels,
        target_crops_dir=args.target_crops_dir,
        reports_dir=args.reports_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
