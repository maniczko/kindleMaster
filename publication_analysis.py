from __future__ import annotations

import re
from statistics import mean, median
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import fitz

from ml_features import route_feature_payload
from ml_route_model import build_route_decision
from premium_tools import detect_toolchain
from publication_model import PublicationAnalysis

try:
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None

try:
    from chess_diagram_renderer import CHESS_FONT_INDICATORS, find_chess_diagram_regions
except Exception:
    CHESS_FONT_INDICATORS = []
    find_chess_diagram_regions = None


NUMBERED_SECTION_HEADING_RE = re.compile(r"^\s*\d{1,2}(?:\.\d{1,2}){0,4}\.?\s+\S.{2,}$")
NUMBERED_HEADING_CAPTION_RE = re.compile(
    r"(?i)^\s*(?:table|tabela|tab\.|figure|fig\.|rys\.?|rysunek|diagram|wykres|chart|exhibit)\s+\d"
)
CHESS_MOVE_TOKEN_RE = re.compile(
    r"\b(?:\d{1,3}\.(?:\.\.)?|O-O(?:-O)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|[a-h]x?[a-h]?[1-8](?:=[QRBN])?[+#]?|1-0|0-1|1/2-1/2)\b"
)
CHESS_GAME_META_RE = re.compile(
    r"(?i)\b(?:[A-E][0-9]{2}|blitz|rapid|classical|white|black|variation|attack|defen[cs]e|gambit|titled|chess)\b"
)
LARGE_DOCUMENT_SAMPLE_ANALYSIS_MIN_PAGES = 360
LARGE_DOCUMENT_SAMPLE_HEAD_PAGES = 24
LARGE_DOCUMENT_SPARSE_SAMPLE_PAGES = 24


