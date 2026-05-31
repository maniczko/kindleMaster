from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen
from scripts.validate_chess_fen_labels import validate_chess_fen_labels


def promote_chess_fen_label_draft(
    draft_labels_path: str | Path,
    *,
    output_path: str | Path,
    verified_by: str,
    verified_at: str | None = None,
    accept_ai_suggestions: bool = False,
) -> dict[str, Any]:
    """Promote manually approved FEN draft rows into validator-ready labels.

    Safety contract:
    - AI suggestions are not copied to `fen` unless the caller explicitly uses
      `--accept-ai-suggestions` or the row has `human_verified=true`.
    - Every promoted FEN must pass syntax/basic validation.
    - The output still requires profile readiness/corpus gates before manifest
      promotion.
    """
    draft_path = Path(draft_labels_path)
    output = Path(output_path)
    verifier = str(verified_by or "").strip()
    if not verifier:
        raise ValueError("verified_by is required")
    resolved_verified_at = str(verified_at or date.today().isoformat()).strip()

    rows = _read_jsonl(draft_path)
    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        promoted_row, reason = _promote_row(
            row,
            verified_by=verifier,
            verified_at=resolved_verified_at,
            accept_ai_suggestions=accept_ai_suggestions,
        )
        if promoted_row is None:
            skipped.append({"id": str(row.get("id") or ""), "reason": reason})
        else:
            promoted.append(promoted_row)

    output.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output, promoted)
    validation = validate_chess_fen_labels(output)
    summary = {
        "status": "passed" if promoted and validation["status"] == "passed" else "failed",
        "accepted_for_corpus": False,
        "ready_for_profile_gate": bool(promoted and validation["status"] == "passed"),
        "draft_labels": str(draft_path),
        "verified_labels": str(output),
        "input_count": len(rows),
        "promoted_count": len(promoted),
        "skipped_count": len(skipped),
        "verified_by": verifier,
        "verified_at": resolved_verified_at,
        "accept_ai_suggestions": bool(accept_ai_suggestions),
        "validation": {
            "status": validation.get("status"),
            "label_count": validation.get("label_count", 0),
            "valid_label_count": validation.get("valid_label_count", 0),
            "issue_count": validation.get("issue_count", 0),
        },
        "skipped": skipped,
        "policy": "human_verified_labels_require_profile_gate_before_corpus",
        "next_actions": [
            "run scripts/build_chess_piece_templates.py on verified_labels",
            "run scripts/evaluate_chess_fen_candidate_labels.py against the base labels",
            "run scripts/check_chess_fen_profile_ready.py before adding the profile to the manifest",
        ],
    }
    summary_path = output.with_suffix(".promotion_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _promote_row(
    row: dict[str, Any],
    *,
    verified_by: str,
    verified_at: str,
    accept_ai_suggestions: bool,
) -> tuple[dict[str, Any] | None, str]:
    row_id = str(row.get("id") or "")
    human_verified = bool(row.get("human_verified"))
    human_rejected = bool(row.get("human_rejected"))
    if human_rejected:
        return None, "human_rejected"
    if not human_verified and not accept_ai_suggestions:
        return None, "manual_approval_missing"

    fen = str(row.get("fen") or "").strip()
    source = "manual_fen"
    if not fen:
        fen = str(row.get("ai_suggested_fen") or "").strip()
        source = "ai_suggested_fen_after_human_acceptance"
    if not fen:
        fen = str(row.get("deterministic_suggested_fen") or "").strip()
        source = "deterministic_suggested_fen_after_human_acceptance"
    if not fen:
        return None, "fen_missing"
    is_valid, warnings = validate_fen(fen)
    if not is_valid:
        return None, "fen_invalid:" + ",".join(warnings)

    crop_path = str(row.get("crop_path") or "").strip()
    return (
        {
            "id": row_id,
            "source_pdf": row.get("source_pdf", ""),
            "page": row.get("page"),
            "diagram_index": row.get("diagram_index"),
            "crop_path": crop_path,
            "fen": fen,
            "verified_by": verified_by,
            "verified_at": verified_at,
            "label_status": "verified",
            "label_source": source,
            "ai_assisted": bool(source.startswith("ai_") or row.get("ai_suggested_fen")),
            "ai_confidence": row.get("ai_confidence", 0.0),
            "deterministic_confidence": row.get("deterministic_confidence", 0.0),
            "notes": "Verified from board crop after label-assist; deterministic profile gates still required.",
        },
        "",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote human-approved chess FEN draft labels into validator-ready JSONL.")
    parser.add_argument("draft_labels")
    parser.add_argument("--output", required=True)
    parser.add_argument("--verified-by", required=True)
    parser.add_argument("--verified-at", default="")
    parser.add_argument(
        "--accept-ai-suggestions",
        action="store_true",
        help="After human visual review, copy approved ai_suggested_fen values into fen.",
    )
    args = parser.parse_args()

    result = promote_chess_fen_label_draft(
        args.draft_labels,
        output_path=args.output,
        verified_by=args.verified_by,
        verified_at=args.verified_at or None,
        accept_ai_suggestions=args.accept_ai_suggestions,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
