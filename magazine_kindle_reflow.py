from __future__ import annotations

import html as html_module
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

import fitz
from PIL import Image

from converter import ConversionConfig, _extract_pdf_metadata, strip_emails


POLISH_LETTERS = "A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż"
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+\b")
MULTISPACE_RE = re.compile(r"\s{2,}")
PAGE_NUMBER_RE = re.compile(r"^\d{1,4}$")
TOC_ENTRY_RE = re.compile(r"^\d{1,3}\s*/\s+.+")
HOUSE_AD_RE = re.compile(
    r"(?i)\b(?:subskrybuj|prenumerat|zamĂłw|zamow|kup teraz|wydanie specjalne|szukaj w salonach|w numerze m\.?\s*in\.?)\b"
)
INLINE_AD_LABEL_RE = re.compile(
    r"(?i)^(?:reklama|materiaĹ‚ sponsorowany|material sponsorowany|partner wydania|partner projektu)\b"
)
SPONSORED_TITLE_STOPWORDS = {
    "material",
    "sponsorowany",
    "sponsorowana",
    "sponsored",
    "partner",
    "partnerem",
    "fot",
    "foto",
    "materialy",
    "prasowe",
    "istock",
    "stock",
    "kino",
    "lezakach",
}
BYLINE_RE = re.compile(
    r"^(?:TEKST|ROZMAWIA|AUTOR|AUTORKA|OPRAC\.?|FOT\.?|ZDJĘCIA|ZDJECIA|FOTO|PRZEŁOŻYŁ|PRZELOZYL)\b",
    re.IGNORECASE,
)
PHOTO_CREDIT_RE = re.compile(r"^(?:FOT\.?|ZDJĘCIA|ZDJECIA|PHOTO)\b", re.IGNORECASE)
META_JUNK_RE = re.compile(
    r"(?i)\b(?:cena|vat|indeks|issn|numer|nr|wydanie|newsweek|polityka|forbes|wprost|press)\b"
)
PROMO_RE = re.compile(r"(?i)\b(?:prenumerata|zamów|zamow|subskrypcja|kup|oferta|promocja|reklama)\b")
SPONSORED_RE = re.compile(r"(?i)\b(?:partner|materiał sponsorowany|material sponsorowany|sponsorowany|advertorial|partnerstwo)\b")
SECTION_LABEL_RE = re.compile(rf"^[{POLISH_LETTERS}0-9/&.,:'’ -]{{3,50}}$")
SPACED_CAPS_RE = re.compile(rf"\b(?:[A-ZĄĆĘŁŃÓŚŹŻ]\s+){{2,}}[A-ZĄĆĘŁŃÓŚŹŻ]\b")
PLAIN_P_RE = re.compile(r"^<p(?P<attrs>[^>]*)>(?P<text>.*)</p>$", re.DOTALL)
TITLE_NAME_TAIL_RE = re.compile(
    rf"^(?P<title>.+?)\s+(?P<name>(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+){{1,2}}))$"
)
INTERVIEW_QA_RE = re.compile(rf"^(?:[A-ZĄĆĘŁŃÓŚŹŻ]{{1,4}}|[{POLISH_LETTERS}][{POLISH_LETTERS}\- ]{{2,25}}):")
TECHNICAL_TITLE_RE = re.compile(r"(?i)^(chapter[_ -]?\d+|section[_ -]?\d+|część\s+\d+|part\s+\d+)$")
BROKEN_TITLE_RE = re.compile(r"(?:\b[a-ząćęłńóśźż]{1,3}[A-ZĄĆĘŁŃÓŚŹŻ]{2,}\b|[ÿœ�])")
LEADING_NAME_RE = re.compile(
    rf"^(?P<name>(?:[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]+){{1,2}}))\s+(?P<title>[A-ZĄĆĘŁŃÓŚŹŻ0-9][^.,:;]{{4,}})$"
)


@dataclass
class MagazineBlock:
    kind: str
    text: str
    bbox: tuple[float, float, float, float]
    avg_font: float
    max_font: float
    page_number: int
    role: str = "body"
    image_name: str | None = None
    image_ext: str | None = None
    image_data: bytes | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class PageModel:
    page_index: int
    page_label: str | None
    width: float
    height: float
    blocks: list[MagazineBlock] = field(default_factory=list)
    title: str | None = None
    section_label: str | None = None
    is_cover: bool = False
    is_toc: bool = False
    is_ad_like: bool = False
    body_font: float = 10.5
    content_type: str = "article"
    title_quality: str = "missing"
    risk_flags: list[str] = field(default_factory=list)


def convert_magazine_to_kindle_reflow(pdf_path: str, config: ConversionConfig | None = None) -> dict:
    """Extract layout-heavy magazines into a Kindle-first reflowable structure."""
    if config is None:
        config = ConversionConfig()

    doc = fitz.open(pdf_path)
    pdf_metadata = _extract_pdf_metadata(pdf_path)

    pages = [_extract_page_model(doc, page_index, config) for page_index in range(len(doc))]
    toc_entries = _build_toc_entries(pages)
    toc_title_map = {entry["page_label"]: entry["title"] for entry in toc_entries if entry.get("page_label")}
    chapters = _group_pages_into_articles(pages, toc_title_map=toc_title_map)
    chapter_title_map = _resolve_chapter_titles(chapters, toc_entries=toc_entries, toc_title_map=toc_title_map)

    cover_image = _extract_cover_image(doc, config)
    images: list[dict] = [cover_image] if cover_image else []

    content_chapters: list[dict] = []
    for chapter_index, chapter_pages in enumerate(chapters, start=1):
        chapter_kind = _infer_chapter_kind(chapter_pages)
        if chapter_kind == "cover":
            continue

        chapter_title = chapter_title_map.get(chapter_index - 1)
        if not chapter_title:
            chapter_title = _pick_chapter_title(
                chapter_pages,
                fallback=f"Artykuł {chapter_index}",
                toc_title_map=toc_title_map,
                chapter_kind=chapter_kind,
            )
        html_parts: list[str] = []

        if all(page.is_toc for page in chapter_pages):
            chapter_title = "Spis treści"
            html_parts.extend(_render_structured_toc(toc_entries))
        elif chapter_kind in {"advertisement", "sponsored", "gallery"}:
            for page in chapter_pages:
                if page.page_label:
                    html_parts.append(
                        f'<span id="book-page-{html_module.escape(page.page_label)}" class="page-marker"></span>'
                    )
                page_html, page_images = _render_special_layout_page(page, doc, config, chapter_title, chapter_kind)
                html_parts.extend(page_html)
                images.extend(page_images)
        else:
            for page in chapter_pages:
                if page.page_label:
                    html_parts.append(
                        f'<span id="book-page-{html_module.escape(page.page_label)}" class="page-marker"></span>'
                    )
                if page.section_label:
                    html_parts.append(f'<p class="kicker">{html_module.escape(_clean_article_title(page.section_label))}</p>')
                if page.title:
                    cleaned_page_title = _clean_article_title(page.title)
                    page_title_quality = _classify_title_quality(cleaned_page_title)
                    if cleaned_page_title != chapter_title and page_title_quality == "strong":
                        html_parts.append(f'<h2>{html_module.escape(cleaned_page_title)}</h2>')

                page_html, page_images = _render_page_blocks(page, config, chapter_title)
                html_parts.extend(page_html)
                images.extend(page_images)

        html_parts = _polish_html_parts(html_parts)
        if html_parts:
            content_chapters.append(
                {
                    "title": chapter_title,
                    "html_parts": html_parts,
                    "images": [],
                    "_kind": chapter_kind,
                    "_audit": _build_chapter_audit(chapter_pages, chapter_title, chapter_kind, toc_title_map),
                }
            )

    doc.close()

    return {
        "success": True,
        "method": "magazine-kindle-reflow",
        "text_content": True,
        "layout_mode": "reflowable",
        "metadata": {
            **pdf_metadata,
            "inferred_publication_title": _infer_publication_title(pages, pdf_metadata),
        },
        "images": images,
        "chapters": content_chapters,
        "audit": _build_magazine_audit(pages, content_chapters),
    }