def analyze_publication(
    pdf_path: str,
    preferred_profile: str = "auto-premium",
    route_model_mode: str = "shadow",
) -> PublicationAnalysis:
    analysis_started = perf_counter()
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    total_pages = len(doc)
    scanned_pages = 0
    pages_with_text = 0
    pages_with_images = 0
    heading_scores: list[float] = []
    font_medians: list[float] = []
    column_estimates: list[int] = []
    text_block_counts: list[int] = []
    visual_area_ratios: list[float] = []
    meaningful_image_pages = 0
    detected_diagrams = 0
    numbered_heading_keys: set[str] = set()
    sample_texts: list[str] = []

    sample_pages = list(range(min(total_pages, LARGE_DOCUMENT_SAMPLE_HEAD_PAGES)))
    sampled_large_chess_notation = _should_use_sampled_large_chess_notation_analysis(doc, total_pages)
    scan_pages = (
        _large_document_sparse_sample_pages(total_pages)
        if sampled_large_chess_notation
        else list(range(total_pages))
    )

    for page_num in scan_pages:
        page = doc[page_num]
        text = page.get_text().strip()
        has_text = len(text) > 50
        images = page.get_images(full=True)
        if has_text:
            pages_with_text += 1
        if images:
            pages_with_images += 1

        if not has_text and images:
            scanned_pages += 1
        elif has_text and images and not sampled_large_chess_notation:
            text_area = 0.0
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 0:
                    x0, y0, x1, y1 = block["bbox"]
                    text_area += (x1 - x0) * (y1 - y0)
            page_area = page.rect.width * page.rect.height
            if text_area < page_area * 0.1:
                scanned_pages += 1

        if page_num in sample_pages:
            sample_texts.append(text)
            page_dict = page.get_text("dict", sort=True)
            font_sizes = []
            heading_blocks = 0
            image_blocks = 0
            x_centers = []
            for block in page_dict.get("blocks", []):
                if block.get("type") == 0:
                    text_block_counts.append(1)
                    block_fonts = []
                    text_fragments = []
                    for line in block.get("lines", []):
                        line_fragments = []
                        line_sizes = []
                        for span in line.get("spans", []):
                            span_text = (span.get("text") or "").strip()
                            if span_text:
                                text_fragments.append(span_text)
                                line_fragments.append(span_text)
                            size = float(span.get("size", 0.0))
                            if size:
                                font_sizes.append(size)
                                block_fonts.append(size)
                                line_sizes.append(size)
                        line_text = re.sub(r"\s+", " ", " ".join(line_fragments)).strip()
                        bbox = line.get("bbox", (0, 0, 0, 0))
                        line_size = max(line_sizes) if line_sizes else 0.0
                        if _looks_like_numbered_section_heading(
                            line_text,
                            x0=float(bbox[0] or 0.0),
                            page_width=float(page.rect.width or 0.0),
                            font_size=line_size,
                        ):
                            numbered_heading_keys.add(_numbered_heading_key(line_text))
                    if block_fonts:
                        block_median = median(block_fonts)
                        if block_median >= 14 and len(" ".join(text_fragments).split()) <= 20:
                            heading_blocks += 1
                        x0, _, x1, _ = block.get("bbox", (0, 0, 0, 0))
                        if (x1 - x0) >= page.rect.width * 0.18:
                            x_centers.append((x0 + x1) / 2)
                elif block.get("type") == 1:
                    x0, y0, x1, y1 = block.get("bbox", (0, 0, 0, 0))
                    area_ratio = ((x1 - x0) * (y1 - y0)) / max(page.rect.width * page.rect.height, 1)
                    visual_area_ratios.append(max(0.0, min(area_ratio, 1.0)))
                    if 0.02 <= area_ratio <= 0.8:
                        image_blocks += 1

            if font_sizes:
                body_font = median(font_sizes)
                font_medians.append(body_font)
                heading_scores.append(heading_blocks / max(len(page_dict.get("blocks", [])), 1))
            if image_blocks:
                meaningful_image_pages += 1
            column_estimates.append(_estimate_columns_from_centers(x_centers, page.rect.width))

            if find_chess_diagram_regions is not None:
                try:
                    detected_diagrams += len(find_chess_diagram_regions(_page_text_spans(page, page_num)))
                except Exception:
                    pass

    doc.close()

    observed_page_count = len(scan_pages) if sampled_large_chess_notation else total_pages
    estimated_pages_with_images = _estimate_total_from_sample(
        pages_with_images,
        observed_page_count=observed_page_count,
        total_pages=total_pages,
        sampled=sampled_large_chess_notation,
    )
    estimated_pages_with_text = _estimate_total_from_sample(
        pages_with_text,
        observed_page_count=observed_page_count,
        total_pages=total_pages,
        sampled=sampled_large_chess_notation,
    )
    estimated_scanned_pages = _estimate_total_from_sample(
        scanned_pages,
        observed_page_count=observed_page_count,
        total_pages=total_pages,
        sampled=sampled_large_chess_notation,
    )
    image_page_ratio = (estimated_pages_with_images / total_pages) if total_pages else 0.0
    text_page_ratio = (estimated_pages_with_text / total_pages) if total_pages else 0.0
    scanned_page_ratio = (estimated_scanned_pages / total_pages) if total_pages else 0.0
    layout_heavy = pages_with_images > 0 and image_page_ratio >= 0.35
    text_heavy = estimated_pages_with_text > total_pages * 0.5 and image_page_ratio <= 0.15
    has_toc = bool(toc)
    has_chess_training_outline = _has_chess_training_outline(toc)
    has_meaningful_images = meaningful_image_pages > 0
    has_tables = False if preferred_profile == "diagram_book_reflow" else _detect_tables(pdf_path, sample_pages)
    chess_font_signal = _detect_chess_fonts(pdf_path)
    has_diagrams = detected_diagrams > 0 or chess_font_signal
    estimated_columns = round(mean(column_estimates)) if column_estimates else 1
    heading_density = mean(heading_scores) if heading_scores else 0.0
    font_consistency = _font_consistency(font_medians)
    sampled_page_count = max(len(sample_pages), 1)
    visual_density = max(visual_area_ratios) if visual_area_ratios else image_page_ratio
    dominant_visual_ratio = max(visual_area_ratios) if visual_area_ratios else 0.0
    text_block_density = sum(text_block_counts) / sampled_page_count
    layout_entropy = _layout_entropy(column_estimates, visual_area_ratios)
    toc_depth = _toc_depth(toc)
    toc_noise_score = _toc_noise_score(toc)
    diagram_signal_count = int(detected_diagrams)
    chess_signal_count = 1 if chess_font_signal else 0
    ocr_confidence = 0.0 if scanned_page_ratio >= 0.35 else 1.0
    toolchain = detect_toolchain()
    ocr_supported = bool(((toolchain.get("conversion_capabilities") or {}).get("ocr_pipeline") or {}).get("status") == "supported")
    tesseract_languages = (((toolchain.get("tesseract") or {}).get("languages")) or [])
    ocr_language_available = bool({"eng", "pol"} & {str(item).lower() for item in tesseract_languages})
    estimated_sections = _estimate_sections_from_toc(toc) if toc else _estimate_sections_from_headings(heading_density, total_pages)
    legacy_strategy = (
        "ocr_fixed"
        if scanned_page_ratio > 0.5
        else "layout_fixed"
        if layout_heavy
        else "hybrid"
        if has_meaningful_images and text_page_ratio > 0.5
        else "text_reflowable"
    )
    render_budget_class = _choose_render_budget_class(
        total_pages=total_pages,
        scanned_page_ratio=scanned_page_ratio,
        has_diagrams=has_diagrams,
        has_meaningful_images=has_meaningful_images,
        layout_heavy=layout_heavy,
        estimated_columns=estimated_columns,
    )

    profile, ui_profile, profile_reason = _choose_profile(
        preferred_profile=preferred_profile,
        total_pages=total_pages,
        has_toc=has_toc,
        has_tables=has_tables,
        has_diagrams=has_diagrams,
        has_meaningful_images=has_meaningful_images,
        estimated_columns=estimated_columns,
        layout_heavy=layout_heavy,
        text_heavy=text_heavy,
        text_page_ratio=text_page_ratio,
        scanned_page_ratio=scanned_page_ratio,
        legacy_strategy=legacy_strategy,
        numbered_section_count=numbered_section_count,
        has_chess_training_outline=has_chess_training_outline,
        has_chess_notation_collection=has_chess_notation_collection,
    )
    confidence = _estimate_confidence(
        profile=profile,
        has_toc=has_toc,
        has_tables=has_tables,
        has_diagrams=has_diagrams,
        estimated_columns=estimated_columns,
        text_page_ratio=text_page_ratio,
        scanned_page_ratio=scanned_page_ratio,
        font_consistency=font_consistency,
    )
    fallback_recommendation = _fallback_recommendation(profile, confidence, scanned_page_ratio)
    feature_payload = route_feature_payload(
        {
            "page_count": total_pages,
            "text_page_ratio": text_page_ratio,
            "scanned_page_ratio": scanned_page_ratio,
            "image_page_ratio": image_page_ratio,
            "visual_density": visual_density,
            "dominant_visual_ratio": dominant_visual_ratio,
            "text_block_density": text_block_density,
            "layout_entropy": layout_entropy,
            "has_toc": has_toc,
            "toc_depth": toc_depth,
            "toc_noise_score": toc_noise_score,
            "has_tables": has_tables,
            "has_diagrams": has_diagrams,
            "diagram_signal_count": diagram_signal_count,
            "chess_signal_count": chess_signal_count,
            "has_meaningful_images": has_meaningful_images,
            "non_content_ratio": 0.0,
            "ocr_confidence": ocr_confidence,
            "ocr_supported": ocr_supported,
            "ocr_language_available": ocr_language_available,
            "estimated_columns": estimated_columns,
            "heading_density": heading_density,
            "font_consistency": font_consistency,
            "layout_heavy": layout_heavy,
            "text_heavy": text_heavy,
            "scanned_pages": estimated_scanned_pages,
            "text_pages": estimated_pages_with_text,
            "image_pages": estimated_pages_with_images,
        },
        input_type="pdf",
    )
    route_decision = build_route_decision(
        heuristic_profile=profile,
        heuristic_confidence=confidence,
        features=feature_payload,
        mode=route_model_mode,
        allow_override=not (preferred_profile and preferred_profile != "auto-premium") and not has_chess_notation_collection,
    )
    if route_decision.get("override_used"):
        original_profile = profile
        profile = str(route_decision.get("selected_profile", "") or profile)
        confidence = float(route_decision.get("ml_confidence", confidence) or confidence)
        ui_profile = _ui_profile_for_profile(profile, fallback=ui_profile)
        profile_reason = (
            f"ML assist selected {profile}; heuristic was {original_profile}. "
            f"Reason codes: {', '.join(route_decision.get('reason_codes', []))}."
        )
        fallback_recommendation = _fallback_recommendation(profile, confidence, scanned_page_ratio)

    features = []
    if has_toc:
        features.append("bookmarks/toc")
    if has_tables:
        features.append("tables")
    if has_diagrams:
        features.append("diagrams")
    if has_meaningful_images:
        features.append("meaningful-images")
    if has_chess_training_outline:
        features.append("chess-training-outline")
    if has_chess_notation_collection:
        features.append("chess-notation-collection")
    if layout_heavy:
        features.append("layout-heavy")
    if text_heavy:
        features.append("text-heavy")
    if estimated_columns >= 2:
        features.append(f"{estimated_columns}-column")
    if sampled_large_chess_notation:
        features.append("sampled-analysis")
    if numbered_section_count >= 3:
        features.append("numbered-sections")
    if _is_document_like_report_candidate(
        has_toc=has_toc,
        has_tables=has_tables,
        estimated_columns=estimated_columns,
        text_page_ratio=text_page_ratio,
        scanned_page_ratio=scanned_page_ratio,
        total_pages=total_pages,
    ):
        features.append("document-like-report")

    return PublicationAnalysis(
        profile=profile,
        confidence=confidence,
        page_count=total_pages,
        render_budget_class=render_budget_class,
        has_toc=has_toc,
        has_tables=has_tables,
        has_diagrams=has_diagrams,
        has_meaningful_images=has_meaningful_images,
        estimated_sections=estimated_sections,
        fallback_recommendation=fallback_recommendation,
        ui_profile=ui_profile,
        legacy_strategy=legacy_strategy,
        has_text_layer=text_page_ratio > 0.5,
        is_scanned=scanned_page_ratio > 0.5,
        layout_heavy=layout_heavy,
        text_heavy=text_heavy,
        scanned_pages=scanned_pages,
        text_pages=pages_with_text,
        image_pages=pages_with_images,
        visual_density=visual_density,
        dominant_visual_ratio=dominant_visual_ratio,
        text_block_density=text_block_density,
        layout_entropy=layout_entropy,
        toc_depth=toc_depth,
        toc_noise_score=toc_noise_score,
        diagram_signal_count=diagram_signal_count,
        chess_signal_count=chess_signal_count,
        non_content_ratio=0.0,
        ocr_confidence=ocr_confidence,
        ocr_supported=ocr_supported,
        ocr_language_available=ocr_language_available,
        estimated_columns=estimated_columns,
        heading_density=heading_density,
        font_consistency=font_consistency,
        detected_features=features,
        external_tools=toolchain,
        profile_reason=profile_reason,
        detected_outline_entries=numbered_section_count,
        route_decision=route_decision,
        analysis_seconds=round(perf_counter() - analysis_started, 6),
    )


