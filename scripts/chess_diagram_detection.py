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
    include_low_confidence_review_candidates: bool = False,
    low_confidence_min_grid_confidence: float = 0.30,
    low_confidence_max_candidates_per_page: int = 12,
    review_sample_limit: int = 0,
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

    with fitz.open(source) as document:
        page_count = len(document)
        selected_page_indices = _selected_page_indices(page_count, pages=pages, page_ranges=page_ranges)
        sampled_pages = [index + 1 for index in selected_page_indices]
        zoom = max(72, int(dpi)) / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in selected_page_indices:
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            page_png = pixmap.tobytes("png")
            page_image = Image.open(io.BytesIO(page_png)).convert("RGB")
            candidates = detect_board_candidates_in_page_image(
                page_png,
                max_candidates=max_candidates_per_page,
                min_grid_confidence=min_grid_confidence,
                enable_sliding_probe=enable_sliding_probe,
            )
            strict_pixel_bboxes: list[tuple[int, int, int, int]] = []
            for candidate_index, candidate in enumerate(candidates, start=1):
                if not candidate.bbox:
                    continue
                pixel_bbox = _clamp_pixel_bbox(candidate.bbox, page_image.size)
                if pixel_bbox is None:
                    continue
                crop = page_image.crop(pixel_bbox)
                if min(crop.size) < 64:
                    continue

                diagram_id = f"p{page_index + 1:03d}_d{candidate_index:02d}"
                filename = f"page-{page_index + 1:03d}-diagram-{candidate_index:02d}.webp"
                crop_path = diagrams_dir / filename
                crop.save(crop_path, format="WEBP", quality=88, method=6)
                strict_pixel_bboxes.append(pixel_bbox)

                fen_result = _recognize_crop_fen(
                    crop,
                    candidate=candidate,
                    pdf_bbox=_scale_bbox_to_pdf(pixel_bbox, page_image.size, page.rect),
                    templates=templates,
                    min_confidence=min_fen_confidence,
                )
                fen_valid = _fen_accepted(fen_result)
                records.append(
                    {
                        "diagram_id": diagram_id,
                        "page": page_index + 1,
                        "page_index": page_index,
                        "label": diagram_id,
                        "bbox": _bbox_xywh(_scale_bbox_to_pdf(pixel_bbox, page_image.size, page.rect)),
                        "bbox_xyxy": list(_scale_bbox_to_pdf(pixel_bbox, page_image.size, page.rect)),
                        "pixel_bbox": _bbox_xywh(pixel_bbox),
                        "orientation": "white-bottom",
                        "side_to_move": fen_result.side_to_move or "w",
                        "confidence": round(float(candidate.confidence or fen_result.confidence or 0.0), 3),
                        "grid_confidence": round(float(candidate.confidence or 0.0), 3),
                        "fen_confidence": round(float(fen_result.confidence or 0.0), 3),
                        "fen": fen_result.fen if fen_valid else "",
                        "fen_candidate": fen_result.fen or "",
                        "status": "accepted" if fen_valid else "needs_review",
                        "reason": None if fen_valid else _fen_review_reason(fen_result),
                        "warnings": list(fen_result.warnings or []),
                        "method": fen_result.method or candidate.method,
                        "image_path": str(crop_path),
                        "image_href": str(Path("assets") / "diagrams" / filename).replace("\\", "/"),
                    }
                )
            if include_low_confidence_review_candidates:
                low_candidates = detect_board_candidates_in_page_image(
                    page_png,
                    max_candidates=max(
                        int(max_candidates_per_page or 0),
                        int(low_confidence_max_candidates_per_page or 0),
                    ),
                    min_grid_confidence=min(
                        float(min_grid_confidence or 0.0),
                        float(low_confidence_min_grid_confidence or 0.0),
                    ),
                    enable_sliding_probe=enable_sliding_probe,
                )
                low_index = 0
                for candidate in low_candidates:
                    if not candidate.bbox:
                        continue
                    pixel_bbox = _clamp_pixel_bbox(candidate.bbox, page_image.size)
                    if pixel_bbox is None:
                        continue
                    if any(_bbox_overlap_ratio(pixel_bbox, existing) > 0.55 for existing in strict_pixel_bboxes):
                        continue
                    crop = page_image.crop(pixel_bbox)
                    if min(crop.size) < 64:
                        continue
                    low_index += 1
                    diagram_id = f"p{page_index + 1:03d}_lc{low_index:02d}"
                    filename = f"page-{page_index + 1:03d}-candidate-{low_index:02d}.webp"
                    crop_path = low_confidence_dir / filename
                    crop.save(crop_path, format="WEBP", quality=82, method=6)
                    pdf_bbox = _scale_bbox_to_pdf(pixel_bbox, page_image.size, page.rect)
                    low_confidence_records.append(
                        {
                            "diagram_id": diagram_id,
                            "page": page_index + 1,
                            "page_index": page_index,
                            "label": diagram_id,
                            "bbox": _bbox_xywh(pdf_bbox),
                            "bbox_xyxy": list(pdf_bbox),
                            "pixel_bbox": _bbox_xywh(pixel_bbox),
                            "orientation": "unknown",
                            "side_to_move": "w",
                            "confidence": round(float(candidate.confidence or 0.0), 3),
                            "grid_confidence": round(float(candidate.confidence or 0.0), 3),
                            "fen_confidence": 0.0,
                            "fen": "",
                            "fen_candidate": "",
                            "status": "needs_review",
                            "reason": "review_only_low_confidence_candidate",
                            "warnings": [
                                "review_only_low_confidence_candidate",
                                "fen_recognition_skipped_for_review_candidate",
                            ],
                            "method": candidate.method or "low-confidence-board-candidate",
                            "image_path": str(crop_path),
                            "image_href": str(Path("assets") / "diagram_candidates" / filename).replace("\\", "/"),
                            "review_only": True,
                        }
                    )

    manifest = {
        "status": "ok",
        "source_pdf": str(source),
        "output_dir": str(target),
        "asset_dir": str(diagrams_dir),
        "dpi": int(dpi),
        "cv_backend": cv_backend,
        "template_dir": str(template_dir or ""),
        "template_count": sum(len(items) for items in templates.values()),
        "page_count": page_count,
        "sampled_pages": sampled_pages,
        "page_ranges": str(page_ranges or ""),
        "page_limit": int(pages or 0),
        "diagram_count": len(records),
        "accepted_fen_count": sum(1 for record in records if record.get("status") == "accepted"),
        "review_count": sum(1 for record in records if record.get("status") != "accepted"),
        "low_confidence_review_count": len(low_confidence_records),
        "low_confidence_review_enabled": bool(include_low_confidence_review_candidates),
        "low_confidence_min_grid_confidence": round(float(low_confidence_min_grid_confidence or 0.0), 3),
        "review_sample_limit": int(review_sample_limit or 0),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "diagrams": records,
        "low_confidence_review_candidates": low_confidence_records,
    }
    review_records = [*records, *low_confidence_records]
    review_paths = _write_diagram_review_dataset(target, review_records, sample_limit=review_sample_limit)
    manifest["review_dataset"] = review_paths
    manifest["review_dataset_count"] = min(len(review_records), int(review_sample_limit or 0)) if int(review_sample_limit or 0) > 0 else len(review_records)
    manifest["board_detection_quality"] = _write_board_detection_quality_artifacts(target, records, low_confidence_records)
    manifest_path = target / "chess_diagrams.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


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
                    "page": record.get("page"),
                    "page_index": record.get("page_index"),
                    "candidate_kind": candidate_kind,
                    "status": status,
                    "reason": reason,
                    "bbox": record.get("bbox") or [],
                    "bbox_xyxy": record.get("bbox_xyxy") or [],
                    "pixel_bbox": record.get("pixel_bbox") or [],
                    "crop_path": record.get("image_path") or record.get("crop_path") or "",
                    "image_href": record.get("image_href") or "",
                    "method": record.get("method") or "",
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
        action="store_true",
        help="Add extra low-confidence crops to review dataset only; never to accepted FEN.",
    )
    parser.add_argument("--low-confidence-min-grid-confidence", type=float, default=0.30)
    parser.add_argument("--low-confidence-max-candidates-per-page", type=int, default=12)
    parser.add_argument("--review-sample-limit", type=int, default=0, help="Limit rows written to review dataset; 0 writes all rows.")
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
    )
    _print_summary(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
