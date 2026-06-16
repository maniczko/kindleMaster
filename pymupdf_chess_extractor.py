"""
KindleMaster — PyMuPDF Extraction with Chess Diagram Support
=============================================================
Extracts content from PDF using PyMuPDF while properly handling chess diagrams.

Chess diagrams in PDFs often use special fonts (Chess-Merida, etc.) with PUA 
(Private Use Area) characters that don't render in EPUB. This module detects 
those and renders them as images instead.
"""

import hashlib
import io
import html as html_module
import json
import re
import time
import zipfile
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

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
    chess_diagram_alt_text,
    chess_fen_html_attrs,
    strip_emails,
)
from chess_position_recognizer import (
    ChessFenResult,
    _bbox_overlap_ratio,
    detect_board_candidates_in_page_image,
    empty_chess_fen_result,
    load_piece_templates,
    recognize_chess_position_from_image,
    recognize_font_board_from_spans,
    summarize_chess_fen_results,
    validate_fen,
)
from chess_pgn_extractor import (
    annotate_records_with_replayed_fens,
    attach_fen_candidates_to_pgn_records,
    build_combined_pgn,
    build_pgn_download_html,
    extract_chess_pgn_records_from_text,
    merge_chess_pgn_continuation_records,
    normalize_ocr_text_for_pgn,
    render_chess_pgn_html_parts,
    summarize_chess_pgn_records,
)

WINGDINGS_TICK = "\uf0fc"
UNICODE_TICK = "✓"
PARAGRAPH_TEXT_RE = re.compile(r"^<p>(.*)</p>$", re.DOTALL)
FIGURINE_FONT_TOKENS = ("sptimefig", "spariesfig")
FIGURINE_TEXT_MAP = {
    "\xa2": "K",
    "\xa3": "Q",
    "\xa4": "N",
    "\xa5": "B",
    "\xa6": "R",
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
SAN_TOKEN_PATTERN = r"(?:O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?)"
EVAL_TOKEN_PATTERN = r"(?:\+\u2013|\u2013\+|\+=|=\+|\u00b1|\u2213)"
PROMOTION_SPACE_RE = re.compile(r"=\s+([QRBN])\b")
SAN_MATE_RE = re.compile(rf"({SAN_TOKEN_PATTERN})\s+mate\b(?!\s+in\b)", re.IGNORECASE)
SAN_PLUS_SPACE_RE = re.compile(r"(?<=[KQRBNa-h0-9])\s+\+(?=[!?]|\s|$)")
SAN_HASH_SPACE_RE = re.compile(r"(?<=[KQRBNa-h0-9])\s+#(?=[!?]|\s|$)")
SAN_EVAL_SPLIT_RE = re.compile(rf"(?<=[A-Za-z0-9])([+#])\s*({EVAL_TOKEN_PATTERN})")
MATE_HASH_PHRASE_RE = re.compile(r"\b(with|avoid|back-rank|smothered|of|the)\s+#(?=\s|[.,;:!?]|$)", re.IGNORECASE)
DIGIT_CAMEL_RE = re.compile(r"(?<=[0-9])(?=[A-Z][a-z])")
DIGIT_MATE_RE = re.compile(r"(?<=[0-9])(?=mate\b)")
PLUS_ANNOTATION_SPACE_RE = re.compile(r"\+\s+([!?])")
HASH_ANNOTATION_SPACE_RE = re.compile(r"#\s+([!?])")
WHITESPACE_RE = re.compile(r"\s+")
MULTISPACE_RE = re.compile(r"\s{2,}")
CLEAN_PUNCT_SPACE_RE = re.compile(r"\s+([,.;:!?])")
CLEAN_PUNCT_JOIN_RE = re.compile(r"([,.;:!?])([^\s])")
CLEAN_DECIMAL_RE = re.compile(r"(\d)\s+\.\s+(\d)")
CLEAN_MOVE_NUMBER_RE = re.compile(r"\b(\d{1,3}\.)\s+([KQRBNOa-h])")


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
    x0: float = 0.0
    x1: float = 0.0


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
    side_marker_margin = max(14.0, width * 0.18)

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
        min(page_rect.x1, expanded[2] + side_marker_margin + 2.0),
        min(page_rect.y1, expanded[3] + 2.0),
    )
    return expanded_bbox, suppressed_indices


def _resize_image_to_long_edge(
    image: Image.Image,
    max_long_edge: int,
    *,
    resample=Image.LANCZOS,
) -> Image.Image:
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
        resample,
    )
    return resized


def _optimize_chess_diagram_export(
    png_data: bytes,
    config: ConversionConfig,
) -> tuple[bytes, int, int]:
    source_image = Image.open(io.BytesIO(png_data)).convert("L")
    image = _resize_image_to_long_edge(
        source_image,
        config.diagram_image_long_edge,
        resample=Image.Resampling.LANCZOS,
    )
    image = _prepare_chess_diagram_for_reader(image)
    target_palette_size = max(4, min(int(config.diagram_palette_colors or 0), 64))

    baseline = _encode_chess_diagram_png(image, target_palette_size)
    size_ceiling = _encode_legacy_size_ceiling_png(
        source_image,
        target_palette_size,
        config.diagram_image_long_edge,
    )
    size_ceiling_cost = _archive_cost(size_ceiling)

    candidates = [size_ceiling, baseline]
    candidates.extend(_build_chess_diagram_png_candidates(image, target_palette_size)[1:])

    optimized = _select_best_chess_diagram_candidate(candidates, size_ceiling_cost)
    with Image.open(io.BytesIO(optimized)) as optimized_image:
        return optimized, optimized_image.width, optimized_image.height


def _legacy_prequantized_chess_image(
    image: Image.Image,
    max_long_edge: int,
) -> Image.Image:
    prequantized = image.quantize(
        colors=16,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    output = io.BytesIO()
    prequantized.save(output, format="PNG", optimize=True, compress_level=9)
    decoded = Image.open(io.BytesIO(output.getvalue())).convert("L")
    resized = _resize_image_to_long_edge(decoded, max_long_edge, resample=Image.Resampling.LANCZOS)
    return _prepare_chess_diagram_for_reader(resized)


def _encode_legacy_size_ceiling_png(
    image: Image.Image,
    target_palette_size: int,
    max_long_edge: int,
) -> bytes:
    legacy_image = _legacy_prequantized_chess_image(image, max_long_edge)
    legacy_candidates = _build_legacy_chess_diagram_png_candidates(legacy_image, target_palette_size)
    return _select_best_chess_diagram_candidate(
        legacy_candidates,
        _archive_cost(legacy_candidates[0]),
    )


def _build_legacy_chess_diagram_png_candidates(
    image: Image.Image,
    target_palette_size: int,
) -> list[bytes]:
    baseline = _encode_chess_diagram_png(image, target_palette_size)
    enhanced_image = ImageOps.autocontrast(image, cutoff=1)
    sharpened_image = enhanced_image.filter(
        ImageFilter.UnsharpMask(radius=0.85, percent=125, threshold=3)
    )
    return [
        baseline,
        _encode_chess_diagram_png(enhanced_image, target_palette_size),
        _encode_chess_diagram_png(sharpened_image, target_palette_size),
    ]


def _build_chess_diagram_png_candidates(
    image: Image.Image,
    target_palette_size: int,
) -> list[bytes]:
    baseline = _encode_chess_diagram_png(image, target_palette_size)
    compact_hatch_palette_size = max(2, min(target_palette_size, 4))
    contrast_sharp_image = ImageOps.autocontrast(image, cutoff=1).filter(
        ImageFilter.UnsharpMask(radius=0.85, percent=125, threshold=3)
    )
    hatch_softened_image = _soften_chess_diagram_hatch_texture(image)
    return [
        baseline,
        _encode_chess_diagram_png(contrast_sharp_image, target_palette_size),
        _encode_chess_diagram_png(hatch_softened_image, compact_hatch_palette_size),
    ]


def _prepare_chess_diagram_for_reader(image: Image.Image) -> Image.Image:
    """Lighten noisy hatch midtones while preserving black piece strokes."""
    grayscale = image.convert("L")

    def tone(pixel: int) -> int:
        if pixel < 80:
            return pixel
        if pixel < 180:
            return min(255, pixel + 24)
        if pixel < 235:
            return min(255, pixel + 12)
        return pixel

    return grayscale.point(tone)


def _soften_chess_diagram_hatch_texture(image: Image.Image) -> Image.Image:
    grayscale = image.convert("L")
    smoothed = grayscale.filter(ImageFilter.MedianFilter(size=3))
    darkened = grayscale.point(lambda pixel: max(0, pixel - 10))
    dark_mask = grayscale.point(lambda pixel: 255 if pixel <= 88 else 0)
    light_mask = grayscale.point(lambda pixel: 255 if pixel >= 224 else 0)
    softened = Image.composite(darkened, smoothed, dark_mask)
    softened = Image.composite(grayscale, softened, light_mask)
    return ImageOps.autocontrast(softened, cutoff=1)


def _scan_chess_diagram_preprocess_variants(
    image: Image.Image,
    config: ConversionConfig,
) -> dict[str, Image.Image]:
    max_edge = max(180, min(int(getattr(config, "scanned_chess_diagram_long_edge", 360) or 360), 640))
    base = _resize_image_to_long_edge(image.convert("L"), max_edge, resample=Image.Resampling.LANCZOS)
    reader_enhanced = ImageOps.autocontrast(_prepare_chess_diagram_for_reader(base), cutoff=1).filter(
        ImageFilter.UnsharpMask(radius=0.85, percent=135, threshold=3)
    )
    autocontrast = ImageOps.autocontrast(base, cutoff=1)
    unsharp = autocontrast.filter(ImageFilter.UnsharpMask(radius=0.9, percent=150, threshold=2))
    threshold_source = ImageOps.autocontrast(base, cutoff=1)
    threshold_bw = threshold_source.point(lambda pixel: 255 if pixel >= 154 else 0).convert("L")
    grid_preview = reader_enhanced.convert("RGB")
    draw = ImageDraw.Draw(grid_preview)
    step_x = grid_preview.width / 8.0
    step_y = grid_preview.height / 8.0
    for index in range(9):
        x = int(round(index * step_x))
        y = int(round(index * step_y))
        draw.line((x, 0, x, grid_preview.height), fill=(196, 91, 36), width=1)
        draw.line((0, y, grid_preview.width, y), fill=(196, 91, 36), width=1)
    return {
        "original": base,
        "reader_enhanced": reader_enhanced,
        "autocontrast": autocontrast,
        "unsharp_mask": unsharp,
        "threshold_bw": threshold_bw,
        "grid_highlight_preview": grid_preview,
    }


def _encode_scan_chess_preprocessed_image(
    image: Image.Image,
    config: ConversionConfig,
) -> tuple[bytes, int, int]:
    target_palette_size = max(4, min(int(getattr(config, "diagram_palette_colors", 16) or 16), 64))
    encoded = _encode_chess_diagram_png(image.convert("L"), target_palette_size)
    with Image.open(io.BytesIO(encoded)) as decoded:
        return encoded, decoded.width, decoded.height


def _scan_chess_preprocess_metadata(
    *,
    selected_variant: str,
    display_variant: str,
    confidence: float,
    piece_confidence: float | None = None,
    grid_confidence: float | None = None,
) -> dict[str, Any]:
    return {
        "selected_preprocess_variant": selected_variant,
        "display_variant_used": display_variant,
        "fen_confidence": round(float(confidence or 0.0), 3),
        "piece_confidence": round(float(piece_confidence if piece_confidence is not None else confidence or 0.0), 3),
        "grid_confidence": round(float(grid_confidence if grid_confidence is not None else confidence or 0.0), 3),
    }


def _recognize_scan_chess_preprocessed_variants(
    crop: Image.Image,
    *,
    config: ConversionConfig,
    piece_templates: dict,
    bbox: tuple[float, float, float, float] | None = None,
):
    variants = _scan_chess_diagram_preprocess_variants(crop, config)
    best_result = None
    best_variant = "original"
    for variant_name in ("original", "autocontrast", "unsharp_mask", "threshold_bw"):
        data, _, _ = _encode_scan_chess_preprocessed_image(variants[variant_name], config)
        result = recognize_chess_position_from_image(
            data,
            bbox=bbox,
            min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85),
            piece_templates=piece_templates,
        )
        if best_result is None or float(getattr(result, "confidence", 0.0) or 0.0) > float(getattr(best_result, "confidence", 0.0) or 0.0):
            best_result = result
            best_variant = variant_name
    if best_result is None:
        return None, best_variant, _scan_chess_preprocess_metadata(
            selected_variant=best_variant,
            display_variant="reader_enhanced",
            confidence=0.0,
        )
    confidence = float(getattr(best_result, "confidence", 0.0) or 0.0)
    return best_result, best_variant, _scan_chess_preprocess_metadata(
        selected_variant=best_variant,
        display_variant="reader_enhanced",
        confidence=confidence,
    )


def _select_best_chess_diagram_candidate(
    candidates: list[bytes],
    baseline_cost: tuple[int, int],
) -> bytes:
    best = candidates[0]
    best_score = _chess_diagram_quality_score(best)
    best_cost = _archive_cost(best)

    for candidate in candidates[1:]:
        candidate_cost = _archive_cost(candidate)
        if candidate_cost[0] > baseline_cost[0] or candidate_cost[1] > baseline_cost[1]:
            continue

        candidate_score = _chess_diagram_quality_score(candidate)
        if candidate_score > best_score + 0.5:
            best = candidate
            best_score = candidate_score
            best_cost = candidate_cost
        elif candidate_score >= best_score - 0.5 and candidate_cost < best_cost:
            best = candidate
            best_score = candidate_score
            best_cost = candidate_cost

    return best