def _large_document_sparse_sample_pages(total_pages: int) -> list[int]:
    if total_pages <= 0:
        return []
    head_pages = list(range(min(total_pages, LARGE_DOCUMENT_SAMPLE_HEAD_PAGES)))
    if total_pages <= LARGE_DOCUMENT_SAMPLE_HEAD_PAGES:
        return head_pages
    sparse_count = min(LARGE_DOCUMENT_SPARSE_SAMPLE_PAGES, max(0, total_pages - len(head_pages)))
    sparse_pages = {
        round(index * (total_pages - 1) / max(1, sparse_count - 1))
        for index in range(sparse_count)
    }
    return sorted(set(head_pages) | {page for page in sparse_pages if 0 <= page < total_pages})


def _should_use_sampled_large_chess_notation_analysis(doc: fitz.Document, total_pages: int) -> bool:
    if total_pages < LARGE_DOCUMENT_SAMPLE_ANALYSIS_MIN_PAGES:
        return False
    sample_count = min(total_pages, LARGE_DOCUMENT_SAMPLE_HEAD_PAGES)
    if sample_count <= 0:
        return False
    sample_texts: list[str] = []
    text_pages = 0
    image_pages = 0
    scanned_pages = 0
    for page_num in range(sample_count):
        page = doc[page_num]
        text = page.get_text().strip()
        images = page.get_images(full=True)
        has_text = len(text) > 50
        if has_text:
            text_pages += 1
        if images:
            image_pages += 1
        if not has_text and images:
            scanned_pages += 1
        sample_texts.append(text)
    return _detect_chess_notation_collection(
        sample_texts,
        total_pages=total_pages,
        text_page_ratio=text_pages / sample_count,
        image_page_ratio=image_pages / sample_count,
        scanned_page_ratio=scanned_pages / sample_count,
    )


