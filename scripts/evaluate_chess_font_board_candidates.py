from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen


def evaluate_chess_font_board_candidates(
    candidate_labels_path: str | Path,
    *,
    min_candidate_fen_coverage: float = 0.90,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate deterministic candidate FEN coverage for review-only font boards."""
    path = Path(candidate_labels_path)
    rows = _read_jsonl(path)
    cases: list[dict[str, Any]] = []
    candidate_fen_count = 0
    valid_candidate_fen_count = 0
    requires_review_count = 0
    accepted_label_count = 0
    invalid_count = 0

    for row in rows:
        candidate_fen = str(row.get("candidate_fen") or "").strip()
        accepted_fen = str(row.get("fen") or "").strip()
        warnings: list[str] = []
        is_valid = False
        if candidate_fen:
            candidate_fen_count += 1
            is_valid, warnings = validate_fen(candidate_fen)
            valid_candidate_fen_count += int(is_valid)
            invalid_count += int(not is_valid)
        requires_review = bool(row.get("candidate_requires_review", True))
        requires_review_count += int(requires_review)
        accepted_label_count += int(bool(accepted_fen))
        cases.append(
            {
                "id": str(row.get("id") or ""),
                "source_pdf": str(row.get("source_pdf") or ""),
                "page": row.get("page"),
                "diagram_index": row.get("diagram_index"),
                "candidate_fen": candidate_fen,
                "candidate_fen_valid": bool(is_valid),
                "candidate_confidence": row.get("candidate_confidence", 0.0),
                "candidate_requires_review": requires_review,
                "accepted_label_present": bool(accepted_fen),
                "warnings": warnings,
            }
        )

    row_count = len(rows)
    candidate_fen_coverage = round(candidate_fen_count / max(1, row_count), 4)
    valid_candidate_fen_coverage = round(valid_candidate_fen_count / max(1, row_count), 4)
    reasons: list[str] = []
    if candidate_fen_coverage < float(min_candidate_fen_coverage):
        reasons.append(
            f"candidate_fen coverage {candidate_fen_coverage} below required {float(min_candidate_fen_coverage):.2f}"
        )
    if invalid_count:
        reasons.append(f"{invalid_count} candidate FEN value(s) failed syntax/basic validation")
    if requires_review_count:
        reasons.append(f"{requires_review_count} candidate(s) still require deterministic review")
    if accepted_label_count:
        reasons.append(
            "review-only candidate file contains accepted fen labels; move verified rows to a separate seed label file"
        )
    status = (
        "review_ready"
        if row_count
        and candidate_fen_coverage >= float(min_candidate_fen_coverage)
        and invalid_count == 0
        and requires_review_count == 0
        and accepted_label_count == 0
        else "failed"
    )
    summary = {
        "status": status,
        "accepted_for_corpus": False,
        "candidate_labels": str(path),
        "row_count": row_count,
        "candidate_fen_count": candidate_fen_count,
        "valid_candidate_fen_count": valid_candidate_fen_count,
        "candidate_fen_coverage": candidate_fen_coverage,
        "valid_candidate_fen_coverage": valid_candidate_fen_coverage,
        "min_candidate_fen_coverage": float(min_candidate_fen_coverage),
        "candidate_requires_review_count": requires_review_count,
        "accepted_label_count": accepted_label_count,
        "invalid_candidate_fen_count": invalid_count,
        "policy": "candidate_fen_is_review_aid_not_corpus_label",
        "reasons": reasons,
        "cases": cases,
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate review-only font-board candidate FEN coverage.")
    parser.add_argument("candidate_labels")
    parser.add_argument("--min-candidate-fen-coverage", type=float, default=0.90)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    result = evaluate_chess_font_board_candidates(
        args.candidate_labels,
        min_candidate_fen_coverage=args.min_candidate_fen_coverage,
        output_path=args.output or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "review_ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
