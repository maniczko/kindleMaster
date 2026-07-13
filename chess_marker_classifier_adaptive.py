from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from PIL import Image


ADAPTIVE_CLASSIFIER_VERSION = "marker_adaptive_v3"
DEFAULT_GRAMMAR_PROFILE = "yusupov-fundamentals"
GRAMMAR_PROFILES: dict[str, dict[str, Any]] = {
    "yusupov-fundamentals": {
        "name": "yusupov-fundamentals",
        "white": {"orientation": "upright", "fill": "outline", "side": "w", "symbol": "△"},
        "black": {"orientation": "inverted", "fill": "filled", "side": "b", "symbol": "▼"},
        "minimum_triangularity": 0.52,
        "minimum_orientation_confidence": 0.20,
        "minimum_raw_confidence": 0.82,
        "minimum_dominance_margin": 0.14,
        "maximum_outside_ink_ratio": 0.20,
        "minimum_raw_contrast": 0.32,
        "maximum_component_aspect": 1.62,
        "minimum_stroke_evidence": 1.25,
    },
    "synthetic-baseline": {
        "name": "synthetic-baseline",
        "white": {"orientation": "any", "fill": "outline", "side": "w", "symbol": "△"},
        "black": {"orientation": "any", "fill": "filled", "side": "b", "symbol": "▼"},
        "minimum_triangularity": 0.48,
        "minimum_orientation_confidence": 0.0,
        "minimum_raw_confidence": 0.78,
        "minimum_dominance_margin": 0.12,
        "maximum_outside_ink_ratio": 0.16,
        "minimum_raw_contrast": 0.42,
        "maximum_component_aspect": 1.62,
        "minimum_stroke_evidence": 1.25,
    },
}