def _estimate_total_from_sample(
    count: int,
    *,
    observed_page_count: int,
    total_pages: int,
    sampled: bool,
) -> int:
    if not sampled or observed_page_count <= 0:
        return count
    ratio = max(0.0, min(float(count) / float(observed_page_count), 1.0))
    return max(0, min(total_pages, int(round(ratio * total_pages))))


def _choose_render_budget_class(
    *,
    total_pages: int,
    scanned_page_ratio: float,
    has_diagrams: bool,
    has_meaningful_images: bool,
    layout_heavy: bool,
    estimated_columns: int,
) -> str:
    if total_pages >= 360 or scanned_page_ratio >= 0.65:
        return "fixed_layout_extreme"
    if total_pages >= 240 or (layout_heavy and total_pages >= 120):
        return "fixed_layout_aggressive"
    if total_pages >= 120 or has_diagrams or (layout_heavy and estimated_columns >= 2):
        return "fixed_layout_dense"
    if total_pages >= 60 or has_meaningful_images or layout_heavy:
        return "fixed_layout_balanced"
    return "fixed_layout_safe"


def _layout_entropy(column_estimates: list[int], visual_area_ratios: list[float]) -> float:
    if not column_estimates and not visual_area_ratios:
        return 0.0
    column_variants = len({max(1, int(value)) for value in column_estimates})
    column_signal = min(column_variants / 3.0, 1.0)
    visual_signal = 1.0 if any(value >= 0.25 for value in visual_area_ratios) else 0.0
    mixed_visual_signal = 1.0 if visual_area_ratios and 0 < len(visual_area_ratios) < max(len(column_estimates), 1) else 0.0
    return max(0.0, min((column_signal * 0.5) + (visual_signal * 0.35) + (mixed_visual_signal * 0.15), 1.0))