def _chess_diagram_quality_score(data: bytes) -> float:
    with Image.open(io.BytesIO(data)) as raw_image:
        image = raw_image.convert("L")
        low, high = image.getextrema()
        contrast = float(high - low)
        edge_image = image.filter(ImageFilter.FIND_EDGES)
        edge_mean = float(ImageStat.Stat(edge_image).mean[0])
        histogram = image.histogram()
        total = float(image.width * image.height) or 1.0
        dark_ratio = sum(histogram[:96]) / total
        light_ratio = sum(histogram[192:]) / total
        middle_ratio = sum(histogram[96:192]) / total
        separation = contrast * min(dark_ratio, light_ratio) * 4.0
        fine_noise = float(ImageStat.Stat(edge_image.filter(ImageFilter.FIND_EDGES)).mean[0])
        hatch_penalty = middle_ratio * fine_noise
        return contrast + separation + edge_mean - hatch_penalty


def _archive_cost(data: bytes) -> tuple[int, int]:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("image.png", data)
        info = archive.getinfo("image.png")
        return int(info.compress_size), len(data)


def _encode_chess_diagram_png(image: Image.Image, target_palette_size: int) -> bytes:
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
    return output.getvalue()


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


def _mate_hash_phrase_replacement(match: re.Match[str]) -> str:
    return f"{match.group(1)} mate"


def _normalize_text_for_epub(text: str, font_name: str) -> str:
    """Replace Wingdings-only markers with readable Unicode."""
    normalized = text or ""
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
    if "=" in normalized and " " in normalized:
        normalized = PROMOTION_SPACE_RE.sub(r"=\1", normalized)
    if "mate" in normalized.lower():
        normalized = SAN_MATE_RE.sub(r"\1#", normalized)
        normalized = DIGIT_MATE_RE.sub(" ", normalized)
    if "+" in normalized:
        normalized = SAN_PLUS_SPACE_RE.sub("+", normalized)
        normalized = SAN_EVAL_SPLIT_RE.sub(r"\1 (\2)", normalized)
        normalized = PLUS_ANNOTATION_SPACE_RE.sub(r"+\1", normalized)
    if "#" in normalized:
        normalized = SAN_HASH_SPACE_RE.sub("#", normalized)
        normalized = SAN_EVAL_SPLIT_RE.sub(r"\1 (\2)", normalized)
        normalized = MATE_HASH_PHRASE_RE.sub(_mate_hash_phrase_replacement, normalized)
        normalized = HASH_ANNOTATION_SPACE_RE.sub(r"#\1", normalized)
    if INLINE_EVAL_RE.search(normalized):
        normalized = INLINE_EVAL_RE.sub(r"\1 ", normalized)
    if any(char.isdigit() for char in normalized) and any(char.isupper() for char in normalized):
        normalized = DIGIT_CAMEL_RE.sub(" ", normalized)
    if "  " in normalized or "\t" in normalized:
        normalized = MULTISPACE_RE.sub(" ", normalized)
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
                    if line_text[-1] not in NO_SPACE_AFTER_TOKENS and line_text[-1] != "=" and not line_text.endswith(" "):
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
            x0=min(float(segment.get("x0", 0.0)) for segment in segments),
            x1=max(float(segment.get("x1", 0.0)) for segment in segments),
        ))

    return items


def extract_chess_notation_pdf_reflow(
    pdf_path: str,
    config: ConversionConfig,
    pdf_metadata: dict | None = None,
) -> dict:
    """Extract large text-layer chess game collections as notation-first EPUB.

    These PDFs often contain thousands of embedded board images. Rendering or
    optimizing each board image makes generation very slow and can exceed size
    budgets. This path preserves the SAN/PGN-like text layer and deliberately
    sends raster diagrams to the report/review layer instead of inlining them.
    """
    started = time.perf_counter()
    metadata = dict(pdf_metadata or _extract_pdf_metadata(pdf_path) or {})
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    try:
        chunk_pages = max(10, int(getattr(config, "chess_notation_chapter_pages", 40) or 40))
        chapters: list[dict[str, Any]] = []
        current_parts: list[str] = []
        current_text_lines: list[str] = []
        current_start = 0
        text_pages = 0
        notation_line_count = 0
        skipped_image_count = 0
        chess_pgn_records: list[Any] = []
        text_extraction_seconds = 0.0
        pgn_extraction_seconds = 0.0

        def flush_chapter(end_page: int) -> None:
            nonlocal current_parts, current_text_lines, current_start, chess_pgn_records, pgn_extraction_seconds
            if not current_parts:
                current_start = end_page + 1
                current_text_lines = []
                return
            flush_started = time.perf_counter()
            chapter_records = annotate_records_with_replayed_fens(
                extract_chess_pgn_records_from_text(
                    "\n".join(current_text_lines),
                    page_num=current_start,
                    source_title=str(metadata.get("title") or Path(pdf_path).stem),
                    ocr_confidence=1.0,
                )
            )
            chess_pgn_records.extend(chapter_records)
            if chapter_records:
                current_parts.extend(
                    render_chess_pgn_html_parts(
                        chapter_records,
                        download_href="",
                    )
                )
            pgn_extraction_seconds += time.perf_counter() - flush_started
            chapters.append(
                {
                    "title": f"Partie {current_start + 1}-{end_page + 1}",
                    "html_parts": current_parts,
                    "images": [],
                    "_page_start": current_start,
                    "_page_end": end_page,
                    "_kind": "chess-notation",
                }
            )
            current_parts = []
            current_text_lines = []
            current_start = end_page + 1

        for page_num in range(total_pages):
            if page_num > current_start and (page_num - current_start) >= chunk_pages:
                flush_chapter(page_num - 1)

            page = doc[page_num]
            page_started = time.perf_counter()
            skipped_image_count += len(page.get_images(full=True))
            line_items = _chess_notation_line_items_from_page(page, page_num)
            body_lines = _chess_notation_body_lines(line_items)
            page_parts = _chess_notation_page_html_parts(line_items, page_num=page_num, body_lines=body_lines)
            text_extraction_seconds += time.perf_counter() - page_started
            if page_parts:
                text_pages += 1
                notation_line_count += sum(1 for line in body_lines if _is_notation_heavy_line(line))
                current_parts.extend(page_parts)
                current_text_lines.extend(body_lines)

        flush_chapter(total_pages - 1)
    finally:
        doc.close()

    chess_pgn_records = merge_chess_pgn_continuation_records(chess_pgn_records)
    chess_pgn_summary = summarize_chess_pgn_records(chess_pgn_records)
    audit_metadata = dict(metadata.get("audit") or {})
    audit_metadata.update(
        {
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "timings": {
                "text_extraction_seconds": round(text_extraction_seconds, 4),
                "pgn_extraction_seconds": round(pgn_extraction_seconds, 4),
            },
        }
    )

    return {
        "success": True,
        "method": "chess-notation-text-reflow",
        "text_content": bool(chapters),
        "layout_mode": "reflowable",
        "metadata": {
            **metadata,
            "publication_kind": "chess-notation-collection",
            "image_policy": "embedded_board_images_skipped_for_runtime_budget",
            "source_page_count": total_pages,
            "text_page_count": text_pages,
            "notation_line_count": notation_line_count,
            "skipped_embedded_image_count": skipped_image_count,
            "chess_pgn": chess_pgn_summary,
            "audit": audit_metadata,
            "chess_fen": {
                "status": "passed" if chess_pgn_summary.get("derived_final_fen_count") else "requires_review",
                "source": "pgn_replay",
                "diagram_count": int(chess_pgn_summary.get("candidate_game_count", 0) or 0),
                "fen_count": int(chess_pgn_summary.get("fen_count", 0) or 0),
                "manual_review_count": int(chess_pgn_summary.get("manual_review_count", 0) or 0),
            },
        },
        "images": [],
        "chapters": chapters,
        "extra_artifacts": _scan_chess_pgn_extra_artifacts(
            chess_pgn_records,
            source_title=str(metadata.get("title") or Path(pdf_path).stem),
        ),
        "audit": {
            "status": "passed_with_warnings",
            "image_policy": "skipped_embedded_images",
            "skipped_embedded_image_count": skipped_image_count,
            "warning": "Raster board images were skipped to keep large chess notation collections generatable.",
        },
    }


def _chess_notation_line_items_from_page(page: fitz.Page, page_num: int) -> list[TextLineItem]:
    raw_lines: list[dict[str, Any]] = []
    span_index = 0
    page_width = float(page.rect.width or 0.0)
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP, sort=True)
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            raw_line_segments = []
            for span in line.get("spans", []):
                raw_text = span.get("text", "")
                if not str(raw_text or "").strip():
                    span_index += 1
                    continue
                x0, y0, _x1, _y1 = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                raw_line_segments.append(
                    {
                        "index": span_index,
                        "text": raw_text,
                        "font_name": span.get("font", "") or "",
                        "font_size": span.get("size", 12),
                        "x0": x0,
                        "x1": _x1,
                        "y0": y0,
                    }
                )
                span_index += 1
            if raw_line_segments:
                for segment_group in _split_chess_notation_raw_line_segments(raw_line_segments, page_width=page_width):
                    raw_lines.append({"segments": segment_group})
    return _build_line_items(raw_lines, set())


def _split_chess_notation_raw_line_segments(
    segments: list[dict[str, Any]],
    *,
    page_width: float,
) -> list[list[dict[str, Any]]]:
    if len(segments) < 2 or page_width <= 0:
        return [segments]
    midpoint = page_width * 0.52
    left = [segment for segment in segments if _segment_center_x(segment) < midpoint]
    right = [segment for segment in segments if _segment_center_x(segment) >= midpoint]
    if not left or not right:
        return [segments]
    if _segment_group_text(left).strip() and _segment_group_text(right).strip():
        return [left, right]
    return [segments]


def _segment_center_x(segment: dict[str, Any]) -> float:
    return (float(segment.get("x0", 0.0) or 0.0) + float(segment.get("x1", 0.0) or 0.0)) / 2.0


def _segment_group_text(segments: list[dict[str, Any]]) -> str:
    return " ".join(str(segment.get("text") or "") for segment in segments).strip()


def _chess_notation_page_html_parts(
    line_items: list[TextLineItem],
    *,
    page_num: int,
    body_lines: list[str] | None = None,
) -> list[str]:
    html_parts: list[str] = []
    if not line_items:
        return html_parts

    page_label = ""
    for index, item in enumerate(line_items[:3]):
        text = _clean_chess_notation_line(item.text)
        if _is_number_only(text):
            page_label = text
            break
    marker = f'<span id="book-page-{html_module.escape(page_label or str(page_num + 1))}" class="page-marker"></span>'
    if body_lines is None:
        body_lines = _chess_notation_body_lines(line_items)
    if not body_lines:
        return []
    html_parts.append(marker)
    html_parts.append(
        f'<pre class="chess-notation-page chess-notation-text" data-page="{page_num + 1}"><code>'
        + html_module.escape("\n".join(body_lines))
        + "</code></pre>"
    )
    return html_parts


def _chess_notation_body_lines(line_items: list[TextLineItem]) -> list[str]:
    body_lines: list[str] = []
    for index, item in enumerate(_order_chess_notation_lines_for_reading(line_items)):
        text = _clean_chess_notation_line(item.text)
        if not text:
            continue
        if index <= 2 and _is_number_only(text):
            continue
        if _is_single_board_coordinate_line(text) or _looks_like_board_coordinate_noise(text):
            continue
        body_lines.append(text)
    return body_lines


def _order_chess_notation_lines_for_reading(line_items: list[TextLineItem]) -> list[TextLineItem]:
    if len(line_items) < 12:
        return line_items
    x_values = [item.x0 for item in line_items if item.x1 > item.x0]
    if not x_values:
        return line_items
    min_x = min(x_values)
    max_x = max(item.x1 for item in line_items if item.x1 > item.x0)
    if max_x - min_x < 220:
        return line_items
    midpoint = min_x + (max_x - min_x) / 2.0
    left = [item for item in line_items if (item.x0 + item.x1) / 2.0 < midpoint]
    right = [item for item in line_items if (item.x0 + item.x1) / 2.0 >= midpoint]
    if min(len(left), len(right)) < 4:
        return line_items
    return sorted(left, key=lambda item: (item.y, item.x0, item.start_index)) + sorted(
        right,
        key=lambda item: (item.y, item.x0, item.start_index),
    )


def _chess_notation_line_count(line_items: list[TextLineItem]) -> int:
    count = 0
    for text in _chess_notation_body_lines(line_items):
        if text and _is_notation_heavy_line(text):
            count += 1
    return count


BOARD_FILES_INLINE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])a\s+b\s+c\s+d\s+e\s+f\s+g\s+h(?![A-Za-z0-9])"
)
BOARD_FILES_PREFIX_ECO_RE = re.compile(
    r"(?i)^\s*a\s+b\s+c\s+d\s+e\s+f\s+g\s+h(?=\s*\d{1,5}\s+[A-E][0-9]{2}\b)"
)
BOARD_RANK_GRID_RE = re.compile(r"(?<![\d.])(?:[1-8]\s+){3,}[1-8](?![\d.])")


def _clean_chess_notation_line(text: str) -> str:
    cleaned = WHITESPACE_RE.sub(" ", strip_emails(text or "")).strip()
    cleaned = normalize_ocr_text_for_pgn(cleaned)
    cleaned = cleaned.strip(" |")
    if "a b c d e f g h" in cleaned.lower():
        cleaned = BOARD_FILES_PREFIX_ECO_RE.sub("", cleaned)
        cleaned = BOARD_FILES_INLINE_RE.sub(" ", cleaned)
    if any(char in cleaned for char in ",.;:!?"):
        cleaned = CLEAN_PUNCT_SPACE_RE.sub(r"\1", cleaned)
        cleaned = CLEAN_PUNCT_JOIN_RE.sub(r"\1 \2", cleaned)
        cleaned = re.sub(r"\b(\d{1,3})\.\s+\.\.\s*", r"\1...", cleaned)
        cleaned = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", cleaned)
        cleaned = re.sub(r"\b(\d{1,3}\.\.\.)\s+([KQRBNOa-h])", r"\1\2", cleaned)
    if any(char in cleaned for char in "12345678") and " " in cleaned:
        cleaned = BOARD_RANK_GRID_RE.sub(" ", cleaned)
        cleaned = CLEAN_DECIMAL_RE.sub(r"\1.\2", cleaned)
        cleaned = CLEAN_MOVE_NUMBER_RE.sub(r"\1\2", cleaned)
    if "  " in cleaned or "\t" in cleaned:
        cleaned = MULTISPACE_RE.sub(" ", cleaned)
    return cleaned


