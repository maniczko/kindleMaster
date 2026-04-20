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
from typing import Optional

import fitz  # PyMuPDF
from toc_segmentation import normalize_toc_entries, select_section_outline_entries

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
    source_page_label: Optional[str] = None
    lines: list[TextLine] = field(default_factory=list)
    figures: list[Figure] = field(default_factory=list)


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
    # Reading order: top to bottom, then left to right
    lines.sort(key=lambda ln: (round(ln.y0, 1), ln.x0))
    return lines


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
            pix = page.get_pixmap(clip=padded, dpi=180, alpha=False)
            png = pix.tobytes("png")
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
                y_position=rect.y0,
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
_HEADING_NUM_RE = re.compile(r"^(\d+(?:\.\d+){0,4})(\s+.+)?$")
_BULLET_RE = re.compile(r"^\s*[â€˘\u2022\u2023\u25E6\u00b7\u2219]\s*")
_FIGURE_CAPTION_RE = re.compile(
    r"^(Figure|Table|Diagram|Chart|Illustration|Exhibit)\s+[A-Za-z0-9][A-Za-z0-9.\-]*(?::|\b)",
    re.IGNORECASE,
)


def _find_caption_lines(lines: list[TextLine]) -> list[TextLine]:
    return [line for line in lines if _FIGURE_CAPTION_RE.match(line.text.strip())]


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
) -> list[Figure]:
    figures: list[Figure] = []
    captions = _find_caption_lines(lines)
    if not captions:
        return figures

    page_rect = page.rect
    for index, caption in enumerate(captions):
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

        try:
            pix = page.get_pixmap(clip=clip, dpi=190, alpha=False)
            image_bytes = pix.tobytes("png")
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
                caption_key=(round(caption.y0, 1), label),
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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Chapter splitting from PDF TOC
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _build_chapter_drafts(doc, toc: list) -> list[ChapterDraft]:
    """Split the document into chapters by top-level TOC entries."""
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
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_line_items = _filter_noise_lines(
            _extract_lines_from_page(page, page_num),
            page_rect=page.rect,
        )
        figures = _extract_captioned_region_figures(
            page,
            page_num,
            img_counter,
            page_line_items,
            body_size=body_size,
        )
        figures.extend(
            _extract_raster_figures(
                page,
                doc,
                page_num,
                img_counter,
                page_line_items,
                used_regions=[fitz.Rect(*figure.bbox) for figure in figures if figure.bbox != (0, 0, 0, 0)],
            )
        )
        figures.sort(key=lambda f: f.y_position)
        page_lines[page_num] = _filter_lines_for_figures(page_line_items, figures)
        page_figures[page_num] = figures

    # â”€â”€ Split into chapters via TOC â”€â”€
    drafts = _build_chapter_drafts(doc, toc)

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
    for ci, draft in enumerate(drafts):
        if draft.title.strip().lower() == "table of contents":
            continue
        # Collect lines in page range
        lines: list[TextLine] = []
        chapter_figures: list[Figure] = []
        for p in range(draft.page_start, draft.page_end + 1):
            lines.extend(page_lines.get(p, []))
            chapter_figures.extend(page_figures.get(p, []))

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
        stream.sort(key=lambda item: (item["page_index"], item["y0"]))

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
            block = item["block"]
            btype = block["type"]
            if btype == "heading":
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

    return {
        "success": True,
        "method": "premium-reflow",
        "text_content": True,
        "layout_mode": "reflowable",
        "chapters": chapters,
        "images": all_images,
        "toc": toc,
    }

