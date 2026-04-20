"""
KindleMaster — PDF to EPUB Converter
=====================================
Production-grade PDF to EPUB conversion with maximum visual fidelity.

Stack:
- PyMuPDF: PDF analysis, text/image extraction with positioning
- pdf2htmlEX: 1:1 visual layout preservation (HTML output)
- ebooklib: EPUB3 construction
- BeautifulSoup: HTML parsing and cleanup
- Pillow: Image processing and optimization

Pipeline:
1. Detect PDF type (text-based, scanned, mixed)
2. Choose conversion strategy based on PDF type
3. Extract content with layout preservation
4. Build EPUB3 with proper structure
5. Validate and optimize output
"""

import os
import re
import uuid
import shutil
import tempfile
import subprocess
import html as html_module
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF
from ebooklib import epub
from bs4 import BeautifulSoup
from PIL import Image

# OCR module import (optional - graceful fallback if not available)
try:
    from ocr_module import run_ocr_on_pdf, OCRResult, install_ocr_instructions
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("Warning: OCR module not available. Scanned PDFs will have reduced quality.")

EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
MAILTO_PATTERN = re.compile(r"(?i)mailto:\s*[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}")
HTML_PARAGRAPH_RE = re.compile(r"^<p(?P<attrs>[^>]*)>(?P<text>.*)</p>$", re.DOTALL)
HTML_TAG_RE = re.compile(r"<[^>]+>")
SOLUTION_PAGE_RE = re.compile(r"Solutions page (\d+)", re.IGNORECASE)
EXERCISE_NUMBER_RE = re.compile(r'exercise-number">(?P<num>\d+)\.</span>')
SOLUTION_ENTRY_RE = re.compile(r"^(?P<num>\d+)\.\s+.+\s[–-]\s.+$")


def strip_emails(text: str) -> str:
    """Remove email addresses from extracted text and collapse leftover gaps."""
    if not text:
        return ""
    cleaned = MAILTO_PATTERN.sub("", text)
    cleaned = EMAIL_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def extract_plain_paragraph(fragment: str) -> tuple[Optional[str], Optional[str]]:
    """Return paragraph attrs and plain text for simple <p> fragments."""
    match = HTML_PARAGRAPH_RE.match((fragment or "").strip())
    if not match:
        return None, None
    attrs = match.group("attrs") or ""
    text = html_module.unescape(HTML_TAG_RE.sub("", match.group("text"))).strip()
    return attrs, text


def detect_source_page_label(parts: list[str]) -> Optional[str]:
    """Pick the leading printed page number from chapter fragments."""
    for part in parts[:6]:
        _, text = extract_plain_paragraph(part)
        if not text:
            continue
        if text.isdigit() and 1 <= len(text) <= 4:
            return text
        break
    return None


def maybe_link_solution_reference(fragment: str, page_label_map: dict[str, str]) -> str:
    """Turn 'Solutions page N' text into an internal EPUB link when possible."""
    attrs, text = extract_plain_paragraph(fragment)
    if attrs is None or not text:
        return fragment

    match = SOLUTION_PAGE_RE.search(text)
    if not match:
        return fragment

    page_label = match.group(1)
    target = page_label_map.get(page_label)
    if not target:
        return fragment

    safe_text = html_module.escape(text)
    return f'<p{attrs}><a href="{target}#book-page-{page_label}">{safe_text}</a></p>'


def extract_problem_exercise_number(fragment: str) -> Optional[str]:
    """Return the exercise number stored in a rendered chess-problem caption."""
    match = EXERCISE_NUMBER_RE.search(fragment or "")
    if not match:
        return None
    return match.group("num")


def add_problem_anchor(fragment: str) -> str:
    """Attach an anchor id to chess problem blocks so solution pages can link back."""
    exercise_num = extract_problem_exercise_number(fragment)
    if not exercise_num or f'id="exercise-{exercise_num}"' in fragment:
        return fragment
    return fragment.replace(
        '<div class="chess-problem">',
        f'<div class="chess-problem" id="exercise-{exercise_num}">',
        1,
    )


def extract_standalone_exercise_number(fragment: str) -> Optional[str]:
    """Return a plain numeric paragraph that likely labels a nearby exercise."""
    _, text = extract_plain_paragraph(fragment)
    if not text or not text.isdigit() or not (1 <= len(text) <= 4):
        return None
    return text


def add_standalone_problem_anchor(fragment: str, exercise_num: str) -> str:
    """Anchor standalone exercise-number paragraphs when captions lost the number."""
    if f'id="exercise-{exercise_num}"' in fragment:
        return fragment
    return fragment.replace("<p", f'<p id="exercise-{exercise_num}"', 1)


def maybe_link_problem_reference(fragment: str, exercise_target_map: dict[str, str]) -> str:
    """Turn a solution header like '74. ...' into a backlink to the exercise."""
    attrs, text = extract_plain_paragraph(fragment)
    if attrs is None or not text:
        return fragment

    match = SOLUTION_ENTRY_RE.match(text)
    if not match:
        return fragment

    exercise_num = match.group("num")
    target = exercise_target_map.get(exercise_num)
    if not target:
        return fragment

    safe_text = html_module.escape(text)
    return f'<p{attrs}><a class="solution-backlink" href="{target}">{safe_text}</a></p>'


