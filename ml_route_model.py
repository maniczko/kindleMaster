from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from ml_features import ROUTE_MODEL_FEATURE_ORDER, normalize_route_features, route_feature_vector, route_features_hash


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_ROUTE_MODEL_PATH = REPO_ROOT / "models" / "route_classifier_v1.json"
ROUTE_MODEL_MODES = ("off", "shadow", "assist")
DEFAULT_ASSIST_CONFIDENCE = 0.82
DEFAULT_HEURISTIC_MAX_CONFIDENCE = 0.70
PROTECTED_ASSIST_CLASSES = frozenset({"diagram_book_reflow", "scanned_reflow"})


def normalize_route_model_mode(mode: str | None) -> str:
    normalized = str(mode or "shadow").strip().lower()
    return normalized if normalized in ROUTE_MODEL_MODES else "shadow"


def load_route_model(model_path: str | Path | None = None) -> dict[str, Any] | None:
    path = Path(model_path or DEFAULT_ROUTE_MODEL_PATH)
    if not path.exists():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return _load_route_model_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=8)
def _load_route_model_cached(path: str, _mtime_ns: int, _size: int) -> dict[str, Any] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("model_type") != "multinomial_logistic_regression":
        return None
    classes = payload.get("classes")
    feature_order = payload.get("feature_order")
    weights = payload.get("weights")
    intercepts = payload.get("intercepts")
    if not isinstance(classes, list) or not isinstance(feature_order, list):
        return None
    if not isinstance(weights, dict) or not isinstance(intercepts, dict):
        return None
    return payload