def _extract_page_model(doc: fitz.Document, page_index: int, config: ConversionConfig) -> PageModel:
    page = doc[page_index]
    page_dict = page.get_text("dict", sort=True)
    model = PageModel(
        page_index=page_index,
        page_label=None,
        width=page.rect.width,
        height=page.rect.height,
        is_cover=page_index == 0,
    )

    image_counter = 0
    text_blocks: list[MagazineBlock] = []
    image_blocks: list[MagazineBlock] = []

    for block in page_dict.get("blocks", []):
        block_type = block.get("type")
        bbox = tuple(block.get("bbox", (0, 0, 0, 0)))

        if block_type == 0:
            text, avg_font, max_font = _extract_text_block(block)
            text = _normalize_text(text)
            if not text or EMAIL_RE.search(text):
                continue

            if PAGE_NUMBER_RE.fullmatch(text):
                if y_near_margin(bbox, model.height):
                    model.page_label = text
                    continue

            if _is_probable_page_junk(text, bbox, model.width, model.height):
                page_digits = re.findall(r"\d{1,4}", text)
                if not model.page_label and len(page_digits) == 1 and y_near_margin(bbox, model.height):
                    model.page_label = page_digits[0]
                continue

            text_blocks.append(
                MagazineBlock(
                    kind="text",
                    text=text,
                    bbox=bbox,
                    avg_font=avg_font,
                    max_font=max_font,
                    page_number=page_index + 1,
                )
            )

        elif block_type == 1:
            image_block = _extract_image_block(block, page_index, image_counter, config, model.width, model.height)
            if image_block:
                image_counter += 1
                image_blocks.append(image_block)

    if not text_blocks and not image_blocks:
        fallback_page_image = _render_page_image_block(page, page_index, config)
        if fallback_page_image:
            image_blocks.append(fallback_page_image)

    model.blocks = sorted(text_blocks + image_blocks, key=lambda item: (item.y0, item.x0))
    _assign_page_roles(model)
    return model


def _render_page_image_block(page: fitz.Page, page_index: int, config: ConversionConfig) -> MagazineBlock | None:
    zoom = max(1.6, min(config.image_max_width / max(page.rect.width, 1.0), 2.2))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    data, media_ext = _optimize_image(pix.tobytes("png"), "png", config)
    filename = f"magazine_page_{page_index + 1:03d}.{media_ext}"
    return MagazineBlock(
        kind="image",
        text="",
        bbox=(0.0, 0.0, page.rect.width, page.rect.height),
        avg_font=0.0,
        max_font=0.0,
        page_number=page_index + 1,
        role="image",
        image_name=filename,
        image_ext=media_ext,
        image_data=data,
    )


def _extract_text_block(block: dict) -> tuple[str, float, float]:
    lines: list[str] = []
    font_sizes: list[float] = []

    for line in block.get("lines", []):
        fragments: list[str] = []
        for span in line.get("spans", []):
            text = strip_emails(span.get("text", ""))
            if text:
                fragments.append(text)
            font_sizes.append(float(span.get("size", 0.0)))
        line_text = "".join(fragments).strip()
        if line_text:
            lines.append(line_text)

    text = _join_lines(lines)
    avg_font = sum(font_sizes) / len(font_sizes) if font_sizes else 0.0
    max_font = max(font_sizes) if font_sizes else 0.0
    return text, avg_font, max_font


def _join_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    merged: list[str] = [lines[0].strip()]
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        previous = merged[-1]
        if previous.endswith("-") and line[:1].islower():
            merged[-1] = previous[:-1] + line
        elif previous.endswith(("/", "(", "“", "\"")):
            merged[-1] = previous + line
        else:
            merged[-1] = previous + " " + line
    return " ".join(merged)


def _normalize_text(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", strip_emails(text or ""))
    cleaned = "".join(ch for ch in cleaned if ch >= " " or ch in "\t")
    cleaned = cleaned.replace("\xa0", " ").replace("\u00ad", "")
    cleaned = cleaned.replace("ﬁ", "fi").replace("ﬂ", "fl")
    cleaned = SPACED_CAPS_RE.sub(lambda m: m.group(0).replace(" ", ""), cleaned)
    cleaned = re.sub(rf"^([A-ZĄĆĘŁŃÓŚŹŻ])\s+([a-ząćęłńóśźż]{{2,}})\b", r"\1\2", cleaned)
    cleaned = re.sub(r"(\d)\s+\.\s+(\d)", r"\1.\2", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.;:!?])([^\s])", r"\1 \2", cleaned)
    cleaned = re.sub(
        r"(?i)\b(?:www|https?)\.\s*[a-z0-9-]+(?:\.\s*[a-z0-9-]+)+",
        lambda m: m.group(0).replace(" ", ""),
        cleaned,
    )
    cleaned = MULTISPACE_RE.sub(" ", cleaned)
    cleaned = cleaned.strip(" |")
    return cleaned.strip()


def _editorial_text_blocks(text_blocks: list[MagazineBlock], model: PageModel) -> list[MagazineBlock]:
    editorial: list[MagazineBlock] = []
    for block in text_blocks:
        text = block.text.strip()
        if not text:
            continue
        if BYLINE_RE.match(text) or PHOTO_CREDIT_RE.match(text):
            continue
        if INLINE_AD_LABEL_RE.match(text):
            continue
        if URL_RE.search(text):
            continue
        if len(text.split()) < 6:
            continue
        if block.max_font > max(model.body_font * 2.2, model.body_font + 14.0):
            continue
        editorial.append(block)
    return editorial


def _has_substantial_editorial_text(text_blocks: list[MagazineBlock], model: PageModel) -> bool:
    editorial_blocks = _editorial_text_blocks(text_blocks, model)
    if not editorial_blocks:
        return False
    editorial_chars = sum(len(block.text) for block in editorial_blocks)
    long_blocks = sum(1 for block in editorial_blocks if len(block.text) >= 120 or len(block.text.split()) >= 20)
    full_width_blocks = sum(1 for block in editorial_blocks if block.width >= model.width * 0.22)
    return editorial_chars >= 360 or long_blocks >= 2 or full_width_blocks >= 3


def _promo_signal_count(text_blocks: list[MagazineBlock]) -> int:
    return sum(
        1
        for block in text_blocks
        if PROMO_RE.search(block.text) or HOUSE_AD_RE.search(block.text) or INLINE_AD_LABEL_RE.match(block.text)
    )


def _looks_like_house_ad_page(
    text_blocks: list[MagazineBlock],
    image_blocks: list[MagazineBlock],
    model: PageModel,
) -> bool:
    promo_blocks = sum(1 for block in text_blocks if HOUSE_AD_RE.search(block.text) or INLINE_AD_LABEL_RE.match(block.text))
    bulletish_blocks = sum(
        1
        for block in text_blocks
        if block.text.strip().startswith(("■", "•", "- ")) or "w numerze m" in block.text.lower()
    )
    large_display = any(block.max_font >= max(model.body_font * 2.0, model.body_font + 8.0) for block in text_blocks)
    if promo_blocks >= 2 and not _has_substantial_editorial_text(text_blocks, model):
        return True
    if promo_blocks >= 1 and bulletish_blocks >= 2 and large_display:
        return True
    if image_blocks and promo_blocks >= 1 and not _has_substantial_editorial_text(text_blocks, model):
        return True
    return False