def finalize_epub_bytes(
    epub_bytes: bytes,
    config: "ConversionConfig",
    pdf_metadata: dict,
    original_filename: str,
    *,
    publication_profile: str | None = None,
    return_details: bool = False,
) -> bytes | tuple[bytes, dict]:
    """Run final Kindle-friendly cleanup on reflowable EPUB output."""
    title = (pdf_metadata or {}).get("title") or Path(original_filename).stem
    author = (pdf_metadata or {}).get("author") or "Unknown"
    text_cleanup_summary = {
        "auto_fix_count": 0,
        "review_needed_count": 0,
        "blocked_count": 0,
        "unknown_term_count": 0,
        "publish_blocked": False,
        "release_gate": "soft",
        "epubcheck_status": "unavailable",
        "status": "unavailable",
    }

    try:
        from text_normalization import TextCleanupConfig, clean_epub_text_package

        cleanup_result = clean_epub_text_package(
            epub_bytes,
            config=TextCleanupConfig(
                language_hint=config.language,
                domain_dictionary_path=config.text_cleanup_domain_dictionary_path,
                safe_threshold=config.text_cleanup_safe_threshold,
                review_threshold=config.text_cleanup_review_threshold,
                enable_pyphen=config.enable_pyphen_cleanup,
                emit_text_diff=config.text_cleanup_emit_diff,
                release_gate="soft",
            ),
            publication_profile=publication_profile,
        )
        epub_bytes = cleanup_result.epub_bytes
        text_cleanup_summary = {
            **cleanup_result.summary,
            "status": cleanup_result.summary.get("epubcheck_status", "unavailable"),
            "epubcheck": cleanup_result.epubcheck,
            "unknown_terms": cleanup_result.unknown_terms[:25],
            "report_available": bool(cleanup_result.markdown_report),
            "chapter_diff_count": len(cleanup_result.chapter_diffs),
        }
    except Exception as exc:
        print(f"Warning: EPUB text normalization failed: {exc}")
        text_cleanup_summary = {
            **text_cleanup_summary,
            "status": "failed",
            "warnings": [f"Text cleanup failed: {exc}"],
        }

    try:
        from kindle_semantic_cleanup import finalize_epub_for_kindle

        epub_bytes, semantic_reference_cleanup = finalize_epub_for_kindle(
            epub_bytes,
            title=title,
            author=author,
            language=config.language,
            publication_profile=publication_profile,
            return_report=True,
        )
    except Exception as exc:
        print(f"Warning: Kindle semantic cleanup failed: {exc}")
        semantic_reference_cleanup = {}

    try:
        from epub_reference_repair import repair_epub_reference_sections

        reference_repair_result = repair_epub_reference_sections(
            epub_bytes,
            language_hint=config.language,
        )
        epub_bytes = reference_repair_result.epub_bytes
        reference_cleanup_summary = {
            **reference_repair_result.summary,
            "semantic_prepass": semantic_reference_cleanup,
        }
        text_cleanup_summary = {
            **text_cleanup_summary,
            "reference_cleanup": reference_cleanup_summary,
        }
    except Exception as exc:
        print(f"Warning: EPUB reference repair failed: {exc}")
        text_cleanup_summary = {
            **text_cleanup_summary,
            "reference_cleanup": semantic_reference_cleanup,
        }

    if return_details:
        return epub_bytes, text_cleanup_summary
    return epub_bytes


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class ConversionConfig:
    """Configuration for PDF to EPUB conversion."""
    # Output preferences
    prefer_fixed_layout: bool = True  # True for 1:1 visual fidelity
    profile: str = "auto-premium"
    premium_mode: bool = True
    preserve_colors: bool = True
    preserve_fonts: bool = True
    
    # Image optimization (CRITICAL for file size!)
    image_quality: int = 80  # JPEG quality 0-100 (80 = good quality, smaller size)
    image_max_width: int = 1200  # Cap image size (smaller = smaller EPUB)
    image_max_height: int = 1200
    chess_diagram_dpi: int = 140  # Balanced for crisp boards with smaller EPUB size
    compress_images: bool = True  # Enable image compression
    
    # Typography
    body_font_family: str = "Georgia, serif"
    heading_font_family: str = "Georgia, serif"
    line_height: float = 1.7
    paragraph_spacing: float = 0.8
    indent_first: bool = True
    indent_size: str = "1.5em"
    
    # Layout
    page_margin: str = "1.2em"
    keep_page_breaks: bool = True
    preserve_tables: bool = True
    preserve_lists: bool = True
    
    # OCR
    force_ocr: bool = False  # Set True for scanned PDFs
    ocr_language: str = "pol"  # Polish by default
    enable_external_ocr: bool = True
    enable_ml_fallback: bool = True
    enable_epubcheck: bool = True
    
    # Metadata
    language: str = "pl"
    creator: str = "KindleMaster"
    text_cleanup_domain_dictionary_path: str | None = None
    text_cleanup_safe_threshold: float = 0.85
    text_cleanup_review_threshold: float = 0.65
    text_cleanup_emit_diff: bool = False
    enable_pyphen_cleanup: bool = True


# ============================================================================
# CSS STYLES
# ============================================================================

EPUB_CSS = """\
/* ========================================================================
   PREMIUM KINDLE REFLOW STYLESHEET
   Designed to match a high-end purchased e-book:
   - generous vertical rhythm
   - true first-line indents, no gaps between paragraphs
   - smart heading hierarchy with tight top spacing after page break
   - serif body with carefully chosen widows/orphans and hyphenation
   ======================================================================== */
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html {
  font-size: 100%;
  line-height: 1.45;
}

body {
  font-family: "Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua",
               Georgia, "Times New Roman", serif;
  font-size: 1em;
  line-height: 1.5;
  color: #111;
  background: transparent;
  margin: 0;
  text-align: justify;
  -webkit-hyphens: auto;
  hyphens: auto;
  orphans: 3;
  widows: 3;
  font-feature-settings: "kern", "liga", "onum";
  -webkit-font-feature-settings: "kern", "liga", "onum";
}

/* === TYPOGRAPHY === */
h1, h2, h3, h4, h5, h6 {
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia,
               "Times New Roman", serif;
  font-weight: 700;
  line-height: 1.2;
  color: #0a0a0a;
  text-align: left;
  -webkit-hyphens: none;
  hyphens: none;
  page-break-after: avoid;
  page-break-inside: avoid;
  break-after: avoid;
  break-inside: avoid;
}

h1 {
  font-size: 1.8em;
  margin: 2.2em 0 0.9em;
  letter-spacing: -0.005em;
  page-break-before: always;
  break-before: page;
}

/* A chapter opens a new page; the very first H1 should not leave an
   empty trailing page above it. */
section > h1:first-child,
body > h1:first-child {
  page-break-before: avoid;
  break-before: auto;
  margin-top: 1.1em;
}

h2 {
  font-size: 1.35em;
  margin: 1.6em 0 0.55em;
}

h3 {
  font-size: 1.12em;
  margin: 1.25em 0 0.45em;
}

h4 {
  font-size: 1.02em;
  margin: 1.1em 0 0.4em;
}

h5 {
  font-size: 0.98em;
  font-style: italic;
  margin: 1em 0 0.35em;
}

h6 {
  font-size: 0.92em;
  font-style: italic;
  color: #444;
  margin: 1em 0 0.35em;
}

p {
  margin: 0;
  text-indent: 1.3em;
  text-align: justify;
  text-justify: inter-word;
}

p.no-indent,
p.lead,
p.byline,
p.kicker,
p.toc-entry,
p.aside {
  text-indent: 0;
}

/* First paragraph after heading - no indent */
h1 + p, h2 + p, h3 + p, h4 + p, h5 + p, h6 + p,
h1 + div + p, h2 + div + p, h3 + div + p,
.figure + p,
figure + p,
blockquote + p,
ul + p,
ol + p {
  text-indent: 0;
}

/* === EMPHASIS === */
em, .italic {
  font-style: italic;
}

strong, .bold {
  font-weight: bold;
}

strong em, em strong, .bold-italic {
  font-weight: bold;
  font-style: italic;
}

small {
  font-size: 0.85em;
}

large {
  font-size: 1.15em;
}

/* === LISTS === */
ul, ol {
  margin: 0.8em 0;
  padding-left: 2em;
}

li {
  margin: 0.3em 0;
  line-height: 1.7;
}

ul ul, ol ol, ul ol, ol ul {
  margin: 0.3em 0;
}

/* === BLOCKQUOTES === */
blockquote {
  margin: 1em 1.5em;
  padding: 0.8em 1em;
  border-left: 3px solid #ccc;
  font-style: italic;
  color: #444;
  background: #f9f9f9;
}

blockquote p {
  text-indent: 0;
  margin: 0.3em 0;
}

/* === IMAGES === */
img {
  max-width: 100%;
  height: auto;
  display: block;
  margin: 1em auto;
  page-break-inside: avoid;
}

.figure {
  text-align: center;
  margin: 1.5em 0;
  page-break-inside: avoid;
}

.figure img {
  margin: 0.5em auto;
  border-radius: 0.35rem;
}

.magazine-figure img {
  border-radius: 0.45rem;
}

.chess-diagram-container {
  margin: 0.35em auto 1.25em;
  text-align: center;
  page-break-inside: avoid;
  break-inside: avoid;
}

.chess-problem {
  margin: 1.2em 0 1.55em;
  page-break-inside: avoid;
  break-inside: avoid;
}

.chess-diagram {
  display: block;
  width: 100%;
  max-width: 22rem;
  height: auto;
  margin: 0 auto;
  padding: 0.18rem;
  border: 0.08rem solid #d8d2c3;
  background: #fff;
  box-sizing: border-box;
  page-break-inside: avoid;
  break-inside: avoid;
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
}

.diagram-caption {
  margin: 0 0 0.45em;
  text-indent: 0;
  text-align: center;
  font-weight: 600;
  line-height: 1.35;
  hyphens: none;
  white-space: normal;
}

.exercise-number {
  color: #666;
  font-weight: 700;
  margin-right: 0.18em;
}

.diagram-tail {
  text-indent: 0;
  margin-top: 0.7em;
}

.diagram-tail a {
  color: inherit;
  text-decoration: underline;
}

.solution-backlink {
  color: inherit;
  text-decoration: underline;
  text-underline-offset: 0.12em;
}

p.kicker,
p.byline {
  text-indent: 0;
  text-align: left;
  font-size: 0.92em;
  margin: 0.55em 0;
  color: #555;
}

p.kicker {
  text-transform: uppercase;
  font-weight: 700;
  letter-spacing: 0.04em;
}

p.lead {
  text-indent: 0;
  margin: 0.35em 0 0.85em;
  font-size: 1.06em;
  line-height: 1.45;
}

p.aside {
  text-indent: 0;
  margin: 0.75em 1em;
  font-size: 0.92em;
  line-height: 1.4;
  color: #555;
  font-style: italic;
}

p.toc-entry {
  text-indent: 0;
  margin: 0.2em 0;
  line-height: 1.45;
}

.page-marker {
  display: block;
  height: 0;
  overflow: hidden;
}

/* === TABLES === */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.2em 0;
  font-size: 0.95em;
  page-break-inside: avoid;
}

th, td {
  padding: 0.6em 0.8em;
  border: 1px solid #ddd;
  text-align: left;
  vertical-align: top;
}

th {
  font-weight: bold;
  background: #f5f5f5;
  border-bottom: 2px solid #999;
}

thead th {
  background: #e8e8e8;
}

tr:nth-child(even) td {
  background: #fafafa;
}

/* === CODE === */
code, pre {
  font-family: "Courier New", Courier, monospace;
  font-size: 0.9em;
  background: #f5f5f5;
  border: 1px solid #e0e0e0;
  border-radius: 3px;
}

code {
  padding: 0.2em 0.4em;
}

pre {
  padding: 1em;
  margin: 1em 0;
  overflow-x: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
}

pre code {
  background: none;
  border: none;
  padding: 0;
}

/* === LINKS === */
a {
  color: #2a6496;
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

/* === PAGE BREAKS === */
.page-break {
  page-break-after: always;
}

.no-break {
  page-break-inside: avoid;
}

/* === SPECIAL ELEMENTS === */
.ornament {
  text-align: center;
  font-size: 1.5em;
  color: #999;
  margin: 2em 0;
}

.divider {
  text-align: center;
  margin: 2em 0;
}

.divider::before {
  content: "• • •";
  color: #999;
}

/* === COVER PAGE === */
.cover-page {
  text-align: center;
  margin: 0;
  padding: 0;
}

.cover-page img {
  max-width: 100%;
  max-height: 100vh;
  margin: 0 auto;
}

.cover-fallback {
  min-height: 70vh;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 3em 2em;
}

.cover-fallback h1 {
  font-size: 2.2em;
  margin-bottom: 0.6em;
}

.cover-subtitle {
  font-size: 1.1em;
  color: #666;
}

/* === TITLE PAGE === */
.title-page {
  text-align: center;
  margin-top: 3em;
}

.title-page h1 {
  font-size: 2.2em;
  margin: 1em 0 0.5em;
}

.title-page h2 {
  font-size: 1.5em;
  font-weight: normal;
  color: #666;
  margin: 0.5em 0;
}

.title-page .author {
  font-size: 1.3em;
  margin: 2em 0 1em;
  font-style: italic;
}

.title-page .publisher {
  font-size: 1em;
  color: #888;
  margin-top: 3em;
}

/* === TOC === */
.toc-entry {
  margin: 0.5em 0;
  padding-left: 0;
}

.toc-level-1 {
  font-weight: bold;
  font-size: 1.1em;
}

.toc-level-2 {
  padding-left: 1.5em;
}

.toc-level-3 {
  padding-left: 3em;
  font-size: 0.95em;
  color: #666;
}

/* === FOOTNOTES === */
.footnote {
  font-size: 0.85em;
  line-height: 1.5;
  margin: 0.3em 0;
}

.footnote-ref {
  font-size: 0.75em;
  vertical-align: super;
  color: #2a6496;
}

/* === CALLOUTS === */
.callout {
  margin: 1.5em;
  padding: 1em;
  border: 1px solid #ddd;
  border-left: 4px solid #4B5EAA;
  background: #f9f9ff;
  border-radius: 4px;
}

.callout-title {
  font-weight: bold;
  margin-bottom: 0.5em;
  color: #4B5EAA;
}

.callout p {
  text-indent: 0;
  margin: 0.3em 0;
}
"""