def _looks_like_board_coordinate_noise(text: str) -> bool:
    tokens = re.findall(r"[A-Za-z0-9]+", text or "")
    compact = re.sub(r"\s+", "", text or "")
    if compact and set(compact) <= set("12345678") and len(compact) >= 2:
        return True
    if len(tokens) < 4:
        return False
    lowered = [token.lower() for token in tokens]
    if all(token in set("abcdefgh") for token in lowered):
        return True
    if all(token in set("12345678") for token in lowered):
        return True
    if all(token and set(token) <= set("12345678") and len(token) <= 2 for token in lowered):
        return True
    return False


def _is_single_board_coordinate_line(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "").lower()
    return normalized in set("abcdefgh12345678")


def _looks_like_chess_game_metadata_line(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return False
    if re.search(r"\b[A-E][0-9]{2}\b", normalized):
        return True
    if re.search(r"(?i)\b(?:white|black|variation|attack|defen[cs]e|gambit|blitz|rapid|classical|titled)\b", normalized):
        return True
    if re.search(r"\b(?:[12][0-9]{3}|[0-9]{4})\b", normalized) and "," in normalized:
        return True
    return False


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


def _attach_fen_to_chess_image(chess_img: dict, result) -> dict:
    payload = result.to_dict()
    chess_img["fen_result"] = payload
    chess_img["fen_confidence"] = payload.get("confidence", 0.0)
    chess_img["fen_method"] = payload.get("method", "")
    if payload.get("fen"):
        chess_img["fen"] = payload["fen"]
    return payload


def _chess_fen_record(*, page_num: int, filename: str, result, source: str) -> dict:
    payload = result.to_dict()
    return {
        **payload,
        "page_num": page_num,
        "page_label": page_num + 1,
        "filename": filename,
        "source": source,
    }


def _scan_image_for_board_candidates(
    image_data: bytes,
    *,
    page_num: int,
    filename: str,
    config: ConversionConfig,
    piece_templates: dict | None = None,
) -> list[dict]:
    if not getattr(config, "chess_fen_recognition_enabled", True):
        return []
    max_candidates = max(0, int(getattr(config, "chess_fen_scan_candidates_per_page", 3) or 0))
    if max_candidates <= 0:
        return []
    max_pages = int(getattr(config, "chess_fen_scan_max_pages", 0) or 0)
    if max_pages <= 0 or page_num >= max_pages:
        return []
    candidates = detect_board_candidates_in_page_image(
        image_data,
        max_candidates=max_candidates,
        enable_sliding_probe=bool(getattr(config, "chess_fen_scan_enable_sliding_probe", False)),
    )
    if candidates and piece_templates:
        try:
            page_image = Image.open(io.BytesIO(image_data)).convert("L")
        except Exception:
            page_image = None
        if page_image is not None:
            recognized = []
            for candidate in candidates:
                if not candidate.bbox:
                    recognized.append(candidate)
                    continue
                x0, y0, x1, y1 = candidate.bbox
                crop_box = tuple(int(round(value)) for value in (x0, y0, x1, y1))
                crop = page_image.crop(crop_box)
                output = io.BytesIO()
                crop.save(output, format="PNG")
                template_result = recognize_chess_position_from_image(
                    output.getvalue(),
                    bbox=candidate.bbox,
                    min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85),
                    piece_templates=piece_templates,
                )
                recognized.append(template_result)
            candidates = recognized
    return [
        _chess_fen_record(
            page_num=page_num,
            filename=filename,
            result=candidate,
            source="embedded-page-image",
        )
        for candidate in candidates
    ]


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


SCAN_CHESS_CACHE_VERSION = 3
SCAN_CHESS_PAGE_CANDIDATE_CACHE_VERSION = 17
SCAN_CHESS_RECOGNITION_CACHE_VERSION = 6
SCAN_CHESS_SPARSE_EXACT_CONSENSUS_MIN_PIECES = 3
SCAN_CHESS_SPARSE_EXACT_CONSENSUS_MIN_CONFIDENCE = 0.827
SCAN_CHESS_SPARSE_EXACT_CONSENSUS_MAX_PIECES = 8
_PIECE_TEMPLATE_RUNTIME_TOKENS: dict[int, str] = {}
_VERIFIED_CROP_LABEL_CACHE: dict[tuple[str, int, int], dict[str, dict[str, Any]]] = {}


def _resolve_chess_piece_template_dir(config: ConversionConfig) -> str:
    explicit = str(getattr(config, "chess_fen_piece_template_dir", "") or "").strip()
    if explicit:
        return explicit
    profile = str(getattr(config, "chess_fen_template_profile", "") or "").strip()
    if not profile:
        return ""
    candidate = Path("reference_inputs") / "chess_fen" / "templates" / profile
    return str(candidate) if candidate.exists() else ""


def _resolve_chess_verified_crop_labels_path(config: ConversionConfig) -> str:
    explicit = str(getattr(config, "chess_fen_verified_crop_labels_path", "") or "").strip()
    if explicit:
        return explicit
    return ""


def _scan_chess_is_partial_separator_crop(image: Image.Image) -> bool:
    """Reject crops that are separator strips or partial adjacent boards, not full diagrams."""
    try:
        gray = image.convert("L")
        dark = np.asarray(gray) < 80
    except Exception:
        return False
    if dark.size == 0:
        return False
    height, width = dark.shape
    if min(width, height) < 120:
        return False
    overall_dark = float(dark.mean())
    if overall_dark >= 0.22:
        return False

    def max_low_density_run(values: np.ndarray, threshold: float = 0.05) -> int:
        best = 0
        current = 0
        for value in values:
            if float(value) < threshold:
                current += 1
                best = max(best, current)
            else:
                current = 0
        return best

    row_low_run = max_low_density_run(dark.mean(axis=1))
    col_low_run = max_low_density_run(dark.mean(axis=0))
    long_horizontal_gap = row_low_run >= max(32, int(height * 0.11))
    long_vertical_gap = col_low_run >= max(48, int(width * 0.18))
    return bool(long_horizontal_gap or long_vertical_gap)


def extract_scanned_chess_pdf_with_support(pdf_path: str, config: ConversionConfig, pdf_metadata: dict | None = None) -> dict:
    """Extract image-only chess books as compact reflow chapters with board crops."""
    started = time.perf_counter()
    metadata = dict(pdf_metadata or {})
    doc = fitz.open(pdf_path)
    try:
        template_dir = _resolve_chess_piece_template_dir(config)
        piece_templates = (
            load_piece_templates(template_dir)
            if getattr(config, "chess_fen_recognition_enabled", True) and template_dir
            else {}
        )
        front_matter_metadata = _scan_chess_front_matter_metadata(pdf_path, doc, config)
        page_candidates = _scan_chess_page_candidates(pdf_path, doc, config, piece_templates=piece_templates)
        selective_ocr = _scan_chess_selective_ocr_pages(pdf_path, doc, page_candidates, config)
        selective_ocr_pages = selective_ocr.get("pages", {}) if isinstance(selective_ocr.get("pages"), dict) else {}
        selective_ocr_summary = selective_ocr.get("summary", {}) if isinstance(selective_ocr.get("summary"), dict) else {}
        chapters: list[dict[str, Any]] = []
        all_images: list[dict[str, Any]] = []
        chess_fen_records: list[dict[str, Any]] = []
        chess_html_diagram_records: list[dict[str, Any]] = []
        chess_pgn_records = []
        diagram_total = 0
        raw_candidate_total = 0
        non_board_rejected_count = 0

        for page_record in page_candidates:
            page_num = int(page_record.get("page_num", 0))
            candidates = list(page_record.get("candidates", []) or [])
            if not candidates:
                continue
            raw_candidate_total += len(candidates)
            try:
                image_data = _page_image_data_for_scan_chess(doc, page_num)
                page_image = Image.open(io.BytesIO(image_data)).convert("RGB")
            except Exception:
                continue

            html_parts = []
            ocr_record = selective_ocr_pages.get(str(page_num)) or selective_ocr_pages.get(page_num)
            page_pgn_records = []
            if isinstance(ocr_record, dict):
                html_parts.extend(_scan_chess_ocr_html_parts(ocr_record, page_num=page_num))
                page_pgn_records = extract_chess_pgn_records_from_text(
                    str(ocr_record.get("text") or ""),
                    page_num=page_num,
                    source_title=str(metadata.get("title") or metadata.get("inferred_publication_title") or ""),
                    ocr_confidence=float(ocr_record.get("confidence", 0.0) or 0.0),
                )
            html_parts.append(
                f'<p class="page-marker">Strona {page_num + 1}: wykryto {len(candidates)} kandydatow diagramow szachowych.</p>'
            )
            chapter_images: list[dict[str, Any]] = []
            page_fen_values: list[str] = []
            for candidate_index, candidate in enumerate(candidates, start=1):
                bbox = _clamp_bbox(candidate.get("bbox"), page_image.size)
                if bbox is None:
                    continue
                recognition_bbox = _clamp_bbox(candidate.get("bbox"), page_image.size, pad_ratio=0.0, min_pad=0.0)
                if recognition_bbox is None:
                    recognition_bbox = bbox
                crop = page_image.crop(bbox)
                if min(crop.size) < 80:
                    continue
                crop = _resize_image_to_long_edge(
                    crop,
                    int(getattr(config, "scanned_chess_diagram_long_edge", 360) or 360),
                    resample=Image.Resampling.LANCZOS,
                )
                if _scan_chess_is_partial_separator_crop(crop):
                    non_board_rejected_count += 1
                    continue
                png_data, width, height = _encode_scan_chess_diagram_crop(crop, config)
                preprocess_metadata = _scan_chess_preprocess_metadata(
                    selected_variant="full_page_bbox_recognition",
                    display_variant="reader_enhanced",
                    confidence=float(candidate.get("confidence", 0.0) or 0.0),
                )
                filename = f"scan_chess_p{page_num + 1:03d}_{candidate_index:02d}.png"
                if piece_templates:
                    recognition = _recognize_scan_chess_candidate_bbox(
                        page_image,
                        tuple(float(value) for value in recognition_bbox),
                        config=config,
                        piece_templates=piece_templates,
                        min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85),
                        reader_bbox=tuple(float(value) for value in bbox),
                    )
                    recognition = _scan_chess_confirm_final_rendered_crop_recognition(
                        recognition,
                        png_data,
                        bbox=tuple(float(value) for value in bbox),
                        piece_templates=piece_templates,
                        min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85),
                    )
                    recognition = _scan_chess_apply_verified_crop_label(
                        recognition,
                        png_data,
                        bbox=tuple(float(value) for value in bbox),
                        config=config,
                    )
                    if not recognition.board_detected:
                        non_board_rejected_count += 1
                        continue
                    candidate_payload = _scan_chess_fen_payload(candidate, recognition)
                else:
                    candidate_payload = _scan_chess_candidate_review_payload(candidate)
                marker_side = _infer_scan_chess_side_to_move(
                    page_image,
                    tuple(float(value) for value in recognition_bbox),
                )
                if marker_side and bool(getattr(config, "chess_fen_apply_side_marker", False)):
                    candidate_payload = _apply_scan_chess_side_to_move_marker(candidate_payload, marker_side)
                diagram_total += 1
                chess_img = {
                    "filename": filename,
                    "data": png_data,
                    "extension": "png",
                    "width": width,
                    "height": height,
                    "bbox": tuple(float(value) for value in bbox),
                    "page": page_num,
                    "is_chess": True,
                    "inline": True,
                    "fen_result": candidate_payload,
                    "fen_confidence": candidate_payload.get("confidence", 0.0),
                    "fen_method": candidate_payload.get("method", ""),
                    **preprocess_metadata,
                }
                if candidate_payload.get("fen"):
                    chess_img["fen"] = candidate_payload["fen"]
                    page_fen_values.append(str(candidate_payload["fen"]))
                chapter_images.append(chess_img)
                all_images.append(chess_img)
                chess_fen_records.append(
                    {
                        "page": page_num + 1,
                        "filename": filename,
                        "source": "scanned-page-board-crop",
                        **preprocess_metadata,
                        **candidate_payload,
                    }
                )
                chess_html_diagram_records.append(
                    {
                        "page": page_num + 1,
                        "filename": filename,
                        "source": "scanned-page-board-crop",
                        "image_data": png_data,
                        "extension": "png",
                        "width": width,
                        "height": height,
                        **preprocess_metadata,
                        **candidate_payload,
                    }
                )
                fen_attrs = chess_fen_html_attrs(chess_img)
                fen_value = str(candidate_payload.get("fen") or "").strip()
                if fen_value:
                    fen_note = (
                        '<p class="diagram-fen">'
                        '<span class="diagram-fen-label">FEN:</span> '
                        f'<code class="diagram-fen-code">{html_module.escape(fen_value)}</code>'
                        "</p>"
                    )
                elif bool(getattr(config, "chess_fen_emit_review_notes", False)):
                    fen_note = (
                        '<p class="diagram-fen diagram-review" data-fen-status="requires-review">'
                        "FEN: wymaga review - brak deterministycznej pewnosci figur."
                        "</p>"
                    )
                else:
                    fen_note = ""
                html_parts.append(
                    '<div class="chess-problem">'
                    f'<p class="diagram-caption">Strona {page_num + 1}, diagram {candidate_index}</p>'
                    f'<div class="figure chess-diagram-container"{fen_attrs}>'
                    f'<img class="chess-diagram" src="images/{html_module.escape(filename, quote=True)}" '
                    f'alt="{html_module.escape(chess_diagram_alt_text(chess_img), quote=True)}"{fen_attrs}/>'
                    '</div>'
                    f"{fen_note}"
                    "</div>"
                )

            if page_pgn_records:
                page_pgn_records = attach_fen_candidates_to_pgn_records(page_pgn_records, page_fen_values)
                page_pgn_records = annotate_records_with_replayed_fens(page_pgn_records)
                chess_pgn_records.extend(page_pgn_records)
                html_parts.extend(
                    render_chess_pgn_html_parts(
                        page_pgn_records,
                        download_href="",
                    )
                )

            if chapter_images:
                chapters.append(
                    {
                        "title": f"Strona {page_num + 1} - diagramy szachowe",
                        "html_parts": html_parts,
                        "images": chapter_images,
                        "page_num": page_num,
                        "_page_start": page_num,
                        "_page_end": page_num,
                        "_source_page_label": str(page_num + 1),
                        "_fallback_mode": "scan-chess-crop-review",
                        "inline_chess_diagrams": True,
                    }
                )

        if not chapters:
            chapters.append(
                {
                    "title": "Skan szachowy - review",
                    "html_parts": [
                        "<p>Nie wykryto wystarczająco pewnych kandydatów plansz. Plik wymaga manualnego review lub mocniejszej segmentacji.</p>"
                    ],
                    "images": [],
                    "page_num": 0,
                    "_page_start": 0,
                    "_page_end": 0,
                    "_fallback_mode": "scan-chess-no-board-candidates",
                }
            )

        toc = [(1, chapter["title"], index + 1) for index, chapter in enumerate(chapters) if chapter.get("title")]
        manual_review_count = len([record for record in chess_fen_records if record.get("requires_review")])
        chess_pgn_records = merge_chess_pgn_continuation_records(chess_pgn_records)
        chess_pgn_summary = summarize_chess_pgn_records(chess_pgn_records)
        ocr_quality = {
            "status": "passed_with_warnings",
            "quality_gate_status": "passed_with_warnings",
            "reason_codes": [
                "selective_ocr_text" if selective_ocr_summary.get("processed_page_count") else "selective_ocr_not_available",
                "scan_chess_crop_review",
            ],
            "fallback_reason": "scan_chess_crop_review",
            "message": "Premium scan-chess cropped board regions and added selective OCR text for notation review.",
            "manual_review_count": manual_review_count,
            "scanned_page_count": len(doc),
            "processed_page_count": len(page_candidates),
            "raw_candidate_count": raw_candidate_total,
            "diagram_crop_count": diagram_total,
            "non_board_rejected_count": non_board_rejected_count,
            "selective_ocr": selective_ocr_summary,
        }
        metadata.update(
            {
                **front_matter_metadata,
                "source_page_count": len(doc),
                "detected_outline_entries": len(doc.get_toc()),
                "figure_summary": {
                    "scan_chess_page_count": len(page_candidates),
                    "raw_candidate_count": raw_candidate_total,
                    "diagram_crop_count": diagram_total,
                    "non_board_rejected_count": non_board_rejected_count,
                },
                "chess_fen": summarize_chess_fen_results(chess_fen_records),
                "ocr_quality": ocr_quality,
                "reading_flow": {
                    "status": "passed_with_warnings",
                    "mode": "scan_chess_crops",
                    "full_page_images_included": False,
                },
                "chess_pgn": chess_pgn_summary,
            }
        )
        extra_artifacts = _scan_chess_pgn_extra_artifacts(
            chess_pgn_records,
            source_title=str(metadata.get("title") or Path(pdf_path).stem),
            diagram_records=chess_html_diagram_records,
        )
        return {
            "success": True,
            "method": "premium-scanned-chess-reflow",
            "text_content": True,
            "layout_mode": "reflowable",
            "chapters": chapters,
            "images": all_images,
            "extra_artifacts": extra_artifacts,
            "toc": toc,
            "metadata": metadata,
            "suppress_auto_cover": True,
            "audit": {
                "status": "passed_with_warnings",
                "elapsed_seconds": round(time.perf_counter() - started, 4),
                "scan_chess": {
                    "cache_enabled": bool(getattr(config, "scanned_chess_cache_enabled", True)),
                    "page_count": len(doc),
                    "pages_with_candidates": len(page_candidates),
                    "raw_candidate_count": raw_candidate_total,
                    "non_board_rejected_count": non_board_rejected_count,
                    "diagram_crop_count": diagram_total,
                    "fen_count": len([record for record in chess_fen_records if record.get("fen")]),
                    "pgn_count": int(chess_pgn_summary.get("valid_pgn_count", 0) or 0),
                    "pgn_candidate_count": int(chess_pgn_summary.get("candidate_game_count", 0) or 0),
                    "manual_review_count": manual_review_count,
                },
            },
        }
    finally:
        doc.close()


