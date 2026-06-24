from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pymupdf_chess_extractor import (  # noqa: E402
    _clamp_bbox,
    _page_image_data_for_scan_chess,
    _scan_chess_ocr_line_geometry_for_image,
)


DEFAULT_OUTPUT_DIR = Path("reports/chess_fen/side_header_candidates/latest")
SYMBOL_RE = re.compile(r"(?<![A-Za-z0-9])(?:A|Vv|VV|V|△|▲|▽|▼)(?![A-Za-z0-9])")


def export_side_header_candidates(
    report_json: str | Path,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    source_pdf: str | Path | None = None,
    include_all_records: bool = False,
) -> dict[str, Any]:
    report_path = Path(report_json)
    target = Path(output_dir)
    header_dir = target / "header_crops"
    board_dir = target / "board_crops"
    header_dir.mkdir(parents=True, exist_ok=True)
    board_dir.mkdir(parents=True, exist_ok=True)

    report = json.loads(report_path.read_text(encoding="utf-8"))
    epub_path = Path(str(report.get("output_path") or "").strip())
    if not epub_path.is_file():
        raise FileNotFoundError(f"Expected output EPUB at {epub_path}")
    source_pdf_path = Path(source_pdf) if source_pdf else None
    page_images = _load_page_images(source_pdf_path) if source_pdf_path and source_pdf_path.is_file() else {}
    records = list((((report.get("quality_report") or {}).get("chess_fen") or {}).get("records") or []))
    queue = [record for record in records if include_all_records or _record_needs_header_candidates(record)]

    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(epub_path) as archive:
        epub_names = {Path(name).name: name for name in archive.namelist()}
        for record in queue:
            filename = str(record.get("filename") or "").strip()
            page = int(record.get("page") or 0)
            board_crop_path = ""
            if filename in epub_names:
                board_crop_path = str(board_dir / filename)
                Path(board_crop_path).write_bytes(archive.read(epub_names[filename]))
            page_image = page_images.get(page)
            header_payload = _header_payload_for_record(
                record,
                page_image=page_image,
                output_path=header_dir / filename if filename else header_dir / f"page_{page:03d}.png",
            )
            rows.append(_row_for_record(record, board_crop_path=board_crop_path, header_payload=header_payload))

    rows.sort(key=lambda row: (int(row.get("page") or 0), str(row.get("filename") or "")))
    jsonl_path = target / "side_header_candidates.jsonl"
    html_path = target / "side_header_review.html"
    summary_path = target / "side_header_candidates_summary.json"
    _write_jsonl(jsonl_path, rows)
    summary = {
        "status": "ok",
        "mode": "side_header_candidates_audit_only",
        "report_json": str(report_path),
        "source_epub": str(epub_path),
        "source_pdf": str(source_pdf_path or ""),
        "output_dir": str(target),
        "record_count": len(rows),
        "header_crop_available_count": sum(1 for row in rows if row.get("header_crop_status") == "available"),
        "ocr_line_geometry_available_count": sum(1 for row in rows if row.get("ocr_line_geometry_status") == "available"),
        "symbol_candidate_count": sum(len(row.get("header_symbol_candidates") or []) for row in rows),
        "symbol_counts": dict(Counter(symbol["symbol"] for row in rows for symbol in row.get("header_symbol_candidates") or [])),
        "side_header_candidates_jsonl": str(jsonl_path),
        "side_header_review_html": str(html_path),
        "policy": "audit_only_no_fen_publication",
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_review_html(summary, rows), encoding="utf-8")
    return summary


def _record_needs_header_candidates(record: dict[str, Any]) -> bool:
    if not bool(record.get("requires_review")):
        return False
    warnings = {str(warning) for warning in (record.get("warnings") or [])}
    return "side_to_move_inferred" in warnings or "side_to_move_marker_multi_region_conflict" in warnings


