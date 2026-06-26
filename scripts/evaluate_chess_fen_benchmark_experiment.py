from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen  # noqa: E402
from chess_study_export import build_fen_square_dataset, train_fen_square_classifier  # noqa: E402
from chess_study_export import _predict_fen_for_source  # noqa: E402

SCHEMA = "kindlemaster.chess_fen.benchmark_experiment.v1"


def evaluate_chess_fen_benchmark_experiment(
    *,
    labels_dir: str | Path = "reference_inputs/chess_fen/labels",
    out_dir: str | Path = "output/chess_fen/benchmark_experiment",
    report_path: str | Path = "reports/chess_fen/fen_benchmark_experiment.json",
    baseline_report: str | Path = "reports/corpus/fen_corpus_90.json",
    fold_count: int = 5,
    holdout_fold: int = 0,
    max_labels: int = 32,
    min_usable_labels: int = 25,
    min_holdout_boards: int = 3,
) -> dict[str, Any]:
    """Run a deterministic local FEN recognizer benchmark without runtime promotion."""
    started = time.perf_counter()
    out = Path(out_dir)
    report = Path(report_path)
    out.mkdir(parents=True, exist_ok=True)
    (out / "review").mkdir(parents=True, exist_ok=True)

    source_labels = _collect_benchmark_labels(Path(labels_dir))
    labels = source_labels[: max(0, int(max_labels or 0))] if int(max_labels or 0) > 0 else source_labels
    normalized_labels_path = out / "review" / "fen_benchmark_labels.jsonl"
    _write_jsonl(normalized_labels_path, labels)

    dataset = build_fen_square_dataset(
        normalized_labels_path,
        out_dir=out,
        fold_count=fold_count,
        holdout_fold=holdout_fold,
    )
    model_eval = train_fen_square_classifier(out)
    model_path = Path(str(model_eval.get("model_path") or out / "models" / "chess_fen_square_v1.json"))
    model = _load_json(model_path) if model_path.is_file() else {}
    benchmark_cases = _benchmark_cases(labels, dataset)
    predictions = [_evaluate_case(row, out, model) for row in benchmark_cases]
    metrics = _metrics(predictions)
    elapsed_seconds = round(time.perf_counter() - started, 4)
    sufficient = (
        len(labels) >= int(min_usable_labels)
        and len(benchmark_cases) >= int(min_holdout_boards)
        and int(dataset.get("board_count") or 0) >= int(min_usable_labels)
    )
    baseline = _load_baseline_metrics(Path(baseline_report))
    payload = {
        "schema": SCHEMA,
        "status": "completed" if sufficient else "insufficient_benchmark",
        "experiment": {
            "name": "deterministic_template_model_ensemble_v1",
            "kind": "repo_local_deterministic_template_model",
            "external_paid_vision_api_used": False,
            "runtime_strict_acceptance_changed": False,
            "accepted_fen_changed": 0,
            "policy": "review_only_without_holdout_improvement_and_no_regression_evidence",
        },
        "inputs": {
            "labels_dir": str(labels_dir),
            "normalized_labels_path": str(normalized_labels_path),
            "source_usable_label_count": len(source_labels),
            "usable_label_count": len(labels),
            "max_labels": int(max_labels or 0),
            "benchmark_case_count": len(benchmark_cases),
            "baseline_report": str(baseline_report),
        },
        "dataset": _dataset_summary(dataset),
        "model_eval": _model_eval_summary(model_eval),
        "metrics": {
            **metrics,
            "elapsed_seconds": elapsed_seconds,
            "seconds_per_benchmark_case": round(elapsed_seconds / max(1, len(benchmark_cases)), 4),
        },
        "before_after": {
            "before": baseline,
            "after": metrics,
            "decision": "no_runtime_strict_change",
        },
        "sufficiency": {
            "sufficient": sufficient,
            "min_usable_labels": int(min_usable_labels),
            "min_holdout_boards": int(min_holdout_boards),
            "reasons": _sufficiency_reasons(labels, benchmark_cases, dataset, min_usable_labels, min_holdout_boards),
            "next_actions": _next_actions(labels, benchmark_cases, dataset, metrics),
        },
        "artifacts": {
            "out_dir": str(out),
            "dataset_path": str(dataset.get("dataset_path") or ""),
            "model_path": str(model_path),
            "predictions_path": str(out / "review" / "fen_benchmark_predictions.jsonl"),
            "report_path": str(report),
        },
    }
    _write_jsonl(out / "review" / "fen_benchmark_predictions.jsonl", predictions)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _collect_benchmark_labels(labels_dir: Path) -> list[dict[str, Any]]:
    labels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(labels_dir.glob("*.jsonl")) if labels_dir.exists() else []:
        for index, row in enumerate(_read_jsonl(path)):
            fen = str(row.get("fen") or row.get("manual_fen") or row.get("current_fen") or "").strip()
            valid, warnings = validate_fen(fen)
            crop_path = _resolve_crop_path(row)
            diagram_id = str(row.get("diagram_id") or row.get("id") or crop_path.stem or f"{path.stem}_{index}")
            if not valid or warnings or not crop_path.is_file() or diagram_id in seen:
                continue
            labels.append(
                {
                    "diagram_id": diagram_id,
                    "manual_fen": fen,
                    "fen": fen,
                    "manual_label": "correct_diagram",
                    "label_status": "verified",
                    "crop_path": str(crop_path),
                    "page": _safe_int(row.get("page")),
                    "verified_by": str(row.get("verified_by") or row.get("verification_source") or "legacy_verified_label"),
                    "verified_at": str(row.get("verified_at") or ""),
                    "source_labels": str(path),
                    "source_row_id": str(row.get("id") or ""),
                }
            )
            seen.add(diagram_id)
    return labels