def _scan_chess_page_candidates(
    pdf_path: str,
    doc: fitz.Document,
    config: ConversionConfig,
    *,
    piece_templates: dict | None = None,
) -> list[dict[str, Any]]:
    cache_path = _scan_chess_cache_path(pdf_path)
    max_pages = int(getattr(config, "scanned_chess_max_pages", 0) or 0)
    page_limit = len(doc) if max_pages <= 0 else min(len(doc), max_pages)
    max_candidates = max(1, int(getattr(config, "chess_fen_scan_candidates_per_page", 3) or 3))
    min_confidence = float(getattr(config, "scanned_chess_min_grid_confidence", 0.50) or 0.50)
    template_dir = _resolve_chess_piece_template_dir(config) if piece_templates else ""
    cache_key = {
        "version": SCAN_CHESS_PAGE_CANDIDATE_CACHE_VERSION,
        "page_limit": page_limit,
        "max_candidates": max_candidates,
        "min_grid_confidence": round(min_confidence, 4),
        "template_token": _scan_chess_template_cache_token(template_dir) if piece_templates else "",
    }
    cache_enabled = bool(getattr(config, "scanned_chess_cache_enabled", True))
    processed_page_nums: set[int] = set()
    pages_by_num: dict[int, dict[str, Any]] = {}
    if bool(getattr(config, "scanned_chess_cache_enabled", True)) and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                cached_pages = list(cached.get("pages", []) or [])
                if cached.get("complete", True):
                    return cached_pages
                for record in cached_pages:
                    try:
                        page_num = int(record.get("page_num"))
                    except Exception:
                        continue
                    pages_by_num[page_num] = record
                processed_page_nums = {
                    int(page_num)
                    for page_num in (cached.get("processed_page_nums") or [])
                    if isinstance(page_num, int) or str(page_num).isdigit()
                }
                processed_page_nums.update(pages_by_num)
        except Exception:
            pass

    # Grid confidence alone ranks many partial or coordinate-shifted boards
    # above complete boards. Probe a wider pool, then keep the expensive
    # recognition ranking adaptive: ordinary pages usually need only a few
    # candidates, while exercise grids such as 2x3 pages need all visible boards.
    detection_pool_size = max_candidates
    if piece_templates:
        detection_pool_size = max(max_candidates, min(max_candidates * 2, max_candidates + 4))
    for page_num in range(page_limit):
        if page_num in processed_page_nums:
            continue
        try:
            image_data = _page_image_data_for_scan_chess(doc, page_num)
            candidates = detect_board_candidates_in_page_image(
                image_data,
                max_candidates=detection_pool_size,
                min_grid_confidence=min_confidence,
                enable_sliding_probe=False,
            )
        except Exception:
            candidates = []
        candidates = [
            candidate
            for candidate in candidates
            if candidate.board_detected and candidate.bbox
        ]
        page_candidate_limit = _scan_chess_effective_page_candidate_limit(candidates, max_candidates)
        if piece_templates and candidates:
            recognition_pool_size = _scan_chess_recognition_pool_size(
                page_candidate_limit,
                max_candidates=max_candidates,
            )
            candidates = _rank_scan_chess_page_candidates_by_recognition(
                image_data,
                candidates[:recognition_pool_size],
                config=config,
                piece_templates=piece_templates,
            )
        else:
            candidates = sorted(candidates, key=lambda item: item.confidence, reverse=True)
        candidates = candidates[:page_candidate_limit]
        processed_page_nums.add(page_num)
        if candidates:
            pages_by_num[page_num] = {"page_num": page_num, "candidates": [candidate.to_dict() for candidate in candidates]}
        if cache_enabled and (page_num % 5 == 0 or candidates):
            _write_scan_chess_page_candidate_cache(
                cache_path,
                cache_key=cache_key,
                pages_by_num=pages_by_num,
                processed_page_nums=processed_page_nums,
                complete=False,
            )
        if not candidates:
            continue

    pages = [pages_by_num[page_num] for page_num in sorted(pages_by_num)]
    if cache_enabled:
        _write_scan_chess_page_candidate_cache(
            cache_path,
            cache_key=cache_key,
            pages_by_num=pages_by_num,
            processed_page_nums=processed_page_nums,
            complete=True,
        )
    return pages


def _write_scan_chess_page_candidate_cache(
    cache_path: Path,
    *,
    cache_key: dict[str, Any],
    pages_by_num: dict[int, dict[str, Any]],
    processed_page_nums: set[int],
    complete: bool,
) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pages = [pages_by_num[page_num] for page_num in sorted(pages_by_num)]
        cache_path.write_text(
            json.dumps(
                {
                    "cache_key": cache_key,
                    "complete": bool(complete),
                    "processed_page_nums": sorted(processed_page_nums),
                    "pages": pages,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _scan_chess_effective_page_candidate_limit(candidates: list, max_candidates: int) -> int:
    """Return how many page candidates should survive for final scan processing.

    The runtime default allows six boards so multi-exercise pages are not
    truncated, but ranking every page as if it had six real boards is expensive.
    Use cheap geometry to detect regular board grids; keep the wider limit only
    when the page actually looks like a multi-diagram exercise layout.
    """
    max_candidates = max(1, int(max_candidates or 1))
    base_limit = min(3, max_candidates)
    boxes = [_scan_chess_candidate_bbox(candidate) for candidate in candidates]
    boxes = [box for box in boxes if box is not None]
    if len(boxes) <= base_limit or max_candidates <= base_limit:
        return min(max_candidates, max(1, len(boxes) or len(candidates) or base_limit))

    sizes = [min(box[2] - box[0], box[3] - box[1]) for box in boxes if box[2] > box[0] and box[3] > box[1]]
    if not sizes:
        return base_limit
    median_size = sorted(sizes)[len(sizes) // 2]
    if median_size < 80:
        return base_limit
    comparable_boxes = [
        box
        for box in boxes
        if 0.65 * median_size <= min(box[2] - box[0], box[3] - box[1]) <= 1.45 * median_size
    ]
    if len(comparable_boxes) < 4:
        return base_limit

    tolerance = max(40.0, median_size * 0.70)
    x_clusters = _scan_chess_cluster_count(
        [(box[0] + box[2]) / 2.0 for box in comparable_boxes],
        tolerance=tolerance,
    )
    y_clusters = _scan_chess_cluster_count(
        [(box[1] + box[3]) / 2.0 for box in comparable_boxes],
        tolerance=tolerance,
    )
    grid_cells = x_clusters * y_clusters
    if x_clusters >= 2 and y_clusters >= 3 and len(comparable_boxes) >= 5:
        return min(max_candidates, max(6, min(len(comparable_boxes), grid_cells)))
    if x_clusters >= 2 and y_clusters >= 2:
        return min(max_candidates, max(4, min(len(comparable_boxes), grid_cells)))
    if max(x_clusters, y_clusters) >= 4:
        return min(max_candidates, 4)
    return base_limit


def _scan_chess_recognition_pool_size(page_candidate_limit: int, *, max_candidates: int) -> int:
    page_candidate_limit = max(1, int(page_candidate_limit or 1))
    max_candidates = max(1, int(max_candidates or 1))
    if page_candidate_limit >= max_candidates:
        return max(max_candidates, min(max_candidates * 2, max_candidates + 4))
    return min(max_candidates, page_candidate_limit + 1)


def _scan_chess_candidate_bbox(candidate) -> tuple[float, float, float, float] | None:
    bbox = getattr(candidate, "bbox", None)
    if bbox is None and isinstance(candidate, dict):
        bbox = candidate.get("bbox")
    if not bbox or len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in bbox)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _scan_chess_cluster_count(values: list[float], *, tolerance: float) -> int:
    if not values:
        return 0
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return len(clusters)


def _scan_chess_template_cache_token(template_dir: str) -> str:
    if not template_dir:
        return ""
    root = Path(template_dir)
    if not root.exists() or not root.is_dir():
        return str(template_dir)
    try:
        parts = []
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".json"}:
                continue
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
        return hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:16]
    except OSError:
        return str(template_dir)


def _prefer_scan_chess_recognition_result(raw_result, reader_result):
    """Choose the strongest deterministic result across raw and reader crops."""
    def score(result) -> tuple[int, int, float]:
        return (
            1 if getattr(result, "fen", "") else 0,
            1 if getattr(result, "board_detected", False) and not getattr(result, "requires_review", True) else 0,
            float(getattr(result, "confidence", 0.0) or 0.0),
        )

    return reader_result if score(reader_result) > score(raw_result) else raw_result


def _rank_scan_chess_page_candidates_by_recognition(
    image_data: bytes,
    candidates: list,
    *,
    config: ConversionConfig,
    piece_templates: dict,
) -> list:
    try:
        page_image = Image.open(io.BytesIO(image_data)).convert("RGB")
    except Exception:
        return candidates
    min_confidence = float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85)
    multi_diagram_grid = _scan_chess_effective_page_candidate_limit(
        candidates,
        max_candidates=max(6, len(candidates)),
    ) >= 4
    ranked = []
    for order, candidate in enumerate(candidates):
        if not candidate.bbox:
            ranked.append(((0, 0, candidate.confidence, -order), candidate, None))
            continue
        if min(abs(float(candidate.bbox[2]) - float(candidate.bbox[0])), abs(float(candidate.bbox[3]) - float(candidate.bbox[1]))) < 80:
            ranked.append(((0, 0, candidate.confidence, -order), candidate, None))
            continue
        try:
            recognition = _recognize_scan_chess_candidate_bbox(
                page_image,
                candidate.bbox,
                config=config,
                piece_templates=piece_templates,
                min_confidence=min_confidence,
                allow_reader_visible_crop_rescue=False,
            )
            candidate_method = str(getattr(candidate, "method", "") or "")
            if candidate_method == "image-page-board-border-refined" and _scan_chess_recognition_needs_bbox_recovery(recognition):
                recovered = _recover_expanded_scan_chess_candidate(
                    page_image,
                    candidate,
                    config=config,
                    piece_templates=piece_templates,
                    min_confidence=min_confidence,
                )
                if recovered is not None:
                    candidate, recognition = recovered
            allow_shift_recovery = candidate_method not in {
                "image-page-board-border",
                "image-page-board-border-refined",
            } and not multi_diagram_grid
            if allow_shift_recovery and _scan_chess_recognition_needs_bbox_recovery(recognition):
                recovered = _recover_shifted_scan_chess_candidate(
                    page_image,
                    candidate,
                    config=config,
                    piece_templates=piece_templates,
                    min_confidence=min_confidence,
                )
                if recovered is not None:
                    candidate, recognition = recovered
        except Exception:
            ranked.append(((0, 0, candidate.confidence, -order), candidate, None))
            continue
        has_fen = 1 if recognition.fen else 0
        accepted = 1 if recognition.board_detected and not recognition.requires_review else 0
        ranked.append(((has_fen, accepted, recognition.confidence, candidate.confidence, -order), candidate, recognition))
    ranked.sort(key=lambda item: item[0], reverse=True)
    deduped = []
    for _, candidate, recognition in ranked:
        if (
            str(getattr(candidate, "method", "")) == "image-page-board-border"
            and not getattr(recognition, "fen", "")
        ):
            continue
        if candidate.bbox and any(
            existing.bbox and _bbox_overlap_ratio(candidate.bbox, existing.bbox) > 0.55
            for existing in deduped
        ):
            continue
        deduped.append(candidate)
    return deduped


