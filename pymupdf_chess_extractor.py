"""
KindleMaster — PyMuPDF Extraction with Chess Diagram Support
=============================================================
Extracts content from PDF using PyMuPDF while properly handling chess diagrams.

Chess diagrams in PDFs often use special fonts (Chess-Merida, etc.) with PUA 
(Private Use Area) characters that don't render in EPUB. This module detects 
those and renders them as images instead.
"""

import hashlib
import base64
import io
import html as html_module
import json
import re
import time
import zipfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

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
    chess_side_marker_html,
    strip_emails,
)
from chess_position_recognizer import (
    ChessFenResult,
    _estimate_board_grid_confidence,
    _bbox_overlap_ratio,
    _has_board_visual_pattern,
    detect_board_candidates_in_page_image,
    empty_chess_fen_result,
    load_piece_templates,
    recognize_chess_position_from_image,
    recognize_font_board_from_spans,
    summarize_chess_fen_results,
    validate_fen,
)
from chess_fen_hardening import machine_accept_fen
from chess_pgn_extractor import (
    UNMAPPED_CHESS_GLYPH_WARNING,
    _detect_unmapped_pgn_glyphs,
    _detect_unmapped_pgn_glyph_details,
    annotate_records_with_replayed_fens,
    attach_fen_candidates_to_pgn_records,
    build_chess_glyph_diagnostics_payload,
    build_combined_pgn,
    build_exercises_pgn,
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
CHESS_SYMBOL_FONT_TOKENS = (*FIGURINE_FONT_TOKENS, "chessbase", "chess", "figurine", "merida")
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
GLYPH_DIAGNOSTIC_SPAN_CHAR_LIMIT = 80
GLYPH_DIAGNOSTIC_LINE_LIMIT = 40


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
    glyph_warnings: list[str] = field(default_factory=list)
    glyph_warning_fonts: list[str] = field(default_factory=list)
    glyph_diagnostics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ScanChessSideMarkerProbe:
    """A bounded region near a scanned board that may contain side marker evidence."""

    role: str
    bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class ScanChessSideToMoveEvidence:
    """Trusted or audit-only side-to-move evidence for a scanned board."""

    side: str = ""
    source: str = "marker"
    raw_text: str = ""
    confidence: float = 0.0
    warnings: tuple[str, ...] = ()
    source_bbox: tuple[float, float, float, float] | None = None
    marker_candidates: tuple[dict[str, Any], ...] = ()


SIDE_MARKER_SYMBOLS = {
    "w": "\u25b3",
    "b": "\u25bc",
    "both": "\u25b3\u25bc",
    "unknown": "?",
    "ambiguous": "!",
}
SIDE_MARKER_TRUSTED_STATUSES = {
    "trusted_marker",
    "trusted_caption",
    "trusted_exact_label",
    "trusted_verified_label",
}
SIDE_MARKER_CONFLICT_WARNINGS = {
    "side_to_move_evidence_conflict",
    "side_to_move_marker_local_conflict",
    "side_to_move_marker_multi_region_conflict",
}
SIDE_MARKER_AMBIGUOUS_WARNINGS = {
    "side_to_move_marker_ambiguous",
    "side_to_move_marker_local_ambiguous",
}


@dataclass
class DiagramCaptionMatch:
    diagram_number: str
    text: str
    bbox: tuple[float, float, float, float]
    distance: float
    score: int
    confidence: float


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


def _page_text_dict_for_glyph_capture(page: fitz.Page, *, sort: bool = False) -> dict[str, Any]:
    flags = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
    try:
        return page.get_text("rawdict", flags=flags, sort=sort)
    except TypeError:
        try:
            return page.get_text("rawdict", flags=flags)
        except Exception:
            return page.get_text("dict", flags=flags, sort=sort)
    except Exception:
        return page.get_text("dict", flags=flags, sort=sort)


def _pdf_text_segment_from_span(
    span: Mapping[str, Any],
    *,
    page_num: int,
    block_index: int,
    line_index: int,
    span_index: int,
) -> dict[str, Any]:
    raw_chars = _span_raw_char_entries(span)
    raw_text = "".join(str(char.get("char") or "") for char in raw_chars)
    if not raw_text:
        raw_text = str(span.get("text") or "")
    bbox = _bbox_list(span.get("bbox", (0.0, 0.0, 0.0, 0.0))) or [0.0, 0.0, 0.0, 0.0]
    x0, y0, x1, _y1 = bbox
    return {
        "index": span_index,
        "page": page_num + 1,
        "page_index": page_num,
        "block_index": block_index,
        "line_index": line_index,
        "span_index": span_index,
        "text": raw_text,
        "raw_chars": raw_chars,
        "font_name": span.get("font", "") or "",
        "font_size": span.get("size", 12),
        "bbox": bbox,
        "x0": x0,
        "x1": x1,
        "y0": y0,
    }


def _span_raw_char_entries(span: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw_chars = span.get("chars") or []
    if isinstance(raw_chars, list):
        for char_index, item in enumerate(raw_chars):
            if not isinstance(item, Mapping):
                char = str(item or "")
                entries.append(_raw_char_entry(char, char_index=char_index))
                continue
            char = str(item.get("c") or item.get("char") or item.get("text") or "")
            entries.append(
                _raw_char_entry(
                    char,
                    char_index=char_index,
                    bbox=item.get("bbox"),
                    origin=item.get("origin"),
                    synthetic=bool(item.get("synthetic", False)),
                )
            )
    return entries


def _raw_char_entry(
    char: str,
    *,
    char_index: int,
    bbox: Any = None,
    origin: Any = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    glyph = char[:1] if char else ""
    entry: dict[str, Any] = {
        "char_index": char_index,
        "char": glyph,
        "codepoint": _glyph_codepoint_label(glyph),
        "synthetic": synthetic,
    }
    bbox_value = _bbox_list(bbox)
    if bbox_value:
        entry["bbox"] = bbox_value
    origin_value = _point_list(origin)
    if origin_value:
        entry["origin"] = origin_value
    return entry


def _glyph_codepoint_label(char: str) -> str:
    if not char:
        return ""
    return f"U+{ord(char):04X}"


def _bbox_list(value: Any) -> list[float]:
    if not value:
        return []
    try:
        values = list(value)
    except TypeError:
        return []
    if len(values) < 4:
        return []
    return [_round_pdf_coord(item) for item in values[:4]]


def _point_list(value: Any) -> list[float]:
    if not value:
        return []
    try:
        values = list(value)
    except TypeError:
        return []
    if len(values) < 2:
        return []
    return [_round_pdf_coord(item) for item in values[:2]]


def _round_pdf_coord(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _normalize_chess_span_text(segment: dict[str, Any]) -> str:
    raw_text = strip_emails(str(segment.get("text") or ""))
    font_name = str(segment.get("font_name") or "")
    glyph_context = _span_glyph_context(segment)
    normalized = _normalize_text_for_epub(raw_text, font_name)
    font_lower = font_name.lower()
    if any(token in font_lower for token in CHESS_SYMBOL_FONT_TOKENS):
        normalized = normalize_ocr_text_for_pgn(normalized)
    if (
        _detect_unmapped_pgn_glyphs(raw_text)
        or _detect_unmapped_pgn_glyphs(glyph_context)
        or _detect_unmapped_pgn_glyphs(normalized)
    ):
        warnings = set(segment.get("warnings") or [])
        warnings.add(UNMAPPED_CHESS_GLYPH_WARNING)
        segment["warnings"] = sorted(warnings)
        segment["glyph_diagnostics"] = _span_glyph_diagnostics(
            segment,
            raw_text=raw_text,
            glyph_context=glyph_context,
            normalized_text=normalized,
        )
    return normalized


def _span_glyph_diagnostics(
    segment: Mapping[str, Any],
    *,
    raw_text: str,
    glyph_context: str,
    normalized_text: str,
) -> list[dict[str, Any]]:
    details: list[dict[str, str]] = []
    details.extend(_detect_unmapped_pgn_glyph_details(raw_text, field="raw_text"))
    details.extend(_detect_unmapped_pgn_glyph_details(glyph_context, field="glyph_context"))
    details.extend(_detect_unmapped_pgn_glyph_details(normalized_text, field="normalized_text"))
    if not details:
        return []
    reasons = sorted({str(detail.get("reason") or "") for detail in details if detail.get("reason")})
    samples = [
        {
            "field": str(detail.get("field") or ""),
            "reason": str(detail.get("reason") or ""),
            "sample": str(detail.get("sample") or ""),
        }
        for detail in details[:12]
    ]
    return [
        {
            "page": segment.get("page"),
            "page_index": segment.get("page_index"),
            "block_index": segment.get("block_index"),
            "line_index": segment.get("line_index"),
            "span_index": segment.get("span_index", segment.get("index")),
            "font_name": str(segment.get("font_name") or "Unknown"),
            "font_size": _round_pdf_coord(segment.get("font_size", 0.0)),
            "bbox": _bbox_list(segment.get("bbox")),
            "reasons": reasons,
            "samples": samples,
            "raw_text": _glyph_audit_sample(raw_text),
            "glyph_context": _glyph_audit_sample(glyph_context),
            "normalized_text": _glyph_audit_sample(normalized_text),
            "codepoints": _segment_codepoint_entries(segment, raw_text=raw_text),
        }
    ]


def _segment_codepoint_entries(segment: Mapping[str, Any], *, raw_text: str) -> list[dict[str, Any]]:
    raw_chars = segment.get("raw_chars") or []
    if isinstance(raw_chars, list) and raw_chars:
        return [
            dict(char_entry)
            for char_entry in raw_chars[:GLYPH_DIAGNOSTIC_SPAN_CHAR_LIMIT]
            if isinstance(char_entry, Mapping)
        ]
    return [
        {
            "char_index": index,
            "char": char,
            "codepoint": _glyph_codepoint_label(char),
        }
        for index, char in enumerate(str(raw_text or "")[:GLYPH_DIAGNOSTIC_SPAN_CHAR_LIMIT])
    ]


def _span_glyph_context(segment: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("raw_chars", "chars", "glyphs"):
        value = segment.get(key)
        if not value:
            continue
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    values.append(str(item.get("c") or item.get("char") or item.get("text") or ""))
                else:
                    values.append(str(item or ""))
        else:
            values.append(str(value))
    return "".join(values)


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
        glyph_warnings: set[str] = set()
        glyph_warning_fonts: set[str] = set()
        glyph_diagnostics: list[dict[str, Any]] = []

        for segment in segments:
            font_lower = (segment["font_name"] or "").lower()
            is_figurine_segment = any(token in font_lower for token in FIGURINE_FONT_TOKENS)
            piece = _normalize_chess_span_text(segment).strip()
            if not piece:
                continue

            if start_index is None:
                start_index = segment["index"]
            max_font_size = max(max_font_size, segment["font_size"])
            if UNMAPPED_CHESS_GLYPH_WARNING in set(segment.get("warnings") or []):
                glyph_warnings.add(UNMAPPED_CHESS_GLYPH_WARNING)
                glyph_warning_fonts.add(str(segment.get("font_name") or "Unknown"))
                glyph_diagnostics.extend(
                    diagnostic
                    for diagnostic in (segment.get("glyph_diagnostics") or [])
                    if isinstance(diagnostic, dict)
                )

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
            glyph_warnings=sorted(glyph_warnings),
            glyph_warning_fonts=sorted(glyph_warning_fonts),
            glyph_diagnostics=glyph_diagnostics[:GLYPH_DIAGNOSTIC_LINE_LIMIT],
        ))

    return items


def _empty_unmapped_glyph_span_audit() -> dict[str, Any]:
    return {"line_count": 0, "by_font": {}, "samples": [], "diagnostics": []}


def _merge_unmapped_glyph_span_audit(audit: dict[str, Any], line_items: list[TextLineItem], *, page_num: int) -> None:
    by_font = dict(audit.get("by_font") or {})
    samples = list(audit.get("samples") or [])
    diagnostics = list(audit.get("diagnostics") or [])
    line_count = int(audit.get("line_count", 0) or 0)
    for item in line_items:
        if UNMAPPED_CHESS_GLYPH_WARNING not in set(item.glyph_warnings or []):
            continue
        line_count += 1
        fonts = item.glyph_warning_fonts or ["Unknown"]
        for font in fonts:
            key = str(font or "Unknown")
            by_font[key] = int(by_font.get(key, 0) or 0) + 1
        if len(samples) < 12:
            samples.append(
                {
                    "page": page_num + 1,
                    "fonts": list(fonts),
                    "sample": _glyph_audit_sample(item.text),
                }
            )
        for diagnostic in item.glyph_diagnostics or []:
            if len(diagnostics) >= 24:
                break
            diagnostics.append(diagnostic)
    audit["line_count"] = line_count
    audit["by_font"] = dict(sorted(by_font.items()))
    audit["samples"] = samples
    audit["diagnostics"] = diagnostics


def _glyph_audit_sample(text: str, *, limit: int = 120) -> str:
    sample = WHITESPACE_RE.sub(" ", str(text or "")).strip()
    escaped: list[str] = []
    for char in sample[:limit]:
        codepoint = ord(char)
        if char == "\ufffd":
            escaped.append("\\ufffd")
        elif 0xE000 <= codepoint <= 0xF8FF:
            escaped.append(f"\\u{codepoint:04x}")
        elif 0xF0000 <= codepoint <= 0xFFFFD or 0x100000 <= codepoint <= 0x10FFFD:
            escaped.append(f"\\U{codepoint:08x}")
        elif codepoint < 32 or 0x7F <= codepoint <= 0x9F:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


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
        template_dir = _resolve_chess_piece_template_dir(config)
        piece_templates = (
            load_piece_templates(template_dir)
            if getattr(config, "chess_fen_recognition_enabled", True) and template_dir
            else {}
        )
        chunk_pages = max(10, int(getattr(config, "chess_notation_chapter_pages", 40) or 40))
        chapters: list[dict[str, Any]] = []
        current_parts: list[str] = []
        current_text_lines: list[str] = []
        current_glyph_diagnostics: list[dict[str, Any]] = []
        current_start = 0
        text_pages = 0
        notation_line_count = 0
        skipped_image_count = 0
        chess_pgn_records: list[Any] = []
        chess_diagram_records: list[dict[str, Any]] = []
        chess_fen_records: list[dict[str, Any]] = []
        book_layout_pages: list[dict[str, Any]] = []
        text_extraction_seconds = 0.0
        pgn_extraction_seconds = 0.0
        diagram_detection_seconds = 0.0
        unmapped_glyph_span_audit = _empty_unmapped_glyph_span_audit()
        template_dir = _resolve_chess_piece_template_dir(config)
        piece_templates = (
            load_piece_templates(template_dir)
            if getattr(config, "chess_fen_recognition_enabled", True) and template_dir
            else {}
        )

        def flush_chapter(end_page: int) -> None:
            nonlocal current_parts, current_text_lines, current_glyph_diagnostics, current_start, chess_pgn_records, pgn_extraction_seconds
            if not current_parts:
                current_start = end_page + 1
                current_text_lines = []
                current_glyph_diagnostics = []
                return
            flush_started = time.perf_counter()
            chapter_records = annotate_records_with_replayed_fens(
                extract_chess_pgn_records_from_text(
                    "\n".join(current_text_lines),
                    page_num=current_start,
                    source_title=str(metadata.get("title") or Path(pdf_path).stem),
                    ocr_confidence=1.0,
                    glyph_diagnostics=current_glyph_diagnostics,
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
            current_glyph_diagnostics = []
            current_start = end_page + 1

        for page_num in range(total_pages):
            if page_num > current_start and (page_num - current_start) >= chunk_pages:
                flush_chapter(page_num - 1)

            page = doc[page_num]
            page_started = time.perf_counter()
            skipped_image_count += len(page.get_images(full=True))
            line_items = _chess_notation_line_items_from_page(page, page_num)
            _merge_unmapped_glyph_span_audit(unmapped_glyph_span_audit, line_items, page_num=page_num)
            body_lines = _chess_notation_body_lines(line_items)
            page_parts = _chess_notation_page_html_parts(line_items, page_num=page_num, body_lines=body_lines)
            diagram_started = time.perf_counter()
            page_diagram_records, page_fen_records = _notation_layout_diagrams_from_page(
                page,
                page_num,
                config,
                piece_templates=piece_templates,
                nearby_text="\n".join(body_lines[:80]),
            )
            diagram_detection_seconds += time.perf_counter() - diagram_started
            chess_diagram_records.extend(page_diagram_records)
            chess_fen_records.extend(page_fen_records)
            elements = _book_layout_text_elements_from_line_items(line_items)
            elements.extend(
                _book_layout_diagram_elements_from_diagrams(
                    page_diagram_records,
                    page_num=page_num,
                    reading_order_start=10_000,
                )
            )
            if elements or page_num not in {int(page.get("page_index", -1) or -1) for page in book_layout_pages}:
                book_layout_pages.append(
                    _book_layout_page_from_pdf_page(
                        page,
                        page_num,
                        config,
                        elements=elements,
                    )
                )
            text_extraction_seconds += time.perf_counter() - page_started
            if page_parts:
                text_pages += 1
                notation_line_count += sum(1 for line in body_lines if _is_notation_heavy_line(line))
                current_parts.extend(page_parts)
                current_text_lines.extend(body_lines)
                current_glyph_diagnostics.extend(_line_items_glyph_diagnostics(line_items))

        flush_chapter(total_pages - 1)
        book_layout_pages = _ensure_book_layout_pages_cover_document(doc, book_layout_pages, config)
    finally:
        doc.close()

    for source_order, record in enumerate(chess_diagram_records):
        record.setdefault("source_order", source_order)

    chess_pgn_records = merge_chess_pgn_continuation_records(chess_pgn_records)
    chess_pgn_summary = summarize_chess_pgn_records(chess_pgn_records, diagram_records=chess_diagram_records)
    audit_metadata = dict(metadata.get("audit") or {})
    audit_metadata.update(
        {
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "timings": {
                "text_extraction_seconds": round(text_extraction_seconds, 4),
                "pgn_extraction_seconds": round(pgn_extraction_seconds, 4),
                "diagram_detection_seconds": round(diagram_detection_seconds, 4),
            },
        }
    )
    if int(unmapped_glyph_span_audit.get("line_count", 0) or 0):
        audit_metadata["unmapped_chess_glyph_spans"] = unmapped_glyph_span_audit
    source_title = str(metadata.get("title") or Path(pdf_path).stem)

    return {
        "success": True,
        "method": "chess-notation-text-reflow",
        "text_content": bool(chapters),
        "layout_mode": "reflowable",
        "metadata": {
            **metadata,
            "publication_kind": "chess-notation-collection",
            "image_policy": "embedded_board_images_skipped_for_runtime_budget",
            "html_diagram_preview_count": len(chess_diagram_records),
            "source_page_count": total_pages,
            "text_page_count": text_pages,
            "notation_line_count": notation_line_count,
            "skipped_embedded_image_count": skipped_image_count,
            "chess_pgn": chess_pgn_summary,
            "audit": audit_metadata,
            "chess_fen": {
                "status": "passed" if chess_pgn_summary.get("derived_final_fen_count") or chess_diagram_records else "requires_review",
                "source": "html_diagram_preview_and_pgn_replay",
                "diagram_count": len(chess_diagram_records) or int(chess_pgn_summary.get("candidate_game_count", 0) or 0),
                "fen_count": len([record for record in chess_diagram_records if record.get("fen")]) + int(chess_pgn_summary.get("fen_count", 0) or 0),
                "manual_review_count": int(chess_pgn_summary.get("manual_review_count", 0) or 0),
                "caption_match_count": len([record for record in chess_diagram_records if int(record.get("caption_match_score") or 0) > 0]),
                "caption_number_count": len([record for record in chess_diagram_records if str(record.get("diagram_number") or "").strip()]),
                "caption_guided_candidate_count": len(
                    [
                        record
                        for record in chess_diagram_records
                        if "caption_guided" in str(record.get("method") or "")
                        or "caption_guided_board_candidate" in {str(warning) for warning in (record.get("warnings") or [])}
                    ]
                ),
                "board_found_near_caption_count": len([record for record in chess_diagram_records if bool(record.get("board_found_near_caption"))]),
                "global_candidate_without_caption_count": len(
                    [record for record in chess_diagram_records if record.get("board_detection_reason") == "global_candidate_without_caption"]
                ),
            },
        },
        "images": [],
        "chapters": chapters,
        "extra_artifacts": _scan_chess_pgn_extra_artifacts(
            chess_pgn_records,
            source_title=source_title,
            diagrams=chess_diagram_records,
            book_layout_pages=book_layout_pages,
        ),
        "audit": {
            "status": "passed_with_warnings",
            "image_policy": "skipped_embedded_images",
            "skipped_embedded_image_count": skipped_image_count,
            "warning": "Raster board images were skipped to keep large chess notation collections generatable.",
        },
    }


def _chess_notation_diagram_records_from_page(
    doc: fitz.Document,
    page_num: int,
    *,
    config: ConversionConfig,
    piece_templates: dict | None = None,
    line_items: list[TextLineItem] | None = None,
) -> list[dict[str, Any]]:
    max_pages = int(getattr(config, "chess_notation_diagram_scan_max_pages", 260) or 0)
    if max_pages <= 0 or page_num >= max_pages:
        return []
    configured_candidates = int(getattr(config, "chess_fen_scan_candidates_per_page", 8) or 8)
    max_candidates = max(1, min(max(configured_candidates, 8), 8))
    try:
        image_data = _page_image_data_for_scan_chess(doc, page_num)
        page_image = Image.open(io.BytesIO(image_data)).convert("RGB")
    except Exception:
        return []
    try:
        candidates = list(detect_board_candidates_in_page_image(
            image_data,
            max_candidates=max_candidates,
            enable_sliding_probe=bool(getattr(config, "chess_fen_scan_enable_sliding_probe", False)),
        ))
    except Exception:
        return []
    candidates = _augment_notation_board_candidates_from_captions(
        doc,
        page_num,
        page_image,
        candidates,
        line_items or [],
        max_candidates=max_candidates,
    )
    records: list[dict[str, Any]] = []
    for candidate_index, candidate in enumerate(candidates, start=1):
        raw_bbox = getattr(candidate, "bbox", None)
        bbox = _clamp_bbox(raw_bbox, page_image.size)
        recognition_bbox = _clamp_bbox(raw_bbox, page_image.size, pad_ratio=0.0, min_pad=0.0)
        if bbox is None:
            continue
        if recognition_bbox is None:
            recognition_bbox = bbox
        source_crop = page_image.crop(bbox)
        if min(source_crop.size) < 80 or _scan_chess_is_partial_separator_crop(source_crop):
            continue
        display_crop, crop_quality = _notation_chess_display_crop(source_crop)
        if min(display_crop.size) < 80 or _scan_chess_is_partial_separator_crop(display_crop):
            display_crop = source_crop
            crop_quality["display_crop_fallback"] = True
        png_data, width, height = _encode_scan_chess_diagram_crop(display_crop, config)
        bbox_tuple = tuple(float(value) for value in bbox)
        recognition_bbox_tuple = tuple(float(value) for value in recognition_bbox)
        candidate_confidence = float(getattr(candidate, "confidence", 0.0) or getattr(candidate, "grid_confidence", 0.0) or 0.0)
        raw_method = str(getattr(candidate, "method", "") or "notation-page-board-crop")
        caption_guided_candidate = "caption_guided" in raw_method
        preprocess_metadata = _scan_chess_preprocess_metadata(
            selected_variant="original",
            display_variant="reader_enhanced",
            confidence=candidate_confidence,
        )
        candidate_payload: dict[str, Any] = {
            "fen": "",
            "placement": "",
            "confidence": candidate_confidence,
            "side_to_move": "w",
            "bbox": tuple(float(value) for value in bbox),
            "method": raw_method,
            "warnings": ["image_board_requires_review"],
            "requires_review": True,
            "board_detected": True,
        }
        if piece_templates:
            try:
                min_confidence = float(getattr(config, "chess_fen_min_confidence", 0.85) or 0.85)
                recognition = _recognize_scan_chess_candidate_bbox(
                    page_image,
                    recognition_bbox_tuple,
                    config=config,
                    piece_templates=piece_templates,
                    min_confidence=min_confidence,
                    reader_bbox=bbox_tuple,
                )
                recognition = _scan_chess_confirm_final_rendered_crop_recognition(
                    recognition,
                    png_data,
                    bbox=bbox_tuple,
                    piece_templates=piece_templates,
                    min_confidence=min_confidence,
                )
                selected_variant = "full_page_bbox_recognition"
                preprocessed_recognition, preprocessed_variant, _preprocessed_metadata = _recognize_scan_chess_preprocessed_variants(
                    display_crop,
                    config=config,
                    bbox=bbox_tuple,
                    piece_templates=piece_templates,
                )
                if preprocessed_recognition is not None:
                    preferred = _prefer_scan_chess_recognition_result(recognition, preprocessed_recognition)
                    if preferred is preprocessed_recognition:
                        recognition = preprocessed_recognition
                        selected_variant = preprocessed_variant
                recognition = _scan_chess_apply_verified_crop_label(
                    recognition,
                    png_data,
                    bbox=bbox_tuple,
                    config=config,
                )
                candidate_payload = _scan_chess_fen_payload(candidate_payload, recognition)
                preprocess_metadata = _scan_chess_preprocess_metadata(
                    selected_variant=selected_variant,
                    display_variant="reader_enhanced",
                    confidence=float(candidate_payload.get("confidence", 0.0) or 0.0),
                    piece_confidence=float(candidate_payload.get("confidence", 0.0) or 0.0),
                    grid_confidence=float(getattr(candidate, "confidence", 0.0) or getattr(candidate, "grid_confidence", 0.0) or 0.0),
                )
            except Exception:
                pass
        fen_value = str(candidate_payload.get("fen") or "").strip()
        caption_match = _nearest_chess_notation_diagram_caption_match(
            line_items or [],
            tuple(float(value) for value in bbox),
        )
        diagram_number = caption_match.diagram_number if caption_match else ""
        detection_reason = (
            "caption_guided_local_scan"
            if caption_guided_candidate
            else ("global_candidate_with_caption" if caption_match else "global_candidate_without_caption")
        )
        warnings = list(candidate_payload.get("warnings") or ([] if fen_value else ["image_board_requires_review"]))
        if caption_guided_candidate and "caption_guided_board_candidate" not in warnings:
            warnings.append("caption_guided_board_candidate")
        records.append(
            {
                "page": page_num + 1,
                "diagram_number": diagram_number,
                "filename": f"notation_chess_p{page_num + 1:03d}_{candidate_index:02d}.png",
                "source": "notation-page-board-crop",
                "image_data": png_data,
                "extension": "png",
                "width": width,
                "height": height,
                "fen": fen_value,
                "full_fen": str(candidate_payload.get("full_fen") or candidate_payload.get("fen") or ""),
                "placement": str(candidate_payload.get("placement") or ""),
                "side_to_move": str(candidate_payload.get("side_to_move") or "w"),
                "confidence": float(candidate_payload.get("confidence", 0.0) or 0.0),
                "requires_review": bool(candidate_payload.get("requires_review", not fen_value)),
                "method": str(candidate_payload.get("method") or "notation-page-board-crop"),
                "warnings": warnings,
                "bbox": candidate_payload.get("bbox") or tuple(float(value) for value in bbox),
                "recognition_bbox": recognition_bbox_tuple,
                "source_crop_bbox": bbox_tuple,
                "caption_text": caption_match.text if caption_match else "",
                "caption_bbox": caption_match.bbox if caption_match else None,
                "caption_distance": round(float(caption_match.distance), 3) if caption_match else None,
                "caption_match_score": int(caption_match.score) if caption_match else 0,
                "caption_confidence": round(float(caption_match.confidence), 3) if caption_match else 0.0,
                "caption_seen": bool(caption_match),
                "board_found_near_caption": bool(caption_match and int(caption_match.score) >= 55),
                "board_detection_reason": detection_reason,
                "crop_confidence": round(candidate_confidence, 3),
                "crop_quality": crop_quality,
                **preprocess_metadata,
            }
        )
    return records


def _nearest_chess_notation_diagram_number(
    line_items: list[TextLineItem],
    bbox: tuple[float, float, float, float],
) -> str:
    match = _nearest_chess_notation_diagram_caption_match(line_items, bbox)
    return match.diagram_number if match else ""


def _nearest_chess_notation_diagram_caption_match(
    line_items: list[TextLineItem],
    bbox: tuple[float, float, float, float],
) -> DiagramCaptionMatch | None:
    x0, y0, x1, y1 = bbox
    board_center_x = (float(x0) + float(x1)) / 2.0
    board_width = max(1.0, float(x1) - float(x0))
    board_height = max(1.0, float(y1) - float(y0))
    matches: list[tuple[float, DiagramCaptionMatch]] = []
    for caption in _chess_notation_caption_candidates(line_items):
        cap_x0, cap_y0, cap_x1, cap_y1 = caption.bbox
        cap_center_x = (float(cap_x0) + float(cap_x1)) / 2.0
        horizontal_distance = abs(cap_center_x - board_center_x)
        horizontal_overlap = max(0.0, min(float(cap_x1), float(x1)) - max(float(cap_x0), float(x0)))
        overlap_ratio = horizontal_overlap / max(1.0, min(board_width, max(1.0, float(cap_x1) - float(cap_x0))))
        if cap_y1 <= float(y0):
            vertical_gap = float(y0) - cap_y1
            below_penalty = 0.0
        elif cap_y0 >= float(y1):
            vertical_gap = cap_y0 - float(y1)
            below_penalty = 180.0
        else:
            vertical_gap = 0.0
            below_penalty = 20.0
        column_penalty = 0.0
        if overlap_ratio < 0.12 and horizontal_distance > board_width * 0.75:
            column_penalty = 120.0
        far_penalty = 120.0 if vertical_gap > board_height * 1.35 else 0.0
        distance = vertical_gap + (horizontal_distance * 0.18) + below_penalty + column_penalty + far_penalty
        score = max(0, min(100, int(round(105.0 - (distance / 3.0)))))
        confidence = round(float(score) / 100.0, 3)
        if confidence < 0.18:
            continue
        matches.append(
            (
                distance,
                DiagramCaptionMatch(
                    diagram_number=caption.diagram_number,
                    text=caption.text,
                    bbox=caption.bbox,
                    distance=distance,
                    score=score,
                    confidence=confidence,
                ),
            )
        )
    if not matches:
        return None
    matches.sort(key=lambda item: (-item[1].score, item[0], item[1].bbox[1]))
    return matches[0][1]


def _chess_notation_caption_candidates(line_items: list[TextLineItem]) -> list[DiagramCaptionMatch]:
    sorted_items = sorted(line_items or [], key=lambda item: (float(getattr(item, "y", 0.0) or 0.0), float(getattr(item, "x0", 0.0) or 0.0)))
    candidates: list[DiagramCaptionMatch] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for index, item in enumerate(sorted_items):
        windows: list[list[TextLineItem]] = [[item]]
        for next_item in sorted_items[index + 1 : index + 3]:
            if not _chess_notation_line_items_can_join_for_caption(windows[-1][-1], next_item):
                break
            windows.append([*windows[-1], next_item])
        for window in windows:
            text = " ".join(str(getattr(part, "text", "") or "").strip() for part in window).strip()
            diagram_number = _extract_chess_notation_diagram_number_from_caption(text)
            if not diagram_number:
                continue
            x0 = min(float(getattr(part, "x0", 0.0) or 0.0) for part in window)
            x1 = max(float(getattr(part, "x1", 0.0) or 0.0) for part in window)
            y0 = min(float(getattr(part, "y", 0.0) or 0.0) for part in window)
            y1 = max(float(getattr(part, "y", 0.0) or 0.0) + float(getattr(part, "font_size", 12.0) or 12.0) for part in window)
            key = (diagram_number, int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                DiagramCaptionMatch(
                    diagram_number=diagram_number,
                    text=text,
                    bbox=(x0, y0, x1, y1),
                    distance=0.0,
                    score=0,
                    confidence=0.0,
                )
            )
    return candidates


def _chess_notation_line_items_can_join_for_caption(left: TextLineItem, right: TextLineItem) -> bool:
    left_y = float(getattr(left, "y", 0.0) or 0.0)
    right_y = float(getattr(right, "y", 0.0) or 0.0)
    if abs(right_y - left_y) > 18.0:
        return False
    left_x0 = float(getattr(left, "x0", 0.0) or 0.0)
    left_x1 = float(getattr(left, "x1", left_x0) or left_x0)
    right_x0 = float(getattr(right, "x0", 0.0) or 0.0)
    right_x1 = float(getattr(right, "x1", right_x0) or right_x0)
    horizontal_gap = max(0.0, right_x0 - left_x1)
    overlap = max(0.0, min(left_x1, right_x1) - max(left_x0, right_x0))
    same_column = overlap > 8.0 or horizontal_gap < 80.0
    return same_column


def _extract_chess_notation_diagram_number_from_caption(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    direct = re.search(r"(?i)\bdiagram\s+(\d{1,2})\s*[-.]\s*(\d{1,2})\b", normalized)
    if direct:
        return f"{int(direct.group(1))}-{int(direct.group(2))}"
    split = re.search(r"(?i)\bdiagram\s+\d{1,2}\s+(\d{1,2})\s*[-.]\s*(\d{1,2})\b", normalized)
    if split:
        return f"{int(split.group(1))}-{int(split.group(2))}"
    spaced = re.search(r"(?i)\bdiagram\s+(\d{1,2})\s+(\d{1,2})\b", normalized)
    if spaced:
        return f"{int(spaced.group(1))}-{int(spaced.group(2))}"
    return ""


def _augment_notation_board_candidates_from_captions(
    doc: fitz.Document,
    page_num: int,
    page_image: Image.Image,
    candidates: list[ChessFenResult],
    line_items: list[TextLineItem],
    *,
    max_candidates: int,
) -> list[ChessFenResult]:
    captions = _chess_notation_caption_candidates(line_items)
    target_limit = max(max_candidates, min(10, max_candidates + min(2, len(captions))))
    if not captions or len(candidates) >= target_limit:
        return candidates
    try:
        page_rect = doc[page_num].rect
        page_width = float(page_rect.width or 0.0)
        page_height = float(page_rect.height or 0.0)
    except Exception:
        return candidates
    if page_width <= 0 or page_height <= 0:
        return candidates
    scale_x = float(page_image.size[0]) / page_width
    scale_y = float(page_image.size[1]) / page_height
    if not (0.2 <= scale_x <= 6.0 and 0.2 <= scale_y <= 6.0):
        return candidates
    augmented = list(candidates)
    caption_scan_count = 0
    for caption in captions:
        if len(augmented) >= target_limit:
            break
        if caption_scan_count >= 2:
            break
        existing_caption_numbers: set[str] = set()
        for result in augmented:
            if not result.bbox:
                continue
            existing_match = _nearest_chess_notation_diagram_caption_match(line_items, tuple(result.bbox))
            if existing_match and existing_match.score >= 55:
                existing_caption_numbers.add(existing_match.diagram_number)
        if caption.diagram_number in existing_caption_numbers:
            continue
        cap_x0, _cap_y0, cap_x1, cap_y1 = caption.bbox
        region_pdf = (
            max(0.0, cap_x0 - 50.0),
            max(0.0, cap_y1 - 8.0),
            min(page_width, cap_x1 + 110.0),
            min(page_height, cap_y1 + 280.0),
        )
        region = (
            int(round(region_pdf[0] * scale_x)),
            int(round(region_pdf[1] * scale_y)),
            int(round(region_pdf[2] * scale_x)),
            int(round(region_pdf[3] * scale_y)),
        )
        if region[2] - region[0] < 120 or region[3] - region[1] < 120:
            continue
        crop = page_image.crop(region)
        output = io.BytesIO()
        crop.save(output, format="PNG")
        try:
            local_candidates = detect_board_candidates_in_page_image(
                output.getvalue(),
                max_candidates=2,
                enable_sliding_probe=False,
            )
        except Exception:
            continue
        caption_scan_count += 1
        for local in local_candidates:
            local_bbox = getattr(local, "bbox", None)
            if not local_bbox:
                continue
            page_bbox = (
                float(region[0]) + float(local_bbox[0]),
                float(region[1]) + float(local_bbox[1]),
                float(region[0]) + float(local_bbox[2]),
                float(region[1]) + float(local_bbox[3]),
            )
            if any(_bbox_overlap_ratio(tuple(existing.bbox or ()), page_bbox) > 0.65 for existing in augmented if existing.bbox):
                continue
            augmented.append(
                ChessFenResult(
                    confidence=float(getattr(local, "confidence", 0.0) or 0.0),
                    bbox=page_bbox,
                    method=f"{getattr(local, 'method', '') or 'caption-guided-board-candidate'}:caption_guided",
                    warnings=["caption_guided_board_candidate"],
                    requires_review=True,
                    board_detected=True,
                )
            )
            break
    return augmented


def _notation_chess_display_crop(crop: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    source = crop.convert("RGB")
    original_width, original_height = source.size
    quality: dict[str, Any] = {
        "source_width": int(original_width),
        "source_height": int(original_height),
        "trimmed": False,
        "squared": False,
        "display_crop_fallback": False,
    }
    working = source
    try:
        gray = ImageOps.grayscale(source)
        mask = gray.point(lambda pixel: 255 if pixel < 246 else 0)
        content_bbox = mask.getbbox()
        if content_bbox:
            x0, y0, x1, y1 = content_bbox
            margin = max(2, int(min(original_width, original_height) * 0.015))
            trim_box = (
                max(0, x0 - margin),
                max(0, y0 - margin),
                min(original_width, x1 + margin),
                min(original_height, y1 + margin),
            )
            trim_width = trim_box[2] - trim_box[0]
            trim_height = trim_box[3] - trim_box[1]
            if trim_width >= 80 and trim_height >= 80:
                area_ratio = (trim_width * trim_height) / max(1, original_width * original_height)
                if 0.25 <= area_ratio <= 1.02:
                    working = source.crop(trim_box)
                    quality["trimmed"] = trim_box != (0, 0, original_width, original_height)
                    quality["trim_box"] = tuple(int(value) for value in trim_box)
    except Exception:
        working = source
    width, height = working.size
    if min(width, height) >= 80:
        side = min(width, height)
        if abs(width - height) > 2 and abs(width - height) / max(width, height) <= 0.22:
            left = max(0, (width - side) // 2)
            top = max(0, (height - side) // 2)
            working = working.crop((left, top, left + side, top + side))
            quality["squared"] = True
            quality["square_box"] = (int(left), int(top), int(left + side), int(top + side))
    try:
        stat = ImageStat.Stat(ImageOps.grayscale(working))
        quality["display_width"] = int(working.size[0])
        quality["display_height"] = int(working.size[1])
        quality["mean_luma"] = round(float(stat.mean[0]), 3)
        quality["contrast"] = round(float(stat.stddev[0]), 3)
    except Exception:
        quality["display_width"] = int(working.size[0])
        quality["display_height"] = int(working.size[1])
    return working, quality


def _chess_notation_line_items_from_page(page: fitz.Page, page_num: int) -> list[TextLineItem]:
    raw_lines: list[dict[str, Any]] = []
    span_index = 0
    page_width = float(page.rect.width or 0.0)
    text_dict = _page_text_dict_for_glyph_capture(page, sort=True)
    for block_index, block in enumerate(text_dict.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            raw_line_segments = []
            for span in line.get("spans", []):
                segment = _pdf_text_segment_from_span(
                    span,
                    page_num=page_num,
                    block_index=block_index,
                    line_index=line_index,
                    span_index=span_index,
                )
                raw_text = segment.get("text", "")
                if not str(raw_text or "").strip():
                    span_index += 1
                    continue
                raw_line_segments.append(segment)
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


def _line_items_glyph_diagnostics(line_items: list[TextLineItem]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for item in _order_chess_notation_lines_for_reading(line_items):
        diagnostics.extend(item.glyph_diagnostics or [])
    return diagnostics


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


def _notation_layout_diagrams_from_page(
    page: fitz.Page,
    page_num: int,
    config: ConversionConfig,
    *,
    piece_templates: dict,
    nearby_text: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not getattr(config, "chess_fen_recognition_enabled", True):
        return [], []
    max_pages = int(getattr(config, "chess_notation_layout_diagram_scan_pages", 0) or 0)
    if max_pages > 0 and page_num >= max_pages:
        return [], []
    max_candidates = max(0, int(getattr(config, "chess_fen_scan_candidates_per_page", 3) or 0))
    if max_candidates <= 0:
        return [], []
    dpi = max(96, min(int(getattr(config, "chess_diagram_dpi", 140) or 140), 180))
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    try:
        pixmap = page.get_pixmap(matrix=matrix, alpha=False)
        page_png = pixmap.tobytes("png")
        page_image = Image.open(io.BytesIO(page_png)).convert("RGB")
    except Exception:
        return [], []

    candidates = detect_board_candidates_in_page_image(
        page_png,
        max_candidates=max_candidates,
        min_grid_confidence=float(getattr(config, "scanned_chess_min_grid_confidence", 0.50) or 0.50),
        enable_sliding_probe=bool(getattr(config, "chess_fen_scan_enable_sliding_probe", False)),
    )
    diagrams: list[dict[str, Any]] = []
    fen_records: list[dict[str, Any]] = []
    seen_bboxes: list[tuple[float, float, float, float]] = []
    for candidate_index, candidate in enumerate(candidates, start=1):
        if not candidate.bbox:
            continue
        pixel_bbox = _clamp_bbox(candidate.bbox, page_image.size, pad_ratio=0.01, min_pad=2.0)
        if pixel_bbox is None:
            continue
        crop = page_image.crop(pixel_bbox)
        if min(crop.size) < 80 or _scan_chess_is_partial_separator_crop(crop):
            continue
        page_bbox = tuple(
            _scale_bbox(
                pixel_bbox,
                source_size=page_image.size,
                target_width=float(page.rect.width or page_image.width),
                target_height=float(page.rect.height or page_image.height),
            )
        )
        if any(_bbox_overlap_ratio(page_bbox, existing) > 0.70 for existing in seen_bboxes):
            continue
        seen_bboxes.append(page_bbox)

        reader_crop = _resize_image_to_long_edge(
            crop,
            int(getattr(config, "scanned_chess_diagram_long_edge", 360) or 360),
            resample=Image.Resampling.LANCZOS,
        )
        png_data, width, height = _encode_scan_chess_diagram_crop(reader_crop, config)
        result = recognize_chess_position_from_image(
            png_data,
            bbox=page_bbox,
            min_confidence=float(getattr(config, "chess_fen_min_confidence", 0.835) or 0.835),
            piece_templates=piece_templates,
        )
        chess_img = {
            "filename": f"notation_layout_p{page_num + 1:03d}_{candidate_index:02d}.png",
            "data": png_data,
            "extension": "png",
            "width": width,
            "height": height,
            "bbox": page_bbox,
            "page": page_num,
            "is_chess": True,
            "inline": True,
            "fen_result": result.to_dict(),
            "fen_confidence": result.confidence,
            "fen_method": result.method,
        }
        if result.fen and not result.requires_review and _fen_string_is_parser_valid(result.fen):
            chess_img["fen"] = result.fen
        diagram_id = f"layout-chess-p{page_num + 1:03d}-d{len(diagrams) + 1:02d}"
        diagrams.append(
            _chess_diagram_record_from_image(
                chess_img,
                diagram_id=diagram_id,
                caption=f"Strona {page_num + 1}, diagram {len(diagrams) + 1}",
                page_num=page_num,
                nearby_text=nearby_text,
            )
        )
        fen_records.append(
            _chess_fen_record(
                page_num=page_num,
                filename=chess_img["filename"],
                result=result,
                source="notation-layout-page-render",
            )
        )
    return diagrams, fen_records


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
    payload.update(_basic_two_crop_contract_fields(chess_img.get("filename"), chess_img.get("bbox")))
    chess_img["fen_result"] = payload
    chess_img["fen_confidence"] = payload.get("confidence", 0.0)
    chess_img["fen_method"] = payload.get("method", "")
    chess_img.update({key: value for key, value in payload.items() if key in TWO_CROP_CONTRACT_FIELDS and value not in (None, "", [])})
    if payload.get("fen"):
        chess_img["fen"] = payload["fen"]
    return payload


def _chess_fen_record(*, page_num: int, filename: str, result, source: str) -> dict:
    payload = result.to_dict()
    payload.update(_basic_two_crop_contract_fields(filename, payload.get("bbox")))
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
SCAN_CHESS_RECOGNITION_CACHE_VERSION = 8
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
        chess_diagram_records: list[dict[str, Any]] = []
        chess_fen_review_artifact_files: list[dict[str, Any]] = []
        page_marker_assignment_reports: list[dict[str, Any]] = []
        book_layout_pages: list[dict[str, Any]] = []
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
            page = doc[page_num]
            page_width = float(page.rect.width or page_image.width)
            page_height = float(page.rect.height or page_image.height)

            html_parts = []
            book_elements: list[dict[str, Any]] = []
            ocr_record = selective_ocr_pages.get(str(page_num)) or selective_ocr_pages.get(page_num)
            page_pgn_records = []
            if isinstance(ocr_record, dict):
                html_parts.extend(_scan_chess_ocr_html_parts(ocr_record, page_num=page_num))
                ocr_lines = [line.strip() for line in str(ocr_record.get("text") or "").splitlines() if line.strip()]
                for line_index, line in enumerate(ocr_lines[:42]):
                    y0 = 34.0 + line_index * 12.5
                    if y0 > page_height - 24.0:
                        break
                    book_elements.append(
                        {
                            "type": "text",
                            "bbox": [34.0, y0, min(page_width - 28.0, page_width * 0.72), y0 + 12.0],
                            "reading_order": 100 + line_index,
                            "text": line,
                            "font_size": 9.5,
                        }
                    )
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
            page_marker_boards: list[dict[str, Any]] = []
            for board_index, page_candidate in enumerate(candidates, start=1):
                page_bbox = _clamp_bbox(page_candidate.get("bbox"), page_image.size, pad_ratio=0.0, min_pad=0.0)
                if page_bbox is not None:
                    page_marker_boards.append(
                        {
                            "diagram_id": f"scan-chess-p{page_num + 1:03d}-d{board_index:02d}",
                            "bbox": tuple(float(value) for value in page_bbox),
                        }
                    )
            page_marker_assignment = (
                _scan_chess_page_marker_pipeline(
                    page_image,
                    page_marker_boards,
                    page_number=page_num + 1,
                )
                if bool(getattr(config, "chess_fen_apply_side_marker", False))
                else {
                    "candidates": [],
                    "assignments": [],
                    "files": [],
                    "summary": {},
                }
            )
            if bool(getattr(config, "chess_fen_apply_side_marker", False)):
                page_marker_assignment_reports.append(
                    {
                        key: value
                        for key, value in page_marker_assignment.items()
                        if key != "files"
                    }
                )
            chess_fen_review_artifact_files.extend(page_marker_assignment.get("files") or [])
            marker_assignments_by_id = {
                str(item.get("diagram_id") or ""): dict(item)
                for item in page_marker_assignment.get("assignments") or []
                if isinstance(item, Mapping)
            }
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
                diagram_id = f"scan-chess-p{page_num + 1:03d}-d{candidate_index:02d}"
                marker_assignment = marker_assignments_by_id.get(diagram_id) or {}
                if bool(getattr(config, "chess_fen_apply_side_marker", False)):
                    side_min_confidence = float(getattr(config, "chess_fen_min_confidence", 0.835) or 0.835)
                    candidate_payload = _scan_chess_apply_page_marker_assignment(
                        candidate_payload,
                        marker_assignment,
                        page_marker_assignment.get("candidates") or [],
                    )
                two_crop_fields, two_crop_files = _scan_chess_two_crop_review_artifacts(
                    page_image,
                    filename=filename,
                    board_bbox=recognition_bbox,
                    side_marker_bbox=(
                        None
                        if bool(getattr(config, "chess_fen_apply_side_marker", False))
                        else candidate_payload.get("side_marker_bbox")
                    ),
                    marker_assignment=(
                        marker_assignment
                        if bool(getattr(config, "chess_fen_apply_side_marker", False))
                        else None
                    ),
                )
                candidate_payload.update(two_crop_fields)
                candidate_payload = _apply_scan_chess_two_crop_quality_gate(candidate_payload, two_crop_fields)
                if bool(getattr(config, "chess_fen_apply_side_marker", False)):
                    candidate_payload = _apply_scan_chess_two_crop_side_marker_if_trusted(
                        candidate_payload,
                        two_crop_fields,
                        min_confidence=side_min_confidence,
                    )
                candidate_payload.update(two_crop_fields)
                chess_fen_review_artifact_files.extend(two_crop_files)
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
                    **{key: candidate_payload.get(key) for key in TWO_CROP_CONTRACT_FIELDS if candidate_payload.get(key) not in (None, "", [])},
                    **preprocess_metadata,
                }
                if candidate_payload.get("fen"):
                    chess_img["fen"] = candidate_payload["fen"]
                    page_fen_values.append(str(candidate_payload["fen"]))
                chapter_images.append(chess_img)
                all_images.append(chess_img)
                diagram_caption = f"Strona {page_num + 1}, diagram {candidate_index}"
                chess_diagram_records.append(
                    _chess_diagram_record_from_image(
                        chess_img,
                        diagram_id=diagram_id,
                        caption=diagram_caption,
                        page_num=page_num,
                        nearby_text=str((ocr_record or {}).get("text") or ""),
                    )
                )
                scaled_bbox = _scale_bbox(
                    tuple(float(value) for value in bbox),
                    source_size=page_image.size,
                    target_width=page_width,
                    target_height=page_height,
                )
                diagram_data_uri = "data:image/png;base64," + base64.b64encode(png_data).decode("ascii")
                book_elements.append(
                    {
                        "type": "diagram",
                        "bbox": scaled_bbox,
                        "reading_order": 1_000 + candidate_index * 10,
                        "title": diagram_caption,
                        "image_data_uri": diagram_data_uri,
                    }
                )
                if candidate_payload.get("fen"):
                    book_elements.append(
                        {
                            "type": "fen",
                            "bbox": [
                                scaled_bbox[0],
                                scaled_bbox[3] + 4.0,
                                scaled_bbox[2],
                                min(page_height - 8.0, scaled_bbox[3] + 30.0),
                            ],
                            "reading_order": 1_000 + candidate_index * 10 + 1,
                            "fen": str(candidate_payload.get("fen") or ""),
                        }
                    )
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
                    f'<p class="diagram-caption">{html_module.escape(diagram_caption)}</p>'
                    f'<div class="figure chess-diagram-container"{fen_attrs}>'
                    f"{chess_side_marker_html(chess_img)}"
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
                if book_elements:
                    book_layout_pages.append(
                        _book_layout_page_from_pdf_page(
                            page,
                            page_num,
                            config,
                            elements=book_elements,
                        )
                    )
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
        chess_pgn_summary = summarize_chess_pgn_records(chess_pgn_records, diagram_records=chess_fen_records)
        chess_fen_summary = summarize_chess_fen_results(chess_fen_records)
        side_marker_runtime_counts = {
            key: int(chess_fen_summary.get(key) or 0)
            for key in (
                "side_marker_probe_checked_count",
                "side_marker_crop_count",
                "trusted_marker_count",
                "marker_missing_count",
                "marker_conflict_count",
                "marker_ambiguous_count",
                "side_to_move_inferred_count",
                "side_unknown_count",
            )
        }
        page_marker_runtime_summary = _scan_chess_page_marker_runtime_summary(
            page_marker_assignment_reports
        )
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
            **side_marker_runtime_counts,
            **page_marker_runtime_summary,
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
                "chess_fen": chess_fen_summary,
                "page_marker_assignment": page_marker_runtime_summary,
                "ocr_quality": ocr_quality,
                "reading_flow": {
                    "status": "passed_with_warnings",
                    "mode": "scan_chess_crops",
                    "full_page_images_included": False,
                },
                "chess_pgn": chess_pgn_summary,
            }
        )
        book_layout_pages = _ensure_book_layout_pages_cover_document(doc, book_layout_pages, config)
        page_marker_report = {
            "schema": "kindlemaster.chess.page_marker_assignment_report.v1",
            "status": "ok" if page_marker_assignment_reports else "not_run",
            "summary": page_marker_runtime_summary,
            "pages": page_marker_assignment_reports,
            "policy": (
                "Page-level candidates are generated before trust classification; ownership is one-to-one, "
                "and semantic/FEN promotion still requires the existing tight-crop quality gate."
            ),
        }
        chess_fen_review_artifact_files.append(
            {
                "path": "reports/chess_fen/page_marker_assignment.json",
                "data": json.dumps(page_marker_report, ensure_ascii=False, indent=2).encode("utf-8"),
            }
        )
        extra_artifacts = _chess_pdf_extra_artifacts(
            pdf_path,
            config,
            source_title=str(metadata.get("title") or Path(pdf_path).stem),
            pgn_records=chess_pgn_records,
            diagrams=chess_diagram_records,
            book_layout_pages=book_layout_pages,
            review_artifact_files=chess_fen_review_artifact_files,
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
                    **side_marker_runtime_counts,
                    **page_marker_runtime_summary,
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
    allow_explicit_trim_recovery: bool | None = None,
):
    if allow_explicit_trim_recovery is None:
        allow_explicit_trim_recovery = allow_reader_visible_crop_rescue
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
        allow_recognition_recovery=False,
    )
    if (
        getattr(recognition, "fen", "")
        and not getattr(recognition, "requires_review", True)
        and float(getattr(recognition, "confidence", 0.0) or 0.0) >= max(0.90, min_confidence + 0.15)
    ):
        return recognition
    if not allow_reader_visible_crop_rescue or not _scan_chess_recognition_needs_bbox_recovery(recognition):
        return recognition

    best_review_result = recognition
    best_review_crop_data = output.getvalue()
    best_review_bbox = tuple(float(value) for value in bbox)

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
        allow_recognition_recovery=False,
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
    if getattr(reader_recognition, "requires_review", True):
        best_review_result = _prefer_scan_chess_recognition_result(best_review_result, reader_recognition)
        if best_review_result is reader_recognition:
            best_review_crop_data = reader_data
            best_review_bbox = tuple(float(value) for value in reader_clamped)
    expanded_reader_clamped = _clamp_bbox(tuple(float(value) for value in reader_clamped), page_image.size)
    if expanded_reader_clamped is not None and tuple(expanded_reader_clamped) != tuple(reader_clamped):
        expanded_reader_crop = page_image.crop(expanded_reader_clamped)
        expanded_reader_data, _, _ = _encode_scan_chess_diagram_crop(expanded_reader_crop, config)
        expanded_reader_recognition = _recognize_scan_chess_crop_with_cache(
            expanded_reader_data,
            bbox=tuple(float(value) for value in expanded_reader_clamped),
            min_confidence=min_confidence,
            piece_templates=piece_templates,
            allow_recognition_recovery=False,
        )
        if _scan_chess_reader_visible_crop_publish_is_safe(
            recognition,
            expanded_reader_recognition,
            min_confidence=min_confidence,
        ):
            return _scan_chess_result_with_warning(expanded_reader_recognition, "reader_expanded_crop_fen_used")
        sparse_consensus = _scan_chess_sparse_exact_consensus_result(
            recognition,
            expanded_reader_recognition,
            min_confidence=min_confidence,
            warning="reader_expanded_crop_sparse_consensus_fen_used",
        )
        if sparse_consensus is not None:
            return sparse_consensus
        if getattr(expanded_reader_recognition, "requires_review", True):
            best_review_result = _prefer_scan_chess_recognition_result(best_review_result, expanded_reader_recognition)
            if best_review_result is expanded_reader_recognition:
                best_review_crop_data = expanded_reader_data
                best_review_bbox = tuple(float(value) for value in expanded_reader_clamped)

    raw_recovery_warnings = {str(warning) for warning in (getattr(recognition, "warnings", []) or [])}
    best_review_warnings = {str(warning) for warning in (getattr(best_review_result, "warnings", []) or [])}
    trim_recovery_warnings = raw_recovery_warnings | best_review_warnings
    if (
        allow_explicit_trim_recovery
        and _scan_chess_recognition_needs_bbox_recovery(best_review_result)
        and (
            any(warning.endswith("king_count_invalid") for warning in trim_recovery_warnings)
            or "annotation_cross_marker_suppressed" in trim_recovery_warnings
        )
    ):
        recovered_recognition = _recognize_scan_chess_crop_with_cache(
            best_review_crop_data,
            bbox=best_review_bbox,
            min_confidence=min_confidence,
            piece_templates=piece_templates,
            allow_recognition_recovery=True,
        )
        if getattr(recovered_recognition, "fen", "") and not getattr(recovered_recognition, "requires_review", True):
            return recovered_recognition
        if getattr(recovered_recognition, "requires_review", True):
            best_review_result = _prefer_scan_chess_recognition_result(best_review_result, recovered_recognition)

    if getattr(best_review_result, "requires_review", True):
        recovery_signal_warnings = {
            warning
            for warning in raw_recovery_warnings
            if warning.endswith("king_count_invalid")
            or warning == "annotation_cross_marker_suppressed"
            or warning == "sparse_position_confidence_below_threshold"
        }
        if recovery_signal_warnings:
            merged_warnings = sorted({*list(getattr(best_review_result, "warnings", []) or []), *recovery_signal_warnings})
            return ChessFenResult(
                fen=str(getattr(best_review_result, "fen", "") or ""),
                placement=str(getattr(best_review_result, "placement", "") or ""),
                confidence=float(getattr(best_review_result, "confidence", 0.0) or 0.0),
                side_to_move=str(getattr(best_review_result, "side_to_move", "w") or "w"),
                bbox=getattr(best_review_result, "bbox", None),
                method=str(getattr(best_review_result, "method", "") or "image-template-board"),
                warnings=merged_warnings,
                requires_review=True,
                board_detected=bool(getattr(best_review_result, "board_detected", False)),
                squares=[dict(square) for square in (getattr(best_review_result, "squares", []) or []) if isinstance(square, dict)],
            )

    return best_review_result


def _recognize_scan_chess_crop_with_cache(
    crop_bytes: bytes,
    *,
    bbox: tuple[float, float, float, float],
    min_confidence: float,
    piece_templates: dict,
    allow_recognition_recovery: bool = True,
):
    cache_path = _scan_chess_recognition_cache_path(
        crop_bytes,
        bbox=bbox,
        min_confidence=min_confidence,
        piece_templates=piece_templates,
        allow_recognition_recovery=allow_recognition_recovery,
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
            allow_recognition_recovery=allow_recognition_recovery,
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
        allow_recognition_recovery=allow_recognition_recovery,
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
        allow_recognition_recovery=True,
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
    allow_recognition_recovery: bool = True,
) -> Path:
    rounded_bbox = ",".join(f"{float(value):.2f}" for value in bbox)
    recovery_mode = "recovery" if allow_recognition_recovery else "base"
    token = "|".join(
        [
            str(SCAN_CHESS_RECOGNITION_CACHE_VERSION),
            recovery_mode,
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
    allow_recognition_recovery: bool = True,
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
    recovery_mode = "recovery" if allow_recognition_recovery else "base"
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
                recovery_mode,
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
            "recognition_inner_border_trim_used",
            "recognition_caption_bottom_trim_used",
            "recognition_side_marker_trim_used",
            "recognition_corner_marker_trim_used",
            "final_rendered_crop_fen_used",
            "reader_visible_crop_fen_used",
            "reader_visible_crop_sparse_consensus_fen_used",
            "reader_expanded_crop_fen_used",
            "reader_expanded_crop_sparse_consensus_fen_used",
            "final_rendered_crop_sparse_consensus_fen_used",
            "sparse_exact_crop_consensus",
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
    warnings = sorted({*fen_warnings, "verified_exact_crop_label_used"})
    return ChessFenResult(
        fen=fen,
        placement=placement,
        full_fen=fen,
        confidence=1.0,
        side_to_move=side_to_move,
        side_to_move_status="explicit",
        side_to_move_evidence="exact_label",
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
    return (
        any(str(warning).endswith("king_count_invalid") for warning in warnings)
        or "annotation_cross_marker_suppressed" in warnings
        or "sparse_position_confidence_below_threshold" in warnings
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

    if not any(warning.endswith("king_count_invalid") for warning in raw_warnings) and "annotation_cross_marker_suppressed" not in raw_warnings:
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
            allow_explicit_trim_recovery=False,
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
            allow_explicit_trim_recovery=False,
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


def _chess_diagram_record_from_image(
    chess_img: Mapping[str, Any],
    *,
    diagram_id: str,
    caption: str,
    page_num: int,
    nearby_text: str = "",
    matched_record_id: str = "",
) -> dict[str, Any]:
    image_data = chess_img.get("data")
    image_data_uri = ""
    if isinstance(image_data, (bytes, bytearray)):
        extension = str(chess_img.get("extension") or "png").lower().lstrip(".") or "png"
        mime = "image/jpeg" if extension in {"jpg", "jpeg"} else f"image/{extension}"
        image_data_uri = f"data:{mime};base64,{base64.b64encode(bytes(image_data)).decode('ascii')}"
    raw_page_index = chess_img.get("page_index", chess_img.get("page", chess_img.get("page_num", page_num)))
    try:
        page_index = int(raw_page_index)
    except (TypeError, ValueError):
        page_index = int(page_num)
    fen_result = chess_img.get("fen_result") if isinstance(chess_img.get("fen_result"), Mapping) else {}
    side_marker = _scan_chess_side_marker_metadata_from_payload(fen_result) if fen_result else {}
    fen_candidate = str(chess_img.get("fen") or fen_result.get("fen") or "").strip()
    placement_status = str(
        fen_result.get("placement_status")
        or fen_result.get("placement_runtime_status")
        or chess_img.get("placement_status")
        or chess_img.get("placement_runtime_status")
        or ""
    )
    full_fen_status = str(
        fen_result.get("full_fen_status")
        or fen_result.get("full_fen_runtime_status")
        or chess_img.get("full_fen_status")
        or chess_img.get("full_fen_runtime_status")
        or ("FEN_MACHINE_ACCEPTED" if fen_candidate and not bool(fen_result.get("requires_review")) else "FEN_REVIEW_REQUIRED")
    )
    raw_bbox = chess_img.get("bbox") or (0.0, 0.0, 0.0, 0.0)
    try:
        bbox = [float(value or 0.0) for value in list(raw_bbox)[:4]]
    except (TypeError, ValueError):
        bbox = []
    while len(bbox) < 4:
        bbox.append(0.0)
    board_crop_path = str(fen_result.get("board_crop_path") or chess_img.get("board_crop_path") or "").strip()
    if not board_crop_path and str(chess_img.get("filename") or "").strip():
        board_crop_path = f"images/{chess_img.get('filename')}"
    return {
        "id": diagram_id,
        "page_index": page_index,
        "page_number": page_index + 1,
        "bbox": bbox[:4],
        "board_bbox": fen_result.get("board_bbox") or chess_img.get("board_bbox") or bbox[:4],
        "board_crop_path": board_crop_path,
        "side_marker_crop_path": str(fen_result.get("side_marker_crop_path") or chess_img.get("side_marker_crop_path") or "").strip(),
        "side_marker_search_crop_path": str(
            fen_result.get("side_marker_search_crop_path") or chess_img.get("side_marker_search_crop_path") or ""
        ).strip(),
        "side_marker_search_bbox": list(fen_result.get("side_marker_search_bbox") or chess_img.get("side_marker_search_bbox") or []),
        "marker_search_zone_preview_path": str(
            fen_result.get("marker_search_zone_preview_path") or chess_img.get("marker_search_zone_preview_path") or ""
        ).strip(),
        "marker_search_zone_preview_bbox": list(
            fen_result.get("marker_search_zone_preview_bbox") or chess_img.get("marker_search_zone_preview_bbox") or []
        ),
        "side_marker_review_crop_path": str(
            fen_result.get("side_marker_review_crop_path")
            or chess_img.get("side_marker_review_crop_path")
            or fen_result.get("side_marker_crop_path")
            or chess_img.get("side_marker_crop_path")
            or fen_result.get("side_marker_search_crop_path")
            or chess_img.get("side_marker_search_crop_path")
            or ""
        ).strip(),
        "side_marker_review_crop_kind": str(
            fen_result.get("side_marker_review_crop_kind") or chess_img.get("side_marker_review_crop_kind") or ""
        ).strip(),
        "debug_overlay_path": str(fen_result.get("debug_overlay_path") or chess_img.get("debug_overlay_path") or "").strip(),
        "board_crop_quality": str(fen_result.get("board_crop_quality") or chess_img.get("board_crop_quality") or ""),
        "board_crop_fail_reason": list(fen_result.get("board_crop_fail_reason") or chess_img.get("board_crop_fail_reason") or []),
        "board_crop_quality_gate": dict(fen_result.get("board_crop_quality_gate") or chess_img.get("board_crop_quality_gate") or {}),
        "marker_search_zones": dict(fen_result.get("marker_search_zones") or chess_img.get("marker_search_zones") or {}),
        "selected_marker_zone": fen_result.get("selected_marker_zone") or chess_img.get("selected_marker_zone") or "",
        "marker_bbox": list(
            fen_result.get("marker_bbox")
            or chess_img.get("marker_bbox")
            or fen_result.get("side_marker_bbox")
            or chess_img.get("side_marker_bbox")
            or []
        ),
        "marker_crop_bbox": list(
            fen_result.get("marker_crop_bbox")
            or chess_img.get("marker_crop_bbox")
            or fen_result.get("marker_bbox")
            or chess_img.get("marker_bbox")
            or []
        ),
        "marker_crop_quality": str(fen_result.get("marker_crop_quality") or chess_img.get("marker_crop_quality") or ""),
        "marker_crop_fail_reason": list(fen_result.get("marker_crop_fail_reason") or chess_img.get("marker_crop_fail_reason") or []),
        "marker_crop_quality_gate": dict(fen_result.get("marker_crop_quality_gate") or chess_img.get("marker_crop_quality_gate") or {}),
        "side_to_move_detected": fen_result.get("side_to_move_detected") or chess_img.get("side_to_move_detected"),
        "side_to_move_confidence": fen_result.get("side_to_move_confidence") or chess_img.get("side_to_move_confidence"),
        "manual_review_required": bool(
            fen_result.get("manual_review_required", chess_img.get("manual_review_required", True))
        ),
        "manual_review_reason": str(fen_result.get("manual_review_reason") or chess_img.get("manual_review_reason") or ""),
        "caption": caption,
        "image_data_uri": image_data_uri,
        "fen_candidate": fen_candidate,
        "placement": str(fen_result.get("placement") or fen_result.get("placement_fen") or "").strip(),
        "placement_fen": str(fen_result.get("placement") or fen_result.get("placement_fen") or "").strip(),
        "full_fen": str(fen_result.get("full_fen") or "").strip(),
        "status": "accepted" if fen_candidate and not bool(fen_result.get("requires_review")) else "needs_review",
        "placement_status": placement_status,
        "full_fen_status": full_fen_status,
        "reason": "" if fen_candidate and not bool(fen_result.get("requires_review")) else _fen_result_review_reason(fen_result),
        "warnings": list(fen_result.get("warnings") or []),
        "side_to_move": side_marker.get("side_to_move") or fen_result.get("side_to_move") or "unknown",
        "side_to_move_status": str(fen_result.get("side_to_move_status") or ""),
        "side_to_move_evidence": str(fen_result.get("side_to_move_evidence") or ""),
        "side_marker_symbol": side_marker.get("side_marker_symbol") or "",
        "side_marker_status": side_marker.get("side_marker_status") or "",
        "side_marker_source": side_marker.get("side_marker_source") or "",
        "side_marker_bbox": side_marker.get("side_marker_bbox") or [],
        "side_marker_confidence": side_marker.get("side_marker_confidence") or "",
        "side_marker_assignment_trace": side_marker.get("side_marker_assignment_trace") or {},
        "marker_semantic_status": str(side_marker.get("marker_semantic_status") or "missing"),
        "marker_semantic_side": str(side_marker.get("marker_semantic_side") or "unknown"),
        "marker_semantic_confidence": float(side_marker.get("marker_semantic_confidence") or 0.0),
        "marker_ownership_status": str(side_marker.get("marker_ownership_status") or "unassigned"),
        "board_placement_status": str(side_marker.get("board_placement_status") or "review"),
        "full_fen_allowed": bool(side_marker.get("full_fen_allowed")),
        "full_fen_blockers": list(side_marker.get("full_fen_blockers") or []),
        "full_fen_blocker": str(side_marker.get("full_fen_blocker") or ""),
        "marker_candidate_id": str(fen_result.get("marker_candidate_id") or ""),
        "marker_candidate_bbox": list(fen_result.get("marker_candidate_bbox") or []),
        "marker_candidate_crop_path": str(fen_result.get("marker_candidate_crop_path") or ""),
        "marker_candidate_features": dict(fen_result.get("marker_candidate_features") or {}),
        "marker_candidate_class": str(fen_result.get("marker_candidate_class") or ""),
        "marker_candidate_confidence": float(
            fen_result.get("marker_candidate_confidence") or 0.0
        ),
        "marker_assignment_status": str(
            fen_result.get("marker_assignment_status") or "unassigned"
        ),
        "marker_assignment_confidence": float(
            fen_result.get("marker_assignment_confidence") or 0.0
        ),
        "marker_assignment_runner_up_margin": float(
            fen_result.get("marker_assignment_runner_up_margin") or 0.0
        ),
        "marker_assignment_rejected_reasons": list(
            fen_result.get("marker_assignment_rejected_reasons") or []
        ),
        "strict_fen_side_evidence_trusted": bool(side_marker.get("strict_fen_side_evidence_trusted")),
        "fen_suppressed_reason": str(fen_result.get("fen_suppressed_reason") or ""),
        "fen_confidence": float(fen_result.get("confidence", 0.0) or 0.0),
        "fen_method": str(fen_result.get("method") or chess_img.get("fen_method") or ""),
        "nearby_text": nearby_text,
        "matched_record_id": matched_record_id,
        "match_confidence": 0.0,
    }


def _fen_result_review_reason(fen_result: Mapping[str, Any]) -> str:
    if not fen_result:
        return "fen_not_recognized"
    if not fen_result.get("board_detected", False):
        return "board_not_detected"
    if not str(fen_result.get("fen") or "").strip():
        return "fen_not_recognized"
    if fen_result.get("requires_review", True):
        return "fen_below_acceptance_threshold"
    return "fen_requires_review"


def _book_layout_page_from_pdf_page(
    page: fitz.Page,
    page_num: int,
    config: ConversionConfig,
    *,
    elements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    dpi = max(48, min(int(getattr(config, "pdf_layout_preview_dpi", 96) or 96), 180))
    jpeg_quality = max(45, min(int(getattr(config, "pdf_layout_preview_jpeg_quality", 72) or 72), 92))
    return {
        "page_index": page_num,
        "page_number": page_num + 1,
        "width": float(page.rect.width or 0.0),
        "height": float(page.rect.height or 0.0),
        "background_image_data_uri": _render_pdf_page_data_uri(
            page,
            scale=dpi / 72.0,
            jpeg_quality=jpeg_quality,
        ),
        "elements": list(elements or []),
    }


def _ensure_book_layout_pages_cover_document(
    doc: fitz.Document,
    pages: list[Mapping[str, Any]],
    config: ConversionConfig,
) -> list[dict[str, Any]]:
    pages_by_index: dict[int, dict[str, Any]] = {}
    for page in pages:
        try:
            page_index = int(page.get("page_index"))
        except (TypeError, ValueError):
            try:
                page_index = int(page.get("page_number")) - 1
            except (TypeError, ValueError):
                continue
        if page_index < 0 or page_index >= len(doc) or page_index in pages_by_index:
            continue
        pages_by_index[page_index] = dict(page)
    for page_index in range(len(doc)):
        if page_index in pages_by_index:
            continue
        pages_by_index[page_index] = _book_layout_page_from_pdf_page(
            doc[page_index],
            page_index,
            config,
            elements=[],
        )
    return [pages_by_index[index] for index in sorted(pages_by_index)]


def _book_layout_text_elements_from_line_items(
    line_items: list[TextLineItem],
    *,
    reading_order_start: int = 0,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    for offset, item in enumerate(_order_chess_notation_lines_for_reading(line_items), start=reading_order_start):
        text = _clean_chess_notation_line(item.text)
        if not text:
            continue
        height = max(8.0, float(item.font_size or 10.0) * 1.35)
        elements.append(
            {
                "type": "text",
                "bbox": [
                    float(item.x0 or 0.0),
                    float(item.y or 0.0),
                    float(item.x1 or item.x0 or 0.0),
                    float(item.y or 0.0) + height,
                ],
                "reading_order": offset,
                "text": text,
                "font_size": float(item.font_size or 10.0),
                "warnings": list(item.glyph_warnings or []),
            }
        )
    return elements


def _book_layout_diagram_elements_from_diagrams(
    diagrams: list[Mapping[str, Any]],
    *,
    page_num: int,
    reading_order_start: int = 10_000,
) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    page_diagrams = [
        diagram
        for diagram in diagrams
        if _book_layout_diagram_page_index(diagram, fallback_page_num=page_num) == page_num
    ]
    for index, diagram in enumerate(
        sorted(page_diagrams, key=lambda item: (_bbox_sort_key(item.get("bbox")), str(item.get("id") or ""))),
        start=reading_order_start,
    ):
        bbox = _bbox_list(diagram.get("bbox")) or [0.0, 0.0, 1.0, 1.0]
        elements.append(
            {
                "type": "diagram",
                "bbox": bbox,
                "reading_order": index,
                "title": str(diagram.get("caption") or diagram.get("id") or "Chess diagram"),
                "image_data_uri": str(diagram.get("image_data_uri") or ""),
                "record_id": str(diagram.get("matched_record_id") or ""),
            }
        )
        fen = str(diagram.get("fen_candidate") or "").strip()
        fen_status = str(diagram.get("status") or "").strip().lower()
        if fen and fen_status == "accepted" and _fen_string_is_parser_valid(fen):
            elements.append(
                {
                    "type": "fen",
                    "bbox": [bbox[0], bbox[3] + 4.0, bbox[2], min(bbox[3] + 30.0, bbox[3] + 4.0 + 26.0)],
                    "reading_order": index + 1,
                    "fen": fen,
                    "text": fen,
                    "record_id": str(diagram.get("matched_record_id") or ""),
                }
            )
        elif fen or str(diagram.get("reason") or "").strip():
            elements.append(
                {
                    "type": "review_warning",
                    "bbox": [bbox[0], bbox[3] + 4.0, bbox[2], min(bbox[3] + 34.0, bbox[3] + 4.0 + 30.0)],
                    "reading_order": index + 1,
                    "text": str(diagram.get("reason") or "FEN requires review"),
                    "warnings": list(diagram.get("warnings") or []),
                    "record_id": str(diagram.get("matched_record_id") or ""),
                }
            )
    return elements


def _fen_string_is_parser_valid(fen: str) -> bool:
    valid, warnings = validate_fen(fen)
    if not valid or warnings:
        return False
    try:
        import chess

        chess.Board(fen)
    except Exception:
        return False
    return True


def _book_layout_diagram_page_index(diagram: Mapping[str, Any], *, fallback_page_num: int) -> int:
    try:
        return int(diagram.get("page_index"))
    except (TypeError, ValueError):
        pass
    try:
        return max(0, int(diagram.get("page_number")) - 1)
    except (TypeError, ValueError):
        return int(fallback_page_num)


def _attach_pgn_records_to_book_layout_pages(
    pages: list[Mapping[str, Any]],
    records: list[Any],
) -> list[dict[str, Any]]:
    page_rows = [dict(page) for page in pages]
    if not page_rows:
        return []
    pages_by_number = {int(page.get("page_number") or 0): page for page in page_rows}
    record_counts_by_page: dict[int, int] = {}
    for record in records:
        source_pages = list(getattr(record, "source_pages", []) or [1])
        page_number = int(source_pages[0] or 1)
        page = pages_by_number.get(page_number)
        if page is None:
            continue
        elements = list(page.get("elements") or [])
        slot = record_counts_by_page.get(page_number, 0)
        record_counts_by_page[page_number] = slot + 1
        width = float(page.get("width") or 600.0)
        height = float(page.get("height") or 800.0)
        box_width = min(260.0, max(180.0, width * 0.40))
        x0 = max(18.0, width - box_width - 24.0)
        y0 = min(max(42.0, 42.0 + slot * 128.0), max(42.0, height - 150.0))
        strict_pgn = build_combined_pgn([record]).strip()
        status = str(getattr(record, "status", "") or "requires_review")
        warnings = list(getattr(record, "warnings", []) or [])
        record_id = str(getattr(record, "id", "") or f"record-{page_number}-{slot + 1}")
        if not strict_pgn:
            elements.append(
                {
                    "type": "review_warning",
                    "bbox": [x0, max(8.0, y0 - 32.0), min(width - 12.0, x0 + box_width), max(34.0, y0 - 6.0)],
                    "reading_order": 19_000 + slot * 2,
                    "record_id": record_id,
                    "text": "PGN requires review; strict export is blocked.",
                    "warnings": warnings,
                }
            )
        elements.append(
            {
                "type": "pgn_record",
                "bbox": [x0, y0, min(width - 12.0, x0 + box_width), min(height - 12.0, y0 + 116.0)],
                "reading_order": 20_000 + slot * 2,
                "record_id": record_id,
                "title": str(getattr(record, "title", "") or f"PGN page {page_number}")[:160],
                "status": "accepted" if strict_pgn else status or "requires_review",
                "fen": str(getattr(record, "final_fen", "") or getattr(record, "fen", "") or ""),
                "pgn": strict_pgn,
                "warnings": warnings,
            }
        )
        page["elements"] = elements
    return page_rows


def _bbox_sort_key(value: Any) -> tuple[float, float]:
    bbox = _bbox_list(value)
    if not bbox:
        return (0.0, 0.0)
    return (bbox[1], bbox[0])


def _scale_bbox(
    bbox: tuple[float, float, float, float] | list[float],
    *,
    source_size: tuple[int, int],
    target_width: float,
    target_height: float,
) -> list[float]:
    source_width = max(1.0, float(source_size[0] or 1))
    source_height = max(1.0, float(source_size[1] or 1))
    scale_x = float(target_width or source_width) / source_width
    scale_y = float(target_height or source_height) / source_height
    x0, y0, x1, y1 = [float(value or 0.0) for value in list(bbox)[:4]]
    return [x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y]


TWO_CROP_CONTRACT_FIELDS = {
    "board_crop_path",
    "side_marker_crop_path",
    "side_marker_search_crop_path",
    "side_marker_search_bbox",
    "marker_search_zone_preview_path",
    "marker_search_zone_preview_bbox",
    "side_marker_review_crop_path",
    "side_marker_review_crop_kind",
    "debug_overlay_path",
    "debug_context_crop_path",
    "debug_context_bbox",
    "raw_board_candidate_bbox",
    "tight_board_bbox",
    "board_bbox",
    "board_crop_quality",
    "board_crop_fail_reason",
    "board_crop_quality_gate",
    "marker_search_zones",
    "selected_marker_zone",
    "marker_bbox",
    "marker_crop_bbox",
    "marker_crop_quality",
    "marker_crop_fail_reason",
    "marker_crop_quality_gate",
    "marker_classifier_version",
    "marker_classifier_reason",
    "marker_classifier_confidence",
    "marker_classifier_symbol",
    "side_marker_bbox",
    "side_to_move_detected",
    "side_to_move_confidence",
    "manual_review_required",
    "manual_review_reason",
    "marker_semantic_status",
    "marker_semantic_side",
    "marker_semantic_confidence",
    "marker_ownership_status",
    "board_placement_status",
    "full_fen_allowed",
    "full_fen_blockers",
    "full_fen_blocker",
    "marker_candidate_id",
    "marker_candidate_bbox",
    "marker_candidate_crop_bbox",
    "marker_candidate_crop_path",
    "marker_candidate_features",
    "marker_candidate_class",
    "marker_candidate_classifier_status",
    "marker_candidate_side",
    "marker_candidate_confidence",
    "marker_assignment_status",
    "marker_assignment_confidence",
    "marker_assignment_runner_up_margin",
    "marker_assignment_ownership_margin",
    "marker_assignment_cost",
    "marker_assignment_zone",
    "marker_assignment_rejected_reasons",
    "two_crop_performance",
}

BOARD_CROP_REASON_CODES = {
    "not_square",
    "cell_size_mismatch",
    "board_cut_off",
    "too_much_margin",
    "contains_coordinates",
    "contains_marker",
    "contains_text",
    "contains_neighbor_diagram",
}

MARKER_CROP_REASON_CODES = {
    "marker_missing",
    "marker_cut_off",
    "multiple_candidates",
    "mostly_board_edge",
    "mostly_rank_numbers",
    "mostly_file_letters",
    "too_narrow",
    "marker_too_small",
    "unclear_symbol",
    "wrong_marker_candidate",
    "marker_crop_not_generated",
    "outside_expected_zone",
}


def _basic_two_crop_contract_fields(filename: Any, bbox: Any) -> dict[str, Any]:
    path = f"images/{filename}" if str(filename or "").strip() else ""
    return {
        "board_crop_path": path,
        "side_marker_crop_path": "",
        "debug_overlay_path": "",
        "board_bbox": _coerce_side_marker_bbox(bbox),
    }


def _scan_chess_projection_groups(values: np.ndarray, *, threshold: float, min_length: int = 1) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(values):
        if float(value) >= threshold:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= min_length:
                groups.append((start, index - 1))
            start = None
    if start is not None and len(values) - start >= min_length:
        groups.append((start, len(values) - 1))
    return groups


def _scan_chess_board_square_score(image: Image.Image) -> float:
    grayscale = ImageOps.autocontrast(image.convert("L"))
    detected, signal = _has_board_visual_pattern(grayscale)
    if not detected:
        return 0.0
    grid = _estimate_board_grid_confidence(grayscale)
    return float(signal) * 2.0 + float(grid)


def _scan_chess_regular_grid_axis(groups: list[tuple[int, int]]) -> tuple[int, int] | None:
    if len(groups) < 9:
        return None
    centers = [(start + end) / 2.0 for start, end in groups]
    best: tuple[float, int, int] | None = None
    for start_index in range(0, len(groups) - 8):
        window = centers[start_index : start_index + 9]
        spacing = (window[-1] - window[0]) / 8.0
        if spacing < 8.0:
            continue
        deviations = [abs(window[i] - (window[0] + spacing * i)) for i in range(9)]
        max_deviation = max(deviations)
        if max_deviation > max(2.5, spacing * 0.18):
            continue
        score = spacing - max_deviation * 0.8
        if best is None or score > best[0]:
            best = (score, start_index, start_index + 8)
    if best is None:
        return None
    _score, first, last = best
    return groups[first][0], groups[last][1] + 1


def _scan_chess_grid_line_board_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    grayscale = ImageOps.autocontrast(image.convert("L"))
    pixels = np.array(grayscale, dtype=np.uint8)
    if pixels.size == 0:
        return None
    dark = pixels < 100
    row_groups = _scan_chess_projection_groups(dark.mean(axis=1), threshold=0.34, min_length=1)
    col_groups = _scan_chess_projection_groups(dark.mean(axis=0), threshold=0.34, min_length=1)
    x_axis = _scan_chess_regular_grid_axis(col_groups)
    y_axis = _scan_chess_regular_grid_axis(row_groups)
    if x_axis is None or y_axis is None:
        return None
    x0, x1 = x_axis
    y0, y1 = y_axis
    width = x1 - x0
    height = y1 - y0
    if width < 64 or height < 64:
        return None
    ratio = width / max(1.0, float(height))
    if not (0.90 <= ratio <= 1.10):
        return None
    side = int(round((width + height) / 2.0))
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    left = int(round(cx - side / 2.0))
    top = int(round(cy - side / 2.0))
    left = min(max(0, left), max(0, image.width - side))
    top = min(max(0, top), max(0, image.height - side))
    return (left, top, left + side, top + side)


def _increment_two_crop_metric(metrics: dict[str, Any] | None, key: str, amount: int = 1) -> None:
    if metrics is not None:
        metrics[key] = int(metrics.get(key) or 0) + amount


@dataclass(frozen=True)
class _ScanChessBoardAnalysis:
    raw_box: tuple[int, int, int, int] | None
    selected_box: tuple[int, int, int, int] | None
    local_tight_box: tuple[int, int, int, int] | None
    derivation: dict[str, Any]


def _scan_chess_tight_board_box_in_crop(
    image: Image.Image,
    *,
    performance: dict[str, Any] | None = None,
) -> tuple[int, int, int, int] | None:
    _increment_two_crop_metric(performance, "tight_board_localization_call_count")
    grayscale = ImageOps.autocontrast(image.convert("L"))
    width, height = grayscale.size
    min_axis = min(width, height)
    if min_axis < 64:
        return None
    grid_box = _scan_chess_grid_line_board_box(grayscale)
    if grid_box is not None:
        fullish_grid = (
            grid_box[0] <= 2
            and grid_box[1] <= 2
            and abs(grid_box[2] - width) <= 4
            and abs(grid_box[3] - height) <= 4
        )
        if not fullish_grid:
            return grid_box

    baseline_side = min_axis
    baseline_left = max(0, (width - baseline_side) // 2)
    baseline_top = max(0, (height - baseline_side) // 2)
    baseline_box = (baseline_left, baseline_top, baseline_left + baseline_side, baseline_top + baseline_side)
    _increment_two_crop_metric(performance, "sliding_window_candidate_evaluations")
    baseline_score = _scan_chess_board_square_score(grayscale.crop(baseline_box))

    side_values = sorted(
        {
            int(round(min_axis * ratio))
            for ratio in (1.0, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72, 0.68, 0.64, 0.60)
            if int(round(min_axis * ratio)) >= 64
        },
        reverse=True,
    )
    best: tuple[float, int, int, int] | None = None
    for side in side_values:
        x_stride = max(4, side // 18)
        y_stride = max(4, side // 18)
        left_values = set(range(0, max(1, width - side + 1), x_stride))
        top_values = set(range(0, max(1, height - side + 1), y_stride))
        left_values.add(max(0, width - side))
        top_values.add(max(0, height - side))
        for top in sorted(top_values):
            for left in sorted(left_values):
                box = (left, top, left + side, top + side)
                _increment_two_crop_metric(performance, "sliding_window_candidate_evaluations")
                score = _scan_chess_board_square_score(grayscale.crop(box))
                if score <= 0.0:
                    continue
                center_penalty = (
                    abs((left + side / 2.0) - width / 2.0) / max(1.0, float(width))
                    + abs((top + side / 2.0) - height / 2.0) / max(1.0, float(height))
                ) * 0.08
                candidate_score = score - center_penalty
                if best is None or candidate_score > best[0]:
                    best = (candidate_score, left, top, side)

    if best is None:
        return None
    score, left, top, side = best
    fullish = left <= 2 and top <= 2 and abs(side - width) <= 4 and abs(side - height) <= 4
    if fullish:
        return None
    crop_ratio = width / max(1.0, float(height))
    if 0.95 <= crop_ratio <= 1.05 and side < min_axis * 0.82:
        return None
    if score < baseline_score + 0.05:
        return None
    return (left, top, left + side, top + side)


def _scan_chess_region_has_dark_content(image: Image.Image) -> bool:
    if image.width < 2 or image.height < 2:
        return False
    pixels = np.array(ImageOps.autocontrast(image.convert("L")), dtype=np.uint8)
    if pixels.size == 0:
        return False
    return float((pixels < 170).mean()) >= 0.0015


def _scan_chess_region_contains_marker(image: Image.Image) -> bool:
    if image.width < 12 or image.height < 12:
        return False
    grayscale = ImageOps.autocontrast(image.convert("L"))
    result = classify_scan_chess_side_marker_crop(grayscale)
    if str(result.get("status") or "") in {
        "trusted_marker",
        "side_to_move_marker_local_conflict",
    }:
        return True
    pixels = np.array(grayscale, dtype=np.uint8)
    dark_y, dark_x = np.where(pixels < 150)
    if len(dark_x) < 8 or len(dark_y) < 8:
        return False
    x0 = max(0, int(dark_x.min()) - 3)
    y0 = max(0, int(dark_y.min()) - 3)
    x1 = min(image.width, int(dark_x.max()) + 4)
    y1 = min(image.height, int(dark_y.max()) + 4)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return False
    result = classify_scan_chess_side_marker_crop(grayscale.crop((x0, y0, x1, y1)))
    return str(result.get("status") or "") in {
        "trusted_marker",
        "side_to_move_marker_local_conflict",
    }


def _scan_chess_region_contains_neighbor_diagram(image: Image.Image, board_side: float) -> bool:
    if image.width < max(48, board_side * 0.35) or image.height < max(48, board_side * 0.35):
        return False
    detected, signal = _has_board_visual_pattern(ImageOps.autocontrast(image.convert("L")))
    if detected and signal >= 0.18:
        return True
    pixels = np.array(ImageOps.autocontrast(image.convert("L")), dtype=np.uint8)
    dark = pixels < 90
    row_groups = _scan_chess_projection_groups(dark.mean(axis=1), threshold=0.12, min_length=1)
    col_groups = _scan_chess_projection_groups(dark.mean(axis=0), threshold=0.12, min_length=1)
    return len(row_groups) >= 6 and len(col_groups) >= 6


def _scan_chess_region_contains_coordinates(image: Image.Image, orientation: str) -> bool:
    if image.width < 8 or image.height < 8:
        return False
    pixels = np.array(ImageOps.autocontrast(image.convert("L")), dtype=np.uint8)
    dark = pixels < 145
    if dark.mean() < 0.004:
        return False
    if orientation in {"top", "bottom"}:
        groups = _scan_chess_projection_groups(dark.mean(axis=0), threshold=0.015, min_length=1)
        small_groups = [group for group in groups if 1 <= group[1] - group[0] + 1 <= max(12, image.width // 12)]
        if len(small_groups) < 4:
            return False
        span = small_groups[-1][1] - small_groups[0][0]
        return span >= image.width * 0.45
    groups = _scan_chess_projection_groups(dark.mean(axis=1), threshold=0.015, min_length=1)
    small_groups = [group for group in groups if 1 <= group[1] - group[0] + 1 <= max(12, image.height // 12)]
    if len(small_groups) < 4:
        return False
    span = small_groups[-1][1] - small_groups[0][0]
    return span >= image.height * 0.45


def _scan_chess_board_margin_reasons(image: Image.Image, local_board_box: tuple[int, int, int, int]) -> list[str]:
    width, height = image.size
    x0, y0, x1, y1 = local_board_box
    board_side = max(1.0, float(max(x1 - x0, y1 - y0)))
    margin_threshold = max(4.0, board_side * 0.03)
    margin_regions = {
        "top": (0, 0, width, max(0, y0)),
        "bottom": (0, min(height, y1), width, height),
        "left": (0, max(0, y0), max(0, x0), min(height, y1)),
        "right": (min(width, x1), max(0, y0), width, min(height, y1)),
    }
    visible_margins = [
        name
        for name, box in margin_regions.items()
        if (box[2] - box[0] if name in {"left", "right"} else box[3] - box[1]) >= margin_threshold
    ]
    reasons: set[str] = set()
    if visible_margins:
        reasons.add("too_much_margin")
    for name in visible_margins:
        box = margin_regions[name]
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        region = image.crop(box)
        if not _scan_chess_region_has_dark_content(region):
            continue
        if _scan_chess_region_contains_neighbor_diagram(region, board_side):
            reasons.add("contains_neighbor_diagram")
        if _scan_chess_region_contains_marker(region):
            reasons.add("contains_marker")
        elif _scan_chess_region_contains_coordinates(region, name):
            reasons.add("contains_coordinates")
        else:
            reasons.add("contains_text")
    return sorted(reasons)


def _scan_chess_tight_board_bbox_from_candidate(
    page_image: Image.Image,
    board_bbox: Any,
    *,
    performance: dict[str, Any] | None = None,
) -> tuple[list[float], dict[str, Any]]:
    analysis = _scan_chess_board_analysis_from_candidate(
        page_image,
        board_bbox,
        performance=performance,
    )
    selected = analysis.selected_box or ()
    return [float(value) for value in selected], dict(analysis.derivation)


def _scan_chess_board_analysis_from_candidate(
    page_image: Image.Image,
    board_bbox: Any,
    *,
    performance: dict[str, Any] | None = None,
) -> _ScanChessBoardAnalysis:
    raw_box = _bbox_to_int_box(_coerce_side_marker_bbox(board_bbox), page_image.size)
    if raw_box is None:
        return _ScanChessBoardAnalysis(
            raw_box=None,
            selected_box=None,
            local_tight_box=None,
            derivation={"decision": "fail", "reasons": ["board_bbox_missing"]},
        )
    crop = page_image.crop(raw_box).convert("RGB")
    local_tight = _scan_chess_tight_board_box_in_crop(crop, performance=performance)
    if local_tight is None:
        tight_box = raw_box
        reasons: list[str] = []
    else:
        tight_box = (
            raw_box[0] + local_tight[0],
            raw_box[1] + local_tight[1],
            raw_box[0] + local_tight[2],
            raw_box[1] + local_tight[3],
        )
        reasons = _scan_chess_board_margin_reasons(crop, local_tight)
    return _ScanChessBoardAnalysis(
        raw_box=raw_box,
        selected_box=tight_box,
        local_tight_box=local_tight,
        derivation={
            "decision": "adjusted" if local_tight is not None else "unchanged",
            "reasons": reasons,
            "raw_bbox": [float(value) for value in raw_box],
            "tight_bbox": [float(value) for value in tight_box],
        },
    )


def _scan_chess_two_crop_review_artifacts(
    page_image: Image.Image,
    *,
    filename: str,
    board_bbox: Any,
    side_marker_bbox: Any,
    marker_assignment: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    total_started = time.perf_counter()
    performance: dict[str, Any] = {
        "tight_board_localization_call_count": 0,
        "sliding_window_candidate_evaluations": 0,
        "localization_seconds": 0.0,
        "marker_analysis_seconds": 0.0,
        "png_encoding_seconds": 0.0,
        "png_encoded_artifact_count": 0,
        "png_encoded_bytes": 0,
        "file_write_measured": False,
        "file_write_seconds": 0.0,
        "file_written_artifact_count": 0,
        "file_written_bytes": 0,
        "board_analysis_mode": "single_pass",
        "legacy_localization_fallback_used": False,
        "legacy_localization_fallback_count": 0,
        "legacy_localization_fallback_reason": "",
        "ambiguity_probe_evaluations": 0,
        "ambiguity_probe_seconds": 0.0,
        "total_seconds": 0.0,
    }
    stem = Path(str(filename or "diagram")).stem or "diagram"
    raw_board_bbox = _coerce_side_marker_bbox(board_bbox)
    localization_started = time.perf_counter()
    board_analysis = _scan_chess_board_analysis_from_candidate(
        page_image,
        raw_board_bbox,
        performance=performance,
    )
    tight_board_bbox = [float(value) for value in (board_analysis.selected_box or ())]
    tight_board_trace = dict(board_analysis.derivation)
    board_bbox_for_crop = tight_board_bbox or raw_board_bbox
    fields: dict[str, Any] = {
        "board_crop_path": f"review/chess_fen/two_crop/{stem}_board.png",
        "side_marker_crop_path": "",
        "side_marker_search_crop_path": "",
        "side_marker_search_bbox": [],
        "marker_search_zone_preview_path": "",
        "marker_search_zone_preview_bbox": [],
        "side_marker_review_crop_path": "",
        "side_marker_review_crop_kind": "missing",
        "debug_overlay_path": f"review/chess_fen/two_crop/{stem}_overlay.png",
        "debug_context_crop_path": "",
        "debug_context_bbox": raw_board_bbox,
        "raw_board_candidate_bbox": raw_board_bbox,
        "tight_board_bbox": board_bbox_for_crop,
        "board_bbox": board_bbox_for_crop,
        "board_bbox_derivation": tight_board_trace,
        "marker_search_zones": {},
        "selected_marker_zone": None,
        "marker_bbox": [],
        "marker_crop_bbox": [],
        "board_crop_quality": "fail",
        "board_crop_fail_reason": ["board_bbox_missing"],
        "board_crop_quality_gate": {"decision": "fail", "reasons": ["board_bbox_missing"]},
        "marker_crop_quality": "fail",
        "marker_crop_fail_reason": ["marker_missing"],
        "marker_crop_quality_gate": {"decision": "fail", "reasons": ["marker_missing"]},
        "marker_classifier_version": "marker_shape_v2",
        "marker_classifier_reason": "marker_missing",
        "marker_classifier_confidence": 0.0,
        "marker_classifier_symbol": None,
        "side_to_move_detected": None,
        "side_to_move_confidence": 0.0,
        "manual_review_required": True,
        "manual_review_reason": "marker_missing",
        **_scan_chess_empty_marker_assignment_fields(),
    }
    if marker_assignment is not None:
        fields.update(
            {
                key: marker_assignment.get(key, fields.get(key))
                for key in _scan_chess_empty_marker_assignment_fields()
            }
        )
    files: list[dict[str, Any]] = []
    board_quality = _scan_chess_board_crop_quality(
        page_image,
        fields["board_bbox"],
        board_analysis=board_analysis,
        performance=performance,
    )
    performance["localization_seconds"] = time.perf_counter() - localization_started
    fields["board_crop_quality_gate"] = board_quality
    fields["board_crop_quality"] = "pass" if board_quality.get("decision") == "pass" else "fail"
    fields["board_crop_fail_reason"] = list(board_quality.get("reasons") or [])

    board_crop = _crop_bbox_from_image(page_image, fields["board_bbox"])
    if board_crop is not None:
        files.append({"path": fields["board_crop_path"], "data": _timed_png_bytes(board_crop, performance)})
    raw_box = _bbox_to_int_box(raw_board_bbox, page_image.size)
    tight_box = _bbox_to_int_box(fields["board_bbox"], page_image.size)
    if raw_box is not None and tight_box is not None and raw_box != tight_box:
        context_crop = _crop_bbox_from_image(page_image, raw_board_bbox)
        if context_crop is not None:
            fields["debug_context_crop_path"] = f"review/chess_fen/two_crop/{stem}_context.png"
            files.append({"path": fields["debug_context_crop_path"], "data": _timed_png_bytes(context_crop, performance)})

    marker_started = time.perf_counter()
    marker_png_seconds_before = float(performance["png_encoding_seconds"])
    zones = _scan_chess_marker_search_zones(fields["board_bbox"], page_image.size)
    fields["marker_search_zones"] = zones
    search_bbox, search_crop = _scan_chess_marker_search_zone_preview(page_image, fields["board_bbox"], zones)
    if search_crop is not None:
        fields["side_marker_search_crop_path"] = f"review/chess_fen/two_crop/{stem}_marker_search.png"
        fields["side_marker_search_bbox"] = search_bbox
        fields["marker_search_zone_preview_path"] = fields["side_marker_search_crop_path"]
        fields["marker_search_zone_preview_bbox"] = search_bbox
        files.append({"path": fields["side_marker_search_crop_path"], "data": _timed_png_bytes(search_crop, performance)})

    marker_source = ""
    marker_bbox = []
    marker_crop_bbox = []
    candidate = None
    if marker_assignment is not None:
        assignment_status = str(marker_assignment.get("marker_assignment_status") or "unassigned")
        if assignment_status != "unassigned":
            marker_bbox = _coerce_side_marker_bbox(marker_assignment.get("marker_candidate_bbox"))
            marker_crop_bbox = _coerce_side_marker_bbox(
                marker_assignment.get("marker_candidate_crop_bbox")
            )
            marker_source = "page_level_assignment"
    else:
        region_candidate = _scan_chess_marker_component_candidate_from_region(
            page_image,
            side_marker_bbox,
            fields["board_bbox"],
            source="side_marker_bbox",
        )
        if region_candidate:
            marker_bbox = _coerce_side_marker_bbox(region_candidate.get("marker_bbox"))
            marker_crop_bbox = _coerce_side_marker_bbox(region_candidate.get("marker_crop_bbox"))
            marker_source = str(region_candidate.get("source") or "")
        elif _coerce_side_marker_bbox(side_marker_bbox):
            marker_bbox = _coerce_side_marker_bbox(side_marker_bbox)
            marker_crop_bbox = _scan_chess_padded_marker_bbox(marker_bbox, fields["board_bbox"], page_image.size)
            marker_source = "side_marker_bbox"
        candidate = _scan_chess_best_marker_zone_candidate(page_image, fields["board_bbox"], zones)
    if marker_bbox and not marker_crop_bbox:
        marker_crop_bbox = _scan_chess_padded_marker_bbox(marker_bbox, fields["board_bbox"], page_image.size)
    if not marker_bbox and candidate and candidate.get("status") == "trusted_marker":
        marker_bbox = _coerce_side_marker_bbox(candidate.get("marker_bbox"))
        marker_crop_bbox = _coerce_side_marker_bbox(candidate.get("marker_crop_bbox"))
        marker_source = str(candidate.get("source") or "marker_search_zone")
    elif not marker_bbox and candidate and candidate.get("status") == "multiple_candidates":
        fields["marker_crop_quality_gate"] = {
            "decision": "fail",
            "reasons": ["multiple_candidates"],
            "side_to_move": None,
            "confidence": 0.0,
            "classifier_status": "side_to_move_marker_local_conflict",
            "component_count": int(candidate.get("component_count") or 2),
        }
        fields["marker_crop_quality"] = "fail"
        fields["marker_crop_fail_reason"] = ["multiple_candidates"]
        fields["manual_review_required"] = True
        fields["manual_review_reason"] = "multiple"
    selected_zone = _scan_chess_selected_marker_zone(marker_bbox, zones) if marker_bbox else ""
    if not selected_zone and candidate:
        selected_zone = str(candidate.get("zone") or "")
    if selected_zone:
        fields["selected_marker_zone"] = selected_zone
    if marker_bbox:
        fields["marker_bbox"] = marker_bbox
        fields["side_marker_bbox"] = marker_bbox
        fields["marker_crop_bbox"] = marker_crop_bbox or marker_bbox
        fields["side_marker_crop_path"] = f"review/chess_fen/two_crop/{stem}_marker.png"
        marker_quality = _scan_chess_marker_crop_quality(
            page_image,
            fields["marker_crop_bbox"],
            fields["board_bbox"],
            marker_bbox=marker_bbox,
            marker_search_zones=zones,
        )
        if marker_source:
            marker_quality["source"] = marker_source
        fields["marker_crop_quality_gate"] = marker_quality
        fields["marker_crop_quality"] = "pass" if marker_quality.get("decision") == "pass" else "fail"
        fields["marker_crop_fail_reason"] = list(marker_quality.get("reasons") or [])
        fields["marker_classifier_version"] = marker_quality.get("classifier_version") or "marker_shape_v2"
        fields["marker_classifier_reason"] = marker_quality.get("reason") or ""
        fields["marker_classifier_confidence"] = marker_quality.get("confidence", 0.0)
        fields["marker_classifier_symbol"] = marker_quality.get("symbol")
        if marker_quality.get("side_to_move") in {"white", "black"}:
            fields["side_to_move_detected"] = marker_quality.get("side_to_move")
            fields["side_to_move_confidence"] = marker_quality.get("confidence", 0.0)
        marker_crop = _crop_bbox_from_image(page_image, fields["marker_crop_bbox"])
        if marker_crop is not None:
            files.append({"path": fields["side_marker_crop_path"], "data": _timed_png_bytes(marker_crop, performance)})
            fields["side_marker_review_crop_path"] = fields["side_marker_crop_path"]
            fields["side_marker_review_crop_kind"] = (
                "detected_marker_bbox" if marker_quality.get("decision") == "pass" else "detected_marker_bbox_quality_failed"
            )
            fields["manual_review_required"] = fields["board_crop_quality"] != "pass" or fields["marker_crop_quality"] != "pass"
            fields["manual_review_reason"] = "" if not fields["manual_review_required"] else _scan_chess_manual_review_reason(fields["marker_crop_fail_reason"])
        else:
            fields["side_marker_crop_path"] = ""
            reasons = sorted(set(list(fields.get("marker_crop_fail_reason") or []) + ["marker_crop_not_generated"]))
            gate = dict(fields.get("marker_crop_quality_gate") or {})
            gate["decision"] = "fail"
            gate["reasons"] = reasons
            gate["reason_codes"] = {
                reason: reasons.count(reason)
                for reason in sorted(MARKER_CROP_REASON_CODES)
                if reason in reasons
            }
            fields["marker_crop_quality_gate"] = gate
            fields["marker_crop_quality"] = "fail"
            fields["marker_crop_fail_reason"] = reasons
            fields["side_marker_review_crop_path"] = fields["side_marker_search_crop_path"]
            fields["side_marker_review_crop_kind"] = "marker_search_zone_preview" if fields["side_marker_search_crop_path"] else "missing"
            fields["manual_review_required"] = True
            fields["manual_review_reason"] = _scan_chess_manual_review_reason(fields["marker_crop_fail_reason"])
    elif fields["side_marker_search_crop_path"]:
        fields["side_marker_review_crop_path"] = fields["side_marker_search_crop_path"]
        fields["side_marker_review_crop_kind"] = "marker_search_zone_preview"

    marker_elapsed = time.perf_counter() - marker_started
    marker_png_elapsed = float(performance["png_encoding_seconds"]) - marker_png_seconds_before
    performance["marker_analysis_seconds"] = max(0.0, marker_elapsed - marker_png_elapsed)

    if fields["board_crop_quality"] != "pass":
        fields["manual_review_required"] = True
        fields["manual_review_reason"] = "bad_crop"
    overlay = _scan_chess_debug_overlay(
        page_image,
        fields["board_bbox"],
        marker_bbox,
        marker_search_zones=zones,
        selected_marker_zone=str(fields.get("selected_marker_zone") or ""),
        board_crop_quality=str(fields.get("board_crop_quality") or ""),
        marker_crop_quality=str(fields.get("marker_crop_quality") or ""),
    )
    if overlay is not None:
        files.append({"path": fields["debug_overlay_path"], "data": _timed_png_bytes(overlay, performance)})
    else:
        fields["debug_overlay_path"] = ""
    performance["total_seconds"] = time.perf_counter() - total_started
    fields["two_crop_performance"] = _rounded_two_crop_performance(performance)
    return fields, files


def _scan_chess_board_crop_quality(
    page_image: Image.Image,
    board_bbox: Any,
    *,
    board_analysis: _ScanChessBoardAnalysis | None = None,
    performance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    board = _coerce_side_marker_bbox(board_bbox)
    box = _bbox_to_int_box(board, page_image.size)
    reasons: list[str] = []
    if box is None:
        reasons.append("board_bbox_missing")
        return {"decision": "fail", "reasons": reasons}
    width = max(1.0, board[2] - board[0])
    height = max(1.0, board[3] - board[1])
    ratio = width / height
    cell_size_x = width / 8.0
    cell_size_y = height / 8.0
    if ratio < 0.95 or ratio > 1.05:
        reasons.append("not_square")
    if abs(cell_size_x - cell_size_y) / max(cell_size_x, cell_size_y) > 0.08:
        reasons.append("cell_size_mismatch")
    if box[0] <= 0 or box[1] <= 0 or box[2] >= page_image.width or box[3] >= page_image.height:
        reasons.append("board_cut_off")
    crop = page_image.crop(box).convert("RGB")
    fallback_reason = ""
    if board_analysis is None or board_analysis.selected_box != box:
        fallback_reason = "board_analysis_missing_or_mismatch"
    elif board_analysis.local_tight_box is not None and _scan_chess_single_pass_needs_fallback(
        crop,
        performance=performance,
    ):
        fallback_reason = "residual_board_candidate_gain"
    local_tight = None
    if fallback_reason:
        if performance is not None:
            performance["board_analysis_mode"] = "legacy_fallback"
            performance["legacy_localization_fallback_used"] = True
            performance["legacy_localization_fallback_reason"] = fallback_reason
        _increment_two_crop_metric(performance, "legacy_localization_fallback_count")
        local_tight = _scan_chess_tight_board_box_in_crop(crop, performance=performance)
    if local_tight is not None:
        reasons.extend(_scan_chess_board_margin_reasons(crop, local_tight))
    reasons = sorted(set(reasons))
    return {
        "decision": "fail" if reasons else "pass",
        "reasons": reasons,
        "ratio": round(ratio, 4),
        "cell_size_x": round(cell_size_x, 2),
        "cell_size_y": round(cell_size_y, 2),
        "reason_codes": {reason: reasons.count(reason) for reason in sorted(BOARD_CROP_REASON_CODES) if reason in reasons},
    }


def _scan_chess_single_pass_needs_fallback(
    crop: Image.Image,
    *,
    performance: dict[str, Any] | None = None,
) -> bool:
    started = time.perf_counter()
    grayscale = ImageOps.autocontrast(crop.convert("L"))
    width, height = grayscale.size
    min_axis = min(width, height)
    if min_axis < 64:
        if performance is not None:
            performance["ambiguity_probe_seconds"] = float(
                performance.get("ambiguity_probe_seconds") or 0.0
            ) + (time.perf_counter() - started)
        return False
    baseline_score = _scan_chess_board_square_score(grayscale)
    ambiguous = False
    for ratio in (0.96, 0.92):
        side = int(round(min_axis * ratio))
        if side < 64 or side >= min_axis:
            continue
        candidates = {
            (0, 0),
            (max(0, width - side), 0),
            (0, max(0, height - side)),
            (max(0, width - side), max(0, height - side)),
            (max(0, (width - side) // 2), max(0, (height - side) // 2)),
        }
        for left, top in candidates:
            _increment_two_crop_metric(performance, "ambiguity_probe_evaluations")
            score = _scan_chess_board_square_score(
                grayscale.crop((left, top, left + side, top + side))
            )
            center_penalty = (
                abs((left + side / 2.0) - width / 2.0) / max(1.0, float(width))
                + abs((top + side / 2.0) - height / 2.0) / max(1.0, float(height))
            ) * 0.08
            if score - center_penalty >= baseline_score + 0.05:
                ambiguous = True
                break
        if ambiguous:
            break
    if performance is not None:
        performance["ambiguity_probe_seconds"] = float(
            performance.get("ambiguity_probe_seconds") or 0.0
        ) + (time.perf_counter() - started)
    return ambiguous


def _scan_chess_marker_search_zones(board_bbox: Any, page_size: tuple[int, int]) -> dict[str, list[float]]:
    board = _coerce_side_marker_bbox(board_bbox)
    if not board:
        return {}
    x0, y0, x1, y1 = board
    cell = min(max(1.0, x1 - x0), max(1.0, y1 - y0)) / 8.0
    raw = {
        "top": (x0 - 0.5 * cell, y0 - 1.5 * cell, x1 + 0.5 * cell, y0),
        "right": (x1, y0 - 0.5 * cell, x1 + 1.5 * cell, y1 + 0.5 * cell),
        "bottom": (x0 - 0.5 * cell, y1, x1 + 0.5 * cell, y1 + 1.5 * cell),
        "left": (x0 - 1.5 * cell, y0 - 0.5 * cell, x0, y1 + 0.5 * cell),
    }
    zones: dict[str, list[float]] = {}
    for name, bbox in raw.items():
        box = _bbox_to_int_box(bbox, page_size)
        if box is not None:
            zones[name] = [float(value) for value in box]
    return zones


def _scan_chess_marker_search_zone_preview(
    page_image: Image.Image,
    board_bbox: Any,
    zones: Mapping[str, Any],
) -> tuple[list[float], Image.Image | None]:
    all_boxes = [_coerce_side_marker_bbox(value) for value in zones.values()]
    all_boxes = [box for box in all_boxes if box]
    board = _coerce_side_marker_bbox(board_bbox)
    if not board or not all_boxes:
        return [], None
    x0 = min([board[0], *[box[0] for box in all_boxes]])
    y0 = min([board[1], *[box[1] for box in all_boxes]])
    x1 = max([board[2], *[box[2] for box in all_boxes]])
    y1 = max([board[3], *[box[3] for box in all_boxes]])
    search_box = _bbox_to_int_box((x0, y0, x1, y1), page_image.size)
    board_box = _bbox_to_int_box(board, page_image.size)
    if search_box is None:
        return [], None
    crop = page_image.crop(search_box).convert("RGB")
    draw = ImageDraw.Draw(crop)
    if board_box is not None:
        local_board = (
            max(0, board_box[0] - search_box[0]),
            max(0, board_box[1] - search_box[1]),
            min(search_box[2] - search_box[0], board_box[2] - search_box[0]),
            min(search_box[3] - search_box[1], board_box[3] - search_box[1]),
        )
        if local_board[2] > local_board[0] and local_board[3] > local_board[1]:
            draw.rectangle(local_board, fill="#f8fafc", outline="#94a3b8", width=2)
    for zone in all_boxes:
        local = (zone[0] - search_box[0], zone[1] - search_box[1], zone[2] - search_box[0], zone[3] - search_box[1])
        draw.rectangle(local, outline="#f59e0b", width=2)
    return [float(value) for value in search_box], crop


def _scan_chess_dark_components(mask: Any) -> list[dict[str, float]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[dict[str, float]] = []
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
            density = area / max(1, box_width * box_height)
            components.append(
                {
                    "area": float(area),
                    "density": float(density),
                    "bbox": (float(min_x), float(min_y), float(max_x), float(max_y)),
                    "width": float(box_width),
                    "height": float(box_height),
                    "score": float(area),
                }
            )
    return components


def _scan_chess_marker_component_candidates_from_region(
    page_image: Image.Image,
    region_bbox: Any,
    board_bbox: Any,
    *,
    source: str,
) -> list[dict[str, Any]]:
    board = _coerce_side_marker_bbox(board_bbox)
    region_box = _bbox_to_int_box(region_bbox, page_image.size)
    if region_box is None or not board:
        return []
    cell = min(max(1.0, board[2] - board[0]), max(1.0, board[3] - board[1])) / 8.0
    crop = ImageOps.autocontrast(page_image.crop(region_box).convert("L"))
    dark = np.asarray(crop) < 120
    candidates: list[dict[str, Any]] = []
    for component in _scan_chess_dark_components(dark):
        raw_bbox = component.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            continue
        local_x0, local_y0, local_x1, local_y1 = [float(value) for value in raw_bbox]
        width = max(1.0, local_x1 - local_x0 + 1.0)
        height = max(1.0, local_y1 - local_y0 + 1.0)
        if width < max(8.0, cell * 0.22) or height < max(8.0, cell * 0.22):
            continue
        if width > max(24.0, cell * 1.9) or height > max(24.0, cell * 1.9):
            continue
        aspect = width / max(1.0, height)
        if not (0.55 <= aspect <= 1.85):
            continue
        density = float(component.get("density") or 0.0)
        if not (0.12 <= density <= 0.78):
            continue
        marker_bbox = [
            float(region_box[0]) + local_x0,
            float(region_box[1]) + local_y0,
            float(region_box[0]) + local_x1,
            float(region_box[1]) + local_y1,
        ]
        if _bbox_overlap_ratio(tuple(float(value) for value in marker_bbox), tuple(float(value) for value in board)) > 0.12:
            continue
        marker_crop_bbox = _scan_chess_padded_marker_bbox(marker_bbox, board, page_image.size)
        marker_crop = _crop_bbox_from_image(page_image, marker_crop_bbox)
        if marker_crop is None:
            continue
        classification = classify_scan_chess_side_marker_crop(marker_crop)
        score = float(component.get("score") or 0.0)
        candidates.append(
            {
                "source": source,
                "zone_bbox": [float(value) for value in region_box],
                "marker_bbox": [round(float(value), 2) for value in marker_bbox],
                "marker_crop_bbox": marker_crop_bbox,
                "component_bbox": [round(float(value), 2) for value in marker_bbox],
                "local_component_bbox": [round(float(value), 2) for value in (local_x0, local_y0, local_x1, local_y1)],
                "status": classification.get("status"),
                "side": classification.get("side") or "",
                "shape": classification.get("shape") or "",
                "confidence": classification.get("confidence") or 0.0,
                "score": score,
            }
        )
    return candidates


def _scan_chess_marker_component_candidate_from_region(
    page_image: Image.Image,
    region_bbox: Any,
    board_bbox: Any,
    *,
    source: str,
) -> dict[str, Any] | None:
    candidates = _scan_chess_marker_component_candidates_from_region(page_image, region_bbox, board_bbox, source=source)
    trusted = [item for item in candidates if item.get("status") == "trusted_marker" and item.get("side") in {"w", "b"}]
    if len(trusted) != 1:
        return None
    return max(trusted, key=lambda item: float(item.get("score") or 0.0))


def _scan_chess_unique_marker_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    ranked = sorted(candidates, key=lambda item: float(item.get("score") or 0.0), reverse=True)
    for candidate in ranked:
        bbox = _coerce_side_marker_bbox(candidate.get("marker_bbox") or candidate.get("component_bbox"))
        if not bbox:
            continue
        duplicate = False
        for existing in unique:
            existing_bbox = _coerce_side_marker_bbox(existing.get("marker_bbox") or existing.get("component_bbox"))
            if existing_bbox and _bbox_overlap_ratio(tuple(bbox), tuple(existing_bbox)) >= 0.72:
                duplicate = True
                break
        if not duplicate:
            unique.append(dict(candidate))
    return unique


def _scan_chess_best_marker_zone_candidate(
    page_image: Image.Image,
    board_bbox: Any,
    zones: Mapping[str, Any],
) -> dict[str, Any] | None:
    board = _coerce_side_marker_bbox(board_bbox)
    if not board:
        return None
    candidates: list[dict[str, Any]] = []
    for zone_name, raw_zone in zones.items():
        zone = _coerce_side_marker_bbox(raw_zone)
        zone_candidates = _scan_chess_marker_component_candidates_from_region(
            page_image,
            zone,
            board,
            source="marker_search_zone",
        )
        for item in zone_candidates:
            enriched = dict(item)
            enriched["zone"] = str(zone_name)
            candidates.append(enriched)
    trusted = [item for item in candidates if item.get("status") == "trusted_marker" and item.get("side") in {"w", "b"}]
    trusted = _scan_chess_unique_marker_candidates(trusted)
    if not trusted:
        return None
    if len(trusted) > 1:
        return {
            "status": "multiple_candidates",
            "zone": str(trusted[0].get("zone") or ""),
            "component_count": len(trusted),
            "candidates": trusted,
        }
    sides = {str(item.get("side") or "") for item in trusted}
    if len(sides) != 1:
        return {
            "status": "multiple_candidates",
            "zone": str(trusted[0].get("zone") or ""),
            "component_count": len(trusted),
            "candidates": trusted,
        }
    return max(trusted, key=lambda item: float(item.get("score") or 0.0))


PAGE_MARKER_ASSIGNMENT_SCHEMA = "kindlemaster.chess.page_marker_assignment.v1"
PAGE_MARKER_UNASSIGNED_COST = 2.75


def _scan_chess_page_marker_pipeline(
    page_image: Image.Image,
    boards: Iterable[Mapping[str, Any]],
    *,
    page_number: int,
    top_k: int = 0,
) -> dict[str, Any]:
    """Detect marker candidates once and assign each candidate to at most one board."""
    normalized_boards = _scan_chess_normalized_page_boards(boards)
    candidates, files = _scan_chess_page_marker_candidates(
        page_image,
        normalized_boards,
        page_number=page_number,
        top_k=top_k,
    )
    assignment = _scan_chess_assign_page_marker_candidates(
        normalized_boards,
        candidates,
        page_size=page_image.size,
    )
    return {
        "schema": PAGE_MARKER_ASSIGNMENT_SCHEMA,
        "page": max(1, int(page_number or 1)),
        "board_count": len(normalized_boards),
        "marker_candidate_count": len(candidates),
        "marker_candidate_crop_count": len(
            [candidate for candidate in candidates if candidate.get("marker_candidate_crop_path")]
        ),
        "candidates": assignment["candidates"],
        "assignments": assignment["assignments"],
        "cost_matrix": assignment["cost_matrix"],
        "summary": assignment["summary"],
        "files": files,
    }


def _scan_chess_page_marker_runtime_summary(
    pages: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    page_rows = [dict(page) for page in pages]
    summaries = [
        dict(page.get("summary") or {})
        for page in page_rows
        if isinstance(page.get("summary"), Mapping)
    ]
    board_count = sum(int(summary.get("board_count") or 0) for summary in summaries)
    candidate_count = sum(int(summary.get("marker_candidate_count") or 0) for summary in summaries)
    assigned_count = sum(int(summary.get("assigned_marker_count") or 0) for summary in summaries)
    confident_count = sum(int(summary.get("confident_ownership_count") or 0) for summary in summaries)
    duplicate_count = sum(
        int(summary.get("duplicate_marker_ownership_count") or 0) for summary in summaries
    )
    crop_count = sum(
        len(
            [
                candidate
                for candidate in page.get("candidates") or []
                if candidate.get("marker_candidate_crop_path")
            ]
        )
        for page in page_rows
    )
    return {
        "page_marker_detection_run_count": len(page_rows),
        "page_marker_board_count": board_count,
        "marker_candidate_count": candidate_count,
        "marker_candidate_crop_count": crop_count,
        "marker_candidate_assigned_count": assigned_count,
        "marker_candidate_recall_proxy_rate": round(assigned_count / board_count, 4)
        if board_count
        else 0.0,
        "marker_ownership_confident_count": confident_count,
        "marker_ownership_confident_rate": round(confident_count / assigned_count, 4)
        if assigned_count
        else 0.0,
        "duplicate_marker_ownership_count": duplicate_count,
    }


def _scan_chess_normalized_page_boards(
    boards: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, board in enumerate(boards, start=1):
        bbox = _coerce_side_marker_bbox(board.get("bbox") or board.get("board_bbox"))
        if not bbox:
            continue
        board_id = str(board.get("diagram_id") or board.get("id") or f"diagram_{index:03d}")
        normalized.append(
            {
                "diagram_id": board_id,
                "bbox": bbox,
                "board_index": int(board.get("board_index") or index),
            }
        )
    normalized.sort(key=lambda item: (item["bbox"][1], item["bbox"][0], item["diagram_id"]))
    return normalized


def _scan_chess_page_marker_candidates(
    page_image: Image.Image,
    boards: Iterable[Mapping[str, Any]],
    *,
    page_number: int,
    top_k: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_boards = _scan_chess_normalized_page_boards(boards)
    if not normalized_boards:
        return [], []
    allowed = np.zeros((page_image.height, page_image.width), dtype=bool)
    board_boxes = [board["bbox"] for board in normalized_boards]
    cell_sizes = [
        min(max(1.0, bbox[2] - bbox[0]), max(1.0, bbox[3] - bbox[1])) / 8.0
        for bbox in board_boxes
    ]
    for board_bbox in board_boxes:
        for zone_bbox in _scan_chess_marker_search_zones(board_bbox, page_image.size).values():
            zone = _bbox_to_int_box(zone_bbox, page_image.size)
            if zone is not None:
                allowed[zone[1] : zone[3], zone[0] : zone[2]] = True
    for board_bbox in board_boxes:
        board = _bbox_to_int_box(board_bbox, page_image.size)
        if board is not None:
            allowed[board[1] : board[3], board[0] : board[2]] = False

    grayscale = ImageOps.autocontrast(page_image.convert("L"))
    components = _scan_chess_page_dark_components((np.asarray(grayscale) < 120) & allowed)
    plausible: list[dict[str, Any]] = []
    for component in components:
        raw_bbox = component.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
            continue
        x0, y0, x1, y1 = [float(value) for value in raw_bbox]
        width = max(1.0, x1 - x0 + 1.0)
        height = max(1.0, y1 - y0 + 1.0)
        aspect = width / max(1.0, height)
        density = float(component.get("density") or 0.0)
        matching_cells = [
            cell
            for cell in cell_sizes
            if width >= max(6.0, cell * 0.18)
            and height >= max(6.0, cell * 0.18)
            and width <= max(30.0, cell * 2.25)
            and height <= max(30.0, cell * 2.25)
        ]
        if not matching_cells or not (0.45 <= aspect <= 2.1) or not (0.06 <= density <= 0.88):
            continue
        marker_bbox = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]
        if any(
            _bbox_overlap_ratio(tuple(marker_bbox), tuple(board_bbox)) > 0.02
            for board_bbox in board_boxes
        ):
            continue
        nearest_board = min(
            normalized_boards,
            key=lambda board: _scan_chess_bbox_edge_distance(tuple(marker_bbox), tuple(board["bbox"])),
        )
        crop_bbox = _scan_chess_padded_marker_bbox(marker_bbox, nearest_board["bbox"], page_image.size)
        crop = _crop_bbox_from_image(page_image, crop_bbox)
        if crop is None:
            continue
        classification = classify_scan_chess_side_marker_crop(crop)
        classifier_status = str(classification.get("status") or "marker_missing")
        candidate_class = str(
            classification.get("shape")
            or ("white" if classification.get("side") == "w" else "black" if classification.get("side") == "b" else "unclear")
        )
        confidence = round(float(classification.get("confidence") or 0.0), 4)
        compactness = min(1.0, float(component.get("area") or 0.0) / max(1.0, width * height))
        shape_score = 1.0 if classifier_status == "trusted_marker" else 0.65 if classifier_status != "marker_missing" else 0.3
        plausibility = round(
            0.35 * min(1.0, float(component.get("area") or 0.0) / 180.0)
            + 0.25 * compactness
            + 0.20 * (1.0 - min(1.0, abs(1.0 - aspect)))
            + 0.20 * shape_score,
            4,
        )
        plausible.append(
            {
                "marker_candidate_bbox": marker_bbox,
                "marker_candidate_crop_bbox": crop_bbox,
                "marker_candidate_features": {
                    "area": round(float(component.get("area") or 0.0), 2),
                    "density": round(density, 4),
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "aspect_ratio": round(aspect, 4),
                    "plausibility": plausibility,
                },
                "marker_candidate_class": candidate_class,
                "marker_candidate_classifier_status": classifier_status,
                "marker_candidate_side": str(classification.get("side") or ""),
                "marker_candidate_confidence": confidence,
                "marker_candidate_plausibility": plausibility,
                "marker_candidate_source": "page_level_dark_components",
            }
        )
    candidate_limit = int(top_k or 0) or max(12, len(normalized_boards) * 8)
    plausible.sort(
        key=lambda item: (
            float(item.get("marker_candidate_plausibility") or 0.0),
            float(item.get("marker_candidate_confidence") or 0.0),
        ),
        reverse=True,
    )
    selected = plausible[:candidate_limit]
    selected.sort(
        key=lambda item: (
            item["marker_candidate_bbox"][1],
            item["marker_candidate_bbox"][0],
        )
    )
    files: list[dict[str, Any]] = []
    page_value = max(1, int(page_number or 1))
    for index, candidate in enumerate(selected, start=1):
        candidate_id = f"p{page_value:03d}_m{index:03d}"
        crop_path = f"review/chess_fen/marker_candidates/page-{page_value:03d}/{candidate_id}.png"
        candidate["marker_candidate_id"] = candidate_id
        candidate["marker_candidate_crop_path"] = crop_path
        candidate["marker_assignment_status"] = "unassigned"
        candidate["marker_assignment_confidence"] = 0.0
        candidate["marker_assignment_runner_up_margin"] = 0.0
        candidate["marker_assignment_rejected_reasons"] = []
        crop = _crop_bbox_from_image(page_image, candidate["marker_candidate_crop_bbox"])
        if crop is not None:
            files.append({"path": crop_path, "data": _png_bytes(crop)})
    return selected, files


def _scan_chess_page_dark_components(mask: Any) -> list[dict[str, float]]:
    """Use the optional CV backend for the full-page pass and keep a deterministic fallback."""
    try:
        import cv2  # type: ignore

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            np.asarray(mask, dtype=np.uint8),
            connectivity=8,
        )
        components: list[dict[str, float]] = []
        for index in range(1, int(count)):
            x, y, width, height, area = [int(value) for value in stats[index]]
            density = area / max(1, width * height)
            components.append(
                {
                    "area": float(area),
                    "density": float(density),
                    "bbox": (
                        float(x),
                        float(y),
                        float(x + width - 1),
                        float(y + height - 1),
                    ),
                    "width": float(width),
                    "height": float(height),
                    "score": float(area),
                }
            )
        return components
    except Exception:
        return _scan_chess_sparse_dark_components(mask)


def _scan_chess_sparse_dark_components(mask: Any) -> list[dict[str, float]]:
    remaining = {
        (int(x), int(y))
        for y, x in np.argwhere(np.asarray(mask, dtype=bool))
    }
    components: list[dict[str, float]] = []
    while remaining:
        start_x, start_y = remaining.pop()
        stack = [(start_x, start_y)]
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
            for neighbor_x in (x - 1, x, x + 1):
                for neighbor_y in (y - 1, y, y + 1):
                    neighbor = (neighbor_x, neighbor_y)
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
        width = max_x - min_x + 1
        height = max_y - min_y + 1
        components.append(
            {
                "area": float(area),
                "density": float(area / max(1, width * height)),
                "bbox": (float(min_x), float(min_y), float(max_x), float(max_y)),
                "width": float(width),
                "height": float(height),
                "score": float(area),
            }
        )
    return components


def _scan_chess_assign_page_marker_candidates(
    boards: Iterable[Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]],
    *,
    page_size: tuple[int, int],
) -> dict[str, Any]:
    normalized_boards = _scan_chess_normalized_page_boards(boards)
    normalized_candidates = [dict(candidate) for candidate in candidates]
    real_candidate_count = len(normalized_candidates)
    cost_matrix: list[list[float]] = []
    cost_details: list[list[dict[str, Any]]] = []
    for board in normalized_boards:
        row: list[float] = []
        detail_row: list[dict[str, Any]] = []
        for candidate in normalized_candidates:
            detail = _scan_chess_board_marker_assignment_cost(
                board["bbox"],
                candidate,
                page_size=page_size,
            )
            row.append(float(detail["cost"]))
            detail_row.append(detail)
        row.extend([PAGE_MARKER_UNASSIGNED_COST] * len(normalized_boards))
        cost_matrix.append(row)
        cost_details.append(detail_row)
    selected_columns = _scan_chess_min_cost_columns(cost_matrix)
    assignments: list[dict[str, Any]] = []
    assigned_candidate_ids: set[str] = set()
    for board_index, board in enumerate(normalized_boards):
        column = selected_columns[board_index] if board_index < len(selected_columns) else -1
        if 0 <= column < real_candidate_count:
            candidate = normalized_candidates[column]
            detail = cost_details[board_index][column]
            candidate_id = str(candidate.get("marker_candidate_id") or "")
            candidate_costs = sorted(
                float(cost_details[board_index][index]["cost"])
                for index in range(real_candidate_count)
                if index != column
            )
            runner_up_cost = candidate_costs[0] if candidate_costs else PAGE_MARKER_UNASSIGNED_COST
            runner_up_cost = min(runner_up_cost, PAGE_MARKER_UNASSIGNED_COST)
            runner_up_margin = max(0.0, runner_up_cost - float(detail["cost"]))
            competing_candidates = [
                normalized_candidates[index]
                for index in range(real_candidate_count)
                if index != column
                and float(cost_details[board_index][index]["cost"])
                <= float(detail["cost"]) + 0.12
            ]
            competing_sides = sorted(
                {
                    str(item.get("marker_candidate_side") or "")
                    for item in [candidate, *competing_candidates]
                    if str(item.get("marker_candidate_side") or "") in {"w", "b"}
                }
            )
            other_owner_costs = [
                float(cost_details[index][column]["cost"])
                for index in range(len(normalized_boards))
                if index != board_index
            ]
            nearest_other_owner = min(other_owner_costs) if other_owner_costs else PAGE_MARKER_UNASSIGNED_COST
            ownership_margin = max(0.0, nearest_other_owner - float(detail["cost"]))
            assignment_confidence = _scan_chess_assignment_confidence(
                float(detail["cost"]),
                runner_up_margin=runner_up_margin,
                ownership_margin=ownership_margin,
            )
            rejected_reasons = list(detail.get("rejected_reasons") or [])
            status = "assigned"
            if float(detail["cost"]) >= PAGE_MARKER_UNASSIGNED_COST:
                status = "unassigned"
                rejected_reasons.append("assignment_cost_above_threshold")
            elif ownership_margin < 0.12:
                status = "needs_review_ambiguous_ownership"
                rejected_reasons.append("ownership_margin_too_small")
            elif runner_up_margin < 0.12:
                status = (
                    "needs_review_candidate_conflict"
                    if len(competing_sides) > 1
                    else "needs_review_ambiguous_candidate"
                )
                rejected_reasons.append("candidate_runner_up_margin_too_small")
            assignment = {
                "diagram_id": board["diagram_id"],
                **_scan_chess_empty_marker_assignment_fields(),
                **{
                    key: candidate.get(key)
                    for key in (
                        "marker_candidate_id",
                        "marker_candidate_bbox",
                        "marker_candidate_crop_bbox",
                        "marker_candidate_crop_path",
                        "marker_candidate_features",
                        "marker_candidate_class",
                        "marker_candidate_classifier_status",
                        "marker_candidate_side",
                        "marker_candidate_confidence",
                    )
                },
                "marker_assignment_status": status,
                "marker_assignment_confidence": assignment_confidence,
                "marker_assignment_runner_up_margin": round(runner_up_margin, 4),
                "marker_assignment_ownership_margin": round(ownership_margin, 4),
                "marker_assignment_cost": round(float(detail["cost"]), 4),
                "marker_assignment_zone": str(detail.get("zone") or ""),
                "marker_assignment_competing_candidate_ids": [
                    str(item.get("marker_candidate_id") or "")
                    for item in competing_candidates
                    if str(item.get("marker_candidate_id") or "")
                ],
                "marker_assignment_competing_candidate_sides": competing_sides,
                "marker_assignment_rejected_reasons": sorted(set(rejected_reasons)),
            }
            if status != "unassigned" and candidate_id:
                assigned_candidate_ids.add(candidate_id)
                candidate.update(
                    {
                        "marker_assignment_status": status,
                        "marker_assignment_confidence": assignment_confidence,
                        "marker_assignment_runner_up_margin": round(runner_up_margin, 4),
                        "marker_assignment_ownership_margin": round(ownership_margin, 4),
                        "marker_assignment_rejected_reasons": sorted(set(rejected_reasons)),
                        "assigned_diagram_id": board["diagram_id"],
                    }
                )
            assignments.append(assignment)
            continue
        assignments.append(
            {
                "diagram_id": board["diagram_id"],
                **_scan_chess_empty_marker_assignment_fields(),
                "marker_assignment_rejected_reasons": ["no_candidate_selected_by_global_assignment"],
            }
        )
    for candidate in normalized_candidates:
        candidate_id = str(candidate.get("marker_candidate_id") or "")
        if candidate_id in assigned_candidate_ids:
            continue
        candidate["marker_assignment_status"] = "unassigned"
        candidate["marker_assignment_confidence"] = 0.0
        candidate["marker_assignment_runner_up_margin"] = 0.0
        candidate["marker_assignment_rejected_reasons"] = ["not_selected_by_global_assignment"]
    assigned = [item for item in assignments if item.get("marker_assignment_status") != "unassigned"]
    owned_ids = [str(item.get("marker_candidate_id") or "") for item in assigned if item.get("marker_candidate_id")]
    confident = [item for item in assigned if item.get("marker_assignment_status") == "assigned"]
    return {
        "assignments": assignments,
        "candidates": normalized_candidates,
        "cost_matrix": [
            {
                "diagram_id": board["diagram_id"],
                "candidate_costs": {
                    str(normalized_candidates[index].get("marker_candidate_id") or index): round(cost, 4)
                    for index, cost in enumerate(row[:real_candidate_count])
                },
            }
            for board, row in zip(normalized_boards, cost_matrix)
        ],
        "summary": {
            "board_count": len(normalized_boards),
            "marker_candidate_count": real_candidate_count,
            "assigned_marker_count": len(assigned),
            "confident_ownership_count": len(confident),
            "unassigned_board_count": len(normalized_boards) - len(assigned),
            "unassigned_candidate_count": real_candidate_count - len(set(owned_ids)),
            "duplicate_marker_ownership_count": len(owned_ids) - len(set(owned_ids)),
            "marker_candidate_assignment_rate": round(len(assigned) / len(normalized_boards), 4)
            if normalized_boards
            else 0.0,
            "ownership_confident_rate": round(len(confident) / len(assigned), 4) if assigned else 0.0,
        },
    }


def _scan_chess_empty_marker_assignment_fields() -> dict[str, Any]:
    return {
        "marker_candidate_id": "",
        "marker_candidate_bbox": [],
        "marker_candidate_crop_bbox": [],
        "marker_candidate_crop_path": "",
        "marker_candidate_features": {},
        "marker_candidate_class": "",
        "marker_candidate_classifier_status": "",
        "marker_candidate_side": "",
        "marker_candidate_confidence": 0.0,
        "marker_assignment_status": "unassigned",
        "marker_assignment_confidence": 0.0,
        "marker_assignment_runner_up_margin": 0.0,
        "marker_assignment_ownership_margin": 0.0,
        "marker_assignment_cost": PAGE_MARKER_UNASSIGNED_COST,
        "marker_assignment_zone": "",
        "marker_assignment_competing_candidate_ids": [],
        "marker_assignment_competing_candidate_sides": [],
        "marker_assignment_rejected_reasons": [],
    }


def _scan_chess_apply_page_marker_assignment(
    payload: Mapping[str, Any],
    assignment: Mapping[str, Any],
    candidates: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    updated = dict(payload)
    updated["side_marker_candidates"] = [dict(candidate) for candidate in candidates]
    updated.update(dict(assignment))
    warnings = {str(warning) for warning in updated.get("warnings") or [] if str(warning)}
    warnings.update({"side_to_move_marker_probes_checked", "side_to_move_marker_page_assignment_checked"})
    status = str(assignment.get("marker_assignment_status") or "unassigned")
    if status == "needs_review_candidate_conflict":
        warnings.update({"side_to_move_marker_detected", "side_to_move_marker_local_conflict"})
    elif status in {"needs_review_ambiguous_candidate", "needs_review_ambiguous_ownership"}:
        warnings.update({"side_to_move_marker_detected", "side_to_move_marker_local_ambiguous"})
    updated["warnings"] = sorted(warnings)
    if status.startswith("needs_review_"):
        updated["fen"] = ""
        updated["requires_review"] = True
        updated["manual_review_required"] = True
        updated["manual_review_reason"] = (
            "marker_conflict" if status == "needs_review_candidate_conflict" else "unclear_symbol"
        )
        updated["side_to_move"] = "unknown"
        updated["side_to_move_status"] = "unknown"
        updated["side_to_move_evidence"] = "none"
    updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
    return updated


def _scan_chess_board_marker_assignment_cost(
    board_bbox: list[float],
    candidate: Mapping[str, Any],
    *,
    page_size: tuple[int, int],
) -> dict[str, Any]:
    marker_bbox = _coerce_side_marker_bbox(candidate.get("marker_candidate_bbox"))
    if not marker_bbox:
        return {"cost": PAGE_MARKER_UNASSIGNED_COST + 10.0, "zone": "", "rejected_reasons": ["candidate_bbox_missing"]}
    cell = min(max(1.0, board_bbox[2] - board_bbox[0]), max(1.0, board_bbox[3] - board_bbox[1])) / 8.0
    distance = _scan_chess_bbox_edge_distance(tuple(marker_bbox), tuple(board_bbox))
    normalized_distance = distance / max(1.0, cell)
    zones = _scan_chess_marker_search_zones(board_bbox, page_size)
    zone = _scan_chess_selected_marker_zone(marker_bbox, zones)
    rejected_reasons: list[str] = []
    if not zone:
        rejected_reasons.append("outside_board_marker_search_zones")
    if normalized_distance > 2.25:
        rejected_reasons.append("candidate_too_far_from_board")
    cost = normalized_distance + (0.0 if zone else 1.15)
    if "candidate_too_far_from_board" in rejected_reasons:
        cost += 6.0
    return {
        "cost": round(cost, 6),
        "zone": zone,
        "distance": round(distance, 4),
        "normalized_distance": round(normalized_distance, 4),
        "rejected_reasons": rejected_reasons,
    }


def _scan_chess_assignment_confidence(
    cost: float,
    *,
    runner_up_margin: float,
    ownership_margin: float,
) -> float:
    distance_score = max(0.0, 1.0 - cost / PAGE_MARKER_UNASSIGNED_COST)
    runner_score = min(1.0, max(0.0, runner_up_margin) / 1.25)
    ownership_score = min(1.0, max(0.0, ownership_margin) / 1.25)
    return round(0.55 * distance_score + 0.20 * runner_score + 0.25 * ownership_score, 4)


def _scan_chess_min_cost_columns(costs: list[list[float]]) -> list[int]:
    """Return an exact minimum-cost rectangular assignment (Hungarian algorithm)."""
    if not costs:
        return []
    row_count = len(costs)
    column_count = max((len(row) for row in costs), default=0)
    if column_count < row_count:
        raise ValueError("assignment matrix must have at least as many columns as rows")
    matrix = [row + [PAGE_MARKER_UNASSIGNED_COST] * (column_count - len(row)) for row in costs]
    u = [0.0] * (row_count + 1)
    v = [0.0] * (column_count + 1)
    p = [0] * (column_count + 1)
    way = [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        p[0] = row_index
        column0 = 0
        min_values = [float("inf")] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column0] = True
            current_row = p[column0]
            delta = float("inf")
            column1 = 0
            for column in range(1, column_count + 1):
                if used[column]:
                    continue
                current = matrix[current_row - 1][column - 1] - u[current_row] - v[column]
                if current < min_values[column]:
                    min_values[column] = current
                    way[column] = column0
                if min_values[column] < delta:
                    delta = min_values[column]
                    column1 = column
            for column in range(column_count + 1):
                if used[column]:
                    u[p[column]] += delta
                    v[column] -= delta
                else:
                    min_values[column] -= delta
            column0 = column1
            if p[column0] == 0:
                break
        while True:
            column1 = way[column0]
            p[column0] = p[column1]
            column0 = column1
            if column0 == 0:
                break
    result = [-1] * row_count
    for column in range(1, column_count + 1):
        if p[column] > 0:
            result[p[column] - 1] = column - 1
    return result


def _scan_chess_padded_marker_bbox(marker_bbox: Any, board_bbox: Any, image_size: tuple[int, int]) -> list[float]:
    marker = _coerce_side_marker_bbox(marker_bbox)
    board = _coerce_side_marker_bbox(board_bbox)
    if not marker:
        return []
    marker_w = max(1.0, marker[2] - marker[0])
    marker_h = max(1.0, marker[3] - marker[1])
    cell = min(max(1.0, board[2] - board[0]), max(1.0, board[3] - board[1])) / 8.0 if board else max(marker_w, marker_h)
    padding = max(2.0, min(0.35 * cell, 0.25 * max(marker_w, marker_h)))
    box = _bbox_to_int_box((marker[0] - padding, marker[1] - padding, marker[2] + padding, marker[3] + padding), image_size)
    return [float(value) for value in box] if box is not None else []


def _scan_chess_selected_marker_zone(marker_bbox: Any, zones: Mapping[str, Any]) -> str:
    marker = _coerce_side_marker_bbox(marker_bbox)
    if not marker:
        return ""
    cx = (marker[0] + marker[2]) / 2.0
    cy = (marker[1] + marker[3]) / 2.0
    for name, raw_zone in zones.items():
        zone = _coerce_side_marker_bbox(raw_zone)
        if zone and zone[0] <= cx <= zone[2] and zone[1] <= cy <= zone[3]:
            return str(name)
    return ""


def _scan_chess_marker_crop_coordinate_reasons(marker: list[float], board: list[float]) -> list[str]:
    if not marker or not board:
        return []
    mx0, my0, mx1, my1 = marker
    bx0, by0, bx1, by1 = board
    marker_w = max(1.0, mx1 - mx0)
    marker_h = max(1.0, my1 - my0)
    board_w = max(1.0, bx1 - bx0)
    board_h = max(1.0, by1 - by0)
    cell = min(board_w, board_h) / 8.0
    reasons: list[str] = []
    vertical_side_band = (mx1 <= bx0 + cell * 0.35 or mx0 >= bx1 - cell * 0.35) and marker_h >= cell * 2.6
    horizontal_coordinate_band = (my1 <= by0 + cell * 0.35 or my0 >= by1 - cell * 0.35) and marker_w >= cell * 2.6
    if vertical_side_band and marker_h / max(1.0, marker_w) >= 2.8:
        reasons.append("mostly_rank_numbers")
    if horizontal_coordinate_band and marker_w / max(1.0, marker_h) >= 2.8:
        reasons.append("mostly_file_letters")
    return reasons


def _scan_chess_marker_crop_quality(
    page_image: Image.Image,
    crop_bbox: Any,
    board_bbox: Any,
    *,
    marker_bbox: Any = None,
    marker_search_zones: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    marker = _coerce_side_marker_bbox(crop_bbox)
    board = _coerce_side_marker_bbox(board_bbox)
    detected_marker = _coerce_side_marker_bbox(marker_bbox) or marker
    box = _bbox_to_int_box(marker, page_image.size)
    reasons: list[str] = []
    if box is None:
        return {
            "decision": "fail",
            "reasons": ["marker_missing"],
            "reason_codes": {"marker_missing": 1},
            "side_to_move": None,
            "confidence": 0.0,
            "classifier_version": "marker_shape_v2",
            "reason": "marker_missing",
            "symbol": None,
        }
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    if min(width, height) < 10 or width / max(1, height) < 0.35 or height / max(1, width) < 0.35:
        reasons.append("too_narrow")
    if board:
        overlap = _bbox_overlap_ratio(tuple(float(value) for value in marker), tuple(float(value) for value in board))
        if overlap > 0.18:
            reasons.append("mostly_board_edge")
        reasons.extend(_scan_chess_marker_crop_coordinate_reasons(marker, board))
        zones = marker_search_zones or _scan_chess_marker_search_zones(board, page_image.size)
        if zones and not _scan_chess_selected_marker_zone(detected_marker, zones):
            reasons.append("outside_expected_zone")
    if box[0] <= 0 or box[1] <= 0 or box[2] >= page_image.width or box[3] >= page_image.height:
        reasons.append("marker_cut_off")
    crop = ImageOps.autocontrast(page_image.crop(box).convert("L"))
    raw_components = _scan_chess_dark_components(np.asarray(crop) < 120)
    classification = classify_scan_chess_side_marker_crop(crop)
    component = classification.get("component")
    component_count = len(
        [
            item
            for item in raw_components
            if float(item.get("width") or 0.0) >= max(8.0, min(width, height) * 0.18)
            and float(item.get("height") or 0.0) >= max(8.0, min(width, height) * 0.18)
            and 0.55
            <= float(item.get("width") or 0.0) / max(1.0, float(item.get("height") or 0.0))
            <= 1.85
        ]
    )
    if component_count > 1:
        reasons.append("multiple_candidates")
    borderline_outline = False
    if classification.get("status") == "side_to_move_marker_local_ambiguous" and isinstance(component, Mapping):
        try:
            density = float(component.get("density") or 0.0)
            score = float(component.get("score") or 0.0)
        except (TypeError, ValueError):
            density = 0.0
            score = 0.0
        borderline_outline = 0.320 < density <= 0.365 and score >= 45.0
    if classification.get("status") == "marker_missing":
        reasons.append("marker_missing")
    elif classification.get("status") in {"side_to_move_marker_local_conflict", "side_to_move_marker_local_ambiguous"}:
        if classification.get("status") == "side_to_move_marker_local_conflict" or classification.get("shape") == "multiple_triangle_candidates":
            reasons.append("multiple_candidates")
        elif not borderline_outline:
            reasons.append("unclear_symbol")
    if isinstance(component, Mapping):
        component_bbox = component.get("bbox")
        if isinstance(component_bbox, (list, tuple)) and len(component_bbox) == 4:
            cx0, cy0, cx1, cy1 = [float(value) for value in component_bbox]
            if cx0 < 0.5 or cy0 < 0.5 or cx1 >= width - 0.5 or cy1 >= height - 0.5:
                reasons.append("marker_cut_off")
            component_h = max(1.0, cy1 - cy0)
            if component_h < max(8.0, height * 0.10):
                reasons.append("marker_too_small")
    side = None
    if classification.get("side") == "w":
        side = "white"
    elif classification.get("side") == "b":
        side = "black"
    elif borderline_outline:
        side = "white"
    if side is None and "marker_missing" not in reasons:
        reasons.append("unclear_symbol")
    if classification.get("status") == "trusted_marker" and any(
        reason in reasons
        for reason in ("mostly_board_edge", "mostly_rank_numbers", "mostly_file_letters", "outside_expected_zone")
    ):
        reasons.append("wrong_marker_candidate")
    reasons = sorted(set(reasons))
    return {
        "decision": "fail" if reasons else "pass",
        "reasons": reasons,
        "reason_codes": {reason: reasons.count(reason) for reason in sorted(MARKER_CROP_REASON_CODES) if reason in reasons},
        "side_to_move": side if not reasons else None,
        "confidence": round(float(classification.get("confidence") or 0.0), 3) if not reasons else 0.0,
        "classifier_status": classification.get("status") or "",
        "classifier_version": classification.get("classifier_version") or "marker_shape_v2",
        "reason": classification.get("reason") or ("pass" if not reasons else reasons[0]),
        "symbol": classification.get("symbol") if not reasons else None,
        "component_count": component_count if component_count else (1 if isinstance(component, Mapping) else 0),
        "candidate_count": int(classification.get("candidate_count") or 0),
    }


def _scan_chess_manual_review_reason(reasons: Iterable[Any]) -> str:
    values = {str(reason) for reason in reasons if str(reason)}
    if "multiple_candidates" in values:
        return "multiple"
    if "marker_missing" in values:
        return "marker_missing"
    if "unclear_symbol" in values:
        return "unclear"
    if values:
        return "bad_crop"
    return ""


def _apply_scan_chess_two_crop_quality_gate(payload: dict[str, Any], fields: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(payload)
    warnings = {str(warning) for warning in updated.get("warnings") or [] if str(warning)}
    board_failed = str(fields.get("board_crop_quality") or "") == "fail"
    marker_failed = str(fields.get("marker_crop_quality") or "") == "fail"
    trusted_side = str(updated.get("side_to_move_status") or "").lower() == "explicit" and str(
        updated.get("side_to_move_evidence") or ""
    ).lower() == "marker"
    if board_failed:
        warnings.add("board_crop_quality_failed")
        updated["board_placement_status"] = "review"
    if marker_failed:
        warnings.add("marker_crop_quality_failed")
    if board_failed or (marker_failed and trusted_side):
        updated["fen"] = ""
        updated["requires_review"] = True
        updated["fen_suppressed_reason"] = "crop_quality_gate"
        updated["full_fen_runtime_status"] = "FEN_REVIEW_REQUIRED"
        updated["full_fen_allowed"] = False
        raw_blockers = updated.get("full_fen_blockers") or []
        if isinstance(raw_blockers, (str, bytes)):
            raw_blockers = [raw_blockers]
        blockers = [str(blocker) for blocker in raw_blockers if str(blocker)]
        if board_failed:
            blockers.extend(["board_placement_not_accepted", "board_crop_quality_failed"])
        if marker_failed:
            blockers.append("marker_semantic_not_trusted")
        updated["full_fen_blockers"] = list(dict.fromkeys(blockers))
        updated["full_fen_blocker"] = (
            updated["full_fen_blockers"][0] if updated["full_fen_blockers"] else ""
        )
        if marker_failed and trusted_side:
            updated["side_to_move"] = "unknown"
            updated["side_to_move_status"] = "unknown"
            updated["side_to_move_evidence"] = "none"
            updated["side_marker_confidence"] = 0.0
    updated["warnings"] = sorted(warnings)
    updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
    return updated


def _scan_chess_two_crop_trusted_side(fields: Mapping[str, Any]) -> str:
    if fields.get("marker_candidate_id") and str(
        fields.get("marker_assignment_status") or ""
    ) != "assigned":
        return ""
    if str(fields.get("marker_crop_quality") or "").lower() != "pass":
        return ""
    if not _coerce_side_marker_bbox(fields.get("marker_bbox")):
        return ""
    if not str(fields.get("selected_marker_zone") or "").strip():
        return ""
    gate = fields.get("marker_crop_quality_gate") if isinstance(fields.get("marker_crop_quality_gate"), Mapping) else {}
    if str(gate.get("decision") or "").lower() != "pass":
        return ""
    try:
        component_count = int(gate.get("component_count") or 0)
    except (TypeError, ValueError):
        return ""
    if component_count != 1:
        return ""
    blocked_reasons = {
        "multiple_candidates",
        "unclear_symbol",
        "marker_cut_off",
        "mostly_board_edge",
        "wrong_marker_candidate",
    }
    reasons = {str(reason) for reason in (fields.get("marker_crop_fail_reason") or gate.get("reasons") or []) if str(reason)}
    if reasons & blocked_reasons:
        return ""
    detected = str(fields.get("side_to_move_detected") or gate.get("side_to_move") or "").strip().lower()
    if detected in {"b", "black"}:
        return "b"
    if detected in {"w", "white"}:
        return "w"
    return ""


def _apply_scan_chess_two_crop_side_marker_if_trusted(
    payload: dict[str, Any],
    fields: Mapping[str, Any],
    *,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    side = _scan_chess_two_crop_trusted_side(fields)
    if not side:
        return dict(payload)
    payload_warnings = {str(warning) for warning in payload.get("warnings") or [] if str(warning)}
    if payload_warnings & SIDE_MARKER_CONFLICT_WARNINGS:
        updated = dict(payload)
        updated["requires_review"] = True
        updated["manual_review_required"] = True
        updated["manual_review_reason"] = "marker_conflict"
        updated["side_to_move"] = "unknown"
        updated["side_to_move_status"] = "unknown"
        updated["side_to_move_evidence"] = "none"
        updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
        return updated
    confidence = fields.get("side_to_move_confidence") or fields.get("side_marker_confidence")
    if confidence is None:
        gate = fields.get("marker_crop_quality_gate") if isinstance(fields.get("marker_crop_quality_gate"), Mapping) else {}
        confidence = gate.get("confidence")
    cleaned_payload = dict(payload)
    cleaned_warnings = {
        str(warning)
        for warning in cleaned_payload.get("warnings") or []
        if str(warning)
        and str(warning) not in SIDE_MARKER_CONFLICT_WARNINGS
        and str(warning) not in SIDE_MARKER_AMBIGUOUS_WARNINGS
        and str(warning) not in {"side_to_move_marker_multi_region_agreement", "side_to_move_marker_dominant_conflict_review_only"}
    }
    cleaned_warnings.add("side_to_move_marker_tight_crop_resolved")
    cleaned_payload["warnings"] = sorted(cleaned_warnings)
    updated = _apply_scan_chess_side_to_move_evidence(
        cleaned_payload,
        side,
        source="marker",
        raw_text=str(fields.get("selected_marker_zone") or "marker_crop"),
        source_bbox=tuple(float(value) for value in _coerce_side_marker_bbox(fields.get("marker_bbox")) or ()),
        min_confidence=min_confidence,
    )
    if confidence is not None:
        try:
            updated["side_marker_confidence"] = round(float(confidence), 3)
        except (TypeError, ValueError):
            pass
    updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
    trace = updated.get("side_marker_assignment_trace") if isinstance(updated.get("side_marker_assignment_trace"), dict) else {}
    trace = {
        **trace,
        "promotion_source": "tight_marker_crop",
        "promotion_rule": "marker_crop_quality_pass_v1",
        "marker_crop_quality": fields.get("marker_crop_quality"),
        "marker_crop_quality_gate": fields.get("marker_crop_quality_gate"),
    }
    updated["side_marker_assignment_trace"] = trace
    return updated


def _crop_bbox_from_image(image: Image.Image, bbox: Any) -> Image.Image | None:
    box = _bbox_to_int_box(bbox, image.size)
    if box is None:
        return None
    return image.crop(box).convert("RGB")


def _bbox_to_int_box(bbox: Any, image_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    values = _coerce_side_marker_bbox(bbox)
    if not values:
        return None
    width, height = image_size
    left = max(0, min(width, int(round(values[0]))))
    top = max(0, min(height, int(round(values[1]))))
    right = max(0, min(width, int(round(values[2]))))
    bottom = max(0, min(height, int(round(values[3]))))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _timed_png_bytes(image: Image.Image, performance: dict[str, Any]) -> bytes:
    started = time.perf_counter()
    data = _png_bytes(image)
    performance["png_encoding_seconds"] = float(performance.get("png_encoding_seconds") or 0.0) + (
        time.perf_counter() - started
    )
    _increment_two_crop_metric(performance, "png_encoded_artifact_count")
    _increment_two_crop_metric(performance, "png_encoded_bytes", len(data))
    return data


def _rounded_two_crop_performance(performance: Mapping[str, Any]) -> dict[str, Any]:
    rounded = dict(performance)
    for key in (
        "localization_seconds",
        "marker_analysis_seconds",
        "png_encoding_seconds",
        "file_write_seconds",
        "ambiguity_probe_seconds",
        "total_seconds",
    ):
        rounded[key] = round(float(rounded.get(key) or 0.0), 6)
    return rounded


def _scan_chess_debug_overlay(
    page_image: Image.Image,
    board_bbox: Any,
    marker_bbox: Any,
    *,
    marker_search_zones: Mapping[str, Any] | None = None,
    selected_marker_zone: str = "",
    board_crop_quality: str = "",
    marker_crop_quality: str = "",
) -> Image.Image | None:
    board = _coerce_side_marker_bbox(board_bbox)
    if not board:
        return None
    marker = _coerce_side_marker_bbox(marker_bbox)
    named_zones = [
        (str(name), zone)
        for name, value in (marker_search_zones or {}).items()
        for zone in [_coerce_side_marker_bbox(value)]
        if zone
    ]
    boxes = [board] + [zone for _name, zone in named_zones] + ([marker] if marker else [])
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    pad = max(12.0, min(board[2] - board[0], board[3] - board[1]) * 0.08)
    crop_box = _bbox_to_int_box((x0 - pad, y0 - pad, x1 + pad, y1 + pad), page_image.size)
    if crop_box is None:
        return None
    overlay = page_image.crop(crop_box).convert("RGB")
    draw = ImageDraw.Draw(overlay)

    def local_box(raw: list[float]) -> tuple[float, float, float, float]:
        return raw[0] - crop_box[0], raw[1] - crop_box[1], raw[2] - crop_box[0], raw[3] - crop_box[1]

    bx0, by0, bx1, by1 = local_box(board)
    draw.rectangle((bx0, by0, bx1, by1), outline="#1769aa", width=3)
    draw.text((bx0 + 3, max(0, by0 - 13)), f"board_bbox {board_crop_quality or 'unknown'}", fill="#1769aa")
    for step in range(1, 8):
        x = bx0 + (bx1 - bx0) * step / 8.0
        y = by0 + (by1 - by0) * step / 8.0
        draw.line((x, by0, x, by1), fill="#1769aa", width=1)
        draw.line((bx0, y, bx1, y), fill="#1769aa", width=1)
    for name, zone in named_zones:
        zx0, zy0, zx1, zy1 = local_box(zone)
        color = "#b45309" if name == selected_marker_zone else "#f59e0b"
        draw.rectangle((zx0, zy0, zx1, zy1), outline=color, width=3 if name == selected_marker_zone else 2)
        label = f"{name} search_zone"
        if name == selected_marker_zone:
            label += " selected"
        draw.text((zx0 + 3, max(0, zy0 - 12)), label, fill=color)
    if marker:
        mx0, my0, mx1, my1 = local_box(marker)
        draw.rectangle((mx0, my0, mx1, my1), outline="#b42318", width=3)
        draw.text((mx0 + 3, max(0, my0 - 13)), f"marker_bbox {marker_crop_quality or 'unknown'}", fill="#b42318")
    return overlay


def _build_two_crop_review_zip(files: list[Mapping[str, Any]], diagrams: list[Mapping[str, Any]]) -> bytes:
    output = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        manifest = {
            "schema": "kindlemaster.chess_fen.two_crop_review_artifacts.v1",
            "artifact_count": len(files),
            "diagram_count": len(diagrams),
            "paths": [str(item.get("path") or "") for item in files if str(item.get("path") or "").strip()],
        }
        archive.writestr("review/chess_fen/two_crop/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for item in files:
            path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
            data = item.get("data")
            if not path or path in seen or not isinstance(data, (bytes, bytearray)):
                continue
            seen.add(path)
            archive.writestr(path, bytes(data))
    return output.getvalue()


def _scan_chess_pgn_extra_artifacts(
    records: list,
    *,
    source_title: str,
    diagrams: list[Mapping[str, Any]] | None = None,
    diagram_records: list[Mapping[str, Any]] | None = None,
    book_layout_pages: list[Mapping[str, Any]] | None = None,
    review_artifact_files: list[Mapping[str, Any]] | None = None,
    deepseek_provider: Any | None = None,
) -> list[dict[str, Any]]:
    record_list = [
        record
        for record in records
        if getattr(record, "pgn", "").strip()
        or getattr(record, "raw_text", "").strip()
        or getattr(record, "movetext", "").strip()
    ]
    diagram_list = list(diagrams or diagram_records or [])
    if not record_list and not diagram_list:
        return []
    pgn_text = build_combined_pgn(record_list)
    exercises_pgn_text = build_exercises_pgn(
        record_list,
        diagram_records=diagram_list,
        source_title=source_title or "Chess Exercises",
    )
    pgn_fen_html = build_pgn_download_html(
        record_list,
        title=f"{source_title or 'Chess'} - PGN and FEN",
        diagram_records=diagram_list,
    )
    artifacts: list[dict[str, Any]] = []
    book_pages = list(book_layout_pages or [])
    layout_preview_html = _book_layout_review_html(book_pages, title=source_title or "Chess") if book_pages else ""
    if record_list or diagram_list or book_pages:
        pgn_text = build_combined_pgn(record_list)
        pgn_fen_html = build_pgn_download_html(
            record_list,
            title=f"{source_title or 'Chess'} - PGN and FEN",
            diagram_records=diagram_list,
        )
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
        if exercises_pgn_text.strip():
            artifacts.append(
                {
                    "key": "chess_exercises_pgn",
                    "filename": "chess_exercises.pgn",
                    "content_type": "application/x-chess-pgn; charset=utf-8",
                    "data": exercises_pgn_text.encode("utf-8"),
                    "label": "Exercises PGN",
                }
            )
        artifacts.append(
            {
                "key": "chess_pgn_html",
                "filename": "chess_games.html",
                "content_type": "text/html; charset=utf-8",
                "data": pgn_fen_html.encode("utf-8"),
                "label": "HTML PGN/FEN",
            }
        )
    if diagram_list and book_pages:
        artifacts.append(
            {
                "key": "chess_diagrams",
                "filename": "chess_diagrams.json",
                "content_type": "application/json; charset=utf-8",
                "data": json.dumps(
                    {"diagram_count": len(diagram_list), "records": diagram_list},
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8"),
                "label": "Chess diagrams",
            }
        )
    review_files = list(review_artifact_files or [])
    if review_files:
        artifacts.append(
            {
                "key": "chess_fen_two_crop_review_artifacts",
                "filename": "chess_fen_two_crop_review_artifacts.zip",
                "content_type": "application/zip",
                "data": _build_two_crop_review_zip(review_files, diagram_list),
                "label": "Chess FEN two-crop review artifacts",
            }
        )
    if book_pages:
        artifacts.append(
            {
                "key": "pdf_layout_preview",
                "filename": "pdf_layout_preview.html",
                "content_type": "text/html; charset=utf-8",
                "data": layout_preview_html.encode("utf-8"),
                "label": "PDF layout preview",
            }
        )
    glyph_payload = build_chess_glyph_diagnostics_payload(record_list, source_title=source_title)
    if int(glyph_payload.get("diagnostic_count", 0) or 0):
        artifacts.append(
            {
                "key": "chess_glyph_diagnostics",
                "filename": "chess_glyph_diagnostics.json",
                "content_type": "application/json; charset=utf-8",
                "data": json.dumps(glyph_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                "label": "Chess glyph diagnostics",
            }
        )
    provider = deepseek_provider
    if provider is None:
        try:
            from deepseek_quality_provider import build_deepseek_audit_provider_from_env

            provider = build_deepseek_audit_provider_from_env(cwd=Path(__file__).resolve().parent)
        except Exception:
            provider = None
    if provider is not None:
        try:
            from deepseek_quality_provider import build_deepseek_audit_payload

            deepseek_payload = build_deepseek_audit_payload(
                provider=provider,
                source_title=source_title,
                glyph_payload=glyph_payload,
                records=record_list,
                diagrams=diagram_list,
            )
            if deepseek_payload:
                artifacts.append(
                    {
                        "key": "deepseek_audit",
                        "filename": "deepseek_audit.json",
                        "content_type": "application/json; charset=utf-8",
                        "data": json.dumps(deepseek_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                        "label": "DeepSeek audit",
                    }
                )
        except Exception:
            pass
    return artifacts


def _chess_pdf_extra_artifacts(
    pdf_path: str,
    config: ConversionConfig,
    *,
    source_title: str,
    pgn_records: list | None = None,
    diagrams: list[Mapping[str, Any]] | None = None,
    book_layout_pages: list[Mapping[str, Any]] | None = None,
    review_artifact_files: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return _scan_chess_pgn_extra_artifacts(
        list(pgn_records or []),
        source_title=source_title or Path(pdf_path).stem,
        diagrams=diagrams or [],
        book_layout_pages=book_layout_pages or [],
        review_artifact_files=review_artifact_files or [],
    )


def _book_layout_review_html(book_pages: list[Mapping[str, Any]], *, title: str) -> str:
    page_markup: list[str] = []
    for page in book_pages:
        elements = []
        for element in list(page.get("elements") or []):
            if not isinstance(element, Mapping):
                continue
            element_type = str(element.get("type") or "text").strip()
            class_name = (
                "book-element book-diagram"
                if element_type == "diagram"
                else "book-element book-fen"
                if element_type == "fen"
                else "book-element book-text"
            )
            text = str(element.get("title") or element.get("fen") or element.get("text") or element_type)
            elements.append(f'<div class="{class_name}">{html_module.escape(text)}</div>')
        bg = str(page.get("background_image_data_uri") or "")
        bg_markup = f'<img class="book-page-bg" src="{html_module.escape(bg, quote=True)}" alt="page background"/>' if bg else ""
        page_markup.append(
            '<section class="pdf-page">'
            '<div class="chess-book-page">'
            f"{bg_markup}"
            + "".join(elements)
            + "</div>"
            + "</section>"
        )
    return (
        '<!doctype html><html><body data-km-view="chess-book-review">'
        f"<h1>{html_module.escape(title)}</h1>"
        '<p class="pdf-layout-preview-warning">'
        "To nie jest finalny reader szachowy. "
        "To artefakt audytowy do sprawdzenia układu PDF; finalny reader szachowy jest w HTML PGN/FEN."
        "</p>"
        + "".join(page_markup)
        + '<p><a href="chess_pgn_html">HTML PGN/FEN</a></p>'
        "</body></html>\n"
    )


def build_pdf_layout_preview_html(pdf_path: str, config: ConversionConfig, *, title: str = "PDF layout preview") -> str:
    dpi = max(48, min(int(getattr(config, "pdf_layout_preview_dpi", 96) or 96), 180))
    jpeg_quality = max(45, min(int(getattr(config, "pdf_layout_preview_jpeg_quality", 72) or 72), 92))
    max_pages = max(0, int(getattr(config, "pdf_layout_preview_max_pages", 0) or 0))
    scale = dpi / 72.0
    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc) if max_pages <= 0 else min(len(doc), max_pages)
        pages = [
            _pdf_layout_preview_page_html(doc[page_num], page_num=page_num, scale=scale, jpeg_quality=jpeg_quality)
            for page_num in range(page_count)
        ]
        source_page_count = len(doc)
    finally:
        doc.close()

    safe_title = html_module.escape(title or "PDF layout preview")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{safe_title}</title>"
        "<style>"
        ":root{color:#1f2933;background:#ece7dc;}"
        "body{margin:0;font-family:Georgia,'Times New Roman',serif;background:#ece7dc;color:#1f2933;}"
        ".toolbar{position:sticky;top:0;z-index:10;display:flex;gap:.75rem;align-items:center;"
        "padding:.75rem 1rem;background:rgba(255,252,246,.94);border-bottom:1px solid #d6c8b6;"
        "box-shadow:0 8px 20px rgba(70,55,35,.12);}"
        ".toolbar h1{font-size:1rem;margin:0 1rem 0 0;}"
        ".toolbar button{border:1px solid #b89f7e;background:#fff8ed;border-radius:999px;padding:.45rem .8rem;"
        "font-weight:700;cursor:pointer;color:#1f2933;}"
        ".preview-meta{font-size:.86rem;color:#6b5b48;}"
        ".page-stack{padding:1.5rem;display:flex;flex-direction:column;align-items:center;gap:1.5rem;}"
        ".pdf-page{position:relative;background:#fff;box-shadow:0 18px 42px rgba(55,42,25,.24);"
        "overflow:hidden;transform-origin:top center;}"
        ".pdf-page-bg{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;user-select:none;pointer-events:none;}"
        ".pdf-text-layer{position:absolute;inset:0;}"
        ".pdf-text-span{position:absolute;white-space:pre;line-height:1;color:rgba(0,0,0,0);"
        "font-family:serif;user-select:text;pointer-events:auto;}"
        ".show-text-layer .pdf-text-span{color:rgba(14,41,64,.78);background:rgba(255,231,92,.18);"
        "outline:1px solid rgba(17,94,89,.18);}"
        ".pdf-page-label{position:absolute;left:.5rem;top:.5rem;background:rgba(255,255,255,.82);"
        "border-radius:999px;padding:.15rem .45rem;font-size:10px;color:#604b36;}"
        "</style></head><body>"
        "<div class=\"toolbar\">"
        f"<h1>{safe_title}</h1>"
        "<button type=\"button\" id=\"toggleTextLayer\">Show text layer</button>"
        f"<span class=\"preview-meta\">Pages: {len(pages)} / {source_page_count}</span>"
        "</div><main class=\"page-stack\">"
        + "\n".join(pages)
        + "</main><script>"
        "(function(){var button=document.getElementById('toggleTextLayer');"
        "button&&button.addEventListener('click',function(){document.body.classList.toggle('show-text-layer');"
        "button.textContent=document.body.classList.contains('show-text-layer')?'Hide text layer':'Show text layer';});"
        "})();"
        "</script></body></html>\n"
    )


def _pdf_layout_preview_page_html(page: fitz.Page, *, page_num: int, scale: float, jpeg_quality: int) -> str:
    rect = page.rect
    width = float(rect.width or 0.0)
    height = float(rect.height or 0.0)
    image_uri = _render_pdf_page_data_uri(page, scale=scale, jpeg_quality=jpeg_quality)
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP, sort=True)
    spans: list[str] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = str(span.get("text") or "")
                if not text.strip():
                    continue
                spans.append(_pdf_layout_preview_span_html(span))
    return (
        f'<section class="pdf-page" data-page="{page_num + 1}" '
        f'style="width:{width:.2f}px;height:{height:.2f}px">'
        f'<img class="pdf-page-bg" alt="" src="{image_uri}"/>'
        f'<span class="pdf-page-label">Page {page_num + 1}</span>'
        '<div class="pdf-text-layer" aria-label="Copyable PDF text layer">'
        + "".join(spans)
        + "</div></section>"
    )


def _render_pdf_page_data_uri(page: fitz.Page, *, scale: float, jpeg_quality: int) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _pdf_layout_preview_span_html(span: dict[str, Any]) -> str:
    bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
    x0, y0, x1, y1 = [float(value or 0.0) for value in bbox]
    size = max(1.0, float(span.get("size", 12) or 12))
    font_family = _preview_font_family(str(span.get("font") or ""))
    flags = int(span.get("flags", 0) or 0)
    font_weight = "700" if flags & (1 << 4) else "400"
    font_style = "italic" if flags & (1 << 1) else "normal"
    style = (
        f"left:{x0:.2f}px;top:{y0:.2f}px;width:{max(0.0, x1 - x0):.2f}px;"
        f"height:{max(0.0, y1 - y0):.2f}px;font-size:{size:.2f}px;"
        f"font-family:{font_family};font-weight:{font_weight};font-style:{font_style};"
    )
    return f'<span class="pdf-text-span" style="{style}">{html_module.escape(str(span.get("text") or ""))}</span>'


def _preview_font_family(font_name: str) -> str:
    font_lower = font_name.lower()
    if any(token in font_lower for token in ("arial", "helvetica", "univers", "aptos")):
        return "Arial,Helvetica,sans-serif"
    if any(token in font_lower for token in ("courier", "mono", "consolas")):
        return "'Courier New',monospace"
    if any(token in font_lower for token in ("times", "georgia", "garamond", "serif")):
        return "Georgia,'Times New Roman',serif"
    return "Georgia,'Times New Roman',serif"


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
    payload = {
        "fen": "",
        "placement": "",
        "confidence": float(candidate.get("confidence", 0.0) or 0.0),
        "side_to_move": "w",
        "side_to_move_status": "inferred",
        "side_to_move_evidence": "inferred",
        "bbox": candidate.get("bbox"),
        "method": str(candidate.get("method") or "image-page-board-candidate"),
        "warnings": warnings,
        "requires_review": True,
        "board_detected": True,
    }
    payload.update(_scan_chess_side_marker_metadata_from_payload(payload))
    return payload


def _scan_chess_side_marker_metadata_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    warnings = {str(warning) for warning in payload.get("warnings") or [] if str(warning)}
    side = str(payload.get("side_to_move") or "").strip().lower()
    if side not in {"w", "b", "both"}:
        side = "unknown"
    status = str(payload.get("side_to_move_status") or "").strip().lower()
    evidence = str(payload.get("side_to_move_evidence") or "").strip().lower()
    candidates = list(payload.get("side_marker_candidates") or [])
    if "side_to_move_marker_multi_side" in warnings:
        marker_status = "multi_side"
        symbol_key = "both"
        side = "unknown"
    elif warnings & SIDE_MARKER_CONFLICT_WARNINGS:
        marker_status = "marker_conflict"
        symbol_key = "ambiguous"
        side = "unknown"
    elif warnings & SIDE_MARKER_AMBIGUOUS_WARNINGS:
        marker_status = "ambiguous_marker"
        symbol_key = "ambiguous"
        side = "unknown"
    elif status == "explicit" and evidence in {"marker", "caption", "exact_label", "verified_label"} and side in {"w", "b"}:
        marker_status = {
            "marker": "trusted_marker",
            "caption": "trusted_caption",
            "exact_label": "trusted_exact_label",
            "verified_label": "trusted_verified_label",
        }.get(evidence, "trusted_marker")
        symbol_key = side
    elif status == "inferred" or evidence == "inferred" or "side_to_move_inferred" in warnings:
        marker_status = "inferred_only"
        symbol_key = "unknown"
    elif "side_to_move_marker_probes_checked" in warnings or candidates:
        marker_status = "marker_missing"
        symbol_key = "unknown"
    else:
        marker_status = "marker_missing"
        symbol_key = "unknown"

    source_bbox = payload.get("side_to_move_evidence_source_bbox") or payload.get("side_marker_bbox")
    bbox = _coerce_side_marker_bbox(source_bbox)
    confidence = payload.get("side_marker_confidence", payload.get("side_to_move_evidence_confidence", ""))
    try:
        confidence_value: float | str = round(float(confidence), 3)
    except (TypeError, ValueError):
        confidence_value = ""
    existing_trace = (
        dict(payload.get("side_marker_assignment_trace") or {})
        if isinstance(payload.get("side_marker_assignment_trace"), Mapping)
        else {}
    )
    trace = {
        **existing_trace,
        "candidate_count": len(candidates),
        "candidate_roles": [
            str(candidate.get("role") or candidate.get("zone") or candidate.get("source") or "")
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ],
        "trusted": marker_status in SIDE_MARKER_TRUSTED_STATUSES,
        "warnings": sorted(warnings),
        "marker_crop_fail_reason": list(payload.get("marker_crop_fail_reason") or []),
        "rejected_candidate_reasons": [],
        "marker_candidate_id": str(payload.get("marker_candidate_id") or ""),
        "marker_assignment_status": str(payload.get("marker_assignment_status") or "unassigned"),
        "marker_assignment_confidence": payload.get("marker_assignment_confidence") or 0.0,
        "marker_assignment_runner_up_margin": payload.get(
            "marker_assignment_runner_up_margin"
        ) or 0.0,
        "marker_assignment_ownership_margin": payload.get(
            "marker_assignment_ownership_margin"
        ) or 0.0,
        "marker_assignment_rejected_reasons": list(
            payload.get("marker_assignment_rejected_reasons") or []
        ),
    }
    if payload.get("side_marker_probe_role"):
        trace["selected_candidate_role"] = str(payload.get("side_marker_probe_role") or "")
    if candidates:
        selected = _scan_chess_best_marker_candidate(candidates)
        nearest = min(
            candidates,
            key=lambda item: float(item.get("distance_to_board") or 10**9) if isinstance(item, Mapping) else 10**9,
        )
        if selected is None:
            selected = nearest if isinstance(nearest, Mapping) else None
        if isinstance(nearest, Mapping):
            trace["nearest_candidate_role"] = str(nearest.get("role") or nearest.get("zone") or nearest.get("source") or "")
            trace["nearest_candidate_distance"] = nearest.get("distance_to_board")
            trace["nearest_candidate_side"] = nearest.get("detected_side") or nearest.get("side_candidate") or ""
        if isinstance(selected, Mapping):
            selected_role = str(selected.get("role") or selected.get("zone") or selected.get("source") or "")
            selected_side = str(
                selected.get("detected_side")
                or selected.get("side_candidate")
                or selected.get("marker_candidate_side")
                or ""
            )
            trace["selected_candidate_role"] = selected_role
            trace["selected_candidate_score"] = selected.get("score")
            trace["selected_candidate_density"] = selected.get("density")
            trace["selected_candidate_distance_to_board"] = selected.get("distance_to_board")
            trace["selected_candidate_bbox"] = _coerce_side_marker_bbox(
                selected.get("bbox")
                or selected.get("marker_bbox")
                or selected.get("marker_candidate_bbox")
            )
            trace["selected_candidate_role"] = selected_role
            trace["selected_candidate_side"] = selected_side
            trace["detected_side"] = selected_side
            trace["score"] = selected.get("score")
            trace["distance_to_board"] = selected.get("distance_to_board")
            rejected: list[dict[str, Any]] = []
            for candidate in candidates:
                if not isinstance(candidate, Mapping) or candidate is selected:
                    continue
                role = str(candidate.get("role") or candidate.get("zone") or candidate.get("source") or "")
                side_candidate = str(
                    candidate.get("detected_side")
                    or candidate.get("side_candidate")
                    or candidate.get("marker_candidate_side")
                    or ""
                )
                classifier_status = str(candidate.get("marker_classifier_status") or candidate.get("status") or "")
                if selected_side in {"w", "b"} and side_candidate in {"w", "b"} and side_candidate != selected_side:
                    reason = "opposite_side_candidate"
                elif classifier_status and classifier_status != "trusted_marker":
                    reason = classifier_status
                elif not side_candidate:
                    reason = "no_side_candidate"
                else:
                    reason = "lower_score_candidate"
                rejected.append(
                    {
                        "role": role,
                        "reason": reason,
                        "side": side_candidate,
                        "score": candidate.get("score"),
                        "density": candidate.get("density"),
                        "distance_to_board": candidate.get("distance_to_board"),
                        "bbox": _coerce_side_marker_bbox(
                            candidate.get("bbox")
                            or candidate.get("marker_bbox")
                            or candidate.get("marker_candidate_bbox")
                        ),
                    }
                )
            trace["rejected_candidate_reasons"] = rejected

    metadata = {
        "side_to_move": side,
        "side_marker_symbol": SIDE_MARKER_SYMBOLS[symbol_key],
        "side_marker_status": marker_status,
        "side_marker_source": evidence if evidence not in {"", "inferred"} else "none",
        "side_marker_bbox": bbox,
        "side_marker_confidence": confidence_value,
        "side_marker_assignment_trace": trace,
        "strict_fen_side_evidence_trusted": marker_status in SIDE_MARKER_TRUSTED_STATUSES,
    }
    from chess_side_to_move_evidence import resolve_marker_semantic_contract

    return {
        **metadata,
        **resolve_marker_semantic_contract({**dict(payload), **metadata}),
    }


def _coerce_side_marker_bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        return [round(float(item), 2) for item in value]
    except (TypeError, ValueError):
        return []


def _scan_chess_best_marker_candidate(candidates: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    ranked: list[Mapping[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if not _coerce_side_marker_bbox(
            candidate.get("bbox")
            or candidate.get("marker_bbox")
            or candidate.get("marker_candidate_bbox")
        ):
            continue
        has_marker_signal = bool(
            candidate.get("component_bbox")
            or candidate.get("detected_side")
            or candidate.get("side_candidate")
            or candidate.get("detected_shape")
            or candidate.get("marker_candidate_class")
        )
        if has_marker_signal:
            ranked.append(candidate)
    if not ranked:
        return None

    def rank(candidate: Mapping[str, Any]) -> tuple[int, float, float]:
        trusted_signal = (
            1
            if candidate.get("detected_side") in {"w", "b"}
            or candidate.get("side_candidate") in {"w", "b"}
            or candidate.get("marker_candidate_side") in {"w", "b"}
            else 0
        )
        try:
            score = float(candidate.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        try:
            distance = float(candidate.get("distance_to_board") or 10**9)
        except (TypeError, ValueError):
            distance = 10**9
        return trusted_signal, score, -distance

    return max(ranked, key=rank)


def _apply_scan_chess_side_to_move_evidence(
    payload: dict[str, Any],
    side_to_move: str,
    *,
    source: str,
    raw_text: str = "",
    source_bbox: Any = None,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    side = "b" if str(side_to_move).lower().startswith("b") else "w"
    updated = dict(payload)
    existing_warnings = {str(warning) for warning in list(updated.get("warnings") or []) if str(warning)}
    if "verified_exact_crop_label_used" in existing_warnings or str(updated.get("method") or "") == "verified-exact-crop-label":
        return updated
    updated["side_to_move"] = side
    updated["side_to_move_status"] = "explicit"
    updated["side_to_move_evidence"] = source
    updated["side_to_move_evidence_confidence"] = round(float(min_confidence or updated.get("confidence") or 0.0), 3)
    if raw_text:
        updated["side_to_move_raw_evidence"] = raw_text
    if source_bbox is not None:
        updated["side_to_move_evidence_source_bbox"] = source_bbox

    full_fen = str(updated.get("full_fen") or updated.get("fen") or "").strip()
    if full_fen:
        parts = full_fen.split()
        if len(parts) == 6:
            parts[1] = side
            full_fen = " ".join(parts)
            updated["full_fen"] = full_fen

    warnings = [warning for warning in existing_warnings if warning != "side_to_move_inferred"]
    detected_warning = f"side_to_move_{source}_detected"
    applied_warning = f"side_to_move_{source}_applied"
    if detected_warning not in warnings:
        warnings.append(detected_warning)
    if applied_warning not in warnings:
        warnings.append(applied_warning)
    if source == "marker" and "side_to_move_marker_detected" not in warnings:
        warnings.append("side_to_move_marker_detected")
    if source == "marker" and "side_to_move_marker_applied" not in warnings:
        warnings.append("side_to_move_marker_applied")
    updated["warnings"] = sorted(set(warnings))
    updated.update(_scan_chess_side_marker_metadata_from_payload(updated))

    if full_fen:
        candidate = dict(updated)
        candidate["fen"] = full_fen
        gate = machine_accept_fen(candidate, {"min_confidence": min_confidence if min_confidence is not None else 0.835})
        placement_runtime_status = ((gate.get("acceptance_trace") or {}).get("placement_gate") or {}).get("runtime_status")
        if placement_runtime_status:
            updated["placement_status"] = placement_runtime_status
            updated["placement_runtime_status"] = placement_runtime_status
        if gate.get("status") == "accepted":
            updated["fen"] = str(gate.get("selected_value") or full_fen)
            updated["full_fen"] = updated["fen"]
            updated["requires_review"] = False
            updated["runtime_status"] = "FEN_MACHINE_ACCEPTED"
            updated["full_fen_status"] = "FEN_MACHINE_ACCEPTED"
            updated["full_fen_runtime_status"] = "FEN_MACHINE_ACCEPTED"
            updated.pop("fen_suppressed_reason", None)
        else:
            updated["fen"] = ""
            updated["full_fen"] = full_fen
            updated["requires_review"] = True
            updated["full_fen_status"] = "FEN_REVIEW_REQUIRED"
            updated["full_fen_runtime_status"] = "FEN_REVIEW_REQUIRED"
            updated["fen_suppressed_reason"] = "side_to_move_evidence_gate"
            updated["machine_acceptance"] = gate
    updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
    return updated


def _apply_scan_chess_side_to_move_marker(
    payload: dict[str, Any],
    side_to_move: str,
    *,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    return _apply_scan_chess_side_to_move_evidence(
        payload,
        side_to_move,
        source="marker",
        min_confidence=min_confidence,
    )


def _apply_scan_chess_side_to_move_context_evidence(
    payload: dict[str, Any],
    evidence: ScanChessSideToMoveEvidence | None,
    *,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    if evidence is None:
        return payload
    updated = dict(payload)
    existing_warnings = {str(warning) for warning in list(updated.get("warnings") or []) if str(warning)}
    if "verified_exact_crop_label_used" in existing_warnings or str(updated.get("method") or "") == "verified-exact-crop-label":
        return updated
    evidence_warnings = {str(warning) for warning in evidence.warnings if str(warning)}
    if evidence.marker_candidates:
        marker_candidates = list(evidence.marker_candidates)
        updated["side_marker_candidates"] = marker_candidates
        best_marker = _scan_chess_best_marker_candidate(marker_candidates)
        if best_marker is not None and not _coerce_side_marker_bbox(updated.get("side_marker_bbox")):
            updated["side_marker_bbox"] = _coerce_side_marker_bbox(best_marker.get("bbox"))
            updated["side_marker_probe_role"] = str(best_marker.get("role") or "")
    if evidence.confidence:
        updated["side_marker_confidence"] = round(float(evidence.confidence), 3)
    if not evidence.side:
        updated["warnings"] = sorted(existing_warnings | evidence_warnings)
        if evidence_warnings & (SIDE_MARKER_CONFLICT_WARNINGS | SIDE_MARKER_AMBIGUOUS_WARNINGS):
            updated["fen"] = ""
            updated["requires_review"] = True
            updated["manual_review_required"] = True
            updated["manual_review_reason"] = (
                "marker_conflict" if evidence_warnings & SIDE_MARKER_CONFLICT_WARNINGS else "unclear_symbol"
            )
            updated["side_to_move"] = "unknown"
            updated["side_to_move_status"] = "unknown"
            updated["side_to_move_evidence"] = "none"
        updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
        return updated
    updated = _apply_scan_chess_side_to_move_evidence(
        updated,
        evidence.side,
        source=evidence.source,
        raw_text=evidence.raw_text,
        source_bbox=evidence.source_bbox,
        min_confidence=min_confidence,
    )
    warnings = {str(warning) for warning in list(updated.get("warnings") or []) if str(warning)}
    updated["warnings"] = sorted(warnings | evidence_warnings | {"side_to_move_context_applied"})
    updated.update(_scan_chess_side_marker_metadata_from_payload(updated))
    return updated


def _scan_chess_clean_side_only_review_payload(payload: dict[str, Any]) -> bool:
    if not bool(payload.get("requires_review")):
        return False
    if not str(payload.get("placement") or payload.get("placement_fen") or payload.get("full_fen") or "").strip():
        return False
    warnings = {str(warning) for warning in (payload.get("warnings") or []) if str(warning)}
    allowed = {
        "side_to_move_inferred",
        "side_to_move_marker_probes_checked",
        "reader_visible_crop_fen_used",
    }
    if not warnings or not warnings.issubset(allowed):
        return False
    return "side_to_move_inferred" in warnings


def _scan_chess_local_side_marker_assignment_evidence(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
    payload: dict[str, Any],
    *,
    diagram_bboxes: list[tuple[float, float, float, float]] | None = None,
) -> ScanChessSideToMoveEvidence | None:
    """Recover only clean side-only review cases with a unique local marker."""
    if not _scan_chess_clean_side_only_review_payload(payload):
        return None
    payloads = _scan_chess_side_marker_probe_payloads(page_image, bbox)
    if any(candidate.get("marker_classifier_status") == "side_to_move_marker_local_conflict" for candidate in payloads):
        return ScanChessSideToMoveEvidence(
            warnings=(
                "side_to_move_marker_detected",
                "side_to_move_marker_local_conflict",
                "side_to_move_marker_probes_checked",
            ),
            marker_candidates=tuple(payloads),
        )
    component_payloads = [candidate for candidate in payloads if candidate.get("component_bbox")]
    candidate_payloads: list[dict[str, Any]] = []
    for candidate in component_payloads:
        side = _scan_chess_local_side_marker_side_candidate(candidate)
        if not side:
            continue
        enriched = dict(candidate)
        enriched["side_candidate"] = side
        enriched["detected_side"] = side
        candidate_payloads.append(enriched)
    if not candidate_payloads:
        if component_payloads:
            return ScanChessSideToMoveEvidence(
                warnings=("side_to_move_marker_local_ambiguous", "side_to_move_marker_probes_checked"),
                marker_candidates=tuple(payloads),
            )
        return ScanChessSideToMoveEvidence(
            warnings=("side_to_move_marker_probes_checked",),
            marker_candidates=tuple(payloads),
        )
    sides = {str(candidate.get("side_candidate") or "") for candidate in candidate_payloads}
    if len(sides) != 1:
        return ScanChessSideToMoveEvidence(
            warnings=(
                "side_to_move_marker_detected",
                "side_to_move_marker_local_conflict",
                "side_to_move_marker_probes_checked",
            ),
            marker_candidates=tuple(payloads),
        )
    if len(candidate_payloads) != 1:
        return ScanChessSideToMoveEvidence(
            warnings=(
                "side_to_move_marker_detected",
                "side_to_move_marker_local_ambiguous",
                "side_to_move_marker_probes_checked",
            ),
            marker_candidates=tuple(payloads),
        )
    ranked = sorted(candidate_payloads, key=lambda item: float(item.get("score") or 0.0), reverse=True)
    best = ranked[0]
    if not _scan_chess_local_marker_is_dominant(best, component_payloads):
        agreement = _scan_chess_local_marker_agreement_evidence(candidate_payloads, bbox, diagram_bboxes or [])
        if agreement is None:
            return ScanChessSideToMoveEvidence(
                warnings=(
                    "side_to_move_marker_detected",
                    "side_to_move_marker_local_ambiguous",
                    "side_to_move_marker_probes_checked",
                ),
                marker_candidates=tuple(payloads),
            )
        side, agreement_best = agreement
        return ScanChessSideToMoveEvidence(
            side=side,
            source="marker",
            raw_text=str(agreement_best.get("role") or ""),
            confidence=0.86,
            warnings=(
                "side_to_move_marker_detected",
                "side_to_move_marker_local_agreement_used",
                "side_to_move_marker_probes_checked",
            ),
            source_bbox=tuple(float(value) for value in agreement_best.get("bbox", ())) if len(agreement_best.get("bbox", ())) == 4 else None,
            marker_candidates=tuple(payloads),
        )
    if not _scan_chess_marker_is_uniquely_local_to_bbox(best, bbox, diagram_bboxes or []):
        return ScanChessSideToMoveEvidence(
            warnings=(
                "side_to_move_marker_detected",
                "side_to_move_marker_local_ambiguous",
                "side_to_move_marker_probes_checked",
            ),
            marker_candidates=tuple(payloads),
        )
    warnings = {
        "side_to_move_marker_detected",
        "side_to_move_marker_local_assignment_used",
        "side_to_move_marker_probes_checked",
    }
    if best.get("local_borderline_outline"):
        warnings.add("side_to_move_marker_local_borderline_outline")
    return ScanChessSideToMoveEvidence(
        side=str(best.get("side_candidate") or ""),
        source="marker",
        raw_text=str(best.get("role") or ""),
        confidence=0.86,
        warnings=tuple(sorted(warnings)),
        source_bbox=tuple(float(value) for value in best.get("bbox", ())) if len(best.get("bbox", ())) == 4 else None,
        marker_candidates=tuple(payloads),
    )


def _scan_chess_local_side_marker_side_candidate(candidate: dict[str, Any]) -> str:
    side = str(candidate.get("detected_side") or candidate.get("side_candidate") or "")
    if side in {"w", "b"}:
        return side
    try:
        density = float(candidate.get("density") or 0.0)
        score = float(candidate.get("score") or 0.0)
    except (TypeError, ValueError):
        return ""
    if 0.320 < density <= 0.360 and score >= 650.0:
        candidate["detected_shape"] = "borderline_outline_triangle_review_only"
        candidate["local_borderline_outline"] = True
        return ""
    return ""


def _scan_chess_local_marker_is_dominant(best: dict[str, Any], component_payloads: list[dict[str, Any]]) -> bool:
    best_score = float(best.get("score") or 0.0)
    if best_score < 650.0:
        return False
    competitors = [
        candidate
        for candidate in component_payloads
        if candidate is not best and float(candidate.get("score") or 0.0) >= max(300.0, best_score * 0.55)
    ]
    if not competitors:
        return True
    best_side = str(best.get("side_candidate") or best.get("detected_side") or "")
    for competitor in competitors:
        competitor_side = _scan_chess_local_side_marker_side_candidate(competitor)
        if not competitor_side or competitor_side != best_side:
            return False
    return True


def _scan_chess_local_marker_agreement_evidence(
    candidate_payloads: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    diagram_bboxes: list[tuple[float, float, float, float]],
) -> tuple[str, dict[str, Any]] | None:
    sides = {str(candidate.get("side_candidate") or candidate.get("detected_side") or "") for candidate in candidate_payloads}
    if len(sides) != 1:
        return None
    side = next(iter(sides))
    if side not in {"w", "b"}:
        return None
    local_candidates = [
        candidate
        for candidate in candidate_payloads
        if _scan_chess_marker_is_uniquely_local_to_bbox(candidate, bbox, diagram_bboxes)
    ]
    if len(local_candidates) != len(candidate_payloads):
        return None
    best = max(local_candidates, key=lambda item: float(item.get("score") or 0.0))
    if float(best.get("score") or 0.0) < 300.0:
        return None
    return side, best


def _scan_chess_marker_is_uniquely_local_to_bbox(
    marker_payload: dict[str, Any],
    bbox: tuple[float, float, float, float],
    diagram_bboxes: list[tuple[float, float, float, float]],
) -> bool:
    marker_bbox = marker_payload.get("bbox")
    if not isinstance(marker_bbox, (list, tuple)) or len(marker_bbox) != 4:
        return False
    current_distance = _scan_chess_bbox_edge_distance(tuple(float(value) for value in marker_bbox), bbox)
    comparable: list[float] = []
    for other in diagram_bboxes:
        if _scan_chess_bbox_same_rect(other, bbox) or _scan_chess_bbox_overlap_ratio(other, bbox) >= 0.72:
            continue
        comparable.append(_scan_chess_bbox_edge_distance(tuple(float(value) for value in marker_bbox), other))
    if not comparable:
        return True
    nearest_other = min(comparable)
    return current_distance + 24.0 < nearest_other and current_distance * 1.20 < nearest_other


def _scan_chess_bbox_same_rect(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return all(abs(float(a) - float(b)) <= 2.0 for a, b in zip(left, right))


def _scan_chess_bbox_overlap_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    lx0, ly0, lx1, ly1 = (float(value) for value in left)
    rx0, ry0, rx1, ry1 = (float(value) for value in right)
    inter_w = max(0.0, min(lx1, rx1) - max(lx0, rx0))
    inter_h = max(0.0, min(ly1, ry1) - max(ly0, ry0))
    inter_area = inter_w * inter_h
    left_area = max(1.0, (lx1 - lx0) * (ly1 - ly0))
    right_area = max(1.0, (rx1 - rx0) * (ry1 - ry0))
    return inter_area / max(1.0, min(left_area, right_area))


def _scan_chess_bbox_edge_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    lx0, ly0, lx1, ly1 = (float(value) for value in left)
    rx0, ry0, rx1, ry1 = (float(value) for value in right)
    dx = max(rx0 - lx1, lx0 - rx1, 0.0)
    dy = max(ry0 - ly1, ly0 - ry1, 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _scan_chess_side_to_move_context_evidence(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> ScanChessSideToMoveEvidence | None:
    """Return local side-to-move marker evidence for a board context.

    This P0 recovery path intentionally excludes OCR symbol mapping and AI
    output. It only surfaces deterministic visual marker probes; ambiguous or
    conflicting probes stay as review evidence.
    """
    return _infer_scan_chess_side_to_move_marker_evidence(page_image, bbox)


def _infer_scan_chess_side_to_move(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> str:
    evidence = _infer_scan_chess_side_to_move_marker_evidence(page_image, bbox)
    return evidence.side if evidence is not None else ""


def _infer_scan_chess_side_to_move_marker_evidence(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> ScanChessSideToMoveEvidence | None:
    """Infer side-to-move from a triangle marker near a scanned board.

    In the Yusupov-style exercise pages, an outlined triangle denotes White to
    move and a filled black triangle denotes Black to move. The signal is
    deliberately treated as optional: if no compact triangular component is
    found, the caller keeps the conservative inferred default.
    """
    payloads = _scan_chess_side_marker_probe_payloads(page_image, bbox)
    if any(payload.get("marker_classifier_status") == "side_to_move_marker_local_conflict" for payload in payloads):
        return ScanChessSideToMoveEvidence(
            warnings=(
                "side_to_move_marker_detected",
                "side_to_move_marker_local_conflict",
                "side_to_move_marker_probes_checked",
            ),
            marker_candidates=tuple(payloads),
        )
    detections = [payload for payload in payloads if payload.get("detected_side")]
    if not detections:
        if payloads:
            return ScanChessSideToMoveEvidence(
                warnings=("side_to_move_marker_probes_checked",),
                marker_candidates=tuple(payloads),
            )
        return None
    detected_sides = {str(payload.get("detected_side") or "") for payload in detections if payload.get("detected_side")}
    warnings = {"side_to_move_marker_detected", "side_to_move_marker_probes_checked"}
    if len(detected_sides) > 1:
        dominant = _scan_chess_dominant_side_marker_detection(detections)
        conflict_warnings = warnings | {
            "side_to_move_marker_ambiguous",
            "side_to_move_marker_multi_region_conflict",
        }
        if dominant is not None:
            conflict_warnings.add("side_to_move_marker_dominant_conflict_review_only")
        return ScanChessSideToMoveEvidence(
            warnings=tuple(sorted(conflict_warnings)),
            marker_candidates=tuple(payloads),
        )
    side = next(iter(detected_sides))
    if len(detections) > 1:
        warnings.add("side_to_move_marker_multi_region_agreement")
        warnings.add("side_to_move_marker_local_ambiguous")
        return ScanChessSideToMoveEvidence(
            warnings=tuple(sorted(warnings)),
            marker_candidates=tuple(payloads),
        )
    best = max(detections, key=lambda payload: float(payload.get("score") or 0.0))
    return ScanChessSideToMoveEvidence(
        side=side,
        source="marker",
        raw_text=str(best.get("role") or ""),
        confidence=0.88,
        warnings=tuple(sorted(warnings)),
        source_bbox=tuple(float(value) for value in best.get("bbox", ())) if len(best.get("bbox", ())) == 4 else None,
        marker_candidates=tuple(payloads),
    )


def _scan_chess_dominant_side_marker_detection(detections: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = sorted(detections, key=lambda payload: float(payload.get("score") or 0.0), reverse=True)
    if len(ranked) < 2:
        return None
    top = ranked[0]
    runner_up = ranked[1]
    top_side = str(top.get("detected_side") or "")
    runner_up_side = str(runner_up.get("detected_side") or "")
    if top_side not in {"w", "b"} or runner_up_side not in {"w", "b"} or top_side == runner_up_side:
        return None
    top_score = float(top.get("score") or 0.0)
    runner_up_score = float(runner_up.get("score") or 0.0)
    if top_score < 450.0:
        return None
    if top_score - runner_up_score < 450.0:
        return None
    if runner_up_score <= 0 or top_score / runner_up_score < 1.75:
        return None
    return top


def _scan_chess_side_marker_probe_payloads(
    page_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for probe in _scan_chess_side_marker_probes(bbox, page_image.size):
        payload: dict[str, Any] = {
            "role": probe.role,
            "bbox": [int(value) for value in probe.bbox],
            "distance_to_board": round(_scan_chess_bbox_edge_distance(tuple(float(value) for value in probe.bbox), bbox), 2),
            "conflict_group": _scan_chess_side_marker_conflict_group(probe.role),
        }
        crop = ImageOps.autocontrast(page_image.crop(probe.bbox).convert("L"))
        classification = classify_scan_chess_side_marker_crop(crop)
        payload["marker_classifier_status"] = classification["status"]
        payload["marker_classifier_confidence"] = classification["confidence"]
        component = classification.get("component")
        if component is not None:
            density = float(component["density"])
            payload.update(
                {
                    "density": round(density, 4),
                    "score": round(float(component.get("score") or 0.0), 3),
                    "component_bbox": [round(float(value), 2) for value in tuple(component.get("bbox") or ())],
                }
            )
            if classification.get("side") in {"w", "b"}:
                payload["detected_side"] = classification["side"]
                payload["side_candidate"] = classification["side"]
                payload["detected_shape"] = classification["shape"]
            else:
                payload["ambiguous_density"] = classification["status"] == "side_to_move_marker_local_ambiguous"
                payload["side_candidate"] = ""
                payload["detected_shape"] = classification.get("shape") or "triangle_like_ambiguous_density"
        payloads.append(payload)
    return payloads


def _scan_chess_side_marker_conflict_group(role: str) -> str:
    value = str(role or "")
    if value.startswith("top_") or value.startswith("bottom_"):
        return "corner"
    if value.endswith("_side"):
        return "side_band"
    if value.startswith("caption_"):
        return "caption_band"
    return "unknown"


def _scan_chess_side_marker_probes(
    bbox: tuple[float, float, float, float],
    page_size: tuple[int, int],
) -> list[ScanChessSideMarkerProbe]:
    x0, y0, x1, y1 = (float(value) for value in bbox)
    side = max(1.0, min(x1 - x0, y1 - y0))
    page_width, page_height = page_size

    def clamp(role: str, left: float, top: float, right: float, bottom: float) -> ScanChessSideMarkerProbe | None:
        region = (
            int(max(0, round(left))),
            int(max(0, round(top))),
            int(min(page_width, round(right))),
            int(min(page_height, round(bottom))),
        )
        if region[2] - region[0] < 20 or region[3] - region[1] < 20:
            return None
        return ScanChessSideMarkerProbe(role=role, bbox=region)

    specs = (
        ("top_right", x1 - side * 0.24, y0, x1 + side * 0.04, y0 + side * 0.20),
        ("top_right_outside", x1 + side * 0.02, y0 - side * 0.02, x1 + side * 0.26, y0 + side * 0.26),
        ("top_left", x0 - side * 0.04, y0, x0 + side * 0.24, y0 + side * 0.20),
        ("top_left_outside", x0 - side * 0.26, y0 - side * 0.02, x0 - side * 0.02, y0 + side * 0.26),
        ("bottom_right", x1 - side * 0.24, y1 - side * 0.20, x1 + side * 0.04, y1),
        ("bottom_right_outside", x1 + side * 0.02, y1 - side * 0.26, x1 + side * 0.26, y1 + side * 0.02),
        ("bottom_left", x0 - side * 0.04, y1 - side * 0.20, x0 + side * 0.24, y1),
        ("bottom_left_outside", x0 - side * 0.26, y1 - side * 0.26, x0 - side * 0.02, y1 + side * 0.02),
        ("right_side", x1 + side * 0.02, y0 + side * 0.18, x1 + side * 0.20, y0 + side * 0.58),
        ("left_side", x0 - side * 0.20, y0 + side * 0.18, x0 - side * 0.02, y0 + side * 0.58),
        ("caption_above", x0, y0 - side * 0.22, x1, y0),
        ("caption_below", x0, y1, x1, y1 + side * 0.22),
    )
    probes: list[ScanChessSideMarkerProbe] = []
    seen: set[tuple[int, int, int, int]] = set()
    for role, left, top, right, bottom in specs:
        probe = clamp(role, left, top, right, bottom)
        if probe is None or probe.bbox in seen:
            continue
        seen.add(probe.bbox)
        probes.append(probe)
    return probes


def _scan_chess_side_marker_region(
    bbox: tuple[float, float, float, float],
    page_size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    for probe in _scan_chess_side_marker_probes(bbox, page_size):
        if probe.role == "top_right":
            return probe.bbox
    return None


def _scan_chess_marker_inner_density(mask: Any, bbox: Any) -> float:
    try:
        x0, y0, x1, y1 = [int(round(float(value))) for value in bbox]
    except (TypeError, ValueError):
        return 0.0
    height, width = mask.shape
    x0 = max(0, min(width - 1, x0))
    x1 = max(0, min(width - 1, x1))
    y0 = max(0, min(height - 1, y0))
    y1 = max(0, min(height - 1, y1))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    box_width = max(1, x1 - x0 + 1)
    box_height = max(1, y1 - y0 + 1)
    ys, xs = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
    nx = (xs - x0) / max(1, box_width - 1)
    ny = (ys - y0) / max(1, box_height - 1)
    # Interior of a triangle, inset from the outline so thick borders do not
    # look like filled markers. Check both orientations because book markers use
    # an outline △ for White and a filled ▼ for Black, while some synthetic
    # fixtures only encode the filled/outline property.
    upright_lower = 0.5 - 0.5 * ny + 0.10
    upright_upper = 0.5 + 0.5 * ny - 0.10
    upright = (ny > 0.30) & (ny < 0.82) & (nx >= upright_lower) & (nx <= upright_upper)
    inverted_width = 1.0 - ny
    inverted_lower = 0.5 - 0.5 * inverted_width + 0.10
    inverted_upper = 0.5 + 0.5 * inverted_width - 0.10
    inverted = (ny > 0.18) & (ny < 0.70) & (nx >= inverted_lower) & (nx <= inverted_upper)
    if not int(upright.sum()) and not int(inverted.sum()):
        return 0.0
    local = mask[y0 : y1 + 1, x0 : x1 + 1]
    densities = []
    if int(upright.sum()):
        densities.append(float(local[upright].mean()))
    if int(inverted.sum()):
        densities.append(float(local[inverted].mean()))
    return min(densities) if densities else 0.0


def _scan_chess_marker_ink_density(mask: Any, bbox: Any) -> float:
    try:
        x0, y0, x1, y1 = [int(round(float(value))) for value in bbox]
    except (TypeError, ValueError):
        return 0.0
    height, width = mask.shape
    x0 = max(0, min(width - 1, x0))
    x1 = max(0, min(width - 1, x1))
    y0 = max(0, min(height - 1, y0))
    y1 = max(0, min(height - 1, y1))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    local = mask[y0 : y1 + 1, x0 : x1 + 1]
    return float(local.mean())


def _scan_chess_marker_classifier_result(
    *,
    status: str,
    side: str = "",
    symbol: str | None = None,
    confidence: float = 0.0,
    shape: str = "",
    reason: str,
    component: Mapping[str, Any] | None = None,
    candidate_count: int = 0,
    warnings: Iterable[str] | None = None,
) -> dict[str, Any]:
    side_value = side if side in {"w", "b"} else ""
    return {
        "status": status,
        "side": side_value,
        "side_to_move": side_value or "unknown",
        "symbol": symbol if symbol is not None else "?",
        "confidence": round(float(confidence or 0.0), 3),
        "classifier_version": "marker_shape_v2",
        "reason": reason,
        "shape": shape,
        "component": dict(component) if isinstance(component, Mapping) else None,
        "candidate_count": int(candidate_count or 0),
        "warnings": list(warnings or ["side_to_move_marker_probes_checked"]),
    }


def classify_scan_chess_side_marker_crop(crop: Image.Image) -> dict[str, Any]:
    """Classify a local side-marker crop without OCR.

    The classifier treats an outlined triangle as White to move and a filled
    triangle as Black to move. Ambiguous density or multiple local markers stay
    review-only, so downstream FEN publication still requires trusted evidence.
    """
    raw_grayscale = crop.convert("L")
    crop_width, crop_height = raw_grayscale.size
    grayscale = ImageOps.autocontrast(raw_grayscale)
    dark = np.asarray(grayscale) < 120
    raw_ink = np.asarray(raw_grayscale) < 120
    components = _scan_chess_side_marker_components(dark, relaxed=True)
    if not components:
        relaxed_dark = np.asarray(grayscale) < 160
        components = _scan_chess_side_marker_components(relaxed_dark, relaxed=True)
    if not components:
        return _scan_chess_marker_classifier_result(
            status="marker_missing",
            confidence=0.0,
            reason="marker_missing",
            candidate_count=0,
        )

    classified: list[dict[str, Any]] = []
    for component in components:
        density = float(component["density"])
        aspect = float(component.get("aspect") or 0.0)
        area = float(component.get("area") or 0.0)
        bbox_values = component.get("bbox") or (0.0, 0.0, 0.0, 0.0)
        try:
            bx0, by0, bx1, _ = [float(value) for value in bbox_values]
        except (TypeError, ValueError):
            bx0 = by0 = bx1 = 0.0
        inner_density = _scan_chess_marker_inner_density(dark, component.get("bbox"))
        ink_density = _scan_chess_marker_ink_density(raw_ink, component.get("bbox"))
        row = {
            **component,
            "status": "side_to_move_marker_local_ambiguous",
            "side": "",
            "symbol": "?",
            "shape": "triangle_like_ambiguous_density",
            "reason": "unclear",
            "inner_density": round(inner_density, 4),
            "ink_density": round(ink_density, 4),
        }
        if aspect >= 1.45 and not (inner_density >= 0.55 and density >= 0.35):
            row.update(
                {
                    "status": "side_to_move_marker_local_ambiguous",
                    "shape": "multiple_triangle_candidates",
                    "confidence": 0.0,
                    "reason": "multiple_candidates",
                }
            )
        elif area < 100:
            row.update(
                {
                    "status": "side_to_move_marker_local_ambiguous",
                    "shape": "marker_too_small",
                    "confidence": 0.0,
                    "reason": "too_small",
                }
            )
        elif by0 <= 0.5 or (len(components) == 1 and ((bx0 + bx1) / 2.0) > crop_width * 0.68):
            row.update(
                {
                    "status": "side_to_move_marker_local_ambiguous",
                    "shape": "marker_cut_off",
                    "confidence": 0.0,
                    "reason": "bad_crop",
                }
            )
        elif ink_density < 0.055:
            row.update(
                {
                    "status": "side_to_move_marker_local_ambiguous",
                    "shape": "weak_marker_ink",
                    "confidence": 0.0,
                    "reason": "unclear",
                }
            )
        elif inner_density <= 0.32 and density >= 0.10:
            row.update(
                {
                    "status": "trusted_marker",
                    "side": "w",
                    "symbol": SIDE_MARKER_SYMBOLS["w"],
                    "shape": "outline_triangle",
                    "confidence": round(min(0.985, 0.90 + (0.32 - inner_density) * 0.20 + min(0.06, ink_density * 0.35)), 3),
                    "reason": "outline_triangle",
                }
            )
        elif inner_density >= 0.55 and density >= 0.35:
            row.update(
                {
                    "status": "trusted_marker",
                    "side": "b",
                    "symbol": SIDE_MARKER_SYMBOLS["b"],
                    "shape": "filled_triangle",
                    "confidence": round(min(0.985, 0.91 + (inner_density - 0.55) * 0.12), 3),
                    "reason": "filled_triangle",
                }
            )
        else:
            row["confidence"] = round(max(0.25, 0.52 - abs(density - 0.37)), 3)
        classified.append(row)

    trusted = [item for item in classified if item.get("side") in {"w", "b"}]
    best = max(classified, key=lambda item: float(item.get("score") or 0.0))
    if len({str(item.get("side") or "") for item in trusted}) > 1:
        return _scan_chess_marker_classifier_result(
            status="side_to_move_marker_local_conflict",
            confidence=round(float(best.get("confidence") or 0.0), 3),
            shape="multiple_triangle_conflict",
            reason="multiple_candidates",
            component=best,
            candidate_count=len(classified),
            warnings=["side_to_move_marker_local_conflict", "side_to_move_marker_probes_checked"],
        )
    if len(trusted) > 1:
        return _scan_chess_marker_classifier_result(
            status="side_to_move_marker_local_ambiguous",
            confidence=round(float(best.get("confidence") or 0.0), 3),
            shape="multiple_triangle_candidates",
            reason="multiple_candidates",
            component=best,
            candidate_count=len(classified),
            warnings=["side_to_move_marker_local_ambiguous", "side_to_move_marker_probes_checked"],
        )
    if len(classified) > 1 and trusted:
        return _scan_chess_marker_classifier_result(
            status="side_to_move_marker_local_ambiguous",
            confidence=round(float(best.get("confidence") or 0.0), 3),
            shape="multiple_triangle_candidates",
            reason="multiple_candidates",
            component=best,
            candidate_count=len(classified),
            warnings=["side_to_move_marker_local_ambiguous", "side_to_move_marker_probes_checked"],
        )
    if trusted:
        best_trusted = max(trusted, key=lambda item: float(item.get("score") or 0.0))
        return _scan_chess_marker_classifier_result(
            status="trusted_marker",
            side=str(best_trusted["side"]),
            symbol=str(best_trusted["symbol"]),
            confidence=float(best_trusted["confidence"]),
            shape=str(best_trusted["shape"]),
            reason=str(best_trusted.get("reason") or best_trusted["shape"]),
            component=best_trusted,
            candidate_count=len(classified),
            warnings=["side_to_move_marker_detected", "side_to_move_marker_probes_checked"],
        )
    return _scan_chess_marker_classifier_result(
        status="side_to_move_marker_local_ambiguous",
        confidence=round(float(best.get("confidence") or 0.0), 3),
        shape=best.get("shape") or "triangle_like_ambiguous_density",
        reason=str(best.get("reason") or "unclear"),
        component=best,
        candidate_count=len(classified),
        warnings=["side_to_move_marker_local_ambiguous", "side_to_move_marker_probes_checked"],
    )


def _scan_chess_best_side_marker_component(mask: Any) -> dict[str, float] | None:
    components = _scan_chess_side_marker_components(mask)
    if not components:
        return None
    return max(components, key=lambda item: float(item.get("score") or 0.0))


def _scan_chess_side_marker_components(mask: Any, *, relaxed: bool = False) -> list[dict[str, float]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[dict[str, float]] = []
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
            min_width = max(8 if relaxed else 12, width * (0.15 if relaxed else 0.20))
            min_height = max(8 if relaxed else 12, height * (0.15 if relaxed else 0.22))
            if box_width < min_width or box_height < min_height:
                continue
            if box_width > width * (0.96 if relaxed else 0.88) or box_height > height * (0.96 if relaxed else 0.92):
                continue
            aspect = box_width / max(1, box_height)
            if aspect < (0.45 if relaxed else 0.62) or aspect > (2.10 if relaxed else 1.75):
                continue
            center_x = (min_x + max_x) / 2.0
            center_y = (min_y + max_y) / 2.0
            if center_x < width * (0.08 if relaxed else 0.20) or center_x > width * (0.92 if relaxed else 0.80):
                continue
            if center_y > height * (0.82 if relaxed else 0.72) or max_y > height * (0.995 if relaxed else 0.98):
                continue
            density = area / max(1, box_width * box_height)
            if density < (0.08 if relaxed else 0.16) or density > (0.82 if relaxed else 0.72):
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
            components.append(candidate)
    return components


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
    chess_diagram_records: list[dict[str, Any]] = []
    book_layout_pages: list[dict[str, Any]] = []
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
        text_dict = _page_text_dict_for_glyph_capture(page)
        
        # Collect all text spans and keep raw per-line segments so we can
        # reconstruct readable notation instead of emitting one paragraph per span.
        text_spans = []
        raw_lines = []
        span_index = 0
        
        for block_index, block in enumerate(text_dict.get("blocks", [])):
            if block.get("type") != 0:
                continue
            
            for line_index, line in enumerate(block.get("lines", [])):
                raw_line_segments = []
                for span in line.get("spans", []):
                    segment = _pdf_text_segment_from_span(
                        span,
                        page_num=page_num,
                        block_index=block_index,
                        line_index=line_index,
                        span_index=span_index,
                    )
                    raw_text = segment.get("text", "")
                    text = raw_text.strip()
                    if not text:
                        span_index += 1
                        continue
                    
                    bbox = tuple(segment.get("bbox") or (0, 0, 0, 0))
                    x0, y0, x1, y1 = bbox
                    
                    # Determine CSS font family
                    font_name = str(segment.get("font_name") or "Unknown")
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
                        font_size=segment.get("font_size", 12),
                        is_bold=bool(span.get("flags", 0) & (1 << 4)),
                        is_italic=bool(span.get("flags", 0) & (1 << 1)),
                        color=span.get("color"),
                        bbox=bbox,
                        css_font_family=css_family,
                        css_color=css_color,
                    ))
                    raw_line_segments.append(segment)
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
                        chess_diagram_records.append(
                            _chess_diagram_record_from_image(
                                chess_img,
                                diagram_id=f"text-chess-p{page_num + 1:03d}-d{region_idx + 1:02d}",
                                caption=f"Strona {page_num + 1}, diagram {region_idx + 1}",
                                page_num=page_num,
                                nearby_text=" ".join(
                                    ts.text for ts in text_spans if ts.index in region.text_span_indices
                                ),
                            )
                        )

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
        book_elements = _book_layout_text_elements_from_line_items(line_items)
        page_diagram_records = [
            record
            for record in chess_diagram_records
            if int(record.get("page_index", -1) or -1) == page_num
        ]
        book_elements.extend(
            _book_layout_diagram_elements_from_diagrams(
                page_diagram_records,
                page_num=page_num,
                reading_order_start=10_000,
            )
        )
        book_layout_pages.append(
            _book_layout_page_from_pdf_page(
                page,
                page_num,
                config,
                elements=book_elements,
            )
        )
        diagram_cursor = 0

        def insert_diagram(entry: dict) -> None:
            chess_img = entry["image"]
            fen_attrs = chess_fen_html_attrs(chess_img)
            html_parts.append(
                f'<div class="figure chess-diagram-container"{fen_attrs}>'
                f"{chess_side_marker_html(chess_img)}"
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
    
    book_layout_pages = _ensure_book_layout_pages_cover_document(doc, book_layout_pages, config)
    doc.close()
    source_title = str((pdf_metadata or {}).get("title") or Path(pdf_path).stem)
    
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
        'extra_artifacts': _chess_pdf_extra_artifacts(
            pdf_path,
            config,
            source_title=source_title,
            pgn_records=[],
            diagrams=chess_diagram_records,
            book_layout_pages=book_layout_pages,
        ),
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