# Fixed-layout CSS for 1:1 preservation
FIXED_LAYOUT_CSS = """\
/* Fixed Layout - preserves exact PDF positioning */
body {
  margin: 0;
  padding: 0;
}

.page {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.page-content {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}

.text-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.image-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
}

.text-span {
  position: absolute;
  white-space: pre;
}
"""


# ============================================================================
# PDF TYPE DETECTION
# ============================================================================

def detect_pdf_type(pdf_path: str) -> dict:
    """
    Detect PDF type to choose optimal conversion strategy.
    
    Returns dict with:
    - is_scanned: bool (True if PDF is primarily scanned images)
    - has_text_layer: bool
    - has_images: bool
    - page_count: int
    - recommended_strategy: str
    """
    doc = fitz.open(pdf_path)
    
    total_pages = len(doc)
    scanned_pages = 0
    pages_with_text = 0
    pages_with_images = 0
    
    for page_num in range(total_pages):
        page = doc[page_num]
        
        # Check for text
        text = page.get_text().strip()
        has_text = len(text) > 50  # More than just metadata
        
        if has_text:
            pages_with_text += 1
        
        # Check for images
        images = page.get_images(full=True)
        if images:
            pages_with_images += 1
        
        # Detect if page is primarily an image (scanned)
        if not has_text and images:
            scanned_pages += 1
        elif has_text and images:
            # Check text-to-image ratio
            text_blocks = page.get_text("dict")["blocks"]
            text_area = 0
            for block in text_blocks:
                if block.get("type") == 0:  # Text block
                    bbox = block["bbox"]
                    text_area += (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            
            page_area = (page.rect.width) * (page.rect.height)
            if text_area < page_area * 0.1 and images:
                scanned_pages += 1
    
    doc.close()
    
    # Determine type
    is_scanned = scanned_pages > total_pages * 0.5
    has_text_layer = pages_with_text > total_pages * 0.5
    has_images = pages_with_images > 0
    image_page_ratio = (pages_with_images / total_pages) if total_pages else 0.0
    text_page_ratio = (pages_with_text / total_pages) if total_pages else 0.0
    scanned_page_ratio = (scanned_pages / total_pages) if total_pages else 0.0

    # Layout-heavy magazines / brochures / richly designed PDFs degrade badly
    # when flattened into generic reflowable paragraphs. Route them to
    # fixed-layout whenever the user prefers fidelity.
    layout_heavy = has_images and image_page_ratio >= 0.35
    text_heavy = has_text_layer and image_page_ratio <= 0.15
    
    # Recommend strategy
    if is_scanned:
        recommended = "ocr_fixed"
    elif layout_heavy:
        recommended = "layout_fixed"
    elif has_images and has_text_layer:
        recommended = "hybrid"
    else:
        recommended = "text_reflowable"
    
    return {
        "is_scanned": is_scanned,
        "has_text_layer": has_text_layer,
        "has_images": has_images,
        "page_count": total_pages,
        "scanned_pages": scanned_pages,
        "text_pages": pages_with_text,
        "image_pages": pages_with_images,
        "image_page_ratio": round(image_page_ratio, 4),
        "text_page_ratio": round(text_page_ratio, 4),
        "scanned_page_ratio": round(scanned_page_ratio, 4),
        "layout_heavy": layout_heavy,
        "text_heavy": text_heavy,
        "recommended_strategy": recommended,
    }


# ============================================================================
# PDF2HTMLEX INTEGRATION
# ============================================================================

def check_pdf2htmlEX_available() -> bool:
    """Check if pdf2htmlEX is installed and accessible."""
    # pdf2htmlEX is an optional external tool
    # For now, we'll rely only on PyMuPDF
    return False


def pdf_to_html_fixed_layout(pdf_path: str, output_dir: str, config: ConversionConfig) -> dict:
    """
    Convert PDF to HTML using pdf2htmlEX for maximum visual fidelity.
    
    This preserves:
    - Exact positioning of all elements
    - Original fonts (with fallbacks)
    - Colors and styling
    - Images with correct placement
    - Tables structure
    - Page breaks
    """
    if not check_pdf2htmlEX_available():
        raise RuntimeError(
            "pdf2htmlEX is not installed. "
            "Install it from https://github.com/pdf2htmlEX/pdf2htmlEX "
            "or use: sudo apt install pdf2htmlex"
        )
    
    output_html = os.path.join(output_dir, "output.html")
    
    # Build pdf2htmlEX command with optimal flags
    cmd = [
        "pdf2htmlEX",
        "--zoom", "1.5",  # High zoom for better quality
        "--fit-width", "1024",  # Fit to common width
        "--dest-dir", output_dir,
        "--embed", "cfij",  # Embed CSS, fonts, images, JavaScript
        "--font-format", "woff",  # Use WOFF for better compatibility
        "--process-nontext", "1",  # Process non-text elements
        "--process-outline", "1",  # Preserve document outline
        "--printing", "1",  # Enable printing
        "--no-drm", "1",  # Ignore DRM restrictions
        "--clean-tmp", "1",  # Clean temporary files
        "--optimize-text", "1",  # Optimize text placement
        pdf_path,
        output_html,
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minutes timeout
            cwd=output_dir
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"pdf2htmlEX failed: {result.stderr}")
        
        # Read the generated HTML
        if os.path.exists(output_html):
            with open(output_html, "r", encoding="utf-8") as f:
                html_content = f.read()
            
            # Collect all generated files
            generated_files = []
            for filename in os.listdir(output_dir):
                filepath = os.path.join(output_dir, filename)
                if os.path.isfile(filepath):
                    generated_files.append({
                        "filename": filename,
                        "filepath": filepath,
                    })
            
            return {
                "success": True,
                "html": html_content,
                "files": generated_files,
                "method": "pdf2htmlEX",
            }
        else:
            raise RuntimeError("pdf2htmlEX did not generate output file")
    
    except subprocess.TimeoutExpired:
        raise RuntimeError("pdf2htmlEX timed out after 5 minutes")


# ============================================================================
# PDMUFDF-BASED EXTRACTION (FALLBACK/ HYBRID)
# ============================================================================

def _extract_pdf_metadata(pdf_path: str) -> dict:
    """Extract metadata from PDF for use in EPUB metadata."""
    try:
        doc = fitz.open(pdf_path)
        metadata = doc.metadata
        doc.close()
        
        return {
            "title": metadata.get("title", "") or Path(pdf_path).stem,
            "author": metadata.get("author", "") or "Unknown",
            "subject": metadata.get("subject", ""),
            "creator": metadata.get("creator", ""),
            "producer": metadata.get("producer", ""),
            "creation_date": metadata.get("creationDate", ""),
            "modification_date": metadata.get("modDate", ""),
        }
    except Exception as e:
        print(f"Warning: Could not extract PDF metadata: {e}")
        return {
            "title": Path(pdf_path).stem,
            "author": "Unknown",
            "subject": "",
            "creator": "",
            "producer": "",
            "creation_date": "",
            "modification_date": "",
        }


def _build_content_from_ocr(ocr_result: OCRResult, config: ConversionConfig, pdf_metadata: dict) -> dict:
    """
    Build content structure from OCR results.
    
    This creates chapters based on OCR text and embeds page images.
    """
    chapters = []
    all_images = []
    
    for page_result in ocr_result.pages:
        # Split OCR text into logical blocks (by double newlines)
        blocks = [b.strip() for b in page_result.text.split("\n\n") if b.strip()]
        
        html_parts = []
        
        # First block is likely title if it's short
        if blocks and len(blocks[0].split()) < 10:
            html_parts.append(f"<h1>{html_module.escape(blocks[0])}</h1>")
            blocks = blocks[1:]
        
        # Remaining blocks are paragraphs
        for block in blocks:
            # Simple heuristic: lines ending with period are full paragraphs
            lines = [l.strip() for l in block.split("\n") if l.strip()]
            if lines:
                para_text = " ".join(lines)
                html_parts.append(f"<p>{html_module.escape(para_text)}</p>")
        
        # Add page image for visual reference
        img_filename = f"page_{page_result.page_num}.jpeg"
        all_images.append({
            "filename": img_filename,
            "data": page_result.image_data,
            "extension": "jpeg",
            "page": page_result.page_num,
            "ocr_text": page_result.text,
            "confidence": page_result.confidence,
        })
        
        chapters.append({
            "page_num": page_result.page_num,
            "title": pdf_metadata.get("title") if page_result.page_num == 0 else f"Strona {page_result.page_num + 1}",
            "html_parts": html_parts,
            "images": [],
            "ocr_confidence": page_result.confidence,
        })
    
    return {
        "success": True,
        "chapters": chapters,
        "images": all_images,
        "method": f"ocr_{ocr_result.engine_used}",
        "text_content": any(len(ch["html_parts"]) > 0 for ch in chapters),
    }


def extract_pdf_with_pymupdf(pdf_path: str, config: ConversionConfig, pdf_metadata: dict = None) -> dict:
    """
    Extract content from PDF using PyMuPDF with full layout preservation.
    
    This extracts:
    - Text with positions and styling
    - Images with original quality
    - Tables structure
    - Document outline (TOC)
    """
    if pdf_metadata is None:
        pdf_metadata = _extract_pdf_metadata(pdf_path)
    
    doc = fitz.open(pdf_path)
    
    chapters = []
    all_images = []
    image_count = 0
    toc = doc.get_toc()  # Table of contents
    
    # Analyze font sizes across document
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
    
    # Calculate thresholds
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
        
        # Get text blocks
        blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        
        html_parts = []
        page_images = []
        
        # Sort blocks by position
        sorted_blocks = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        
        for block in sorted_blocks:
            # Image block
            if block["type"] == 1:
                image_count += 1
                img_data = block.get("image")
                if not img_data:
                    continue
                
                # Optimize image
                img_ext = _detect_image_ext(img_data)
                img_filename = f"img_p{page_num}_{image_count}.{img_ext}"
                
                # Save image info
                page_images.append({
                    "filename": img_filename,
                    "data": img_data,
                    "extension": img_ext,
                    "bbox": block.get("bbox"),
                })
                all_images.append({
                    "filename": img_filename,
                    "data": img_data,
                    "extension": img_ext,
                    "page": page_num,
                })
                
                html_parts.append(f'<div class="figure"><img src="images/{img_filename}" alt=""/></div>')
                continue
            
            # Text block
            if block["type"] != 0:
                continue
            
            for line in block["lines"]:
                line_html = ""
                line_size = 0
                line_flags = 0
                line_bbox = line.get("bbox", (0, 0, 0, 0))
                
                for span in line["spans"]:
                    text = strip_emails(span["text"])
                    if not text.strip():
                        line_html += " "
                        continue
                    
                    size = span["size"]
                    flags = span["flags"]
                    font = span.get("font", "Unknown")
                    color = span.get("color", 0)
                    
                    line_size = max(line_size, size)
                    line_flags |= flags
                    
                    escaped = html_module.escape(text)
                    
                    # Apply styling based on flags
                    is_bold = bool(flags & 2**4)
                    is_italic = bool(flags & 2**1)
                    
                    # Add font and color styling
                    style_parts = []
                    if is_bold:
                        escaped = f'<strong>{escaped}</strong>'
                    if is_italic:
                        escaped = f'<em>{escaped}</em>'
                    if size > h1_threshold:
                        escaped = f'<h1>{escaped}</h1>'
                        line_size = size
                    elif size > h2_threshold:
                        escaped = f'<h2>{escaped}</h2>'
                        line_size = size
                    elif size > h3_threshold:
                        escaped = f'<h3>{escaped}</h3>'
                        line_size = size
                    else:
                        escaped = f'<p>{escaped}</p>'
                    
                    line_html += escaped
                
                if line_html.strip():
                    html_parts.append(line_html)
        
        # Extract embedded images via get_images (catches XObject images)
        for img_index, img_info in enumerate(page.get_images(full=True)):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue
            if not base_image or not base_image.get("image"):
                continue
            
            image_count += 1
            img_filename = f"img_x_p{page_num}_{img_index}.{base_image['ext']}"
            
            page_images.append({
                "filename": img_filename,
                "data": base_image["image"],
                "extension": base_image["ext"],
                "bbox": None,
            })
            all_images.append({
                "filename": img_filename,
                "data": base_image["image"],
                "extension": base_image["ext"],
                "page": page_num,
            })
            
            html_parts.append(f'<div class="figure"><img src="images/{img_filename}" alt=""/></div>')
        
        # Check if this page has a TOC entry
        page_title = None
        for item in toc:
            if item[-1] == page_num + 1:  # TOC pages are 1-indexed
                page_title = item[1]
                break
        
        chapters.append({
            "page_num": page_num,
            "title": page_title or f"Strona {page_num + 1}",
            "html_parts": html_parts,
            "images": page_images,
        })
    
    doc.close()
    
    return {
        "success": True,
        "chapters": chapters,
        "images": all_images,
        "toc": toc,
        "method": "pymupdf",
    }


# ============================================================================
# EPUB BUILDER
# ============================================================================

def build_epub(content: dict, config: ConversionConfig, original_filename: str, pdf_metadata: dict = None) -> bytes:
    """
    Build EPUB3 from extracted content with maximum quality.

    Supports both reflowable and fixed-layout modes.
    Uses PDF metadata for title, author, etc.
    """
    if pdf_metadata is None:
        pdf_metadata = {"title": Path(original_filename).stem, "author": "Unknown"}
    
    title = pdf_metadata.get("title") or Path(original_filename).stem
    author = pdf_metadata.get("author") or "Unknown"

    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title(title)
    book.set_language(config.language)
    book.add_author(author)
    
    # Add CSS
    css = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=EPUB_CSS.encode("utf-8"),
    )
    book.add_item(css)
    
    # build_epub emits reflowable XHTML. Dedicated fixed-layout builders add
    # their own assets and metadata without going through this path.
    if content.get("layout_mode") == "fixed":
        fixed_css = epub.EpubItem(
            uid="fixed_style",
            file_name="style/fixed.css",
            media_type="text/css",
            content=FIXED_LAYOUT_CSS.encode("utf-8"),
        )
        book.add_item(fixed_css)
    
    chapters = []
    toc_entries = []
    chapter_num = 0
    chapter_filename_map = {
        idx + 1: f"chapter_{idx + 1:03d}.xhtml"
        for idx in range(len(content.get("chapters", [])))
    }
    page_label_map = {}
    exercise_target_map = {}
    for idx, chapter_data in enumerate(content.get("chapters", []), start=1):
        source_page_label = detect_source_page_label(chapter_data.get("html_parts", [])) or chapter_data.get("_source_page_label")
        for part in chapter_data.get("html_parts", []):
            exercise_num = extract_problem_exercise_number(part)
            if exercise_num and exercise_num not in exercise_target_map:
                exercise_target_map[exercise_num] = f'{chapter_filename_map[idx]}#exercise-{exercise_num}'
                continue
            standalone_num = extract_standalone_exercise_number(part)
            if (
                standalone_num
                and standalone_num != source_page_label
                and standalone_num not in exercise_target_map
            ):
                exercise_target_map[standalone_num] = f'{chapter_filename_map[idx]}#exercise-{standalone_num}'
        if source_page_label and source_page_label not in page_label_map:
            page_label_map[source_page_label] = chapter_filename_map[idx]
        chapter_data["_source_page_label"] = source_page_label
    
    # Add images
    image_items = []
    added_image_filenames = set()
    for img_info in content.get("images", []):
        if img_info["filename"] in added_image_filenames:
            continue
        try:
            img_item = epub.EpubItem(
                uid=f"image_{img_info['filename']}",
                file_name=f"images/{img_info['filename']}",
                media_type=f"image/{img_info['extension']}",
                content=img_info["data"],
            )
            book.add_item(img_item)
            image_items.append(img_item)
            added_image_filenames.add(img_info["filename"])
        except Exception as e:
            print(f"Warning: Could not add image {img_info['filename']}: {e}")
    
    # CRITICAL FIX: Add chess diagram images from chapters
    chess_diagram_count = 0
    for chapter_data in content.get("chapters", []):
        chapter_images = chapter_data.get("images", [])
        chess_imgs = [img for img in chapter_images if img.get("is_chess")]
        
        for chess_img in chess_imgs:
            if chess_img["filename"] in added_image_filenames:
                continue
            try:
                chess_diagram_count += 1
                chess_item = epub.EpubItem(
                    uid=f"chess_{chess_img['filename']}",
                    file_name=f"images/{chess_img['filename']}",
                    media_type=f"image/{chess_img['extension']}",
                    content=chess_img["data"],
                )
                book.add_item(chess_item)
                added_image_filenames.add(chess_img["filename"])
            except Exception as e:
                print(f"Warning: Could not add chess diagram {chess_img['filename']}: {e}")
    
    if chess_diagram_count > 0:
        print(f"Added {chess_diagram_count} chess diagram images to EPUB")
    
    # Build chapters
    for chapter_data in content.get("chapters", []):
        chapter_num += 1

        chapter = epub.EpubHtml(
            title=chapter_data["title"],
            file_name=f"chapter_{chapter_num:03d}.xhtml",
            lang=config.language,
        )

        # Build chapter content
        html_content = "<html><head></head><body>\n"

        # Add title
        html_content += f'<h1>{html_module.escape(chapter_data["title"])}</h1>\n'
        source_page_label = chapter_data.get("_source_page_label")
        chapter_title_text = (chapter_data.get("title") or "").strip()
        chapter_has_chess_context = bool(
            chapter_data.get("inline_chess_diagrams")
            or any(img.get("is_chess") for img in chapter_data.get("images", []))
            or any("chess-problem" in part for part in chapter_data["html_parts"])
        )
        if source_page_label:
            html_content += f'<span id="book-page-{html_module.escape(source_page_label)}" class="page-marker"></span>\n'

        # Add content from html_parts
        sanitized_parts = [part for part in (strip_emails(part) for part in chapter_data["html_parts"]) if part.strip()]
        visible_parts = []
        page_label_removed = False
        title_paragraph_removed = False
        for part in sanitized_parts:
            part = add_problem_anchor(part)
            standalone_num = extract_standalone_exercise_number(part) if chapter_has_chess_context else None
            expected_target = (
                f'{chapter.file_name}#exercise-{standalone_num}'
                if standalone_num
                else None
            )
            if (
                standalone_num
                and standalone_num != source_page_label
                and exercise_target_map.get(standalone_num) == expected_target
            ):
                part = add_standalone_problem_anchor(part, standalone_num)
            _, paragraph_text = extract_plain_paragraph(part)
            if source_page_label and not page_label_removed and paragraph_text == source_page_label:
                page_label_removed = True
                continue
            if (
                chapter_title_text
                and not title_paragraph_removed
                and paragraph_text
                and paragraph_text.strip() == chapter_title_text
            ):
                title_paragraph_removed = True
                continue
            part = maybe_link_solution_reference(part, page_label_map)
            part = maybe_link_problem_reference(part, exercise_target_map)
            visible_parts.append(part)
        html_content += "\n".join(visible_parts)
        
        # CRITICAL FIX: Add chess diagram images from chapter
        chapter_images = chapter_data.get("images", [])
        chess_imgs = [img for img in chapter_images if img.get("is_chess")]
        
        if chess_imgs and not chapter_data.get("inline_chess_diagrams"):
            html_content += '\n<div class="chess-diagrams-section">\n'
            for chess_img in chess_imgs:
                x0, y0, x1, y1 = chess_img.get("bbox", (0, 0, 100, 100))
                width = x1 - x0
                height = y1 - y0
                
                html_content += (
                    f'<div class="chess-diagram-container">\n'
                    f'<img class="chess-diagram" src="images/{chess_img["filename"]}" '
                    f'alt="Diagram szachowy" '
                    f'style="width: {min(width, 400):.0f}px; height: auto; max-width: 100%;"/>\n'
                    f'</div>\n'
                )
            html_content += '</div>\n'
        
        # Add regular chapter images (non-chess)
        regular_imgs = [
            img
            for img in chapter_images
            if not img.get("is_chess") and not img.get("inline")
        ]
        for img in regular_imgs:
            html_content += (
                f'<div class="figure">\n'
                f'<img src="images/{img["filename"]}" alt="" style="max-width: 100%; height: auto;"/>\n'
                f'</div>\n'
            )
        
        html_content += "\n</body></html>"

        chapter.content = html_content
        chapter.add_item(css)

        book.add_item(chapter)
        chapters.append(chapter)
        toc_entries.append(chapter)
    
    # If no chapters were created, add placeholder
    if not chapters:
        ch = epub.EpubHtml(title=title, file_name="chapter_001.xhtml", lang=config.language)
        ch.content = f"<html><body><h1>{html_module.escape(title)}</h1><p>Brak treści do wyodrębnienia.</p></body></html>"
        ch.add_item(css)
        book.add_item(ch)
        chapters.append(ch)
        toc_entries.append(ch)
    
    # Add cover page from first page image
    cover_item = None
    cover_page_added = False
    if content.get("images"):
        first_image = content["images"][0]
        try:
            cover_extension = (first_image.get("extension") or "jpeg").lower()
            if cover_extension == "jpg":
                cover_extension = "jpeg"
            cover_filename = f"cover.{cover_extension}"
            cover_img = epub.EpubItem(
                uid="cover-image",
                file_name=f"images/{cover_filename}",
                media_type=f"image/{cover_extension}",
                content=first_image["data"],
            )
            book.add_item(cover_img)
            book.add_metadata(None, "meta", "", {"name": "cover", "content": "cover-image"})
            cover_item = cover_img

            cover_page = epub.EpubHtml(title="Okładka", file_name="cover.xhtml", lang=config.language)
            cover_page.content = (
                '<html><body class="cover-page">'
                f'<img src="images/{cover_filename}" alt="{html_module.escape(title)}"/>'
                "</body></html>"
            )
            book.add_item(cover_page)
            # Add cover to spine as first item
            chapters.insert(0, cover_page)
            cover_page_added = True
        except Exception as e:
            print(f"Warning: Could not add cover: {e}")

    if not cover_page_added:
        cover_page = epub.EpubHtml(title="Okładka", file_name="cover.xhtml", lang=config.language)
        author_html = (
            f'<p class="cover-subtitle">{html_module.escape(author)}</p>'
            if author and author != "Unknown"
            else ""
        )
        cover_page.content = (
            '<html><body class="cover-page">'
            f'<div class="cover-fallback"><h1>{html_module.escape(title)}</h1>{author_html}</div>'
            "</body></html>"
        )
        book.add_item(cover_page)
        chapters.insert(0, cover_page)

    # Set TOC and spine
    book.toc = toc_entries
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = chapters + ["nav"]
    
    # Only true fixed-layout content should be marked pre-paginated.
    if content.get("layout_mode") == "fixed":
        book.add_metadata("media", "rendition:layout", "pre-paginated")
        book.add_metadata("media", "rendition:orientation", "auto")
        book.add_metadata("media", "rendition:spread", "auto")
    
    # Write EPUB to bytes
    import io
    epub_buffer = io.BytesIO()
    epub.write_epub(epub_buffer, book)
    epub_buffer.seek(0)
    
    return epub_buffer.getvalue()


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _detect_image_ext(data: bytes) -> str:
    """Detect image format from raw bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"\xff\xd8":
        return "jpeg"
    if data[:4] == b"GIF8":
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return "png"


def _strip_tags(html_str: str) -> str:
    """Remove HTML tags for plain text."""
    return re.sub(r"<[^>]+>", "", html_str).strip()


def optimize_image_data(image_data: bytes, config: ConversionConfig) -> bytes:
    """Optimize image for EPUB while preserving quality.
    
    CRITICAL: Reduces file size significantly while maintaining readability.
    """
    if not config.compress_images:
        return image_data
    
    try:
        img = Image.open(io.BytesIO(image_data))

        # Resize if too large (CRITICAL for EPUB size!)
        if img.width > config.image_max_width or img.height > config.image_max_height:
            img.thumbnail((config.image_max_width, config.image_max_height), Image.LANCZOS)

        # Convert to RGB if necessary (for JPEG compatibility)
        if img.mode in ("RGBA", "LA", "P"):
            # For images with transparency, flatten to white background
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # For chess diagrams and simple graphics: reduce colors to save space
        # Chess diagrams typically have < 20 colors
        unique_colors = len(set(img.getdata()))
        if unique_colors < 100:
            # Quantize to reduce file size
            img = img.quantize(colors=min(64, unique_colors), method=Image.Quantize.MEDIANCUT)
            img = img.convert("RGB")

        # Save with optimized settings
        output = io.BytesIO()
        if unique_colors < 100:
            # Simple graphics: PNG with max compression
            img.save(output, format="PNG", optimize=True, compress_level=9)
        else:
            # Photos: JPEG with quality setting
            img.save(output, format="JPEG", quality=config.image_quality, optimize=True)

        return output.getvalue()
    except Exception as e:
        print(f"Warning: Could not optimize image: {e}")
        return image_data


# ============================================================================
# MAIN CONVERSION PIPELINE
# ============================================================================

def _legacy_convert_pdf_to_epub(pdf_path: str, config: Optional[ConversionConfig] = None, original_filename: str = "document.pdf") -> bytes:
    """
    Main conversion pipeline.
    
    Steps:
    1. Detect PDF type
    2. Choose strategy
    3. Extract content
    4. Build EPUB
    5. Return EPUB bytes
    """
    if config is None:
        config = ConversionConfig()
    
    # Step 1: Detect PDF type
    pdf_type = detect_pdf_type(pdf_path)
    print(f"PDF Analysis: {pdf_type}")
    
    # Step 2: Choose strategy
    strategy = pdf_type["recommended_strategy"]
    print(f"Conversion strategy: {strategy}")

    # CRITICAL FIX: Check if this PDF has a text layer
    # If it has text, we MUST use hybrid extraction (NO screenshots!)
    has_text_layer = pdf_type.get("has_text_layer", False) and not pdf_type.get("is_scanned", False)
    has_images = pdf_type.get("has_images", False)

    # Step 3: Extract content
    content = None
    pdf_metadata = _extract_pdf_metadata(pdf_path)

    is_layout_heavy = pdf_type.get("layout_heavy", False)
    is_text_heavy = pdf_type.get("text_heavy", False)
    should_use_fixed_layout = config.prefer_fixed_layout and (
        pdf_type["is_scanned"]
        or pdf_type["recommended_strategy"] == "ocr_fixed"
        or is_layout_heavy
    )
    should_use_magazine_hybrid = has_text_layer and has_images and not is_layout_heavy and not is_text_heavy
    should_use_magazine_reflow = has_text_layer and is_layout_heavy and not pdf_type["is_scanned"]

    # PRIORITY 1: layout-heavy PDFs with a real text layer should use the
    # dedicated Kindle magazine reflow path. Fixed-layout preserves the page
    # look, but it turns the issue into page screenshots and defeats Kindle's
    # main strength: comfortable, resizable reading.
    if should_use_magazine_reflow:
        try:
            print("Building Kindle-first magazine EPUB (layout-aware reflow)...")
            from magazine_kindle_reflow import convert_magazine_to_kindle_reflow

            content = convert_magazine_to_kindle_reflow(pdf_path, config=config)
            print(
                f"Generated magazine reflow content: {len(content.get('chapters', []))} chapters, "
                f"{len(content.get('images', []))} images"
            )
        except Exception as e:
            print(f"Kindle magazine reflow failed ({e}), falling back to alternate extraction...")

    # PRIORITY 2: scanned PDFs or documents without a useful text layer fall
    # back to fixed-layout for fidelity.
    if content is None and should_use_fixed_layout:
        try:
            print("Building fixed-layout EPUB v2 (layout-heavy or scanned document)...")
            from fixed_layout_builder_v2 import build_fixed_layout_epub_v2
            epub_bytes = build_fixed_layout_epub_v2(pdf_path, config, pdf_metadata)
            print(f"Generated fixed-layout EPUB v2: {len(epub_bytes)} bytes")
            return finalize_epub_bytes(epub_bytes, config, pdf_metadata, original_filename)
        except Exception as e:
            print(f"Fixed-layout v2 build failed ({e}), falling back to v1...")
            try:
                from fixed_layout_builder import build_fixed_layout_epub
                epub_bytes = build_fixed_layout_epub(pdf_path, config, pdf_metadata)
                print(f"Generated fixed-layout EPUB v1: {len(epub_bytes)} bytes")
                return finalize_epub_bytes(epub_bytes, config, pdf_metadata, original_filename)
            except Exception as e2:
                print(f"Fixed-layout v1 also failed ({e2}), continuing with alternate extraction...")

        if pdf_type["is_scanned"] and OCR_AVAILABLE and config.force_ocr:
            try:
                print(f"Running OCR with engine preference... Language: {config.ocr_language}")
                ocr_result = run_ocr_on_pdf(pdf_path, language=config.ocr_language, dpi=300)
                print(f"OCR engine used: {ocr_result.engine_used}")
                print(f"OCR success rate: {ocr_result.success_rate:.1%}")
                content = _build_content_from_ocr(ocr_result, config, pdf_metadata)
            except Exception as e:
                print(f"OCR failed ({e}), falling back to PyMuPDF...")
        elif pdf_type["is_scanned"] and not OCR_AVAILABLE:
            print("PDF is scanned but no OCR engine available.")
            print("For better results, install Tesseract or EasyOCR.")
            print("Falling back to PyMuPDF text extraction (limited quality for scanned PDFs).")
    elif should_use_magazine_hybrid:
        try:
            print("  PDF has text layer - using HYBRID v3 (text + images, no screenshots)")
            from magazine_hybrid_converter_v3 import convert_magazine_optimized
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tmp:
                tmp_path = tmp.name
            convert_magazine_optimized(pdf_path, tmp_path, config=config)
            with open(tmp_path, 'rb') as f:
                epub_bytes = f.read()
            import os
            os.unlink(tmp_path)
            print(f"  Generated optimized EPUB: {len(epub_bytes)//1024} KB (text + compressed images)")
            return finalize_epub_bytes(epub_bytes, config, pdf_metadata, original_filename)
        except Exception as e:
            print(f"  Hybrid v3 failed ({e}), falling back to standard methods...")
            import traceback
            traceback.print_exc()
    elif has_text_layer and is_text_heavy:
        print("  Text-heavy PDF detected - skipping magazine hybrid and using text-aware extraction")

    # Try PyMuPDF extraction (always as baseline or fallback)
    if content is None or not content.get("text_content"):
        print("Using PyMuPDF extraction...")
        
        # Try chess-aware extraction first
        try:
            from pymupdf_chess_extractor import extract_pdf_with_chess_support
            print("  Using chess-aware extraction (diagrams will be rendered as images)")
            content = extract_pdf_with_chess_support(pdf_path, config, pdf_metadata)
        except Exception as e:
            print(f"  Chess-aware extraction failed ({e}), falling back to standard extraction")
            content = extract_pdf_with_pymupdf(pdf_path, config, pdf_metadata)

    if not content or not content.get("success"):
        raise RuntimeError("Content extraction failed")

    print(f"Extraction method: {content.get('method')}")
    print(f"Extracted {len(content.get('chapters', []))} chapters and {len(content.get('images', []))} images")
    print(f"Has text content: {content.get('text_content', False)}")

    # Step 4: Build EPUB
    epub_bytes = build_epub(content, config, original_filename, pdf_metadata)

    print(f"Generated EPUB: {len(epub_bytes)} bytes")

    if content.get("method") == "magazine-kindle-reflow":
        return epub_bytes

    return finalize_epub_bytes(epub_bytes, config, pdf_metadata, original_filename)


def convert_pdf_to_epub_with_report(
    pdf_path: str,
    config: Optional[ConversionConfig] = None,
    original_filename: str = "document.pdf",
) -> dict:
    """
    Premium publication-first conversion pipeline.

    Returns:
      {
        "epub_bytes": bytes,
        "analysis": PublicationAnalysis,
        "quality_report": PublicationQualityReport,
        "document": PublicationDocument,
      }
    """
    if config is None:
        config = ConversionConfig()

    pdf_metadata = _extract_pdf_metadata(pdf_path)

    try:
        from publication_analysis import analyze_publication
        from publication_pipeline import (
            build_publication_document,
            finalize_publication_epub,
            publication_to_content,
        )

        analysis = analyze_publication(pdf_path, preferred_profile=config.profile)
        preserve_layout = config.profile == "preserve-layout" or analysis.profile == "fixed_layout_fallback"

        if preserve_layout:
            epub_bytes = _legacy_convert_pdf_to_epub(
                pdf_path,
                config=ConversionConfig(
                    **{
                        **config.__dict__,
                        "prefer_fixed_layout": True,
                    }
                ),
                original_filename=original_filename,
            )
            validation_report = {
                "validation_status": "unavailable",
                "validation_messages": ["Build wykonany sciezka fallback preserve-layout."],
                "validation_tool": "legacy",
                "text_cleanup": {
                    "status": "blocked",
                    "package_blocked": True,
                    "auto_fix_count": 0,
                    "review_needed_count": 0,
                    "blocked_count": 1,
                    "publish_blocked": False,
                },
            }
            return {
                "epub_bytes": epub_bytes,
                "source_type": "pdf",
                "analysis": analysis,
                "quality_report": validation_report,
                "document": None,
                "document_summary": {
                    "title": pdf_metadata.get("title") or Path(original_filename).stem,
                    "author": pdf_metadata.get("author") or "Unknown",
                    "profile": analysis.profile,
                    "layout_mode": "fixed-layout",
                    "section_count": 0,
                    "asset_count": 0,
                },
            }

        document = build_publication_document(pdf_path, config, analysis)
        content = publication_to_content(document)
        final_metadata = {**pdf_metadata, "title": document.title, "author": document.author}
        epub_bytes = build_epub(content, config, original_filename, final_metadata)
        epub_bytes, text_cleanup_summary = finalize_epub_bytes(
            epub_bytes,
            config,
            final_metadata,
            original_filename,
            publication_profile=analysis.profile,
            return_details=True,
        )

        document.quality_report.text_cleanup = text_cleanup_summary
        quality_report = finalize_publication_epub(document, epub_bytes)
        return {
            "epub_bytes": epub_bytes,
            "source_type": "pdf",
            "analysis": analysis,
            "quality_report": quality_report.to_dict(),
            "document": document.to_dict(),
            "document_summary": {
                "title": document.title,
                "author": document.author,
                "profile": document.profile,
                "layout_mode": document.metadata.get("layout_mode", "reflowable"),
                "section_count": len(document.sections),
                "asset_count": len(document.assets),
            },
        }
    except Exception as exc:
        print(f"Premium pipeline failed ({exc}), falling back to legacy conversion...")
        epub_bytes = _legacy_convert_pdf_to_epub(pdf_path, config=config, original_filename=original_filename)
        return {
            "epub_bytes": epub_bytes,
            "source_type": "pdf",
            "analysis": {
                "profile": "legacy-fallback",
                "confidence": 0.0,
                "profile_reason": f"Premium pipeline failed: {exc}",
            },
            "quality_report": {
                "validation_status": "unavailable",
                "validation_messages": [f"Premium pipeline failed: {exc}"],
                "validation_tool": "legacy",
                "text_cleanup": {
                    "status": "unavailable",
                    "auto_fix_count": 0,
                    "review_needed_count": 0,
                    "blocked_count": 0,
                    "publish_blocked": False,
                },
            },
            "document": None,
            "document_summary": {
                "title": pdf_metadata.get("title") or Path(original_filename).stem,
                "author": pdf_metadata.get("author") or "Unknown",
                "profile": "legacy-fallback",
                "layout_mode": "reflowable",
                "section_count": 0,
                "asset_count": 0,
            },
        }


def convert_pdf_to_epub(pdf_path: str, config: Optional[ConversionConfig] = None, original_filename: str = "document.pdf") -> bytes:
    result = convert_pdf_to_epub_with_report(pdf_path, config=config, original_filename=original_filename)
    return result["epub_bytes"]


def convert_docx_to_epub_with_report(
    docx_path: str,
    config: Optional[ConversionConfig] = None,
    original_filename: str = "document.docx",
) -> dict:
    if config is None:
        config = ConversionConfig()

    from docx_conversion import analyze_docx, build_docx_publication_document
    from publication_pipeline import finalize_publication_epub, publication_to_content

    analysis = analyze_docx(docx_path)
    document = build_docx_publication_document(docx_path, language=config.language)
    content = publication_to_content(document)
    # DOCX images are chapter-scoped inline figures; suppress PDF-like auto-cover generation.
    content["images"] = []

    final_metadata = {"title": document.title, "author": document.author}
    epub_bytes = build_epub(content, config, original_filename, final_metadata)
    epub_bytes, text_cleanup_summary = finalize_epub_bytes(
        epub_bytes,
        config,
        final_metadata,
        original_filename,
        publication_profile=document.profile,
        return_details=True,
    )
    document.quality_report.text_cleanup = text_cleanup_summary
    quality_report = finalize_publication_epub(document, epub_bytes)
    return {
        "epub_bytes": epub_bytes,
        "source_type": "docx",
        "analysis": analysis,
        "quality_report": quality_report.to_dict(),
        "document": document.to_dict(),
        "document_summary": {
            "title": document.title,
            "author": document.author,
            "profile": document.profile,
            "layout_mode": document.metadata.get("layout_mode", "reflowable"),
            "section_count": len(document.sections),
            "asset_count": len(document.assets),
        },
    }


def convert_document_to_epub_with_report(
    source_path: str,
    config: Optional[ConversionConfig] = None,
    original_filename: str | None = None,
    source_type: str | None = None,
) -> dict:
    resolved_path = Path(source_path)
    detected_source_type = (source_type or resolved_path.suffix.lstrip(".")).lower()
    if detected_source_type == "pdf":
        return convert_pdf_to_epub_with_report(
            source_path,
            config=config,
            original_filename=original_filename or resolved_path.name or "document.pdf",
        )
    if detected_source_type == "docx":
        return convert_docx_to_epub_with_report(
            source_path,
            config=config,
            original_filename=original_filename or resolved_path.name or "document.docx",
        )
    raise ValueError(f"Unsupported source type: {resolved_path.suffix or detected_source_type}")


def convert_document_to_epub(
    source_path: str,
    config: Optional[ConversionConfig] = None,
    original_filename: str | None = None,
    source_type: str | None = None,
) -> bytes:
    return convert_document_to_epub_with_report(
        source_path,
        config=config,
        original_filename=original_filename,
        source_type=source_type,
    )["epub_bytes"]


def _parse_pdf2htmlex_output(result: dict, config: ConversionConfig) -> dict:
    """
    Parse pdf2htmlEX output and convert to our internal structure.
    """
    from bs4 import BeautifulSoup
    
    html_content = result.get("html", "")
    if not html_content:
        return None
    
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Extract pages
    pages = soup.find_all(class_="page")
    chapters = []
    images = []
    
    for page_idx, page in enumerate(pages):
        # Get page content
        html_parts = []
        
        # Extract text elements
        text_elements = page.find_all(class_=["t", "text"])
        for elem in text_elements:
            sanitized_fragment = strip_emails(str(elem))
            if sanitized_fragment.strip():
                html_parts.append(sanitized_fragment)
        
        # Extract images
        img_tags = page.find_all("img")
        for img in img_tags:
            src = img.get("src", "")
            if not src:
                continue
            
            # Find image file in result files
            for file_info in result.get("files", []):
                if file_info["filename"] == src:
                    with open(file_info["filepath"], "rb") as f:
                        img_data = f.read()
                    
                    img_ext = Path(src).suffix.lstrip(".") or "png"
                    images.append({
                        "filename": src,
                        "data": img_data,
                        "extension": img_ext,
                        "page": page_idx,
                    })
                    
                    html_parts.append(f'<div class="figure"><img src="images/{src}" alt=""/></div>')
                    break
        
        chapters.append({
            "page_num": page_idx,
            "title": f"Strona {page_idx + 1}",
            "html_parts": html_parts,
            "images": [],
        })
    
    return {
        "success": True,
        "chapters": chapters,
        "images": images,
        "method": "pdf2htmlEX_parsed",
    }
