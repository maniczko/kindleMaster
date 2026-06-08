from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from ml_features import ROUTE_LABELS, ROUTE_MODEL_FEATURE_ORDER, route_feature_vector
from ml_route_model import predict_route


def train_route_classifier(
    *,
    dataset_path: str | Path = "reports/ml/datasets/route_examples.jsonl",
    model_path: str | Path = "models/route_classifier_v1.json",
    report_path: str | Path = "reports/ml/route_classifier_v1.metrics.json",
) -> dict[str, Any]:
    sklearn = _import_sklearn_training_dependencies()
    if sklearn.get("status") != "available":
        rows = _load_jsonl(Path(dataset_path))
        usable = [row for row in rows if row.get("label") in ROUTE_LABELS and isinstance(row.get("features"), Mapping)]
        label_counts = Counter(row["label"] for row in usable)
        payload = {
            "status": "training_unavailable",
            "error": "scikit-learn is required for training. Install developer dependencies with python kindlemaster.py bootstrap.",
            "exception": sklearn.get("exception", ""),
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

    LogisticRegression = sklearn["LogisticRegression"]
    accuracy_score = sklearn["accuracy_score"]
    confusion_matrix = sklearn["confusion_matrix"]
    f1_score = sklearn["f1_score"]
    recall_score = sklearn["recall_score"]
    StandardScaler = sklearn["StandardScaler"]

    rows = _load_jsonl(Path(dataset_path))
    usable = [row for row in rows if row.get("label") in ROUTE_LABELS and isinstance(row.get("features"), Mapping)]
    label_counts = Counter(row["label"] for row in usable)
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
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x)
    classifier = LogisticRegression(max_iter=1000, class_weight="balanced")
    classifier.fit(x_scaled, y)
    predictions = classifier.predict(x_scaled)
    classes = [str(item) for item in classifier.classes_]
    metrics = {
        "status": "train_set_metrics",
        "accuracy": round(float(accuracy_score(y, predictions)), 6),
        "macro_f1": round(float(f1_score(y, predictions, average="macro", zero_division=0)), 6),
        "per_class_recall": {
            class_name: round(float(value), 6)
            for class_name, value in zip(
                classes,
                recall_score(y, predictions, average=None, labels=classes, zero_division=0),
            )
        },
        "confusion_matrix": confusion_matrix(y, predictions, labels=classes).tolist(),
        "coverage": round(len(usable) / max(len(rows), 1), 6),
        "example_count": len(usable),
        "label_counts": dict(label_counts),
    }
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
        "training_status": "trained",
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
    model_file = Path(model_path)
    report_file = Path(report_path)
    model_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    model_file.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    report_file.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "trained", "model_path": str(model_file), "report_path": str(report_file), "metrics": metrics}


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


def _import_sklearn_training_dependencies() -> dict[str, Any]:
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score
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
        "StandardScaler": StandardScaler,
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Train or evaluate KindleMaster route classifier.")
    subparsers = parser.add_subparsers(dest="command")
    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--dataset", default="reports/ml/datasets/route_examples.jsonl")
    train_parser.add_argument("--model", default="models/route_classifier_v1.json")
    train_parser.add_argument("--report", default="reports/ml/route_classifier_v1.metrics.json")
    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--dataset", default="reports/ml/datasets/route_examples.jsonl")
    evaluate_parser.add_argument("--model", default="models/route_classifier_v1.json")
    evaluate_parser.add_argument("--report", default="reports/ml/route_classifier_v1.evaluation.json")
    args = parser.parse_args()
    if args.command == "train":
        payload = train_route_classifier(dataset_path=args.dataset, model_path=args.model, report_path=args.report)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") in {"trained", "training_unavailable"} else 1
    if args.command == "evaluate":
        payload = evaluate_route_classifier(dataset_path=args.dataset, model_path=args.model, report_path=args.report)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("status") != "failed" else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