def _recognize_scan_chess_candidate_bbox(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
    *,
    config: ConversionConfig,
    piece_templates: dict,
    min_confidence: float,
    allow_reader_visible_crop_rescue: bool = True,
    reader_bbox: tuple[float, float, float, float] | None = None,
):
    clamped = _clamp_bbox(bbox, page_image.size, pad_ratio=0.0, min_pad=0.0)
    if clamped is None:
        return empty_chess_fen_result(method="image-template-board", warning="candidate_bbox_out_of_bounds", bbox=bbox)
    crop = page_image.crop(clamped)
    output = io.BytesIO()
    crop.save(output, format="PNG")
    recognition = _recognize_scan_chess_crop_with_cache(
        output.getvalue(),
        bbox=bbox,
        min_confidence=min_confidence,
        piece_templates=piece_templates,
    )
    if (
        getattr(recognition, "fen", "")
        and not getattr(recognition, "requires_review", True)
        and float(getattr(recognition, "confidence", 0.0) or 0.0) >= max(0.90, min_confidence + 0.15)
    ):
        return recognition
    if not allow_reader_visible_crop_rescue or not _scan_chess_recognition_needs_bbox_recovery(recognition):
        return recognition

    # The reader-visible EPUB image is generated from a lightly padded bbox.
    # Use that exact prepared crop as the second deterministic probe; otherwise
    # a clipped raw bbox can miss edge kings while the published image clearly
    # contains them.
    reader_clamped = (
        _clamp_bbox(reader_bbox, page_image.size, pad_ratio=0.0, min_pad=0.0)
        if reader_bbox is not None
        else _clamp_bbox(bbox, page_image.size)
    )
    if reader_clamped is None:
        return recognition
    reader_crop = page_image.crop(reader_clamped)
    reader_data, _, _ = _encode_scan_chess_diagram_crop(reader_crop, config)
    reader_recognition = _recognize_scan_chess_crop_with_cache(
        reader_data,
        bbox=tuple(float(value) for value in reader_clamped),
        min_confidence=min_confidence,
        piece_templates=piece_templates,
    )
    if _scan_chess_reader_visible_crop_publish_is_safe(
        recognition,
        reader_recognition,
        min_confidence=min_confidence,
    ):
        return _scan_chess_result_with_warning(reader_recognition, "reader_visible_crop_fen_used")
    sparse_consensus = _scan_chess_sparse_exact_consensus_result(
        recognition,
        reader_recognition,
        min_confidence=min_confidence,
        warning="reader_visible_crop_sparse_consensus_fen_used",
    )
    if sparse_consensus is not None:
        return sparse_consensus
    return recognition


def _recognize_scan_chess_crop_with_cache(
    crop_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float],
    min_confidence: float,
    piece_templates: dict,
):
    cache_path = _scan_chess_recognition_cache_path(
        crop_bytes,
        bbox=bbox,
        min_confidence=min_confidence,
        piece_templates=piece_templates,
    )
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("version") == SCAN_CHESS_RECOGNITION_CACHE_VERSION:
                return _scan_chess_recalibrate_cached_result(
                    _scan_chess_result_from_dict(cached.get("result") or {}),
                    min_confidence=min_confidence,
                )
        for legacy_path in _scan_chess_legacy_recognition_cache_paths(
            crop_bytes,
            bbox=bbox,
            min_confidence=min_confidence,
            piece_templates=piece_templates,
        ):
            if not legacy_path.exists():
                continue
            cached = json.loads(legacy_path.read_text(encoding="utf-8"))
            if cached.get("version") == SCAN_CHESS_RECOGNITION_CACHE_VERSION:
                result = _scan_chess_recalibrate_cached_result(
                    _scan_chess_result_from_dict(cached.get("result") or {}),
                    min_confidence=min_confidence,
                )
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(
                        json.dumps(
                            {
                                "version": SCAN_CHESS_RECOGNITION_CACHE_VERSION,
                                "result": result.to_dict(),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                return result
    except Exception:
        pass
    recognition = recognize_chess_position_from_image(
        crop_bytes,
        bbox=bbox,
        min_confidence=min_confidence,
        piece_templates=piece_templates,
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "version": SCAN_CHESS_RECOGNITION_CACHE_VERSION,
                    "result": recognition.to_dict(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return recognition


def _scan_chess_confirm_final_rendered_crop_recognition(
    recognition,
    crop_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float],
    piece_templates: dict,
    min_confidence: float,
):
    """Confirm uncertain raw recognition against the exact PNG written to EPUB."""
    if not _scan_chess_recognition_needs_bbox_recovery(recognition):
        return recognition
    final_recognition = _recognize_scan_chess_crop_with_cache(
        crop_bytes,
        bbox=bbox,
        min_confidence=min_confidence,
        piece_templates=piece_templates,
    )
    if _scan_chess_reader_visible_crop_publish_is_safe(
        recognition,
        final_recognition,
        min_confidence=min_confidence,
    ):
        return _scan_chess_result_with_warning(final_recognition, "final_rendered_crop_fen_used")
    sparse_consensus = _scan_chess_sparse_exact_consensus_result(
        recognition,
        final_recognition,
        min_confidence=min_confidence,
        warning="final_rendered_crop_sparse_consensus_fen_used",
    )
    if sparse_consensus is not None:
        return sparse_consensus
    return recognition


def _scan_chess_recognition_cache_path(
    crop_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float],
    min_confidence: float,
    piece_templates: dict,
) -> Path:
    rounded_bbox = ",".join(f"{float(value):.2f}" for value in bbox)
    token = "|".join(
        [
            str(SCAN_CHESS_RECOGNITION_CACHE_VERSION),
            _piece_templates_runtime_token(piece_templates),
            rounded_bbox,
            hashlib.sha256(crop_bytes).hexdigest(),
        ]
    )
    digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return Path("output") / "cache" / "scanned_chess" / "recognition" / f"{digest}.json"


def _scan_chess_legacy_recognition_cache_paths(
    crop_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float],
    min_confidence: float,
    piece_templates: dict,
) -> list[Path]:
    """Return cache paths from versions that baked the acceptance threshold in.

    Piece matching produces the same placement/confidence regardless of the
    publication threshold; only FEN publication changes. Reusing older cache
    entries avoids a full 266-page re-recognition pass after safe threshold
    calibration.
    """
    rounded_bbox = ",".join(f"{float(value):.2f}" for value in bbox)
    image_digest = hashlib.sha256(crop_bytes).hexdigest()
    template_token = _piece_templates_runtime_token(piece_templates)
    thresholds = []
    for value in (min_confidence, 0.84, 0.835, 0.85, 0.70):
        formatted = f"{float(value):.3f}"
        if formatted not in thresholds:
            thresholds.append(formatted)
    paths: list[Path] = []
    for threshold in thresholds:
        token = "|".join(
            [
                str(SCAN_CHESS_RECOGNITION_CACHE_VERSION),
                template_token,
                threshold,
                rounded_bbox,
                image_digest,
            ]
        )
        digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:24]
        paths.append(Path("output") / "cache" / "scanned_chess" / "recognition" / f"{digest}.json")
    return paths


def _scan_chess_recalibrate_cached_result(result, *, min_confidence: float):
    """Apply the current FEN acceptance threshold to cached recognition data."""
    if not getattr(result, "board_detected", False):
        return result

    confidence = float(getattr(result, "confidence", 0.0) or 0.0)
    placement = str(getattr(result, "placement", "") or "").strip()
    side_to_move = str(getattr(result, "side_to_move", "") or "w").strip() or "w"
    warnings = {str(warning) for warning in (getattr(result, "warnings", []) or [])}

    disqualifying_warnings = {
        warning
        for warning in warnings
        if warning not in {
            "piece_template_confidence_below_threshold",
            "side_to_move_inferred",
            "side_to_move_marker_detected",
            "dense_board_area_crop_used",
            "final_rendered_crop_fen_used",
            "reader_visible_crop_fen_used",
            "verified_exact_crop_label_used",
        }
        and not warning.startswith("fen:")
    }
    if confidence < float(min_confidence or 0.0) or disqualifying_warnings or not placement:
        if getattr(result, "fen", "") and not getattr(result, "requires_review", True):
            recalibrated_warnings = sorted({*warnings, "piece_template_confidence_below_threshold"})
            return ChessFenResult(
                fen="",
                placement=placement,
                confidence=confidence,
                side_to_move=side_to_move,
                bbox=getattr(result, "bbox", None),
                method=str(getattr(result, "method", "") or "image-template-board"),
                warnings=recalibrated_warnings,
                requires_review=True,
                board_detected=True,
                squares=[dict(square) for square in (getattr(result, "squares", []) or []) if isinstance(square, dict)],
            )
        return result

    fen = f"{placement} {side_to_move} - - 0 1"
    valid, fen_warnings = validate_fen(fen)
    if not valid:
        return result
    recalibrated_warnings = sorted((warnings - {"piece_template_confidence_below_threshold"}) | set(fen_warnings))
    return ChessFenResult(
        fen=fen,
        placement=placement,
        confidence=confidence,
        side_to_move=side_to_move,
        bbox=getattr(result, "bbox", None),
        method=str(getattr(result, "method", "") or "image-template-board"),
        warnings=recalibrated_warnings,
        requires_review=False,
        board_detected=True,
        squares=[dict(square) for square in (getattr(result, "squares", []) or []) if isinstance(square, dict)],
    )


def _piece_templates_runtime_token(piece_templates: dict) -> str:
    cache_key = id(piece_templates)
    cached = _PIECE_TEMPLATE_RUNTIME_TOKENS.get(cache_key)
    if cached:
        return cached
    digest = hashlib.sha256()
    try:
        for label in sorted(piece_templates):
            templates = piece_templates.get(label) or []
            digest.update(str(label).encode("utf-8", errors="ignore"))
            digest.update(str(len(templates)).encode("ascii", errors="ignore"))
            for template in templates:
                digest.update(str(getattr(template, "mode", "")).encode("ascii", errors="ignore"))
                digest.update(str(getattr(template, "size", "")).encode("ascii", errors="ignore"))
                try:
                    digest.update(hashlib.sha1(template.tobytes()).hexdigest().encode("ascii"))
                except Exception:
                    digest.update(repr(template).encode("utf-8", errors="ignore"))
    except Exception:
        digest.update(repr(sorted(piece_templates)).encode("utf-8", errors="ignore"))
    token = digest.hexdigest()[:16]
    _PIECE_TEMPLATE_RUNTIME_TOKENS[cache_key] = token
    return token


def _scan_chess_result_from_dict(data: dict[str, Any]):
    bbox_value = data.get("bbox")
    bbox: tuple[float, float, float, float] | None = None
    if isinstance(bbox_value, (list, tuple)) and len(bbox_value) == 4:
        try:
            bbox = tuple(float(value) for value in bbox_value)  # type: ignore[assignment]
        except Exception:
            bbox = None
    return ChessFenResult(
        fen=str(data.get("fen") or ""),
        placement=str(data.get("placement") or ""),
        confidence=float(data.get("confidence", 0.0) or 0.0),
        side_to_move=str(data.get("side_to_move") or "w"),
        bbox=bbox,
        method=str(data.get("method") or "image-template-board"),
        warnings=list(data.get("warnings") or []),
        requires_review=bool(data.get("requires_review", True)),
        board_detected=bool(data.get("board_detected", False)),
        squares=[dict(square) for square in (data.get("squares") or []) if isinstance(square, dict)],
    )


def _scan_chess_apply_verified_crop_label(
    recognition,
    crop_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float],
    config: ConversionConfig,
):
    """Publish a FEN only when this exact crop has a verified label."""
    if getattr(recognition, "fen", "") and not getattr(recognition, "requires_review", True):
        return recognition
    labels_path = _resolve_chess_verified_crop_labels_path(config)
    if not labels_path:
        return recognition
    labels = _load_verified_crop_labels(labels_path)
    if not labels:
        return recognition
    digest = hashlib.sha256(crop_bytes).hexdigest()
    label = labels.get(digest)
    if not label:
        return recognition
    fen = str(label.get("fen") or "").strip()
    valid, fen_warnings = validate_fen(fen)
    if not valid:
        return recognition
    placement = fen.split()[0]
    side_to_move = fen.split()[1] if len(fen.split()) >= 2 else "w"
    carried_warnings = {
        str(warning)
        for warning in (getattr(recognition, "warnings", []) or [])
        if str(warning) == "side_to_move_inferred"
    }
    warnings = sorted({*carried_warnings, *fen_warnings, "verified_exact_crop_label_used"})
    return ChessFenResult(
        fen=fen,
        placement=placement,
        confidence=1.0,
        side_to_move=side_to_move,
        bbox=bbox,
        method="verified-exact-crop-label",
        warnings=warnings,
        requires_review=False,
        board_detected=True,
        squares=[],
    )


