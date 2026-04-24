"""
KindleMaster — PyMuPDF Extraction with Chess Diagram Support
=============================================================
Extracts content from PDF using PyMuPDF while properly handling chess diagrams.

Chess diagrams in PDFs often use special fonts (Chess-Merida, etc.) with PUA 
(Private Use Area) characters that don't render in EPUB. This module detects 
those and renders them as images instead.
"""

import io
import html as html_module
import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image

# Import chess renderer
try:
    from chess_diagram_renderer import (
        find_chess_diagram_regions,
        render_chess_diagram_to_png,
        is_chess_text,
    )
    CHESS_RENDERER_AVAILABLE = True
except ImportError:
    CHESS_RENDERER_AVAILABLE = False

from converter import (
    ConversionConfig,
    _extract_pdf_metadata,
    detect_pdf_type,
    build_epub,
    strip_emails,
)

WINGDINGS_TICK = "\uf0fc"
UNICODE_TICK = "✓"
PARAGRAPH_TEXT_RE = re.compile(r"^<p>(.*)</p>$", re.DOTALL)
FIGURINE_FONT_TOKENS = ("sptimefig", "spariesfig")
FIGURINE_TEXT_MAP = {
    "\xa2": "K",
    "\xa3": "Q",
    "\xa4": "R",
    "\xa5": "B",
    "\xa6": "N",
    "\xa9": " with compensation",
    "\xb1": " \u00b1",
    "\xb2": " +=",
    "\xb3": " =+",
    "\xb5": " \u2213",
    "\xf7": " unclear",
    "\u201e": " with counterplay",
}
BOARD_FILE_COORD_RE = re.compile(r"^[a-h](?:\s+[a-h]){0,7}$", re.IGNORECASE)
BOARD_RANK_COORD_RE = re.compile(r"^[1-8](?:\s+[1-8]){0,7}$")
BOARD_MARKER_RE = re.compile(r"^(?:#|mate|checkmate|[\u25b2-\u25ff\u2206\u25b3])$", re.IGNORECASE)
WINGDINGS_BOARD_MARKERS = {"\uf071", "\uf072", "\uf073", "\uf074"}
NO_SPACE_BEFORE_TOKENS = tuple(",.;:!?)]}/%")
NO_SPACE_AFTER_TOKENS = tuple("([/{")
NO_SPACE_BEFORE_SAN_START = tuple("abcdefghKQRBNO0x+#=†‡")
NOTATION_TOKEN_RE = re.compile(
    r"\b(?:\d+\.(?:\.\.)?|O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#†‡]?|[a-h]x?[a-h]?[1-8](?:=[QRBN])?[+#†‡]?|1-0|0-1|1/2-1/2)\b"
)
INLINE_EVAL_RE = re.compile(r"(\+=|=\+|\u00b1|\u2213|\+\u2013|\u2013\+)(?=[A-Za-z(])")
GAME_CAPTION_RE = re.compile(r"^[A-Z].{2,}\s[â€“-]\s.+(?:19|20)\d{2}$")


GAME_CAPTION_RE = re.compile(r"^[A-Z].{2,}\s[\u2013-]\s.+(?:18|19|20)\d{2}$")


@dataclass
class TextSpanWithIndex:
    """Text span with its index for tracking."""
    index: int
    page_num: int
    text: str
    x: float
    y: float
    width: float
    height: float
    font_name: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color: Optional[int]
    bbox: tuple
    css_font_family: str
    css_color: str


@dataclass
class TextLineItem:
    """Normalized text reconstructed from all spans that share one PDF line."""

    start_index: int
    text: str
    font_size: float
    y: float


def _bbox_is_inside(inner: tuple, outer: tuple, margin: float = 1.5) -> bool:
    """Return True when inner bbox sits inside outer bbox with a small tolerance."""
    ix0, iy0, ix1, iy1 = inner
    ox0, oy0, ox1, oy1 = outer
    return (
        ix0 >= ox0 - margin
        and iy0 >= oy0 - margin
        and ix1 <= ox1 + margin
        and iy1 <= oy1 + margin
    )


def _looks_like_board_auxiliary_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return False
    if len(normalized) == 1 and normalized in WINGDINGS_BOARD_MARKERS:
        return True
    if BOARD_FILE_COORD_RE.fullmatch(normalized):
        return True
    if BOARD_RANK_COORD_RE.fullmatch(normalized):
        return True
    if re.fullmatch(r"[a-h1-8]", normalized, re.IGNORECASE):
        return True
    if len(normalized) <= 12 and BOARD_MARKER_RE.fullmatch(normalized):
        return True
    return False


