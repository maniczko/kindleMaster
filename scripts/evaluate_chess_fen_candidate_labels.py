from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.build_chess_piece_templates import build_templates_from_labels
from scripts.evaluate_chess_fen_recognizer import evaluate_chess_fen_recognizer


def evaluate_candidate_labels(
    base_labels_path: str | Path,
    candidate_labels_path: str | Path,
    *,
    min_confidence: float = 0.70,
    min_base_exact_accuracy: float = 0.99,
    min_candidate_exact_accuracy: float = 1.0,
    max_base_regressions: int = 0,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Gate new chess FEN labels before they can pollute the template set.

    The candidate is accepted only if a clean combined template build still
    recognizes the already verified base labels and recognizes the candidate
    labels at the requested accuracy. This keeps manual review conservative:
    a new crop can be queued for more work without changing EPUB output.
    """
    base_labels = _read_jsonl(Path(base_labels_path))
    candidate_labels = _read_jsonl(Path(candidate_labels_path))
    if not base_labels or not candidate_labels:
        result = {
            "status": "failed",
            "accepted": False,
            "reasons": ["missing_base_or_candidate_labels"],
            "base_label_count": len(base_labels),
            "candidate_label_count": len(candidate_labels),
        }
        _write_output(result, output_path)
        return result

    with tempfile.TemporaryDirectory(prefix="kindlemaster-fen-label-gate-") as temp_dir:
        root = Path(temp_dir)
        base_templates = root / "base_templates"
        combined_templates = root / "combined_templates"
        combined_labels_path = root / "combined_labels.jsonl"
        _write_jsonl(combined_labels_path, [*base_labels, *candidate_labels])

        base_template_summary = build_templates_from_labels(base_labels_path, output_dir=base_templates)
        combined_template_summary = build_templates_from_labels(combined_labels_path, output_dir=combined_templates)
        base_before = evaluate_chess_fen_recognizer(
            base_labels_path,
            template_dir=base_templates,
            min_confidence=min_confidence,
            min_exact_accuracy=min_base_exact_accuracy,
        )
        base_after = evaluate_chess_fen_recognizer(
            base_labels_path,
            template_dir=combined_templates,
            min_confidence=min_confidence,
            min_exact_accuracy=min_base_exact_accuracy,
        )
        candidate_after = evaluate_chess_fen_recognizer(
            candidate_labels_path,
            template_dir=combined_templates,
            min_confidence=min_confidence,
            min_exact_accuracy=min_candidate_exact_accuracy,
        )

    regressions = _base_case_regressions(base_before.get("cases", []), base_after.get("cases", []))
    reasons: list[str] = []
    if base_before.get("exact_fen_accuracy", 0.0) < min_base_exact_accuracy:
        reasons.append("base_baseline_below_threshold")
    if base_after.get("exact_fen_accuracy", 0.0) < min_base_exact_accuracy:
        reasons.append("base_after_below_threshold")
    if len(regressions) > max_base_regressions:
        reasons.append("base_case_regressions_exceeded")
    if candidate_after.get("exact_fen_accuracy", 0.0) < min_candidate_exact_accuracy:
        reasons.append("candidate_below_threshold")

    accepted = not reasons
    result = {
        "status": "passed" if accepted else "failed",
        "accepted": accepted,
        "reasons": reasons,
        "base_labels": str(base_labels_path),
        "candidate_labels": str(candidate_labels_path),
        "base_label_count": len(base_labels),
        "candidate_label_count": len(candidate_labels),
        "min_confidence": float(min_confidence),
        "min_base_exact_accuracy": float(min_base_exact_accuracy),
        "min_candidate_exact_accuracy": float(min_candidate_exact_accuracy),
        "max_base_regressions": int(max_base_regressions),
        "base_regression_count": len(regressions),
        "base_regressions": regressions,
        "base_template_summary": _compact_template_summary(base_template_summary),
        "combined_template_summary": _compact_template_summary(combined_template_summary),
        "base_before": _compact_eval_summary(base_before),
        "base_after": _compact_eval_summary(base_after),
        "candidate_after": _compact_eval_summary(candidate_after),
        "recommendation": (
            "candidate_labels_safe_to_merge"
            if accepted
            else "keep_candidate_labels_in_review_queue_until_relabel_or_classifier_fix"
        ),
    }
    _write_output(result, output_path)
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _case_key(case: dict[str, Any]) -> str:
    return str(case.get("id") or case.get("crop_path") or case.get("expected_fen") or "")


def _base_case_regressions(before_cases: list[dict[str, Any]], after_cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    after_by_key = {_case_key(case): case for case in after_cases}
    regressions: list[dict[str, Any]] = []
    for before in before_cases:
        key = _case_key(before)
        after = after_by_key.get(key)
        if not after:
            regressions.append({"id": key, "reason": "missing_after_case"})
            continue
        if before.get("matched") and not after.get("matched"):
            regressions.append(
                {
                    "id": key,
                    "reason": "matched_base_case_became_unmatched",
                    "expected_fen": before.get("expected_fen", ""),
                    "before_actual_fen": before.get("actual_fen", ""),
                    "after_actual_fen": after.get("actual_fen", ""),
                    "after_warnings": after.get("warnings", []),
                    "after_confidence": after.get("confidence", 0.0),
                }
            )
    return regressions


def _compact_eval_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary.get("status"),
        "case_count": summary.get("case_count", 0),
        "fen_count": summary.get("fen_count", 0),
        "exact_fen_count": summary.get("exact_fen_count", 0),
        "exact_fen_accuracy": summary.get("exact_fen_accuracy", 0.0),
        "square_accuracy": summary.get("square_accuracy", 0.0),
    }


def _compact_template_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary.get("status"),
        "boards_processed": summary.get("boards_processed", 0),
        "template_count": summary.get("template_count", 0),
        "clean_output": summary.get("clean_output", False),
        "removed_stale_files": summary.get("removed_stale_files", 0),
    }


def _write_output(result: dict[str, Any], output_path: str | Path | None) -> None:
    if not output_path:
        return
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate new chess FEN labels against verified holdout labels.")
    parser.add_argument("--base-labels", required=True)
    parser.add_argument("--candidate-labels", required=True)
    parser.add_argument("--min-confidence", type=float, default=0.70)
    parser.add_argument("--min-base-exact-accuracy", type=float, default=0.99)
    parser.add_argument("--min-candidate-exact-accuracy", type=float, default=1.0)
    parser.add_argument("--max-base-regressions", type=int, default=0)
    parser.add_argument("--output", default="reports/chess_fen/evals/candidate_labels_latest.json")
    args = parser.parse_args()

    result = evaluate_candidate_labels(
        args.base_labels,
        args.candidate_labels,
        min_confidence=args.min_confidence,
        min_base_exact_accuracy=args.min_base_exact_accuracy,
        min_candidate_exact_accuracy=args.min_candidate_exact_accuracy,
        max_base_regressions=args.max_base_regressions,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
