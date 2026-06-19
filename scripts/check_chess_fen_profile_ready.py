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

from chess_fen_workflow import CHESS_FEN_WORKFLOW_SCHEMA_VERSION, profile_workflow_state
from scripts.build_chess_piece_templates import build_templates_from_labels
from scripts.evaluate_chess_fen_recognizer import (
    DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    evaluate_chess_fen_recognizer,
)
from scripts.evaluate_chess_fen_profile_holdout import evaluate_chess_fen_profile_holdout
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
    require_holdout: bool = True,
    holdout_eval_path: str | Path | None = None,
    fold_count: int = 5,
    holdout_fold: int = 0,
    require_accepted_audit: bool = True,
    accepted_audit_summary_path: str | Path | None = None,
    max_critical_risk_count: int = 0,
    max_high_risk_count: int = 0,
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
    holdout_evaluation: dict[str, Any] = {}
    holdout_source = ""
    accepted_audit_summary: dict[str, Any] = {}
    accepted_audit_source = ""
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

    if not issues and require_holdout:
        holdout_evaluation, holdout_source = _load_or_run_holdout_evaluation(
            labels,
            holdout_eval_path=holdout_eval_path,
            min_confidence=min_confidence,
            min_exact_accuracy=min_exact_accuracy,
            fold_count=fold_count,
            holdout_fold=holdout_fold,
        )
        holdout_issues = _holdout_readiness_issues(
            holdout_evaluation,
            min_exact_accuracy=min_exact_accuracy,
        )
        if holdout_issues:
            issues.extend(holdout_issues)
            next_required_actions.append(
                "provide passing holdout evidence with status=passed, exact_fen_accuracy above threshold, and false_positive_count == 0"
            )

    if not issues and require_accepted_audit:
        accepted_audit_summary, accepted_audit_source = _load_accepted_audit_summary(
            accepted_audit_summary_path or manifest_case.get("chess_fen_accepted_audit_summary") or ""
        )
        accepted_audit_issues = _accepted_audit_readiness_issues(
            accepted_audit_summary,
            max_critical_risk_count=max_critical_risk_count,
            max_high_risk_count=max_high_risk_count,
        )
        if accepted_audit_issues:
            issues.extend(accepted_audit_issues)
            next_required_actions.append(
                "provide accepted/high-confidence false-positive audit evidence with status=ok and risk counts within thresholds"
            )

    status = "ready" if not issues else "failed"
    readiness_mode = "corpus" if require_holdout and require_accepted_audit else "diagnostic"
    accepted_for_corpus = bool(status == "ready" and require_holdout and require_accepted_audit)
    summary = {
        "status": status,
        "schema_version": CHESS_FEN_WORKFLOW_SCHEMA_VERSION,
        "workflow_state": profile_workflow_state(status) if accepted_for_corpus else "",
        "accepted_for_corpus": accepted_for_corpus,
        "release_ready": accepted_for_corpus,
        "readiness_mode": readiness_mode,
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
        "holdout_required": bool(require_holdout),
        "holdout_evaluation_source": holdout_source,
        "holdout_evaluation": _compact_holdout_evaluation(holdout_evaluation),
        "accepted_audit_required": bool(require_accepted_audit),
        "accepted_audit_summary_source": accepted_audit_source,
        "accepted_audit_summary": _compact_accepted_audit_summary(accepted_audit_summary),
        "max_critical_risk_count": int(max_critical_risk_count),
        "max_high_risk_count": int(max_high_risk_count),
        "issues": issues,
        "next_required_actions": next_required_actions,
        "manifest_case_ready": _ready_manifest_case(manifest_case, labels, templates, required_labels, min_exact_accuracy)
        if accepted_for_corpus
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


def _load_or_run_holdout_evaluation(
    labels_path: Path,
    *,
    holdout_eval_path: str | Path | None,
    min_confidence: float,
    min_exact_accuracy: float,
    fold_count: int,
    holdout_fold: int,
) -> tuple[dict[str, Any], str]:
    if holdout_eval_path:
        path = _resolve_repo_path(holdout_eval_path)
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                return {"status": "failed", "reasons": ["holdout_eval_load_failed"], "error": str(exc)}, str(path)
            if not isinstance(loaded, dict):
                return {"status": "failed", "reasons": ["holdout_eval_must_be_object"]}, str(path)
            return loaded, str(path)
        result = evaluate_chess_fen_profile_holdout(
            labels_path,
            min_confidence=min_confidence,
            min_exact_accuracy=min_exact_accuracy,
            fold_count=fold_count,
            holdout_fold=holdout_fold,
            output_path=path,
        )
        return result, str(path)
    return (
        evaluate_chess_fen_profile_holdout(
            labels_path,
            min_confidence=min_confidence,
            min_exact_accuracy=min_exact_accuracy,
            fold_count=fold_count,
            holdout_fold=holdout_fold,
        ),
        "computed",
    )


def _holdout_readiness_issues(
    holdout_evaluation: dict[str, Any],
    *,
    min_exact_accuracy: float,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    metrics = _holdout_metrics(holdout_evaluation)
    status = str(holdout_evaluation.get("status") or "")
    exact_fen_accuracy = float(metrics.get("exact_fen_accuracy") or 0.0)
    false_positive_count = int(metrics.get("false_positive_count") or 0)
    if status != "passed":
        issues.append({"code": "holdout_eval_failed", "holdout_status": status or "missing"})
    if false_positive_count > 0:
        issues.append({"code": "holdout_false_positive_detected", "false_positive_count": false_positive_count})
    if exact_fen_accuracy < float(min_exact_accuracy):
        issues.append(
            {
                "code": "holdout_exact_accuracy_below_minimum",
                "exact_fen_accuracy": exact_fen_accuracy,
                "min_exact_accuracy": float(min_exact_accuracy),
            }
        )
    return issues


def _holdout_metrics(holdout_evaluation: dict[str, Any]) -> dict[str, Any]:
    nested = holdout_evaluation.get("holdout_eval")
    if isinstance(nested, dict):
        return nested
    return holdout_evaluation


def _compact_holdout_evaluation(holdout_evaluation: dict[str, Any]) -> dict[str, Any]:
    if not holdout_evaluation:
        return {}
    metrics = _holdout_metrics(holdout_evaluation)
    return {
        "status": holdout_evaluation.get("status"),
        "fold_count": holdout_evaluation.get("fold_count"),
        "holdout_fold": holdout_evaluation.get("holdout_fold"),
        "train_label_count": holdout_evaluation.get("train_label_count", 0),
        "holdout_label_count": holdout_evaluation.get("holdout_label_count", metrics.get("case_count", 0)),
        "case_count": metrics.get("case_count", 0),
        "fen_count": metrics.get("fen_count", 0),
        "exact_fen_count": metrics.get("exact_fen_count", 0),
        "exact_fen_accuracy": metrics.get("exact_fen_accuracy", 0.0),
        "false_positive_count": metrics.get("false_positive_count", 0),
        "false_positive_rate": metrics.get("false_positive_rate", 0.0),
        "square_accuracy": metrics.get("square_accuracy", 0.0),
        "reasons": holdout_evaluation.get("reasons", []),
    }


def _load_accepted_audit_summary(path_value: str | Path) -> tuple[dict[str, Any], str]:
    raw = str(path_value or "").strip()
    if not raw:
        return {"status": "missing", "reasons": ["accepted_audit_summary_missing"]}, ""
    path = _resolve_repo_path(raw)
    if not path.exists():
        return {"status": "missing", "reasons": ["accepted_audit_summary_missing"], "path": str(path)}, str(path)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "failed", "reasons": ["accepted_audit_summary_load_failed"], "error": str(exc)}, str(path)
    if not isinstance(loaded, dict):
        return {"status": "failed", "reasons": ["accepted_audit_summary_must_be_object"]}, str(path)
    return loaded, str(path)


def _accepted_audit_readiness_issues(
    audit_summary: dict[str, Any],
    *,
    max_critical_risk_count: int,
    max_high_risk_count: int,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    status = str(audit_summary.get("status") or "")
    critical_risk_count = int(audit_summary.get("critical_risk_count") or 0)
    high_risk_count = int(audit_summary.get("high_risk_count") or 0)
    if status == "missing":
        issues.append({"code": "accepted_audit_summary_missing", "path": str(audit_summary.get("path") or "")})
    elif status != "ok":
        issues.append({"code": "accepted_audit_failed", "audit_status": status or "missing"})
    if critical_risk_count > int(max_critical_risk_count):
        issues.append(
            {
                "code": "accepted_audit_critical_risk_count_exceeded",
                "critical_risk_count": critical_risk_count,
                "max_critical_risk_count": int(max_critical_risk_count),
            }
        )
    if high_risk_count > int(max_high_risk_count):
        issues.append(
            {
                "code": "accepted_audit_high_risk_count_exceeded",
                "high_risk_count": high_risk_count,
                "max_high_risk_count": int(max_high_risk_count),
            }
        )
    return issues


def _compact_accepted_audit_summary(audit_summary: dict[str, Any]) -> dict[str, Any]:
    if not audit_summary:
        return {}
    return {
        "status": audit_summary.get("status"),
        "accepted_count": audit_summary.get("accepted_count", audit_summary.get("accepted_fen_count", 0)),
        "audited_count": audit_summary.get("audited_count", audit_summary.get("accepted_count", 0)),
        "critical_risk_count": audit_summary.get("critical_risk_count", 0),
        "high_risk_count": audit_summary.get("high_risk_count", 0),
        "medium_risk_count": audit_summary.get("medium_risk_count", 0),
        "low_risk_count": audit_summary.get("low_risk_count", 0),
        "reasons": audit_summary.get("reasons", []),
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
    parser.add_argument("--require-holdout", dest="require_holdout", action="store_true", default=True)
    parser.add_argument("--no-require-holdout", dest="require_holdout", action="store_false")
    parser.add_argument("--holdout-eval", default="")
    parser.add_argument("--fold-count", type=int, default=5)
    parser.add_argument("--holdout-fold", type=int, default=0)
    parser.add_argument("--require-accepted-audit", dest="require_accepted_audit", action="store_true", default=True)
    parser.add_argument("--no-require-accepted-audit", dest="require_accepted_audit", action="store_false")
    parser.add_argument("--accepted-audit-summary", default="")
    parser.add_argument("--max-critical-risk-count", type=int, default=0)
    parser.add_argument("--max-high-risk-count", type=int, default=0)
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
        require_holdout=args.require_holdout,
        holdout_eval_path=args.holdout_eval or None,
        fold_count=args.fold_count,
        holdout_fold=args.holdout_fold,
        require_accepted_audit=args.require_accepted_audit,
        accepted_audit_summary_path=args.accepted_audit_summary or None,
        max_critical_risk_count=args.max_critical_risk_count,
        max_high_risk_count=args.max_high_risk_count,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