def _toc_depth(toc: list[list[Any]]) -> int:
    levels = [int(row[0]) for row in toc if row and str(row[0]).isdigit()]
    return max(levels) if levels else 0


def _toc_noise_score(toc: list[list[Any]]) -> float:
    if not toc:
        return 0.0
    noisy = 0
    total = 0
    for row in toc:
        if len(row) < 2:
            continue
        total += 1
        title = str(row[1] or "").strip()
        words = title.split()
        if not title or title.isdigit() or len(words) > 14 or len(title) > 110:
            noisy += 1
    return noisy / max(total, 1)


def _estimate_columns_from_centers(x_centers: list[float], page_width: float) -> int:
    if len(x_centers) < 2:
        return 1
    left = sum(1 for center in x_centers if center < page_width * 0.42)
    right = sum(1 for center in x_centers if center > page_width * 0.58)
    if left >= 2 and right >= 2:
        return 2
    return 1


def _detect_tables(pdf_path: str, sample_pages: list[int]) -> bool:
    if pdfplumber is None:
        return False
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_index in sample_pages[:10]:
                if page_index >= len(pdf.pages):
                    break
                tables = pdf.pages[page_index].find_tables()
                if tables:
                    return True
    except Exception:
        return False
    return False


def _detect_chess_fonts(pdf_path: str) -> bool:
    doc = fitz.open(pdf_path)
    try:
        for page_index in range(min(len(doc), 20)):
            fonts = page_fonts(doc[page_index])
            if any(any(indicator in font.lower() for indicator in CHESS_FONT_INDICATORS) for font in fonts):
                return True
    finally:
        doc.close()
    return False


