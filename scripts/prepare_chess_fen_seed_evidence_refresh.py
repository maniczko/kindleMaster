from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import crop_sha256, infer_verification_source  # noqa: E402
from chess_fen_workflow import MANUAL_DRAFT, with_workflow_state  # noqa: E402
from chess_position_recognizer import validate_fen  # noqa: E402


DRAFT_NAME = "seed_evidence_refresh_draft.jsonl"
SUMMARY_NAME = "seed_evidence_refresh_summary.json"
README_NAME = "README.md"


def prepare_chess_fen_seed_evidence_refresh(
    labels_path: str | Path,
    *,
    output_dir: str | Path,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Create a review-only draft for refreshing old FEN labels.

    Historical seed labels may contain manually transcribed FENs but miss the
    newer evidence contract fields. This helper intentionally does not copy
    the old value into canonical ``fen`` or ``manual_fen``. The historical FEN
    is preserved as evidence so a human can visually confirm the crop and type
    the value into ``manual_fen`` before promotion.
    """

    source = Path(labels_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    rows = _read_jsonl(source)
    if max_rows is not None:
        rows = rows[: max(0, int(max_rows))]

    draft_rows = [_refresh_draft_row(row, labels_path=source) for row in rows]
    draft_path = target / DRAFT_NAME
    _write_jsonl(draft_path, draft_rows)
    readme_path = target / README_NAME
    readme_path.write_text(_readme(source, draft_path), encoding="utf-8")

    summary = {
        "status": "ok" if draft_rows else "empty",
        "accepted_for_corpus": False,
        "source_labels": str(source),
        "output_dir": str(target),
        "draft_path": str(draft_path),
        "readme": str(readme_path),
        "input_count": len(rows),
        "draft_count": len(draft_rows),
        "historical_fen_count": sum(1 for row in draft_rows if row.get("historical_fen")),
        "historical_fen_valid_count": sum(1 for row in draft_rows if row.get("historical_fen_valid") is True),
        "missing_crop_count": sum(1 for row in draft_rows if row.get("crop_exists") is False),
        "crop_hash_count": sum(1 for row in draft_rows if row.get("crop_sha256")),
        "policy": "review_only_historical_fen_is_evidence_not_canonical_label",
        "next_actions": [
            "open the crop and historical_fen evidence side by side",
            "only after visual confirmation, copy/type the verified value into manual_fen",
            "set human_verified=true, square_diff_ack=true, verified_by, and verified_at",
            "run scripts/promote_chess_fen_label_draft.py on the completed draft",
        ],
    }
    summary_path = target / SUMMARY_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def _refresh_draft_row(row: dict[str, Any], *, labels_path: Path) -> dict[str, Any]:
    raw_crop_path = str(row.get("crop_path") or row.get("source_crop_path") or "").strip()
    resolved_crop = _resolve_crop_path(raw_crop_path, labels_path=labels_path)
    crop_exists = bool(resolved_crop and resolved_crop.exists())
    crop_hash = crop_sha256(resolved_crop) if crop_exists and resolved_crop is not None else ""
    historical_fen = str(row.get("fen") or row.get("manual_fen") or "").strip()
    historical_valid = False
    historical_warnings: list[str] = []
    if historical_fen:
        historical_valid, historical_warnings = validate_fen(historical_fen)

    draft = {
        "id": str(row.get("id") or ""),
        "source_pdf": row.get("source_pdf", ""),
        "page": row.get("page"),
        "diagram_index": row.get("diagram_index"),
        "crop_path": raw_crop_path,
        "crop_exists": crop_exists,
        "crop_sha256": crop_hash,
        "historical_fen": historical_fen,
        "historical_fen_valid": historical_valid,
        "historical_fen_warnings": historical_warnings,
        "historical_verified_by": row.get("verified_by", ""),
        "historical_verified_at": row.get("verified_at", ""),
        "historical_verification_source": infer_verification_source(row),
        "historical_notes": row.get("notes", ""),
        "fen": "",
        "manual_fen": "",
        "human_verified": False,
        "human_rejected": False,
        "square_diff_ack": False,
        "verification_source": "",
        "verified_by": "",
        "verified_at": "",
        "label_status": "needs_manual_fen",
        "accepted_for_corpus": False,
        "notes": "Review-only refresh draft. Historical FEN is evidence only; type verified value into manual_fen.",
    }
    return with_workflow_state(draft, MANUAL_DRAFT)


def _resolve_crop_path(value: str, *, labels_path: Path) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    candidates = [Path.cwd() / path, ROOT_DIR / path, labels_path.parent / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _readme(source: Path, draft_path: Path) -> str:
    return f"""# Chess FEN Seed Evidence Refresh

Source labels: `{source}`

Draft: `{draft_path}`

This directory is an operator review aid. It does not create verified labels.

Rules:
- `historical_fen` is evidence only.
- Do not copy AI, deterministic, or historical values into canonical `fen` automatically.
- A row can be promoted only after a human visually checks the crop and writes the confirmed value into `manual_fen`.
- Set `human_verified=true`, `square_diff_ack=true`, `verified_by`, and `verified_at` only after visual verification.

Promotion command:

```powershell
python scripts/promote_chess_fen_label_draft.py {draft_path} --output reference_inputs/chess_fen/labels/<profile>_seed_positions.jsonl --verified-by "<reviewer>"
```

Then run:

```powershell
python scripts/validate_chess_fen_labels.py reference_inputs/chess_fen/labels/<profile>_seed_positions.jsonl
python scripts/check_chess_fen_profile_ready.py <manifest_case> --labels reference_inputs/chess_fen/labels/<profile>_seed_positions.jsonl --template-dir reference_inputs/chess_fen/templates/<profile>
```
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare review-only draft rows to refresh old chess FEN seed label evidence.")
    parser.add_argument("labels")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-rows", type=int, default=None)
    args = parser.parse_args()

    result = prepare_chess_fen_seed_evidence_refresh(
        args.labels,
        output_dir=args.output_dir,
        max_rows=args.max_rows,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