def _row_for_record(record: dict[str, Any], *, board_crop_path: str, header_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"side_header_p{int(record.get('page') or 0):03d}_{Path(str(record.get('filename') or '')).stem}",
        "page": int(record.get("page") or 0),
        "filename": str(record.get("filename") or ""),
        "bbox": list(record.get("bbox") or []),
        "board_crop_path": board_crop_path,
        "candidate_placement": str(record.get("placement") or record.get("placement_fen") or ""),
        "candidate_full_fen": str(record.get("full_fen") or ""),
        "candidate_confidence": float(record.get("confidence") or 0.0),
        "candidate_warnings": [str(warning) for warning in (record.get("warnings") or [])],
        "side_marker_candidates": [candidate for candidate in (record.get("side_marker_candidates") or []) if isinstance(candidate, dict)],
        **header_payload,
        "human_side_to_move": "",
        "human_verified": False,
        "notes": "",
    }


def _header_payload_for_record(record: dict[str, Any], *, page_image: Image.Image | None, output_path: Path) -> dict[str, Any]:
    if page_image is None:
        return {
            "header_crop_status": "unavailable",
            "header_crop_unavailable_reason": "source_pdf_page_image_unavailable",
            "header_crop_path": "",
            "header_bbox": [],
            "ocr_line_geometry_status": "unavailable",
            "ocr_line_geometry_warning": "ocr_line_geometry_unavailable",
            "ocr_line_items": [],
            "header_symbol_candidates": [],
        }
    header_bbox = _side_header_zone_bbox(record.get("bbox"), page_image.size)
    if header_bbox is None:
        return {
            "header_crop_status": "unavailable",
            "header_crop_unavailable_reason": "record_bbox_invalid",
            "header_crop_path": "",
            "header_bbox": [],
            "ocr_line_geometry_status": "unavailable",
            "ocr_line_geometry_warning": "ocr_line_geometry_unavailable",
            "ocr_line_items": [],
            "header_symbol_candidates": [],
        }
    header = page_image.crop(header_bbox).convert("RGB")
    _draw_header_overlay(header, record, header_bbox)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header.save(output_path)
    line_items, reason = _ocr_header_lines(header, page=int(record.get("page") or 0), offset=(header_bbox[0], header_bbox[1]))
    symbol_candidates = _symbol_candidates_from_lines(line_items)
    return {
        "header_crop_status": "available",
        "header_crop_unavailable_reason": "",
        "header_crop_path": str(output_path),
        "header_bbox": [round(float(value), 2) for value in header_bbox],
        "ocr_line_geometry_status": "available" if line_items else "unavailable",
        "ocr_line_geometry_warning": "" if line_items else "ocr_line_geometry_unavailable",
        "ocr_line_geometry_unavailable_reason": reason if not line_items else "",
        "ocr_line_items": line_items,
        "header_symbol_candidates": symbol_candidates,
    }


def _side_header_zone_bbox(raw_bbox: Any, page_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    clamped = _clamp_bbox(raw_bbox, page_size, pad_ratio=0.0, min_pad=0.0)
    if clamped is None:
        return None
    x0, y0, x1, y1 = (float(value) for value in clamped)
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)
    margin_x = max(24.0, width * 0.12)
    top_pad = max(48.0, height * 0.25)
    return (
        int(max(0.0, x0 - margin_x)),
        int(max(0.0, y0 - top_pad)),
        int(min(float(page_size[0]), x1 + margin_x)),
        int(max(1.0, y0)),
    )


def _ocr_header_lines(header: Image.Image, *, page: int, offset: tuple[int, int]) -> tuple[list[dict[str, Any]], str]:
    lines, reason = _scan_chess_ocr_line_geometry_for_image(
        header,
        page_num=page,
        target_size=header.size,
        language="eng",
    )
    output: list[dict[str, Any]] = []
    ox, oy = offset
    for line in lines:
        data = line.to_dict()
        bbox = data.get("bbox") or []
        if len(bbox) == 4:
            data["bbox"] = [
                round(float(bbox[0]) + ox, 2),
                round(float(bbox[1]) + oy, 2),
                round(float(bbox[2]) + ox, 2),
                round(float(bbox[3]) + oy, 2),
            ]
        output.append(data)
    return output, reason