def _detect_chess_notation_collection(
    sample_texts: list[str],
    *,
    total_pages: int,
    text_page_ratio: float,
    image_page_ratio: float,
    scanned_page_ratio: float,
) -> bool:
    """Detect text-layer chess game collections that look like magazines geometrically.

    Large chess databases/notes often have two columns and many board images,
    so pure layout heuristics route them to magazine reflow. Their primary
    reading value is SAN/PGN notation text, so they need a notation-first path
    that does not rasterize every embedded board image.
    """
    if total_pages < 40:
        return False
    if text_page_ratio < 0.65 or scanned_page_ratio >= 0.25:
        return False
    if image_page_ratio < 0.15:
        return False

    notation_pages = 0
    meta_pages = 0
    total_tokens = 0
    for text in sample_texts[:24]:
        normalized = re.sub(r"\s+", " ", text or "").strip()
        if not normalized:
            continue
        tokens = CHESS_MOVE_TOKEN_RE.findall(normalized)
        token_count = len(tokens)
        total_tokens += token_count
        if token_count >= 8:
            notation_pages += 1
        if token_count >= 4 and CHESS_GAME_META_RE.search(normalized):
            meta_pages += 1

    sampled_pages = max(1, len([text for text in sample_texts[:24] if str(text or "").strip()]))
    return notation_pages >= 3 and meta_pages >= 2 and total_tokens >= max(24, sampled_pages * 4)


def _page_text_spans(page: fitz.Page, page_num: int) -> list[SimpleNamespace]:
    spans: list[SimpleNamespace] = []
    index = 0
    for block in page.get_text("dict", sort=True).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = (span.get("text") or "").strip()
                if not text:
                    index += 1
                    continue
                x0, y0, x1, y1 = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                spans.append(
                    SimpleNamespace(
                        index=index,
                        page_num=page_num,
                        text=text,
                        x=x0,
                        y=y0,
                        width=x1 - x0,
                        height=y1 - y0,
                        bbox=(x0, y0, x1, y1),
                        font_name=span.get("font", "") or "",
                        font_size=float(span.get("size", 0.0)),
                    )
                )
                index += 1
    return spans


def page_fonts(page: fitz.Page) -> set[str]:
    names: set[str] = set()
    for block in page.get_text("dict", sort=True).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font = span.get("font")
                if font:
                    names.add(font)
    return names


def _font_consistency(font_medians: list[float]) -> float:
    if len(font_medians) <= 1:
        return 1.0
    median_font = median(font_medians)
    max_deviation = max(abs(size - median_font) for size in font_medians)
    return max(0.0, 1.0 - (max_deviation / max(median_font, 1.0)))


def _estimate_sections_from_headings(heading_density: float, total_pages: int) -> int:
    estimate = int(max(4, min(total_pages, total_pages * max(heading_density, 0.05))))
    return estimate


def _looks_like_numbered_section_heading(
    text: str,
    *,
    x0: float = 0.0,
    page_width: float = 0.0,
    font_size: float = 0.0,
) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized or len(normalized) > 170:
        return False
    if NUMBERED_HEADING_CAPTION_RE.match(normalized):
        return False
    if not NUMBERED_SECTION_HEADING_RE.match(normalized):
        return False
    if re.search(r"[.!?;:]$", normalized):
        return False
    if page_width > 0 and x0 > max(132.0, page_width * 0.34):
        return False
    if font_size and font_size < 8.0:
        return False
    return len(normalized.split()) >= 3


def _numbered_heading_key(text: str) -> str:
    return re.sub(r"\W+", "", (text or "").lower())