def _benchmark_cases(labels: list[dict[str, Any]], dataset: Mapping[str, Any]) -> list[dict[str, Any]]:
    dataset_rows = _read_jsonl(Path(str(dataset.get("dataset_path") or "")))
    holdout_ids = {str(row.get("diagram_id") or "") for row in dataset_rows if row.get("split") == "holdout"}
    if not holdout_ids:
        holdout_ids = {str(row.get("diagram_id") or "") for row in dataset_rows}
    cases = [row for row in labels if str(row.get("diagram_id") or "") in holdout_ids]
    return cases or labels


def _evaluate_case(label: Mapping[str, Any], out: Path, model: Mapping[str, Any]) -> dict[str, Any]:
    expected_fen = str(label.get("fen") or label.get("manual_fen") or "")
    source = {
        "diagram_id": label.get("diagram_id"),
        "page": label.get("page"),
        "crop_path": label.get("crop_path"),
        "caption": label.get("diagram_id"),
    }
    prediction = _predict_fen_for_source(source, out, dict(model))
    candidate = str(prediction.get("fen_candidate") or "")
    expected_placement = expected_fen.split()[0] if expected_fen else ""
    actual_placement = str(prediction.get("placement") or "")
    exact_placement = bool(expected_placement and actual_placement == expected_placement)
    exact_full_fen = bool(expected_fen and candidate == expected_fen)
    false_positive = bool(candidate and not exact_full_fen)
    return {
        "diagram_id": label.get("diagram_id"),
        "expected_fen": expected_fen,
        "candidate_fen": candidate,
        "expected_placement": expected_placement,
        "candidate_placement": actual_placement,
        "exact_placement": exact_placement,
        "exact_full_fen": exact_full_fen,
        "false_positive": false_positive,
        "review_required": not exact_full_fen,
        "global_confidence": prediction.get("global_confidence", 0.0),
        "deterministic_validation": prediction.get("deterministic_validation") or {},
        "status": prediction.get("status") or "needs_review",
        "accepted_fen_changed": 0,
    }


