from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROUTE_LABELS = (
    "book_reflow",
    "magazine_reflow",
    "diagram_book_reflow",
    "scanned_reflow",
    "docx_reflow",
    "fixed_layout_fallback",
)

ROUTE_FEATURE_FIELDS = (
    "input_type",
    "page_count",
    "text_page_ratio",
    "scanned_page_ratio",
    "image_page_ratio",
    "visual_density",
    "dominant_visual_ratio",
    "text_block_density",
    "layout_entropy",
    "has_toc",
    "toc_depth",
    "toc_noise_score",
    "has_tables",
    "has_diagrams",
    "diagram_signal_count",
    "chess_signal_count",
    "has_meaningful_images",
    "non_content_ratio",
    "ocr_confidence",
    "ocr_supported",
    "ocr_language_available",
    "estimated_columns",
    "heading_density",
    "font_consistency",
    "layout_heavy",
    "text_heavy",
    "docx_paragraph_count",
    "docx_heading1_count",
    "docx_heading2_count",
    "docx_heading3_count",
    "docx_list_count",
    "docx_table_count",
    "docx_image_count",
    "docx_hyperlink_count",
)

ROUTE_MODEL_FEATURE_ORDER = (
    "input_type=pdf",
    "input_type=docx",
    "page_count",
    "text_page_ratio",
    "scanned_page_ratio",
    "image_page_ratio",
    "visual_density",
    "dominant_visual_ratio",
    "text_block_density",
    "layout_entropy",
    "has_toc",
    "toc_depth",
    "toc_noise_score",
    "has_tables",
    "has_diagrams",
    "diagram_signal_count",
    "chess_signal_count",
    "has_meaningful_images",
    "non_content_ratio",
    "ocr_confidence",
    "ocr_supported",
    "ocr_language_available",
    "estimated_columns",
    "heading_density",
    "font_consistency",
    "layout_heavy",
    "text_heavy",
    "docx_paragraph_count",
    "docx_heading1_count",
    "docx_heading2_count",
    "docx_heading3_count",
    "docx_list_count",
    "docx_table_count",
    "docx_image_count",
    "docx_hyperlink_count",
)


def route_feature_payload(
    analysis: Any,
    *,
    input_type: str,
    docx_counts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "input_type": _clean_input_type(input_type),
        "page_count": _get_int(analysis, "page_count"),
        "text_page_ratio": _ratio(_get_int(analysis, "text_pages"), _get_int(analysis, "page_count")),
        "scanned_page_ratio": _ratio(_get_int(analysis, "scanned_pages"), _get_int(analysis, "page_count")),
        "image_page_ratio": _ratio(_get_int(analysis, "image_pages"), _get_int(analysis, "page_count")),
        "visual_density": _get_float(analysis, "visual_density"),
        "dominant_visual_ratio": _get_float(analysis, "dominant_visual_ratio"),
        "text_block_density": _get_float(analysis, "text_block_density"),
        "layout_entropy": _get_float(analysis, "layout_entropy"),
        "has_toc": _get_bool(analysis, "has_toc"),
        "toc_depth": _get_int(analysis, "toc_depth"),
        "toc_noise_score": _get_float(analysis, "toc_noise_score"),
        "has_tables": _get_bool(analysis, "has_tables"),
        "has_diagrams": _get_bool(analysis, "has_diagrams"),
        "diagram_signal_count": _get_int(analysis, "diagram_signal_count"),
        "chess_signal_count": _get_int(analysis, "chess_signal_count"),
        "has_meaningful_images": _get_bool(analysis, "has_meaningful_images"),
        "non_content_ratio": _get_float(analysis, "non_content_ratio"),
        "ocr_confidence": _get_float(analysis, "ocr_confidence"),
        "ocr_supported": _get_bool(analysis, "ocr_supported"),
        "ocr_language_available": _get_bool(analysis, "ocr_language_available"),
        "estimated_columns": max(1, _get_int(analysis, "estimated_columns", default=1)),
        "heading_density": _get_float(analysis, "heading_density"),
        "font_consistency": _get_float(analysis, "font_consistency", default=1.0),
        "layout_heavy": _get_bool(analysis, "layout_heavy"),
        "text_heavy": _get_bool(analysis, "text_heavy"),
    }
    if _get_float(analysis, "text_page_ratio", default=-1.0) >= 0:
        payload["text_page_ratio"] = _clamp01(_get_float(analysis, "text_page_ratio"))
    if _get_float(analysis, "scanned_page_ratio", default=-1.0) >= 0:
        payload["scanned_page_ratio"] = _clamp01(_get_float(analysis, "scanned_page_ratio"))
    if _get_float(analysis, "image_page_ratio", default=-1.0) >= 0:
        payload["image_page_ratio"] = _clamp01(_get_float(analysis, "image_page_ratio"))
    if payload["visual_density"] <= 0:
        payload["visual_density"] = payload["image_page_ratio"]
    if payload["dominant_visual_ratio"] <= 0:
        payload["dominant_visual_ratio"] = payload["image_page_ratio"]
    if payload["ocr_confidence"] <= 0 and payload["text_page_ratio"] >= 0.8 and payload["scanned_page_ratio"] <= 0.1:
        payload["ocr_confidence"] = 1.0

    payload.update(_docx_feature_counts(docx_counts or {}))
    return normalize_route_features(payload)