def _load_verified_crop_labels(labels_path: str | Path) -> dict[str, dict[str, Any]]:
    path = Path(labels_path)
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _VERIFIED_CROP_LABEL_CACHE.get(key)
    if cached is not None:
        return cached
    labels: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        digest = str(record.get("sha256") or "").strip().lower()
        fen = str(record.get("fen") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        valid, _warnings = validate_fen(fen)
        if not valid:
            continue
        labels[digest] = record
    _VERIFIED_CROP_LABEL_CACHE.clear()
    _VERIFIED_CROP_LABEL_CACHE[key] = labels
    return labels


def _scan_chess_recognition_needs_bbox_recovery(recognition) -> bool:
    if getattr(recognition, "fen", "") and not getattr(recognition, "requires_review", True):
        return False
    if not getattr(recognition, "board_detected", False):
        return False
    warnings = set(getattr(recognition, "warnings", []) or [])
    return any(str(warning).endswith("king_count_invalid") for warning in warnings) or (
        "sparse_position_confidence_below_threshold" in warnings
    )


def _scan_chess_reader_crop_king_rescue_is_safe(raw_result, reader_result) -> bool:
    """Allow reader-crop rescue only when it adds one missing king.

    Padded reader crops can include edge pieces that raw board crops clipped,
    but they can also pull in coordinate or caption artifacts. Keep this rescue
    deliberately narrow: it may publish only if the accepted reader result
    differs from the raw placement by exactly one empty square becoming the
    single missing king.
    """
    if not getattr(reader_result, "fen", "") or getattr(reader_result, "requires_review", True):
        return False
    raw_cells = _scan_chess_expand_placement(str(getattr(raw_result, "placement", "") or ""))
    reader_cells = _scan_chess_expand_placement(str(getattr(reader_result, "placement", "") or ""))
    if raw_cells is None or reader_cells is None:
        return False

    missing_kings: list[str] = []
    for king in ("K", "k"):
        count = raw_cells.count(king)
        if count == 0:
            missing_kings.append(king)
        elif count != 1:
            return False
    if len(missing_kings) != 1:
        return False

    missing_king = missing_kings[0]
    other_king = "k" if missing_king == "K" else "K"
    if reader_cells.count(missing_king) != 1 or reader_cells.count(other_king) != 1:
        return False

    diffs = [index for index, (raw, reader) in enumerate(zip(raw_cells, reader_cells)) if raw != reader]
    if len(diffs) != 1:
        return False
    diff_index = diffs[0]
    return raw_cells[diff_index] == "" and reader_cells[diff_index] == missing_king


def _scan_chess_reader_visible_crop_publish_is_safe(raw_result, reader_result, *, min_confidence: float) -> bool:
    """Allow FEN from the exact reader crop when the raw bbox clipped pieces.

    Raw detector boxes can sit a few pixels inside the board, especially on
    dense 2x3 exercise pages. The EPUB, however, shows the lightly padded reader
    crop. If that exact displayed crop deterministically recognizes a valid
    board, publishing its FEN is safer than preserving a raw clipped review.
    """
    if not getattr(raw_result, "board_detected", False):
        return False
    raw_warnings = {str(warning) for warning in (getattr(raw_result, "warnings", []) or [])}
    if not getattr(reader_result, "fen", "") or getattr(reader_result, "requires_review", True):
        return False
    if not getattr(reader_result, "board_detected", False):
        return False
    confidence = float(getattr(reader_result, "confidence", 0.0) or 0.0)
    if confidence < max(float(min_confidence or 0.0), 0.80):
        return False

    if "sparse_position_confidence_below_threshold" in raw_warnings and not any(
        warning.endswith("king_count_invalid") for warning in raw_warnings
    ):
        if confidence < max(float(min_confidence or 0.0), 0.83):
            return False
        raw_cells = _scan_chess_expand_placement(str(getattr(raw_result, "placement", "") or ""))
        reader_cells = _scan_chess_expand_placement(str(getattr(reader_result, "placement", "") or ""))
        if raw_cells is None or reader_cells is None:
            return False
        if raw_cells == reader_cells:
            return True
        return (
            confidence >= max(float(min_confidence or 0.0), 0.835)
            and _scan_chess_reader_cells_extend_raw_without_conflict(raw_cells, reader_cells)
        )

    if not any(warning.endswith("king_count_invalid") for warning in raw_warnings):
        return False
    return True


def _scan_chess_sparse_exact_consensus_result(
    raw_result,
    reader_result,
    *,
    min_confidence: float,
    warning: str,
) -> ChessFenResult | None:
    """Promote only sparse positions that agree across raw and visible crops."""
    if not getattr(raw_result, "board_detected", False) or not getattr(reader_result, "board_detected", False):
        return None
    raw_warnings = {str(warning) for warning in (getattr(raw_result, "warnings", []) or [])}
    reader_warnings = {str(warning) for warning in (getattr(reader_result, "warnings", []) or [])}
    if "sparse_position_confidence_below_threshold" not in raw_warnings:
        return None
    if any(warning.endswith("king_count_invalid") for warning in raw_warnings | reader_warnings):
        return None
    if "piece_template_confidence_below_threshold" in raw_warnings | reader_warnings:
        return None

    raw_cells = _scan_chess_expand_placement(str(getattr(raw_result, "placement", "") or ""))
    reader_cells = _scan_chess_expand_placement(str(getattr(reader_result, "placement", "") or ""))
    if raw_cells is None or reader_cells is None or raw_cells != reader_cells:
        return None
    if raw_cells.count("K") != 1 or raw_cells.count("k") != 1:
        return None
    piece_count = sum(1 for cell in raw_cells if cell)
    if piece_count < SCAN_CHESS_SPARSE_EXACT_CONSENSUS_MIN_PIECES:
        return None
    if piece_count > SCAN_CHESS_SPARSE_EXACT_CONSENSUS_MAX_PIECES:
        return None

    raw_confidence = float(getattr(raw_result, "confidence", 0.0) or 0.0)
    reader_confidence = float(getattr(reader_result, "confidence", 0.0) or 0.0)
    if raw_confidence < max(float(min_confidence or 0.0), 0.80):
        return None
    if reader_confidence < max(float(min_confidence or 0.0), SCAN_CHESS_SPARSE_EXACT_CONSENSUS_MIN_CONFIDENCE):
        return None

    placement = str(getattr(raw_result, "placement", "") or "").strip()
    side_to_move = str(getattr(raw_result, "side_to_move", "") or getattr(reader_result, "side_to_move", "") or "w")
    fen = f"{placement} {side_to_move} - - 0 1"
    valid, fen_warnings = validate_fen(fen)
    if not valid:
        return None

    warnings = sorted(
        {
            *raw_warnings,
            *reader_warnings,
            *fen_warnings,
            "sparse_exact_crop_consensus",
            warning,
        }
    )
    return ChessFenResult(
        fen=fen,
        placement=placement,
        confidence=max(raw_confidence, reader_confidence),
        side_to_move=side_to_move,
        bbox=getattr(reader_result, "bbox", None) or getattr(raw_result, "bbox", None),
        method=str(getattr(reader_result, "method", "") or getattr(raw_result, "method", "") or "image-template-board"),
        warnings=warnings,
        requires_review=False,
        board_detected=True,
        squares=[dict(square) for square in (getattr(reader_result, "squares", []) or []) if isinstance(square, dict)],
    )


def _scan_chess_reader_cells_extend_raw_without_conflict(raw_cells: list[str], reader_cells: list[str]) -> bool:
    added = 0
    for raw_cell, reader_cell in zip(raw_cells, reader_cells):
        if raw_cell == reader_cell:
            continue
        if raw_cell == "" and reader_cell:
            added += 1
            continue
        return False
    return 0 < added <= 4


def _scan_chess_expand_placement(placement_or_fen: str) -> list[str] | None:
    placement = str(placement_or_fen or "").split()[0]
    rows = placement.split("/")
    if len(rows) != 8:
        return None
    cells: list[str] = []
    for row in rows:
        row_cells: list[str] = []
        for char in row:
            if char.isdigit():
                row_cells.extend([""] * int(char))
            elif char.isalpha():
                row_cells.append(char)
            else:
                return None
        if len(row_cells) != 8:
            return None
        cells.extend(row_cells)
    return cells


def _scan_chess_result_with_warning(result, warning: str):
    warnings = sorted(set([*list(getattr(result, "warnings", []) or []), warning]))
    return ChessFenResult(
        fen=str(getattr(result, "fen", "") or ""),
        placement=str(getattr(result, "placement", "") or ""),
        confidence=float(getattr(result, "confidence", 0.0) or 0.0),
        side_to_move=str(getattr(result, "side_to_move", "w") or "w"),
        bbox=getattr(result, "bbox", None),
        method=str(getattr(result, "method", "") or "image-template-board"),
        warnings=warnings,
        requires_review=bool(getattr(result, "requires_review", True)),
        board_detected=bool(getattr(result, "board_detected", False)),
        squares=[dict(square) for square in (getattr(result, "squares", []) or []) if isinstance(square, dict)],
    )


def _recover_shifted_scan_chess_candidate(
    page_image: Image.Image,
    candidate,
    *,
    config: ConversionConfig,
    piece_templates: dict,
    min_confidence: float,
):
    if not candidate.bbox:
        return None
    best: tuple[float, Any, Any] | None = None
    for bbox in _scan_chess_vertical_recovery_bboxes(candidate.bbox, page_image.size):
        recognition = _recognize_scan_chess_candidate_bbox(
            page_image,
            bbox,
            config=config,
            piece_templates=piece_templates,
            min_confidence=min_confidence,
            allow_reader_visible_crop_rescue=False,
        )
        if not getattr(recognition, "fen", "") or getattr(recognition, "requires_review", True):
            continue
        score = float(getattr(recognition, "confidence", 0.0) or 0.0)
        if best is None or score > best[0]:
            recovered_candidate = ChessFenResult(
                confidence=max(float(getattr(candidate, "confidence", 0.0) or 0.0), score),
                bbox=bbox,
                method="image-page-board-shift-recovered",
                warnings=list(getattr(candidate, "warnings", []) or []) + ["shifted_board_bbox_recovered"],
                requires_review=False,
                board_detected=True,
            )
            best = (score, recovered_candidate, recognition)
    if best is None:
        return None
    return best[1], best[2]


def _recover_expanded_scan_chess_candidate(
    page_image: Image.Image,
    candidate,
    *,
    config: ConversionConfig,
    piece_templates: dict,
    min_confidence: float,
):
    if not candidate.bbox:
        return None
    best: tuple[float, Any, Any] | None = None
    for bbox in _scan_chess_local_expansion_bboxes(candidate.bbox, page_image.size):
        recognition = _recognize_scan_chess_candidate_bbox(
            page_image,
            bbox,
            config=config,
            piece_templates=piece_templates,
            min_confidence=min_confidence,
            allow_reader_visible_crop_rescue=False,
        )
        if not getattr(recognition, "fen", "") or getattr(recognition, "requires_review", True):
            continue
        if _scan_chess_piece_count(str(getattr(recognition, "placement", "") or getattr(recognition, "fen", ""))) <= 8:
            continue
        score = float(getattr(recognition, "confidence", 0.0) or 0.0)
        if best is None or score > best[0]:
            recovered_candidate = ChessFenResult(
                confidence=max(float(getattr(candidate, "confidence", 0.0) or 0.0), score),
                bbox=bbox,
                method="image-page-board-border-expanded",
                warnings=list(getattr(candidate, "warnings", []) or []) + ["full_board_bbox_expanded"],
                requires_review=False,
                board_detected=True,
            )
            best = (score, recovered_candidate, recognition)
    if best is None:
        return None
    return best[1], best[2]


def _scan_chess_piece_count(placement_or_fen: str) -> int:
    placement = str(placement_or_fen or "").split()[0]
    return sum(1 for char in placement if char.isalpha())


def _scan_chess_local_expansion_bboxes(
    bbox: tuple[float, float, float, float],
    page_size: tuple[int, int],
) -> list[tuple[float, float, float, float]]:
    """Generate tiny full-board expansions for border crops clipped by labels.

    This is deliberately narrower than vertical rank-shift recovery. It keeps
    the detected right edge stable and only expands the square by a few percent,
    which covers cases where rank/file labels caused the crop to start a little
    too far inside the board without searching neighboring partial boards.
    """
    x0, y0, x1, y1 = (float(value) for value in bbox)
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)
    if width < 120 or height < width * 0.92 or height > width * 1.08:
        return []
    side = max(width, height)
    page_width, page_height = page_size
    candidates: list[tuple[float, float, float, float]] = []
    for factor in (1.024, 1.0265, 1.03):
        expanded_side = side * factor
        extra = expanded_side - side
        left = x1 - expanded_side
        top = y0 - extra * 0.33
        right = left + expanded_side
        bottom = top + expanded_side
        if left < 0 or top < 0 or right > page_width or bottom > page_height:
            continue
        recovered = (left, top, right, bottom)
        if any(all(abs(value - other) < 0.5 for value, other in zip(recovered, existing)) for existing in candidates):
            continue
        candidates.append(recovered)
    return candidates


