"""
Premium reflowable PDF -> structured book content extractor.

Designed for text-heavy books, textbooks, guides, and long-form reports with proper:
- PDF TOC-driven chapter structure (respects publisher intent)
- Paragraph reconstruction across line / page breaks
- Vector drawing / diagram extraction as rendered PNG figures
- Mojibake / encoding repair for trademark, smart quotes, dashes
- Heading detection by font size with full-line assembly
- Kindle-friendly output dict ready for converter.build_epub

Output dict shape (identical to extract_pdf_with_pymupdf):
    {
        "success": True,
        "method": "premium-reflow",
        "chapters": [ {title, html_parts, images, _source_page_label}, ... ],
        "images": [ {filename, data, extension, page}, ... ],
        "toc": [ (level, title, page), ... ],
    }
"""
from __future__ import annotations

import html as html_module
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional

import fitz  # PyMuPDF
from toc_segmentation import normalize_toc_entries, select_section_outline_entries

try:
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Mojibake / encoding repair
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Common broken sequences seen in PDFs with non-standard encodings.
_MOJIBAKE_MAP = {
    # Common replacement-character and CP1252/UTF-8 mojibake around trademark symbols.
    # Common CP1252 -> UTF-8 mojibake
    "\u00e2\u0080\u0099": "\u2019",  # '
    "\u00e2\u0080\u0098": "\u2018",  # '
    "\u00e2\u0080\u009c": "\u201c",  # "
    "\u00e2\u0080\u009d": "\u201d",  # "
    "\u00e2\u0080\u0093": "\u2013",  # â€“
    "\u00e2\u0080\u0094": "\u2014",  # â€”
    "\u00e2\u0080\u00a6": "\u2026",  # â€¦
    "\u00e2\u0080\u00a2": "\u2022",  # â€˘
    "\u00c2\u00ae": "\u00ae",        # Â®
    "\u00c2\u00a9": "\u00a9",        # Â©
    "\u00e2\u0084\u00a2": "\u2122",  # â„˘
    # Lonely replacement chars adjacent to common brand letters â€“ best effort
    "\ufffd\u00ae": "\u00ae",
    "\ufffd\u2122": "\u2122",
}
_REGISTERED_SUFFIX_MOJIBAKE_RE = re.compile("(?P<word>[A-Za-z0-9][A-Za-z0-9&+._/-]{1,})(?:\u0139\u02dd|\u017d)(?=\\W|$)")


def _repair_mojibake(text: str) -> str:
    if not text:
        return text
    for broken, fixed in _MOJIBAKE_MAP.items():
        if broken in text:
            text = text.replace(broken, fixed)
    text = _REGISTERED_SUFFIX_MOJIBAKE_RE.sub(lambda match: match.group("word") + "\u00ae", text)
    # Replace stray U+FFFD right after an uppercase brand-ish letter with Â®
    # (PDFs that used Â® in a custom font often decode to U+FFFD)
    text = re.sub(r"([A-Za-z])\ufffd(?=\W|$)", lambda match: match.group(1) + "\u00ae", text)
    # Collapse any remaining U+FFFD to empty (they are pure noise)
    text = text.replace("\ufffd", "")
    # Normalize NFC
    text = unicodedata.normalize("NFC", text)
    return text


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Line / span data
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@dataclass
class TextLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float            # dominant span size
    is_bold: bool
    is_italic: bool
    html: str              # inline html with <strong>/<em>
    page_index: int
    font_ratio: float = 1.0  # filled later relative to body size

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)


@dataclass
class Figure:
    filename: str
    data: bytes
    extension: str
    page_index: int
    y_position: float  # for insertion order on the page
    caption: str = ""
    source: str = "vector"  # or "raster"
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    caption_key: tuple[float, str] | None = None


@dataclass
class ChapterDraft:
    title: str
    level: int
    page_start: int  # 0-indexed
    page_end: int    # 0-indexed, inclusive
    y_start: Optional[float] = None
    y_end: Optional[float] = None
    source_page_label: Optional[str] = None
    lines: list[TextLine] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)


@dataclass
class PublicationTable:
    page_index: int
    bbox: tuple[float, float, float, float]
    y_position: float
    rows: list[list[str]]
    header_rows: int = 0
    caption: str = ""
    confidence: float = 1.0
    source_method: str = "pdfplumber"
    classification: str = "semantic"
    issues: list[str] = field(default_factory=list)
    page_span: list[int] = field(default_factory=list)
    html: str = ""

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def cell_count(self) -> int:
        return sum(len(row) for row in self.rows)


TableRegion = PublicationTable


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Span â†’ line assembly with inline formatting
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _span_is_bold(span) -> bool:
    # PyMuPDF sets bit 4 for monospace, bit 16 for bold. Font name check helps for Type1 fonts.
    return bool(span.get("flags", 0) & (1 << 4)) or "Bold" in span.get("font", "")


def _span_is_italic(span) -> bool:
    return bool(span.get("flags", 0) & (1 << 1)) or any(
        tag in span.get("font", "") for tag in ("Italic", "Oblique", "It")
    )


def _extract_lines_from_page(page, page_index: int) -> list[TextLine]:
    lines: list[TextLine] = []
    data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_DEHYPHENATE)
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = line.get("bbox", (0, 0, 0, 0))
            if bbox[3] - bbox[1] <= 0:
                continue
            pieces: list[str] = []
            sizes: list[float] = []
            any_bold = False
            any_italic = False
            for span in line.get("spans", []):
                raw = span.get("text", "")
                if not raw:
                    continue
                repaired = _repair_mojibake(raw)
                if not repaired:
                    continue
                size = float(span.get("size", 12) or 12)
                sizes.append(size)
                bold = _span_is_bold(span)
                italic = _span_is_italic(span)
                any_bold = any_bold or bold
                any_italic = any_italic or italic
                escaped = html_module.escape(repaired)
                if bold and italic:
                    escaped = f"<strong><em>{escaped}</em></strong>"
                elif bold:
                    escaped = f"<strong>{escaped}</strong>"
                elif italic:
                    escaped = f"<em>{escaped}</em>"
                pieces.append(escaped)
            if not pieces:
                continue
            plain = _repair_mojibake(
                "".join(span.get("text", "") for span in line.get("spans", []))
            )
            dominant_size = max(sizes) if sizes else 12.0
            lines.append(
                TextLine(
                    text=plain,
                    x0=float(bbox[0]),
                    y0=float(bbox[1]),
                    x1=float(bbox[2]),
                    y1=float(bbox[3]),
                    size=dominant_size,
                    is_bold=any_bold,
                    is_italic=any_italic,
                    html="".join(pieces),
                    page_index=page_index,
                )
            )
    return _sort_lines_in_reading_order(lines, page_width=float(page.rect.width or 0.0))


def _sort_lines_in_reading_order(lines: list[TextLine], *, page_width: float) -> list[TextLine]:
    """Sort page text in Kindle reading order, including simple two-column pages."""
    top_to_bottom = sorted(lines, key=lambda ln: (round(ln.y0, 1), ln.x0))
    if not _page_has_two_column_text(top_to_bottom, page_width=page_width):
        return top_to_bottom

    ordered: list[TextLine] = []
    current_zone: list[TextLine] = []
    for line in top_to_bottom:
        if _line_is_full_width_break(line, page_width=page_width):
            ordered.extend(_sort_column_zone(current_zone, page_width=page_width))
            current_zone = []
            ordered.append(line)
            continue
        current_zone.append(line)
    ordered.extend(_sort_column_zone(current_zone, page_width=page_width))
    return ordered


def _line_sort_key(line: TextLine) -> tuple[float, float]:
    return (round(line.y0, 1), line.x0)


def _line_is_full_width_break(line: TextLine, *, page_width: float) -> bool:
    if page_width <= 0:
        return False
    width = max(0.0, line.x1 - line.x0)
    if width >= page_width * 0.62:
        return True
    return line.x0 <= page_width * 0.16 and line.x1 >= page_width * 0.84


def _page_has_two_column_text(lines: list[TextLine], *, page_width: float) -> bool:
    return _two_column_stats(lines, page_width=page_width)["is_two_column"]


def _sort_column_zone(lines: list[TextLine], *, page_width: float) -> list[TextLine]:
    if not lines:
        return []
    stats = _two_column_stats(lines, page_width=page_width)
    if not stats["is_two_column"]:
        return sorted(lines, key=_line_sort_key)

    mid = page_width / 2.0
    left: list[TextLine] = []
    middle: list[TextLine] = []
    right: list[TextLine] = []
    for line in lines:
        center = (line.x0 + line.x1) / 2.0
        if center < mid and line.x1 < mid + page_width * 0.08:
            left.append(line)
        elif center >= mid and line.x0 > mid - page_width * 0.08:
            right.append(line)
        else:
            middle.append(line)

    if len(left) < 2 or len(right) < 2:
        return sorted(lines, key=_line_sort_key)
    return sorted(left, key=_line_sort_key) + sorted(middle, key=_line_sort_key) + sorted(right, key=_line_sort_key)