def classify_marker_crop_adaptive(
    crop: Image.Image,
    *,
    source_profile: str | Mapping[str, Any] = DEFAULT_GRAMMAR_PROFILE,
    board_cell_size: float | None = None,
    confidence_calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a marker using adaptive segmentation and normalized geometry."""
    grammar = _grammar(source_profile)
    raw = np.asarray(crop.convert("L"), dtype=np.uint8)
    if raw.ndim != 2 or min(raw.shape) < 12:
        return _result(status="marker_missing", reason="crop_too_small", grammar=grammar)
    normalized = _normalize_grayscale(raw)
    variants = _segmentation_variants(normalized)
    candidates = _candidate_bank(
        variants,
        raw_grayscale=raw,
        crop_shape=normalized.shape,
        board_cell_size=board_cell_size,
        grammar=grammar,
    )
    if not candidates:
        return _result(
            status="marker_missing",
            reason="marker_missing",
            grammar=grammar,
            segmentation_methods=[name for name, _ in variants],
        )

    ranked = sorted(candidates, key=lambda row: float(row.get("score") or 0.0), reverse=True)
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    dominance_margin = (
        float(best.get("score") or 0.0) - float(runner_up.get("score") or 0.0)
        if runner_up is not None
        else 1.0
    )
    best["dominance_margin"] = round(dominance_margin, 4)
    if runner_up is not None and _competitive_runner_up(best, runner_up, grammar=grammar):
        conflicting = (
            best.get("grammar_side") in {"w", "b"}
            and runner_up.get("grammar_side") in {"w", "b"}
            and best.get("grammar_side") != runner_up.get("grammar_side")
        )
        return _result(
            status=(
                "side_to_move_marker_local_conflict"
                if conflicting
                else "side_to_move_marker_local_ambiguous"
            ),
            confidence=float(best.get("calibrated_confidence") or 0.0),
            reason="multiple_candidates",
            shape="multiple_triangle_conflict" if conflicting else "multiple_triangle_candidates",
            component=best,
            candidate_count=len(ranked),
            grammar=grammar,
            candidates=ranked,
        )

    raw_confidence = float(best.get("raw_confidence") or 0.0)
    calibrated, calibration_status = calibrate_marker_confidence(
        raw_confidence,
        confidence_calibration,
    )
    best["calibrated_confidence"] = round(calibrated, 4)
    best["calibration_status"] = calibration_status
    grammar_match = best.get("side") in {"w", "b"}
    trust_ready = bool(
        grammar_match
        and best.get("geometry_status") == "accepted"
        and raw_confidence >= float(grammar.get("minimum_raw_confidence") or 0.0)
        and dominance_margin >= float(grammar.get("minimum_dominance_margin") or 0.0)
    )
    if trust_ready:
        return _result(
            status="trusted_marker",
            side=str(best.get("side")),
            symbol=str(best.get("symbol") or "?"),
            confidence=calibrated,
            reason=str(best.get("reason") or "adaptive_triangle"),
            shape=str(best.get("shape") or "adaptive_triangle"),
            component=best,
            candidate_count=len(ranked),
            grammar=grammar,
            candidates=ranked,
            calibration_status=calibration_status,
        )
    return _result(
        status="side_to_move_marker_local_ambiguous",
        confidence=calibrated,
        reason=str(best.get("reason") or "unclear_symbol"),
        shape=str(best.get("shape") or "triangle_like_review"),
        component=best,
        candidate_count=len(ranked),
        grammar=grammar,
        candidates=ranked,
        calibration_status=calibration_status,
    )


def calibrate_marker_confidence(
    raw_confidence: float,
    calibration: Mapping[str, Any] | None,
) -> tuple[float, str]:
    value = min(1.0, max(0.0, float(raw_confidence or 0.0)))
    if not isinstance(calibration, Mapping):
        return value, "profile_default_conservative"
    points = calibration.get("points")
    if not isinstance(points, list) or not points:
        return value, "profile_default_conservative"
    parsed: list[tuple[float, float]] = []
    for point in points:
        if not isinstance(point, Mapping):
            continue
        try:
            raw = float(point.get("raw"))
            calibrated = float(point.get("calibrated"))
        except (TypeError, ValueError):
            continue
        parsed.append((min(1.0, max(0.0, raw)), min(1.0, max(0.0, calibrated))))
    if not parsed:
        return value, "profile_default_conservative"
    parsed.sort()
    if value <= parsed[0][0]:
        return parsed[0][1], "real_calibration_split"
    if value >= parsed[-1][0]:
        return parsed[-1][1], "real_calibration_split"
    for left, right in zip(parsed, parsed[1:]):
        if left[0] <= value <= right[0]:
            span = max(1e-9, right[0] - left[0])
            weight = (value - left[0]) / span
            return left[1] + (right[1] - left[1]) * weight, "real_calibration_split"
    return value, "profile_default_conservative"


def fit_reliability_calibration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fit monotone bin calibration using calibration-split rows only."""
    usable = []
    rejected_non_calibration = 0
    for row in rows:
        if str(row.get("split") or "") != "calibration":
            rejected_non_calibration += 1
            continue
        try:
            raw = min(1.0, max(0.0, float(row.get("raw_confidence") or 0.0)))
        except (TypeError, ValueError):
            continue
        usable.append((raw, bool(row.get("correct"))))
    if not usable:
        return {
            "status": "unavailable",
            "source_split": "calibration",
            "sample_count": 0,
            "rejected_non_calibration_count": rejected_non_calibration,
            "points": [],
        }
    usable.sort()
    bin_count = min(8, max(2, int(round(len(usable) ** 0.5))))
    chunks = np.array_split(np.asarray(usable, dtype=object), bin_count)
    points = []
    previous = 0.0
    for chunk in chunks:
        if not len(chunk):
            continue
        raw_mean = float(np.mean([float(item[0]) for item in chunk]))
        correct = sum(bool(item[1]) for item in chunk)
        empirical = (correct + 1.0) / (len(chunk) + 2.0)
        monotone = max(previous, empirical)
        previous = monotone
        points.append(
            {
                "raw": round(raw_mean, 4),
                "calibrated": round(monotone, 4),
                "count": len(chunk),
                "correct": correct,
            }
        )
    return {
        "status": "fitted",
        "source_split": "calibration",
        "sample_count": len(usable),
        "rejected_non_calibration_count": rejected_non_calibration,
        "points": points,
        "holdout_used_for_tuning": False,
    }


def reliability_metrics(rows: Sequence[Mapping[str, Any]], *, bin_count: int = 10) -> dict[str, Any]:
    usable: list[tuple[float, bool]] = []
    for row in rows:
        try:
            confidence = min(1.0, max(0.0, float(row.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            continue
        usable.append((confidence, bool(row.get("correct"))))
    if not usable:
        return {"sample_count": 0, "brier_score": 0.0, "expected_calibration_error": 0.0, "bins": []}
    bins = []
    ece = 0.0
    for index in range(max(1, int(bin_count))):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        bucket = [item for item in usable if lower <= item[0] < upper or (index == bin_count - 1 and item[0] == 1.0)]
        if not bucket:
            continue
        mean_confidence = float(np.mean([item[0] for item in bucket]))
        accuracy = float(np.mean([1.0 if item[1] else 0.0 for item in bucket]))
        ece += len(bucket) / len(usable) * abs(accuracy - mean_confidence)
        bins.append(
            {
                "lower": round(lower, 3),
                "upper": round(upper, 3),
                "count": len(bucket),
                "mean_confidence": round(mean_confidence, 4),
                "accuracy": round(accuracy, 4),
            }
        )
    brier = float(np.mean([(confidence - (1.0 if correct else 0.0)) ** 2 for confidence, correct in usable]))
    return {
        "sample_count": len(usable),
        "brier_score": round(brier, 4),
        "expected_calibration_error": round(ece, 4),
        "bins": bins,
    }


def _segmentation_variants(grayscale: np.ndarray) -> list[tuple[str, np.ndarray]]:
    otsu = grayscale <= _otsu_threshold(grayscale)
    sauvola = _sauvola_mask(grayscale)
    adaptive = _adaptive_mean_mask(grayscale)
    variants = [
        ("otsu", otsu),
        ("otsu_close", _close(otsu)),
        ("otsu_open", _open(otsu)),
        ("sauvola", sauvola),
        ("sauvola_close", _close(sauvola)),
        ("adaptive_mean", adaptive),
        ("adaptive_mean_close", _close(adaptive)),
    ]
    return [(name, mask) for name, mask in variants if float(mask.mean()) < 0.72]


def _candidate_bank(
    variants: Sequence[tuple[str, np.ndarray]],
    *,
    raw_grayscale: np.ndarray,
    crop_shape: tuple[int, int],
    board_cell_size: float | None,
    grammar: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for method, mask in variants:
        for component in _connected_components(mask):
            features = _component_features(
                mask,
                component,
                raw_grayscale=raw_grayscale,
                crop_shape=crop_shape,
                board_cell_size=board_cell_size,
            )
            if features is None:
                continue
            classified = _classify_features(features, grammar=grammar)
            classified["segmentation_methods"] = [method]
            merged = False
            for existing in candidates:
                if _bbox_iou(existing.get("bbox"), classified.get("bbox")) >= 0.62:
                    existing_methods = set(existing.get("segmentation_methods") or [])
                    existing_methods.add(method)
                    if float(classified.get("score") or 0.0) > float(existing.get("score") or 0.0):
                        methods = sorted(existing_methods)
                        existing.clear()
                        existing.update(classified)
                        existing["segmentation_methods"] = methods
                    else:
                        existing["segmentation_methods"] = sorted(existing_methods)
                    merged = True
                    break
            if not merged:
                candidates.append(classified)
    variant_count = max(1, len(variants))
    for candidate in candidates:
        agreement = len(candidate.get("segmentation_methods") or []) / variant_count
        candidate["segmentation_agreement"] = round(agreement, 4)
        candidate["score"] = round(
            min(1.0, float(candidate.get("score") or 0.0) * 0.88 + agreement * 0.12),
            4,
        )
        candidate["raw_confidence"] = round(
            min(0.995, 0.48 + float(candidate["score"]) * 0.52),
            4,
        )
    return [candidate for candidate in candidates if float(candidate.get("score") or 0.0) >= 0.42]


def _component_features(
    mask: np.ndarray,
    component: Mapping[str, Any],
    *,
    raw_grayscale: np.ndarray,
    crop_shape: tuple[int, int],
    board_cell_size: float | None,
) -> dict[str, Any] | None:
    x0, y0, x1, y1 = (int(value) for value in component["bbox"])
    width = x1 - x0 + 1
    height = y1 - y0 + 1
    crop_height, crop_width = crop_shape
    if width < max(5, round(crop_width * 0.08)) or height < max(5, round(crop_height * 0.08)):
        return None
    if width > crop_width * 0.96 or height > crop_height * 0.96:
        return None
    aspect = width / max(1.0, height)
    if not 0.42 <= aspect <= 1.90:
        return None
    local = mask[y0 : y1 + 1, x0 : x1 + 1]
    local_raw = raw_grayscale[y0 : y1 + 1, x0 : x1 + 1]
    local_ink = float(local.sum())
    outside_ink_ratio = max(0.0, float(mask.sum()) - local_ink) / max(1.0, float(mask.sum()))
    row_spans: list[float] = []
    row_centers: list[float] = []
    row_positions: list[float] = []
    for y, row in enumerate(local):
        xs = np.flatnonzero(row)
        if not len(xs):
            continue
        row_spans.append((int(xs[-1]) - int(xs[0]) + 1) / max(1, width))
        row_centers.append(((float(xs[0]) + float(xs[-1])) / 2.0) / max(1, width - 1))
        row_positions.append(y / max(1, height - 1))
    if len(row_spans) < max(4, round(height * 0.35)):
        return None
    correlation = _correlation(row_positions, row_spans)
    orientation = "upright" if correlation >= 0.20 else "inverted" if correlation <= -0.20 else "unknown"
    orientation_confidence = min(1.0, abs(correlation))
    ideal = _ideal_triangle_mask(height, width, orientation if orientation != "unknown" else "upright")
    inner = _ideal_triangle_mask(height, width, orientation if orientation != "unknown" else "upright", inset=0.18)
    border = ideal & ~inner
    outside_ratio = float((local & ~ideal).sum()) / max(1.0, float(local.sum()))
    interior_density = float(local[inner].mean()) if int(inner.sum()) else 0.0
    border_density = float(local[border].mean()) if int(border.sum()) else 0.0
    fill_ratio = float(local.mean())
    border_to_interior = border_density / max(0.01, interior_density)
    symmetry = max(0.0, 1.0 - float(np.mean([abs(center - 0.5) for center in row_centers])) * 2.5)
    row_coverage = len(row_spans) / max(1, height)
    triangularity = min(
        1.0,
        orientation_confidence * 0.38
        + max(0.0, 1.0 - outside_ratio) * 0.27
        + symmetry * 0.20
        + row_coverage * 0.15,
    )
    if interior_density >= 0.48 and fill_ratio >= 0.30:
        fill_state = "filled"
        fill_confidence = min(1.0, 0.55 + (interior_density - 0.48) * 0.9 + fill_ratio * 0.25)
    elif interior_density <= 0.34 and border_density >= 0.10 and border_to_interior >= 1.25:
        fill_state = "outline"
        fill_confidence = min(1.0, 0.58 + (0.34 - interior_density) * 0.8 + min(0.25, border_density * 0.35))
    else:
        fill_state = "unknown"
        fill_confidence = max(0.0, 0.45 - abs(interior_density - 0.41))
    cell = max(1.0, float(board_cell_size or min(crop_width, crop_height)))
    touches_edge = x0 <= 0 or y0 <= 0 or x1 >= crop_width - 1 or y1 >= crop_height - 1
    raw_low, raw_high = np.percentile(local_raw, (5.0, 95.0))
    raw_contrast = max(0.0, float(raw_high - raw_low) / 255.0)
    stroke_evidence = float(component.get("area") or 0.0) / max(1.0, width + 2.0 * height)
    return {
        "area": float(component.get("area") or 0.0),
        "bbox": (float(x0), float(y0), float(x1), float(y1)),
        "width": float(width),
        "height": float(height),
        "aspect": round(aspect, 4),
        "density": round(fill_ratio, 4),
        "fill_ratio": round(fill_ratio, 4),
        "inner_density": round(interior_density, 4),
        "border_density": round(border_density, 4),
        "border_to_interior_ratio": round(border_to_interior, 4),
        "triangularity": round(triangularity, 4),
        "contour_triangle_support": round(max(0.0, 1.0 - outside_ratio), 4),
        "orientation": orientation,
        "orientation_confidence": round(orientation_confidence, 4),
        "fill_state": fill_state,
        "fill_confidence": round(fill_confidence, 4),
        "size_to_board_cell": round(max(width, height) / cell, 4),
        "board_cell_context": board_cell_size is not None,
        "size_to_crop": round(max(width / crop_width, height / crop_height), 4),
        "touches_crop_edge": touches_edge,
        "outside_ink_ratio": round(outside_ink_ratio, 4),
        "raw_contrast": round(raw_contrast, 4),
        "stroke_evidence": round(stroke_evidence, 4),
    }


def _classify_features(features: Mapping[str, Any], *, grammar: Mapping[str, Any]) -> dict[str, Any]:
    orientation = str(features.get("orientation") or "unknown")
    fill_state = str(features.get("fill_state") or "unknown")
    triangularity = float(features.get("triangularity") or 0.0)
    orientation_confidence = float(features.get("orientation_confidence") or 0.0)
    size_to_crop = float(features.get("size_to_crop") or 0.0)
    grammar_row = None
    for role in ("white", "black"):
        expected = grammar.get(role) if isinstance(grammar.get(role), Mapping) else {}
        expected_orientation = str(expected.get("orientation") or "")
        if fill_state != str(expected.get("fill") or ""):
            continue
        if expected_orientation not in {"any", orientation}:
            continue
        grammar_row = expected
        break
    geometry_ok = (
        triangularity >= float(grammar.get("minimum_triangularity") or 0.0)
        and orientation_confidence >= float(grammar.get("minimum_orientation_confidence") or 0.0)
        and 0.14 <= size_to_crop <= 0.92
        and float(features.get("aspect") or 0.0)
        <= float(grammar.get("maximum_component_aspect") or 0.0)
        and (
            not bool(features.get("touches_crop_edge"))
            or (
                bool(features.get("board_cell_context"))
                and str(features.get("fill_state") or "") == "filled"
            )
        )
        and float(features.get("outside_ink_ratio") or 0.0)
        <= float(grammar.get("maximum_outside_ink_ratio") or 0.0)
        + (0.28 if features.get("board_cell_context") else 0.0)
        and float(features.get("raw_contrast") or 0.0)
        >= float(grammar.get("minimum_raw_contrast") or 0.0)
        and float(features.get("stroke_evidence") or 0.0)
        >= float(grammar.get("minimum_stroke_evidence") or 0.0)
    )
    score = (
        triangularity * 0.40
        + orientation_confidence * 0.20
        + float(features.get("fill_confidence") or 0.0) * 0.22
        + float(features.get("contour_triangle_support") or 0.0) * 0.18
    )
    result = dict(features)
    result.update(
        {
            "score": round(min(1.0, score), 4),
            "geometry_status": "accepted" if geometry_ok else "review",
            "side": str(grammar_row.get("side") or "") if grammar_row and geometry_ok else "",
            "grammar_side": str(grammar_row.get("side") or "") if grammar_row else "",
            "symbol": str(grammar_row.get("symbol") or "?") if grammar_row and geometry_ok else "?",
            "shape": f"{fill_state}_{orientation}_triangle" if fill_state != "unknown" else "triangle_like_review",
            "reason": (
                f"adaptive_{fill_state}_{orientation}_triangle"
                if grammar_row and geometry_ok
                else "geometry_review"
                if grammar_row
                else "orientation_fill_disagreement"
                if fill_state in {"outline", "filled"} and orientation in {"upright", "inverted"}
                else "geometry_review"
            ),
        }
    )
    return result


def _competitive_runner_up(
    best: Mapping[str, Any],
    runner_up: Mapping[str, Any],
    *,
    grammar: Mapping[str, Any],
) -> bool:
    runner_score = float(runner_up.get("score") or 0.0)
    margin = float(best.get("score") or 0.0) - runner_score
    return runner_score >= 0.50 and margin < float(grammar.get("minimum_dominance_margin") or 0.0)


def _result(
    *,
    status: str,
    reason: str,
    grammar: Mapping[str, Any],
    side: str = "",
    symbol: str = "?",
    confidence: float = 0.0,
    shape: str = "",
    component: Mapping[str, Any] | None = None,
    candidate_count: int = 0,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    segmentation_methods: Sequence[str] | None = None,
    calibration_status: str = "profile_default_conservative",
) -> dict[str, Any]:
    trusted_side = side if status == "trusted_marker" and side in {"w", "b"} else ""
    warnings = ["side_to_move_marker_probes_checked"]
    if status == "trusted_marker":
        warnings.insert(0, "side_to_move_marker_detected")
    elif status == "side_to_move_marker_local_conflict":
        warnings.insert(0, "side_to_move_marker_local_conflict")
    elif status != "marker_missing":
        warnings.insert(0, "side_to_move_marker_local_ambiguous")
    return {
        "status": status,
        "side": trusted_side,
        "side_to_move": trusted_side or "unknown",
        "symbol": symbol if trusted_side else "?",
        "confidence": round(min(1.0, max(0.0, float(confidence or 0.0))), 4),
        "raw_confidence": round(float((component or {}).get("raw_confidence") or confidence or 0.0), 4),
        "calibration_status": calibration_status,
        "classifier_version": ADAPTIVE_CLASSIFIER_VERSION,
        "source_profile": str(grammar.get("name") or DEFAULT_GRAMMAR_PROFILE),
        "reason": reason,
        "shape": shape,
        "component": dict(component) if isinstance(component, Mapping) else None,
        "candidate_count": int(candidate_count or 0),
        "candidates": [dict(row) for row in candidates or []],
        "segmentation_methods": sorted(
            set(segmentation_methods or (component or {}).get("segmentation_methods") or [])
        ),
        "warnings": warnings,
    }


def _grammar(source_profile: str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(source_profile, Mapping):
        profile = dict(source_profile)
        profile.setdefault("name", "custom")
        return profile
    return dict(GRAMMAR_PROFILES.get(str(source_profile), GRAMMAR_PROFILES[DEFAULT_GRAMMAR_PROFILE]))


def _normalize_grayscale(raw: np.ndarray) -> np.ndarray:
    low, high = np.percentile(raw, (1.0, 99.0))
    if high <= low + 1.0:
        return raw.copy()
    scaled = (raw.astype(np.float32) - float(low)) * (255.0 / float(high - low))
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _otsu_threshold(gray: np.ndarray) -> int:
    histogram = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    total = float(gray.size)
    sum_total = float(np.dot(np.arange(256), histogram))
    weight_background = 0.0
    sum_background = 0.0
    best_variance = -1.0
    best_threshold = 127
    for threshold in range(256):
        weight_background += histogram[threshold]
        if weight_background <= 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground <= 0:
            break
        sum_background += threshold * histogram[threshold]
        mean_background = sum_background / weight_background
        mean_foreground = (sum_total - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def _sauvola_mask(gray: np.ndarray) -> np.ndarray:
    window = _window_size(gray.shape)
    mean = _box_mean(gray.astype(np.float32), window)
    square_mean = _box_mean(gray.astype(np.float32) ** 2, window)
    std = np.sqrt(np.maximum(0.0, square_mean - mean**2))
    threshold = mean * (1.0 + 0.20 * (std / 128.0 - 1.0))
    return gray.astype(np.float32) <= threshold


def _adaptive_mean_mask(gray: np.ndarray) -> np.ndarray:
    window = _window_size(gray.shape)
    mean = _box_mean(gray.astype(np.float32), window)
    local_contrast = np.maximum(4.0, np.sqrt(np.maximum(0.0, _box_mean(gray.astype(np.float32) ** 2, window) - mean**2)) * 0.18)
    return gray.astype(np.float32) <= mean - local_contrast


def _window_size(shape: tuple[int, int]) -> int:
    value = max(9, min(31, int(round(min(shape) * 0.31))))
    return value if value % 2 else value + 1


def _box_mean(values: np.ndarray, window: int) -> np.ndarray:
    radius = window // 2
    padded = np.pad(values, radius, mode="reflect")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    total = integral[window:, window:] - integral[:-window, window:] - integral[window:, :-window] + integral[:-window, :-window]
    return total / float(window * window)


def _dilate(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, mode="constant")
    return np.logical_or.reduce(
        [padded[y : y + mask.shape[0], x : x + mask.shape[1]] for y in range(3) for x in range(3)]
    )


def _erode(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, mode="constant", constant_values=True)
    return np.logical_and.reduce(
        [padded[y : y + mask.shape[0], x : x + mask.shape[1]] for y in range(3) for x in range(3)]
    )


def _close(mask: np.ndarray) -> np.ndarray:
    return _erode(_dilate(mask))


def _open(mask: np.ndarray) -> np.ndarray:
    return _dilate(_erode(mask))


def _connected_components(mask: np.ndarray) -> list[dict[str, Any]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[dict[str, Any]] = []
    minimum_area = max(6, int(round(mask.size * 0.0015)))
    for start_y in range(height):
        for start_x in range(width):
            if visited[start_y, start_x] or not mask[start_y, start_x]:
                continue
            stack = [(start_x, start_y)]
            visited[start_y, start_x] = True
            points: list[tuple[int, int]] = []
            while stack:
                x, y = stack.pop()
                points.append((x, y))
                for dy in (-1, 0, 1):
                    ny = y + dy
                    if not 0 <= ny < height:
                        continue
                    for dx in (-1, 0, 1):
                        nx = x + dx
                        if not 0 <= nx < width:
                            continue
                        if visited[ny, nx] or not mask[ny, nx]:
                            continue
                        visited[ny, nx] = True
                        stack.append((nx, ny))
            if len(points) < minimum_area:
                continue
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            components.append(
                {
                    "area": len(points),
                    "bbox": (min(xs), min(ys), max(xs), max(ys)),
                }
            )
    return components


def _ideal_triangle_mask(height: int, width: int, orientation: str, *, inset: float = -0.04) -> np.ndarray:
    ys, xs = np.mgrid[0:height, 0:width]
    nx = xs / max(1, width - 1)
    ny = ys / max(1, height - 1)
    if orientation == "inverted":
        effective_y = 1.0 - ny
    else:
        effective_y = ny
    half_width = np.maximum(0.0, effective_y * 0.5 - inset)
    return (effective_y >= inset) & (effective_y <= 1.0 - inset) & (np.abs(nx - 0.5) <= half_width)


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) < 2 or len(right) < 2 or len(left) != len(right):
        return 0.0
    left_array = np.asarray(left, dtype=np.float64)
    right_array = np.asarray(right, dtype=np.float64)
    if float(left_array.std()) <= 1e-9 or float(right_array.std()) <= 1e-9:
        return 0.0
    value = float(np.corrcoef(left_array, right_array)[0, 1])
    return value if np.isfinite(value) else 0.0


def _bbox_iou(left: Any, right: Any) -> float:
    if not isinstance(left, (list, tuple)) or len(left) < 4:
        return 0.0
    if not isinstance(right, (list, tuple)) or len(right) < 4:
        return 0.0
    lx0, ly0, lx1, ly1 = (float(value) for value in left[:4])
    rx0, ry0, rx1, ry1 = (float(value) for value in right[:4])
    intersection = max(0.0, min(lx1, rx1) - max(lx0, rx0) + 1.0) * max(
        0.0, min(ly1, ry1) - max(ly0, ry0) + 1.0
    )
    left_area = max(0.0, lx1 - lx0 + 1.0) * max(0.0, ly1 - ly0 + 1.0)
    right_area = max(0.0, rx1 - rx0 + 1.0) * max(0.0, ry1 - ry0 + 1.0)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0