def _assign_page_roles(model: PageModel) -> None:
    text_blocks = sorted(
        [block for block in model.blocks if block.kind == "text"],
        key=lambda item: (item.y0, item.x0),
    )
    image_blocks = [block for block in model.blocks if block.kind == "image"]
    if not text_blocks and image_blocks:
        model.is_ad_like = False
        model.content_type = "cover" if model.is_cover else "gallery"
        model.title_quality = "missing"
        if not model.is_cover:
            model.risk_flags.append("image-only-page")
        return

    font_candidates = [block.avg_font for block in text_blocks if 6.0 <= block.avg_font <= 14.0]
    model.body_font = median(font_candidates) if font_candidates else 10.5
    if model.is_cover:
        model.title = "Okładka"
        model.is_toc = False
        model.content_type = "cover"
        model.title_quality = "strong"
        for block in text_blocks:
            block.role = "body"
        return
    model.is_toc = _is_toc_page(text_blocks)

    if model.is_toc:
        model.title = "Spis treści"
        model.content_type = "contents"
        model.title_quality = "strong"
        for block in text_blocks:
            if _looks_like_section_label(block.text):
                block.role = "section"
            elif TOC_ENTRY_RE.match(block.text):
                block.role = "toc-entry"
            else:
                block.role = "body"
        return

    title_index = _pick_title_index(text_blocks, model)
    title_bottom = 0.0
    if title_index is not None:
        title_block = text_blocks[title_index]
        title_block.role = "title"
        title_fragments = [title_block.text]
        consumed_indices: list[int] = []
        for next_index in range(title_index + 1, len(text_blocks)):
            candidate = text_blocks[next_index]
            if candidate.kind != "text":
                continue
            if candidate.y0 > title_block.y1 + 56:
                break
            if len(candidate.text.strip()) < 2:
                continue
            if candidate.max_font < max(model.body_font + 2.0, model.body_font * 1.25):
                break
            if len(candidate.text.split()) > 12:
                break
            if BYLINE_RE.match(candidate.text) or PHOTO_CREDIT_RE.match(candidate.text):
                break
            title_fragments.append(candidate.text)
            consumed_indices.append(next_index)
            candidate.role = "title"
            title_block.bbox = (
                title_block.x0,
                title_block.y0,
                max(title_block.x1, candidate.x1),
                candidate.y1,
            )

        title_text, tail_byline = _split_title_and_byline(" ".join(title_fragments))
        title_text = _clean_article_title(title_text)
        title_block.text = title_text
        model.title = title_text
        title_bottom = title_block.y1

        for idx in range(title_index - 1, -1, -1):
            candidate = text_blocks[idx]
            if candidate.y1 < title_block.y0 - 80:
                break
            if _looks_like_section_label(candidate.text):
                candidate.role = "section"
                model.section_label = _clean_article_title(candidate.text)
                break

        if tail_byline:
            synthetic_byline = MagazineBlock(
                kind="text",
                text=tail_byline,
                bbox=(title_block.x0, title_block.y1 + 2, title_block.x1, title_block.y1 + 18),
                avg_font=max(model.body_font, title_block.avg_font * 0.55),
                max_font=max(model.body_font, title_block.max_font * 0.55),
                page_number=title_block.page_number,
                role="byline",
            )
            model.blocks.append(synthetic_byline)
            text_blocks.append(synthetic_byline)
            text_blocks.sort(key=lambda item: (item.y0, item.x0))

    meaningful_text_chars = 0
    for block in text_blocks:
        if block.role == "title":
            continue
        meaningful_text_chars += len(block.text)
        if BYLINE_RE.match(block.text):
            block.role = "byline"
            continue
        if title_bottom and block.y0 < title_bottom + 120:
            if block.avg_font >= model.body_font + 0.8 and 5 <= len(block.text.split()) <= 80:
                block.role = "lead"
                continue
        if _looks_like_pullquote(block, model):
            block.role = "pullquote"
            continue
        if _looks_like_aside(block, model):
            block.role = "aside"
            continue
        block.role = "body"

    if not model.title and meaningful_text_chars < 180 and len(image_blocks) >= 1 and not model.is_cover:
        if not _has_substantial_editorial_text(text_blocks, model):
            model.is_ad_like = True
    if not model.title and _promo_signal_count(text_blocks) >= 2:
        if not _has_substantial_editorial_text(text_blocks, model):
            model.is_ad_like = True
    if _looks_like_house_ad_page(text_blocks, image_blocks, model):
        model.is_ad_like = True
    model.content_type = _classify_page_content(model, text_blocks, image_blocks, meaningful_text_chars)
    model.title_quality = _classify_title_quality(model.title)
    if model.content_type in {"article", "interview", "contents"} and model.title_quality in {"technical", "broken", "missing", "weak"}:
        model.risk_flags.append(f"title-{model.title_quality}")
    elif model.title and model.title_quality in {"technical", "broken"}:
        model.risk_flags.append(f"title-{model.title_quality}")
    if len([block for block in text_blocks if block.role == "title"]) > 1:
        model.risk_flags.append("multiple-title-blocks")
    if model.content_type in {"article", "interview"} and not model.title:
        model.risk_flags.append("article-without-title")
    model.blocks = sorted(text_blocks + image_blocks, key=lambda item: (item.y0, item.x0))


def _is_toc_page(blocks: list[MagazineBlock]) -> bool:
    if not blocks:
        return False
    entry_count = sum(1 for block in blocks if TOC_ENTRY_RE.match(block.text))
    section_count = sum(1 for block in blocks if _looks_like_section_label(block.text))
    has_w_numerze = any("W NUMERZE" in block.text.upper() for block in blocks)
    return entry_count >= 6 or has_w_numerze or (entry_count >= 4 and section_count >= 2)


def _pick_title_index(blocks: list[MagazineBlock], model: PageModel) -> int | None:
    candidates: list[tuple[float, int]] = []
    body_font = model.body_font
    for idx, block in enumerate(blocks):
        text = block.text
        word_count = len(text.split())
        if not text or TOC_ENTRY_RE.match(text) or BYLINE_RE.match(text) or PHOTO_CREDIT_RE.match(text):
            continue
        if PAGE_NUMBER_RE.fullmatch(text) or URL_RE.fullmatch(text):
            continue
        if block.y0 > model.height * 0.42:
            continue
        if word_count < 1 or word_count > 22:
            continue
        if len(text.strip()) < 6:
            continue
        if sum(len(prev.text) for prev in blocks[:idx]) > 120 and not model.is_cover:
            continue
        if block.max_font < max(body_font + 3.0, body_font * 1.4):
            continue
        section_like = _looks_like_section_label(text)
        all_caps_short = text == text.upper() and word_count <= 5
        score = (block.max_font - body_font) * 7.0
        score += min(word_count, 12)
        score -= block.y0 / 35.0
        if block.width < model.width * 0.82:
            score += 3.0
        if block.x0 > model.width * 0.08 and block.x1 < model.width * 0.92:
            score += 2.0
        if section_like:
            score -= 10.0
        if all_caps_short:
            score -= 6.0
        if re.search(rf"[a-ząćęłńóśźż]", text):
            score += 4.0
        if word_count >= 2 and block.width >= model.width * 0.3:
            score += 2.0
        candidates.append((score, idx))

    if not candidates:
        return None
    best_score, best_idx = max(candidates, key=lambda item: item[0])
    return best_idx if best_score >= 12.0 else None


def _looks_like_section_label(text: str) -> bool:
    if not text or len(text) > 60:
        return False
    compact = text.strip()
    if TOC_ENTRY_RE.match(compact):
        return False
    if compact.lower() == compact:
        return False
    words = compact.split()
    if len(words) > 6:
        return False
    letters = re.sub(rf"[^{POLISH_LETTERS}]", "", compact)
    upper_ratio = sum(1 for char in letters if char.isupper()) / max(len(letters), 1)
    return upper_ratio >= 0.75 and bool(SECTION_LABEL_RE.match(compact))


def _looks_like_pullquote(block: MagazineBlock, model: PageModel) -> bool:
    words = len(block.text.split())
    if words < 4 or words > 45:
        return False
    return block.max_font >= max(model.body_font + 4.0, model.body_font * 1.55)


def _looks_like_aside(block: MagazineBlock, model: PageModel) -> bool:
    if len(block.text.split()) < 6:
        return False
    narrow = block.width <= model.width * 0.34
    edge = block.x0 < model.width * 0.12 or block.x1 > model.width * 0.88
    return narrow and edge and block.avg_font <= model.body_font + 0.4


def _is_probable_page_junk(
    text: str,
    bbox: tuple[float, float, float, float],
    page_width: float,
    page_height: float,
) -> bool:
    x0, y0, x1, y1 = bbox
    if EMAIL_RE.search(text):
        return True
    if PAGE_NUMBER_RE.fullmatch(text):
        return True
    near_margin = y_near_margin(bbox, page_height)
    if near_margin and (META_JUNK_RE.search(text) or URL_RE.fullmatch(text)):
        return True
    if near_margin and len(text) <= 32 and text.upper() == text:
        return True
    if PHOTO_CREDIT_RE.match(text) and (x1 - x0) <= page_width * 0.4:
        return True
    if y0 < page_height * 0.1 and len(text) <= 40 and re.search(r"\d{1,2}[-./ ]\d{1,2}", text):
        return True
    return False