def predict_route(
    features: Mapping[str, Any],
    *,
    model: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    route_model = dict(model or load_route_model() or {})
    if not route_model:
        return {
            "profile": "",
            "confidence": 0.0,
            "scores": {},
            "model_version": "",
            "available": False,
        }
    feature_order = list(route_model.get("feature_order") or ROUTE_MODEL_FEATURE_ORDER)
    vector = route_feature_vector(features, feature_order=feature_order)
    scaled = _scale_vector(vector, route_model.get("scaler"))
    classes = [str(item) for item in route_model.get("classes", [])]
    logits: list[float] = []
    for class_name in classes:
        class_weights = _float_list((route_model.get("weights") or {}).get(class_name), expected=len(feature_order))
        intercept = _float_value((route_model.get("intercepts") or {}).get(class_name, 0.0))
        logits.append(intercept + sum(weight * value for weight, value in zip(class_weights, scaled)))
    probabilities = _softmax(logits)
    scores = {class_name: round(probability, 6) for class_name, probability in zip(classes, probabilities)}
    if not scores:
        return {
            "profile": "",
            "confidence": 0.0,
            "scores": {},
            "model_version": str(route_model.get("model_version", "") or ""),
            "available": False,
        }
    profile, confidence = max(scores.items(), key=lambda item: item[1])
    return {
        "profile": profile,
        "confidence": round(float(confidence), 6),
        "scores": scores,
        "model_version": str(route_model.get("model_version", "") or ""),
        "available": True,
    }


def build_route_decision(
    *,
    heuristic_profile: str,
    heuristic_confidence: float,
    features: Mapping[str, Any],
    mode: str | None = "shadow",
    model: Mapping[str, Any] | None = None,
    allow_override: bool = True,
) -> dict[str, Any]:
    normalized_mode = normalize_route_model_mode(mode)
    normalized_features = normalize_route_features(features)
    reason_codes: list[str] = []
    if normalized_mode == "off":
        reason_codes.append("route-model-off")
        return _route_decision_payload(
            heuristic_profile=heuristic_profile,
            heuristic_confidence=heuristic_confidence,
            ml_profile="",
            ml_confidence=0.0,
            selected_profile=heuristic_profile,
            mode="off",
            override_used=False,
            reason_codes=reason_codes,
            model_version="",
            features=normalized_features,
            scores={},
        )

    route_model = dict(model or load_route_model() or {})
    prediction = predict_route(normalized_features, model=route_model)
    if not prediction.get("available"):
        reason_codes.append("route-model-unavailable")
        return _route_decision_payload(
            heuristic_profile=heuristic_profile,
            heuristic_confidence=heuristic_confidence,
            ml_profile="",
            ml_confidence=0.0,
            selected_profile=heuristic_profile,
            mode=normalized_mode,
            override_used=False,
            reason_codes=reason_codes,
            model_version="",
            features=normalized_features,
            scores={},
        )

    ml_profile = str(prediction.get("profile", "") or "")
    ml_confidence = float(prediction.get("confidence", 0.0) or 0.0)
    selected_profile = heuristic_profile
    override_used = False
    reason_codes.append("shadow-reporting" if normalized_mode == "shadow" else "assist-evaluated")

    if normalized_mode == "assist":
        allowed, assist_reasons = should_apply_assist_override(
            heuristic_profile=heuristic_profile,
            heuristic_confidence=heuristic_confidence,
            ml_profile=ml_profile,
            ml_confidence=ml_confidence,
            features=normalized_features,
            model=route_model,
            allow_override=allow_override,
        )
        reason_codes.extend(assist_reasons)
        if allowed:
            selected_profile = ml_profile
            override_used = True

    return _route_decision_payload(
        heuristic_profile=heuristic_profile,
        heuristic_confidence=heuristic_confidence,
        ml_profile=ml_profile,
        ml_confidence=ml_confidence,
        selected_profile=selected_profile,
        mode=normalized_mode,
        override_used=override_used,
        reason_codes=reason_codes,
        model_version=str(prediction.get("model_version", "") or ""),
        features=normalized_features,
        scores=dict(prediction.get("scores") or {}),
    )


def should_apply_assist_override(
    *,
    heuristic_profile: str,
    heuristic_confidence: float,
    ml_profile: str,
    ml_confidence: float,
    features: Mapping[str, Any],
    model: Mapping[str, Any] | None = None,
    allow_override: bool = True,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    thresholds = dict((model or {}).get("thresholds") or {})
    confidence_threshold = float(thresholds.get("assist_confidence", DEFAULT_ASSIST_CONFIDENCE) or DEFAULT_ASSIST_CONFIDENCE)
    heuristic_threshold = float(
        thresholds.get("max_heuristic_confidence_for_override", DEFAULT_HEURISTIC_MAX_CONFIDENCE)
        or DEFAULT_HEURISTIC_MAX_CONFIDENCE
    )
    protected_classes = set(thresholds.get("protected_classes") or PROTECTED_ASSIST_CLASSES)

    if not allow_override:
        return False, ["override-disabled"]
    if not ml_profile or ml_profile == heuristic_profile:
        return False, ["same-profile"]
    if str(features.get("input_type", "")).lower() == "docx":
        return False, ["docx-route-locked"]
    if ml_confidence < confidence_threshold:
        return False, ["ml-confidence-below-threshold"]
    if heuristic_confidence >= heuristic_threshold:
        return False, ["heuristic-confidence-high"]
    if ml_profile in protected_classes and not _has_protected_class_signal(ml_profile, features):
        return False, [f"protected-class-without-signal:{ml_profile}"]
    reasons.append("assist-override")
    return True, reasons


def _route_decision_payload(
    *,
    heuristic_profile: str,
    heuristic_confidence: float,
    ml_profile: str,
    ml_confidence: float,
    selected_profile: str,
    mode: str,
    override_used: bool,
    reason_codes: list[str],
    model_version: str,
    features: Mapping[str, Any],
    scores: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "heuristic_profile": str(heuristic_profile or ""),
        "heuristic_confidence": round(float(heuristic_confidence or 0.0), 3),
        "ml_profile": str(ml_profile or ""),
        "ml_confidence": round(float(ml_confidence or 0.0), 6),
        "selected_profile": str(selected_profile or heuristic_profile or ""),
        "mode": mode,
        "override_used": bool(override_used),
        "reason_codes": list(reason_codes),
        "model_version": str(model_version or ""),
        "input_features_hash": route_features_hash(features),
        "scores": dict(scores),
    }


def _has_protected_class_signal(profile: str, features: Mapping[str, Any]) -> bool:
    if profile == "diagram_book_reflow":
        return bool(features.get("has_diagrams"))
    if profile == "scanned_reflow":
        return bool(features.get("scanned_page_ratio", 0.0) >= 0.55)
    return True


def _scale_vector(vector: list[float], scaler: Any) -> list[float]:
    if not isinstance(scaler, Mapping):
        return vector
    means = _float_list(scaler.get("mean"), expected=len(vector), default=0.0)
    scales = _float_list(scaler.get("scale"), expected=len(vector), default=1.0)
    return [(value - mean) / (scale if abs(scale) > 1e-12 else 1.0) for value, mean, scale in zip(vector, means, scales)]


def _softmax(logits: list[float]) -> list[float]:
    if not logits:
        return []
    offset = max(logits)
    exps = [math.exp(max(-60.0, min(60.0, value - offset))) for value in logits]
    total = sum(exps) or 1.0
    return [value / total for value in exps]


def _float_list(value: Any, *, expected: int, default: float = 0.0) -> list[float]:
    values = list(value or []) if isinstance(value, (list, tuple)) else []
    normalized = [_float_value(item) for item in values[:expected]]
    if len(normalized) < expected:
        normalized.extend([default] * (expected - len(normalized)))
    return normalized


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