def _two_column_stats(lines: list[TextLine], *, page_width: float) -> dict[str, Any]:
    if page_width <= 0 or len(lines) < 6:
        return {"is_two_column": False, "left_count": 0, "right_count": 0, "ambiguous_count": 0, "gap": 0.0}
    mid = page_width / 2.0
    narrow_lines = [
        line
        for line in lines
        if line.text.strip()
        and not _line_is_full_width_break(line, page_width=page_width)
        and max(0.0, line.x1 - line.x0) <= page_width * 0.52
    ]
    left = [line for line in narrow_lines if ((line.x0 + line.x1) / 2.0) < mid and line.x1 < mid + page_width * 0.08]
    right = [line for line in narrow_lines if ((line.x0 + line.x1) / 2.0) >= mid and line.x0 > mid - page_width * 0.08]
    assigned = {id(line) for line in left + right}
    ambiguous_count = sum(1 for line in narrow_lines if id(line) not in assigned)
    if len(left) < 2 or len(right) < 2:
        return {
            "is_two_column": False,
            "left_count": len(left),
            "right_count": len(right),
            "ambiguous_count": ambiguous_count,
            "gap": 0.0,
        }
    gap = min(line.x0 for line in right) - max(line.x1 for line in left)
    is_two_column = gap >= page_width * 0.04
    return {
        "is_two_column": is_two_column,
        "left_count": len(left),
        "right_count": len(right),
        "ambiguous_count": ambiguous_count,
        "gap": round(gap, 2),
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Vector drawing â†’ figure extraction
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _cluster_drawing_rects(drawings, page_rect) -> list[fitz.Rect]:
    """Cluster vector drawings into connected figure regions."""
    rects: list[fitz.Rect] = []
    page_area = page_rect.width * page_rect.height
    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        r = fitz.Rect(rect)
        if r.is_empty or r.is_infinite:
            continue
        area = r.width * r.height
        # Filter out page backgrounds, rules, frames, huge decorative boxes
        if area > page_area * 0.7:
            continue
        if area < 4:
            continue
        if r.height < 1.5 and r.width < page_rect.width * 0.9:
            # Thin ruler lines
            continue
        rects.append(r)

    if not rects:
        return []

    # Merge rects that are near each other (within 12pt gap)
    merged: list[fitz.Rect] = []
    used = [False] * len(rects)
    for i, r in enumerate(rects):
        if used[i]:
            continue
        cluster = fitz.Rect(r)
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, other in enumerate(rects):
                if used[j]:
                    continue
                expanded = fitz.Rect(cluster)
                expanded.x0 -= 12
                expanded.y0 -= 12
                expanded.x1 += 12
                expanded.y1 += 12
                if expanded.intersects(other):
                    cluster |= other
                    used[j] = True
                    changed = True
        merged.append(cluster)

    # Drop tiny singletons â€“ not real figures
    filtered: list[fitz.Rect] = []
    for r in merged:
        if r.width * r.height < 900:
            continue
        if r.width < 28 or r.height < 28:
            continue
        filtered.append(r)
    return filtered


def _extract_vector_figures(page, page_index: int, img_counter: list[int], lines: list[TextLine]) -> list[Figure]:
    figures: list[Figure] = []
    drawings = page.get_drawings()
    if not drawings:
        return figures
    clusters = _cluster_drawing_rects(drawings, page.rect)
    if not clusters:
        return figures
    captions = _find_caption_lines(lines)
    if not captions:
        return figures

    used_clusters: set[int] = set()
    for caption in captions:
        rect = None
        rect_index = -1
        caption_bottom = caption.y1
        best_score = None
        for idx, candidate in enumerate(clusters):
            if idx in used_clusters:
                continue
            if candidate.y0 < caption_bottom - 6:
                continue
            if candidate.y0 > caption_bottom + 320:
                continue
            if candidate.width < 90 or candidate.height < 40:
                continue
            score = (abs(candidate.y0 - caption_bottom), -(candidate.width * candidate.height))
            if best_score is None or score < best_score:
                best_score = score
                rect = candidate
                rect_index = idx
        if rect is None:
            continue
        used_clusters.add(rect_index)
        # Pad slightly so strokes aren't clipped
        padded = fitz.Rect(rect)
        padded.x0 = max(page.rect.x0, padded.x0 - 6)
        padded.y0 = max(page.rect.y0, padded.y0 - 6)
        padded.x1 = min(page.rect.x1, padded.x1 + 6)
        padded.y1 = min(page.rect.y1, padded.y1 + 6)
        try:
            png = _render_clip_png(page, padded, dpi=180, max_long_edge=1200)
        except Exception:
            continue
        img_counter[0] += 1
        filename = f"diagram_p{page_index + 1}_{img_counter[0]}.png"
        figures.append(
            Figure(
                filename=filename,
                data=png,
                extension="png",
                page_index=page_index,
                y_position=caption.y0,
                caption=caption.text.strip(),
                source="vector",
                bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                caption_key=(round(caption.y0, 1), caption.text.strip()),
            )
        )
    return figures


def _extract_raster_figures(
    page,
    doc,
    page_index: int,
    img_counter: list[int],
    lines: list[TextLine],
    *,
    used_regions: list[fitz.Rect] | None = None,
) -> list[Figure]:
    figures: list[Figure] = []
    captions = _find_caption_lines(lines)
    used_captions: set[int] = set()
    for img_info in page.get_images(full=True):
        xref = img_info[0]
        try:
            base = doc.extract_image(xref)
        except Exception:
            continue
        if not base or not base.get("image"):
            continue
        width = base.get("width", 0) or 0
        height = base.get("height", 0) or 0
        if width < 32 or height < 32:
            continue  # decorative glyphs
        img_counter[0] += 1
        filename = f"figure_p{page_index + 1}_{img_counter[0]}.{base['ext']}"
        # Use image block bbox when available for ordering
        try:
            rects = page.get_image_rects(xref) or []
            y_pos = rects[0].y0 if rects else 0.0
            bbox = (rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1) if rects else (0, 0, 0, 0)
        except Exception:
            y_pos = 0.0
            bbox = (0, 0, 0, 0)
        if used_regions and bbox != (0, 0, 0, 0):
            image_rect = fitz.Rect(*bbox)
            if any(region.intersects(image_rect) for region in used_regions):
                continue

        caption_text = ""
        caption_key = None
        if bbox != (0, 0, 0, 0):
            best_caption = None
            best_caption_index = -1
            best_distance = None
            for idx, caption in enumerate(captions):
                if idx in used_captions:
                    continue
                if caption.y1 <= bbox[1] + 4:
                    distance = bbox[1] - caption.y1
                elif caption.y0 >= bbox[3] - 4:
                    distance = caption.y0 - bbox[3]
                else:
                    continue
                if distance > 64:
                    continue
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_caption = caption
                    best_caption_index = idx
            if best_caption is not None:
                used_captions.add(best_caption_index)
                caption_text = best_caption.text.strip()
                caption_key = (round(best_caption.y0, 1), best_caption.text.strip())

        figures.append(
            Figure(
                filename=filename,
                data=base["image"],
                extension=base["ext"],
                page_index=page_index,
                y_position=y_pos,
                caption=caption_text,
                source="raster",
                bbox=bbox,
                caption_key=caption_key,
            )
        )
    return figures


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Paragraph reconstruction
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_HEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+){0,4}\.?)(\s+.+)?$")
_BULLET_RE = re.compile(r"^\s*[â€˘\u2022\u2023\u25E6\u00b7\u2219]\s*")
_FIGURE_CAPTION_RE = re.compile(
    r"^(Figure|Table|Diagram|Chart|Illustration|Exhibit)\s+[A-Za-z0-9][A-Za-z0-9.\-]*(?::|\b)",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(r"^Table\s+[A-Za-z0-9][A-Za-z0-9.\-]*(?::|\b)", re.IGNORECASE)


def _find_caption_lines(lines: list[TextLine]) -> list[TextLine]:
    return [line for line in lines if _FIGURE_CAPTION_RE.match(line.text.strip()) and not _TABLE_CAPTION_RE.match(line.text.strip())]


def _render_clip_png(page, clip: fitz.Rect, *, dpi: int, max_long_edge: int) -> bytes:
    base_scale = max(1.0, dpi / 72.0)
    long_edge = max(float(clip.width), float(clip.height), 1.0)
    scale = min(base_scale, max_long_edge / long_edge)
    scale = max(1.0, scale)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
    return pix.tobytes("png")


_PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_MEMBER_COPY_RE = re.compile(
    r"(?i)complimentary\s+iiba(?:Ă‚Â®|Â®)?\s+member\s+copy|not\s+for\s+distribution\s+or\s+resale"
)


def _looks_like_structural_heading_line(line: TextLine, body_size: float) -> bool:
    text = re.sub(r"\s+", " ", line.text.strip())
    if not text or _FIGURE_CAPTION_RE.match(text):
        return False
    if _PAGE_NUMBER_RE.fullmatch(text):
        return False
    if line.size >= body_size * 1.18 and len(text) <= 140:
        return True
    if _HEADING_NUM_RE.match(text) and line.x0 <= 96:
        return True
    if line.is_bold and line.size >= body_size * 1.06 and len(text) <= 120:
        return True
    return False


def _filter_noise_lines(lines: list[TextLine], *, page_rect: fitz.Rect) -> list[TextLine]:
    filtered: list[TextLine] = []
    for line in lines:
        text = re.sub(r"\s+", " ", line.text.strip())
        if not text:
            continue
        if _MEMBER_COPY_RE.search(text):
            continue
        if _PAGE_NUMBER_RE.fullmatch(text) and (line.y0 < page_rect.y0 + 72 or line.y1 > page_rect.y1 - 42):
            continue
        filtered.append(line)
    return filtered


def _drop_repeated_running_headers(lines: list[TextLine], *, chapter_title: str = "") -> list[TextLine]:
    if not lines:
        return lines

    title_key = re.sub(r"\s+", " ", chapter_title.strip().lower())
    top_counts: dict[str, int] = {}
    for line in lines:
        text = re.sub(r"\s+", " ", line.text.strip())
        key = text.lower()
        if not text or len(text) > 120 or line.y0 > 48:
            continue
        top_counts[key] = top_counts.get(key, 0) + 1

    repeated_headers = {
        key for key, count in top_counts.items() if count >= 2 or (title_key and key == title_key)
    }
    if not repeated_headers:
        return lines

    return [
        line
        for line in lines
        if not (
            line.y0 <= 48
            and re.sub(r"\s+", " ", line.text.strip()).lower() in repeated_headers
        )
    ]


def _extract_captioned_region_figures(
    page,
    page_index: int,
    img_counter: list[int],
    lines: list[TextLine],
    *,
    body_size: float,
    used_caption_keys: set[tuple[float, str]] | None = None,
    used_regions: list[fitz.Rect] | None = None,
) -> list[Figure]:
    figures: list[Figure] = []
    captions = _find_caption_lines(lines)
    if not captions:
        return figures

    page_rect = page.rect
    for index, caption in enumerate(captions):
        caption_key = (round(caption.y0, 1), caption.text.strip())
        if used_caption_keys and caption_key in used_caption_keys:
            continue
        next_caption_y = captions[index + 1].y0 if index + 1 < len(captions) else page_rect.y1 - 24
        stop_y = min(next_caption_y - 10, page_rect.y1 - 24)

        for candidate in lines:
            if candidate.y0 <= caption.y1 + 18 or candidate.y0 >= stop_y:
                continue
            if _looks_like_structural_heading_line(candidate, body_size):
                stop_y = min(stop_y, candidate.y0 - 12)
                break

        if stop_y <= caption.y1 + 28:
            continue

        content_lines = [
            line
            for line in lines
            if line.y0 >= caption.y1 + 4
            and line.y1 <= stop_y
            and not _MEMBER_COPY_RE.search(line.text)
        ]
        if not content_lines:
            continue

        x0 = min(line.x0 for line in content_lines)
        y0 = min(line.y0 for line in content_lines)
        x1 = max(line.x1 for line in content_lines)
        y1 = max(line.y1 for line in content_lines)
        clip = fitz.Rect(
            max(page_rect.x0, x0 - 12),
            max(page_rect.y0, y0 - 8),
            min(page_rect.x1, x1 + 12),
            min(page_rect.y1, y1 + 10),
        )
        if clip.width < 80 or clip.height < 40:
            continue
        if used_regions and any(region.intersects(clip) for region in used_regions):
            continue

        try:
            image_bytes = _render_clip_png(page, clip, dpi=190, max_long_edge=1200)
        except Exception:
            continue

        img_counter[0] += 1
        label = caption.text.strip()
        filename = f"figure_region_p{page_index + 1}_{img_counter[0]}.png"
        figures.append(
            Figure(
                filename=filename,
                data=image_bytes,
                extension="png",
                page_index=page_index,
                y_position=caption.y0,
                caption=label,
                source="region",
                bbox=(clip.x0, max(page_rect.y0, caption.y0 - 2), clip.x1, clip.y1),
                caption_key=caption_key,
            )
        )

    return figures


def _dehyphenate_join(prev: str, nxt: str) -> str:
    prev = prev.rstrip()
    nxt = nxt.lstrip()
    if not prev:
        return nxt
    if not nxt:
        return prev
    if prev.endswith(("-", "\u00ad")) and nxt[:1].isalpha():
        # Join without space
        return prev[:-1] + nxt
    # Avoid double spaces around punctuation
    return prev + " " + nxt


def _merge_lines_into_paragraphs(
    lines: list[TextLine],
    body_size: float,
    heading_thresholds: dict,
) -> list[dict]:
    """Group lines into blocks: paragraph, heading, list-item, figure-placeholder.

    Output blocks: {type, text, html, level}
    """
    blocks: list[dict] = []
    if not lines:
        return blocks

    def _is_heading_size(line: TextLine) -> int:
        # Returns heading level 1/2/3 or 0 if body
        stripped = line.text.strip()
        if _BULLET_RE.match(stripped) or _FIGURE_CAPTION_RE.match(stripped):
            return 0
        if line.size >= heading_thresholds["h1"]:
            return 1
        if line.size >= heading_thresholds["h2"]:
            return 2
        if line.size >= heading_thresholds["h3"]:
            return 3
        # Bold short line with slightly larger font â†’ h3 candidate
        if line.is_bold and line.size > body_size * 1.03 and len(line.text.strip()) < 120:
            return 3
        return 0

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        level = _is_heading_size(line)

        # â”€â”€ heading block â”€â”€
        if level:
            heading_lines = [line]
            j = i + 1
            # Merge consecutive lines of the same size on close y-axis (multi-line heading)
            while j < n:
                nxt = lines[j]
                if _is_heading_size(nxt) == level and nxt.page_index == line.page_index:
                    vgap = nxt.y0 - heading_lines[-1].y1
                    if 0 <= vgap <= line.size * 0.6:
                        heading_lines.append(nxt)
                        j += 1
                        continue
                break
            raw_text = " ".join(ln.text.strip() for ln in heading_lines).strip()
            raw_text = re.sub(r"\s+", " ", raw_text)
            if raw_text:
                blocks.append({
                    "type": "heading",
                    "level": level,
                    "text": raw_text,
                    "page_index": line.page_index,
                    "y0": line.y0,
                })
            i = j
            continue

        # â”€â”€ list item (starts with bullet) â”€â”€
        if _BULLET_RE.match(line.text):
            text = _BULLET_RE.sub("", line.text).strip()
            html = html_module.escape(text)
            j = i + 1
            while j < n:
                nxt = lines[j]
                if _is_heading_size(nxt):
                    break
                if _BULLET_RE.match(nxt.text):
                    break
                # same page, close vertical gap, larger left indent â†’ continuation
                vgap = nxt.y0 - lines[j - 1].y1
                if vgap > body_size * 1.4:
                    break
                if nxt.page_index != line.page_index and vgap > body_size * 2:
                    break
                text = _dehyphenate_join(text, nxt.text)
                html = _dehyphenate_join(html, html_module.escape(nxt.text))
                j += 1
            blocks.append({
                "type": "list-item",
                "text": text,
                "html": html,
                "page_index": line.page_index,
                "y0": line.y0,
            })
            i = j
            continue

        # â”€â”€ regular paragraph (merge until gap / heading / page-break with reset) â”€â”€
        text = line.text
        html = line.html
        para_x0 = line.x0
        j = i + 1
        while j < n:
            nxt = lines[j]
            if _is_heading_size(nxt):
                break
            if _BULLET_RE.match(nxt.text):
                break
            # cross-page: allow continuation if left edge matches and no new indent
            if nxt.page_index != lines[j - 1].page_index:
                if abs(nxt.x0 - para_x0) > 12:
                    break
                # No reliable vgap across pages; only merge if current line ends mid-sentence
                if text.rstrip().endswith((".", "!", "?", ":", "â€ť", "\"", ")")):
                    break
                text = _dehyphenate_join(text, nxt.text)
                html = _dehyphenate_join(html, html_module.escape(nxt.text))
                j += 1
                continue
            vgap = nxt.y0 - lines[j - 1].y1
            if vgap > body_size * 1.4:
                break
            # New paragraph if large indent compared to current
            if nxt.x0 > para_x0 + 14 and j == i + 1:
                # first-line indent of a NEW para that starts on same visual block
                break
            text = _dehyphenate_join(text, nxt.text)
            html = _dehyphenate_join(html, html_module.escape(nxt.text))
            j += 1
        cleaned = re.sub(r"\s+", " ", text).strip()
        html_clean = re.sub(r"\s+", " ", html).strip()
        if cleaned:
            blocks.append({
                "type": "paragraph",
                "text": cleaned,
                "html": html_clean,
                "page_index": line.page_index,
                "y0": line.y0,
            })
        i = j
    return blocks


def _filter_lines_for_figures(lines: list[TextLine], figures: list[Figure]) -> list[TextLine]:
    if not lines or not figures:
        return lines

    figure_boxes = [fitz.Rect(*figure.bbox) for figure in figures if figure.bbox != (0, 0, 0, 0)]
    caption_keys = {figure.caption_key for figure in figures if figure.caption_key}
    filtered: list[TextLine] = []
    for line in lines:
        line_key = (round(line.y0, 1), line.text.strip())
        if line_key in caption_keys:
            continue
        line_rect = fitz.Rect(line.x0, line.y0, line.x1, line.y1)
        if any(box.intersects(line_rect) for box in figure_boxes):
            continue
        filtered.append(line)
    return filtered


TABLE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>()]+|www\.[^\s<>()]+)", re.IGNORECASE)
INLINE_XHTML_TABLE_MAX_COLUMNS = 6
WIDE_XHTML_TABLE_MAX_COLUMNS = 19
MATRIX_MAPPING_MIN_COLUMNS = 20
CHECKBOX_MARKERS = {
    "x",
    "yes",
    "y",
    "true",
    "1",
    "\u2713",
    "\u2714",
    "\u221a",
    "\u2022",
    "\u25cf",
    "\u25a0",
    "\u2611",
}