def y_near_margin(bbox: tuple[float, float, float, float], page_height: float) -> bool:
    return bbox[1] < page_height * 0.06 or bbox[3] > page_height * 0.95


def _extract_image_block(
    block: dict,
    page_index: int,
    image_counter: int,
    config: ConversionConfig,
    page_width: float,
    page_height: float,
) -> MagazineBlock | None:
    bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    area_ratio = (width * height) / max(page_width * page_height, 1)

    if width < 90 or height < 90:
        return None
    if area_ratio < 0.018 or area_ratio > 0.82:
        return None

    raw = block.get("image")
    if not raw:
        return None

    ext = (block.get("ext") or "jpeg").lower()
    data, media_ext = _optimize_image(raw, ext, config)
    filename = f"magazine_p{page_index + 1:03d}_{image_counter + 1}.{media_ext}"

    return MagazineBlock(
        kind="image",
        text="",
        bbox=bbox,
        avg_font=0.0,
        max_font=0.0,
        page_number=page_index + 1,
        role="image",
        image_name=filename,
        image_ext=media_ext,
        image_data=data,
    )


def _optimize_image(raw: bytes, ext: str, config: ConversionConfig) -> tuple[bytes, str]:
    try:
        image = Image.open(io.BytesIO(raw))
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        width, height = image.size
        max_width = min(config.image_max_width, 1400)
        max_height = min(config.image_max_height, 1400)
        if width > max_width or height > max_height:
            image.thumbnail((max_width, max_height), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=max(config.image_quality, 78), optimize=True, progressive=True)
        return buffer.getvalue(), "jpeg"
    except Exception:
        fallback_ext = "png" if ext == "png" else "jpeg"
        return raw, fallback_ext


def _group_pages_into_articles(pages: list[PageModel], *, toc_title_map: dict[str, str] | None = None) -> list[list[PageModel]]:
    chapters: list[list[PageModel]] = []
    current: list[PageModel] = []
    pending: list[PageModel] = []
    toc_title_map = toc_title_map or {}

    for page in pages:
        if page.is_cover:
            if current:
                chapters.append(current)
                current = []
            if pending:
                chapters.append(pending)
                pending = []
            chapters.append([page])
            continue
        if page.is_toc:
            if current:
                chapters.append(current)
                current = []
            pending = []
            if chapters and all(item.is_toc for item in chapters[-1]):
                chapters[-1].append(page)
            else:
                chapters.append([page])
            continue

        starts_new = _page_starts_new_article(page, current, toc_title_map=toc_title_map)
        if starts_new and current:
            chapters.append(current)
            current = [page]
            pending = []
        elif starts_new:
            current = pending + [page]
            pending = []
        else:
            if current:
                current.append(page)
            else:
                pending.append(page)

    if current:
        chapters.append(current)
    elif pending:
        chapters.append(pending)

    return chapters


def _coalesce_fragile_chapters(chapters: list[list[PageModel]]) -> list[list[PageModel]]:
    merged: list[list[PageModel]] = []
    index = 0

    while index < len(chapters):
        chapter_pages = chapters[index]
        chapter_kind = _infer_chapter_kind(chapter_pages)

        if chapter_kind in {"article", "interview"} and _is_placeholder_chapter(chapter_pages):
            next_index = index + 1
            if next_index < len(chapters):
                next_pages = chapters[next_index]
                next_kind = _infer_chapter_kind(next_pages)
                if next_kind in {"article", "interview"} and _has_explicit_chapter_title(next_pages):
                    chapters[next_index] = chapter_pages + next_pages
                    index += 1
                    continue

            if merged:
                previous_pages = merged[-1]
                previous_kind = _infer_chapter_kind(previous_pages)
                if previous_kind in {"article", "interview"}:
                    merged[-1] = previous_pages + chapter_pages
                    index += 1
                    continue

        merged.append(chapter_pages)
        index += 1

    return merged


def _has_explicit_chapter_title(chapter_pages: list[PageModel]) -> bool:
    for page in chapter_pages:
        if page.title or page.section_label:
            return True
        if any(block.kind == "text" and block.role in {"title", "lead", "byline"} for block in page.blocks):
            return True
    return False


def _is_placeholder_chapter(chapter_pages: list[PageModel]) -> bool:
    if _has_explicit_chapter_title(chapter_pages):
        return False
    if len(chapter_pages) > 2:
        return False
    text_chars = sum(
        len(block.text)
        for page in chapter_pages
        for block in page.blocks
        if block.kind == "text"
    )
    return text_chars <= 2200

def _pick_chapter_title(
    chapter_pages: list[PageModel],
    fallback: str,
    *,
    toc_title_map: dict[str, str] | None = None,
    chapter_kind: str = "article",
) -> str:
    if chapter_pages and chapter_pages[0].is_cover:
        return "Okładka"
    if chapter_kind == "contents":
        return "Spis treści"
    if chapter_kind == "advertisement":
        return _label_special_section("Reklama", chapter_pages)
    if chapter_kind == "sponsored":
        return _label_special_section("Materiał sponsorowany", chapter_pages)
    if chapter_kind == "gallery":
        return _label_special_section("Galeria", chapter_pages)
    toc_title_map = toc_title_map or {}
    start_label = chapter_pages[0].page_label if chapter_pages else None
    mapped_title = toc_title_map.get(start_label or "")
    if mapped_title and _classify_title_quality(mapped_title) == "strong":
        return mapped_title
    for page in chapter_pages:
        if not page.title:
            continue
        cleaned_title = _clean_article_title(page.title)
        if page.section_label and _normalize_magazine_title(cleaned_title) == _normalize_magazine_title(page.section_label):
            continue
        title_quality = _classify_title_quality(cleaned_title)
        if title_quality in {"weak", "broken"}:
            sponsored_title = _synthesize_sponsored_feature_title(chapter_pages)
            if sponsored_title:
                return sponsored_title
        if title_quality not in {"technical", "missing"}:
            return cleaned_title
    if mapped_title and _classify_title_quality(mapped_title) == "strong":
        return mapped_title
    for page in chapter_pages:
        if page.section_label:
            return _clean_article_title(page.section_label)
    sponsored_title = _synthesize_sponsored_feature_title(chapter_pages)
    if sponsored_title:
        return sponsored_title
    return fallback


def _chapter_has_sponsored_marker(chapter_pages: list[PageModel]) -> bool:
    for page in chapter_pages[:2]:
        for block in page.blocks:
            if block.kind != "text":
                continue
            text = _normalize_text(block.text)
            if not text:
                continue
            if SPONSORED_RE.search(text) or HOUSE_AD_RE.search(text) or INLINE_AD_LABEL_RE.search(text):
                return True
    return False


def _synthesize_sponsored_feature_title(chapter_pages: list[PageModel]) -> str:
    snippets: list[str] = []
    for page in chapter_pages[:2]:
        for block in page.blocks:
            if block.kind != "text":
                continue
            text = _normalize_text(block.text)
            if not text:
                continue
            if EMAIL_RE.search(text) or PAGE_NUMBER_RE.fullmatch(text):
                continue
            snippets.append(text)
            if sum(len(item) for item in snippets) >= 2200:
                break
        if sum(len(item) for item in snippets) >= 2200:
            break

    combined = " ".join(snippets)
    if not combined:
        return ""

    sponsor_signal = _chapter_has_sponsored_marker(chapter_pages)

    phrase_re = re.compile(
        r"\b(?:[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż&+/-]{1,24})"
        r"(?:\s+(?:[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż&+/-]{1,24})){0,3}\b"
    )
    explicit_brand_re = re.compile(
        r"(?i)\b(?:marka|produkt|linia|seria)\s+"
        r"(?P<brand>(?:[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż&+/-]{1,24})"
        r"(?:\s+(?:[0-9A-ZĄĆĘŁŃÓŚŹŻ][0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż&+/-]{1,24})){0,3})"
    )

    explicit_brand = ""
    explicit_match = explicit_brand_re.search(combined)
    if explicit_match:
        explicit_brand = _normalize_text(explicit_match.group("brand")).strip(" -:;,")

    best_phrase = ""
    best_score = 0
    seen: set[str] = set()
    for match in phrase_re.finditer(combined):
        candidate = _normalize_text(match.group(0)).strip(" -:;,")
        if candidate in seen:
            continue
        seen.add(candidate)

        words = candidate.split()
        lowered = {word.lower() for word in words}
        if len(words) == 1 and not any(char.isdigit() for char in candidate):
            continue
        if len(words) > 4:
            continue
        if lowered <= SPONSORED_TITLE_STOPWORDS:
            continue
        if any(word.lower() in SPONSORED_TITLE_STOPWORDS for word in words) and not any(char.isdigit() for char in candidate):
            continue

        occurrences = combined.count(candidate)
        score = len(words) * 2 + min(occurrences, 3)
        if any(char.isdigit() for char in candidate):
            score += 4
        if len(candidate) >= 9:
            score += 1
        if candidate.upper() == candidate and not any(char.isdigit() for char in candidate):
            score -= 3
        if candidate.lower().startswith("jak "):
            score -= 2
        if score > best_score:
            best_phrase = candidate
            best_score = score

    if explicit_brand:
        return f"Material sponsorowany - {explicit_brand}"
    if best_phrase and best_score >= 5:
        repeated_brand = combined.count(best_phrase) >= 2
        if sponsor_signal or repeated_brand or any(char.isdigit() for char in best_phrase):
            return f"Material sponsorowany - {best_phrase}"
    if sponsor_signal:
        return "Material sponsorowany"
    return ""