def _expand_chess_region_for_auxiliary_labels(
    region,
    text_spans: list[TextSpanWithIndex],
    page_rect,
) -> tuple[tuple, set[int]]:
    """Expand board crop to include nearby ranks/files and suppress them from flowing text."""
    x0, y0, x1, y1 = region.bbox
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    margin_x = max(12.0, width * 0.10)
    margin_y = max(12.0, height * 0.10)

    expanded = [x0, y0, x1, y1]
    suppressed_indices = set(region.text_span_indices)

    for ts in text_spans:
        tx0, ty0, tx1, ty1 = ts.bbox
        if tx1 < x0 - margin_x or tx0 > x1 + margin_x:
            continue
        if ty1 < y0 - margin_y or ty0 > y1 + margin_y:
            continue

        normalized = re.sub(r"\s+", " ", (ts.text or "").strip())
        if not normalized:
            continue

        # Only widen the crop for true board auxiliaries like ranks/files or
        # small markers. Pulling in arbitrary chess-font spans here swallows
        # neighboring boards on dense multi-column exercise pages.
        if _looks_like_board_auxiliary_text(normalized):
            suppressed_indices.add(ts.index)
            expanded[0] = min(expanded[0], tx0)
            expanded[1] = min(expanded[1], ty0)
            expanded[2] = max(expanded[2], tx1)
            expanded[3] = max(expanded[3], ty1)

    expanded_bbox = (
        max(page_rect.x0, expanded[0] - 2.0),
        max(page_rect.y0, expanded[1] - 2.0),
        min(page_rect.x1, expanded[2] + 2.0),
        min(page_rect.y1, expanded[3] + 2.0),
    )
    return expanded_bbox, suppressed_indices


def _resize_image_to_long_edge(image: Image.Image, max_long_edge: int) -> Image.Image:
    target_long_edge = max(1, int(max_long_edge or 0))
    if target_long_edge <= 0:
        return image
    current_long_edge = max(image.size)
    if current_long_edge <= target_long_edge:
        return image
    scale = target_long_edge / float(current_long_edge)
    resized = image.resize(
        (
            max(1, int(round(image.width * scale))),
            max(1, int(round(image.height * scale))),
        ),
        Image.LANCZOS,
    )
    return resized