def _estimate_sections_from_toc(toc: list) -> int:
    if not toc:
        return 0
    top_level = [entry for entry in toc if entry[0] == 1]
    if len(top_level) >= 4:
        return len(top_level)
    major = [entry for entry in toc if entry[0] <= 2]
    if major:
        return min(len(major), 40)
    return len(toc)


def _choose_profile(**kwargs) -> tuple[str, str, str]:
    preferred_profile = kwargs["preferred_profile"]
    if preferred_profile and preferred_profile != "auto-premium":
        explicit_profiles = {
            "book": ("book_reflow", "book"),
            "book_reflow": ("book_reflow", "book"),
            "diagram_book_reflow": ("diagram_book_reflow", "book"),
            "magazine": ("magazine_reflow", "magazine"),
            "magazine_reflow": ("magazine_reflow", "magazine"),
            "premium_scanned_chess_reflow": ("premium_scanned_chess_reflow", "book"),
            "scan_chess_reflow": ("premium_scanned_chess_reflow", "book"),
            "technical-study": ("book_reflow", "technical-study"),
            "scanned_reflow": ("scanned_reflow", "book"),
            "preserve-layout": ("fixed_layout_fallback", "preserve-layout"),
            "fixed_layout_fallback": ("fixed_layout_fallback", "preserve-layout"),
        }
        mapped, ui_profile = explicit_profiles.get(preferred_profile, ("book_reflow", "book"))
        return mapped, ui_profile, "Profil wymuszony przez użytkownika."

    scanned_page_ratio = kwargs["scanned_page_ratio"]
    has_diagrams = kwargs["has_diagrams"]
    layout_heavy = kwargs["layout_heavy"]
    text_heavy = kwargs["text_heavy"]
    estimated_columns = kwargs["estimated_columns"]
    has_tables = kwargs["has_tables"]
    has_toc = kwargs["has_toc"]
    has_meaningful_images = kwargs["has_meaningful_images"]
    has_chess_training_outline = bool(kwargs.get("has_chess_training_outline", False))
    has_chess_notation_collection = bool(kwargs.get("has_chess_notation_collection", False))
    legacy_strategy = kwargs["legacy_strategy"]
    text_page_ratio = float(kwargs.get("text_page_ratio", 0.0) or 0.0)
    total_pages = int(kwargs.get("total_pages", 0) or 0)
    numbered_section_count = int(kwargs.get("numbered_section_count", 0) or 0)

    if scanned_page_ratio > 0.55 and has_chess_training_outline:
        return (
            "premium_scanned_chess_reflow",
            "book",
            "Wykryto skanowaną publikację szachową; używam segmentacji diagramów zamiast pełnostronicowego EPUB obrazowego.",
        )
    if scanned_page_ratio > 0.55:
        return "scanned_reflow", "preserve-layout", "Duży udział stron skanowanych wymaga OCR/fallbacków."
    if has_chess_notation_collection:
        return (
            "book_reflow",
            "book",
            "Wykryto tekstowa kolekcje partii szachowych; uzywam notacji-first reflow bez magazynowej rasteryzacji tysiecy diagramow.",
        )
    if has_diagrams and (has_toc or text_heavy):
        return "diagram_book_reflow", "book", "Wykryto publikację tekstową z diagramami wymagającymi image-first."
    if _is_document_like_report_candidate(
        has_toc=has_toc,
        has_tables=has_tables,
        estimated_columns=estimated_columns,
        text_page_ratio=text_page_ratio,
        scanned_page_ratio=scanned_page_ratio,
        total_pages=total_pages,
    ):
        return "book_reflow", "technical-study", "Wykryto raport/dokument techniczny z jedną kolumną, spisem treści i tabelami."
    if (
        layout_heavy
        and has_meaningful_images
        and estimated_columns >= 2
        and total_pages >= 40
        and scanned_page_ratio < 0.35
    ):
        return "magazine_reflow", "magazine", "Detected a long multi-column publication with images; magazine reflow protects reading order."
    if _is_numbered_document_like_report_candidate(
        numbered_section_count=numbered_section_count,
        has_tables=has_tables,
        text_page_ratio=text_page_ratio,
        scanned_page_ratio=scanned_page_ratio,
        total_pages=total_pages,
    ):
        return "book_reflow", "technical-study", "Wykryto raport/dokument techniczny: numerowane sekcje i tabele."
    if layout_heavy and has_meaningful_images and scanned_page_ratio < 0.35:
        return "magazine_reflow", "magazine", "Wykryto publikację layout-heavy z warstwą tekstową, lepszą do article-first reflow niż do screenshotów."
    if has_tables and has_toc:
        return "book_reflow", "technical-study", "Wykryto książkę techniczną/studyjną z tabelami i spisem treści."
    if text_heavy or has_toc:
        return "book_reflow", "book", "Wykryto publikację tekstową typu książka."
    if legacy_strategy == "layout_fixed":
        return "fixed_layout_fallback", "preserve-layout", "Układ dokumentu jest zbyt ciężki dla bezpiecznego reflow."
    return "book_reflow", "book", "Domyślny profil tekstowy."


