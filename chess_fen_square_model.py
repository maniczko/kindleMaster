from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from PIL import Image, ImageOps


MODEL_SCHEMA = "kindlemaster.fen_square_classifier.v2"
MODEL_EVAL_SCHEMA = "kindlemaster.fen_square_model_eval.v2"
FEATURE_SCHEMA = "grayscale16_hog4x4_projection_v1"
MODEL_TYPE = "rbf_svm_hog"
PORTABLE_MODEL_SCHEMA = "kindlemaster.fen_square_classifier.portable.v1"
RUNTIME_RESULT_SCHEMA = "kindlemaster.fen_square_runtime.v1"
RUNTIME_MODES = frozenset({"off", "shadow", "assist"})
EXPECTED_RUNTIME_CLASSES = frozenset(
    {"B", "K", "N", "P", "Q", "R", "b", "empty", "k", "n", "p", "q", "r"}
)
MODEL_CONFIG = {
    "C": 8.0,
    "gamma": "scale",
    "class_weight": "balanced",
    "decision_function_shape": "ovr",
    "break_ties": True,
    "random_state": 42,
}


def export_portable_fen_square_model(
    model_path: str | Path,
    *,
    output_path: str | Path,
) -> dict[str, Any]:
    """Export the trained SVC to a safe NumPy-only runtime artifact."""
    source = Path(model_path)
    target = Path(output_path)
    source_manifest_path = source.with_suffix(".manifest.json")
    manifest_path = target.with_suffix(".manifest.json")
    if not source.is_file():
        return {
            "schema": PORTABLE_MODEL_SCHEMA,
            "status": "failed",
            "error": "model_missing",
            "model_path": str(source),
        }
    try:
        manifest_collision = (
            source_manifest_path.resolve() == manifest_path.resolve()
        )
    except OSError:
        manifest_collision = source_manifest_path == manifest_path
    if manifest_collision:
        return {
            "schema": PORTABLE_MODEL_SCHEMA,
            "status": "failed",
            "error": "portable_manifest_path_conflicts_with_source_manifest",
            "model_path": str(source),
            "output_path": str(target),
        }
    dependencies = _import_training_dependencies()
    if dependencies.get("status") != "available":
        return {
            "schema": PORTABLE_MODEL_SCHEMA,
            "status": "export_unavailable",
            "error": "scikit-learn is required only while exporting the training artifact.",
            "exception": dependencies.get("exception", ""),
        }
    bundle = dependencies["joblib"].load(source)
    if bundle.get("schema") != MODEL_SCHEMA or bundle.get("model_type") != MODEL_TYPE:
        return {
            "schema": PORTABLE_MODEL_SCHEMA,
            "status": "failed",
            "error": "unsupported_model_contract",
            "model_path": str(source),
        }

    classifier = bundle["classifier"]
    scaler = bundle["scaler"]
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target,
        classes=np.asarray([str(value) for value in classifier.classes_]),
        scaler_mean=np.asarray(scaler.mean_, dtype=np.float64),
        scaler_scale=np.asarray(scaler.scale_, dtype=np.float64),
        support_vectors=np.asarray(classifier.support_vectors_, dtype=np.float64),
        dual_coef=np.asarray(classifier.dual_coef_, dtype=np.float64),
        intercept=np.asarray(classifier.intercept_, dtype=np.float64),
        n_support=np.asarray(classifier.n_support_, dtype=np.int32),
        gamma=np.asarray([classifier._gamma], dtype=np.float64),
        temperature=np.asarray(
            [float((bundle.get("calibration") or {}).get("temperature") or 1.0)],
            dtype=np.float64,
        ),
        acceptance_threshold=np.asarray(
            [float((bundle.get("acceptance") or {}).get("threshold") or 1.0)],
            dtype=np.float64,
        ),
    )

    source_manifest: dict[str, Any] = {}
    if source_manifest_path.is_file():
        try:
            source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            source_manifest = {}
    marker_calibration_path = (
        Path(__file__).resolve().parent
        / "models"
        / "chess"
        / "chess_marker_calibration_yusupov_v1.json"
    )
    marker_calibration: dict[str, Any] = {}
    if marker_calibration_path.is_file():
        try:
            marker_payload = json.loads(
                marker_calibration_path.read_text(encoding="utf-8")
            )
            marker_calibration = {
                "calibration_version": marker_payload.get("calibration_version"),
                "source_profile": marker_payload.get("source_profile"),
                "classifier_version": marker_payload.get("classifier_version"),
                "source_split": marker_payload.get("source_split"),
                "artifact_path": "models/chess/chess_marker_calibration_yusupov_v1.json",
                "artifact_sha256": _file_sha256(marker_calibration_path),
            }
        except (OSError, ValueError, TypeError):
            marker_calibration = {}
    baseline_holdout = dict(
        ((source_manifest.get("baseline") or {}).get("holdout") or {})
    )
    candidate_holdout = dict(source_manifest.get("holdout") or {})
    manifest = {
        "schema": PORTABLE_MODEL_SCHEMA,
        "status": "ready",
        "model_type": MODEL_TYPE,
        "model_name": str(bundle.get("model_name") or target.stem),
        "profile": str(bundle.get("profile") or ""),
        "feature_schema": str(bundle.get("feature_schema") or ""),
        "feature_count": int(bundle.get("feature_count") or 0),
        "classes": [str(value) for value in classifier.classes_],
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "artifact_path": target.as_posix(),
        "artifact_sha256": _file_sha256(target),
        "source_artifact_sha256": _file_sha256(source),
        "source_manifest_sha256": _file_sha256(source_manifest_path),
        "training_data": {
            "dataset_sha256": source_manifest.get("dataset_sha256"),
            "split_counts": dict(source_manifest.get("split_counts") or {}),
            "split_integrity": dict(source_manifest.get("split_integrity") or {}),
        },
        "benchmark_comparison": {
            "split": "holdout",
            "baseline": {
                "model_type": str(
                    (source_manifest.get("baseline") or {}).get("model_type")
                    or "feature_centroid"
                ),
                "square_accuracy": baseline_holdout.get("square_accuracy"),
                "exact_board_accuracy": baseline_holdout.get(
                    "exact_board_accuracy"
                ),
            },
            "candidate": {
                "model_type": MODEL_TYPE,
                "square_accuracy": candidate_holdout.get("square_accuracy"),
                "exact_board_accuracy": candidate_holdout.get(
                    "exact_board_accuracy"
                ),
            },
            "delta": {
                "square_accuracy": round(
                    float(candidate_holdout.get("square_accuracy") or 0.0)
                    - float(baseline_holdout.get("square_accuracy") or 0.0),
                    6,
                ),
                "exact_board_accuracy": round(
                    float(candidate_holdout.get("exact_board_accuracy") or 0.0)
                    - float(baseline_holdout.get("exact_board_accuracy") or 0.0),
                    6,
                ),
            },
        },
        "calibration": dict(bundle.get("calibration") or {}),
        "acceptance": dict(bundle.get("acceptance") or {}),
        "decoding": dict(bundle.get("decoding") or source_manifest.get("decoding") or {}),
        "orientation": {
            "value": "white_bottom",
            "source": "fixed_edition_profile",
            "version": "yusupov-orientation-v1",
        },
        "marker_calibration": marker_calibration,
        "validation": dict(source_manifest.get("validation") or {}),
        "holdout": dict(source_manifest.get("holdout") or {}),
        "promotion": dict(source_manifest.get("promotion") or {}),
        "runtime": {
            "dependency": "numpy",
            "allow_pickle": False,
            "default_mode": "shadow",
            "rollback": "Set KINDLEMASTER_CHESS_FEN_MODEL_MODE=off.",
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "schema": PORTABLE_MODEL_SCHEMA,
        "status": "exported",
        "model_path": str(target),
        "manifest_path": str(manifest_path),
        "artifact_sha256": manifest["artifact_sha256"],
        "artifact_bytes": target.stat().st_size,
    }


def predict_portable_fen_board(
    image: Image.Image,
    *,
    model_path: str | Path,
    mode: str = "shadow",
) -> dict[str, Any]:
    """Predict one board while preserving calibrated abstention and provenance."""
    started = time.perf_counter()
    normalized_mode = str(mode or "shadow").strip().lower()
    if normalized_mode not in RUNTIME_MODES:
        normalized_mode = "off"
    if normalized_mode == "off":
        return _runtime_failure(
            mode=normalized_mode,
            status="disabled",
            blocker="model_runtime_disabled",
            model_path=Path(model_path),
        )

    loaded = load_portable_fen_square_model(model_path)
    loaded_at = time.perf_counter()
    if loaded.get("status") != "ready":
        return _runtime_failure(
            mode=normalized_mode,
            status=str(loaded.get("status") or "unavailable"),
            blocker=str(loaded.get("error") or "model_runtime_unavailable"),
            model_path=Path(model_path),
            provenance=dict(loaded.get("provenance") or {}),
        )

    model = loaded["model"]
    squares = _runtime_board_squares(image)
    features = np.stack([square_feature_vector(square) for square in squares]).astype(np.float64)
    scaled = (features - model["scaler_mean"]) / np.where(
        model["scaler_scale"] == 0.0,
        1.0,
        model["scaler_scale"],
    )
    scores = _portable_ovr_decision_function(model, scaled)
    probabilities = _softmax(scores / max(float(model["temperature"]), 1e-6))
    classes = model["classes"]
    predicted, confidences, decoding = _decode_king_constrained_predictions(
        probabilities,
        classes,
    )
    placement = _runtime_placement(predicted.tolist())
    validation_fen = f"{placement} w - - 0 1"
    from chess_position_recognizer import validate_fen

    valid, validation_warnings = validate_fen(validation_fen)
    threshold = float(model["acceptance_threshold"])
    minimum_confidence = float(confidences.min()) if len(confidences) else 0.0
    assigned_indexes = np.asarray(
        [int(np.where(classes == label)[0][0]) for label in predicted],
        dtype=np.int64,
    )
    alternatives_masked = probabilities.copy()
    alternatives_masked[np.arange(len(assigned_indexes)), assigned_indexes] = -1.0
    runner_up_margins = confidences - alternatives_masked.max(axis=1)
    minimum_runner_up_margin = (
        float(runner_up_margins.min()) if len(runner_up_margins) else 0.0
    )
    blockers = [str(warning) for warning in validation_warnings]
    if minimum_confidence < threshold:
        blockers.insert(0, "board_confidence_below_calibrated_threshold")
    candidate_accepted = bool(valid and minimum_confidence >= threshold)
    publish_blockers = list(blockers)
    if normalized_mode == "shadow":
        publish_blockers.append("shadow_mode_not_publishable")
    square_records: list[dict[str, Any]] = []
    for index, (label, confidence) in enumerate(zip(predicted.tolist(), confidences.tolist())):
        alternatives = np.argsort(probabilities[index])[-3:][::-1]
        square_records.append(
            {
                "square": f"{chr(ord('a') + index % 8)}{8 - index // 8}",
                "piece": "" if label == "empty" else str(label),
                "class": str(label),
                "confidence": round(float(confidence), 6),
                "runner_up_margin": round(float(runner_up_margins[index]), 6),
                "alternatives": [
                    {
                        "class": str(classes[candidate]),
                        "confidence": round(float(probabilities[index, candidate]), 6),
                    }
                    for candidate in alternatives
                ],
            }
        )
    return {
        "schema": RUNTIME_RESULT_SCHEMA,
        "status": "accepted_candidate" if candidate_accepted else "needs_review",
        "mode": normalized_mode,
        "placement": placement,
        "validation_fen": validation_fen,
        "confidence": round(minimum_confidence, 6),
        "confidence_policy": "minimum_square_confidence",
        "acceptance_threshold": round(threshold, 6),
        "minimum_runner_up_margin": round(minimum_runner_up_margin, 6),
        "decoding": decoding,
        "candidate_accepted": candidate_accepted,
        "publishable": bool(candidate_accepted and normalized_mode == "assist"),
        "blockers": blockers,
        "publish_blockers": publish_blockers,
        "owning_blocker": (publish_blockers or blockers or [""])[0],
        "validation": {
            "valid": bool(valid),
            "warnings": list(validation_warnings),
        },
        "orientation": dict((loaded.get("provenance") or {}).get("orientation") or {}),
        "timing": {
            "model_load_ms": round((loaded_at - started) * 1000.0, 3),
            "inference_ms": round((time.perf_counter() - loaded_at) * 1000.0, 3),
            "total_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
        "squares": square_records,
        "provenance": dict(loaded.get("provenance") or {}),
    }


def load_portable_fen_square_model(model_path: str | Path) -> dict[str, Any]:
    artifact = Path(model_path).expanduser()
    manifest_path = artifact.with_suffix(".manifest.json")
    provenance = {
        "artifact_path": str(artifact),
        "manifest_path": str(manifest_path),
    }
    if not artifact.is_file():
        return {
            "status": "unavailable",
            "error": "model_artifact_missing",
            "provenance": provenance,
        }
    if not manifest_path.is_file():
        return {
            "status": "invalid",
            "error": "model_manifest_missing",
            "provenance": provenance,
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {
            "status": "invalid",
            "error": "model_manifest_invalid",
            "provenance": provenance,
        }
    if (
        manifest.get("schema") != PORTABLE_MODEL_SCHEMA
        or manifest.get("status") != "ready"
        or manifest.get("model_type") != MODEL_TYPE
    ):
        return {
            "status": "invalid",
            "error": "model_manifest_contract_invalid",
            "provenance": provenance,
        }
    expected_hash = str(manifest.get("artifact_sha256") or "")
    try:
        model = _load_portable_fen_square_model_cached(
            str(artifact.resolve()),
            artifact.stat().st_mtime_ns,
            artifact.stat().st_size,
            expected_hash,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {
            "status": "invalid",
            "error": str(error) or "model_contract_invalid",
            "provenance": provenance,
        }
    provenance.update(
        {
            "schema": manifest.get("schema"),
            "model_name": manifest.get("model_name"),
            "profile": manifest.get("profile"),
            "feature_schema": manifest.get("feature_schema"),
            "artifact_sha256": expected_hash,
            "calibration": dict(manifest.get("calibration") or {}),
            "acceptance": dict(manifest.get("acceptance") or {}),
            "decoding": dict(manifest.get("decoding") or {}),
            "orientation": dict(manifest.get("orientation") or {}),
            "marker_calibration": dict(manifest.get("marker_calibration") or {}),
            "training_data": dict(manifest.get("training_data") or {}),
            "benchmark_comparison": dict(
                manifest.get("benchmark_comparison") or {}
            ),
            "promotion": dict(manifest.get("promotion") or {}),
        }
    )
    return {"status": "ready", "model": model, "provenance": provenance}


@lru_cache(maxsize=4)
def _load_portable_fen_square_model_cached(
    artifact_path: str,
    artifact_mtime_ns: int,
    artifact_size: int,
    expected_hash: str,
) -> dict[str, Any]:
    del artifact_mtime_ns, artifact_size
    artifact = Path(artifact_path)
    if not expected_hash or _file_sha256(artifact) != expected_hash:
        raise ValueError("model_artifact_hash_mismatch")
    with np.load(artifact, allow_pickle=False) as payload:
        required = {
            "classes",
            "scaler_mean",
            "scaler_scale",
            "support_vectors",
            "dual_coef",
            "intercept",
            "n_support",
            "gamma",
            "temperature",
            "acceptance_threshold",
        }
        missing = sorted(required - set(payload.files))
        if missing:
            raise ValueError(f"model_contract_missing:{','.join(missing)}")
        model = {key: np.array(payload[key], copy=True) for key in required}
    model["classes"] = np.asarray([str(value) for value in model["classes"]])
    feature_count = int(model["support_vectors"].shape[1])
    if feature_count != 512:
        raise ValueError("model_feature_count_invalid")
    if model["scaler_mean"].shape != (feature_count,) or model["scaler_scale"].shape != (feature_count,):
        raise ValueError("model_scaler_shape_invalid")
    class_count = len(model["classes"])
    support_vector_count = int(model["support_vectors"].shape[0])
    if (
        class_count != len(model["n_support"])
        or frozenset(model["classes"].tolist()) != EXPECTED_RUNTIME_CLASSES
    ):
        raise ValueError("model_class_contract_invalid")
    if (
        int(np.sum(model["n_support"])) != support_vector_count
        or model["dual_coef"].shape != (class_count - 1, support_vector_count)
        or model["intercept"].shape != (class_count * (class_count - 1) // 2,)
    ):
        raise ValueError("model_svc_shape_invalid")
    numeric_arrays = [
        model["scaler_mean"],
        model["scaler_scale"],
        model["support_vectors"],
        model["dual_coef"],
        model["intercept"],
        model["gamma"],
        model["temperature"],
        model["acceptance_threshold"],
    ]
    if any(not np.all(np.isfinite(values)) for values in numeric_arrays):
        raise ValueError("model_numeric_contract_invalid")
    model["gamma"] = float(model["gamma"][0])
    model["temperature"] = float(model["temperature"][0])
    model["acceptance_threshold"] = float(model["acceptance_threshold"][0])
    if (
        model["gamma"] <= 0.0
        or model["temperature"] <= 0.0
        or not 0.0 <= model["acceptance_threshold"] <= 1.0
    ):
        raise ValueError("model_calibration_contract_invalid")
    return model


def _portable_ovr_decision_function(model: Mapping[str, Any], features: np.ndarray) -> np.ndarray:
    support_vectors = np.asarray(model["support_vectors"], dtype=np.float64)
    dual_coef = np.asarray(model["dual_coef"], dtype=np.float64)
    intercept = np.asarray(model["intercept"], dtype=np.float64)
    n_support = np.asarray(model["n_support"], dtype=np.int32)
    feature_norms = np.sum(features * features, axis=1, keepdims=True)
    support_norms = np.sum(support_vectors * support_vectors, axis=1, keepdims=True).T
    distances = feature_norms + support_norms - 2.0 * (features @ support_vectors.T)
    np.maximum(distances, 0.0, out=distances)
    kernels = np.exp(-float(model["gamma"]) * distances)
    starts = np.cumsum(np.concatenate([np.asarray([0], dtype=np.int32), n_support]))
    pair_scores: list[np.ndarray] = []
    class_count = len(n_support)
    for first in range(class_count):
        for second in range(first + 1, class_count):
            first_start = int(starts[first])
            second_start = int(starts[second])
            first_count = int(n_support[first])
            second_count = int(n_support[second])
            score = (
                kernels[:, first_start : first_start + first_count]
                @ dual_coef[second - 1, first_start : first_start + first_count]
                + kernels[:, second_start : second_start + second_count]
                @ dual_coef[first, second_start : second_start + second_count]
                + intercept[len(pair_scores)]
            )
            pair_scores.append(score)
    raw = np.stack(pair_scores, axis=1)
    pair_predictions = raw < 0
    pair_confidences = -raw
    votes = np.zeros((len(features), class_count), dtype=np.float64)
    confidence_sums = np.zeros_like(votes)
    pair_index = 0
    for first in range(class_count):
        for second in range(first + 1, class_count):
            confidence_sums[:, first] -= pair_confidences[:, pair_index]
            confidence_sums[:, second] += pair_confidences[:, pair_index]
            votes[pair_predictions[:, pair_index] == 0, first] += 1
            votes[pair_predictions[:, pair_index] == 1, second] += 1
            pair_index += 1
    return votes + confidence_sums / (3.0 * (np.abs(confidence_sums) + 1.0))


def _runtime_board_squares(image: Image.Image) -> list[Image.Image]:
    rgb = image.convert("RGB")
    side = min(rgb.size)
    left = max(0, (rgb.width - side) // 2)
    top = max(0, (rgb.height - side) // 2)
    square = rgb.crop((left, top, left + side, top + side))
    margin = max(0, int(side * 0.035))
    if side - margin * 2 >= 32:
        square = square.crop((margin, margin, side - margin, side - margin))
    normalized = ImageOps.autocontrast(square.convert("L")).resize(
        (256, 256),
        Image.Resampling.LANCZOS,
    )
    normalized = normalized.resize((512, 512), Image.Resampling.LANCZOS).convert("RGB")
    return [
        normalized.crop(
            (
                file_index * 64,
                rank * 64,
                (file_index + 1) * 64,
                (rank + 1) * 64,
            )
        )
        for rank in range(8)
        for file_index in range(8)
    ]


def _runtime_placement(labels: Sequence[str]) -> str:
    if len(labels) != 64:
        raise ValueError("runtime_board_must_have_64_squares")
    ranks: list[str] = []
    for rank in range(8):
        values: list[str] = []
        empty_count = 0
        for label in labels[rank * 8 : (rank + 1) * 8]:
            if label == "empty":
                empty_count += 1
                continue
            if empty_count:
                values.append(str(empty_count))
                empty_count = 0
            values.append(str(label))
        if empty_count:
            values.append(str(empty_count))
        ranks.append("".join(values) or "8")
    return "/".join(ranks)


def _runtime_failure(
    *,
    mode: str,
    status: str,
    blocker: str,
    model_path: Path,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": RUNTIME_RESULT_SCHEMA,
        "status": status,
        "mode": mode,
        "placement": "",
        "confidence": 0.0,
        "candidate_accepted": False,
        "publishable": False,
        "blockers": [blocker],
        "publish_blockers": [blocker],
        "owning_blocker": blocker,
        "squares": [],
        "provenance": {
            "artifact_path": str(model_path),
            **dict(provenance or {}),
        },
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
        diagram_ids=diagram_ids[split_masks["val"]],
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
        diagram_ids=diagram_ids[split_masks["holdout"]],
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
        "decoding": {
            "policy": "exactly_one_king_per_color",
            "confidence": "assigned_class_probability",
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
        "decoding": bundle["decoding"],
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
        "decoding": bundle["decoding"],
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
        diagram_ids=diagram_ids[mask],
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
    diagram_ids: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    probabilities = _softmax(scores / max(float(temperature), 1e-6))
    indexes = probabilities.argmax(axis=1)
    classes = np.asarray([str(value) for value in classifier.classes_])
    predicted = classes[indexes]
    confidences = probabilities[np.arange(len(indexes)), indexes]
    if diagram_ids is None:
        return predicted, confidences

    normalized_ids = np.asarray([str(value) for value in diagram_ids])
    for diagram_id in sorted(set(normalized_ids.tolist())):
        board_indexes = np.flatnonzero(normalized_ids == diagram_id)
        if len(board_indexes) != 64:
            continue
        board_predictions, board_confidences, _decoding = _decode_king_constrained_predictions(
            probabilities[board_indexes],
            classes,
        )
        predicted[board_indexes] = board_predictions
        confidences[board_indexes] = board_confidences
    return predicted, confidences


def _decode_king_constrained_predictions(
    probabilities: np.ndarray,
    classes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Return the highest-probability board with exactly one white and black king."""
    class_values = np.asarray([str(value) for value in classes])
    raw_indexes = probabilities.argmax(axis=1)
    raw_predictions = class_values[raw_indexes]
    raw_confidences = probabilities[np.arange(len(raw_indexes)), raw_indexes]
    default = {
        "policy": "raw_argmax",
        "constraint_applied": False,
        "changed_square_count": 0,
    }
    if (
        probabilities.shape[0] != 64
        or probabilities.shape[1] != len(class_values)
        or "K" not in class_values
        or "k" not in class_values
    ):
        return raw_predictions, raw_confidences, default

    white_king_index = int(np.where(class_values == "K")[0][0])
    black_king_index = int(np.where(class_values == "k")[0][0])
    non_king_indexes = np.asarray(
        [index for index, label in enumerate(class_values) if label not in {"K", "k"}],
        dtype=np.int64,
    )
    best_non_king = non_king_indexes[
        probabilities[:, non_king_indexes].argmax(axis=1)
    ]
    clipped = np.clip(probabilities, 1e-12, 1.0)
    base_scores = np.log(clipped[np.arange(64), best_non_king])
    white_deltas = np.log(clipped[:, white_king_index]) - base_scores
    black_deltas = np.log(clipped[:, black_king_index]) - base_scores

    best_score = float("-inf")
    best_white_square = 0
    best_black_square = 1
    for white_square in range(64):
        pair_scores = white_deltas[white_square] + black_deltas
        pair_scores = pair_scores.copy()
        pair_scores[white_square] = float("-inf")
        black_square = int(pair_scores.argmax())
        score = float(pair_scores[black_square])
        if score > best_score:
            best_score = score
            best_white_square = white_square
            best_black_square = black_square

    decoded_indexes = best_non_king.copy()
    decoded_indexes[best_white_square] = white_king_index
    decoded_indexes[best_black_square] = black_king_index
    decoded_predictions = class_values[decoded_indexes]
    decoded_confidences = probabilities[np.arange(64), decoded_indexes]
    changed_count = int((decoded_indexes != raw_indexes).sum())
    return decoded_predictions, decoded_confidences, {
        "policy": "exactly_one_king_per_color",
        "constraint_applied": bool(changed_count),
        "changed_square_count": changed_count,
        "white_king_square_index": best_white_square,
        "black_king_square_index": best_black_square,
        "confidence": "assigned_class_probability",
    }


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