def _extract_structured_table_regions(pdf_path: str) -> dict[int, list[PublicationTable]]:
    if pdfplumber is None:
        return {}
    table_regions: dict[int, list[PublicationTable]] = {}
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_index, page in enumerate(pdf.pages):
                for table in page.find_tables() or []:
                    rows = table.extract() or []
                    classification, issues, confidence = _classify_pdf_table_rows(rows)
                    if classification in {"reference_like", "toc_like", "layout_grid"}:
                        continue
                    normalized_rows = _normalize_table_rows(rows)
                    if not normalized_rows:
                        continue
                    x0, top, x1, bottom = table.bbox
                    table_model = PublicationTable(
                        page_index=page_index,
                        bbox=(float(x0), float(top), float(x1), float(bottom)),
                        rows=normalized_rows,
                        header_rows=_infer_table_header_rows(normalized_rows),
                        y_position=float(top),
                        confidence=confidence,
                        classification=classification,
                        issues=issues,
                        page_span=[page_index],
                    )
                    table_model.html = _publication_table_to_html(table_model)
                    table_regions.setdefault(page_index, []).append(table_model)
    except Exception:
        return {}
    return _merge_continued_tables(table_regions)


def _table_rows_look_like_reference_records(rows: list[list[str | None]]) -> bool:
    flattened = " ".join(str(cell or "") for row in rows for cell in row)
    normalized = re.sub(r"\s+", " ", flattened).strip()
    if not normalized:
        return False
    if re.search(r"\[R\d+\]", normalized):
        return True
    lowered = normalized.lower()
    return "źródło" in lowered and "adres" in lowered and "http" in lowered


