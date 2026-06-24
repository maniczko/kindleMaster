from __future__ import annotations

import argparse
import html
import io
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pymupdf_chess_extractor import _clamp_bbox, _page_image_data_for_scan_chess


DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/side_marker_calibration/latest")


def export_side_marker_calibration_campaign(
    report_json: str | Path,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    source_pdf: str | Path | None = None,
) -> dict[str, Any]:
    report_path = Path(report_json)
    target = Path(output_dir)
    crops_dir = target / "crops"
    context_dir = target / "context"
    crops_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    epub_path = Path(str(report.get("output_path") or "").strip())
    if not epub_path.is_file():
        raise FileNotFoundError(f"Expected output EPUB at {epub_path}")

    records = list((((report.get("quality_report") or {}).get("chess_fen") or {}).get("records") or []))
    queue_records = [record for record in records if _record_needs_marker_calibration(record)]

    source_pdf_path = Path(source_pdf) if source_pdf else None
    page_images = _load_page_images(source_pdf_path) if source_pdf_path and source_pdf_path.is_file() else {}
    rows: list[dict[str, Any]] = []
    missing_crop_details: list[dict[str, Any]] = []

    with zipfile.ZipFile(epub_path) as archive:
        epub_names = {Path(name).name: name for name in archive.namelist()}
        for record in queue_records:
            filename = str(record.get("filename") or "").strip()
            page = int(record.get("page") or 0)
            if not filename or filename not in epub_names:
                missing_crop_details.append({"page": page, "filename": filename, "reason": "crop_missing_in_epub"})
                continue
            crop_bytes = archive.read(epub_names[filename])
            crop_path = crops_dir / filename
            crop_path.write_bytes(crop_bytes)
            context_path = _write_context_crop(
                record,
                page_image=page_images.get(page),
                output_path=context_dir / filename,
            )
            row = _campaign_row(
                record,
                crop_path=crop_path,
                context_path=context_path,
                source_pdf=source_pdf_path,
            )
            rows.append(row)

    rows.sort(key=_campaign_sort_key)
    draft_path = target / "side_marker_calibration_draft.jsonl"
    summary_path = target / "side_marker_calibration_summary.json"
    review_sheet_path = target / "side_marker_review_sheet.html"
    _write_jsonl(draft_path, rows)

    priority_counts = Counter(str(row.get("review_priority") or "unknown") for row in rows)
    warning_counts = Counter(warning for row in rows for warning in row.get("candidate_warnings", []))
    marker_role_counts = Counter(
        str(candidate.get("role") or "unknown")
        for row in rows
        for candidate in row.get("side_marker_candidates", [])
        if candidate.get("detected_side")
    )
    summary = {
        "status": "ok",
        "report_json": str(report_path),
        "source_epub": str(epub_path),
        "source_pdf": str(source_pdf_path or ""),
        "output_dir": str(target),
        "review_record_count": len(queue_records),
        "draft_count": len(rows),
        "missing_crop_count": len(missing_crop_details),
        "priority_counts": dict(sorted(priority_counts.items())),
        "marker_role_counts": dict(sorted(marker_role_counts.items())),
        "warning_counts": dict(warning_counts.most_common(30)),
        "missing_crop_details": missing_crop_details,
        "draft_path": str(draft_path),
        "review_sheet_path": str(review_sheet_path),
        "instructions": [
            "Fill human_side_to_move with w or b only when the marker is visually unambiguous.",
            "Set marker_source to visual_marker, ocr_symbol, none, or ambiguous.",
            "Fill marker_role/marker_symbol when useful; leave fen untouched.",
            "Rows are calibration evidence only and must not be promoted as FEN labels.",
        ],
        "queue": rows,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    review_sheet_path.write_text(_review_sheet_html(summary, rows), encoding="utf-8")
    return summary


def _record_needs_marker_calibration(record: dict[str, Any]) -> bool:
    if not bool(record.get("requires_review")):
        return False
    warnings = {str(warning) for warning in (record.get("warnings") or [])}
    if "side_to_move_marker_multi_region_conflict" in warnings or "side_to_move_marker_ambiguous" in warnings:
        return True
    if "side_to_move_inferred" in warnings and str(record.get("placement") or record.get("placement_fen") or "").strip():
        return True
    return False


def _campaign_row(
    record: dict[str, Any],
    *,
    crop_path: Path,
    context_path: Path | None,
    source_pdf: Path | None,
) -> dict[str, Any]:
    warnings = [str(warning) for warning in (record.get("warnings") or [])]
    marker_candidates = [candidate for candidate in (record.get("side_marker_candidates") or []) if isinstance(candidate, dict)]
    return {
        "id": f"side_marker_p{int(record.get('page') or 0):03d}_{Path(str(record.get('filename') or '')).stem}",
        "source_pdf": str(source_pdf or ""),
        "page": int(record.get("page") or 0),
        "filename": str(record.get("filename") or ""),
        "crop_path": str(crop_path),
        "context_crop_path": str(context_path or ""),
        "bbox": list(record.get("bbox") or []),
        "review_priority": _review_priority(warnings, marker_candidates),
        "candidate_placement": str(record.get("placement") or record.get("placement_fen") or "").strip(),
        "candidate_full_fen": str(record.get("full_fen") or "").strip(),
        "candidate_confidence": float(record.get("confidence") or 0.0),
        "candidate_method": str(record.get("method") or "").strip(),
        "candidate_warnings": warnings,
        "side_marker_candidates": marker_candidates,
        "detected_marker_sides": sorted(
            {
                str(candidate.get("detected_side") or "")
                for candidate in marker_candidates
                if candidate.get("detected_side")
            }
        ),
        "human_side_to_move": "",
        "marker_source": "",
        "marker_role": "",
        "marker_symbol": "",
        "human_verified": False,
        "human_rejected": False,
        "verified_by": "",
        "verified_at": "",
        "notes": "",
    }


def _review_priority(warnings: list[str], marker_candidates: list[dict[str, Any]]) -> str:
    warning_set = set(warnings)
    if "side_to_move_marker_multi_region_conflict" in warning_set:
        return "conflict"
    if "side_to_move_marker_ambiguous" in warning_set:
        return "ambiguous"
    if any(candidate.get("detected_side") for candidate in marker_candidates):
        return "detected_candidate"
    if "side_to_move_inferred" in warning_set:
        return "inferred_no_candidate"
    return "other"


def _campaign_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    order = {
        "conflict": 0,
        "ambiguous": 1,
        "detected_candidate": 2,
        "inferred_no_candidate": 3,
        "other": 4,
    }
    return (
        order.get(str(row.get("review_priority") or "other"), 9),
        int(row.get("page") or 0),
        str(row.get("filename") or ""),
    )


def _write_context_crop(record: dict[str, Any], *, page_image: Image.Image | None, output_path: Path) -> Path | None:
    if page_image is None:
        return None
    bbox = _clamp_bbox(record.get("bbox"), page_image.size, pad_ratio=0.16, min_pad=32.0)
    if bbox is None:
        return None
    context = page_image.crop(bbox).convert("RGB")
    draw = ImageDraw.Draw(context)
    offset_x, offset_y = bbox[0], bbox[1]
    board_bbox = _relative_bbox(record.get("bbox"), offset_x=offset_x, offset_y=offset_y)
    if board_bbox:
        draw.rectangle(board_bbox, outline=(0, 100, 255), width=5)
    for candidate in record.get("side_marker_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        marker_bbox = _relative_bbox(candidate.get("bbox"), offset_x=offset_x, offset_y=offset_y)
        if not marker_bbox:
            continue
        color = (0, 170, 0) if candidate.get("detected_side") else (255, 165, 0) if candidate.get("ambiguous_density") else (160, 160, 160)
        draw.rectangle(marker_bbox, outline=color, width=4)
        draw.text((marker_bbox[0] + 4, marker_bbox[1] + 4), str(candidate.get("role") or ""), fill=color)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    context.save(output_path)
    return output_path


def _relative_bbox(value: Any, *, offset_x: int, offset_y: int) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(round(float(item))) for item in value)
    except Exception:
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return x0 - offset_x, y0 - offset_y, x1 - offset_x, y1 - offset_y


def _load_page_images(source_pdf: Path) -> dict[int, Image.Image]:
    try:
        import fitz
    except Exception:
        return {}
    images: dict[int, Image.Image] = {}
    try:
        doc = fitz.open(source_pdf)
    except Exception:
        return {}
    try:
        for page_num in range(len(doc)):
            try:
                data = _page_image_data_for_scan_chess(doc, page_num)
                images[page_num] = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception:
                continue
    finally:
        doc.close()
    return images


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _review_sheet_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    cards = []
    for row in rows:
        crop = _html_path(row.get("crop_path"))
        context = _html_path(row.get("context_crop_path"))
        candidates = html.escape(json.dumps(row.get("side_marker_candidates") or [], ensure_ascii=False, indent=2))
        warnings = ", ".join(str(w) for w in row.get("candidate_warnings", []))
        cards.append(
            f"""
            <article class="card priority-{html.escape(str(row.get('review_priority')))}">
              <h2>{html.escape(str(row.get('id')))} <span>{html.escape(str(row.get('review_priority')))}</span></h2>
              <p>page={row.get('page')} file={html.escape(str(row.get('filename')))} confidence={row.get('candidate_confidence'):.3f}</p>
              <div class="images">
                {'<img src="' + context + '" alt="context crop">' if context else ''}
                <img src="{crop}" alt="board crop">
              </div>
              <p><strong>warnings:</strong> {html.escape(warnings)}</p>
              <pre>{candidates}</pre>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Chess Side Marker Calibration</title>
  <style>
    body {{ font-family: sans-serif; margin: 24px; background: #f6f1e8; color: #1f1a14; }}
    .summary, .card {{ background: white; border: 1px solid #d8cab8; border-radius: 14px; padding: 16px; margin: 0 0 18px; }}
    .card h2 {{ margin: 0 0 8px; font-size: 18px; }}
    .card h2 span {{ color: #8a4b00; font-size: 14px; }}
    .images {{ display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }}
    img {{ max-width: 520px; max-height: 520px; border: 1px solid #ccc; background: #fff; }}
    pre {{ overflow: auto; background: #27221d; color: #f7ead6; padding: 12px; border-radius: 10px; }}
  </style>
</head>
<body>
  <section class="summary">
    <h1>Chess Side Marker Calibration</h1>
    <p>draft_count={summary.get('draft_count')} missing_crop_count={summary.get('missing_crop_count')}</p>
    <p>priority_counts={html.escape(json.dumps(summary.get('priority_counts'), ensure_ascii=False))}</p>
  </section>
  {''.join(cards)}
</body>
</html>"""


def _html_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return html.escape(Path(text).as_posix())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a side-to-move marker calibration campaign from a chess FEN report.")
    parser.add_argument("report_json", help="Runtime report JSON with quality_report.chess_fen.records.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Campaign output directory.")
    parser.add_argument("--source-pdf", default="", help="Optional source PDF for context crops with marker overlays.")
    args = parser.parse_args(argv)
    summary = export_side_marker_calibration_campaign(
        args.report_json,
        output_dir=args.output_dir,
        source_pdf=args.source_pdf or None,
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "queue"}, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