def _ui_profile_for_profile(profile: str, *, fallback: str) -> str:
    mapping = {
        "book_reflow": "book",
        "diagram_book_reflow": "book",
        "magazine_reflow": "magazine",
        "premium_scanned_chess_reflow": "book",
        "scanned_reflow": "preserve-layout",
        "fixed_layout_fallback": "preserve-layout",
        "docx_reflow": "book",
    }
    return mapping.get(str(profile or "").strip().lower(), fallback)


def _is_document_like_report_candidate(
    *,
    has_toc: bool,
    has_tables: bool,
    estimated_columns: int,
    text_page_ratio: float,
    scanned_page_ratio: float,
    total_pages: int,
) -> bool:
    if not has_toc or not has_tables:
        return False
    if estimated_columns > 1:
        return False
    if scanned_page_ratio >= 0.35:
        return False
    if text_page_ratio < 0.65:
        return False
    return total_pages >= 3


def _is_numbered_document_like_report_candidate(
    *,
    numbered_section_count: int,
    has_tables: bool,
    text_page_ratio: float,
    scanned_page_ratio: float,
    total_pages: int,
) -> bool:
    if numbered_section_count < 3 or not has_tables:
        return False
    if scanned_page_ratio >= 0.35 or text_page_ratio < 0.65:
        return False
    return total_pages >= 3


def _estimate_confidence(**kwargs) -> float:
    confidence = 0.5
    if kwargs["has_toc"]:
        confidence += 0.14
    if kwargs["has_tables"]:
        confidence += 0.05
    if kwargs["has_diagrams"]:
        confidence += 0.08
    if kwargs["estimated_columns"] == 2 and kwargs["profile"] == "magazine_reflow":
        confidence += 0.1
    if kwargs["text_page_ratio"] >= 0.8:
        confidence += 0.1
    confidence += kwargs["font_consistency"] * 0.08
    confidence -= kwargs["scanned_page_ratio"] * 0.25
    return max(0.35, min(confidence, 0.97))


def _fallback_recommendation(profile: str, confidence: float, scanned_page_ratio: float) -> str:
    if profile == "premium_scanned_chess_reflow":
        return "scan-chess-segment-and-review"
    if profile == "fixed_layout_fallback":
        return "render-whole-document-fixed"
    if scanned_page_ratio > 0.3:
        return "ocr-then-fallback-sections"
    if confidence < 0.6:
        return "page-level-figure-fallback"
    return "semantic-reflow"


def _has_chess_training_outline(toc: list) -> bool:
    if not toc:
        return False
    joined = " ".join(str(entry[1] or "") for entry in toc if len(entry) >= 2).lower()
    if not joined:
        return False
    markers = {
        "mate",
        "mating",
        "tactic",
        "tactics",
        "pawn",
        "bishop",
        "queen",
        "king",
        "rook",
        "knight",
        "opening",
        "gambit",
        "stalemate",
        "combination",
        "pin",
        "opposition",
        "diagram",
        "szach",
        "mat",
        "pion",
        "goniec",
        "hetman",
        "wieża",
        "wieza",
        "skoczek",
        "roszada",
    }
    return sum(1 for marker in markers if marker in joined) >= 3