def _normalize_table_rows(rows: list[list[str | None]]) -> list[list[str]]:
    cleaned_rows: list[list[str]] = []
    for row in rows:
        cleaned = [re.sub(r"\s+", " ", str(cell or "").strip()) for cell in row]
        if any(cleaned):
            cleaned_rows.append(cleaned)
    if not cleaned_rows:
        return []
    column_count = max(len(row) for row in cleaned_rows)
    return [row + [""] * (column_count - len(row)) for row in cleaned_rows]


def _classify_pdf_table_rows(rows: list[list[str | None]]) -> tuple[str, list[str], float]:
    normalized_rows = _normalize_table_rows(rows)
    if not normalized_rows:
        return "empty", ["empty-table"], 0.0
    if _table_rows_look_like_reference_records(rows):
        return "reference_like", ["reference-like-table"], 0.2
    if _table_rows_look_like_toc(normalized_rows):
        return "toc_like", ["toc-like-table"], 0.25
    row_count = len(normalized_rows)
    column_count = max((len(row) for row in normalized_rows), default=0)
    non_empty = sum(1 for row in normalized_rows for cell in row if cell)
    total_cells = row_count * max(column_count, 1)
    fill_ratio = non_empty / total_cells if total_cells else 0.0
    issues: list[str] = []
    confidence = 0.96
    classification = "semantic"
    if column_count >= MATRIX_MAPPING_MIN_COLUMNS and _table_rows_look_like_checkbox_matrix(normalized_rows):
        issues.extend(["wide-table", "matrix-checkbox-table", "matrix-table-transformed"])
        return "matrix_mapping", issues, 0.88
    if column_count > INLINE_XHTML_TABLE_MAX_COLUMNS:
        issues.append("wide-table")
        classification = "wide"
        if column_count <= WIDE_XHTML_TABLE_MAX_COLUMNS:
            issues.append("wide-table-review")
            confidence = min(confidence, 0.82)
        else:
            issues.append("very-wide-table-review")
            confidence = min(confidence, 0.68)
    if row_count < 2 or column_count < 2:
        issues.append("low-confidence-table-shape")
        confidence = min(confidence, 0.55)
        classification = "low_confidence"
    if fill_ratio < 0.35:
        issues.append("sparse-table")
        confidence = min(confidence, 0.6)
        if row_count <= 3:
            return "layout_grid", ["layout-grid-like-table"], 0.25
        classification = "low_confidence"
    if _table_rows_look_like_fragment(normalized_rows):
        issues.append("table-fragment")
        confidence = min(confidence, 0.58)
        classification = "fragment"
    return classification, issues, confidence


