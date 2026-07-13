from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


DIAGRAM_FINGERPRINT_SCHEMA = "kindlemaster.chess.diagram_fingerprint.v1"
DIAGRAM_RECALL_SCHEMA = "kindlemaster.chess.expected_diagram_recall.v1"
BBOX_QUANTIZATION_GRID = 64


def source_document_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_bbox(
    bbox_xyxy: Sequence[float],
    page_size: Sequence[float],
) -> tuple[float, float, float, float]:
    if len(bbox_xyxy) < 4 or len(page_size) < 2:
        raise ValueError("bbox_xyxy and page_size must contain four and two numeric values respectively")
    page_width = max(1e-9, float(page_size[0]))
    page_height = max(1e-9, float(page_size[1]))
    x0, y0, x1, y1 = (float(value) for value in bbox_xyxy[:4])
    values = (
        min(1.0, max(0.0, x0 / page_width)),
        min(1.0, max(0.0, y0 / page_height)),
        min(1.0, max(0.0, x1 / page_width)),
        min(1.0, max(0.0, y1 / page_height)),
    )
    if values[2] <= values[0] or values[3] <= values[1]:
        raise ValueError("normalized bbox must have positive width and height")
    return values


def diagram_perceptual_hash(image: Image.Image) -> str:
    grayscale = ImageOps.autocontrast(image.convert("L"), cutoff=1)
    reduced = grayscale.resize((9, 8), Image.Resampling.LANCZOS)
    pixel_reader = getattr(reduced, "get_flattened_data", None)
    if not callable(pixel_reader):
        pixel_reader = reduced.getdata
    pixels = list(pixel_reader())
    bits = []
    for row in range(8):
        offset = row * 9
        bits.extend(pixels[offset + column] > pixels[offset + column + 1] for column in range(8))
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def build_diagram_fingerprint(
    *,
    source_sha256: str,
    page: int,
    normalized_bbox_xyxy: Sequence[float],
    board_crop: Image.Image,
) -> dict[str, Any]:
    source_digest = _normalize_source_sha(source_sha256)
    page_number = max(1, int(page))
    normalized = _normalized_bbox_values(normalized_bbox_xyxy)
    quantized = tuple(int(round(value * BBOX_QUANTIZATION_GRID)) for value in normalized)
    perceptual_hash = diagram_perceptual_hash(board_crop)
    material = json.dumps(
        {
            "source_sha256": source_digest,
            "page": page_number,
            "bbox_grid": quantized,
            "perceptual_hash": perceptual_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = "dfp_" + sha256(material.encode("utf-8")).hexdigest()[:32]
    return {
        "schema": DIAGRAM_FINGERPRINT_SCHEMA,
        "diagram_fingerprint": fingerprint,
        "source_document_sha256": source_digest,
        "page": page_number,
        "normalized_bbox_xyxy": [round(value, 6) for value in normalized],
        "bbox_quantization_grid": BBOX_QUANTIZATION_GRID,
        "bbox_quantized": list(quantized),
        "board_perceptual_hash": perceptual_hash,
    }


def load_expected_diagram_manifest(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    candidate = Path(path)
    if not candidate.is_file():
        return {}
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def expected_diagram_records(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("diagrams", "items", "records", "expected_diagrams"):
        value = manifest.get(key)
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def expected_diagram_counts_by_page(manifest: Mapping[str, Any]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for record in expected_diagram_records(manifest):
        try:
            page = int(record.get("page") or 0)
        except (TypeError, ValueError):
            continue
        if page > 0:
            counts[page] = counts.get(page, 0) + 1
    return counts


def measure_expected_diagram_recall(
    detected_records: Iterable[Mapping[str, Any]],
    expected_manifest: Mapping[str, Any],
    *,
    source_sha256: str,
    bbox_iou_threshold: float = 0.50,
) -> dict[str, Any]:
    expected = expected_diagram_records(expected_manifest)
    detected = [dict(record) for record in detected_records if isinstance(record, Mapping)]
    expected_source = _manifest_source_sha(expected_manifest)
    actual_source = _normalize_source_sha(source_sha256)
    if expected_source and expected_source != actual_source:
        return {
            "schema": DIAGRAM_RECALL_SCHEMA,
            "status": "source_mismatch",
            "source_document_sha256": actual_source,
            "expected_source_document_sha256": expected_source,
            "expected_diagram_count": len(expected),
            "detected_diagram_count": len(detected),
            "matched_diagram_count": 0,
            "expected_diagram_recall": 0.0,
            "matches": [],
            "missing_expected": expected,
        }

    detected_by_fingerprint = {
        str(record.get("diagram_fingerprint")): record
        for record in detected
        if str(record.get("diagram_fingerprint") or "").strip()
    }
    used_detected: set[str] = set()
    matches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for index, expected_record in enumerate(expected):
        expected_fingerprint = str(expected_record.get("diagram_fingerprint") or "").strip()
        matched = detected_by_fingerprint.get(expected_fingerprint) if expected_fingerprint else None
        if matched is not None and _record_identity(matched) in used_detected:
            matched = None
        match_method = "fingerprint" if matched is not None else ""
        if matched is None:
            matched = _best_bbox_match(
                expected_record,
                detected,
                used_detected=used_detected,
                iou_threshold=bbox_iou_threshold,
            )
            match_method = "page_normalized_bbox" if matched is not None else ""
        if matched is None:
            missing.append(dict(expected_record))
            continue
        detected_key = _record_identity(matched)
        used_detected.add(detected_key)
        matches.append(
            {
                "expected_index": index,
                "expected_diagram_fingerprint": expected_fingerprint,
                "detected_diagram_fingerprint": str(matched.get("diagram_fingerprint") or ""),
                "detected_diagram_id": str(matched.get("diagram_id") or matched.get("id") or ""),
                "page": int(matched.get("page") or expected_record.get("page") or 0),
                "match_method": match_method,
            }
        )

    expected_count = len(expected)
    matched_count = len(matches)
    return {
        "schema": DIAGRAM_RECALL_SCHEMA,
        "status": "passed" if expected_count and matched_count == expected_count else "incomplete",
        "source_document_sha256": actual_source,
        "expected_source_document_sha256": expected_source or actual_source,
        "expected_diagram_count": expected_count,
        "detected_diagram_count": len(detected),
        "matched_diagram_count": matched_count,
        "expected_diagram_recall": round(matched_count / expected_count, 4) if expected_count else 0.0,
        "matches": matches,
        "missing_expected": missing,
    }


def preferred_record_key(record: Mapping[str, Any]) -> tuple[str, str]:
    fingerprint = str(record.get("diagram_fingerprint") or "").strip()
    if fingerprint:
        return "diagram_fingerprint", fingerprint
    return "diagram_id", str(record.get("diagram_id") or record.get("id") or "").strip()


def _best_bbox_match(
    expected: Mapping[str, Any],
    detected: list[dict[str, Any]],
    *,
    used_detected: set[str],
    iou_threshold: float,
) -> dict[str, Any] | None:
    try:
        expected_page = int(expected.get("page") or 0)
    except (TypeError, ValueError):
        return None
    expected_bbox = _record_normalized_bbox(expected)
    if expected_page <= 0 or expected_bbox is None:
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for record in detected:
        if _record_identity(record) in used_detected:
            continue
        try:
            page = int(record.get("page") or 0)
        except (TypeError, ValueError):
            continue
        detected_bbox = _record_normalized_bbox(record)
        if page != expected_page or detected_bbox is None:
            continue
        score = _bbox_iou(expected_bbox, detected_bbox)
        if score >= float(iou_threshold):
            candidates.append((score, record))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], _record_identity(item[1])), reverse=True)
    return candidates[0][1]


def _record_normalized_bbox(record: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    value = record.get("normalized_bbox_xyxy")
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        components = record.get("diagram_fingerprint_components")
        value = components.get("normalized_bbox_xyxy") if isinstance(components, Mapping) else None
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        return _normalized_bbox_values(value)
    except (TypeError, ValueError):
        return None


def _bbox_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _normalized_bbox_values(value: Sequence[float]) -> tuple[float, float, float, float]:
    if len(value) < 4:
        raise ValueError("normalized bbox needs four values")
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(item))) for item in value[:4])
    if x1 <= x0 or y1 <= y0:
        raise ValueError("normalized bbox must have positive area")
    return x0, y0, x1, y1


def _normalize_source_sha(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.split(":", 1)[1]
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("source_sha256 must be a 64-character hexadecimal SHA256")
    return normalized


def _manifest_source_sha(manifest: Mapping[str, Any]) -> str:
    source = manifest.get("source")
    nested_sha = source.get("sha256") if isinstance(source, Mapping) else ""
    value = str(
        manifest.get("source_document_sha256")
        or manifest.get("source_sha256")
        or nested_sha
        or ""
    ).strip()
    return _normalize_source_sha(value) if value else ""


def _record_identity(record: Mapping[str, Any]) -> str:
    key_type, value = preferred_record_key(record)
    return f"{key_type}:{value}"
