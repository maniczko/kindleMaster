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
from scripts.evaluate_chess_fen_recognizer import (
    DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    evaluate_chess_fen_recognizer,
)
from scripts.validate_chess_fen_labels import validate_chess_fen_labels


def evaluate_chess_fen_profile_holdout(
    labels_path: str | Path,
    *,
    min_confidence: float = DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    min_exact_accuracy: float = DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    fold_count: int = 5,
    holdout_fold: int = 0,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate a FEN profile without training templates on the holdout rows."""
    labels_file = Path(labels_path)
    label_validation = validate_chess_fen_labels(labels_file)
    rows = _read_jsonl(labels_file) if labels_file.exists() else []
    fold_count = max(2, int(fold_count))
    holdout_fold = int(holdout_fold) % fold_count
    train_rows = [row for index, row in enumerate(rows) if index % fold_count != holdout_fold]
    holdout_rows = [row for index, row in enumerate(rows) if index % fold_count == holdout_fold]
    reasons: list[str] = []

    if label_validation["status"] != "passed":
        reasons.append("label_validation_failed")
    if not train_rows:
        reasons.append("train_split_empty")
    if not holdout_rows:
        reasons.append("holdout_split_empty")

    template_summary: dict[str, Any] = {}
    holdout_eval: dict[str, Any] = {}
    if not reasons:
        with tempfile.TemporaryDirectory(prefix="kindlemaster-fen-holdout-") as temp_dir:
            root = Path(temp_dir)
            train_labels = root / "train_labels.jsonl"
            holdout_labels = root / "holdout_labels.jsonl"
            templates = root / "templates"
            _write_jsonl(train_labels, train_rows)
            _write_jsonl(holdout_labels, holdout_rows)
            template_summary = build_templates_from_labels(train_labels, output_dir=templates)
            holdout_eval = evaluate_chess_fen_recognizer(
                holdout_labels,
                template_dir=templates,
                min_confidence=min_confidence,
                min_exact_accuracy=min_exact_accuracy,
            )
        if holdout_eval.get("status") != "passed":
            reasons.append("holdout_eval_failed")
        if int(holdout_eval.get("false_positive_count") or 0) > 0:
            reasons.append("holdout_false_positive_detected")

    result = {
        "status": "passed" if not reasons else "failed",
        "labels_path": str(labels_file),
        "fold_count": fold_count,
        "holdout_fold": holdout_fold,
        "min_confidence": float(min_confidence),
        "min_exact_accuracy": float(min_exact_accuracy),
        "label_validation": {
            "status": label_validation.get("status"),
            "label_count": label_validation.get("label_count", 0),
            "valid_label_count": label_validation.get("valid_label_count", 0),
            "issue_count": label_validation.get("issue_count", 0),
        },
        "train_label_count": len(train_rows),
        "holdout_label_count": len(holdout_rows),
        "template_summary": _compact_template_summary(template_summary),
        "holdout_eval": _compact_eval_summary(holdout_eval),
        "holdout_cases": _compact_case_summaries(holdout_eval.get("cases", []) if holdout_eval else []),
        "reasons": reasons,
        "policy": "templates_built_from_train_split_only",
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def _compact_template_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}
    return {
        "status": summary.get("status"),
        "boards_processed": summary.get("boards_processed", 0),
        "template_count": summary.get("template_count", 0),
        "clean_output": summary.get("clean_output", False),
    }


def _compact_eval_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not summary:
        return {}
    return {
        "status": summary.get("status"),
        "case_count": summary.get("case_count", 0),
        "fen_count": summary.get("fen_count", 0),
        "exact_fen_count": summary.get("exact_fen_count", 0),
        "exact_fen_accuracy": summary.get("exact_fen_accuracy", 0.0),
        "false_positive_count": summary.get("false_positive_count", 0),
        "false_positive_rate": summary.get("false_positive_rate", 0.0),
        "square_accuracy": summary.get("square_accuracy", 0.0),
    }


def _compact_case_summaries(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for case in cases:
        if case.get("matched") and not case.get("false_positive"):
            continue
        compact.append(
            {
                "id": case.get("id", ""),
                "crop_path": case.get("crop_path", ""),
                "expected_fen": case.get("expected_fen", ""),
                "actual_fen": case.get("actual_fen", ""),
                "matched": bool(case.get("matched")),
                "false_positive": bool(case.get("false_positive")),
                "confidence": case.get("confidence", 0.0),
                "warnings": case.get("warnings", []),
            }
        )
    return compact


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a chess FEN profile with a train/holdout split.")
    parser.add_argument("labels")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE)
    parser.add_argument("--min-exact-accuracy", type=float, default=DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN)
    parser.add_argument("--fold-count", type=int, default=5)
    parser.add_argument("--holdout-fold", type=int, default=0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    result = evaluate_chess_fen_profile_holdout(
        args.labels,
        min_confidence=args.min_confidence,
        min_exact_accuracy=args.min_exact_accuracy,
        fold_count=args.fold_count,
        holdout_fold=args.holdout_fold,
        output_path=args.output or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