def _table_rows_look_like_checkbox_matrix(rows: list[list[str]]) -> bool:
    column_count = max((len(row) for row in rows), default=0)
    if column_count < MATRIX_MAPPING_MIN_COLUMNS or len(rows) < 3:
        return False
    header_rows = _infer_matrix_header_rows(rows)
    if not header_rows:
        return False
    body_rows = rows[header_rows:]
    label_column = _matrix_label_column_index(rows, header_rows)
    if label_column is None:
        return False
    labeled_rows = 0
    marker_count = 0
    non_marker_mapping_values = 0
    for row in body_rows:
        label = row[label_column].strip() if label_column < len(row) else ""
        if label and not _is_checkbox_marker(label):
            labeled_rows += 1
        for column_index, cell in enumerate(row):
            if column_index == label_column:
                continue
            value = cell.strip()
            if not value:
                continue
            if _is_checkbox_marker(value):
                marker_count += 1
            else:
                non_marker_mapping_values += 1
    if labeled_rows < max(2, len(body_rows) // 2):
        return False
    if marker_count < max(2, len(body_rows) // 2):
        return False
    checked_values = marker_count + non_marker_mapping_values
    marker_ratio = marker_count / checked_values if checked_values else 0.0
    return marker_ratio >= 0.8


def _matrix_label_column_index(rows: list[list[str]], header_rows: int) -> int | None:
    body_rows = rows[header_rows:]
    if not body_rows:
        return None
    column_count = max((len(row) for row in rows), default=0)
    best_index: int | None = None
    best_score = 0
    for column_index in range(min(4, column_count)):
        score = 0
        for row in body_rows:
            cell = row[column_index].strip() if column_index < len(row) else ""
            if cell and not _is_checkbox_marker(cell):
                score += 1
        if score > best_score:
            best_index = column_index
            best_score = score
    return best_index


def _infer_matrix_header_rows(rows: list[list[str]]) -> int:
    header_rows = _infer_table_header_rows(rows)
    if not header_rows:
        return 0
    limit = min(len(rows) - 1, 3)
    while header_rows < limit:
        row = rows[header_rows]
        non_empty = [cell.strip() for cell in row if cell.strip()]
        marker_cells = sum(1 for cell in non_empty if _is_checkbox_marker(cell))
        if len(non_empty) >= 2 and marker_cells == 0:
            header_rows += 1
            continue
        break
    return header_rows


def _is_checkbox_marker(value: str) -> bool:
    normalized = re.sub(r"\s+", "", str(value or "").strip().lower())
    if not normalized:
        return False
    return normalized in CHECKBOX_MARKERS


def _table_rows_look_like_fragment(rows: list[list[str]]) -> bool:
    if len(rows) < 2:
        return False
    column_count = max((len(row) for row in rows), default=0)
    if column_count < 2:
        return False
    header_rows = _infer_table_header_rows(rows)
    body_rows = rows[header_rows:] if header_rows else rows[1:]
    if len(body_rows) != 1:
        return False
    body = body_rows[0]
    empty_count = sum(1 for cell in body if not cell.strip())
    if empty_count >= max(1, column_count // 3):
        return True
    first_cell = body[0].strip() if body else ""
    if not first_cell and any(cell.strip() for cell in body[1:]):
        return True
    return False


def _table_rows_look_like_toc(rows: list[list[str]]) -> bool:
    flattened = " ".join(cell for row in rows for cell in row)
    lowered = flattened.lower()
    if "table of contents" in lowered or "spis treści" in lowered or "spis tresci" in lowered:
        return True
    toc_like_rows = 0
    for row in rows:
        non_empty = [cell.strip() for cell in row if cell and cell.strip()]
        if len(non_empty) < 2:
            continue
        if re.fullmatch(r"\d{1,4}", non_empty[-1]) and not re.fullmatch(r"[-+]?[\d.,%/]+", non_empty[0]):
            toc_like_rows += 1
    if len(rows) >= 4 and toc_like_rows >= 3:
        return True
    page_like_cells = sum(1 for row in rows for cell in row if re.fullmatch(r"\d{1,4}", cell.strip()))
    title_like_cells = sum(1 for row in rows for cell in row if len(cell.split()) >= 2)
    return len(rows) >= 4 and page_like_cells >= 3 and title_like_cells >= 3


def _infer_table_header_rows(rows: list[list[str]]) -> int:
    if len(rows) < 2:
        return 0
    first_row = rows[0]
    if not any(cell for cell in first_row):
        return 0
    numeric_cells = sum(1 for cell in first_row if _looks_like_numeric_table_cell(cell))
    text_cells = sum(1 for cell in first_row if cell and not _looks_like_numeric_table_cell(cell))
    return 1 if text_cells >= numeric_cells else 0


def _publication_table_to_html(table: PublicationTable) -> str:
    if not table.rows:
        return ""
    if not _should_render_publication_table(table):
        return ""
    if table.classification == "matrix_mapping" or (
        table.column_count >= MATRIX_MAPPING_MIN_COLUMNS and _table_rows_look_like_checkbox_matrix(table.rows)
    ):
        matrix_html = _publication_matrix_table_to_html(table)
        if matrix_html:
            return matrix_html
    if table.column_count > WIDE_XHTML_TABLE_MAX_COLUMNS:
        return _publication_very_wide_table_to_html(table)
    return _publication_table_to_xhtml(table)


def _should_render_publication_table(table: PublicationTable) -> bool:
    if table.classification in {"reference_like", "toc_like", "layout_grid", "layout_noise", "empty"}:
        return False
    if table.classification in {"low_confidence", "fragment"} or table.confidence < 0.75:
        return _table_has_strong_rendering_evidence(table)
    return True


def _table_has_strong_rendering_evidence(table: PublicationTable) -> bool:
    if not table.caption or not _looks_like_table_caption(table.caption):
        return False
    if table.row_count < 2 or table.column_count < 2:
        return False
    non_empty = sum(1 for row in table.rows for cell in row if str(cell or "").strip())
    total = max(1, table.row_count * max(1, table.column_count))
    fill_ratio = non_empty / total
    if fill_ratio < 0.45:
        return False
    return not _table_rows_look_like_diagram_label_noise(table.rows)


def _publication_table_to_xhtml(table: PublicationTable) -> str:
    classes = ["report-table"]
    if table.column_count > INLINE_XHTML_TABLE_MAX_COLUMNS or table.classification == "wide":
        classes.append("wide-table")
    if table.column_count > WIDE_XHTML_TABLE_MAX_COLUMNS:
        classes.append("very-wide-table")
    if table.confidence < 0.75 or "sparse-table" in table.issues:
        classes.append("low-confidence-table")
    if "table-fragment" in table.issues or table.classification == "fragment":
        classes.append("table-fragment")
    if len(set(table.page_span or [table.page_index])) > 1 or "multi-page-table" in table.issues:
        classes.append("multi-page-table")
    parts = [f'<table class="{" ".join(classes)}" data-source="pdf-table">']
    if table.caption:
        parts.append(f"<caption>{html_module.escape(table.caption)}</caption>")
    header_rows = max(0, min(table.header_rows, len(table.rows)))
    if header_rows:
        parts.append("<thead><tr>")
        for cell in table.rows[0]:
            parts.append(f'<th scope="col">{_table_cell_to_html(cell)}</th>')
        parts.append("</tr></thead>")
        body_rows = table.rows[header_rows:]
    else:
        body_rows = table.rows
    parts.append("<tbody>")
    for row in body_rows:
        parts.append("<tr>")
        for cell in row:
            class_attr = ' class="numeric-cell"' if _looks_like_numeric_table_cell(cell) else ""
            parts.append(f"<td{class_attr}>{_table_cell_to_html(cell)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    if _table_requires_review_note(table):
        note = "Wide table structure requires review." if "wide-table-review" in table.issues else "Table structure requires review."
        parts.append(f'<p class="table-note">{note}</p>')
    return "".join(parts)


def _publication_matrix_table_to_html(table: PublicationTable) -> str:
    inferred_header_rows = _infer_matrix_header_rows(table.rows)
    header_rows = max(0, min(max(table.header_rows, inferred_header_rows), len(table.rows)))
    if not header_rows:
        return ""
    label_column = _matrix_label_column_index(table.rows, header_rows)
    if label_column is None:
        return ""
    headers = _matrix_column_headers(table.rows[:header_rows], table.column_count)
    body_rows = table.rows[header_rows:]
    parts = ['<section class="report-table matrix-mapping-table wide-table" data-source="pdf-table">']
    if table.caption:
        parts.append(f'<p class="table-caption">{html_module.escape(table.caption)}</p>')
    parts.append('<p class="table-note">Very wide checkbox/matrix table converted to a readable mapping.</p>')
    parts.append('<dl class="matrix-mapping-list">')
    mapped_rows = 0
    for row in body_rows:
        label = row[label_column].strip() if label_column < len(row) else ""
        if not label or _is_checkbox_marker(label):
            continue
        mapped_headers = [
            headers[column_index]
            for column_index, cell in enumerate(row)
            if column_index != label_column
            and column_index < len(headers)
            and headers[column_index]
            and _is_checkbox_marker(cell)
        ]
        if not mapped_headers:
            continue
        mapped_rows += 1
        parts.append(f"<dt>{_table_cell_to_html(label)}</dt><dd><ul>")
        for header in mapped_headers:
            parts.append(f"<li>{_table_cell_to_html(header)}</li>")
        parts.append("</ul></dd>")
    parts.append("</dl></section>")
    return "".join(parts) if mapped_rows else ""


def _publication_very_wide_table_to_html(table: PublicationTable) -> str:
    """Render 20+ column tables as Kindle-readable row summaries, not wide XHTML grids."""
    rows = _normalize_table_rows(table.rows)
    if not rows:
        return ""
    header_rows = max(0, min(table.header_rows or _infer_table_header_rows(rows), len(rows) - 1))
    if not header_rows and len(rows) > 1:
        header_rows = 1
    headers = _matrix_column_headers(rows[:header_rows], max((len(row) for row in rows), default=0)) if header_rows else []
    body_rows = rows[header_rows:] if header_rows else rows
    if not body_rows:
        body_rows = rows

    parts: list[str] = []
    if table.caption:
        parts.append(f'<p class="table-caption">{html_module.escape(table.caption)}</p>')
    parts.append(
        '<p class="table-note">Very wide table converted to readable row summaries for Kindle.</p>'
    )
    parts.append(
        '<ul class="report-table wide-table very-wide-table table-row-list" data-source="pdf-table">'
    )
    emitted_rows = 0
    for row_index, row in enumerate(body_rows, start=1):
        label_index = _first_meaningful_table_cell_index(row)
        label = row[label_index].strip() if label_index is not None else f"Row {row_index}"
        detail_items: list[str] = []
        for column_index, cell in enumerate(row):
            value = cell.strip()
            if not value or column_index == label_index:
                continue
            header = headers[column_index].strip() if column_index < len(headers) else ""
            if header and _table_text_key(header) != _table_text_key(value):
                detail_items.append(
                    f'<li><span class="table-field-label">{_table_cell_to_html(header)}:</span> {_table_cell_to_html(value)}</li>'
                )
            else:
                detail_items.append(f"<li>{_table_cell_to_html(value)}</li>")
        if not label and not detail_items:
            continue
        emitted_rows += 1
        parts.append(f"<li><strong>{_table_cell_to_html(label)}</strong>")
        if detail_items:
            parts.append("<ul>")
            parts.extend(detail_items)
            parts.append("</ul>")
        parts.append("</li>")
    parts.append("</ul>")
    if not emitted_rows:
        return _publication_table_to_xhtml(table)
    return "".join(parts)


def _first_meaningful_table_cell_index(row: list[str]) -> int | None:
    for index, cell in enumerate(row):
        value = re.sub(r"\s+", " ", str(cell or "")).strip()
        if value:
            return index
    return None


def _table_text_key(value: str) -> str:
    return re.sub(r"\W+", "", str(value or "").lower())


def _matrix_column_headers(header_rows: list[list[str]], column_count: int) -> list[str]:
    headers: list[str] = []
    for column_index in range(column_count):
        pieces = []
        for row in header_rows:
            cell = row[column_index].strip() if column_index < len(row) else ""
            if cell:
                pieces.append(cell)
        headers.append(" ".join(pieces))
    return headers


def _table_requires_review_note(table: PublicationTable) -> bool:
    review_issues = {"wide-table-review", "very-wide-table-review", "matrix-table-transformed"}
    return table.confidence < 0.75 or bool(review_issues.intersection(table.issues))


def _table_rendered_as_mapping(table: PublicationTable) -> bool:
    html = table.html.lstrip()
    return (
        table.classification == "matrix_mapping"
        or html.startswith('<section class="report-table matrix-mapping-table')
        or "table-row-list" in html
    )


def _transformed_table_has_content(table: PublicationTable) -> bool:
    html = table.html or ""
    if "matrix-mapping-table" in html:
        return "<dt>" in html and "<li>" in html
    if "table-row-list" in html:
        return "<li><strong>" in html
    return False


def _table_rows_look_like_diagram_label_noise(rows: list[list[str]]) -> bool:
    cells = [_table_noise_key(cell) for row in rows for cell in row if str(cell or "").strip()]
    if not cells:
        return True
    if len(cells) <= 4 and all(cell in {"input", "inputs", "output", "outputs", "out", "put", "noun", "verb", "data", "process"} for cell in cells):
        return True
    joined = " ".join(cells)
    return joined in {"out put", "in put", "input output", "inputs outputs"}


def _table_noise_key(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _table_cell_to_html(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    pieces: list[str] = []
    last = 0
    for match in TABLE_URL_RE.finditer(text):
        pieces.append(html_module.escape(text[last : match.start()]))
        raw_url = match.group("url")
        href = raw_url if raw_url.lower().startswith(("http://", "https://")) else f"https://{raw_url}"
        pieces.append(f'<a href="{html_module.escape(href, quote=True)}">{html_module.escape(raw_url)}</a>')
        last = match.end()
    pieces.append(html_module.escape(text[last:]))
    return "".join(pieces)


def _looks_like_numeric_table_cell(value: str) -> bool:
    normalized = re.sub(r"\s+", "", str(value or "").strip())
    if not normalized:
        return False
    return bool(re.fullmatch(r"[-+]?[$€£złPLNUSD0-9.,%/]+", normalized, re.IGNORECASE))


def _merge_continued_tables(page_tables: dict[int, list[PublicationTable]]) -> dict[int, list[PublicationTable]]:
    flat = sorted(
        [table for tables in page_tables.values() for table in tables],
        key=lambda table: (table.page_index, table.y_position),
    )
    merged: list[PublicationTable] = []
    index = 0
    while index < len(flat):
        current = flat[index]
        index += 1
        while index < len(flat) and _tables_look_like_continuation(current, flat[index]):
            continuation = flat[index]
            body_rows = continuation.rows[continuation.header_rows :] if continuation.header_rows else continuation.rows
            current.rows.extend(body_rows)
            current.page_span = sorted(set((current.page_span or [current.page_index]) + (continuation.page_span or [continuation.page_index])))
            current.issues = sorted(set(current.issues + continuation.issues + ["multi-page-table"]))
            current.classification = "multi_page"
            current.confidence = min(current.confidence, continuation.confidence, 0.82)
            current.html = _publication_table_to_html(current)
            index += 1
        merged.append(current)
    result: dict[int, list[PublicationTable]] = {}
    for table in merged:
        result.setdefault(table.page_index, []).append(table)
    return result


def _tables_look_like_continuation(left: PublicationTable, right: PublicationTable) -> bool:
    if right.page_index != left.page_index + 1:
        return False
    if left.column_count < 2 or left.column_count != right.column_count:
        return False
    if abs(left.bbox[0] - right.bbox[0]) > 12 or abs(left.bbox[2] - right.bbox[2]) > 18:
        return False
    if not left.header_rows or not right.header_rows:
        return False
    return _normalized_table_row(left.rows[0]) == _normalized_table_row(right.rows[0])


def _normalized_table_row(row: list[str]) -> tuple[str, ...]:
    return tuple(re.sub(r"\W+", "", cell.lower()) for cell in row)


def _filter_lines_for_tables(lines: list[TextLine], tables: list[TableRegion]) -> list[TextLine]:
    if not lines or not tables:
        return lines
    table_boxes = [fitz.Rect(*table.bbox) for table in tables]
    filtered: list[TextLine] = []
    for line in lines:
        line_rect = fitz.Rect(line.x0, line.y0, line.x1, line.y1)
        if any(box.intersects(line_rect) for box in table_boxes):
            continue
        filtered.append(line)
    return filtered


def _attach_table_captions(tables: list[PublicationTable], lines: list[TextLine]) -> None:
    if not tables or not lines:
        return
    for table in tables:
        candidates = [
            line
            for line in lines
            if line.y1 <= table.bbox[1]
            and line.y1 >= table.bbox[1] - 48
            and line.x1 >= table.bbox[0] - 12
            and line.x0 <= table.bbox[2] + 12
        ]
        if not candidates:
            continue
        caption_line = max(candidates, key=lambda line: line.y1)
        caption = caption_line.text.strip()
        if _looks_like_table_caption(caption):
            table.caption = caption
            table.html = _publication_table_to_html(table)


def _looks_like_table_caption(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized or len(normalized) > 160:
        return False
    return bool(re.match(r"(?i)^(?:table|tabela|tab\.|exhibit)\s+[A-Za-z0-9IVXivx.:-]+\b", normalized))


def _table_summary_payload(page_tables: dict[int, list[PublicationTable]]) -> dict[str, Any]:
    tables = [table for table_list in page_tables.values() for table in table_list]
    if not tables:
        return {
            "source_table_count": 0,
            "xhtml_table_count": 0,
            "transformed_table_count": 0,
            "table_cell_count": 0,
            "table_row_count": 0,
            "table_cell_coverage": 1.0,
            "table_page_count": 0,
            "multi_page_table_count": 0,
            "wide_table_count": 0,
            "low_confidence_table_count": 0,
            "fragment_table_count": 0,
            "false_positive_table_candidate_count": 0,
            "suppressed_table_fragment_count": 0,
            "rendered_low_confidence_table_count": 0,
            "rendered_fragment_table_count": 0,
            "transformed_table_preservation_count": 0,
            "transformed_table_content_loss_count": 0,
            "table_shape_histogram": [],
            "review_tables": [],
        }
    review_tables: list[dict[str, Any]] = []
    shape_counts: dict[str, int] = {}
    for index, table in enumerate(tables, start=1):
        shape_key = f"{table.classification}|{table.row_count}x{table.column_count}"
        shape_counts[shape_key] = shape_counts.get(shape_key, 0) + 1
        if table.confidence < 0.75 or table.issues:
            review_tables.append(
                {
                    "index": index,
                    "page": table.page_index + 1,
                    "rows": table.row_count,
                    "columns": table.column_count,
                    "confidence": round(table.confidence, 3),
                    "classification": table.classification,
                    "issues": list(table.issues),
                    "caption": table.caption,
                    "rendered": bool(table.html),
                }
            )
    transformed_tables = [table for table in tables if _table_rendered_as_mapping(table)]
    rendered_low_confidence = [
        table
        for table in tables
        if table.html and (table.confidence < 0.75 or table.classification == "low_confidence")
    ]
    rendered_fragments = [
        table
        for table in tables
        if table.html and ("table-fragment" in table.issues or table.classification == "fragment")
    ]
    false_positive_candidates = [
        table
        for table in tables
        if (
            table.classification in {"low_confidence", "fragment", "layout_noise"}
            or "low-confidence-table-shape" in table.issues
            or "table-fragment" in table.issues
            or _table_rows_look_like_diagram_label_noise(table.rows)
        )
    ]
    suppressed_fragments = [
        table
        for table in false_positive_candidates
        if not table.html
    ]
    return {
        "source_table_count": len(tables),
        "xhtml_table_count": sum(1 for table in tables if table.html.lstrip().startswith("<table")),
        "transformed_table_count": sum(1 for table in tables if _table_rendered_as_mapping(table)),
        "table_cell_count": sum(table.cell_count for table in tables),
        "table_row_count": sum(table.row_count for table in tables),
        "table_cell_coverage": 1.0,
        "table_page_count": len({page for table in tables for page in (table.page_span or [table.page_index])}),
        "multi_page_table_count": sum(1 for table in tables if len(set(table.page_span or [table.page_index])) > 1),
        "wide_table_count": sum(1 for table in tables if table.column_count > INLINE_XHTML_TABLE_MAX_COLUMNS or table.classification in {"wide", "matrix_mapping"}),
        "low_confidence_table_count": sum(1 for table in tables if table.confidence < 0.75),
        "fragment_table_count": sum(1 for table in tables if "table-fragment" in table.issues or table.classification == "fragment"),
        "false_positive_table_candidate_count": len(false_positive_candidates),
        "suppressed_table_fragment_count": len(suppressed_fragments),
        "rendered_low_confidence_table_count": len(rendered_low_confidence),
        "rendered_fragment_table_count": len(rendered_fragments),
        "transformed_table_preservation_count": sum(1 for table in transformed_tables if _transformed_table_has_content(table)),
        "transformed_table_content_loss_count": sum(1 for table in transformed_tables if not _transformed_table_has_content(table)),
        "table_shape_histogram": [
            {
                "classification": key.split("|", 1)[0],
                "shape": key.split("|", 1)[1],
                "count": count,
            }
            for key, count in sorted(shape_counts.items(), key=lambda item: (-item[1], item[0]))[:40]
        ],
        "review_tables": review_tables[:100],
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Chapter splitting from PDF TOC
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _build_chapter_drafts(doc, toc: list, *, use_outline_positions: bool = False) -> list[ChapterDraft]:
    """Split the document into chapters by top-level TOC entries."""
    if use_outline_positions:
        positioned = _build_positioned_chapter_drafts(doc)
        if positioned:
            return positioned

    if not toc:
        # No TOC at all â€“ single chapter covering the whole doc
        return [ChapterDraft(title="Content", level=1, page_start=0, page_end=len(doc) - 1)]

    entries = normalize_toc_entries(toc)
    if not entries:
        return [ChapterDraft(title="Content", level=1, page_start=0, page_end=len(doc) - 1)]

    outline_entries = select_section_outline_entries(entries)
    if not outline_entries:
        return [ChapterDraft(title="Content", level=1, page_start=0, page_end=len(doc) - 1)]

    drafts: list[ChapterDraft] = []
    for index, entry in enumerate(outline_entries):
        start = entry["page"]
        if index + 1 < len(outline_entries):
            end = max(start, outline_entries[index + 1]["page"] - 1)
        else:
            end = len(doc) - 1
        drafts.append(
            ChapterDraft(
                title=entry["title"],
                level=min(int(entry["level"]), 2),
                page_start=start,
                page_end=end,
            )
        )

    # Prepend front-matter chapter if first TOC entry starts after page 0
    if drafts and drafts[0].page_start > 0:
        drafts.insert(
            0,
            ChapterDraft(
                title="Front Matter",
                level=1,
                page_start=0,
                page_end=drafts[0].page_start - 1,
            ),
        )
    return drafts


def _build_positioned_chapter_drafts(doc) -> list[ChapterDraft]:
    entries = _select_positioned_outline_chapter_entries(_positioned_toc_entries(doc))
    if not entries:
        return []

    drafts: list[ChapterDraft] = []
    for index, entry in enumerate(entries):
        next_entry = entries[index + 1] if index + 1 < len(entries) else None
        page_start = int(entry["page"])
        y_start = entry.get("y")
        if next_entry is None:
            page_end = len(doc) - 1
            y_end = None
        else:
            page_end = max(page_start, int(next_entry["page"]))
            y_end = next_entry.get("y")
        drafts.append(
            ChapterDraft(
                title=str(entry["title"]),
                level=min(int(entry["level"]), 2),
                page_start=page_start,
                page_end=page_end,
                y_start=float(y_start) if y_start is not None else None,
                y_end=float(y_end) if y_end is not None else None,
            )
        )

    first = drafts[0]
    if first.page_start > 0 or (first.page_start == 0 and first.y_start is not None and first.y_start > 72):
        drafts.insert(
            0,
            ChapterDraft(
                title="Front Matter",
                level=1,
                page_start=0,
                page_end=first.page_start,
                y_end=first.y_start,
            ),
        )
    return drafts


def _positioned_toc_entries(doc) -> list[dict]:
    try:
        raw_toc = doc.get_toc(simple=False)
    except TypeError:
        raw_toc = doc.get_toc()
    except Exception:
        return []

    entries: list[dict] = []
    for row in raw_toc:
        if len(row) < 3:
            continue
        title = re.sub(r"\s+", " ", str(row[1] or "").replace("\n", " ")).strip(" -")
        if not title:
            continue
        try:
            level = int(row[0])
            page = max(0, int(row[2]) - 1)
        except (TypeError, ValueError):
            continue
        y = None
        if len(row) >= 4 and isinstance(row[3], dict):
            target = row[3].get("to")
            if target is not None and hasattr(target, "y"):
                try:
                    y = float(target.y)
                except (TypeError, ValueError):
                    y = None
        entries.append({"level": level, "title": title, "page": page, "y": y})

    entries.sort(key=lambda item: (item["page"], float(item["y"]) if item.get("y") is not None else 0.0, item["level"]))
    return entries


def _select_positioned_outline_chapter_entries(entries: list[dict]) -> list[dict]:
    if not entries:
        return []

    top_level = min(int(entry["level"]) for entry in entries)
    selected: list[dict] = []
    seen_targets: set[tuple[int, int, str]] = set()
    for entry in entries:
        if int(entry["level"]) != top_level:
            continue
        title = str(entry.get("title") or "")
        if _is_low_value_outline_title(title):
            continue
        y_value = entry.get("y")
        y_bucket = int(round(float(y_value))) if y_value is not None else -1
        key = (int(entry["page"]), y_bucket, re.sub(r"\s+", " ", title.strip().lower()))
        if key in seen_targets:
            continue
        seen_targets.add(key)
        selected.append(entry)
    return selected


def _is_low_value_outline_title(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", (title or "").strip()).lower()
    return normalized in {
        "contents",
        "table of contents",
        "spis treści",
        "spis tresci",
        "index",
        "name index",
    }


def _line_in_draft(line: TextLine, draft: ChapterDraft) -> bool:
    return _vertical_item_in_draft(line.page_index, line.y0, draft)


def _figure_in_draft(figure: Figure, draft: ChapterDraft) -> bool:
    return _vertical_item_in_draft(figure.page_index, figure.y_position, draft)


def _table_in_draft(table: TableRegion, draft: ChapterDraft) -> bool:
    return _vertical_item_in_draft(table.page_index, table.y_position, draft)


def _vertical_item_in_draft(page_index: int, y_position: float, draft: ChapterDraft) -> bool:
    if page_index < draft.page_start or page_index > draft.page_end:
        return False
    tolerance = 2.0
    if page_index == draft.page_start and draft.y_start is not None and y_position < draft.y_start - tolerance:
        return False
    if page_index == draft.page_end and draft.y_end is not None and y_position >= draft.y_end - tolerance:
        return False
    return True


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Main extractor
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def extract_book_premium(
    pdf_path: str,
    config=None,
    pdf_metadata: Optional[dict] = None,
) -> dict:
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    page_tables = _extract_structured_table_regions(pdf_path)
    document_like_report = bool(toc and page_tables and len(doc) >= 3)

    # â”€â”€ Body size estimation across first ~40 pages â”€â”€
    size_counts: dict[int, int] = {}
    sample_pages = min(40, len(doc))
    for page_num in range(sample_pages):
        page = doc[page_num]
        data = page.get_text("dict")
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if not span.get("text", "").strip():
                        continue
                    key = round(float(span.get("size", 12) or 12))
                    size_counts[key] = size_counts.get(key, 0) + 1
    if size_counts:
        body_size = float(max(size_counts.items(), key=lambda kv: kv[1])[0])
    else:
        body_size = 11.0

    heading_thresholds = {
        "h1": body_size * 1.55,
        "h2": body_size * 1.30,
        "h3": body_size * 1.12,
    }

    # â”€â”€ Extract text lines + figures page by page â”€â”€
    img_counter = [0]
    page_lines: dict[int, list[TextLine]] = {}
    page_figures: dict[int, list[Figure]] = {}
    page_widths: dict[int, float] = {}
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_widths[page_num] = float(page.rect.width or 0.0)
        page_line_items = _filter_noise_lines(
            _extract_lines_from_page(page, page_num),
            page_rect=page.rect,
        )
        page_table_regions = page_tables.get(page_num, [])
        _attach_table_captions(page_table_regions, page_line_items)
        rendered_table_regions = [table for table in page_table_regions if table.html]
        page_line_items = _filter_lines_for_tables(page_line_items, rendered_table_regions)
        figures = _extract_vector_figures(page, page_num, img_counter, page_line_items)
        used_figure_regions = [fitz.Rect(*figure.bbox) for figure in figures if figure.bbox != (0, 0, 0, 0)]
        used_caption_keys = {figure.caption_key for figure in figures if figure.caption_key}
        figures.extend(
            _extract_captioned_region_figures(
                page,
                page_num,
                img_counter,
                page_line_items,
                body_size=body_size,
                used_caption_keys=used_caption_keys,
                used_regions=used_figure_regions,
            )
        )
        used_figure_regions = [fitz.Rect(*figure.bbox) for figure in figures if figure.bbox != (0, 0, 0, 0)]
        figures.extend(
            _extract_raster_figures(
                page,
                doc,
                page_num,
                img_counter,
                page_line_items,
                used_regions=used_figure_regions,
            )
        )
        figures.sort(key=lambda f: f.y_position)
        page_lines[page_num] = _filter_lines_for_figures(page_line_items, figures)
        page_figures[page_num] = figures

    # â”€â”€ Split into chapters via TOC â”€â”€
    drafts = _build_chapter_drafts(doc, toc, use_outline_positions=document_like_report)

    # Build all_images registry (flat)
    all_images: list[dict] = []
    for figs in page_figures.values():
        for f in figs:
            all_images.append(
                {
                    "filename": f.filename,
                    "data": f.data,
                    "extension": f.extension,
                    "page": f.page_index,
                }
            )

    # â”€â”€ Render each chapter to html_parts â”€â”€
    chapters: list[dict] = []
    cover_assigned = False
    figure_neighborhood_samples: list[dict[str, Any]] = []
    reading_flow_samples: list[dict[str, Any]] = []
    for ci, draft in enumerate(drafts):
        if draft.title.strip().lower() == "table of contents":
            continue
        # Collect lines in page range
        lines: list[TextLine] = []
        chapter_figures: list[Figure] = []
        for p in range(draft.page_start, draft.page_end + 1):
            lines.extend(line for line in page_lines.get(p, []) if _line_in_draft(line, draft))
            chapter_figures.extend(figure for figure in page_figures.get(p, []) if _figure_in_draft(figure, draft))
        chapter_tables = [
            table
            for p in range(draft.page_start, draft.page_end + 1)
            for table in page_tables.get(p, [])
            if table.html and _table_in_draft(table, draft)
        ]

        lines = _drop_repeated_running_headers(lines, chapter_title=draft.title)
        blocks = _merge_lines_into_paragraphs(lines, body_size, heading_thresholds)

        # Remove leading block if it duplicates chapter title (publisher often repeats)
        normalized_title = re.sub(r"\s+", " ", draft.title.strip().lower())
        if blocks:
            first = blocks[0]
            first_text = re.sub(r"\s+", " ", first["text"].strip().lower())
            if first_text == normalized_title or normalized_title in first_text:
                if first["type"] in {"heading", "paragraph"}:
                    blocks.pop(0)
        if len(blocks) >= 2:
            first, second = blocks[0], blocks[1]
            if (
                first["type"] == "heading"
                and re.fullmatch(r"\d+(?:\.\d+)*", first["text"].strip())
                and second["type"] == "heading"
            ):
                blocks.pop(0)

        # â”€â”€ Interleave figures with blocks by (page_index, y0) â”€â”€
        stream: list[dict] = []
        for block in blocks:
            stream.append({"kind": "block", "block": block, "page_index": block.get("page_index", 0), "y0": block.get("y0", 0.0)})
        for f in chapter_figures:
            stream.append({"kind": "figure", "figure": f, "page_index": f.page_index, "y0": f.y_position})
        for table in chapter_tables:
            stream.append({"kind": "table", "table": table, "page_index": table.page_index, "y0": table.y_position})
        stream.sort(key=lambda item: (item["page_index"], item["y0"]))
        _append_figure_neighborhood_samples(
            figure_neighborhood_samples,
            stream,
            chapter_title=draft.title,
            max_samples=16,
        )

        html_parts: list[str] = []
        # NOTE: converter.build_epub auto-inserts the chapter title as <h1>,
        # so we never emit it here.

        for item in stream:
            if item["kind"] == "figure":
                f = item["figure"]
                figure_class = "figure premium-figure technical-figure" if f.caption else "figure premium-figure illustration"
                figure_html = [
                    f'<figure class="{figure_class}">',
                    f'<img src="images/{f.filename}" alt="{html_module.escape(f.caption or "")}"/>',
                ]
                if f.caption:
                    figure_html.append(f"<figcaption>{html_module.escape(f.caption)}</figcaption>")
                figure_html.append("</figure>")
                html_parts.append("".join(figure_html))
                continue
            if item["kind"] == "table":
                html_parts.append(item["table"].html)
                continue
            block = item["block"]
            btype = block["type"]
            if btype == "heading":
                if document_like_report and not _HEADING_NUM_RE.match(block["text"].strip()):
                    html_parts.append(f'<p class="report-label"><strong>{html_module.escape(block["text"])}</strong></p>')
                else:
                    lvl = min(4, max(3, block["level"] + 2))  # chapter title is emitted by build_epub
                    html_parts.append(f"<h{lvl}>{html_module.escape(block['text'])}</h{lvl}>")
            elif btype == "list-item":
                html_parts.append(f"<li>{block['html'] or html_module.escape(block['text'])}</li>")
            else:
                content = block.get("html") or html_module.escape(block["text"])
                html_parts.append(f"<p>{content}</p>")

        # Wrap contiguous list items in <ul> (post-process)
        wrapped: list[str] = []
        in_list = False
        for part in html_parts:
            if part.startswith("<li>"):
                if not in_list:
                    wrapped.append("<ul>")
                    in_list = True
                wrapped.append(part)
            else:
                if in_list:
                    wrapped.append("</ul>")
                    in_list = False
                wrapped.append(part)
        if in_list:
            wrapped.append("</ul>")

        reading_flow_samples.append(
            {
                "title": draft.title,
                "page_start": draft.page_start + 1,
                "page_end": draft.page_end + 1,
                "text_char_count": sum(len(block.get("text", "")) for block in blocks),
                "figure_count": len(chapter_figures),
                "rendered_table_count": len(chapter_tables),
                "heading_count": sum(1 for block in blocks if block.get("type") == "heading"),
                "status": "sampled",
            }
        )

        # IMPORTANT: leave chapter images [] so build_epub doesn't
        # re-emit figures at the end of the chapter. Image bytes are
        # still registered via the global `images` list below.
        chapters.append(
            {
                "title": draft.title,
                "html_parts": wrapped,
                "images": [],
                "_source_page_label": str(draft.page_start + 1),
                "_page_start": draft.page_start,
                "_page_end": draft.page_end,
            }
        )

        if not cover_assigned and chapter_figures:
            cover_assigned = True

    doc.close()

    table_summary = _table_summary_payload(page_tables)
    reading_order_summary = _reading_order_summary(page_lines, page_widths)

    return {
        "success": True,
        "method": "premium-reflow",
        "text_content": True,
        "layout_mode": "reflowable",
        "chapters": chapters,
        "images": all_images,
        "toc": toc,
        "metadata": {
            **(pdf_metadata or {}),
            "source_table_count": table_summary["source_table_count"],
            "xhtml_table_count": table_summary["xhtml_table_count"],
            "table_summary": table_summary,
            "figure_summary": {
                "figure_count": len(all_images),
                "sampled_figure_neighborhoods": figure_neighborhood_samples[:16],
            },
            "reading_flow": {
                **reading_order_summary,
                "sampled_sections": _select_reading_flow_samples(reading_flow_samples),
            },
            "document_like_report": document_like_report,
        },
    }


def _append_figure_neighborhood_samples(
    samples: list[dict[str, Any]],
    stream: list[dict],
    *,
    chapter_title: str,
    max_samples: int,
) -> None:
    if len(samples) >= max_samples:
        return
    for index, item in enumerate(stream):
        if item.get("kind") != "figure":
            continue
        figure = item.get("figure")
        if not isinstance(figure, Figure):
            continue
        previous_text = _nearest_stream_text(stream, index, direction=-1)
        next_text = _nearest_stream_text(stream, index, direction=1)
        samples.append(
            {
                "chapter": chapter_title,
                "page": figure.page_index + 1,
                "caption": figure.caption,
                "asset": figure.filename,
                "source": figure.source,
                "preceding_text": previous_text,
                "following_text": next_text,
                "status": "sampled" if figure.caption and previous_text and next_text else "review",
            }
        )
        if len(samples) >= max_samples:
            return


def _nearest_stream_text(stream: list[dict], index: int, *, direction: int) -> str:
    cursor = index + direction
    while 0 <= cursor < len(stream):
        item = stream[cursor]
        if item.get("kind") == "block":
            text = re.sub(r"\s+", " ", str((item.get("block") or {}).get("text", ""))).strip()
            if text:
                return text[:240]
        cursor += direction
    return ""


def _select_reading_flow_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(samples) <= 12:
        return samples
    selected: list[dict[str, Any]] = []
    wanted_keywords = ("introduction", "techniques", "glossary", "appendix", "perspectives")
    for sample in samples:
        title = str(sample.get("title", "")).lower()
        if any(keyword in title for keyword in wanted_keywords):
            selected.append(sample)
    for sample in samples:
        if sample not in selected:
            selected.append(sample)
        if len(selected) >= 12:
            break
    return selected[:12]


def _reading_order_summary(page_lines: dict[int, list[TextLine]], page_widths: dict[int, float]) -> dict[str, Any]:
    page_reports: list[dict[str, Any]] = []
    low_confidence_pages: list[int] = []
    for page_index, lines in sorted(page_lines.items()):
        page_width = float(page_widths.get(page_index, 0.0) or 0.0)
        stats = _two_column_stats(lines, page_width=page_width)
        if not stats["is_two_column"]:
            continue
        assigned_count = int(stats["left_count"]) + int(stats["right_count"])
        ambiguous_count = int(stats["ambiguous_count"])
        confidence = 0.94
        if assigned_count:
            confidence -= min(0.35, ambiguous_count / max(assigned_count + ambiguous_count, 1))
        confidence = max(0.45, min(0.98, confidence))
        page_report = {
            "page": page_index + 1,
            "status": "passed" if confidence >= 0.75 else "review",
            "confidence": round(confidence, 3),
            "left_line_count": int(stats["left_count"]),
            "right_line_count": int(stats["right_count"]),
            "ambiguous_line_count": ambiguous_count,
            "column_gap": stats["gap"],
        }
        page_reports.append(page_report)
        if confidence < 0.75:
            low_confidence_pages.append(page_index + 1)

    if low_confidence_pages:
        status = "passed_with_warnings"
        message = "Multi-column reading order needs manual review."
    else:
        status = "passed"
        message = "Reading order passed heuristic checks."

    return {
        "status": status,
        "quality_gate_status": status,
        "confidence": round(
            min((float(page.get("confidence", 1.0)) for page in page_reports), default=1.0),
            3,
        ),
        "estimated_multi_column_pages": len(page_reports),
        "low_confidence_region_count": len(low_confidence_pages),
        "manual_review_count": len(low_confidence_pages),
        "message": message,
        "pages": page_reports[:24],
    }