def _build_toc_entries(pages: list[PageModel]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for page in pages:
        if not page.is_toc:
            continue
        current_section = ""
        for block in page.blocks:
            if block.role == "section":
                current_section = _clean_article_title(block.text)
                continue
            if block.role != "toc-entry":
                continue
            match = re.match(r"^(?P<page>\d{1,3})\s*/\s*(?P<title>.+)$", block.text)
            if not match:
                continue
            page_label = match.group("page").strip()
            title = _clean_article_title(match.group("title"))
            if page_label and title:
                entries.append({"page_label": page_label, "title": title, "section": current_section})
    return entries


def _build_toc_title_map(pages: list[PageModel]) -> dict[str, str]:
    return {entry["page_label"]: entry["title"] for entry in _build_toc_entries(pages)}


def _classify_page_content(
    model: PageModel,
    text_blocks: list[MagazineBlock],
    image_blocks: list[MagazineBlock],
    meaningful_text_chars: int,
) -> str:
    if model.is_cover:
        return "cover"
    if model.is_toc:
        return "contents"
    page_text = " ".join(block.text for block in text_blocks)
    substantial_editorial = _has_substantial_editorial_text(text_blocks, model)
    sponsored_hits = sum(
        1
        for pattern in (
            r"(?i)\bpartner",
            r"(?i)\bmarka\b",
            r"(?i)\bwspółpracy\b",
            r"(?i)\bpromocj",
            r"(?i)\bofercie\b",
            r"(?i)\bkampani",
            r"(?i)\bodbiorcza\b",
        )
        if re.search(pattern, page_text)
    )
    if _looks_like_house_ad_page(text_blocks, image_blocks, model):
        return "sponsored"
    if model.is_ad_like and not substantial_editorial:
        return "advertisement"
    if (SPONSORED_RE.search(page_text) or sponsored_hits >= 2) and not substantial_editorial:
        return "sponsored"
    if any(BYLINE_RE.match(block.text) and "ROZMAWIA" in block.text.upper() for block in text_blocks):
        return "interview"
    if any(INTERVIEW_QA_RE.match(block.text) for block in text_blocks[:6]):
        return "interview"
    if image_blocks and meaningful_text_chars < 260 and not substantial_editorial:
        return "gallery"
    return "article"


def _classify_title_quality(title: str | None) -> str:
    if not title:
        return "missing"
    normalized = _clean_article_title(title)
    if not normalized:
        return "missing"
    if normalized in {"Okładka", "Spis treści", "Reklama", "Galeria"}:
        return "strong"
    if (
        normalized.startswith("Reklama ")
        or normalized.startswith("Materiał sponsorowany")
        or normalized.startswith("Material sponsorowany")
        or normalized.startswith("Galeria ")
    ):
        return "strong"
    if re.fullmatch(r"(?i)(artykuł|sekcja|rozdział)\s+\d+", normalized):
        return "technical"
    if TECHNICAL_TITLE_RE.match(normalized):
        return "technical"
    if BROKEN_TITLE_RE.search(normalized):
        return "broken"
    weird_chars = re.findall(r"[^0-9A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż .,:'\"!?&()/+-]", normalized)
    if weird_chars:
        weird_ratio = len(weird_chars) / max(len(normalized), 1)
        if weird_ratio >= 0.04 or any(char in {"ÿ", "œ", "Ò", "Â", "Ã", "Ä", "Ĺ", "ď", "·", "�"} for char in weird_chars):
            return "broken"
    if len(normalized) < 8:
        return "weak"
    if re.search(r"\b(?:z|w|na|do|od|i|o)\s*$", normalized, re.IGNORECASE):
        return "weak"
    if re.fullmatch(r"[0-9A-Fa-f]{12,}", normalized):
        return "technical"
    if _looks_like_section_label(normalized):
        return "weak"
    return "strong"


def _page_starts_new_article(
    page: PageModel,
    current: list[PageModel],
    *,
    toc_title_map: dict[str, str],
) -> bool:
    if not current:
        return page.title is not None or page.page_label in toc_title_map or page.content_type in {"advertisement", "sponsored", "gallery", "interview"}
    previous = current[-1]
    if page.page_label and page.page_label in toc_title_map:
        return True
    if page.content_type != previous.content_type and page.content_type in {"advertisement", "sponsored", "gallery", "interview"}:
        return True
    if previous.content_type in {"advertisement", "sponsored", "gallery"}:
        return True
    if page.title and page.title != previous.title:
        return True
    if page.section_label and previous.section_label and page.section_label != previous.section_label and page.title:
        return True
    return False


def _infer_chapter_kind(chapter_pages: list[PageModel]) -> str:
    if chapter_pages and chapter_pages[0].is_cover:
        return "cover"
    kinds = {page.content_type for page in chapter_pages}
    if kinds == {"contents"}:
        return "contents"
    if "advertisement" in kinds and len(kinds) == 1:
        return "advertisement"
    if "sponsored" in kinds and len(kinds) == 1:
        return "sponsored"
    if "gallery" in kinds and len(kinds) == 1:
        return "gallery"
    if "interview" in kinds:
        return "interview"
    return "article"


def _label_special_section(prefix: str, chapter_pages: list[PageModel]) -> str:
    page_label = next((page.page_label for page in chapter_pages if page.page_label), None)
    return f"{prefix} {page_label}" if page_label else prefix


def _normalize_magazine_title(text: str) -> str:
    cleaned = _normalize_text(text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:")
    if not cleaned:
        return ""
    tokens = cleaned.split()
    if any(len(token) == 1 for token in tokens):
        letter_tokens = [token for token in tokens if re.search(rf"[{POLISH_LETTERS}]", token)]
        avg_token_len = sum(len(token) for token in tokens) / max(len(tokens), 1)
        if letter_tokens and all(token == token.upper() for token in letter_tokens) and avg_token_len <= 3.5:
            cleaned = "".join(tokens)
    if cleaned.isupper() and len(cleaned.split()) >= 2:
        cleaned = cleaned.title()
    return cleaned


def _ascii_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", _normalize_text(text or ""))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


def _clean_article_title(text: str) -> str:
    cleaned = _normalize_magazine_title(text)
    if not cleaned:
        return ""
    if cleaned.lower().startswith("material sponsorowany -") or cleaned.lower().startswith("materiał sponsorowany -"):
        return re.sub(r"\s+", " ", cleaned).strip(" -–—:")

    split_title, _ = _split_title_and_byline(cleaned)
    cleaned = split_title

    match = LEADING_NAME_RE.match(cleaned)
    if match:
        candidate = match.group("title").strip()
        if len(candidate.split()) >= 2:
            cleaned = candidate
    else:
        tokens = cleaned.split()
        if 4 <= len(tokens) <= 8:
            name_tokens = tokens[:2]
            title_tokens = tokens[2:]
            if (
                all(re.fullmatch(rf"[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż-]{{2,20}}", token) for token in name_tokens)
                and len(title_tokens) >= 2
                and any(token[:1].islower() for token in title_tokens[1:])
            ):
                cleaned = " ".join(title_tokens)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:")
    return cleaned


def _title_similarity(left: str, right: str) -> float:
    left_tokens = {
        token.lower()
        for token in re.findall(rf"[{POLISH_LETTERS}0-9]{{3,}}", _clean_article_title(left))
        if len(token) >= 3
    }
    right_tokens = {
        token.lower()
        for token in re.findall(rf"[{POLISH_LETTERS}0-9]{{3,}}", _clean_article_title(right))
        if len(token) >= 3
    }
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(min(len(left_tokens), len(right_tokens)), 1)


def _is_prefix_fragment(base_title: str, candidate_title: str) -> bool:
    base = _clean_article_title(base_title).lower()
    candidate = _clean_article_title(candidate_title).lower()
    if not base or not candidate:
        return False
    if len(base) < 6:
        return False
    return candidate.startswith(base) or base in candidate


def _chapter_title_context(chapter_pages: list[PageModel]) -> str:
    parts: list[str] = []
    for page in chapter_pages[:2]:
        if page.title:
            parts.append(_clean_article_title(page.title))
        if page.section_label:
            parts.append(_clean_article_title(page.section_label))
        for block in page.blocks:
            if block.kind != "text":
                continue
            if block.role in {"lead", "title"}:
                parts.append(_normalize_text(block.text))
            if len(parts) >= 4:
                break
    return " ".join(part for part in parts if part)


def _resolve_chapter_titles(
    chapters: list[list[PageModel]],
    *,
    toc_entries: list[dict[str, str]],
    toc_title_map: dict[str, str],
) -> dict[int, str]:
    resolved: dict[int, str] = {}
    used_toc_indices: set[int] = set()
    article_indices = [
        idx for idx, chapter_pages in enumerate(chapters) if _infer_chapter_kind(chapter_pages) in {"article", "interview"}
    ]

    for article_order, chapter_index in enumerate(article_indices):
        chapter_pages = chapters[chapter_index]
        chapter_kind = _infer_chapter_kind(chapter_pages)
        base_title = _pick_chapter_title(
            chapter_pages,
            fallback=f"Artykuł {article_order + 1}",
            toc_title_map=toc_title_map,
            chapter_kind=chapter_kind,
        )
        cleaned_base = _clean_article_title(base_title)
        start_label = chapter_pages[0].page_label if chapter_pages else None
        mapped_title = toc_title_map.get(start_label or "")
        if mapped_title and _classify_title_quality(mapped_title) == "strong":
            resolved[chapter_index] = mapped_title
            for toc_index, entry in enumerate(toc_entries):
                if entry.get("page_label") == start_label:
                    used_toc_indices.add(toc_index)
            continue

        base_quality = _classify_title_quality(cleaned_base)
        context = _chapter_title_context(chapter_pages)
        best_index = None
        best_score = 0.0
        for toc_index, entry in enumerate(toc_entries):
            if toc_index in used_toc_indices:
                continue
            candidate_title = entry.get("title", "")
            score = max(
                _title_similarity(cleaned_base, candidate_title),
                _title_similarity(context, candidate_title),
            )
            if entry.get("section") and any(
                _normalize_magazine_title(entry["section"]) == _normalize_magazine_title(page.section_label or "")
                for page in chapter_pages
            ):
                score += 0.2
            if score > best_score:
                best_score = score
                best_index = toc_index

        if best_index is not None:
            candidate_title = toc_entries[best_index]["title"]
            candidate_quality = _classify_title_quality(candidate_title)
            should_use_candidate = (
                base_quality in {"missing", "technical", "weak", "broken"}
                or cleaned_base.startswith("Artykuł ")
                or _title_needs_toc_help(cleaned_base)
            ) and best_score >= 0.22 and candidate_quality == "strong"
            if (
                not should_use_candidate
                and candidate_quality == "strong"
                and base_quality in {"weak", "broken"}
                and _is_prefix_fragment(cleaned_base, candidate_title)
            ):
                should_use_candidate = True
            if should_use_candidate:
                resolved[chapter_index] = candidate_title
                used_toc_indices.add(best_index)
                continue

        if base_quality in {"missing", "technical", "weak"}:
            previous_title = _neighbor_resolved_title(chapters, resolved, chapter_index, direction=-1)
            next_title = _neighbor_candidate_title(chapters, chapter_index, direction=1, toc_title_map=toc_title_map)
            if previous_title:
                resolved[chapter_index] = f"{previous_title} — ciąg dalszy"
                continue
            if next_title:
                resolved[chapter_index] = f"{next_title} — wprowadzenie"
                continue

        resolved[chapter_index] = cleaned_base

    return resolved


def _neighbor_resolved_title(
    chapters: list[list[PageModel]],
    resolved: dict[int, str],
    chapter_index: int,
    *,
    direction: int,
) -> str:
    probe = chapter_index + direction
    steps = 0
    while 0 <= probe < len(chapters) and steps < 3:
        kind = _infer_chapter_kind(chapters[probe])
        if kind in {"advertisement", "sponsored", "gallery", "contents", "cover"}:
            probe += direction
            steps += 1
            continue
        title = resolved.get(probe, "")
        if title and _classify_title_quality(title) == "strong":
            return title
        break
    return ""


def _neighbor_candidate_title(
    chapters: list[list[PageModel]],
    chapter_index: int,
    *,
    direction: int,
    toc_title_map: dict[str, str],
) -> str:
    probe = chapter_index + direction
    steps = 0
    while 0 <= probe < len(chapters) and steps < 3:
        kind = _infer_chapter_kind(chapters[probe])
        if kind in {"advertisement", "sponsored", "gallery", "contents", "cover"}:
            probe += direction
            steps += 1
            continue
        title = _pick_chapter_title(
            chapters[probe],
            fallback="",
            toc_title_map=toc_title_map,
            chapter_kind=kind,
        )
        if title and _classify_title_quality(title) == "strong":
            return title
        break
    return ""


def _build_chapter_audit(
    chapter_pages: list[PageModel],
    chapter_title: str,
    chapter_kind: str,
    toc_title_map: dict[str, str],
) -> dict:
    page_titles = [page.title for page in chapter_pages if page.title]
    toc_starts = [page.page_label for page in chapter_pages if page.page_label and page.page_label in toc_title_map]
    risk_flags: list[str] = []
    relevant_types = {page.content_type for page in chapter_pages if page.content_type not in {"cover", "contents"}}
    if len(toc_starts) > 1:
        risk_flags.append("multiple-toc-start-pages")
    if len({title for title in page_titles if title}) > 1 and chapter_kind == "article":
        risk_flags.append("multiple-page-titles")
    if chapter_kind in {"article", "interview", "contents"} and _classify_title_quality(chapter_title) in {"technical", "broken", "missing", "weak"}:
        risk_flags.append("weak-chapter-title")
    if len(relevant_types - {"article", "interview"}) > 1 or (
        "article" in relevant_types and ("advertisement" in relevant_types or "sponsored" in relevant_types)
    ):
        risk_flags.append("mixed-page-types")
    risk_score = _score_risk_flags(risk_flags)
    return {
        "kind": chapter_kind,
        "page_range": [chapter_pages[0].page_index + 1, chapter_pages[-1].page_index + 1],
        "page_labels": [page.page_label for page in chapter_pages if page.page_label],
        "page_titles": page_titles[:8],
        "risk_flags": risk_flags,
        "risk_score": risk_score,
    }


def _build_magazine_audit(pages: list[PageModel], content_chapters: list[dict]) -> dict:
    chapter_rows = []
    page_to_chapter: dict[int, dict] = {}
    for chapter in content_chapters:
        audit = dict(chapter.get("_audit") or {})
        audit["title"] = chapter.get("title", "")
        chapter_rows.append(audit)
        start, end = audit.get("page_range", [0, 0])
        for page_num in range(start, end + 1):
            page_to_chapter[page_num] = audit

    page_rows = []
    for page in pages:
        chapter_audit = page_to_chapter.get(page.page_index + 1, {})
        page_risk_score = _score_page_risk(page, chapter_audit)
        page_rows.append(
            {
                "page_index": page.page_index + 1,
                "page_label": page.page_label,
                "title": page.title,
                "title_quality": page.title_quality,
                "content_type": page.content_type,
                "risk_flags": page.risk_flags,
                "risk_score": page_risk_score,
            }
        )
    return {
        "page_map": page_rows,
        "chapters": chapter_rows,
        "high_risk_chapters": [row for row in chapter_rows if row.get("risk_score", 0) >= 2],
        "high_risk_pages": [row for row in page_rows if row.get("risk_score", 0) >= 3],
    }


def _score_risk_flags(flags: list[str]) -> int:
    weights = {
        "weak-chapter-title": 2,
        "multiple-page-titles": 2,
        "mixed-page-types": 3,
        "multiple-toc-start-pages": 2,
        "title-missing": 2,
        "title-broken": 2,
        "title-technical": 2,
        "article-without-title": 2,
        "multiple-title-blocks": 1,
        "image-only-page": 1,
    }
    return sum(weights.get(flag, 1) for flag in flags)


def _score_page_risk(page: PageModel, chapter_audit: dict) -> int:
    score = _score_risk_flags(page.risk_flags)
    if not score:
        return 0

    page_range = chapter_audit.get("page_range", [page.page_index + 1, page.page_index + 1])
    chapter_title = chapter_audit.get("title", "") or ""
    chapter_len = max(page_range[1] - page_range[0] + 1, 1)
    chapter_title_quality = _classify_title_quality(chapter_title)
    only_continuation_flags = set(page.risk_flags).issubset({"title-missing", "article-without-title"})
    if only_continuation_flags:
        chapter_key = _ascii_key(chapter_title)
        if chapter_title_quality == "strong" or chapter_len > 1 or "ciag dalszy" in chapter_key or "wprowadzenie" in chapter_key:
            return 1
    if page.content_type in {"gallery", "advertisement", "sponsored"} and only_continuation_flags:
        return 1
    return score


def _infer_publication_title(pages: list[PageModel], pdf_metadata: dict) -> str:
    metadata_title = (pdf_metadata.get("title") or "").strip()
    if metadata_title and not re.fullmatch(r"[0-9A-Fa-f]{12,}", metadata_title):
        return metadata_title

    counter: Counter[str] = Counter()
    for page in pages[:4]:
        for block in page.blocks:
            if block.kind != "text":
                continue
            text = _normalize_text(block.text)
            for token in re.findall(r"[A-ZĄĆĘŁŃÓŚŹŻ]{4,}", text):
                if token.lower() in {"wprost", "forbes"}:
                    counter[token.title()] += 2
                else:
                    counter[token.title()] += 1
            for match in re.findall(r"(?i)\bwww\.([a-z0-9-]+)\.", text):
                counter[match.title()] += 3
    best = counter.most_common(1)
    return best[0][0] if best else (metadata_title or "Magazyn")


def _render_toc_page(page: PageModel) -> list[str]:
    rendered: list[str] = []
    for block in sorted([item for item in page.blocks if item.kind == "text"], key=lambda item: (item.y0, item.x0)):
        if block.role == "section":
            rendered.append(f'<p class="kicker">{html_module.escape(_title_case_if_needed(block.text))}</p>')
        elif block.role == "toc-entry":
            rendered.append(f'<p class="toc-entry">{html_module.escape(_format_toc_entry(block.text))}</p>')
        elif block.text and not _looks_like_section_label(block.text):
            rendered.append(f'<p>{html_module.escape(block.text)}</p>')
    return rendered


def _render_structured_toc(toc_entries: list[dict[str, str]]) -> list[str]:
    rendered: list[str] = []
    current_section = ""
    for entry in toc_entries:
        section = _clean_toc_section(entry.get("section", ""))
        if section and section != current_section:
            rendered.append(f"<h2>{html_module.escape(section)}</h2>")
            current_section = section
        title = _clean_article_title(entry.get("title", ""))
        page_label = entry.get("page_label", "")
        if not title:
            continue
        line = f"{page_label}. {title}" if page_label else title
        rendered.append(f'<p class="toc-entry">{html_module.escape(line)}</p>')
    return rendered


def _clean_toc_section(text: str) -> str:
    section = _clean_article_title(text)
    section = re.sub(r"^\d+\s+", "", section).strip()
    if len(section.split()) > 4:
        return ""
    if _classify_title_quality(section) in {"missing", "technical", "broken"}:
        return ""
    return section


def _format_toc_entry(text: str) -> str:
    match = re.match(r"^(?P<page>\d{1,3})\s*/\s*(?P<title>.+)$", text)
    if not match:
        return _clean_article_title(text)
    return f'{match.group("page")}. {_clean_article_title(match.group("title"))}'


def _collect_toc_titles(pages: list[PageModel]) -> list[str]:
    titles: list[str] = []
    for page in pages:
        if not page.is_toc:
            continue
        for block in page.blocks:
            if block.role != "toc-entry":
                continue
            match = re.match(r"^\d{1,3}\s*/\s*(.+)$", block.text)
            if match:
                titles.append(match.group(1).strip())
    return titles


def _title_needs_toc_help(title: str) -> bool:
    if not title:
        return True
    stripped = title.strip()
    if len(stripped) < 12:
        return True
    if re.search(r"\b(?:z|w|na|do|od|i|o)\s*$", stripped, re.IGNORECASE):
        return True
    if re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]{8,}", stripped):
        return True
    if re.search(rf"[{POLISH_LETTERS}]{{16,}}", stripped):
        return True
    if any(len(token) >= 12 for token in stripped.split()):
        return True
    letters = re.sub(rf"[^{POLISH_LETTERS}]", "", stripped)
    if letters and letters.upper() == letters and len(stripped.split()) >= 3:
        return True
    return False


def _split_title_and_byline(text: str) -> tuple[str, str | None]:
    stripped = text.strip()
    if "|" in stripped:
        left, right = [part.strip() for part in stripped.split("|", 1)]
        if right and re.fullmatch(
            rf"(?:[A-ZÄ„Ä†ÄĹĹĂ“ĹšĹąĹ»][a-zÄ…Ä‡Ä™Ĺ‚Ĺ„ĂłĹ›ĹşĹĽ-]+(?:\s+[A-ZÄ„Ä†ÄĹĹĂ“ĹšĹąĹ»][a-zÄ…Ä‡Ä™Ĺ‚Ĺ„ĂłĹ›ĹşĹĽ-]+){{1,2}})", left
        ):
            return right, left
    match = TITLE_NAME_TAIL_RE.match(stripped)
    if not match:
        return text, None
    title = match.group("title").strip()
    name = match.group("name").strip()
    if ((len(title.split()) < 2 and len(title) < 10) or len(name.split()) > 3):
        return text, None
    if title.endswith((".", "!", "?", ":")):
        return text, None
    if re.search(r"\b(?:z|w|na|do|od|o|i|u|we|ze)\s*$", title, re.IGNORECASE):
        return text, None
    return title, name


def _title_case_if_needed(text: str) -> str:
    return text.title() if text.isupper() else text


def _render_page_blocks(page: PageModel, config: ConversionConfig, chapter_title: str) -> tuple[list[str], list[dict]]:
    html_parts: list[str] = []
    image_items: list[dict] = []

    text_blocks = [block for block in page.blocks if block.kind == "text" and block.role not in {"title", "section", "toc-entry"}]
    text_blocks = _strip_embedded_page_title(text_blocks, page.title, chapter_title)
    image_blocks = [block for block in page.blocks if block.kind == "image"]

    header_blocks = [block for block in text_blocks if block.role in {"lead", "byline"}]
    stream_blocks = [block for block in text_blocks if block.role in {"body", "pullquote", "aside"}] + image_blocks

    for block in sorted(header_blocks, key=lambda item: (item.y0, item.x0)):
        html_parts.append(_block_to_html(block))

    left_blocks = sorted(
        [block for block in stream_blocks if _block_column(block, page.width) == "left"],
        key=lambda item: (item.y0, item.x0),
    )
    right_blocks = sorted(
        [block for block in stream_blocks if _block_column(block, page.width) == "right"],
        key=lambda item: (item.y0, item.x0),
    )
    full_blocks = sorted(
        [block for block in stream_blocks if _block_column(block, page.width) == "full"],
        key=lambda item: (item.y0, item.x0),
    )

    left_idx = 0
    right_idx = 0
    prev_y = min((block.y0 for block in header_blocks), default=0.0)

    for item in full_blocks:
        left_idx = _emit_column_range(html_parts, image_items, left_blocks, left_idx, prev_y, item.y0, chapter_title)
        right_idx = _emit_column_range(html_parts, image_items, right_blocks, right_idx, prev_y, item.y0, chapter_title)
        _append_stream_item(html_parts, image_items, item, chapter_title)
        prev_y = item.y1

    _emit_column_range(html_parts, image_items, left_blocks, left_idx, prev_y, float("inf"), chapter_title)
    _emit_column_range(html_parts, image_items, right_blocks, right_idx, prev_y, float("inf"), chapter_title)

    return _merge_loose_paragraphs(_polish_html_parts(html_parts)), image_items


def _strip_embedded_page_title(
    blocks: list[MagazineBlock],
    page_title: str | None,
    chapter_title: str,
) -> list[MagazineBlock]:
    if not page_title or not blocks:
        return blocks
    cleaned_page_title = _clean_article_title(page_title)
    if not cleaned_page_title:
        return blocks
    if _normalize_magazine_title(cleaned_page_title) == _normalize_magazine_title(chapter_title):
        return blocks

    def normalize_key(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", _normalize_text(value or ""))
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()

    stripped_blocks: list[MagazineBlock] = []
    title_key = normalize_key(cleaned_page_title)
    for index, block in enumerate(blocks):
        if index > 1 or block.kind != "text":
            stripped_blocks.append(block)
            continue
        normalized_text = _normalize_text(block.text)
        if not normalized_text:
            stripped_blocks.append(block)
            continue
        block_key = normalize_key(normalized_text)
        if block_key == title_key:
            continue
        if block_key.startswith(title_key):
            trimmed = normalized_text[len(cleaned_page_title):].lstrip(" -:;,.")
            if trimmed:
                stripped_blocks.append(
                    MagazineBlock(
                        kind=block.kind,
                        text=trimmed,
                        bbox=block.bbox,
                        avg_font=block.avg_font,
                        max_font=block.max_font,
                        page_number=block.page_number,
                        role=block.role,
                        image_name=block.image_name,
                        image_ext=block.image_ext,
                        image_data=block.image_data,
                    )
                )
            continue
        stripped_blocks.append(block)
    return stripped_blocks


def _render_special_layout_page(
    page: PageModel,
    doc: fitz.Document,
    config: ConversionConfig,
    chapter_title: str,
    chapter_kind: str,
) -> tuple[list[str], list[dict]]:
    page_obj = doc[page.page_index]
    pix = page_obj.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    ext = "png"
    data, ext = _optimize_image(pix.tobytes("png"), ext, config)
    filename = f"special_p{page.page_index + 1}.{ext}"
    alt_label = {
        "advertisement": "Reklama",
        "sponsored": "Materiał sponsorowany",
        "gallery": "Galeria",
    }.get(chapter_kind, chapter_title or "Strona specjalna")
    figure_class = "figure magazine-special"
    html_parts = [
        f'<figure class="{figure_class}"><img src="images/{filename}" alt="{html_module.escape(alt_label)}"/></figure>'
    ]
    if chapter_kind == "sponsored" and chapter_title:
        html_parts.insert(0, f'<p class="kicker">Materiał sponsorowany</p>')
    return html_parts, [{"filename": filename, "extension": ext, "data": data}]


def _emit_column_range(
    html_parts: list[str],
    image_items: list[dict],
    blocks: list[MagazineBlock],
    start: int,
    min_y: float,
    max_y: float,
    chapter_title: str,
) -> int:
    index = start
    while index < len(blocks):
        block = blocks[index]
        if block.y0 < min_y:
            index += 1
            continue
        if block.y0 >= max_y:
            break
        _append_stream_item(html_parts, image_items, block, chapter_title)
        index += 1
    return index


def _append_stream_item(
    html_parts: list[str],
    image_items: list[dict],
    block: MagazineBlock,
    chapter_title: str,
) -> None:
    if block.kind == "image" and block.image_name and block.image_data:
        alt = f'Zdjęcie do artykułu "{chapter_title}"'
        html_parts.append(
            f'<figure class="figure magazine-figure"><img src="images/{block.image_name}" alt="{html_module.escape(alt)}"/></figure>'
        )
        image_items.append(
            {
                "filename": block.image_name,
                "extension": block.image_ext or "jpeg",
                "data": block.image_data,
            }
        )
        return
    html_parts.append(_block_to_html(block))


def _block_column(block: MagazineBlock, page_width: float) -> str:
    if block.kind == "image":
        return "full" if block.width >= page_width * 0.4 else ("left" if (block.x0 + block.x1) / 2 < page_width / 2 else "right")
    if block.width >= page_width * 0.58 or block.role in {"pullquote", "aside"}:
        return "full"
    center = (block.x0 + block.x1) / 2
    return "left" if center < page_width / 2 else "right"


def _block_to_html(block: MagazineBlock) -> str:
    safe = html_module.escape(block.text)
    if block.role == "lead":
        return f'<p class="lead">{safe}</p>'
    if block.role == "byline":
        return f'<p class="byline">{safe}</p>'
    if block.role == "aside":
        return f'<p class="aside sidebar">{safe}</p>'
    if block.role == "pullquote":
        return f'<blockquote class="quote"><p>{safe}</p></blockquote>'
    return f"<p>{safe}</p>"


def _merge_loose_paragraphs(parts: list[str]) -> list[str]:
    merged: list[str] = []
    for part in parts:
        if not merged:
            merged.append(part)
            continue

        previous_text = _extract_plain_p_text(merged[-1])
        current_text = _extract_plain_p_text(part)
        if previous_text is None or current_text is None:
            merged.append(part)
            continue

        if _should_join_paragraphs(previous_text, current_text):
            merged[-1] = f"<p>{html_module.escape(_join_paragraph_text(previous_text, current_text))}</p>"
            continue
        merged.append(part)

    return merged


def _extract_plain_p_text(fragment: str) -> str | None:
    match = PLAIN_P_RE.match(fragment.strip())
    if not match:
        return None
    return html_module.unescape(match.group("text")).strip()


def _should_join_paragraphs(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    if previous.endswith((".", "!", "?", ":", ";", "”", "\"")):
        return False
    return current[:1].islower()


def _join_paragraph_text(previous: str, current: str) -> str:
    if previous.endswith("-") and current[:1].islower():
        return previous[:-1] + current
    return f"{previous} {current}"


def _polish_html_parts(parts: list[str]) -> list[str]:
    cleaned: list[str] = []
    for part in parts:
        text = _extract_plain_p_text(part)
        if text is None:
            cleaned.append(part)
            continue
        polished = _normalize_text(text)
        if not polished or _is_branding_junk(polished):
            continue
        if cleaned:
            previous_text = _extract_plain_p_text(cleaned[-1])
            if previous_text and _is_duplicate_paragraph(previous_text, polished):
                continue
        cleaned.append(f"<p>{html_module.escape(polished)}</p>" if part.startswith("<p") and "class=" not in part else part.replace(html_module.escape(text), html_module.escape(polished)))
    return cleaned


def _is_duplicate_paragraph(previous: str, current: str) -> bool:
    norm_prev = re.sub(r"\W+", "", previous).lower()
    norm_curr = re.sub(r"\W+", "", current).lower()
    return bool(norm_prev and norm_prev == norm_curr)


def _is_branding_junk(text: str) -> bool:
    letters = re.sub(r"[^A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]", "", text)
    if not letters:
        return False
    if URL_RE.fullmatch(text):
        return False
    return letters.upper() in {"NEWSWEEK", "POLITYKA", "FORBES", "WPROST"}


def _extract_cover_image(doc: fitz.Document, config: ConversionConfig) -> dict | None:
    if not len(doc):
        return None
    first_page = doc[0]
    page_dict = first_page.get_text("dict")
    for block in page_dict.get("blocks", []):
        if block.get("type") != 1:
            continue
        raw = block.get("image")
        if not raw:
            continue
        data, ext = _optimize_image(raw, (block.get("ext") or "jpeg").lower(), config)
        return {
            "filename": f"cover_source.{ext}",
            "extension": ext,
            "data": data,
        }
    pix = first_page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
    data, ext = _optimize_image(pix.tobytes("png"), "png", config)
    return {
        "filename": f"cover_source.{ext}",
        "extension": ext,
        "data": data,
    }