def _symbol_candidates_from_lines(line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for line in line_items:
        text = str(line.get("text") or "")
        for match in SYMBOL_RE.finditer(text):
            candidates.append(
                {
                    "symbol": match.group(0),
                    "source": "ocr_line",
                    "line_text": text,
                    "line_bbox": line.get("bbox") or [],
                    "confidence": float(line.get("confidence") or 0.0),
                }
            )
    return candidates


def _draw_header_overlay(header: Image.Image, record: dict[str, Any], header_bbox: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(header)
    ox, oy = header_bbox[0], header_bbox[1]
    board = _relative_bbox(record.get("bbox"), offset=(ox, oy))
    if board:
        draw.rectangle(board, outline=(0, 100, 255), width=4)
    for candidate in record.get("side_marker_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        marker = _relative_bbox(candidate.get("bbox"), offset=(ox, oy))
        if not marker:
            continue
        color = (0, 170, 0) if candidate.get("detected_side") else (255, 165, 0)
        draw.rectangle(marker, outline=color, width=3)
        draw.text((marker[0] + 3, marker[1] + 3), str(candidate.get("role") or ""), fill=color)


def _relative_bbox(value: Any, *, offset: tuple[int, int]) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(round(float(item))) for item in value)
    except Exception:
        return None
    ox, oy = offset
    return x0 - ox, y0 - oy, x1 - ox, y1 - oy


def _load_page_images(source_pdf: Path | None) -> dict[int, Image.Image]:
    if source_pdf is None:
        return {}
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
                images[page_num] = Image.open(io.BytesIO(_page_image_data_for_scan_chess(doc, page_num))).convert("RGB")
            except Exception:
                continue
    finally:
        doc.close()
    return images


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _review_html(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    cards = []
    for row in rows:
        header = _html_path(row.get("header_crop_path"))
        board = _html_path(row.get("board_crop_path"))
        symbols = html.escape(json.dumps(row.get("header_symbol_candidates") or [], ensure_ascii=False, indent=2))
        lines = html.escape(json.dumps(row.get("ocr_line_items") or [], ensure_ascii=False, indent=2))
        cards.append(
            f"""
            <article class="card">
              <h2>{html.escape(str(row.get('id')))}</h2>
              <p>page={row.get('page')} file={html.escape(str(row.get('filename')))} status={html.escape(str(row.get('header_crop_status')))}</p>
              <div class="images">
                {'<img src="' + header + '" alt="header crop">' if header else ''}
                {'<img src="' + board + '" alt="board crop">' if board else ''}
              </div>
              <h3>Symbol candidates</h3><pre>{symbols}</pre>
              <h3>OCR lines</h3><pre>{lines}</pre>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Chess Side Header Candidates</title>
<style>body{{font-family:sans-serif;margin:24px;background:#f4efe6}}.card{{background:#fff;border:1px solid #d8cab8;border-radius:14px;padding:16px;margin:0 0 18px}}.images{{display:flex;gap:16px;flex-wrap:wrap}}img{{max-width:620px;max-height:360px;border:1px solid #ccc}}pre{{background:#231f1b;color:#ffedd3;padding:12px;border-radius:10px;overflow:auto}}</style>
</head><body><h1>Chess Side Header Candidates</h1>
<p>Audit only. Does not publish FEN. rows={summary.get('record_count')} symbols={summary.get('symbol_candidate_count')}</p>
{''.join(cards)}</body></html>"""


def _html_path(value: Any) -> str:
    text = str(value or "").strip()
    return html.escape(Path(text).as_posix()) if text else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export audit-only side-to-move header/caption candidates for scanned chess diagrams.")
    parser.add_argument("report_json")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--source-pdf", default="")
    parser.add_argument("--include-all-records", action="store_true")
    args = parser.parse_args(argv)
    summary = export_side_header_candidates(
        args.report_json,
        output_dir=args.output_dir,
        source_pdf=args.source_pdf or None,
        include_all_records=bool(args.include_all_records),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