def docx_route_feature_payload(analysis: Mapping[str, Any]) -> dict[str, Any]:
    publication_analysis = analysis.get("publication_analysis") or {}
    counts = {
        "paragraph_count": analysis.get("paragraph_count", 0),
        "heading1_count": analysis.get("heading1_count", 0),
        "heading2_count": analysis.get("heading2_count", 0),
        "heading3_count": analysis.get("heading3_count", 0),
        "list_count": analysis.get("list_count", 0),
        "table_count": analysis.get("table_count", 0),
        "image_count": analysis.get("image_count", 0),
        "hyperlink_count": analysis.get("hyperlink_count", 0),
    }
    return route_feature_payload(publication_analysis, input_type="docx", docx_counts=counts)


def normalize_route_features(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    normalized: dict[str, Any] = {}
    for field in ROUTE_FEATURE_FIELDS:
        if field == "input_type":
            normalized[field] = _clean_input_type(raw.get(field, "pdf"))
        elif field.startswith("has_") or field in {"layout_heavy", "text_heavy", "ocr_supported", "ocr_language_available"}:
            normalized[field] = _bool_value(raw.get(field, False))
        elif field.endswith("_ratio") or field in {
            "heading_density",
            "font_consistency",
            "visual_density",
            "dominant_visual_ratio",
            "layout_entropy",
            "toc_noise_score",
            "non_content_ratio",
            "ocr_confidence",
        }:
            normalized[field] = round(_clamp01(_float_value(raw.get(field, 0.0))), 6)
        elif field == "estimated_columns":
            normalized[field] = max(1, _int_value(raw.get(field, 1)))
        elif field == "text_block_density":
            normalized[field] = round(max(0.0, _float_value(raw.get(field, 0.0))), 6)
        else:
            normalized[field] = max(0, _int_value(raw.get(field, 0)))
    return normalized


def route_feature_vector(
    payload: Mapping[str, Any] | None,
    *,
    feature_order: list[str] | tuple[str, ...] | None = None,
) -> list[float]:
    features = normalize_route_features(payload)
    order = tuple(feature_order or ROUTE_MODEL_FEATURE_ORDER)
    return [_feature_value(features, feature_name) for feature_name in order]


def route_features_hash(payload: Mapping[str, Any] | None) -> str:
    normalized = normalize_route_features(payload)
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def route_example_from_analysis(
    *,
    case_id: str,
    input_path: str | Path,
    input_type: str,
    label: str,
    analysis: Any,
    document_class: str = "",
    language: str = "",
) -> dict[str, Any]:
    features = (
        docx_route_feature_payload(analysis)
        if _clean_input_type(input_type) == "docx" and isinstance(analysis, Mapping)
        else route_feature_payload(analysis, input_type=input_type)
    )
    return {
        "case_id": case_id,
        "input_path": str(input_path),
        "input_type": _clean_input_type(input_type),
        "document_class": str(document_class or ""),
        "language": str(language or ""),
        "label": str(label or ""),
        "features": features,
        "features_hash": route_features_hash(features),
        "heuristic_profile": _get_str(analysis, "profile"),
        "heuristic_confidence": round(_get_float(analysis, "confidence"), 3),
    }


def _docx_feature_counts(counts: Mapping[str, Any]) -> dict[str, int]:
    return {
        "docx_paragraph_count": _int_value(counts.get("paragraph_count", counts.get("docx_paragraph_count", 0))),
        "docx_heading1_count": _int_value(counts.get("heading1_count", counts.get("docx_heading1_count", 0))),
        "docx_heading2_count": _int_value(counts.get("heading2_count", counts.get("docx_heading2_count", 0))),
        "docx_heading3_count": _int_value(counts.get("heading3_count", counts.get("docx_heading3_count", 0))),
        "docx_list_count": _int_value(counts.get("list_count", counts.get("docx_list_count", 0))),
        "docx_table_count": _int_value(counts.get("table_count", counts.get("docx_table_count", 0))),
        "docx_image_count": _int_value(counts.get("image_count", counts.get("docx_image_count", 0))),
        "docx_hyperlink_count": _int_value(counts.get("hyperlink_count", counts.get("docx_hyperlink_count", 0))),
    }


def _feature_value(features: Mapping[str, Any], feature_name: str) -> float:
    if "=" in feature_name:
        key, expected = feature_name.split("=", 1)
        return 1.0 if str(features.get(key, "")).strip().lower() == expected.strip().lower() else 0.0
    value = features.get(feature_name, 0.0)
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _get_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        if key in source:
            return source.get(key)
        nested = source.get("publication_analysis")
        if isinstance(nested, Mapping) and key in nested:
            return nested.get(key)
        return default
    return getattr(source, key, default)


def _get_int(source: Any, key: str, default: int = 0) -> int:
    return _int_value(_get_value(source, key, default))


def _get_float(source: Any, key: str, default: float = 0.0) -> float:
    return _float_value(_get_value(source, key, default))


def _get_bool(source: Any, key: str, default: bool = False) -> bool:
    return _bool_value(_get_value(source, key, default))


def _get_str(source: Any, key: str, default: str = "") -> str:
    value = _get_value(source, key, default)
    return str(value or "").strip()


def _int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "y"}
    return bool(value)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp01(float(numerator) / max(float(denominator), 1.0))


def _clamp01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _clean_input_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().lstrip(".")
    if normalized in {"pdf", "docx"}:
        return normalized
    return "pdf"
