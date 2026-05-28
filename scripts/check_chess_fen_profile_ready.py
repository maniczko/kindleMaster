from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.build_chess_piece_templates import build_templates_from_labels
from scripts.evaluate_chess_fen_recognizer import (
    DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    evaluate_chess_fen_recognizer,
)
from scripts.validate_chess_fen_labels import validate_chess_fen_labels


REVIEW_ONLY_LABEL_FILENAMES = {"candidate_labels_review.jsonl", "manual_label_template.jsonl"}


def check_chess_fen_profile_ready(
    manifest_case_path: str | Path,
    *,
    labels_path: str | Path | None = None,
    template_dir: str | Path | None = None,
    output_path: str | Path | None = None,
    min_seed_labels: int = 20,
    min_confidence: float = DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    min_exact_accuracy: float = DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    build_templates: bool = True,
) -> dict[str, Any]:
    """Validate that a manually verified FEN profile is safe to add to corpus manifest."""
    draft_path = Path(manifest_case_path)
    manifest_case = json.loads(draft_path.read_text(encoding="utf-8-sig"))
    labels = _resolve_repo_path(labels_path or manifest_case.get("chess_fen_seed_labels") or "")
    templates = _resolve_template_dir(manifest_case, template_dir=template_dir)
    required_labels = max(1, int(min_seed_labels))
    issues: list[dict[str, Any]] = []
    next_required_actions: list[str] = []

    source_path = _resolve_optional_repo_path(manifest_case.get("source") or manifest_case.get("target") or "")
    if source_path is None or not source_path.exists():
        issues.append({"code": "source_pdf_missing", "path": str(source_path or "")})
        next_required_actions.append("point manifest source/target at an existing real scanned chess PDF")

    if labels.name in REVIEW_ONLY_LABEL_FILENAMES:
        issues.append({"code": "review_label_artifact_path", "path": str(labels)})
        next_required_actions.append("copy only manually verified rows into reference_inputs/chess_fen/labels/<profile>_seed_positions.jsonl")

    label_validation = validate_chess_fen_labels(labels)
    if label_validation["status"] != "passed":
        issues.append(
            {
                "code": "label_validation_failed",
                "issue_count": label_validation.get("issue_count", 0),
                "valid_label_count": label_validation.get("valid_label_count", 0),
            }
        )
        next_required_actions.append("fix label validation issues: FEN, crop_path, verified_by, verified_at, and review-only markers")

    valid_label_count = int(label_validation.get("valid_label_count") or 0)
    if valid_label_count < required_labels:
        issues.append(
            {
                "code": "seed_label_count_below_minimum",
                "valid_label_count": valid_label_count,
                "min_seed_labels": required_labels,
            }
        )
        next_required_actions.append(f"add manually verified labels until valid_label_count >= {required_labels}")

    template_summary: dict[str, Any] = {}
    evaluation: dict[str, Any] = {}
    if not issues:
        if build_templates:
            template_summary = build_templates_from_labels(labels, output_dir=templates)
        evaluation = evaluate_chess_fen_recognizer(
            labels,
            template_dir=templates,
            min_confidence=min_confidence,
            min_exact_accuracy=min_exact_accuracy,
        )
        if evaluation["status"] != "passed":
            issues.append(
                {
                    "code": "fen_eval_failed",
                    "exact_fen_accuracy": evaluation.get("exact_fen_accuracy", 0.0),
                    "false_positive_count": evaluation.get("false_positive_count", 0),
                }
            )
            next_required_actions.append(
                f"improve labels/templates until exact_fen_accuracy >= {float(min_exact_accuracy):.2f} and false_positive_count == 0"
            )

    status = "ready" if not issues else "failed"
    summary = {
        "status": status,
        "accepted_for_corpus": status == "ready",
        "manifest_case_path": str(draft_path),
        "labels_path": str(labels),
        "template_dir": str(templates),
        "source_pdf": str(source_path or ""),
        "min_seed_labels": required_labels,
        "min_confidence": float(min_confidence),
        "min_exact_accuracy": float(min_exact_accuracy),
        "openai_policy": "review_only_not_used",
        "label_validation": {
            "status": label_validation.get("status"),
            "label_count": label_validation.get("label_count", 0),
            "valid_label_count": label_validation.get("valid_label_count", 0),
            "issue_count": label_validation.get("issue_count", 0),
        },
        "template_summary": template_summary,
        "evaluation": _compact_evaluation(evaluation),
        "issues": issues,
        "next_required_actions": next_required_actions,
        "manifest_case_ready": _ready_manifest_case(manifest_case, labels, templates, required_labels, min_exact_accuracy)
        if status == "ready"
        else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _ready_manifest_case(
    manifest_case: dict[str, Any],
    labels_path: Path,
    template_dir: Path,
    min_seed_labels: int,
    min_exact_accuracy: float,
) -> dict[str, Any]:
    ready = dict(manifest_case)
    ready["chess_fen_seed_labels"] = _display_path(labels_path)
    ready["chess_fen_template_dir"] = _display_path(template_dir)
    ready["chess_fen_seed_min_count"] = int(min_seed_labels)
    ready["chess_fen_seed_exact_accuracy_min"] = float(min_exact_accuracy)
    ready["notes"] = "Ready for corpus manifest after deterministic FEN profile readiness gate."
    return ready


def _compact_evaluation(evaluation: dict[str, Any]) -> dict[str, Any]:
    if not evaluation:
        return {}
    return {
        "status": evaluation.get("status"),
        "case_count": evaluation.get("case_count", 0),
        "min_confidence": evaluation.get("min_confidence"),
        "fen_count": evaluation.get("fen_count", 0),
        "exact_fen_count": evaluation.get("exact_fen_count", 0),
        "exact_fen_accuracy": evaluation.get("exact_fen_accuracy", 0.0),
        "false_positive_count": evaluation.get("false_positive_count", 0),
        "false_positive_rate": evaluation.get("false_positive_rate", 0.0),
        "square_accuracy": evaluation.get("square_accuracy", 0.0),
    }


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(str(path_value))
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _resolve_optional_repo_path(path_value: Any) -> Path | None:
    raw = str(path_value or "").strip()
    if not raw:
        return None
    return _resolve_repo_path(raw)


def _resolve_template_dir(manifest_case: dict[str, Any], *, template_dir: str | Path | None) -> Path:
    if template_dir:
        return _resolve_repo_path(template_dir)
    explicit = str(manifest_case.get("chess_fen_template_dir") or "").strip()
    if explicit:
        return _resolve_repo_path(explicit)
    profile = str(manifest_case.get("chess_fen_template_profile") or "").strip()
    if not profile:
        raise ValueError("manifest case is missing chess_fen_template_profile")
    return ROOT_DIR / "reference_inputs" / "chess_fen" / "templates" / profile


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a chess FEN profile is ready for corpus manifest promotion.")
    parser.add_argument("manifest_case")
    parser.add_argument("--labels", default="")
    parser.add_argument("--template-dir", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--min-seed-labels", type=int, default=20)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE)
    parser.add_argument("--min-exact-accuracy", type=float, default=DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN)
    parser.add_argument("--no-build-templates", action="store_true")
    args = parser.parse_args()

    result = check_chess_fen_profile_ready(
        args.manifest_case,
        labels_path=args.labels or None,
        template_dir=args.template_dir or None,
        output_path=args.output or None,
        min_seed_labels=args.min_seed_labels,
        min_confidence=args.min_confidence,
        min_exact_accuracy=args.min_exact_accuracy,
        build_templates=not args.no_build_templates,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
