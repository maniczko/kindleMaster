from __future__ import annotations

import argparse
import csv
import html
import io
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping

import fitz
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import (
    ChessFenResult,
    detect_board_candidates_in_page_image,
    load_piece_templates,
    recognize_chess_position_from_image,
    validate_fen,
)
from chess_diagram_fingerprint import (
    build_diagram_fingerprint,
    expected_diagram_counts_by_page,
    load_expected_diagram_manifest,
    measure_expected_diagram_recall,
    normalized_bbox,
    source_document_sha256,
)


DEFAULT_TEMPLATE_DIR = ROOT_DIR / "reference_inputs" / "chess_fen" / "templates" / "fundamenty_merida_like"


def detect_chess_diagrams(
    pdf_path: str | Path,
    *,
    output_dir: str | Path = "dist",
    dpi: int = 300,
    max_candidates_per_page: int = 6,
    min_grid_confidence: float = 0.58,
    min_fen_confidence: float = 0.85,
    template_dir: str | Path | None = DEFAULT_TEMPLATE_DIR,
    pages: int = 0,
    page_ranges: str = "",
    enable_sliding_probe: bool = False,
    include_low_confidence_review_candidates: bool = True,
    low_confidence_min_grid_confidence: float = 0.30,
    low_confidence_max_candidates_per_page: int = 12,
    review_sample_limit: int = 0,
    detection_dpis: tuple[int, ...] | list[int] | None = None,
    adaptive_sliding_probe: bool = True,
    expected_diagram_manifest: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(pdf_path)
    target = Path(output_dir)
    diagrams_dir = target / "assets" / "diagrams"
    low_confidence_dir = target / "assets" / "diagram_candidates"
    diagrams_dir.mkdir(parents=True, exist_ok=True)
    low_confidence_dir.mkdir(parents=True, exist_ok=True)

    templates = _load_templates(template_dir)
    started = time.perf_counter()
    records: list[dict[str, Any]] = []
    low_confidence_records: list[dict[str, Any]] = []
    cv_backend = _optional_cv_backend_status()
    source_sha256 = source_document_sha256(source)
    expected_manifest = load_expected_diagram_manifest(expected_diagram_manifest)
    expected_by_page = expected_diagram_counts_by_page(expected_manifest)
    pass_dpis = _normalized_detection_dpis(dpi, detection_dpis)
    pass_metrics: list[dict[str, Any]] = []

    with fitz.open(source) as document:
        page_count = len(document)
        selected_page_indices = _selected_page_indices(page_count, pages=pages, page_ranges=page_ranges)
        sampled_pages = [index + 1 for index in selected_page_indices]
        for page_index in selected_page_indices:
            page = document[page_index]
            rendered_pages: dict[int, tuple[Image.Image, bytes]] = {}
            strict_envelopes: list[dict[str, Any]] = []
            for pass_index, pass_dpi in enumerate(pass_dpis):
                page_image, page_png = _render_detection_page(page, pass_dpi)
                rendered_pages[pass_dpi] = (page_image, page_png)
                candidates = detect_board_candidates_in_page_image(
                    page_png,
                    max_candidates=max_candidates_per_page,
                    min_grid_confidence=min_grid_confidence,
                    enable_sliding_probe=False,
                )
                strict_envelopes.extend(
                    _candidate_envelopes(
                        candidates,
                        page_image=page_image,
                        page_dpi=pass_dpi,
                        pass_name=f"strict_{pass_index + 1}",
                    )
                )
                pass_metrics.append(
                    {
                        "page": page_index + 1,
                        "pass": f"strict_{pass_index + 1}",
                        "dpi": pass_dpi,
                        "candidate_count": len(candidates),
                        "sliding_probe": False,
                    }
                )

            strict_limit = max(
                int(max_candidates_per_page or 0),
                int(expected_by_page.get(page_index + 1, 0)),
                int(max_candidates_per_page or 0) * len(pass_dpis),
            )
            strict_selected = _merge_detection_envelopes(strict_envelopes, max_count=strict_limit)
            expected_count = int(expected_by_page.get(page_index + 1, 0))
            expected_gap = expected_count > len(strict_selected)
            run_recovery = bool(include_low_confidence_review_candidates or enable_sliding_probe or expected_gap)
            recovered_selected: list[dict[str, Any]] = []
            if run_recovery:
                recovery_dpi = max(pass_dpis)
                page_image, page_png = rendered_pages[recovery_dpi]
                recovery_sliding = bool(enable_sliding_probe or (adaptive_sliding_probe and expected_gap))
                low_candidates = detect_board_candidates_in_page_image(
                    page_png,
                    max_candidates=max(
                        int(max_candidates_per_page or 0),
                        int(low_confidence_max_candidates_per_page or 0),
                        expected_count,
                    ),
                    min_grid_confidence=min(
                        float(min_grid_confidence or 0.0),
                        float(low_confidence_min_grid_confidence or 0.0),
                    ),
                    enable_sliding_probe=recovery_sliding,
                )
                recovery_envelopes = _candidate_envelopes(
                    low_candidates,
                    page_image=page_image,
                    page_dpi=recovery_dpi,
                    pass_name="adaptive_recovery",
                )
                recovered_selected = _recovery_only_envelopes(
                    strict_selected,
                    recovery_envelopes,
                    max_count=max(int(low_confidence_max_candidates_per_page or 0), expected_count),
                )
                pass_metrics.append(
                    {
                        "page": page_index + 1,
                        "pass": "adaptive_recovery",
                        "dpi": recovery_dpi,
                        "candidate_count": len(low_candidates),
                        "new_candidate_count": len(recovered_selected),
                        "sliding_probe": recovery_sliding,
                        "expected_diagram_count": expected_count,
                        "strict_candidate_count": len(strict_selected),
                    }
                )

            strict_selected.sort(key=_visual_envelope_order)
            recovered_selected.sort(key=_visual_envelope_order)
            page_strict_records = [
                _strict_diagram_record(
                    envelope,
                    page=page,
                    page_index=page_index,
                    candidate_index=index,
                    diagrams_dir=diagrams_dir,
                    templates=templates,
                    min_fen_confidence=min_fen_confidence,
                    source_sha256=source_sha256,
                )
                for index, envelope in enumerate(strict_selected, start=1)
            ]
            page_recovered_records = [
                _recovered_diagram_record(
                    envelope,
                    page=page,
                    page_index=page_index,
                    candidate_index=index,
                    low_confidence_dir=low_confidence_dir,
                    source_sha256=source_sha256,
                )
                for index, envelope in enumerate(recovered_selected, start=1)
            ]
            records.extend(page_strict_records)
            low_confidence_records.extend(page_recovered_records)

    canonical_records = [*records, *low_confidence_records]
    expected_recall = (
        measure_expected_diagram_recall(
            canonical_records,
            expected_manifest,
            source_sha256=source_sha256,
        )
        if expected_manifest
        else {
            "status": "not_configured",
            "source_document_sha256": source_sha256,
            "expected_diagram_count": 0,
            "detected_diagram_count": len(canonical_records),
            "matched_diagram_count": 0,
            "expected_diagram_recall": None,
        }
    )
    manifest = {
        "status": "ok",
        "source_pdf": str(source),
        "source_document_sha256": source_sha256,
        "output_dir": str(target),
        "asset_dir": str(diagrams_dir),
        "dpi": int(dpi),
        "detection_dpis": pass_dpis,
        "detection_passes": pass_metrics,
        "multi_pass_detection": len(pass_dpis) > 1,
        "adaptive_sliding_probe": bool(adaptive_sliding_probe),
        "cv_backend": cv_backend,
        "template_dir": str(template_dir or ""),
        "template_count": sum(len(items) for items in templates.values()),
        "page_count": page_count,
        "sampled_pages": sampled_pages,
        "page_ranges": str(page_ranges or ""),
        "page_limit": int(pages or 0),
        "diagram_count": len(canonical_records),
        "strict_diagram_count": len(records),
        "recovered_diagram_count": len(low_confidence_records),
        "accepted_fen_count": sum(1 for record in records if record.get("status") == "accepted"),
        "review_count": sum(1 for record in canonical_records if record.get("status") != "accepted"),
        "low_confidence_review_count": len(low_confidence_records),
        "low_confidence_review_enabled": bool(include_low_confidence_review_candidates),
        "low_confidence_min_grid_confidence": round(float(low_confidence_min_grid_confidence or 0.0), 3),
        "review_sample_limit": int(review_sample_limit or 0),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "expected_diagram_recall": expected_recall,
        "diagrams": canonical_records,
        "low_confidence_review_candidates": low_confidence_records,
    }
    review_records = canonical_records
    review_paths = _write_diagram_review_dataset(target, review_records, sample_limit=review_sample_limit)
    manifest["review_dataset"] = review_paths
    manifest["review_dataset_count"] = min(len(review_records), int(review_sample_limit or 0)) if int(review_sample_limit or 0) > 0 else len(review_records)
    manifest["board_detection_quality"] = _write_board_detection_quality_artifacts(target, records, low_confidence_records)
    manifest_path = target / "chess_diagrams.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _normalized_detection_dpis(primary_dpi: int, configured: tuple[int, ...] | list[int] | None) -> list[int]:
    if configured:
        values = [max(72, min(360, int(value))) for value in configured if int(value) > 0]
    else:
        primary = max(72, min(360, int(primary_dpi or 160)))
        recovery = max(primary + 48, int(round(primary * 1.35)))
        values = [primary, min(360, recovery)]
    return list(dict.fromkeys(values)) or [160]


def _render_detection_page(page: fitz.Page, dpi: int) -> tuple[Image.Image, bytes]:
    zoom = max(72, int(dpi)) / 72.0
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    page_png = pixmap.tobytes("png")
    return Image.open(io.BytesIO(page_png)).convert("RGB"), page_png


def _candidate_envelopes(
    candidates: list[ChessFenResult],
    *,
    page_image: Image.Image,
    page_dpi: int,
    pass_name: str,
) -> list[dict[str, Any]]:
    envelopes: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.bbox:
            continue
        pixel_bbox = _clamp_pixel_bbox(candidate.bbox, page_image.size)
        if pixel_bbox is None:
            continue
        crop = page_image.crop(pixel_bbox)
        if min(crop.size) < 64:
            continue
        envelopes.append(
            {
                "candidate": candidate,
                "page_image": page_image,
                "pixel_bbox": pixel_bbox,
                "normalized_bbox_xyxy": normalized_bbox(pixel_bbox, page_image.size),
                "confidence": float(candidate.confidence or 0.0),
                "dpi": int(page_dpi),
                "pass_name": pass_name,
                "detection_passes": [
                    {
                        "pass": pass_name,
                        "dpi": int(page_dpi),
                        "confidence": round(float(candidate.confidence or 0.0), 4),
                        "method": str(candidate.method or ""),
                    }
                ],
            }
        )
    return envelopes


def _merge_detection_envelopes(
    envelopes: list[dict[str, Any]],
    *,
    max_count: int,
    iou_threshold: float = 0.45,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    ordered = sorted(
        envelopes,
        key=lambda item: (float(item.get("confidence") or 0.0), -int(item.get("dpi") or 0)),
        reverse=True,
    )
    for envelope in ordered:
        duplicate = next(
            (
                existing
                for existing in selected
                if _normalized_bbox_iou(
                    envelope["normalized_bbox_xyxy"],
                    existing["normalized_bbox_xyxy"],
                )
                >= iou_threshold
            ),
            None,
        )
        if duplicate is not None:
            duplicate["detection_passes"].extend(envelope.get("detection_passes") or [])
            continue
        selected.append(dict(envelope))
        if max_count > 0 and len(selected) >= max_count:
            break
    return selected


def _recovery_only_envelopes(
    strict: list[dict[str, Any]],
    recovery: list[dict[str, Any]],
    *,
    max_count: int,
) -> list[dict[str, Any]]:
    merged_recovery = _merge_detection_envelopes(recovery, max_count=max_count)
    return [
        envelope
        for envelope in merged_recovery
        if not any(
            _normalized_bbox_iou(envelope["normalized_bbox_xyxy"], existing["normalized_bbox_xyxy"]) >= 0.45
            for existing in strict
        )
    ][:max_count]


def _strict_diagram_record(
    envelope: dict[str, Any],
    *,
    page: fitz.Page,
    page_index: int,
    candidate_index: int,
    diagrams_dir: Path,
    templates: Mapping[str, list[Image.Image]],
    min_fen_confidence: float,
    source_sha256: str,
) -> dict[str, Any]:
    candidate = envelope["candidate"]
    page_image = envelope["page_image"]
    pixel_bbox = envelope["pixel_bbox"]
    crop = page_image.crop(pixel_bbox)
    diagram_id = f"p{page_index + 1:03d}_d{candidate_index:02d}"
    filename = f"page-{page_index + 1:03d}-diagram-{candidate_index:02d}.webp"
    crop_path = diagrams_dir / filename
    crop.save(crop_path, format="WEBP", quality=88, method=6)
    pdf_bbox = _scale_bbox_to_pdf(pixel_bbox, page_image.size, page.rect)
    fingerprint = build_diagram_fingerprint(
        source_sha256=source_sha256,
        page=page_index + 1,
        normalized_bbox_xyxy=envelope["normalized_bbox_xyxy"],
        board_crop=crop,
    )
    fen_result = _recognize_crop_fen(
        crop,
        candidate=candidate,
        pdf_bbox=pdf_bbox,
        templates=templates,
        min_confidence=min_fen_confidence,
    )
    side_to_move = _explicit_side_to_move(fen_result)
    fen_valid = _fen_accepted(fen_result) and side_to_move in {"w", "b"}
    return {
        "diagram_id": diagram_id,
        "legacy_diagram_id": diagram_id,
        "diagram_fingerprint": fingerprint["diagram_fingerprint"],
        "diagram_fingerprint_components": fingerprint,
        "source_document_sha256": source_sha256,
        "page": page_index + 1,
        "page_index": page_index,
        "label": diagram_id,
        "bbox": _bbox_xywh(pdf_bbox),
        "bbox_xyxy": list(pdf_bbox),
        "normalized_bbox_xyxy": list(envelope["normalized_bbox_xyxy"]),
        "pixel_bbox": _bbox_xywh(pixel_bbox),
        "orientation": "white-bottom",
        "side_to_move": side_to_move,
        "side_to_move_status": fen_result.side_to_move_status if side_to_move in {"w", "b"} else "unknown",
        "side_to_move_evidence": fen_result.side_to_move_evidence if side_to_move in {"w", "b"} else "none",
        "placement": fen_result.placement,
        "placement_fen": fen_result.placement,
        "full_fen": (fen_result.full_fen or fen_result.fen) if fen_valid else "",
        "full_fen_allowed": bool(fen_valid),
        "board_placement_status": "accepted" if fen_valid else "review",
        "fen_suppressed_reason": (
            "side_to_move_not_explicit"
            if side_to_move == "unknown" and (fen_result.full_fen or fen_result.fen)
            else fen_result.to_dict().get("fen_suppressed_reason")
            if hasattr(fen_result, "to_dict")
            else ""
        ),
        "confidence": round(float(candidate.confidence or fen_result.confidence or 0.0), 3),
        "grid_confidence": round(float(candidate.confidence or 0.0), 3),
        "fen_confidence": round(float(fen_result.confidence or 0.0), 3),
        "fen": fen_result.fen if fen_valid else "",
        "fen_candidate": (fen_result.full_fen or fen_result.fen) if side_to_move in {"w", "b"} else "",
        "status": "accepted" if fen_valid else "needs_review",
        "reason": (
            None
            if fen_valid
            else "side_to_move_not_explicit"
            if side_to_move == "unknown" and (fen_result.full_fen or fen_result.fen)
            else _fen_review_reason(fen_result)
        ),
        "warnings": sorted(
            {
                *list(fen_result.warnings or []),
                *(
                    ["side_to_move_not_explicit"]
                    if side_to_move == "unknown" and (fen_result.full_fen or fen_result.fen)
                    else []
                ),
            }
        ),
        "method": fen_result.method or candidate.method,
        "candidate_tier": "strict",
        "detection_dpi": int(envelope["dpi"]),
        "detection_passes": envelope.get("detection_passes") or [],
        "image_path": str(crop_path),
        "image_href": str(Path("assets") / "diagrams" / filename).replace("\\", "/"),
    }


def _recovered_diagram_record(
    envelope: dict[str, Any],
    *,
    page: fitz.Page,
    page_index: int,
    candidate_index: int,
    low_confidence_dir: Path,
    source_sha256: str,
) -> dict[str, Any]:
    candidate = envelope["candidate"]
    page_image = envelope["page_image"]
    pixel_bbox = envelope["pixel_bbox"]
    crop = page_image.crop(pixel_bbox)
    diagram_id = f"p{page_index + 1:03d}_lc{candidate_index:02d}"
    filename = f"page-{page_index + 1:03d}-candidate-{candidate_index:02d}.webp"
    crop_path = low_confidence_dir / filename
    crop.save(crop_path, format="WEBP", quality=82, method=6)
    pdf_bbox = _scale_bbox_to_pdf(pixel_bbox, page_image.size, page.rect)
    fingerprint = build_diagram_fingerprint(
        source_sha256=source_sha256,
        page=page_index + 1,
        normalized_bbox_xyxy=envelope["normalized_bbox_xyxy"],
        board_crop=crop,
    )
    return {
        "diagram_id": diagram_id,
        "legacy_diagram_id": diagram_id,
        "diagram_fingerprint": fingerprint["diagram_fingerprint"],
        "diagram_fingerprint_components": fingerprint,
        "source_document_sha256": source_sha256,
        "page": page_index + 1,
        "page_index": page_index,
        "label": diagram_id,
        "bbox": _bbox_xywh(pdf_bbox),
        "bbox_xyxy": list(pdf_bbox),
        "normalized_bbox_xyxy": list(envelope["normalized_bbox_xyxy"]),
        "pixel_bbox": _bbox_xywh(pixel_bbox),
        "orientation": "unknown",
        "side_to_move": "unknown",
        "side_to_move_status": "unknown",
        "side_to_move_evidence": "none",
        "confidence": round(float(candidate.confidence or 0.0), 3),
        "grid_confidence": round(float(candidate.confidence or 0.0), 3),
        "fen_confidence": 0.0,
        "fen": "",
        "fen_candidate": "",
        "full_fen": "",
        "full_fen_allowed": False,
        "board_placement_status": "review",
        "status": "needs_review",
        "reason": "review_only_low_confidence_candidate",
        "warnings": [
            "review_only_low_confidence_candidate",
            "fen_recognition_skipped_for_review_candidate",
        ],
        "method": candidate.method or "low-confidence-board-candidate",
        "candidate_tier": "recovered",
        "recovery_candidate": True,
        "detection_dpi": int(envelope["dpi"]),
        "detection_passes": envelope.get("detection_passes") or [],
        "image_path": str(crop_path),
        "image_href": str(Path("assets") / "diagram_candidates" / filename).replace("\\", "/"),
        "review_only": True,
    }


def _explicit_side_to_move(result: ChessFenResult) -> str:
    side = str(result.side_to_move or "").strip().lower()
    status = str(result.side_to_move_status or "").strip().lower()
    evidence = str(result.side_to_move_evidence or "").strip().lower()
    trusted_evidence = {"marker", "caption", "verified_label", "exact_label"}
    verified_warning = "verified_exact_crop_label_used" in set(result.warnings or [])
    if side in {"w", "b"} and status == "explicit" and (
        evidence in trusted_evidence or verified_warning
    ):
        return side
    return "unknown"


def _visual_envelope_order(envelope: Mapping[str, Any]) -> tuple[float, float, float]:
    x0, y0, x1, y1 = envelope["normalized_bbox_xyxy"]
    return round(y0, 4), round(x0, 4), -((x1 - x0) * (y1 - y0))


def _normalized_bbox_iou(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _load_templates(template_dir: str | Path | None) -> dict[str, list[Image.Image]]:
    if not template_dir:
        return {}
    path = Path(template_dir)
    if not path.is_dir():
        return {}
    return load_piece_templates(path)


def _optional_cv_backend_status() -> dict[str, Any]:
    try:
        import cv2  # type: ignore

        return {"available": True, "version": getattr(cv2, "__version__", "")}
    except Exception:
        return {"available": False, "reason": "opencv_not_installed"}


def _selected_page_indices(page_count: int, *, pages: int = 0, page_ranges: str = "") -> list[int]:
    explicit_pages = _parse_page_ranges(page_ranges, page_count=page_count)
    if explicit_pages:
        return [page - 1 for page in explicit_pages]
    page_limit = page_count if int(pages or 0) <= 0 else min(page_count, int(pages))
    return list(range(page_limit))


def _parse_page_ranges(value: str, *, page_count: int) -> list[int]:
    text = str(value or "").strip()
    if not text:
        return []
    selected: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = _positive_int(start_text)
            end = _positive_int(end_text)
            if start <= 0 or end <= 0:
                raise ValueError(f"Invalid page range: {token}")
            if end < start:
                start, end = end, start
            for page in range(start, end + 1):
                if 1 <= page <= page_count:
                    selected.add(page)
        else:
            page = _positive_int(token)
            if page <= 0:
                raise ValueError(f"Invalid page number: {token}")
            if 1 <= page <= page_count:
                selected.add(page)
    return sorted(selected)


def _positive_int(value: str) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid page range value: {value}") from exc


def _recognize_crop_fen(
    crop: Image.Image,
    *,
    candidate: ChessFenResult,
    pdf_bbox: tuple[float, float, float, float],
    templates: Mapping[str, list[Image.Image]],
    min_confidence: float,
) -> ChessFenResult:
    buffer = io.BytesIO()
    crop.save(buffer, format="PNG")
    result = recognize_chess_position_from_image(
        buffer.getvalue(),
        bbox=pdf_bbox,
        min_confidence=min_confidence,
        piece_templates=templates,
    )
    if result.board_detected or result.fen:
        return result
    return ChessFenResult(
        confidence=float(candidate.confidence or 0.0),
        bbox=pdf_bbox,
        method=candidate.method or "page-board-candidate",
        warnings=["fen_not_recognized", *list(candidate.warnings or [])],
        requires_review=True,
        board_detected=bool(candidate.board_detected),
    )


def _fen_accepted(result: ChessFenResult) -> bool:
    if result.requires_review or not result.fen:
        return False
    valid, warnings = validate_fen(result.fen)
    if not valid or warnings:
        return False
    try:
        import chess

        chess.Board(result.fen)
    except Exception:
        return False
    return True


def _fen_review_reason(result: ChessFenResult) -> str:
    if not result.board_detected:
        return "board_not_detected"
    if not result.fen:
        return "fen_not_recognized"
    valid, warnings = validate_fen(result.fen)
    if not valid:
        return "fen_invalid"
    if warnings:
        return ",".join(warnings)
    if result.requires_review:
        return "fen_below_acceptance_threshold"
    return "fen_requires_review"


def _build_board_detection_quality_records(records: list[dict], low_confidence_records: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for candidate_kind, source_rows in [("strict", records), ("low_confidence_review", low_confidence_records)]:
        for record in source_rows:
            warnings = [str(item) for item in record.get("warnings") or [] if str(item)]
            fen_present = bool(str(record.get("fen") or "").strip())
            fen_candidate_present = bool(str(record.get("fen_candidate") or "").strip())
            status = str(record.get("status") or "")
            reason = str(record.get("reason") or "")
            primary_blocker = _board_detection_primary_quality_blocker(record, warnings=warnings)
            if candidate_kind == "low_confidence_review":
                gate_status = "low_confidence_review"
            elif status == "accepted" and fen_present:
                gate_status = "accepted_crop"
            elif warnings or reason:
                gate_status = "needs_review"
            else:
                gate_status = "false_positive_or_unrecognized"
            rows.append(
                {
                    "diagram_id": record.get("diagram_id") or record.get("id") or "",
                    "diagram_fingerprint": record.get("diagram_fingerprint") or "",
                    "source_document_sha256": record.get("source_document_sha256") or "",
                    "page": record.get("page"),
                    "page_index": record.get("page_index"),
                    "candidate_kind": candidate_kind,
                    "status": status,
                    "reason": reason,
                    "bbox": record.get("bbox") or [],
                    "bbox_xyxy": record.get("bbox_xyxy") or [],
                    "normalized_bbox_xyxy": record.get("normalized_bbox_xyxy") or [],
                    "pixel_bbox": record.get("pixel_bbox") or [],
                    "crop_path": record.get("image_path") or record.get("crop_path") or "",
                    "image_href": record.get("image_href") or "",
                    "method": record.get("method") or "",
                    "candidate_tier": record.get("candidate_tier") or candidate_kind,
                    "grid_confidence": _safe_float(record.get("grid_confidence")),
                    "fen_confidence": _safe_float(record.get("fen_confidence")),
                    "confidence": _safe_float(record.get("confidence")),
                    "board_detected": record.get("board_detected", None),
                    "fen_present": fen_present,
                    "fen_candidate_present": fen_candidate_present,
                    "warnings": warnings,
                    "quality_gate_status": gate_status,
                    "primary_quality_blocker": primary_blocker,
                }
            )
    return rows


def _write_board_detection_quality_artifacts(
    target: Path,
    records: list[dict],
    low_confidence_records: list[dict],
) -> dict[str, Any]:
    quality_records = _build_board_detection_quality_records(records, low_confidence_records)
    output_dir = target / "reports" / "chess_fen"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "board_detection_quality.json"
    jsonl_path = output_dir / "board_detection_quality.jsonl"
    by_blocker: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in quality_records:
        blocker = str(row.get("primary_quality_blocker") or "unknown")
        gate_status = str(row.get("quality_gate_status") or "unknown")
        by_blocker[blocker] = by_blocker.get(blocker, 0) + 1
        by_status[gate_status] = by_status.get(gate_status, 0) + 1
    summary = {
        "total_candidates": len(quality_records),
        "strict_candidate_count": len(records),
        "low_confidence_candidate_count": len(low_confidence_records),
        "accepted_crop_count": by_status.get("accepted_crop", 0),
        "needs_review_count": by_status.get("needs_review", 0),
        "by_primary_quality_blocker": dict(sorted(by_blocker.items(), key=lambda pair: (-pair[1], pair[0]))),
        "by_quality_gate_status": dict(sorted(by_status.items(), key=lambda pair: (-pair[1], pair[0]))),
    }
    payload = {
        "schema": "kindlemaster.chess_fen.board_detection_quality.v1",
        "summary": summary,
        "items": quality_records,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in quality_records),
        encoding="utf-8",
    )
    return {"json": str(json_path), "jsonl": str(jsonl_path), "summary": summary}


def _board_detection_primary_quality_blocker(record: dict, *, warnings: list[str]) -> str:
    reason = str(record.get("reason") or "").strip()
    if reason:
        return reason
    if warnings:
        return warnings[0]
    if not str(record.get("fen") or "").strip() and not str(record.get("fen_candidate") or "").strip():
        return "fen_candidate_missing"
    if str(record.get("status") or "") == "accepted":
        return "none"
    return "unknown"


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _clamp_pixel_bbox(
    bbox: tuple[float, float, float, float] | list[float],
    image_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    width, height = image_size
    x0, y0, x1, y1 = [int(round(float(value or 0.0))) for value in list(bbox)[:4]]
    x0 = max(0, min(x0, width))
    x1 = max(0, min(x1, width))
    y0 = max(0, min(y0, height))
    y1 = max(0, min(y1, height))
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _scale_bbox_to_pdf(
    bbox: tuple[int, int, int, int],
    image_size: tuple[int, int],
    page_rect: fitz.Rect,
) -> tuple[float, float, float, float]:
    image_width, image_height = image_size
    page_width = float(page_rect.width or image_width)
    page_height = float(page_rect.height or image_height)
    sx = page_width / max(1.0, float(image_width))
    sy = page_height / max(1.0, float(image_height))
    x0, y0, x1, y1 = bbox
    return (x0 * sx, y0 * sy, x1 * sx, y1 * sy)


def _bbox_xywh(bbox: tuple[float, float, float, float] | tuple[int, int, int, int]) -> list[float]:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    return [round(x0, 2), round(y0, 2), round(max(0.0, x1 - x0), 2), round(max(0.0, y1 - y0), 2)]


def _bbox_overlap_ratio(
    first: tuple[float, float, float, float] | tuple[int, int, int, int],
    second: tuple[float, float, float, float] | tuple[int, int, int, int],
) -> float:
    ax0, ay0, ax1, ay1 = [float(value) for value in first]
    bx0, by0, bx1, by1 = [float(value) for value in second]
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    intersection = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    if intersection <= 0:
        return 0.0
    first_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    second_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return intersection / max(1.0, min(first_area, second_area))


def _write_diagram_review_dataset(target: Path, records: list[dict[str, Any]], *, sample_limit: int = 0) -> dict[str, str]:
    review_dir = target / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    limit = max(0, int(sample_limit or 0))
    visible_records = records[:limit] if limit else records
    rows = [_diagram_review_row(record) for record in visible_records]
    jsonl_path = review_dir / "diagram_review.jsonl"
    csv_path = review_dir / "diagram_review.csv"
    html_path = review_dir / "diagram_review.html"

    jsonl_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    _write_review_csv(csv_path, rows)
    html_path.write_text(_diagram_review_html(rows), encoding="utf-8")
    return {
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
        "html": str(html_path),
        "label_schema": "manual_label in {correct_diagram,false_positive,cropped_diagram,uncertain}",
        "record_count": len(rows),
        "total_available_records": len(records),
        "sample_limit": limit,
    }


def _diagram_review_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "diagram_id": record.get("diagram_id") or "",
        "diagram_fingerprint": record.get("diagram_fingerprint") or "",
        "candidate_type": "low_confidence_review" if record.get("review_only") else "strict_detector",
        "page": int(record.get("page") or 0),
        "bbox": record.get("bbox") or [0, 0, 0, 0],
        "pixel_bbox": record.get("pixel_bbox") or [0, 0, 0, 0],
        "confidence": round(float(record.get("confidence") or 0.0), 3),
        "grid_confidence": round(float(record.get("grid_confidence") or 0.0), 3),
        "fen_confidence": round(float(record.get("fen_confidence") or 0.0), 3),
        "label": record.get("label") or record.get("diagram_id") or "",
        "image_href": record.get("image_href") or "",
        "image_path": record.get("image_path") or "",
        "thumbnail": record.get("image_href") or "",
        "detector_status": record.get("status") or "needs_review",
        "detector_reason": record.get("reason") or "",
        "review_only": bool(record.get("review_only")),
        "manual_label": "",
        "reviewer_notes": "",
    }


def _write_review_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "diagram_id",
        "diagram_fingerprint",
        "candidate_type",
        "page",
        "bbox",
        "pixel_bbox",
        "confidence",
        "grid_confidence",
        "fen_confidence",
        "label",
        "image_href",
        "image_path",
        "thumbnail",
        "detector_status",
        "detector_reason",
        "review_only",
        "manual_label",
        "reviewer_notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key)
                    for key in fieldnames
                }
            )


def _diagram_review_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_diagram_review_card(row) for row in rows) or "<p>No detected diagram crops yet.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chess Diagram Crop Review</title>
  <style>
    :root {{ --ink:#1f1a14; --paper:#fffaf0; --line:#d9c7ab; --muted:#745f49; --accent:#8f4818; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; background:#efe3d0; color:var(--ink); }}
    header {{ position:sticky; top:0; z-index:1; background:#24180f; color:#fff7ea; padding:1rem clamp(1rem, 4vw, 2rem); box-shadow:0 10px 28px rgba(0,0,0,.18); }}
    h1 {{ margin:.2rem 0; font-size:clamp(1.6rem, 4vw, 2.8rem); }}
    main {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:1rem; padding:1rem clamp(1rem, 4vw, 2rem) 2rem; }}
    article {{ background:var(--paper); border:1px solid var(--line); border-radius:18px; padding:1rem; box-shadow:0 14px 34px rgba(63,39,13,.10); }}
    img {{ display:block; width:100%; height:auto; border:1px solid var(--line); border-radius:12px; background:white; }}
    dl {{ display:grid; grid-template-columns:auto 1fr; gap:.25rem .65rem; margin:.75rem 0; font-size:.92rem; }}
    dt {{ color:var(--muted); font-weight:700; }}
    dd {{ margin:0; overflow-wrap:anywhere; }}
    fieldset {{ border:1px solid var(--line); border-radius:12px; margin:.75rem 0 0; padding:.55rem .7rem; }}
    label {{ display:block; margin:.25rem 0; }}
    textarea {{ width:100%; min-height:4rem; resize:vertical; border:1px solid var(--line); border-radius:10px; padding:.5rem; background:#fffdf8; }}
    code {{ font-family:'Courier New', monospace; }}
  </style>
</head>
<body>
  <header>
    <p>Manual labels are intentionally local: update <code>review/diagram_review.csv</code> or <code>review/diagram_review.jsonl</code> after inspection.</p>
    <h1>Chess Diagram Crop Review</h1>
    <p>{len(rows)} detected crop(s). Label each as correct diagram, false positive, cropped diagram, or uncertain.</p>
  </header>
  <main>{cards}</main>
</body>
</html>"""


def _diagram_review_card(row: dict[str, Any]) -> str:
    image_href = str(row.get("image_href") or "")
    image_src = "../" + image_href if image_href else ""
    options = ["correct_diagram", "false_positive", "cropped_diagram", "uncertain"]
    radios = "\n".join(
        f'<label><input type="radio" name="{html.escape(str(row.get("diagram_id")), quote=True)}" value="{option}"> {option}</label>'
        for option in options
    )
    img = f'<img src="{html.escape(image_src, quote=True)}" alt="{html.escape(str(row.get("diagram_id") or "diagram crop"), quote=True)}">' if image_src else "<p>No crop image available.</p>"
    return f"""<article data-diagram-id="{html.escape(str(row.get("diagram_id") or ""), quote=True)}">
  <h2>{html.escape(str(row.get("label") or row.get("diagram_id") or "Diagram"))}</h2>
  {img}
  <dl>
    <dt>Type</dt><dd>{html.escape(str(row.get("candidate_type") or ""))}</dd>
    <dt>Page</dt><dd>{html.escape(str(row.get("page") or ""))}</dd>
    <dt>BBox</dt><dd>{html.escape(json.dumps(row.get("bbox") or [], ensure_ascii=False))}</dd>
    <dt>Confidence</dt><dd>{html.escape(str(row.get("confidence") or 0))}</dd>
    <dt>Status</dt><dd>{html.escape(str(row.get("detector_status") or ""))}</dd>
    <dt>Reason</dt><dd>{html.escape(str(row.get("detector_reason") or ""))}</dd>
  </dl>
  <fieldset><legend>Manual label</legend>{radios}</fieldset>
  <label>Notes<textarea aria-label="Reviewer notes for {html.escape(str(row.get("diagram_id") or ""), quote=True)}"></textarea></label>
</article>"""


def _print_summary(manifest: dict[str, Any]) -> None:
    print("Chess diagram detection: ok")
    print(f"  Diagrams: {manifest['diagram_count']}")
    print(f"  Accepted FEN: {manifest['accepted_fen_count']}")
    print(f"  Needs review: {manifest['review_count']}")
    print(f"  Low-confidence review candidates: {manifest.get('low_confidence_review_count', 0)}")
    print(f"  Sampled pages: {manifest.get('sampled_pages', [])}")
    print(f"  DPI: {manifest['dpi']}")
    print(f"  OpenCV: {'yes' if manifest['cv_backend'].get('available') else 'no'}")
    print(f"  Manifest: {manifest['manifest_path']}")
    print(f"  Review HTML: {manifest.get('review_dataset', {}).get('html')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect chess diagrams in a PDF and export crops plus FEN metadata.")
    parser.add_argument("pdf", help="Source PDF path.")
    parser.add_argument("--output-dir", default="dist")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--pages", type=int, default=0, help="Limit pages; 0 means all pages.")
    parser.add_argument("--page-ranges", "--diagram-page-ranges", dest="page_ranges", default="", help='1-based inclusive page ranges, for example "10-20,40-45". Overrides --pages when set.')
    parser.add_argument("--max-candidates-per-page", type=int, default=6)
    parser.add_argument("--min-grid-confidence", type=float, default=0.58)
    parser.add_argument("--min-fen-confidence", type=float, default=0.85)
    parser.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    parser.add_argument("--sliding-probe", action="store_true")
    parser.add_argument(
        "--low-confidence-review",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Add recovered candidates to the canonical review pipeline; never accept their FEN automatically.",
    )
    parser.add_argument("--low-confidence-min-grid-confidence", type=float, default=0.30)
    parser.add_argument("--low-confidence-max-candidates-per-page", type=int, default=12)
    parser.add_argument("--review-sample-limit", type=int, default=0, help="Limit rows written to review dataset; 0 writes all rows.")
    parser.add_argument("--expected-diagram-manifest", default="")
    args = parser.parse_args()

    manifest = detect_chess_diagrams(
        args.pdf,
        output_dir=args.output_dir,
        dpi=args.dpi,
        pages=args.pages,
        max_candidates_per_page=args.max_candidates_per_page,
        min_grid_confidence=args.min_grid_confidence,
        min_fen_confidence=args.min_fen_confidence,
        template_dir=args.template_dir,
        page_ranges=args.page_ranges,
        enable_sliding_probe=args.sliding_probe,
        include_low_confidence_review_candidates=args.low_confidence_review,
        low_confidence_min_grid_confidence=args.low_confidence_min_grid_confidence,
        low_confidence_max_candidates_per_page=args.low_confidence_max_candidates_per_page,
        review_sample_limit=args.review_sample_limit,
        expected_diagram_manifest=args.expected_diagram_manifest or None,
    )
    _print_summary(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
