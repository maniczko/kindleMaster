from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.evaluate_chess_fen_recognizer import (
    DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    evaluate_chess_fen_recognizer,
)
from scripts.evaluate_chess_font_board_candidates import evaluate_chess_font_board_candidates
from scripts.validate_chess_fen_labels import validate_chess_fen_labels


def evaluate_chess_fen_corpus(
    manifest_path: str | Path = "reference_inputs/manifest.json",
    *,
    template_root: str | Path = "reference_inputs/chess_fen/templates",
    min_confidence: float = DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    default_min_exact_accuracy: float = DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    default_min_seed_label_count: int = 20,
    min_profile_count: int = 1,
    output_path: str | Path | None = None,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Evaluate every manifest case that declares a chess FEN seed dataset."""
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    cases = [
        case
        for case in manifest.get("cases", [])
        if str(case.get("chess_fen_seed_labels") or "").strip()
    ]
    font_board_cases = [
        case
        for case in manifest.get("cases", [])
        if str(case.get("chess_fen_font_board_candidate_labels") or "").strip()
    ]
    results: list[dict[str, Any]] = []
    font_board_results: list[dict[str, Any]] = []
    failed_case_count = 0
    font_board_failed_count = 0
    total_false_positive_count = 0
    total_case_count = 0
    total_exact_fen_count = 0

    for case in cases:
        labels_path = _resolve_repo_path(case["chess_fen_seed_labels"])
        template_dir = _resolve_template_dir(case, template_root=template_root)
        min_exact_accuracy = float(case.get("chess_fen_seed_exact_accuracy_min") or default_min_exact_accuracy)
        min_seed_label_count = max(1, int(case.get("chess_fen_seed_min_count") or default_min_seed_label_count))
        label_validation = validate_chess_fen_labels(labels_path)
        if label_validation["status"] != "passed":
            result_summary = {
                "id": case.get("id", ""),
                "document_class": case.get("document_class", ""),
                "input_type": case.get("input_type", ""),
                "language": case.get("language", ""),
                "labels_path": str(labels_path),
                "template_dir": str(template_dir),
                "template_profile": str(case.get("chess_fen_template_profile") or ""),
                "min_confidence": float(min_confidence),
                "min_exact_accuracy": min_exact_accuracy,
                "min_seed_label_count": min_seed_label_count,
                "status": "failed",
                "case_count": int(label_validation.get("label_count") or 0),
                "fen_count": 0,
                "exact_fen_count": 0,
                "exact_fen_accuracy": 0.0,
                "false_positive_count": 0,
                "false_positive_rate": 0.0,
                "square_accuracy": 0.0,
                "label_validation": label_validation,
            }
            results.append(result_summary)
            failed_case_count += 1
            total_case_count += int(label_validation.get("label_count") or 0)
            continue
        if int(label_validation["valid_label_count"]) < min_seed_label_count:
            result_summary = {
                "id": case.get("id", ""),
                "document_class": case.get("document_class", ""),
                "input_type": case.get("input_type", ""),
                "language": case.get("language", ""),
                "labels_path": str(labels_path),
                "template_dir": str(template_dir),
                "template_profile": str(case.get("chess_fen_template_profile") or ""),
                "min_confidence": float(min_confidence),
                "min_exact_accuracy": min_exact_accuracy,
                "min_seed_label_count": min_seed_label_count,
                "status": "failed",
                "case_count": int(label_validation["label_count"]),
                "fen_count": 0,
                "exact_fen_count": 0,
                "exact_fen_accuracy": 0.0,
                "false_positive_count": 0,
                "false_positive_rate": 0.0,
                "square_accuracy": 0.0,
                "label_validation": label_validation,
                "failure_reason": "seed_label_count_below_minimum",
            }
            results.append(result_summary)
            failed_case_count += 1
            total_case_count += int(label_validation["label_count"])
            continue
        result = evaluate_chess_fen_recognizer(
            labels_path,
            template_dir=template_dir,
            min_confidence=min_confidence,
            min_exact_accuracy=min_exact_accuracy,
        )
        result_summary = {
            "id": case.get("id", ""),
            "document_class": case.get("document_class", ""),
            "input_type": case.get("input_type", ""),
            "language": case.get("language", ""),
            "labels_path": str(labels_path),
            "template_dir": str(template_dir),
            "template_profile": str(case.get("chess_fen_template_profile") or ""),
            "min_confidence": float(min_confidence),
            "min_exact_accuracy": min_exact_accuracy,
            "min_seed_label_count": min_seed_label_count,
            "status": result["status"],
            "case_count": result["case_count"],
            "fen_count": result["fen_count"],
            "exact_fen_count": result["exact_fen_count"],
            "exact_fen_accuracy": result["exact_fen_accuracy"],
            "false_positive_count": result["false_positive_count"],
            "false_positive_rate": result["false_positive_rate"],
            "square_accuracy": result["square_accuracy"],
            "label_validation": {
                "status": label_validation["status"],
                "label_count": label_validation["label_count"],
                "valid_label_count": label_validation["valid_label_count"],
                "issue_count": label_validation["issue_count"],
            },
        }
        results.append(result_summary)
        failed_case_count += int(result["status"] != "passed")
        total_false_positive_count += int(result["false_positive_count"])
        total_case_count += int(result["case_count"])
        total_exact_fen_count += int(result["exact_fen_count"])

    for case in font_board_cases:
        labels_path = _resolve_repo_path(case["chess_fen_font_board_candidate_labels"])
        min_candidate_fen_coverage = float(case.get("chess_fen_candidate_fen_coverage_min") or 0.90)
        result = evaluate_chess_font_board_candidates(
            labels_path,
            min_candidate_fen_coverage=min_candidate_fen_coverage,
        )
        result_summary = {
            "id": case.get("id", ""),
            "document_class": case.get("document_class", ""),
            "input_type": case.get("input_type", ""),
            "language": case.get("language", ""),
            "candidate_labels_path": str(labels_path),
            "profile_kind": "font_board_candidate",
            "status": result["status"],
            "accepted_for_corpus": False,
            "row_count": result["row_count"],
            "candidate_fen_count": result["candidate_fen_count"],
            "valid_candidate_fen_count": result["valid_candidate_fen_count"],
            "candidate_fen_coverage": result["candidate_fen_coverage"],
            "valid_candidate_fen_coverage": result["valid_candidate_fen_coverage"],
            "min_candidate_fen_coverage": result["min_candidate_fen_coverage"],
            "candidate_requires_review_count": result["candidate_requires_review_count"],
            "accepted_label_count": result["accepted_label_count"],
            "invalid_candidate_fen_count": result["invalid_candidate_fen_count"],
            "policy": result["policy"],
            "reasons": result["reasons"],
        }
        font_board_results.append(result_summary)
        font_board_failed_count += int(result["status"] != "review_ready")

    min_profile_count = max(0, int(min_profile_count))
    missing_profile_count = max(0, min_profile_count - len(results))
    reasons: list[str] = []
    if missing_profile_count:
        reasons.append(
            f"manifest has {len(results)} chess FEN profile(s), below required minimum {min_profile_count}"
        )
    if failed_case_count:
        reasons.append(f"{failed_case_count} chess FEN profile(s) failed label, accuracy, or false-positive gate")
    if font_board_failed_count:
        reasons.append(f"{font_board_failed_count} font-board candidate profile(s) failed candidate FEN gate")
    next_required_actions = _build_next_required_actions(
        has_cases=bool(results),
        missing_profile_count=missing_profile_count,
        failed_case_count=failed_case_count,
        font_board_failed_count=font_board_failed_count,
        min_profile_count=min_profile_count,
        default_min_seed_label_count=default_min_seed_label_count,
        default_min_exact_accuracy=default_min_exact_accuracy,
    )
    has_cases = bool(results)
    status = (
        "passed"
        if (has_cases or allow_empty)
        and failed_case_count == 0
        and missing_profile_count == 0
        and font_board_failed_count == 0
        else "failed"
    )
    if not has_cases and allow_empty and missing_profile_count == 0:
        status = "passed_with_warnings"
    summary = {
        "status": status,
        "manifest": str(manifest_file),
        "template_root": str(template_root),
        "min_confidence": float(min_confidence),
        "default_min_exact_accuracy": float(default_min_exact_accuracy),
        "default_min_seed_label_count": max(1, int(default_min_seed_label_count)),
        "min_profile_count": min_profile_count,
        "evaluated_case_count": len(results),
        "font_board_candidate_profile_count": len(font_board_results),
        "font_board_candidate_failed_count": font_board_failed_count,
        "font_board_candidate_status": "review_ready" if font_board_results and font_board_failed_count == 0 else ("not_configured" if not font_board_results else "failed"),
        "missing_profile_count": missing_profile_count,
        "failed_case_count": failed_case_count,
        "total_labeled_diagram_count": total_case_count,
        "total_exact_fen_count": total_exact_fen_count,
        "overall_exact_fen_accuracy": round(total_exact_fen_count / max(1, total_case_count), 4),
        "total_false_positive_count": total_false_positive_count,
        "reasons": reasons,
        "next_required_actions": next_required_actions,
        "cases": results,
        "font_board_candidate_profiles": font_board_results,
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _build_next_required_actions(
    *,
    has_cases: bool,
    missing_profile_count: int,
    failed_case_count: int,
    font_board_failed_count: int,
    min_profile_count: int,
    default_min_seed_label_count: int,
    default_min_exact_accuracy: float,
) -> list[str]:
    actions: list[str] = []
    if not has_cases:
        actions.append(
            "add at least one real scanned chess manifest case with chess_fen_seed_labels and chess_fen_template_profile"
        )
    if missing_profile_count:
        actions.append(
            "add "
            f"{missing_profile_count} real scanned chess FEN profile(s) to reach min_profile_count={min_profile_count}; "
            f"each needs at least {max(1, int(default_min_seed_label_count))} manually verified labels"
        )
    if failed_case_count:
        actions.append(
            f"fix failing profile labels/templates until exact_fen_accuracy >= {float(default_min_exact_accuracy):.2f} "
            "and false_positive_count == 0"
        )
    if font_board_failed_count:
        actions.append("fix font-board candidate rows until candidate_fen coverage passes and accepted fen labels are kept in a separate seed file")
    return actions


def _resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _resolve_template_dir(case: dict[str, Any], *, template_root: str | Path) -> Path:
    explicit = str(case.get("chess_fen_template_dir") or "").strip()
    if explicit:
        return _resolve_repo_path(explicit)
    profile = str(case.get("chess_fen_template_profile") or "").strip()
    if not profile:
        raise ValueError(f"Manifest case {case.get('id', '<unknown>')!r} is missing chess_fen_template_profile")
    root = Path(template_root)
    if not root.is_absolute():
        root = REPO_ROOT / root
    return root / profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate manifest-backed chess FEN seed datasets.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--template-root", default="reference_inputs/chess_fen/templates")
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE)
    parser.add_argument("--min-exact-accuracy", type=float, default=DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN)
    parser.add_argument("--min-seed-label-count", type=int, default=20)
    parser.add_argument("--min-profile-count", type=int, default=1)
    parser.add_argument("--output", default="reports/chess_fen/evals/fen_corpus_90_latest.json")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args()
    result = evaluate_chess_fen_corpus(
        args.manifest,
        template_root=args.template_root,
        min_confidence=args.min_confidence,
        default_min_exact_accuracy=args.min_exact_accuracy,
        default_min_seed_label_count=args.min_seed_label_count,
        min_profile_count=args.min_profile_count,
        output_path=args.output,
        allow_empty=args.allow_empty,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"passed", "passed_with_warnings"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
