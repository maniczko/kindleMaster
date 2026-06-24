from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from ml_features import ROUTE_LABELS, ROUTE_MODEL_FEATURE_ORDER, route_feature_vector
from ml_route_model import predict_route

MIN_ROUTE_EXAMPLES_PER_CLASS = 25
MIN_HOLDOUT_ACCURACY = 0.85
MIN_MACRO_F1 = 0.85
MIN_PROTECTED_RECALL = 0.80
PROTECTED_RECALL_CLASSES = ("scanned_reflow", "diagram_book_reflow")
CORPUS_HARD_NEGATIVE_ROUTES = ("magazine_layout_heavy", "diagram_chess")


def _import_sklearn_training_dependencies() -> dict[str, Any]:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score
        from sklearn.model_selection import train_test_split
        from sklearn.preprocessing import StandardScaler
    except Exception as error:
        return {"status": "unavailable", "exception": str(error)}
    return {
        "status": "available",
        "LogisticRegression": LogisticRegression,
        "accuracy_score": accuracy_score,
        "confusion_matrix": confusion_matrix,
        "f1_score": f1_score,
        "recall_score": recall_score,
        "train_test_split": train_test_split,
        "StandardScaler": StandardScaler,
    }


def train_route_classifier(
    *,
    dataset_path: str | Path = "reports/ml/datasets/route_examples.jsonl",
    model_path: str | Path | None = None,
    report_path: str | Path | None = None,
    min_examples_per_class: int = MIN_ROUTE_EXAMPLES_PER_CLASS,
    enforce_readiness: bool = True,
) -> dict[str, Any]:
    rows = _load_jsonl(Path(dataset_path))
    sklearn_deps = _import_sklearn_training_dependencies()
    usable = [row for row in rows if row.get("label") in ROUTE_LABELS and isinstance(row.get("features"), Mapping)]
    label_counts = Counter(row["label"] for row in usable)
    if sklearn_deps.get("status") != "available":
        payload = {
            "status": "training_unavailable",
            "error": "scikit-learn is required for training. Install developer dependencies with python kindlemaster.py bootstrap.",
            "exception": str(sklearn_deps.get("exception") or ""),
            "dependency": "scikit-learn",
            "install_command": "python kindlemaster.py bootstrap",
            "dataset_path": str(dataset_path),
            "example_count": len(usable),
            "label_counts": dict(label_counts),
            "model_path": str(model_path),
            "report_path": str(report_path),
            "online_learning": False,
        }
        _write_report(report_path, payload)
        return payload
    readiness = _dataset_readiness_from_dataset(Path(dataset_path), rows, min_examples_per_class=min_examples_per_class)
    if enforce_readiness and readiness.get("status") != "ready":
        payload = {
            "status": "failed",
            "error": "dataset_not_ready",
            "dataset_path": str(dataset_path),
            "dataset_readiness": readiness,
        }
        _write_report(report_path or "reports/ml/route_classifier_v1.metrics.json", payload)
        return payload

    LogisticRegression = sklearn_deps["LogisticRegression"]
    accuracy_score = sklearn_deps["accuracy_score"]
    confusion_matrix = sklearn_deps["confusion_matrix"]
    f1_score = sklearn_deps["f1_score"]
    recall_score = sklearn_deps["recall_score"]
    train_test_split = sklearn_deps["train_test_split"]
    StandardScaler = sklearn_deps["StandardScaler"]

    if len(usable) < 2 or len(label_counts) < 2:
        return {
            "status": "failed",
            "error": "insufficient_route_examples",
            "example_count": len(usable),
            "label_counts": dict(label_counts),
        }

    feature_order = list(ROUTE_MODEL_FEATURE_ORDER)
    x = [route_feature_vector(row["features"], feature_order=feature_order) for row in usable]
    y = [row["label"] for row in usable]
    x_train, x_holdout, y_train, y_holdout = train_test_split(
        x,
        y,
        test_size=0.25,
        random_state=42,
        stratify=y,
    )
    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(x_train)
    x_holdout_scaled = scaler.transform(x_holdout)
    classifier = LogisticRegression(max_iter=1000, multi_class="auto", class_weight="balanced")
    classifier.fit(x_train_scaled, y_train)
    predictions = classifier.predict(x_holdout_scaled)
    classes = [str(item) for item in classifier.classes_]
    metrics = {
        "status": "holdout_metrics",
        "accuracy": round(float(accuracy_score(y_holdout, predictions)), 6),
        "macro_f1": round(float(f1_score(y_holdout, predictions, average="macro", zero_division=0)), 6),
        "per_class_recall": {
            class_name: round(float(value), 6)
            for class_name, value in zip(
                classes,
                recall_score(y_holdout, predictions, average=None, labels=classes, zero_division=0),
            )
        },
        "confusion_matrix": confusion_matrix(y_holdout, predictions, labels=classes).tolist(),
        "calibration_bins": _calibration_bins(classifier, x_holdout_scaled, y_holdout),
        "coverage": round(len(usable) / max(len(rows), 1), 6),
        "example_count": len(usable),
        "holdout_example_count": len(y_holdout),
        "train_example_count": len(y_train),
        "label_counts": dict(label_counts),
        "dataset_readiness": readiness,
    }
    metrics["promotion_gates"] = _promotion_metric_gates(metrics)
    coefs = classifier.coef_
    intercept_values = classifier.intercept_
    if len(classes) == 2 and len(coefs) == 1:
        class_coefs = {
            classes[0]: [-float(value) for value in coefs[0]],
            classes[1]: [float(value) for value in coefs[0]],
        }
        class_intercepts = {
            classes[0]: -float(intercept_values[0]),
            classes[1]: float(intercept_values[0]),
        }
    else:
        class_coefs = {
            class_name: [float(value) for value in coefs[index]]
            for index, class_name in enumerate(classes)
        }
        class_intercepts = {
            class_name: float(value)
            for class_name, value in zip(classes, intercept_values)
        }

    model = {
        "model_version": "route-classifier-v1",
        "model_type": "multinomial_logistic_regression",
        "trained_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "training_status": "candidate_trained_pending_promotion",
        "feature_order": feature_order,
        "classes": classes,
        "scaler": {
            "mean": [float(value) for value in scaler.mean_],
            "scale": [float(value) for value in scaler.scale_],
        },
        "intercepts": class_intercepts,
        "weights": class_coefs,
        "thresholds": {
            "assist_confidence": 0.82,
            "max_heuristic_confidence_for_override": 0.70,
            "protected_classes": ["diagram_book_reflow", "scanned_reflow"],
        },
        "metrics": metrics,
    }
    model_file = Path(model_path) if model_path else _candidate_model_path()
    report_file = Path(report_path) if report_path else _candidate_report_path(model_file)
    model_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    report_file.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    model_card_path = model_file.with_suffix(".model_card.json")
    model_card = _model_card(model=model, dataset_path=dataset_path, report_path=report_file)
    model_card_path.write_text(json.dumps(model_card, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "candidate_trained",
        "model_path": str(model_file),
        "report_path": str(report_file),
        "model_card_path": str(model_card_path),
        "metrics": metrics,
    }


def promote_route_classifier(
    *,
    candidate_path: str | Path,
    model_path: str | Path = "models/route_classifier_v1.json",
    corpus_report_path: str | Path = "reports/corpus/premium_corpus_smoke_report.json",
) -> dict[str, Any]:
    candidate_file = Path(candidate_path)
    try:
        candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"status": "failed", "error": "candidate_unavailable", "exception": str(error), "candidate_path": str(candidate_file)}
    metrics = candidate.get("metrics") if isinstance(candidate, Mapping) else {}
    if not isinstance(metrics, Mapping):
        return {"status": "failed", "error": "candidate_metrics_missing", "candidate_path": str(candidate_file)}
    metric_gates = _promotion_metric_gates(metrics)
    corpus_gate = _corpus_promotion_gate(corpus_report_path)
    if not metric_gates["passed"] or not corpus_gate["passed"]:
        return {
            "status": "blocked",
            "error": "promotion_gates_failed",
            "candidate_path": str(candidate_file),
            "metric_gates": metric_gates,
            "corpus_gate": corpus_gate,
        }
    promoted = dict(candidate)
    promoted["training_status"] = "promoted"
    promoted["promoted_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    promoted["promotion_gates"] = {"metric_gates": metric_gates, "corpus_gate": corpus_gate}
    target = Path(model_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(promoted, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "promoted",
        "candidate_path": str(candidate_file),
        "model_path": str(target),
        "metric_gates": metric_gates,
        "corpus_gate": corpus_gate,
    }


def evaluate_route_classifier(
    *,
    dataset_path: str | Path = "reports/ml/datasets/route_examples.jsonl",
    model_path: str | Path = "models/route_classifier_v1.json",
    report_path: str | Path = "reports/ml/route_classifier_v1.evaluation.json",
) -> dict[str, Any]:
    rows = _load_jsonl(Path(dataset_path))
    usable = [row for row in rows if row.get("label") in ROUTE_LABELS and isinstance(row.get("features"), Mapping)]
    if not usable:
        payload = {"status": "failed", "error": "no_usable_route_examples", "dataset_path": str(dataset_path)}
        _write_report(report_path, payload)
        return payload
    try:
        model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        payload = {"status": "failed", "error": "model_unavailable", "exception": str(error), "model_path": str(model_path)}
        _write_report(report_path, payload)
        return payload
    predictions = []
    correct = 0
    expected_label_counts: Counter[str] = Counter()
    predicted_label_counts: Counter[str] = Counter()
    correct_by_label: Counter[str] = Counter()
    document_class_counts: Counter[str] = Counter()
    document_class_correct: Counter[str] = Counter()
    confusion_counts: Counter[str] = Counter()
    for row in usable:
        prediction = predict_route(row["features"], model=model)
        predicted = str(prediction.get("profile", "") or "")
        expected = str(row.get("label", "") or "")
        is_correct = predicted == expected
        correct += int(is_correct)
        expected_label_counts[expected] += 1
        predicted_label_counts[predicted] += 1
        correct_by_label[expected] += int(is_correct)
        document_class = str(row.get("document_class", "") or "unknown")
        document_class_counts[document_class] += 1
        document_class_correct[document_class] += int(is_correct)
        confusion_counts[f"{expected}->{predicted}"] += 1
        predictions.append(
            {
                "case_id": row.get("case_id", ""),
                "document_class": document_class,
                "expected": expected,
                "predicted": predicted,
                "confidence": prediction.get("confidence", 0.0),
                "features_hash": row.get("features_hash", ""),
                "correct": is_correct,
            }
        )
    per_label = {
        label: {
            "expected_count": expected_label_counts[label],
            "predicted_count": predicted_label_counts.get(label, 0),
            "correct_count": correct_by_label.get(label, 0),
            "recall": round(correct_by_label.get(label, 0) / max(expected_label_counts[label], 1), 6),
        }
        for label in sorted(expected_label_counts)
    }
    per_document_class = {
        label: {
            "example_count": document_class_counts[label],
            "correct_count": document_class_correct.get(label, 0),
            "accuracy": round(document_class_correct.get(label, 0) / max(document_class_counts[label], 1), 6),
        }
        for label in sorted(document_class_counts)
    }
    misclassification_warnings = [
        {
            "code": "route_model_misclassification",
            "expected": prediction["expected"],
            "predicted": prediction["predicted"],
            "case_id": prediction["case_id"],
            "document_class": prediction["document_class"],
            "confidence": prediction["confidence"],
        }
        for prediction in predictions
        if not prediction["correct"]
    ]
    payload = {
        "status": "evaluated",
        "dataset_path": str(dataset_path),
        "model_path": str(model_path),
        "example_count": len(usable),
        "accuracy": round(correct / max(len(usable), 1), 6),
        "per_label": per_label,
        "per_document_class": per_document_class,
        "confusion_counts": dict(sorted(confusion_counts.items())),
        "warnings": misclassification_warnings,
        "predictions": predictions,
    }
    _write_report(report_path, payload)
    return payload


def _dataset_readiness_from_dataset(
    dataset_path: Path,
    rows: list[dict[str, Any]],
    *,
    min_examples_per_class: int,
) -> dict[str, Any]:
    completeness_path = dataset_path.parent / "completeness_report.json"
    try:
        completeness = json.loads(completeness_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        completeness = {}
    if isinstance(completeness, Mapping) and isinstance(completeness.get("dataset_readiness"), Mapping):
        return dict(completeness["dataset_readiness"])

    from scripts.build_ml_datasets import build_dataset_readiness, build_feature_collision_report

    usable = [row for row in rows if row.get("label") in ROUTE_LABELS and isinstance(row.get("features"), Mapping)]
    label_counts = dict(Counter(row["label"] for row in usable))
    missing_classes = [label for label in ROUTE_LABELS if label_counts.get(label, 0) <= 0]
    under_minimum_classes = [
        {"label": label, "count": label_counts.get(label, 0), "minimum": int(min_examples_per_class)}
        for label in ROUTE_LABELS
        if label_counts.get(label, 0) < int(min_examples_per_class)
    ]
    collision_report = build_feature_collision_report(usable)
    return build_dataset_readiness(
        label_counts=label_counts,
        missing_classes=missing_classes,
        under_minimum_classes=under_minimum_classes,
        collision_report=collision_report,
        min_examples_per_class=min_examples_per_class,
    )


def _candidate_model_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return Path("models") / "candidates" / f"route_classifier_{stamp}.json"


def _candidate_report_path(model_path: Path) -> Path:
    return Path("reports") / "ml" / "candidates" / f"{model_path.stem}.metrics.json"


def _calibration_bins(classifier: Any, x_holdout_scaled: Any, y_holdout: list[str]) -> list[dict[str, Any]]:
    probabilities = classifier.predict_proba(x_holdout_scaled)
    classes = [str(item) for item in classifier.classes_]
    rows: list[dict[str, Any]] = []
    buckets = {
        "0.00-0.50": [],
        "0.50-0.70": [],
        "0.70-0.85": [],
        "0.85-1.00": [],
    }
    for expected, row in zip(y_holdout, probabilities):
        confidence = float(max(row))
        predicted = classes[int(row.argmax())]
        if confidence < 0.50:
            bucket = "0.00-0.50"
        elif confidence < 0.70:
            bucket = "0.50-0.70"
        elif confidence < 0.85:
            bucket = "0.70-0.85"
        else:
            bucket = "0.85-1.00"
        buckets[bucket].append(predicted == expected)
    for bucket, values in buckets.items():
        rows.append(
            {
                "bucket": bucket,
                "count": len(values),
                "accuracy": round(sum(1 for value in values if value) / max(len(values), 1), 6) if values else None,
            }
        )
    return rows


def _promotion_metric_gates(metrics: Mapping[str, Any]) -> dict[str, Any]:
    accuracy = _float_value(metrics.get("accuracy"))
    macro_f1 = _float_value(metrics.get("macro_f1"))
    recalls = dict(metrics.get("per_class_recall") or {})
    protected = {
        label: _float_value(recalls.get(label))
        for label in PROTECTED_RECALL_CLASSES
    }
    failures: list[str] = []
    if accuracy < MIN_HOLDOUT_ACCURACY:
        failures.append("holdout_accuracy_below_threshold")
    if macro_f1 < MIN_MACRO_F1:
        failures.append("macro_f1_below_threshold")
    for label, value in protected.items():
        if value < MIN_PROTECTED_RECALL:
            failures.append(f"protected_recall_below_threshold:{label}")
    readiness = metrics.get("dataset_readiness")
    if isinstance(readiness, Mapping) and readiness.get("status") != "ready":
        failures.append(f"dataset_not_ready:{readiness.get('status', 'unknown')}")
    return {
        "passed": not failures,
        "failures": failures,
        "thresholds": {
            "holdout_accuracy": MIN_HOLDOUT_ACCURACY,
            "macro_f1": MIN_MACRO_F1,
            "protected_recall": MIN_PROTECTED_RECALL,
        },
        "values": {
            "accuracy": accuracy,
            "macro_f1": macro_f1,
            "protected_recall": protected,
        },
    }


def _corpus_promotion_gate(corpus_report_path: str | Path) -> dict[str, Any]:
    path = Path(corpus_report_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"passed": False, "status": "unavailable", "path": str(path), "error": str(error)}
    overall_status = str(payload.get("overall_status", "") or "").lower()
    failed_routes = list(payload.get("failed_routes") or [])
    if not failed_routes:
        route_coverage = payload.get("route_coverage")
        if isinstance(route_coverage, Mapping):
            failed_routes = list(route_coverage.get("failed_routes") or [])
    hard_negative_failures = _corpus_hard_negative_failures(payload)
    return {
        "passed": overall_status not in {"failed", "error"} and not failed_routes and not hard_negative_failures,
        "status": overall_status or "unknown",
        "failed_routes": failed_routes,
        "hard_negative_routes": list(CORPUS_HARD_NEGATIVE_ROUTES),
        "hard_negative_failures": hard_negative_failures,
        "path": str(path),
    }


def _corpus_hard_negative_failures(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for case in payload.get("cases") or []:
        if not isinstance(case, Mapping):
            continue
        focus_routes = {str(route) for route in (case.get("focus_routes") or [])}
        blocked_routes = sorted(focus_routes.intersection(CORPUS_HARD_NEGATIVE_ROUTES))
        if not blocked_routes:
            continue
        grade = str(case.get("grade", "") or "").lower()
        failed_assertions = [
            assertion
            for assertion in (case.get("output_assertions") or [])
            if isinstance(assertion, Mapping)
            and str(assertion.get("route", "") or "") in blocked_routes
            and str(assertion.get("status", "") or "").lower() == "failed"
        ]
        if grade == "fail" or failed_assertions:
            failures.append(
                {
                    "case_id": str(case.get("case_id", "") or case.get("file", "") or "unknown"),
                    "document_class": str(case.get("document_class", "") or ""),
                    "routes": blocked_routes,
                    "grade": grade or "unknown",
                    "failed_assertion_ids": [
                        str(assertion.get("id", "") or "") for assertion in failed_assertions
                    ],
                }
            )
    return failures


def _model_card(*, model: Mapping[str, Any], dataset_path: str | Path, report_path: str | Path) -> dict[str, Any]:
    metrics = dict(model.get("metrics") or {})
    return {
        "model_version": model.get("model_version", ""),
        "model_type": model.get("model_type", ""),
        "training_status": model.get("training_status", ""),
        "dataset_path": str(dataset_path),
        "metrics_report_path": str(report_path),
        "trained_at": model.get("trained_at", ""),
        "feature_order": list(model.get("feature_order") or []),
        "classes": list(model.get("classes") or []),
        "promotion_gates": metrics.get("promotion_gates") or _promotion_metric_gates(metrics),
        "runtime_dependency_policy": "JSON inference only; scikit-learn is training-time only.",
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _write_report(path: str | Path, payload: Mapping[str, Any]) -> None:
    report_file = Path(path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Train or evaluate KindleMaster route classifier.")
    subparsers = parser.add_subparsers(dest="command")
    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--dataset", default="reports/ml/datasets/route_examples.jsonl")
    train_parser.add_argument("--model", default="")
    train_parser.add_argument("--report", default="")
    train_parser.add_argument("--min-examples-per-class", type=int, default=MIN_ROUTE_EXAMPLES_PER_CLASS)
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--dataset", default="reports/ml/datasets/route_examples.jsonl")
    evaluate_parser.add_argument("--model", default="models/route_classifier_v1.json")
    evaluate_parser.add_argument("--report", default="reports/ml/route_classifier_v1.evaluation.json")
    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--candidate", required=True)
    promote_parser.add_argument("--model", default="models/route_classifier_v1.json")
    promote_parser.add_argument("--corpus-report", default="reports/corpus/premium_corpus_smoke_report.json")
    args = parser.parse_args()
    if args.command == "train":
        payload = train_route_classifier(
            dataset_path=args.dataset,
            model_path=args.model or None,
            report_path=args.report or None,
            min_examples_per_class=args.min_examples_per_class,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") == "candidate_trained" else 1
    if args.command == "evaluate":
        payload = evaluate_route_classifier(dataset_path=args.dataset, model_path=args.model, report_path=args.report)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") != "failed" else 1
    if args.command == "promote":
        payload = promote_route_classifier(
            candidate_path=args.candidate,
            model_path=args.model,
            corpus_report_path=args.corpus_report,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") == "promoted" else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