def _metrics(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    exact_placement = sum(1 for row in rows if row.get("exact_placement"))
    exact_full = sum(1 for row in rows if row.get("exact_full_fen"))
    false_positive = sum(1 for row in rows if row.get("false_positive"))
    review = sum(1 for row in rows if row.get("review_required"))
    return {
        "benchmark_case_count": total,
        "exact_placement_count": exact_placement,
        "exact_placement_rate": round(exact_placement / max(1, total), 4),
        "exact_full_fen_count": exact_full,
        "exact_full_fen_rate": round(exact_full / max(1, total), 4),
        "false_positive_count": false_positive,
        "false_positive_rate": round(false_positive / max(1, total), 4),
        "review_count": review,
        "review_rate": round(review / max(1, total), 4),
        "accepted_fen_changed": 0,
    }


def _sufficiency_reasons(
    labels: list[Mapping[str, Any]],
    benchmark_cases: list[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    min_usable_labels: int,
    min_holdout_boards: int,
) -> list[str]:
    reasons: list[str] = []
    if len(labels) < int(min_usable_labels):
        reasons.append(f"usable labels {len(labels)} below minimum {int(min_usable_labels)}")
    if len(benchmark_cases) < int(min_holdout_boards):
        reasons.append(f"holdout benchmark boards {len(benchmark_cases)} below minimum {int(min_holdout_boards)}")
    if int(dataset.get("board_count") or 0) < int(min_usable_labels):
        reasons.append(f"dataset boards {int(dataset.get('board_count') or 0)} below minimum {int(min_usable_labels)}")
    return reasons


def _next_actions(
    labels: list[Mapping[str, Any]],
    benchmark_cases: list[Mapping[str, Any]],
    dataset: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> list[str]:
    actions: list[str] = []
    if not labels:
        actions.append("regenerate or repair repo-local chess FEN label/crop artifacts before ML benchmark evaluation")
    if int(dataset.get("board_count") or 0) < 50:
        actions.append("increase usable benchmark crops to at least 50 before considering holdout-based promotion evidence")
    if len(benchmark_cases) < 5:
        actions.append("ensure the fixed holdout split contains at least 5 boards")
    if float(metrics.get("false_positive_rate") or 0.0) > 0:
        actions.append("keep this recognizer experiment out of strict acceptance until false positives reach zero on holdout")
    if float(metrics.get("exact_full_fen_rate") or 0.0) <= 0:
        actions.append("improve crop normalization or square classifier features before rerunning the benchmark")
    actions.append("keep model/vision FEN outputs review-only until a later issue adds deterministic no-regression proof")
    return actions


def _load_baseline_metrics(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "unavailable", "path": str(path), "reason": "baseline_report_missing"}
    payload = _load_json(path)
    return {
        "status": str(payload.get("status") or payload.get("overall_status") or "available"),
        "path": str(path),
        "overall_exact_fen_accuracy": payload.get("overall_exact_fen_accuracy"),
        "total_false_positive_count": payload.get("total_false_positive_count"),
        "evaluated_case_count": payload.get("evaluated_case_count"),
    }


def _dataset_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "verified_label_count": payload.get("verified_label_count"),
        "board_count": payload.get("board_count"),
        "sample_count": payload.get("sample_count"),
        "split_counts": payload.get("split_counts"),
        "dataset_path": payload.get("dataset_path"),
    }


def _model_eval_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status"),
        "sample_count": payload.get("sample_count"),
        "square_accuracy": payload.get("square_accuracy"),
        "model_path": payload.get("model_path"),
        "model_type": payload.get("model_type"),
        "onnx_available": payload.get("onnx_available"),
    }


def _resolve_crop_path(row: Mapping[str, Any]) -> Path:
    for key in ("crop_path", "source_crop_path"):
        value = str(row.get(key) or "").strip()
        if value:
            path = Path(value)
            if path.is_file():
                return path
    return Path("")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate repo-local chess FEN recognizer experiments against a fixed benchmark.")
    parser.add_argument("--labels-dir", default="reference_inputs/chess_fen/labels")
    parser.add_argument("--out-dir", default="output/chess_fen/benchmark_experiment")
    parser.add_argument("--report", default="reports/chess_fen/fen_benchmark_experiment.json")
    parser.add_argument("--baseline-report", default="reports/corpus/fen_corpus_90.json")
    parser.add_argument("--fold-count", type=int, default=5)
    parser.add_argument("--holdout-fold", type=int, default=0)
    parser.add_argument("--max-labels", type=int, default=32)
    parser.add_argument("--min-usable-labels", type=int, default=25)
    parser.add_argument("--min-holdout-boards", type=int, default=3)
    args = parser.parse_args(argv)
    payload = evaluate_chess_fen_benchmark_experiment(
        labels_dir=args.labels_dir,
        out_dir=args.out_dir,
        report_path=args.report,
        baseline_report=args.baseline_report,
        fold_count=args.fold_count,
        holdout_fold=args.holdout_fold,
        max_labels=args.max_labels,
        min_usable_labels=args.min_usable_labels,
        min_holdout_boards=args.min_holdout_boards,
    )
    print(
        json.dumps(
            {
                "schema": payload["schema"],
                "status": payload["status"],
                "report_path": payload["artifacts"]["report_path"],
                "usable_label_count": payload["inputs"]["usable_label_count"],
                "benchmark_case_count": payload["metrics"]["benchmark_case_count"],
                "exact_full_fen_rate": payload["metrics"]["exact_full_fen_rate"],
                "false_positive_rate": payload["metrics"]["false_positive_rate"],
                "review_rate": payload["metrics"]["review_rate"],
                "accepted_fen_changed": payload["metrics"]["accepted_fen_changed"],
                "next_actions": payload["sufficiency"]["next_actions"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