def _optimize_chess_diagram_export(
    png_data: bytes,
    config: ConversionConfig,
) -> tuple[bytes, int, int]:
    image = Image.open(io.BytesIO(png_data)).convert("L")
    image = _resize_image_to_long_edge(image, config.diagram_image_long_edge)
    target_palette_size = max(4, min(int(config.diagram_palette_colors or 0), 64))
    quantized = image.quantize(
        colors=target_palette_size,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    used_colors = quantized.getcolors(maxcolors=target_palette_size)
    used_color_count = len(used_colors) if used_colors else target_palette_size
    png_bits = 8
    if used_color_count <= 2:
        png_bits = 1
    elif used_color_count <= 4:
        png_bits = 2
    elif used_color_count <= 16:
        png_bits = 4
    output = io.BytesIO()
    quantized.save(
        output,
        format="PNG",
        optimize=True,
        compress_level=9,
        bits=png_bits,
    )
    optimized = output.getvalue()
    return optimized, quantized.width, quantized.height


def _normalize_image_extension(raw_extension: str) -> str:
    normalized = str(raw_extension or "png").strip().lower()
    if normalized == "jpg":
        return "jpeg"
    return normalized or "png"


def _optimize_embedded_raster_image(
    image_bytes: bytes,
    extension: str,
    config: ConversionConfig,
) -> tuple[bytes, str]:
    normalized_ext = _normalize_image_extension(extension)
    try:
        image = Image.open(io.BytesIO(image_bytes))
        has_alpha = "A" in image.getbands()
        image = _resize_image_to_long_edge(image, config.diagram_raster_long_edge)
        palette_candidate = image.convert("RGBA" if has_alpha else "RGB")
        colors = palette_candidate.getcolors(maxcolors=128)

        if has_alpha or (normalized_ext == "png" and colors and len(colors) <= 64):
            png_image = palette_candidate
            if has_alpha:
                png_image = png_image.quantize(colors=128)
            else:
                png_image = png_image.convert("P", palette=Image.ADAPTIVE, colors=min(64, len(colors)))
            output = io.BytesIO()
            png_image.save(output, format="PNG", optimize=True, compress_level=9)
            return output.getvalue(), "png"

        jpeg_image = palette_candidate.convert("RGB")
        output = io.BytesIO()
        jpeg_image.save(
            output,
            format="JPEG",
            quality=max(60, min(int(config.diagram_raster_jpeg_quality or 0), 90)),
            optimize=True,
            progressive=True,
        )
        return output.getvalue(), "jpeg"
    except Exception as exc:
        print(f"    Warning: Could not optimize embedded raster image: {exc}")
        return image_bytes, normalized_ext


def _normalize_text_for_epub(text: str, font_name: str) -> str:
    """Replace Wingdings-only markers with readable Unicode."""
    normalized = text or ""
    san_token = r"(?:O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)"
    eval_token = r"(?:\+\u2013|\u2013\+|\+=|=\+|\u00b1|\u2213)"
    font_lower = (font_name or "").lower()
    if any(token in font_lower for token in FIGURINE_FONT_TOKENS):
        normalized = "".join(FIGURINE_TEXT_MAP.get(char, char) for char in normalized)
    if font_name == "Wingdings-Regular":
        normalized = normalized.replace(WINGDINGS_TICK, UNICODE_TICK)
    normalized = normalized.replace("â€ ", "\u2020").replace("â€ˇ", "\u2021").replace("âś“", "\u2713")
    normalized = normalized.replace("â€“", "\u2013").replace("â€”", "\u2014")
    normalized = normalized.replace("â€˜", "\u2018").replace("â€™", "\u2019")
    normalized = normalized.replace("â€œ", "\u201c").replace("â€", "\u201d")
    normalized = normalized.replace("â“", "\u2213").replace("Â½", "\u00bd").replace("Â˝", "\u00bd").replace("Â±", "\u00b1")
    normalized = normalized.replace("\r", "").replace("\n", " ")
    normalized = normalized.replace("+/-", "\u00b1")
    normalized = normalized.replace("-/+", "\u2213")
    normalized = normalized.replace("\u2020", "+").replace("\u2021", "+").replace("\u2713", " ")
    normalized = normalized.replace("1–0", "1-0").replace("0–1", "0-1").replace("½–½", "½-½")
    normalized = re.sub(r"=\s+([QRBN])\b", r"=\1", normalized)
    normalized = re.sub(rf"({san_token})\s+mate\b(?!\s+in\b)", r"\1#", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"(?<=[KQRBNa-h0-9])\s+\+(?=[!?]|\s|$)", "+", normalized)
    normalized = re.sub(r"(?<=[KQRBNa-h0-9])\s+#(?=[!?]|\s|$)", "#", normalized)
    normalized = re.sub(rf"(?<=[A-Za-z0-9])([+#])\s*({eval_token})", r"\1 (\2)", normalized)
    normalized = re.sub(r"\bwith\s+#(?=\s|[.,;:!?]|$)", "with mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bavoid\s+#(?=\s|[.,;:!?]|$)", "avoid mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bback-rank\s+#(?=\s|[.,;:!?]|$)", "back-rank mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bsmothered\s+#(?=\s|[.,;:!?]|$)", "smothered mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bof\s+#(?=\s|[.,;:!?]|$)", "of mate", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bthe\s+#(?=\s|[.,;:!?]|$)", "the mate", normalized, flags=re.IGNORECASE)
    normalized = INLINE_EVAL_RE.sub(r"\1 ", normalized)
    normalized = re.sub(r"(?<=[0-9])(?=[A-Z][a-z])", " ", normalized)
    normalized = re.sub(r"(?<=[0-9])(?=mate\b)", " ", normalized)
    normalized = re.sub(r"\+\s+([!?])", r"+\1", normalized)
    normalized = re.sub(r"#\s+([!?])", r"#\1", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    return normalized


def _should_insert_space(previous_text: str, current_text: str, gap: float, font_size: float) -> bool:
    if not previous_text or not current_text:
        return False

    prev_char = previous_text[-1]
    next_char = current_text[0]

    if prev_char.isspace() or next_char.isspace():
        return False
    if next_char in NO_SPACE_BEFORE_TOKENS:
        return False
    if prev_char in NO_SPACE_AFTER_TOKENS:
        return False
    if prev_char in "KQRBN" and next_char in NO_SPACE_BEFORE_SAN_START:
        return False
    if gap > 0.1 and (next_char.isupper() or next_char.isdigit()):
        return True

    gap_threshold = max(0.6, font_size * 0.08)
    return gap >= gap_threshold


def _is_notation_heavy_line(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return False
    token_count = len(NOTATION_TOKEN_RE.findall(normalized))
    if token_count >= 3:
        return True
    if not re.match(r"^\d+\.(?:\.\.)?\s*\S+", normalized):
        return False
    return any(marker in normalized for marker in ("x", "+", "#", "†", "‡", "O-O", "="))


def _build_line_items(raw_lines: list[dict], skipped_indices: set[int]) -> list[TextLineItem]:
    items: list[TextLineItem] = []

    for raw_line in raw_lines:
        segments = [segment for segment in raw_line["segments"] if segment["index"] not in skipped_indices]
        if not segments:
            continue

        segments.sort(key=lambda segment: (segment["x0"], segment["index"]))
        line_text = ""
        prev_x1 = None
        start_index = None
        max_font_size = 0.0

        for segment in segments:
            font_lower = (segment["font_name"] or "").lower()
            is_figurine_segment = any(token in font_lower for token in FIGURINE_FONT_TOKENS)
            piece = _normalize_text_for_epub(strip_emails(segment["text"]), segment["font_name"]).strip()
            if not piece:
                continue

            if start_index is None:
                start_index = segment["index"]
            max_font_size = max(max_font_size, segment["font_size"])

            gap = (segment["x0"] - prev_x1) if prev_x1 is not None else 0.0
            if line_text:
                if (
                    is_figurine_segment
                    or piece[0].isdigit()
                    or (piece[0].isupper() and piece[0] not in "KQRBN")
                ):
                    if line_text[-1] not in NO_SPACE_AFTER_TOKENS and not line_text.endswith(" "):
                        line_text += " "
                elif _should_insert_space(line_text, piece, gap, segment["font_size"]):
                    line_text += " "
            line_text += piece
            prev_x1 = segment["x1"]

        line_text = re.sub(r"[ \t]{2,}", " ", line_text).strip()
        line_text = re.sub(r"([†‡✓])(?=[A-Z0-9])", r"\1 ", line_text)
        line_text = re.sub(r"(mate)(?=[A-Z0-9])", r"\1 ", line_text)
        line_text = re.sub(r"([=+\-/\u00b1\u2213]{1,3})(?=✓)", r"\1 ", line_text)
        line_text = INLINE_EVAL_RE.sub(r"\1 ", line_text)
        line_text = re.sub(r"(?<=[0-9])(?=[A-Z][a-z])", " ", line_text)
        if not line_text or start_index is None:
            continue

        items.append(TextLineItem(
            start_index=start_index,
            text=line_text,
            font_size=max_font_size,
            y=min(float(segment.get("y0", 0.0)) for segment in segments),
        ))

    return items


def _extract_paragraph_text(html_fragment: str) -> Optional[str]:
    match = PARAGRAPH_TEXT_RE.match((html_fragment or "").strip())
    if not match:
        return None
    return html_module.unescape(match.group(1)).strip()


def _is_number_only(text: str) -> bool:
    cleaned = (text or "").strip()
    return cleaned.isdigit() and 1 <= len(cleaned) <= 4


def _join_caption_parts(parts: list[str]) -> str:
    caption = ""
    for part in parts:
        piece = (part or "").strip()
        if not piece:
            continue
        if not caption:
            caption = piece
            continue
        if piece[0] in ",.;:)]":
            caption += piece
        else:
            caption += f" {piece}"
    return caption.strip()


def _looks_like_caption_parts(parts: list[str]) -> bool:
    filtered = [part.strip() for part in parts if part and part.strip()]
    if not filtered:
        return False
    total_length = sum(len(part) for part in filtered)
    return len(filtered) <= 3 and total_length <= 120


def _wrap_chess_problem(diagram_html: str, caption_text: str, exercise_number: str) -> str:
    caption_core = html_module.escape(caption_text)
    if exercise_number:
        number_html = f'<span class="exercise-number">{html_module.escape(exercise_number)}.</span> '
    else:
        number_html = ""
    return (
        '<div class="chess-problem">'
        f'<p class="diagram-caption">{number_html}{caption_core}</p>'
        f"{diagram_html}</div>"
    )


def _detect_page_label_from_spans(
    text_spans: list[TextSpanWithIndex],
    page_width: float,
    page_height: float,
) -> Optional[str]:
    """Find the printed book page number using page geometry instead of HTML order."""
    candidates: list[tuple[float, str]] = []

    for ts in text_spans:
        text = (ts.text or "").strip()
        if not _is_number_only(text):
            continue

        center_x = ts.x + (ts.width / 2)
        near_footer = ts.y >= page_height * 0.84
        near_header = ts.y <= page_height * 0.12
        near_center = page_width * 0.34 <= center_x <= page_width * 0.66
        near_outer_margin = center_x <= page_width * 0.16 or center_x >= page_width * 0.84

        if not (near_footer or near_header):
            continue

        if not (near_center or near_outer_margin):
            continue

        edge_distance = (page_height - ts.y) if near_footer else ts.y
        center_distance = abs(center_x - (page_width / 2))
        side_distance = min(center_x, page_width - center_x)
        horizontal_distance = min(center_distance, side_distance)
        score = edge_distance + (horizontal_distance * 0.35)
        candidates.append((score, text))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _merge_chess_problem_fragments(html_parts: list[str]) -> list[str]:
    """Attach exercise numbers and split captions to the next chess diagram."""
    diagram_count = sum("chess-diagram-container" in part for part in html_parts)
    if diagram_count == 0:
        return html_parts

    row_grouped = _reconstruct_row_grouped_diagrams(html_parts)
    if row_grouped is not None:
        return row_grouped

    reconstructed = _reconstruct_numbered_diagram_grid(html_parts, diagram_count=diagram_count)
    if reconstructed is not None:
        return reconstructed

    leading_numbers = []
    index = 0
    while index < len(html_parts):
        text = _extract_paragraph_text(html_parts[index])
        if text is None or not _is_number_only(text):
            break
        leading_numbers.append(text)
        index += 1

    consumed_leading = 0
    exercise_numbers: list[str] = []
    if len(leading_numbers) >= diagram_count + 1:
        exercise_numbers = leading_numbers[1:diagram_count + 1]
        consumed_leading = diagram_count + 1
    elif len(leading_numbers) >= diagram_count:
        exercise_numbers = leading_numbers[:diagram_count]
        consumed_leading = diagram_count

    merged_parts = []
    pending_caption_parts: list[str] = []
    diagram_index = 0

    for part_index, part in enumerate(html_parts):
        if part_index < consumed_leading:
            continue

        if "chess-diagram-container" in part:
            caption_candidates = pending_caption_parts[:]
            pending_caption_parts = []
            exercise_number = exercise_numbers[diagram_index] if diagram_index < len(exercise_numbers) else ""
            if _looks_like_caption_parts(caption_candidates):
                caption_text = _join_caption_parts(caption_candidates)
                merged_parts.append(_wrap_chess_problem(part, caption_text, exercise_number))
            else:
                for pending_text in caption_candidates:
                    merged_parts.append(f"<p>{html_module.escape(pending_text)}</p>")
                merged_parts.append(part)
            diagram_index += 1
            continue

        text = _extract_paragraph_text(part)
        if text is not None:
            pending_caption_parts.append(text)
            continue

        if pending_caption_parts:
            for pending_text in pending_caption_parts:
                merged_parts.append(f"<p>{html_module.escape(pending_text)}</p>")
            pending_caption_parts = []

        merged_parts.append(part)

    if pending_caption_parts:
        css_class = "diagram-tail" if diagram_index > 0 else ""
        for pending_text in pending_caption_parts:
            class_attr = f' class="{css_class}"' if css_class else ""
            merged_parts.append(f"<p{class_attr}>{html_module.escape(pending_text)}</p>")

    return merged_parts


def _looks_like_game_caption(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    return bool(GAME_CAPTION_RE.match(normalized))


def _add_paragraph_class(fragment: str, css_class: str) -> str:
    stripped = (fragment or "").strip()
    if not stripped.startswith("<p") or 'class="' in stripped:
        return fragment
    return stripped.replace("<p", f'<p class="{css_class}"', 1)


def _reconstruct_numbered_diagram_grid(html_parts: list[str], *, diagram_count: int) -> Optional[list[str]]:
    diagram_positions = [index for index, part in enumerate(html_parts) if "chess-diagram-container" in part]
    if not diagram_positions:
        return None

    first_diagram = diagram_positions[0]
    last_diagram = diagram_positions[-1]

    leading_numbers: list[str] = []
    prefix_index = 0
    while prefix_index < first_diagram:
        text = _extract_paragraph_text(html_parts[prefix_index])
        if text is None or not _is_number_only(text):
            break
        leading_numbers.append(text)
        prefix_index += 1

    if prefix_index != first_diagram or len(leading_numbers) < diagram_count:
        return None

    page_label_count = len(leading_numbers) - diagram_count
    if page_label_count < 0 or page_label_count > 1:
        return None

    exercise_numbers = leading_numbers[page_label_count:page_label_count + diagram_count]
    if len(exercise_numbers) != diagram_count:
        return None

    trailing_fragments = html_parts[last_diagram + 1:]
    trailing_captions: list[str] = []
    consumed_trailing = 0
    for fragment in trailing_fragments:
        text = _extract_paragraph_text(fragment)
        if text is None or not _looks_like_game_caption(text):
            break
        trailing_captions.append(text)
        consumed_trailing += 1

    if len(trailing_captions) != diagram_count:
        return None

    reconstructed: list[str] = []
    reconstructed.extend(html_parts[:page_label_count])

    for diagram_html, exercise_number, caption_text in zip(
        (html_parts[index] for index in diagram_positions),
        exercise_numbers,
        trailing_captions,
    ):
        reconstructed.append(_wrap_chess_problem(diagram_html, caption_text, exercise_number))

    for fragment in trailing_fragments[consumed_trailing:]:
        text = _extract_paragraph_text(fragment)
        if text is not None:
            reconstructed.append(_add_paragraph_class(fragment, "diagram-tail"))
        else:
            reconstructed.append(fragment)

    return reconstructed


def _reconstruct_row_grouped_diagrams(html_parts: list[str]) -> Optional[list[str]]:
    cursor = 0
    prefix: list[str] = []
    tasks: list[tuple[int, str]] = []

    while cursor < len(html_parts) and "chess-diagram-container" not in html_parts[cursor]:
        prefix.append(html_parts[cursor])
        cursor += 1

    if cursor >= len(html_parts):
        return None

    while cursor < len(html_parts):
        if "chess-diagram-container" not in html_parts[cursor]:
            break

        diagrams: list[str] = []
        while cursor < len(html_parts) and "chess-diagram-container" in html_parts[cursor]:
            diagrams.append(html_parts[cursor])
            cursor += 1

        captions: list[str] = []
        while cursor < len(html_parts):
            text = _extract_paragraph_text(html_parts[cursor])
            if text is None or not _looks_like_game_caption(text):
                break
            captions.append(text)
            cursor += 1

        numbers: list[str] = []
        while cursor < len(html_parts):
            text = _extract_paragraph_text(html_parts[cursor])
            if text is None or not _is_number_only(text):
                break
            numbers.append(text)
            cursor += 1

        if len(diagrams) == len(captions) == len(numbers):
            for diagram_html, caption_text, exercise_num in zip(diagrams, captions, numbers):
                tasks.append((int(exercise_num), _wrap_chess_problem(diagram_html, caption_text, exercise_num)))
            continue

        return None

    if not tasks:
        return None

    reconstructed = list(prefix)
    reconstructed.extend(fragment for _, fragment in sorted(tasks, key=lambda item: item[0]))

    for fragment in html_parts[cursor:]:
        text = _extract_paragraph_text(fragment)
        if text is not None:
            reconstructed.append(_add_paragraph_class(fragment, "diagram-tail"))
        else:
            reconstructed.append(fragment)

    return reconstructed


def extract_pdf_with_chess_support(
    pdf_path: str,
    config: ConversionConfig,
    pdf_metadata: dict = None,
) -> dict:
    """
    Extract PDF content with special handling for chess diagrams.
    
    Chess diagrams are rendered as PNG images instead of text to avoid
    the empty squares problem with PUA characters.
    """
    if pdf_metadata is None:
        pdf_metadata = _extract_pdf_metadata(pdf_path)
    
    doc = fitz.open(pdf_path)
    
    chapters = []
    all_images = []
    all_chess_diagrams = []
    image_count = 0
    chess_diagram_count = 0
    toc = doc.get_toc()
    
    # Analyze font sizes for heading detection
    all_font_sizes = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        all_font_sizes.append(span["size"])
    
    # Calculate heading thresholds
    if all_font_sizes:
        body_size = max(set(all_font_sizes), key=all_font_sizes.count)
    else:
        body_size = 12
    
    h1_threshold = body_size * 1.5
    h2_threshold = body_size * 1.3
    h3_threshold = body_size * 1.1
    
    # Process each page
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_width = page.rect.width
        page_height = page.rect.height
        
        # Get text blocks with full detail
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
        
        # Collect all text spans and keep raw per-line segments so we can
        # reconstruct readable notation instead of emitting one paragraph per span.
        text_spans = []
        raw_lines = []
        span_index = 0
        
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            
            for line in block.get("lines", []):
                raw_line_segments = []
                for span in line.get("spans", []):
                    raw_text = span.get("text", "")
                    text = raw_text.strip()
                    if not text:
                        span_index += 1
                        continue
                    
                    bbox = span.get("bbox", (0, 0, 0, 0))
                    x0, y0, x1, y1 = bbox
                    
                    # Determine CSS font family
                    font_name = span.get("font", "Unknown")
                    css_family = _get_css_font_family(font_name)
                    css_color = _color_to_css(span.get("color"))
                    
                    text_spans.append(TextSpanWithIndex(
                        index=span_index,
                        page_num=page_num,
                        text=text,
                        x=x0,
                        y=y0,
                        width=x1 - x0,
                        height=y1 - y0,
                        font_name=font_name,
                        font_size=span.get("size", 12),
                        is_bold=bool(span.get("flags", 0) & (1 << 4)),
                        is_italic=bool(span.get("flags", 0) & (1 << 1)),
                        color=span.get("color"),
                        bbox=bbox,
                        css_font_family=css_family,
                        css_color=css_color,
                    ))
                    raw_line_segments.append({
                        "index": span_index,
                        "text": raw_text,
                        "font_name": font_name,
                        "font_size": span.get("size", 12),
                        "x0": x0,
                        "x1": x1,
                        "y0": y0,
                    })
                    span_index += 1
                if raw_line_segments:
                    raw_lines.append({"segments": raw_line_segments})
        
        # Find chess diagram regions (if renderer available)
        chess_diagram_regions = []
        chess_text_indices = set()
        diagram_entries = []
        chess_imgs_for_page = []
        
        if CHESS_RENDERER_AVAILABLE and text_spans:
            # Pass text_spans directly - they have all needed attributes (text, font_name, x, y, width, height)
            chess_diagram_regions = find_chess_diagram_regions(text_spans)
            
            if chess_diagram_regions:
                print(f"    Page {page_num + 1}: Found {len(chess_diagram_regions)} chess diagram(s)")
                
                for region_idx, region in enumerate(chess_diagram_regions):
                    expanded_bbox, suppressed_indices = _expand_chess_region_for_auxiliary_labels(
                        region,
                        text_spans,
                        page.rect,
                    )
                    region.bbox = expanded_bbox
                    chess_text_indices.update(suppressed_indices)

                    try:
                        png_data, png_width, png_height = render_chess_diagram_to_png(
                            page, region, dpi=max(config.chess_diagram_dpi, 96)
                        )
                        png_data, png_width, png_height = _optimize_chess_diagram_export(png_data, config)

                        filename = f"chess_p{page_num}_{region_idx}.png"
                        chess_img = {
                            'page_num': page_num,
                            'filename': filename,
                            'data': png_data,
                            'extension': 'png',
                            'bbox': region.bbox,
                            'indices': region.text_span_indices,
                            'width': png_width,
                            'height': png_height,
                            'is_chess': True,
                        }
                        chess_imgs_for_page.append(chess_img)
                        all_chess_diagrams.append(chess_img)

                        start_index = min(region.text_span_indices)
                        diagram_entries.append(
                            {
                                "region_idx": region_idx,
                                "sort_y": float(region.bbox[1]),
                                "sort_x": float(region.bbox[0]),
                                "start_index": start_index,
                                "image": chess_img,
                            }
                        )

                        for ts in text_spans:
                            if ts.index in chess_text_indices:
                                continue
                            if is_chess_text(ts.text, ts.font_name) and _bbox_is_inside(ts.bbox, region.bbox):
                                chess_text_indices.add(ts.index)

                        chess_diagram_count += 1
                    except Exception as e:
                        print(f"    Warning: Could not render chess diagram: {e}")
        
        # Generate HTML for this page
        html_parts = []
        
        # Check for TOC entry
        page_title = None
        for item in toc:
            if item[-1] == page_num + 1:
                page_title = item[1]
                break
        
        line_items = _build_line_items(raw_lines, chess_text_indices)
        line_items.sort(key=lambda item: (item.y, item.start_index))
        diagram_entries.sort(key=lambda entry: (entry["sort_y"], entry["sort_x"], entry["start_index"]))
        diagram_cursor = 0

        def insert_diagram(entry: dict) -> None:
            chess_img = entry["image"]
            html_parts.append(
                '<div class="figure chess-diagram-container">'
                f'<img class="chess-diagram" src="images/{chess_img["filename"]}" '
                'alt="Diagram szachowy"/>'
                "</div>"
            )

        for line_item in line_items:
            while diagram_cursor < len(diagram_entries) and diagram_entries[diagram_cursor]["sort_y"] <= line_item.y + 1.0:
                insert_diagram(diagram_entries[diagram_cursor])
                diagram_cursor += 1

            normalized_text = line_item.text
            if not normalized_text.strip():
                continue
            
            # Determine if this is a heading based on line font size
            if line_item.font_size >= h1_threshold:
                html_parts.append(f"<h1>{html_module.escape(normalized_text)}</h1>")
            elif line_item.font_size >= h2_threshold:
                html_parts.append(f"<h2>{html_module.escape(normalized_text)}</h2>")
            elif line_item.font_size >= h3_threshold:
                html_parts.append(f"<h3>{html_module.escape(normalized_text)}</h3>")
            else:
                paragraph_class = ' class="notation-heavy"' if _is_notation_heavy_line(normalized_text) else ""
                html_parts.append(f"<p{paragraph_class}>{html_module.escape(normalized_text)}</p>")

        while diagram_cursor < len(diagram_entries):
            insert_diagram(diagram_entries[diagram_cursor])
            diagram_cursor += 1
        
        # Extract images (non-chess)
        page_images = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = page.parent.extract_image(xref)
            except Exception:
                continue
            
            if not base_image or not base_image.get("image"):
                continue
            
            image_count += 1
            optimized_image, optimized_extension = _optimize_embedded_raster_image(
                base_image["image"],
                base_image.get("ext", "png"),
                config,
            )
            img_filename = f"img_p{page_num}_{image_count}.{optimized_extension}"
            
            page_images.append({
                'filename': img_filename,
                'data': optimized_image,
                'extension': optimized_extension,
                'page': page_num,
            })
            all_images.append({
                'filename': img_filename,
                'data': optimized_image,
                'extension': optimized_extension,
                'page': page_num,
            })
        
        # Chess images are inlined into html_parts in reading order, but still
        # need to be attached to the chapter so build_epub can add them to the
        # EPUB manifest.
        html_parts = _merge_chess_problem_fragments(html_parts)
        page_images.extend(chess_imgs_for_page)
        source_page_label = _detect_page_label_from_spans(text_spans, page_width, page_height)
        
        chapters.append({
            'page_num': page_num,
            'title': page_title or f"Strona {page_num + 1}",
            'html_parts': html_parts,
            'images': page_images,
            'has_chess_diagrams': len(chess_imgs_for_page) > 0,
            'inline_chess_diagrams': True,
            '_source_page_label': source_page_label,
        })
    
    doc.close()
    
    return {
        'success': True,
        'chapters': chapters,
        'images': all_images,
        'chess_diagrams': all_chess_diagrams,
        'chess_diagram_count': chess_diagram_count,
        'method': 'pymupdf_with_chess_support',
        'layout_mode': 'reflowable',
        'text_content': any(len(ch['html_parts']) > 0 for ch in chapters),
    }


def _get_css_font_family(pdf_font_name: str) -> str:
    """Map PDF font names to CSS font families."""
    font_lower = pdf_font_name.lower()
    
    # Skip chess fonts - they'll be rendered as images
    if any(x in font_lower for x in ['chess', 'merida', 'skak', 'alpha', 'leipzig']):
        return "sans-serif"
    
    # Common font mappings
    if any(x in font_lower for x in ['aptos', 'arial', 'helvetica']):
        return "Arial, Helvetica, sans-serif"
    if any(x in font_lower for x in ['times', 'georgia', 'palatino']):
        return "Georgia, 'Times New Roman', serif"
    if any(x in font_lower for x in ['courier', 'mono']):
        return "'Courier New', Courier, monospace"
    if any(x in font_lower for x in ['calibri']):
        return "Calibri, 'Segoe UI', sans-serif"
    
    return "Georgia, 'Times New Roman', serif"


def _color_to_css(color_int) -> str:
    """Convert PDF color integer to CSS color string."""
    if color_int is None:
        return "#000000"
    
    r = (color_int >> 16) & 0xFF
    g = (color_int >> 8) & 0xFF
    b = color_int & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"
