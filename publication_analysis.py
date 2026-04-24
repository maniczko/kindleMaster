from __future__ import annotations

import re
from statistics import mean, median
from types import SimpleNamespace

import fitz

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


def analyze_publication(pdf_path: str, preferred_profile: str = "auto-premium") -> PublicationAnalysis:
    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    total_pages = len(doc)
    scanned_pages = 0
    pages_with_text = 0
    pages_with_images = 0
    heading_scores: list[float] = []
    font_medians: list[float] = []
    column_estimates: list[int] = []
    meaningful_image_pages = 0
    detected_diagrams = 0

    sample_pages = list(range(min(total_pages, 24)))

    for page_num in range(total_pages):
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
        elif has_text and images:
            text_area = 0.0
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") == 0:
                    x0, y0, x1, y1 = block["bbox"]
                    text_area += (x1 - x0) * (y1 - y0)
            page_area = page.rect.width * page.rect.height
            if text_area < page_area * 0.1:
                scanned_pages += 1

        if page_num in sample_pages:
            page_dict = page.get_text("dict", sort=True)
            font_sizes = []
            heading_blocks = 0
            image_blocks = 0
            x_centers = []
            for block in page_dict.get("blocks", []):
                if block.get("type") == 0:
                    block_fonts = []
                    text_fragments = []
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            span_text = (span.get("text") or "").strip()
                            if span_text:
                                text_fragments.append(span_text)
                            size = float(span.get("size", 0.0))
                            if size:
                                font_sizes.append(size)
                                block_fonts.append(size)
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

    image_page_ratio = (pages_with_images / total_pages) if total_pages else 0.0
    text_page_ratio = (pages_with_text / total_pages) if total_pages else 0.0
    scanned_page_ratio = (scanned_pages / total_pages) if total_pages else 0.0
    layout_heavy = pages_with_images > 0 and image_page_ratio >= 0.35
    text_heavy = pages_with_text > total_pages * 0.5 and image_page_ratio <= 0.15
    has_toc = bool(toc)
    has_meaningful_images = meaningful_image_pages > 0
    has_tables = _detect_tables(pdf_path, sample_pages)
    has_diagrams = detected_diagrams > 0 or _detect_chess_fonts(pdf_path)
    estimated_columns = round(mean(column_estimates)) if column_estimates else 1
    heading_density = mean(heading_scores) if heading_scores else 0.0
    font_consistency = _font_consistency(font_medians)
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
        has_toc=has_toc,
        has_tables=has_tables,
        has_diagrams=has_diagrams,
        has_meaningful_images=has_meaningful_images,
        estimated_columns=estimated_columns,
        layout_heavy=layout_heavy,
        text_heavy=text_heavy,
        scanned_page_ratio=scanned_page_ratio,
        legacy_strategy=legacy_strategy,
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

    features = []
    if has_toc:
        features.append("bookmarks/toc")
    if has_tables:
        features.append("tables")
    if has_diagrams:
        features.append("diagrams")
    if has_meaningful_images:
        features.append("meaningful-images")
    if layout_heavy:
        features.append("layout-heavy")
    if text_heavy:
        features.append("text-heavy")
    if estimated_columns >= 2:
        features.append(f"{estimated_columns}-column")

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
        estimated_columns=estimated_columns,
        heading_density=heading_density,
        font_consistency=font_consistency,
        detected_features=features,
        external_tools=detect_toolchain(),
        profile_reason=profile_reason,
    )


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
    legacy_strategy = kwargs["legacy_strategy"]

    if scanned_page_ratio > 0.55:
        return "scanned_reflow", "preserve-layout", "Duży udział stron skanowanych wymaga OCR/fallbacków."
    if has_diagrams and (has_toc or text_heavy):
        return "diagram_book_reflow", "book", "Wykryto publikację tekstową z diagramami wymagającymi image-first."
    if layout_heavy and has_meaningful_images and scanned_page_ratio < 0.35:
        return "magazine_reflow", "magazine", "Wykryto publikację layout-heavy z warstwą tekstową, lepszą do article-first reflow niż do screenshotów."
    if has_tables and has_toc:
        return "book_reflow", "technical-study", "Wykryto książkę techniczną/studyjną z tabelami i spisem treści."
    if text_heavy or has_toc:
        return "book_reflow", "book", "Wykryto publikację tekstową typu książka."
    if legacy_strategy == "layout_fixed":
        return "fixed_layout_fallback", "preserve-layout", "Układ dokumentu jest zbyt ciężki dla bezpiecznego reflow."
    return "book_reflow", "book", "Domyślny profil tekstowy."


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
    if profile == "fixed_layout_fallback":
        return "render-whole-document-fixed"
    if scanned_page_ratio > 0.3:
        return "ocr-then-fallback-sections"
    if confidence < 0.6:
        return "page-level-figure-fallback"
    return "semantic-reflow"
