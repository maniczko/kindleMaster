from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import fitz

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import recognize_font_board_from_lines


FONT_BOARD_EMPTY_MARKERS = {"0", "Z"}
FONT_BOARD_ROW_RE = re.compile(r"^[A-Za-z0-9]{8}$")
REVIEW_ONLY_NOTE = "Fill FEN manually from raw_rows. This font-board row is not accepted for corpus proof."


def extract_chess_font_board_candidates(
    pdf_path: str | Path,
    *,
    output_dir: str | Path = "reports/chess_fen/font_board_intake/latest",
    page_start: int = 1,
    pages: int = 96,
    min_seed_labels: int = 20,
) -> dict[str, Any]:
    """Extract review-only candidate labels from text/font encoded chess boards."""
    source = Path(pdf_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(source)
    start = max(1, int(page_start))
    stop = min(doc.page_count, start + max(1, int(pages)) - 1)

    candidates: list[dict[str, Any]] = []
    for page_number in range(start, stop + 1):
        page = doc[page_number - 1]
        lines = _font_board_lines_from_page(page)
        page_candidates = extract_font_board_candidates_from_lines(
            lines,
            source_pdf=str(source),
            page_number=page_number,
            id_prefix=normalize_font_board_profile_id(source),
        )
        candidates.extend(page_candidates)

    labels_path = target / "candidate_font_board_labels_review.jsonl"
    _write_jsonl(labels_path, candidates)
    summary = {
        "status": "review_required" if len(candidates) >= max(1, int(min_seed_labels)) else ("insufficient_candidates" if candidates else "no_candidates"),
        "accepted_for_corpus": False,
        "source_pdf": str(source),
        "page_start": start,
        "page_end": stop,
        "page_count": doc.page_count,
        "candidate_label_count": len(candidates),
        "required_verified_seed_count": max(1, int(min_seed_labels)),
        "candidate_labels_review": str(labels_path),
        "policy": "review_only_no_fen_generation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "next_steps": [
            "Manually fill FEN, verified_by, verified_at, and notes in a copy of candidate_font_board_labels_review.jsonl.",
            "Do not use this review queue directly as corpus proof.",
            "If this becomes a supported profile, add a dedicated deterministic font-board evaluator before merging the manifest case.",
        ],
    }
    summary_path = target / "font_board_intake_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def normalize_font_board_profile_id(value: str | Path) -> str:
    stem = Path(str(value)).stem if str(value).lower().endswith(".pdf") else str(value)
    normalized = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return normalized or "font_board_chess_profile"


def extract_font_board_candidates_from_lines(
    lines: Iterable[dict[str, Any]],
    *,
    source_pdf: str,
    page_number: int,
    id_prefix: str = "font_board",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    diagram_index = 1
    for line in lines:
        row = normalize_font_board_row(line.get("text", ""))
        if row is None:
            rows = []
            continue
        rows.append({**line, "row": row})
        if len(rows) == 8:
            candidates.append(_candidate_row(id_prefix, source_pdf, page_number, diagram_index, rows))
            diagram_index += 1
            rows = []
    return candidates


def normalize_font_board_row(text: str) -> str | None:
    compact = re.sub(r"\s+", "", str(text or ""))
    if len(compact) == 9 and compact[0] in "12345678":
        compact = compact[1:]
    if len(compact) != 8 or not FONT_BOARD_ROW_RE.fullmatch(compact):
        return None
    if sum(1 for char in compact if char in FONT_BOARD_EMPTY_MARKERS) < 4:
        return None
    return compact


def _font_board_lines_from_page(page: Any) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    page_dict = page.get_text("dict")
    for block in page_dict.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue
            font_names = sorted({str(span.get("font", "")) for span in spans if span.get("font")})
            row = normalize_font_board_row(text)
            if row is None and not any("skak" in font.lower() for font in font_names):
                continue
            bbox = _line_bbox(line)
            lines.append(
                {
                    "text": text,
                    "font_names": font_names,
                    "bbox": list(bbox) if bbox else None,
                }
            )
    return lines


def _line_bbox(line: dict[str, Any]) -> tuple[float, float, float, float] | None:
    spans = [span for span in line.get("spans", []) if span.get("bbox")]
    if not spans:
        return None
    xs0, ys0, xs1, ys1 = zip(*(span["bbox"] for span in spans))
    return (float(min(xs0)), float(min(ys0)), float(max(xs1)), float(max(ys1)))


def _candidate_row(
    id_prefix: str,
    source_pdf: str,
    page_number: int,
    diagram_index: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    bboxes = [row.get("bbox") for row in rows if row.get("bbox")]
    bbox = _merge_bboxes(bboxes)
    font_names = sorted({font for row in rows for font in row.get("font_names", [])})
    raw_rows = [str(row["row"]) for row in rows]
    recognition = recognize_font_board_from_lines(raw_rows, bbox=bbox, min_confidence=0.84).to_dict()
    return {
        "id": f"{id_prefix}_p{page_number:03d}_d{diagram_index:02d}",
        "source_pdf": source_pdf,
        "page": int(page_number),
        "diagram_index": int(diagram_index),
        "input_type": "font_board_text",
        "font_names": font_names,
        "bbox": list(bbox) if bbox else None,
        "raw_rows": raw_rows,
        "raw_board_text": "\n".join(raw_rows),
        "candidate_fen": recognition.get("fen", ""),
        "candidate_confidence": recognition.get("confidence", 0.0),
        "candidate_warnings": recognition.get("warnings", []),
        "candidate_requires_review": recognition.get("requires_review", True),
        "fen": "",
        "label_status": "needs_manual_fen",
        "verified_by": "",
        "verified_at": "",
        "notes": REVIEW_ONLY_NOTE,
    }


def _merge_bboxes(bboxes: list[Any]) -> tuple[float, float, float, float] | None:
    normalized = [tuple(float(value) for value in bbox) for bbox in bboxes if bbox and len(bbox) == 4]
    if not normalized:
        return None
    xs0, ys0, xs1, ys1 = zip(*normalized)
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract review-only labels from text/font encoded chess board diagrams.")
    parser.add_argument("pdf")
    parser.add_argument("--output-dir", default="reports/chess_fen/font_board_intake/latest")
    parser.add_argument("--page-start", type=int, default=1)
    parser.add_argument("--pages", type=int, default=96)
    parser.add_argument("--min-seed-labels", type=int, default=20)
    args = parser.parse_args()

    result = extract_chess_font_board_candidates(
        args.pdf,
        output_dir=args.output_dir,
        page_start=args.page_start,
        pages=args.pages,
        min_seed_labels=args.min_seed_labels,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"review_required", "insufficient_candidates"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