def _scan_chess_vertical_recovery_bboxes(
    bbox: tuple[float, float, float, float],
    page_size: tuple[int, int],
) -> list[tuple[float, float, float, float]]:
    """Generate local full-board candidates for crops shifted into coordinates.

    Some scanned chess pages have rank/file labels dense enough that the first
    detector locks onto ranks 6-1 plus the coordinate baseline. The x-axis is
    usually still reliable, so try a small, bounded set of upward rank shifts
    and tiny horizontal corrections. FEN acceptance still requires deterministic
    piece recognition; these candidates are never published on geometry alone.
    """
    x0, y0, x1, y1 = (float(value) for value in bbox)
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)
    if width < 120 or height < width * 0.80 or height > width * 1.18:
        return []
    side = width
    cell = side / 8.0
    if y0 < cell * 1.5:
        return []
    page_width, page_height = page_size
    candidates: list[tuple[float, float, float, float]] = []
    for up_cells in (2.0, 1.75, 2.25, 3.0):
        top = y0 - cell * up_cells
        if top < 0:
            continue
        for dx_cells in (0.075, 0.10, 0.0, -0.075, 0.125):
            left = x0 + cell * dx_cells
            right = left + side
            bottom = top + side
            if left < 0 or right > page_width or bottom > page_height:
                continue
            recovered = (left, top, right, bottom)
            if any(_bbox_overlap_ratio(recovered, existing) > 0.98 for existing in candidates):
                continue
            candidates.append(recovered)
    return candidates


def _scan_chess_front_matter_metadata(
    pdf_path: str,
    doc: fitz.Document,
    config: ConversionConfig,
) -> dict[str, Any]:
    if not bool(getattr(config, "scanned_chess_ocr_enabled", True)):
        return {}
    max_pages = max(0, int(getattr(config, "scanned_chess_front_matter_ocr_pages", 4) or 0))
    if max_pages <= 0:
        return {}

    cache_path = _scan_chess_front_matter_cache_path(pdf_path)
    cache_key = {
        "version": SCAN_CHESS_CACHE_VERSION,
        "pages": max_pages,
        "language": str(getattr(config, "ocr_language", "eng") or "eng"),
    }
    if bool(getattr(config, "scanned_chess_cache_enabled", True)) and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                return dict(cached.get("metadata", {}) or {})
        except Exception:
            pass

    try:
        from ocr_module import ocr_with_tesseract
    except Exception:
        return {}

    ocr_pages: list[dict[str, Any]] = []
    for page_num in range(min(max_pages, len(doc))):
        try:
            image_data = _page_image_data_for_scan_chess(doc, page_num)
            page_image = Image.open(io.BytesIO(image_data)).convert("L")
            page_image = ImageOps.autocontrast(page_image)
            page_image = _resize_image_to_long_edge(page_image, 1600, resample=Image.Resampling.LANCZOS)
            text, confidence = ocr_with_tesseract(page_image, str(getattr(config, "ocr_language", "eng") or "eng"))
        except Exception:
            continue
        normalized = _normalize_scan_chess_ocr_text(text)
        if not normalized.strip():
            continue
        ocr_pages.append(
            {
                "page_num": page_num,
                "confidence": round(float(confidence or 0.0), 3),
                "text": normalized,
            }
        )

    metadata = _infer_scan_chess_front_matter_metadata(ocr_pages)
    if metadata:
        metadata["front_matter_ocr"] = {
            "status": "passed",
            "processed_page_count": len(ocr_pages),
            "avg_confidence": round(
                sum(float(page.get("confidence", 0.0) or 0.0) for page in ocr_pages) / max(1, len(ocr_pages)),
                3,
            ),
            "source": "scan_chess_front_matter_ocr",
        }

    if bool(getattr(config, "scanned_chess_cache_enabled", True)):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"cache_key": cache_key, "metadata": metadata}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    return metadata


def _infer_scan_chess_front_matter_metadata(ocr_pages: list[dict[str, Any]]) -> dict[str, Any]:
    lines: list[str] = []
    for page in ocr_pages:
        for line in str(page.get("text") or "").splitlines():
            normalized = re.sub(r"\s+", " ", line).strip(" \t\r\n\u00a0")
            if normalized:
                lines.append(normalized)
    if not lines:
        return {}

    author = _infer_front_matter_author(lines)
    publisher = _infer_front_matter_publisher(lines)
    title = _infer_front_matter_title(lines, author=author, publisher=publisher)
    date = _infer_front_matter_date(lines)
    metadata: dict[str, Any] = {}
    if title:
        metadata["inferred_publication_title"] = title
        metadata["title"] = title
    if author:
        metadata["author"] = author
    if publisher:
        metadata["publisher"] = publisher
    if date:
        metadata["date"] = date
    if title or author or publisher:
        pieces = [item for item in (title, f"by {author}" if author else "", publisher) if item]
        metadata["description"] = ". ".join(pieces)
        metadata["subjects"] = ["Chess", "Chess training"]
        metadata["subject"] = "Chess; Chess training"
        metadata["metadata_inference"] = {
            "title": ["scan-front-matter-ocr"] if title else [],
            "author": ["scan-front-matter-ocr"] if author else [],
            "publisher": ["scan-front-matter-ocr"] if publisher else [],
        }
    return metadata


def _infer_front_matter_author(lines: list[str]) -> str:
    copyright_candidates: list[str] = []
    for line in lines[:80]:
        match = re.search(
            r"(?i)\bcopyright\s*(?:©|\(c\))?\s*(?:\d{4}(?:\s*[-,]\s*\d{4})?\s*)?(?P<name>[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\b",
            line,
        )
        if match:
            copyright_candidates.append(_title_case_person_name(match.group("name")))
    if copyright_candidates:
        return copyright_candidates[0]

    for line in lines[:40]:
        cleaned = line.strip(" .;:-")
        if _front_matter_line_is_noise(cleaned):
            continue
        if re.fullmatch(r"[A-Z][A-Z.'-]+(?:\s+[A-Z][A-Z.'-]+){1,3}", cleaned):
            return _title_case_person_name(cleaned)
        if _looks_like_person_name(cleaned) and not _looks_like_publisher_line(cleaned):
            return cleaned
    return ""


def _infer_front_matter_title(lines: list[str], *, author: str, publisher: str) -> str:
    best_fragments: list[str] = []
    best_score = -1
    for start in range(min(len(lines), 60)):
        fragments: list[str] = []
        for line in lines[start : start + 6]:
            cleaned = line.strip(" .;:-")
            if not cleaned or _front_matter_line_is_noise(cleaned):
                if fragments:
                    break
                continue
            if _same_normalized_text(cleaned, author) or _same_normalized_text(cleaned, publisher):
                if fragments:
                    break
                continue
            if _looks_like_publisher_line(cleaned):
                if fragments:
                    break
                continue
            if not _looks_like_front_title_fragment(cleaned, author=author):
                if fragments:
                    break
                continue
            fragments.append(_normalize_front_title_fragment(cleaned))
            if len(fragments) >= 4:
                break
        score = _score_front_title_fragments(fragments)
        if score > best_score:
            best_score = score
            best_fragments = fragments
    return _join_front_title_fragments(best_fragments)


def _infer_front_matter_publisher(lines: list[str]) -> str:
    for line in lines[:90]:
        cleaned = line.strip(" .;:-")
        if _front_matter_line_is_noise(cleaned):
            continue
        if _looks_like_publisher_line(cleaned):
            return re.sub(r"(?i)\s+(?:UK|USA)?\s*(?:LLP|LLC|Ltd\.?|Inc\.?)$", "", cleaned).strip(" .;:-")
    return ""


def _infer_front_matter_date(lines: list[str]) -> str:
    years: list[int] = []
    for line in lines[:80]:
        if re.search(r"(?i)\b(?:copyright|edition|published|printed)\b", line):
            years.extend(int(match.group(0)) for match in re.finditer(r"\b(?:19|20)\d{2}\b", line))
    return str(max(years)) if years else ""


def _looks_like_person_name(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3}", text or ""))


def _title_case_person_name(text: str) -> str:
    words = []
    for word in re.split(r"\s+", text.strip()):
        if not word:
            continue
        words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def _same_normalized_text(left: str, right: str) -> bool:
    if not left or not right:
        return False
    normalize = lambda value: re.sub(r"[^a-z0-9]+", "", value.lower())
    return normalize(left) == normalize(right)


def _front_matter_line_is_noise(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return True
    return bool(
        re.search(
            r"\b(?:copyright|all rights reserved|isbn|translated by|typeset|printed|distributed|website|e-mail|www\.|http|contents|key to symbols|preface|introduction)\b",
            lowered,
        )
    )


def _looks_like_publisher_line(text: str) -> bool:
    return bool(
        re.search(
            r"(?i)\b(?:press|publishing|publisher|books|quality chess|university|verlag|editions?|llp|llc|ltd|inc)\b",
            text or "",
        )
    )


def _looks_like_front_title_fragment(text: str, *, author: str) -> bool:
    if not text or len(text) > 90:
        return False
    if (
        _looks_like_person_name(text)
        and not text.lower().startswith("with ")
        and not re.search(r"(?i)\b(?:chess|fundamentals|tactics|endgame|manual|course|lessons?|training|build|play|openings?)\b", text)
    ):
        return False
    if text.isupper() and len(text.split()) <= 4 and not re.search(r"(?i)\b(?:fundamentals|chess|tactics|endgame|manual|course)\b", text):
        return False
    if text.lower().startswith("with ") and author:
        return bool(re.search(re.escape(author.split()[-1]), text, flags=re.IGNORECASE))
    return bool(re.search(r"(?i)\b(?:chess|fundamentals|tactics|endgame|manual|course|lessons?|training|build|play|openings?)\b", text))


def _normalize_front_title_fragment(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" .;:-")
    if cleaned.isupper() and len(cleaned) > 3:
        return " ".join(word[:1].upper() + word[1:].lower() for word in cleaned.split())
    return cleaned


def _score_front_title_fragments(fragments: list[str]) -> int:
    if not fragments:
        return -1
    joined = " ".join(fragments)
    score = len(joined)
    score += 40 * len(fragments)
    if re.search(r"(?i)\bchess\b", joined):
        score += 80
    if re.search(r"(?i)\bfundamentals?\b", joined):
        score += 50
    return score


def _join_front_title_fragments(fragments: list[str]) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        key = re.sub(r"[^a-z0-9]+", "", fragment.lower())
        if not fragment or key in seen:
            continue
        seen.add(key)
        cleaned.append(fragment)
    if not cleaned:
        return ""
    if (
        len(cleaned) >= 3
        and re.search(r"(?i)\b(?:fundamentals?|volume|part|book)\b", cleaned[0])
        and any(re.search(r"(?i)\b(?:chess|manual|course|training|build)\b", fragment) for fragment in cleaned[1:])
    ):
        cleaned = cleaned[1:] + [cleaned[0]]
    if len(cleaned) == 1:
        return cleaned[0]
    return f"{' '.join(cleaned[:-1])}: {cleaned[-1]}"


def _scan_chess_selective_ocr_pages(
    pdf_path: str,
    doc: fitz.Document,
    page_candidates: list[dict[str, Any]],
    config: ConversionConfig,
) -> dict[str, Any]:
    if not bool(getattr(config, "scanned_chess_ocr_enabled", True)):
        return {
            "pages": {},
            "summary": {
                "status": "skipped",
                "reason": "disabled",
                "processed_page_count": 0,
                "notation_token_count": 0,
            },
        }

    max_pages = int(getattr(config, "scanned_chess_ocr_max_pages", 32) or 0)
    if bool(getattr(config, "force_ocr", False)):
        max_pages = 0
    candidate_records = [record for record in page_candidates if record.get("candidates")]
    selected_records = candidate_records if max_pages <= 0 else candidate_records[:max_pages]

    cache_path = _scan_chess_ocr_cache_path(pdf_path)
    cache_key = {
        "version": SCAN_CHESS_CACHE_VERSION,
        "long_edge": int(getattr(config, "scanned_chess_ocr_long_edge", 1800) or 1800),
        "language": str(getattr(config, "ocr_language", "eng") or "eng"),
        "max_pages": max_pages,
        "candidate_page_count": len(candidate_records),
        "selected_page_count": len(selected_records),
    }
    if bool(getattr(config, "scanned_chess_cache_enabled", True)) and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                return {
                    "pages": dict(cached.get("pages", {}) or {}),
                    "summary": dict(cached.get("summary", {}) or {}),
                }
        except Exception:
            pass

    pages: dict[str, dict[str, Any]] = {}
    unavailable_reason = ""
    try:
        from ocr_module import ocr_with_tesseract
    except Exception as exc:
        ocr_with_tesseract = None
        unavailable_reason = str(exc)

    processed = 0
    low_confidence = 0
    notation_token_count = 0
    text_char_count = 0
    min_confidence = float(getattr(config, "scanned_chess_ocr_min_confidence", 0.35) or 0.35)

    if ocr_with_tesseract is not None:
        for page_record in selected_records:
            page_num = int(page_record.get("page_num", 0))
            try:
                image_data = _page_image_data_for_scan_chess(doc, page_num)
                page_image = Image.open(io.BytesIO(image_data)).convert("RGB")
            except Exception:
                continue
            ocr_image = _mask_scan_chess_boards_for_ocr(page_image, list(page_record.get("candidates", []) or []))
            max_edge = max(900, int(getattr(config, "scanned_chess_ocr_long_edge", 1800) or 1800))
            ocr_image = _resize_image_to_long_edge(ocr_image, max_edge, resample=Image.Resampling.LANCZOS)
            try:
                text, confidence = ocr_with_tesseract(ocr_image, str(getattr(config, "ocr_language", "eng") or "eng"))
            except Exception as exc:
                unavailable_reason = str(exc)
                continue
            normalized_text = _normalize_scan_chess_ocr_text(text)
            if not normalized_text.strip():
                continue
            token_count = len(NOTATION_TOKEN_RE.findall(normalized_text))
            record = {
                "page_num": page_num,
                "text": normalized_text,
                "confidence": round(float(confidence or 0.0), 3),
                "notation_token_count": token_count,
                "requires_review": float(confidence or 0.0) < min_confidence,
            }
            pages[str(page_num)] = record
            processed += 1
            text_char_count += len(normalized_text)
            notation_token_count += token_count
            if record["requires_review"]:
                low_confidence += 1

    summary = {
        "status": "passed_with_warnings" if low_confidence or len(selected_records) < len(candidate_records) else "passed",
        "engine": "tesseract" if ocr_with_tesseract is not None else "unavailable",
        "processed_page_count": processed,
        "candidate_page_count": len(candidate_records),
        "skipped_page_count": max(0, len(candidate_records) - len(selected_records)),
        "low_confidence_page_count": low_confidence,
        "notation_token_count": notation_token_count,
        "text_char_count": text_char_count,
        "unavailable_reason": unavailable_reason,
    }
    if bool(getattr(config, "scanned_chess_cache_enabled", True)):
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps({"cache_key": cache_key, "pages": pages, "summary": summary}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    return {"pages": pages, "summary": summary}


def _mask_scan_chess_boards_for_ocr(image: Image.Image, candidates: list[dict[str, Any]]) -> Image.Image:
    masked = ImageOps.autocontrast(image.convert("L"))
    draw = ImageDraw.Draw(masked)
    for candidate in candidates:
        bbox = _clamp_bbox(candidate.get("bbox"), image.size)
        if bbox is None:
            continue
        pad = max(8, int(max(image.size) * 0.003))
        draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad), fill=255)
    return masked


