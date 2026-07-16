from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image, ImageOps


MODEL_SCHEMA = "kindlemaster.fen_square_classifier.v2"
MODEL_EVAL_SCHEMA = "kindlemaster.fen_square_model_eval.v2"
FEATURE_SCHEMA = "grayscale16_hog4x4_projection_v1"
MODEL_TYPE = "rbf_svm_hog"
MODEL_CONFIG = {
    "C": 8.0,
    "gamma": "scale",
    "class_weight": "balanced",
    "decision_function_shape": "ovr",
    "break_ties": True,
    "random_state": 42,
}


def train_fen_square_candidate(
    rows: Sequence[Mapping[str, Any]],
    *,
    dataset_path: str | Path,
    models_dir: str | Path,
    reports_dir: str | Path,
    model_name: str,
    profile: str,
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Train, calibrate and evaluate the fixed-edition square candidate."""
    dependencies = _import_training_dependencies()
    if dependencies.get("status") != "available":
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "training_unavailable",
            "error": "scikit-learn is required for FEN classifier training.",
            "exception": dependencies.get("exception", ""),
            "install_command": "python kindlemaster.py bootstrap",
        }

    integrity = _split_integrity(rows)
    if integrity["status"] != "passed":
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "failed",
            "error": "dataset_split_integrity_failed",
            "split_integrity": integrity,
        }

    usable_rows, features, labels, splits, diagram_ids = _feature_matrix(rows)
    split_masks = {name: splits == name for name in ("train", "val", "holdout")}
    split_counts = {name: int(mask.sum()) for name, mask in split_masks.items()}
    class_counts = dict(sorted(Counter(labels.tolist()).items()))
    split_class_counts = {
        name: dict(sorted(Counter(labels[mask].tolist()).items()))
        for name, mask in split_masks.items()
    }
    if any(count == 0 for count in split_counts.values()):
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "failed",
            "error": "train_val_holdout_required",
            "split_counts": split_counts,
            "split_integrity": integrity,
        }
    train_classes = sorted(set(labels[split_masks["train"]].tolist()))
    if len(train_classes) < 2:
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "failed",
            "error": "at_least_two_training_classes_required",
            "train_classes": train_classes,
        }

    scaler = dependencies["StandardScaler"]()
    x_train = scaler.fit_transform(features[split_masks["train"]])
    classifier = dependencies["SVC"](**MODEL_CONFIG)
    classifier.fit(x_train, labels[split_masks["train"]])

    x_val = scaler.transform(features[split_masks["val"]])
    val_scores = _decision_scores(classifier, x_val)
    temperature = _calibrate_temperature(
        val_scores,
        labels[split_masks["val"]],
        [str(value) for value in classifier.classes_],
    )
    val_predictions, val_confidences = _predictions_and_confidences(
        classifier,
        val_scores,
        temperature=temperature,
    )
    validation = _classification_metrics(
        labels[split_masks["val"]],
        val_predictions,
        val_confidences,
        diagram_ids[split_masks["val"]],
    )
    acceptance_threshold = _zero_false_board_threshold(validation["boards"])
    validation["acceptance"] = _acceptance_metrics(validation["boards"], acceptance_threshold)

    x_holdout = scaler.transform(features[split_masks["holdout"]])
    holdout_scores = _decision_scores(classifier, x_holdout)
    holdout_predictions, holdout_confidences = _predictions_and_confidences(
        classifier,
        holdout_scores,
        temperature=temperature,
    )
    holdout = _classification_metrics(
        labels[split_masks["holdout"]],
        holdout_predictions,
        holdout_confidences,
        diagram_ids[split_masks["holdout"]],
    )
    holdout["acceptance"] = _acceptance_metrics(holdout["boards"], acceptance_threshold)

    models_path = Path(models_dir)
    reports_path = Path(reports_dir)
    models_path.mkdir(parents=True, exist_ok=True)
    reports_path.mkdir(parents=True, exist_ok=True)
    artifact_path = models_path / f"{_safe_filename(model_name)}.joblib"
    manifest_path = models_path / f"{_safe_filename(model_name)}.manifest.json"
    bundle = {
        "schema": MODEL_SCHEMA,
        "model_type": MODEL_TYPE,
        "model_name": model_name,
        "profile": profile,
        "feature_schema": FEATURE_SCHEMA,
        "feature_count": int(features.shape[1]),
        "classes": [str(value) for value in classifier.classes_],
        "scaler": scaler,
        "classifier": classifier,
        "calibration": {
            "method": "temperature_scaling",
            "source_split": "val",
            "temperature": temperature,
        },
        "acceptance": {
            "source_split": "val",
            "board_confidence": "minimum_square_confidence",
            "threshold": acceptance_threshold,
            "policy": "Abstain unless every square clears the validation-calibrated zero-false-board threshold.",
        },
    }
    dependencies["joblib"].dump(bundle, artifact_path, compress=3)

    baseline_holdout = dict(baseline.get("holdout") or {})
    promotion = _promotion_decision(holdout, baseline_holdout)
    dataset_file = Path(dataset_path)
    manifest = {
        "schema": MODEL_SCHEMA,
        "model_type": MODEL_TYPE,
        "model_name": model_name,
        "profile": profile,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "feature_schema": FEATURE_SCHEMA,
        "feature_count": int(features.shape[1]),
        "classes": [str(value) for value in classifier.classes_],
        "training_config": MODEL_CONFIG,
        "dataset_path": str(dataset_file),
        "dataset_sha256": _file_sha256(dataset_file),
        "artifact_path": str(artifact_path),
        "artifact_sha256": _file_sha256(artifact_path),
        "split_counts": split_counts,
        "class_counts": class_counts,
        "split_class_counts": split_class_counts,
        "split_integrity": integrity,
        "calibration": bundle["calibration"],
        "acceptance": bundle["acceptance"],
        "baseline": baseline,
        "validation": _without_boards(validation),
        "holdout": _without_boards(holdout),
        "promotion": promotion,
        "rollback": {
            "model_type": "feature_centroid",
            "model_name": "chess_fen_square_v1",
            "policy": "Keep the centroid JSON runtime path unchanged until the candidate passes runtime integration.",
        },
        "reproduce": (
            "python kindlemaster.py chess-study train-fen-classifier "
            f"--labels {dataset_file} --profile {profile}"
        ),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = {
        "schema": MODEL_EVAL_SCHEMA,
        "status": "candidate_trained",
        "model_path": str(artifact_path),
        "manifest_path": str(manifest_path),
        "model_type": MODEL_TYPE,
        "profile": profile,
        "feature_schema": FEATURE_SCHEMA,
        "feature_count": int(features.shape[1]),
        "usable_sample_count": len(usable_rows),
        "split_counts": split_counts,
        "class_counts": class_counts,
        "split_class_counts": split_class_counts,
        "split_integrity": integrity,
        "calibration": bundle["calibration"],
        "acceptance": bundle["acceptance"],
        "baseline": baseline,
        "validation": _without_boards(validation),
        "holdout": _without_boards(holdout),
        "promotion": promotion,
    }
    (reports_path / "fen_square_candidate_eval.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def evaluate_fen_square_candidate(
    rows: Sequence[Mapping[str, Any]],
    *,
    model_path: str | Path,
    split: str = "holdout",
) -> dict[str, Any]:
    """Evaluate an existing candidate without retraining or recalibrating it."""
    artifact = Path(model_path)
    if not artifact.is_file():
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "failed",
            "error": "model_missing",
            "model_path": str(artifact),
        }
    dependencies = _import_training_dependencies()
    if dependencies.get("status") != "available":
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "evaluation_unavailable",
            "error": "scikit-learn is required to load the candidate artifact.",
            "exception": dependencies.get("exception", ""),
        }
    bundle = dependencies["joblib"].load(artifact)
    if bundle.get("schema") != MODEL_SCHEMA or bundle.get("model_type") != MODEL_TYPE:
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "failed",
            "error": "unsupported_model_contract",
            "model_path": str(artifact),
        }

    usable_rows, features, labels, splits, diagram_ids = _feature_matrix(rows)
    mask = splits == split
    if not mask.any():
        return {
            "schema": MODEL_EVAL_SCHEMA,
            "status": "failed",
            "error": "evaluation_split_empty",
            "split": split,
        }
    scaled = bundle["scaler"].transform(features[mask])
    scores = _decision_scores(bundle["classifier"], scaled)
    temperature = float((bundle.get("calibration") or {}).get("temperature") or 1.0)
    predictions, confidences = _predictions_and_confidences(
        bundle["classifier"],
        scores,
        temperature=temperature,
    )
    metrics = _classification_metrics(labels[mask], predictions, confidences, diagram_ids[mask])
    stored_threshold = (bundle.get("acceptance") or {}).get("threshold")
    threshold = float(stored_threshold) if stored_threshold is not None else 1.0
    metrics["acceptance"] = _acceptance_metrics(metrics["boards"], threshold)
    return {
        "schema": MODEL_EVAL_SCHEMA,
        "status": "evaluated",
        "model_path": str(artifact),
        "model_sha256": _file_sha256(artifact),
        "model_type": bundle.get("model_type"),
        "profile": bundle.get("profile"),
        "feature_schema": bundle.get("feature_schema"),
        "split": split,
        "usable_sample_count": len(usable_rows),
        "metrics": _without_boards(metrics),
    }


def square_feature_vector(image: Image.Image) -> np.ndarray:
    """Extract fixed-length silhouette features resilient to small scan shifts."""
    gray = ImageOps.autocontrast(image.convert("L")).resize((32, 32), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float32) / 255.0
    raw = np.asarray(
        gray.resize((16, 16), Image.Resampling.BILINEAR),
        dtype=np.float32,
    ).reshape(-1) / 255.0

    gradient_x = np.zeros_like(pixels)
    gradient_y = np.zeros_like(pixels)
    gradient_x[:, 1:-1] = pixels[:, 2:] - pixels[:, :-2]
    gradient_y[1:-1, :] = pixels[2:, :] - pixels[:-2, :]
    magnitude = np.sqrt(gradient_x * gradient_x + gradient_y * gradient_y)
    angle = (np.arctan2(gradient_y, gradient_x) + np.pi) % np.pi
    bins = np.floor(angle / (np.pi / 8)).astype(int).clip(0, 7)

    hog: list[float] = []
    for cell_y in range(4):
        for cell_x in range(4):
            y_slice = slice(cell_y * 8, (cell_y + 1) * 8)
            x_slice = slice(cell_x * 8, (cell_x + 1) * 8)
            histogram = np.bincount(
                bins[y_slice, x_slice].reshape(-1),
                weights=magnitude[y_slice, x_slice].reshape(-1),
                minlength=8,
            ).astype(np.float32)
            histogram /= np.linalg.norm(histogram) + 1e-6
            hog.extend(float(value) for value in histogram)

    projections = np.concatenate(
        [
            pixels.mean(axis=0),
            pixels.mean(axis=1),
            magnitude.mean(axis=0),
            magnitude.mean(axis=1),
        ]
    )
    return np.concatenate([raw, np.asarray(hog, dtype=np.float32), projections]).astype(np.float32)


def _import_training_dependencies() -> dict[str, Any]:
    try:
        import joblib
        from sklearn.preprocessing import StandardScaler
        from sklearn.svm import SVC
    except Exception as error:
        return {"status": "unavailable", "exception": str(error)}
    return {
        "status": "available",
        "joblib": joblib,
        "StandardScaler": StandardScaler,
        "SVC": SVC,
    }


def _feature_matrix(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    usable: list[Mapping[str, Any]] = []
    vectors: list[np.ndarray] = []
    labels: list[str] = []
    splits: list[str] = []
    diagram_ids: list[str] = []
    for row in rows:
        image_path = Path(str(row.get("image_path") or ""))
        if not image_path.is_file():
            continue
        with Image.open(image_path) as image:
            vectors.append(square_feature_vector(image))
        usable.append(row)
        labels.append(str(row.get("class") or "empty"))
        splits.append(str(row.get("split") or ""))
        diagram_ids.append(str(row.get("diagram_id") or ""))
    if not vectors:
        return usable, np.empty((0, 0)), np.asarray(labels), np.asarray(splits), np.asarray(diagram_ids)
    return usable, np.stack(vectors), np.asarray(labels), np.asarray(splits), np.asarray(diagram_ids)


def _split_integrity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    group_splits: dict[str, set[str]] = defaultdict(set)
    diagram_splits: dict[str, set[str]] = defaultdict(set)
    diagram_counts: Counter[str] = Counter()
    for row in rows:
        group = str(row.get("split_group") or "")
        split = str(row.get("split") or "")
        diagram_id = str(row.get("diagram_id") or "")
        if group:
            group_splits[group].add(split)
        if diagram_id:
            diagram_splits[diagram_id].add(split)
            diagram_counts[diagram_id] += 1
    leaked_groups = sorted(group for group, values in group_splits.items() if len(values) > 1)
    leaked_diagrams = sorted(diagram for diagram, values in diagram_splits.items() if len(values) > 1)
    incomplete_boards = sorted(diagram for diagram, count in diagram_counts.items() if count != 64)
    return {
        "status": "passed" if not leaked_groups and not leaked_diagrams and not incomplete_boards else "failed",
        "group_count": len(group_splits),
        "diagram_count": len(diagram_splits),
        "leaked_groups": leaked_groups,
        "leaked_diagrams": leaked_diagrams,
        "incomplete_boards": incomplete_boards,
        "policy": "A page or chapter group and all 64 squares from each diagram must stay in exactly one split.",
    }


def _decision_scores(classifier: Any, features: np.ndarray) -> np.ndarray:
    scores = np.asarray(classifier.decision_function(features), dtype=np.float64)
    if scores.ndim == 1:
        scores = np.stack([-scores, scores], axis=1)
    return scores


def _calibrate_temperature(scores: np.ndarray, labels: np.ndarray, classes: list[str]) -> float:
    class_indexes = {label: index for index, label in enumerate(classes)}
    expected = np.asarray([class_indexes[str(label)] for label in labels], dtype=np.int64)
    best_temperature = 1.0
    best_loss = float("inf")
    for temperature in np.geomspace(0.05, 5.0, 121):
        probabilities = _softmax(scores / float(temperature))
        loss = -float(np.log(np.clip(probabilities[np.arange(len(expected)), expected], 1e-12, 1.0)).mean())
        if loss < best_loss:
            best_loss = loss
            best_temperature = float(temperature)
    return round(best_temperature, 6)


def _predictions_and_confidences(
    classifier: Any,
    scores: np.ndarray,
    *,
    temperature: float,
) -> tuple[np.ndarray, np.ndarray]:
    probabilities = _softmax(scores / max(float(temperature), 1e-6))
    indexes = probabilities.argmax(axis=1)
    classes = np.asarray([str(value) for value in classifier.classes_])
    return classes[indexes], probabilities[np.arange(len(indexes)), indexes]


def _classification_metrics(
    expected: np.ndarray,
    predicted: np.ndarray,
    confidences: np.ndarray,
    diagram_ids: np.ndarray,
) -> dict[str, Any]:
    classes = sorted(set(expected.tolist()) | set(predicted.tolist()))
    confusion = {label: {candidate: 0 for candidate in classes} for label in classes}
    for truth, candidate in zip(expected, predicted):
        confusion[str(truth)][str(candidate)] += 1

    board_rows: dict[str, dict[str, Any]] = {}
    for diagram_id, truth, candidate, confidence in zip(diagram_ids, expected, predicted, confidences):
        board = board_rows.setdefault(
            str(diagram_id),
            {"diagram_id": str(diagram_id), "correct": True, "minimum_square_confidence": 1.0},
        )
        board["correct"] = bool(board["correct"] and truth == candidate)
        board["minimum_square_confidence"] = min(
            float(board["minimum_square_confidence"]),
            float(confidence),
        )
    boards = sorted(board_rows.values(), key=lambda row: row["diagram_id"])
    occupied = expected != "empty"
    exact_boards = sum(1 for row in boards if row["correct"])
    return {
        "sample_count": int(len(expected)),
        "exact_square_count": int((expected == predicted).sum()),
        "square_accuracy": round(float((expected == predicted).mean()), 6),
        "piece_accuracy": round(float((expected[occupied] == predicted[occupied]).mean()), 6)
        if occupied.any()
        else 0.0,
        "board_count": len(boards),
        "exact_board_count": exact_boards,
        "exact_board_accuracy": round(exact_boards / max(1, len(boards)), 6),
        "exact_fen_placement_count": exact_boards,
        "exact_fen_placement_accuracy": round(exact_boards / max(1, len(boards)), 6),
        "confusion": {
            label: {candidate: count for candidate, count in values.items() if count}
            for label, values in confusion.items()
        },
        "per_class_accuracy": {
            label: round(confusion[label].get(label, 0) / max(1, sum(confusion[label].values())), 6)
            for label in classes
        },
        "confidence": _confidence_bins(expected == predicted, confidences),
        "boards": boards,
    }


def _confidence_bins(correct: np.ndarray, confidences: np.ndarray) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    for lower in np.arange(0.0, 1.0, 0.1):
        upper = min(1.0, float(lower + 0.1))
        mask = (confidences >= lower) & (confidences < upper if upper < 1.0 else confidences <= upper)
        count = int(mask.sum())
        if not count:
            continue
        bins.append(
            {
                "lower": round(float(lower), 1),
                "upper": round(upper, 1),
                "count": count,
                "mean_confidence": round(float(confidences[mask].mean()), 6),
                "accuracy": round(float(correct[mask].mean()), 6),
            }
        )
    return bins


def _zero_false_board_threshold(boards: Sequence[Mapping[str, Any]]) -> float:
    incorrect_confidences = [
        float(row.get("minimum_square_confidence") or 0.0)
        for row in boards
        if not row.get("correct")
    ]
    if not incorrect_confidences:
        return 0.0
    return round(min(1.0, max(incorrect_confidences) + 1e-6), 6)


def _acceptance_metrics(boards: Sequence[Mapping[str, Any]], threshold: float) -> dict[str, Any]:
    accepted = [
        row
        for row in boards
        if float(row.get("minimum_square_confidence") or 0.0) >= float(threshold)
    ]
    false_accepted = [row for row in accepted if not row.get("correct")]
    return {
        "threshold": round(float(threshold), 6),
        "accepted_board_count": len(accepted),
        "abstained_board_count": len(boards) - len(accepted),
        "coverage": round(len(accepted) / max(1, len(boards)), 6),
        "false_accepted_board_count": len(false_accepted),
        "false_accepted_board_rate": round(len(false_accepted) / max(1, len(accepted)), 6),
    }


def _promotion_decision(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if float(candidate.get("square_accuracy") or 0.0) <= float(baseline.get("square_accuracy") or 0.0):
        reasons.append("square_accuracy_not_better_than_baseline")
    if float(candidate.get("exact_board_accuracy") or 0.0) <= float(baseline.get("exact_board_accuracy") or 0.0):
        reasons.append("exact_board_accuracy_not_better_than_baseline")
    acceptance = candidate.get("acceptance") if isinstance(candidate.get("acceptance"), Mapping) else {}
    if int(acceptance.get("false_accepted_board_count") or 0) > 0:
        reasons.append("false_accepted_holdout_boards")
    return {
        "status": "eligible_for_shadow_integration" if not reasons else "blocked",
        "passed": not reasons,
        "reasons": reasons,
        "policy": "Candidate may enter shadow runtime only after beating the baseline and accepting zero incorrect holdout boards.",
    }


def _without_boards(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "boards"}


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=1, keepdims=True)
    exponentials = np.exp(shifted)
    return exponentials / np.clip(exponentials.sum(axis=1, keepdims=True), 1e-12, None)


def _safe_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in "._-" else "-" for character in value)
    return cleaned.strip(".-_") or "fen-square-model"


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