def _normalize_scan_chess_ocr_text(text: str) -> str:
    normalized_lines: list[str] = []
    for raw_line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            if normalized_lines and normalized_lines[-1]:
                normalized_lines.append("")
            continue
        line = line.replace("\u2020", "+").replace("\u2021", "#")
        line = re.sub(r"(?<!\w)[@&]2(?=x?[a-h][1-8])", "K", line)
        line = re.sub(r"(?<!\w)[28](?=x?[g-h][1-2])", "K", line)
        line = re.sub(r"(?<!\w)D(?=x?[a-h][1-8])", "Q", line)
        line = re.sub(r"(?<!\w)W(?=x?[a-h][1-8])", "R", line)
        line = re.sub(r"(?<!\w)A(?=x?[a-h][1-8])", "N", line)
        line = re.sub(r"(?<!\w)&(?=x?[a-h][1-8])", "B", line)
        line = re.sub(r"(?<!\w)@(?=x?[a-h][1-8])", "K", line)
        line = re.sub(r"(?<!\w)0(?=[a-h][1-8])", "N", line)
        line = re.sub(r"\b([KQRBN]?[a-h][1-8])t\b", r"\1+", line)
        line = re.sub(r"\b([KQRBN]x?[a-h][1-8])t\b", r"\1+", line)
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        normalized_lines.append(line)
    while normalized_lines and not normalized_lines[-1]:
        normalized_lines.pop()
    return "\n".join(normalized_lines)


def _scan_chess_ocr_html_parts(ocr_record: dict[str, Any], *, page_num: int) -> list[str]:
    text = str(ocr_record.get("text") or "").strip()
    if not text:
        return []
    confidence = float(ocr_record.get("confidence", 0.0) or 0.0)
    parts: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if not paragraph:
            return
        merged = " ".join(paragraph).strip()
        paragraph.clear()
        if not merged:
            return
        classes = ["scan-chess-ocr-text"]
        if _is_notation_heavy_line(merged):
            classes.append("notation-heavy")
            classes.append("chess-notation-text")
        elif re.match(r"(?i)^diagram\s+\d+", merged):
            classes.append("diagram-caption")
        parts.append(
            f'<p class="{" ".join(classes)}" data-ocr-confidence="{confidence:.3f}">'
            f"{html_module.escape(merged)}"
            "</p>"
        )

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if paragraph and (re.match(r"(?i)^(diagram|ex\\.|exercise|\\d+\\.)", stripped) or _is_notation_heavy_line(stripped)):
            flush()
        paragraph.append(stripped)
    flush()
    if not parts:
        return []
    parts.insert(
        0,
        f'<p class="scan-chess-ocr-marker">OCR strony {page_num + 1}: tekst i notacja rozpoznane automatycznie; wymagaja kontroli.</p>',
    )
    return parts


def _scan_chess_pgn_extra_artifacts(records: list, *, source_title: str) -> list[dict[str, Any]]:
    record_list = [record for record in records if getattr(record, "pgn", "").strip()]
    if not record_list:
        return []
    pgn_text = build_combined_pgn(record_list)
    html_text = build_pgn_download_html(
        record_list,
        title=f"{source_title or 'Chess'} - PGN and FEN",
    )
    artifacts: list[dict[str, Any]] = []
    if pgn_text.strip():
        artifacts.append(
            {
                "key": "chess_pgn",
                "filename": "chess_games.pgn",
                "content_type": "application/x-chess-pgn; charset=utf-8",
                "data": pgn_text.encode("utf-8"),
                "label": "PGN",
            }
        )
    artifacts.append(
        {
            "key": "chess_pgn_html",
            "filename": "chess_games.html",
            "content_type": "text/html; charset=utf-8",
            "data": html_text.encode("utf-8"),
            "label": "HTML PGN/FEN",
        }
    )
    return artifacts


def _encode_scan_chess_diagram_crop(image: Image.Image, config: ConversionConfig) -> tuple[bytes, int, int]:
    variants = _scan_chess_diagram_preprocess_variants(image, config)
    return _encode_scan_chess_preprocessed_image(variants["reader_enhanced"], config)


def _scan_chess_cache_path(pdf_path: str) -> Path:
    path = Path(pdf_path)
    try:
        stat = path.stat()
        token = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        token = str(path)
    digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return Path("output") / "cache" / "scanned_chess" / f"{path.stem}-{digest}.json"


def _scan_chess_ocr_cache_path(pdf_path: str) -> Path:
    path = Path(pdf_path)
    try:
        stat = path.stat()
        token = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:ocr"
    except OSError:
        token = f"{path}:ocr"
    digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return Path("output") / "cache" / "scanned_chess" / f"{path.stem}-{digest}-ocr.json"


def _scan_chess_front_matter_cache_path(pdf_path: str) -> Path:
    path = Path(pdf_path)
    try:
        stat = path.stat()
        token = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:front-matter"
    except OSError:
        token = f"{path}:front-matter"
    digest = hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return Path("output") / "cache" / "scanned_chess" / f"{path.stem}-{digest}-front-matter.json"


def _page_image_data_for_scan_chess(doc: fitz.Document, page_num: int) -> bytes:
    page = doc[page_num]
    images = page.get_images(full=True)
    if images:
        largest: tuple[int, dict[str, Any]] | None = None
        for image_info in images:
            try:
                base_image = doc.extract_image(image_info[0])
            except Exception:
                continue
            data = base_image.get("image")
            if not data:
                continue
            area = int(base_image.get("width", 0) or 0) * int(base_image.get("height", 0) or 0)
            if largest is None or area > largest[0]:
                largest = (area, base_image)
        if largest is not None:
            return largest[1]["image"]
    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    return pix.tobytes("png")


def _clamp_bbox(
    raw_bbox: Any,
    image_size: tuple[int, int],
    *,
    pad_ratio: float = 0.025,
    min_pad: float = 4.0,
) -> tuple[int, int, int, int] | None:
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    width, height = image_size
    try:
        x0, y0, x1, y1 = [float(value) for value in raw_bbox]
    except (TypeError, ValueError):
        return None
    pad = max(float(min_pad), min(x1 - x0, y1 - y0) * max(0.0, float(pad_ratio)))
    left = max(0, int(round(x0 - pad)))
    top = max(0, int(round(y0 - pad)))
    right = min(width, int(round(x1 + pad)))
    bottom = min(height, int(round(y1 + pad)))
    if right - left < 40 or bottom - top < 40:
        return None
    return left, top, right, bottom


def _scan_chess_fen_payload(candidate: dict[str, Any], recognition) -> dict[str, Any]:
    payload = recognition.to_dict()
    if payload.get("board_detected"):
        return payload
    return _scan_chess_candidate_review_payload(candidate)


def _scan_chess_candidate_review_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    warnings = list(candidate.get("warnings") or [])
    if "image_board_requires_review" not in warnings:
        warnings.append("image_board_requires_review")
    return {
        "fen": "",
        "placement": "",
        "confidence": float(candidate.get("confidence", 0.0) or 0.0),
        "side_to_move": "w",
        "bbox": candidate.get("bbox"),
        "method": str(candidate.get("method") or "image-page-board-candidate"),
        "warnings": warnings,
        "requires_review": True,
        "board_detected": True,
    }


def _apply_scan_chess_side_to_move_marker(payload: dict[str, Any], side_to_move: str) -> dict[str, Any]:
    side = "b" if str(side_to_move).lower().startswith("b") else "w"
    updated = dict(payload)
    updated["side_to_move"] = side
    fen = str(updated.get("fen") or "").strip()
    if fen:
        parts = fen.split()
        if len(parts) == 6:
            parts[1] = side
            updated["fen"] = " ".join(parts)
    warnings = [warning for warning in list(updated.get("warnings") or []) if warning != "side_to_move_inferred"]
    if "side_to_move_marker_detected" not in warnings:
        warnings.append("side_to_move_marker_detected")
    updated["warnings"] = sorted(set(warnings))
    return updated


def _infer_scan_chess_side_to_move(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> str:
    """Infer side-to-move from a triangle marker near a scanned board.

    In the Yusupov-style exercise pages, an outlined triangle denotes White to
    move and a filled black triangle denotes Black to move. The signal is
    deliberately treated as optional: if no compact triangular component is
    found, the caller keeps the conservative inferred default.
    """
    region = _scan_chess_side_marker_region(bbox, page_image.size)
    if region is None:
        return ""
    crop = ImageOps.autocontrast(page_image.crop(region).convert("L"))
    dark = np.asarray(crop) < 120
    component = _scan_chess_best_side_marker_component(dark)
    if component is None:
        return ""
    density = component["density"]
    return "b" if density >= 0.36 else "w"


def _scan_chess_side_marker_region(
    bbox: tuple[float, float, float, float],
    page_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    side = max(1.0, min(x1 - x0, y1 - y0))
    page_width, page_height = page_size
    left = int(max(0, round(x1 - side * 0.24)))
    right = int(min(page_width, round(x1 + side * 0.04)))
    top = int(max(0, round(y0)))
    bottom = int(min(page_height, round(y0 + side * 0.14)))
    if right - left < 20 or bottom - top < 20:
        return None
    return left, top, right, bottom


def _scan_chess_best_side_marker_component(mask: Any) -> dict[str, float] | None:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    best: dict[str, float] | None = None
    for start_y in range(height):
        for start_x in range(width):
            if visited[start_y, start_x] or not mask[start_y, start_x]:
                continue
            stack = [(start_x, start_y)]
            visited[start_y, start_x] = True
            area = 0
            min_x = max_x = start_x
            min_y = max_y = start_y
            while stack:
                x, y = stack.pop()
                area += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for nx in (x - 1, x, x + 1):
                    for ny in (y - 1, y, y + 1):
                        if nx == x and ny == y:
                            continue
                        if nx < 0 or ny < 0 or nx >= width or ny >= height:
                            continue
                        if visited[ny, nx] or not mask[ny, nx]:
                            continue
                        visited[ny, nx] = True
                        stack.append((nx, ny))
            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            if box_width < max(12, width * 0.20) or box_height < max(12, height * 0.22):
                continue
            if box_width > width * 0.62 or box_height > height * 0.72:
                continue
            aspect = box_width / max(1, box_height)
            if aspect < 0.72 or aspect > 1.42:
                continue
            center_x = (min_x + max_x) / 2.0
            center_y = (min_y + max_y) / 2.0
            if center_x < width * 0.20 or center_x > width * 0.80:
                continue
            if center_y > height * 0.66 or max_y > height * 0.82:
                continue
            density = area / max(1, box_width * box_height)
            if density < 0.16 or density > 0.72:
                continue
            score = area * (1.0 - abs(center_x / max(1, width) - 0.48)) * (
                1.0 - min(0.5, center_y / max(1, height))
            )
            candidate = {
                "area": float(area),
                "density": float(density),
                "bbox": (float(min_x), float(min_y), float(max_x), float(max_y)),
                "aspect": float(aspect),
                "score": float(score),
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
    return best


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
    piece_templates = {}
    template_dir = _resolve_chess_piece_template_dir(config)
    if getattr(config, "chess_fen_recognition_enabled", True) and template_dir:
        piece_templates = load_piece_templates(template_dir)
    
    chapters = []
    all_images = []
    all_chess_diagrams = []
    chess_fen_records = []
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
                            page,
                            region,
                            dpi=max(config.chess_diagram_dpi, 96),
                            optimize=False,
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
                        if getattr(config, "chess_fen_recognition_enabled", True):
                            region_spans = [ts for ts in text_spans if ts.index in region.text_span_indices]
                            fen_result = recognize_font_board_from_spans(
                                region_spans,
                                bbox=region.bbox,
                                min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85),
                            )
                            if not fen_result.fen and fen_result.board_detected:
                                image_result = recognize_chess_position_from_image(
                                    png_data,
                                    bbox=region.bbox,
                                    min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85),
                                    piece_templates=piece_templates,
                                )
                                if image_result.confidence > fen_result.confidence:
                                    fen_result = image_result
                            _attach_fen_to_chess_image(chess_img, fen_result)
                            chess_fen_records.append(
                                _chess_fen_record(
                                    page_num=page_num,
                                    filename=filename,
                                    result=fen_result,
                                    source="font-region",
                                )
                            )
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
            fen_attrs = chess_fen_html_attrs(chess_img)
            html_parts.append(
                f'<div class="figure chess-diagram-container"{fen_attrs}>'
                f'<img class="chess-diagram" src="images/{chess_img["filename"]}" '
                f'alt="{html_module.escape(chess_diagram_alt_text(chess_img), quote=True)}"{fen_attrs}/>'
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
        scanned_page_for_fen = False
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
            if not text_spans and not chess_imgs_for_page and not scanned_page_for_fen:
                scanned_page_for_fen = True
                chess_fen_records.extend(
                    _scan_image_for_board_candidates(
                        base_image["image"],
                        page_num=page_num,
                        filename=img_filename,
                        config=config,
                        piece_templates=piece_templates,
                    )
                )
        
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
        'metadata': {
            'chess_fen': summarize_chess_fen_results(chess_fen_records),
        },
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
