from __future__ import annotations

import argparse
import html
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import fitz

from kindlemaster_structured_lists import detect_inline_ordered_list

ROOT = Path(__file__).resolve().parent
RUNTIME_ROOT = ROOT / "kindlemaster_runtime"
BASELINE_OUTPUT_DIR = RUNTIME_ROOT / "output" / "baseline_epub"
REPORT_OUTPUT_DIR = RUNTIME_ROOT / "output" / "reports"
XHTML_NS = "http://www.w3.org/1999/xhtml"
ALLOWED_FALLBACK_REASONS = {
    "illustration_only",
    "diagram_dense",
    "advertisement",
    "true_non_reflowable_layout",
    "safe_text_reconstruction_impossible",
}

BASELINE_CSS = """\
html {
  font-size: 100%;
}

body {
  margin: 0;
  padding: 0 5%;
  color: #111;
  background: #fff;
  font-family: serif;
  line-height: 1.4;
}

h1, h2 {
  line-height: 1.2;
  text-align: left;
}

h1 {
  margin: 0 0 0.5em;
  font-size: 1.7em;
}

h2 {
  margin: 1.2em 0 0.6em;
  font-size: 1.15em;
}

p {
  margin: 0 0 0.85em;
}

ol.inline-choice-list {
  margin: 0 0 1em 1.4em;
  padding: 0;
}

ol.inline-choice-list li {
  margin: 0 0 0.35em;
}

ol.inline-choice-list.upper-alpha {
  list-style-type: upper-alpha;
}

ol.inline-choice-list.decimal {
  list-style-type: decimal;
}

figure {
  margin: 1.2em 0;
  text-align: center;
}

img {
  max-width: 100%;
  height: auto;
}

.page-label {
  font-size: 0.95em;
  color: #666;
}
"""


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "item"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            suffix=path.suffix or ".tmp",
            encoding="utf-8",
            newline="",
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    joined = " ".join(lines)
    joined = re.sub(r"\s+([,.;:!?])", r"\1", joined)
    return joined.strip()


def join_block_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    merged: list[str] = [lines[0].strip()]
    for line in lines[1:]:
        current = line.strip()
        if not current:
            continue
        previous = merged[-1]
        if previous.endswith("-") and current[:1].islower():
            merged[-1] = previous[:-1] + current
        elif previous.endswith(("/", "(", "“", "\"")):
            merged[-1] = previous + current
        else:
            merged[-1] = previous + " " + current
    return normalize_text(" ".join(merged))


def extract_pdf_metadata(pdf_path: Path) -> tuple[str, str, str]:
    with fitz.open(pdf_path) as doc:
        metadata = doc.metadata or {}
    title = (metadata.get("title") or pdf_path.stem).strip()
    author = (metadata.get("author") or "Unknown").strip()
    language = "pl"
    return title or pdf_path.stem, author or "Unknown", language


def extract_text_blocks(page: fitz.Page) -> list[dict]:
    page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    blocks: list[dict] = []
    for raw_block in page_dict.get("blocks", []):
        if raw_block.get("type") != 0:
            continue
        bbox = tuple(raw_block.get("bbox", (0.0, 0.0, 0.0, 0.0)))
        line_texts: list[str] = []
        for line in raw_block.get("lines", []):
            fragments: list[str] = []
            for span in line.get("spans", []):
                text = span.get("text", "")
                if text:
                    fragments.append(text)
            merged_line = "".join(fragments).strip()
            if merged_line:
                line_texts.append(merged_line)
        text = join_block_lines(line_texts)
        if not text:
            continue
        blocks.append(
            {
                "bbox": bbox,
                "text": text,
                "x0": bbox[0],
                "y0": bbox[1],
                "x1": bbox[2],
                "y1": bbox[3],
                "width": bbox[2] - bbox[0],
            }
        )
    return blocks


def extract_ordered_text_blocks(page: fitz.Page) -> tuple[list[dict], int]:
    blocks = extract_text_blocks(page)
    column_starts = detect_column_starts(blocks, page.rect.width)
    ordered = sort_text_blocks_for_reading(blocks, page.rect.width, page.rect.height, column_starts=column_starts)
    return ordered, len(column_starts)


def detect_column_starts(blocks: list[dict], page_width: float) -> list[float]:
    wide_threshold = page_width * 0.7
    column_gap_threshold = max(60.0, page_width * 0.12)
    narrow_blocks = [block for block in blocks if block["width"] < wide_threshold]
    if len(narrow_blocks) < 4:
        return []
    x_positions = sorted(block["x0"] for block in narrow_blocks)
    column_starts: list[float] = []
    for x0 in x_positions:
        if not column_starts or abs(x0 - column_starts[-1]) > column_gap_threshold:
            column_starts.append(x0)
    return column_starts


def sort_text_blocks_for_reading(
    blocks: list[dict],
    page_width: float,
    page_height: float,
    *,
    column_starts: list[float] | None = None,
) -> list[dict]:
    if len(blocks) <= 2:
        return sorted(blocks, key=lambda item: (item["y0"], item["x0"]))

    wide_threshold = page_width * 0.7
    column_starts = list(column_starts or detect_column_starts(blocks, page_width))
    if len(column_starts) <= 1:
        return sorted(blocks, key=lambda item: (item["y0"], item["x0"]))

    def assign_column(block: dict) -> int:
        if block["width"] >= wide_threshold:
            return -1
        return min(range(len(column_starts)), key=lambda idx: abs(block["x0"] - column_starts[idx]))

    column_blocks = [block for block in blocks if assign_column(block) >= 0]
    if not column_blocks:
        return sorted(blocks, key=lambda item: (item["y0"], item["x0"]))

    lower_page_column_blocks = [block for block in column_blocks if block["y0"] >= page_height * 0.4]
    if len(lower_page_column_blocks) >= 4:
        column_top = min(block["y0"] for block in lower_page_column_blocks)
    else:
        column_top = min(block["y0"] for block in column_blocks)
    ordered: list[dict] = []
    pre_column_blocks = sorted(
        (
            block
            for block in blocks
            if block["y0"] < column_top - 8
        ),
        key=lambda item: (item["y0"], item["x0"]),
    )
    ordered.extend(pre_column_blocks)

    for column_index in range(len(column_starts)):
        ordered.extend(
            sorted(
                (
                    block
                    for block in column_blocks
                    if block["y0"] >= column_top - 8 and assign_column(block) == column_index
                ),
                key=lambda item: (item["y0"], item["x0"]),
            )
        )

    trailing_wide = sorted(
        (
            block
            for block in blocks
            if block not in ordered
        ),
        key=lambda item: (item["y0"], item["x0"]),
    )
    ordered.extend(trailing_wide)
    return ordered


def looks_like_photo_credit(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return lowered.startswith(("fot.", "foto", "fotografia", "photo"))


def looks_like_title_candidate(text: str) -> bool:
    cleaned = (text or "").strip()
    if not cleaned or cleaned.startswith(("—", "-")):
        return False
    if re.fullmatch(r"Page\s+\d+", cleaned):
        return False
    if looks_like_photo_credit(cleaned):
        return False
    if cleaned.isupper() and len(cleaned) < 40:
        return False
    words = cleaned.split()
    return 3 <= len(words) <= 24 and cleaned[:1].isupper()


def select_hybrid_header_blocks(blocks: list[dict]) -> tuple[list[dict], float | None]:
    title_index = next((idx for idx, block in enumerate(blocks[:8]) if looks_like_title_candidate(block["text"])), None)
    if title_index is None:
        return [], None

    selected: list[dict] = []
    for block in blocks[max(0, title_index - 2) : title_index]:
        text = block["text"]
        if re.fullmatch(r"Page\s+\d+", text):
            continue
        if looks_like_photo_credit(text):
            continue
        if len(text) <= 40 and text.upper() == text:
            selected.append(block)

    selected.append(blocks[title_index])

    for block in blocks[title_index + 1 : title_index + 4]:
        text = block["text"]
        if text.startswith(("—", "-")):
            selected.append(block)
            continue
        if len(text) >= 80 and text[:1].isupper():
            selected.append(block)
            break
        if len(text) <= 80 and text.upper() == text:
            continue
        break

    if not selected:
        return [], None
    return selected, max(block["y1"] for block in selected)


def normalize_conversion_profile(value: str | None) -> str:
    normalized = (value or "auto-premium").strip().lower()
    if normalized in {"book", "magazine", "technical-study", "preserve-layout", "auto-premium"}:
        return normalized
    return "auto-premium"


def build_content_blocks(paragraphs: list[str]) -> tuple[list[dict[str, object]], dict[str, int]]:
    blocks: list[dict[str, object]] = []
    ordered_list_blocks = 0
    ordered_list_items = 0
    for paragraph in paragraphs:
        ordered_list = detect_inline_ordered_list(paragraph)
        if ordered_list:
            blocks.append(ordered_list)
            ordered_list_blocks += 1
            ordered_list_items += int(ordered_list.get("item_count") or 0)
            continue
        blocks.append({"kind": "paragraph", "text": paragraph})
    return blocks, {
        "ordered_list_blocks": ordered_list_blocks,
        "ordered_list_items": ordered_list_items,
    }


def _build_page_evidence(
    *,
    page_index: int,
    page_name: str,
    nav_label: str,
    kind: str,
    content_blocks: list[dict[str, object]],
    page_image_count: int,
    text_length: int,
    paragraph_count: int,
    column_count: int,
    fallback_reason: str | None,
    fallback_justified: bool,
    text_reconstruction_attempted: bool,
    ocr_needed: bool,
    ocr_applied: bool,
) -> dict[str, object]:
    if fallback_reason and fallback_reason not in ALLOWED_FALLBACK_REASONS:
        raise ValueError(f"Unsupported fallback reason: {fallback_reason}")
    return {
        "page_number": page_index,
        "page_name": page_name,
        "nav_label": nav_label,
        "page_kind": kind,
        "text_first": kind == "text_first",
        "fallback_used": kind != "text_first",
        "fallback_reason": fallback_reason,
        "fallback_justified": fallback_justified,
        "text_reconstruction_attempted": text_reconstruction_attempted,
        "ocr_needed": ocr_needed,
        "ocr_applied": ocr_applied,
        "page_images_detected": page_image_count,
        "text_length": text_length,
        "paragraph_count": paragraph_count,
        "column_count": column_count,
        "content_block_count": len(content_blocks),
    }


def build_page_records(
    pdf_path: Path,
    *,
    image_fallback_threshold: int = 80,
    conversion_profile: str | None = None,
    force_ocr_requested: bool = False,
) -> tuple[list[dict], dict[str, bytes], dict[str, object]]:
    pages: list[dict] = []
    images: dict[str, bytes] = {}
    normalized_profile = normalize_conversion_profile(conversion_profile)
    image_detected_pages = 0
    hybrid_candidate_pages = 0
    hybrid_applied_pages = 0
    structured_list_blocks = 0
    structured_list_items = 0
    page_models: list[dict[str, object]] = []

    with fitz.open(pdf_path) as doc:
        pdf_page_count = doc.page_count
        for index, page in enumerate(doc, start=1):
            paragraphs: list[str] = []
            ordered_blocks, column_count = extract_ordered_text_blocks(page)
            for block in ordered_blocks:
                text = block["text"]
                if text:
                    paragraphs.append(text)

            text_length = sum(len(paragraph) for paragraph in paragraphs)
            page_image_count = len(page.get_images(full=True))
            if page_image_count > 0:
                image_detected_pages += 1
            page_id = f"page-{index:04d}"
            page_name = f"{page_id}.xhtml"
            nav_label = f"Page {index}"
            header_blocks, header_bottom = select_hybrid_header_blocks(ordered_blocks)
            hybrid_candidate = column_count >= 3 and text_length >= 1200 and page_image_count > 0
            if hybrid_candidate:
                hybrid_candidate_pages += 1

            if normalized_profile == "preserve-layout" and hybrid_candidate:
                content_blocks, content_stats = build_content_blocks([block["text"] for block in header_blocks])
                structured_list_blocks += content_stats["ordered_list_blocks"]
                structured_list_items += content_stats["ordered_list_items"]
                image_name = f"images/{page_id}.png"
                clip = None
                if header_bottom is not None and header_bottom < page.rect.height - 40:
                    clip = fitz.Rect(0, header_bottom + 12, page.rect.width, page.rect.height - 10)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=clip, alpha=False)
                images[image_name] = pixmap.tobytes("png")
                hybrid_applied_pages += 1
                pages.append(
                    {
                        "id": page_id,
                        "name": page_name,
                        "nav_label": nav_label,
                        "kind": "hybrid_illustrated",
                        "content_blocks": content_blocks,
                        "image_name": image_name,
                        "evidence": _build_page_evidence(
                            page_index=index,
                            page_name=page_name,
                            nav_label=nav_label,
                            kind="hybrid_illustrated",
                            content_blocks=content_blocks,
                            page_image_count=page_image_count,
                            text_length=text_length,
                            paragraph_count=len(paragraphs),
                            column_count=column_count,
                            fallback_reason="true_non_reflowable_layout",
                            fallback_justified=True,
                            text_reconstruction_attempted=True,
                            ocr_needed=False,
                            ocr_applied=False,
                        ),
                    }
                )
                page_models.append(pages[-1]["evidence"])
                continue

            if text_length < image_fallback_threshold and page_image_count > 0:
                image_name = f"images/{page_id}.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                images[image_name] = pixmap.tobytes("png")
                fallback_reason = "safe_text_reconstruction_impossible"
                fallback_justified = text_length <= 40 or len(paragraphs) <= 1
                pages.append(
                    {
                        "id": page_id,
                        "name": page_name,
                        "nav_label": nav_label,
                        "kind": "image_fallback",
                        "content_blocks": [],
                        "image_name": image_name,
                        "evidence": _build_page_evidence(
                            page_index=index,
                            page_name=page_name,
                            nav_label=nav_label,
                            kind="image_fallback",
                            content_blocks=[],
                            page_image_count=page_image_count,
                            text_length=text_length,
                            paragraph_count=len(paragraphs),
                            column_count=column_count,
                            fallback_reason=fallback_reason,
                            fallback_justified=fallback_justified,
                            text_reconstruction_attempted=True,
                            ocr_needed=text_length == 0,
                            ocr_applied=False,
                        ),
                    }
                )
                page_models.append(pages[-1]["evidence"])
                continue

            content_blocks, content_stats = build_content_blocks(paragraphs)
            structured_list_blocks += content_stats["ordered_list_blocks"]
            structured_list_items += content_stats["ordered_list_items"]
            pages.append(
                    {
                        "id": page_id,
                        "name": page_name,
                        "nav_label": nav_label,
                        "kind": "text_first",
                        "content_blocks": content_blocks,
                        "image_name": None,
                        "evidence": _build_page_evidence(
                            page_index=index,
                            page_name=page_name,
                            nav_label=nav_label,
                            kind="text_first",
                            content_blocks=content_blocks,
                            page_image_count=page_image_count,
                            text_length=text_length,
                            paragraph_count=len(paragraphs),
                            column_count=column_count,
                            fallback_reason=None,
                            fallback_justified=False,
                            text_reconstruction_attempted=True,
                            ocr_needed=False,
                            ocr_applied=False,
                        ),
                    }
                )
            page_models.append(pages[-1]["evidence"])

    diagnostics = {
        "pdf_page_count": pdf_page_count,
        "pages_with_detected_images": image_detected_pages,
        "hybrid_candidate_pages": hybrid_candidate_pages,
        "hybrid_applied_pages": hybrid_applied_pages,
        "conversion_profile_requested": normalized_profile,
        "conversion_profile_applied": (
            "preserve_layout_fallback"
            if normalized_profile == "preserve-layout"
            else "text_first_reflow"
        ),
        "force_ocr_requested": force_ocr_requested,
        "force_ocr_applied": False,
        "structured_list_blocks": structured_list_blocks,
        "structured_list_items": structured_list_items,
        "page_models": page_models,
    }

    return pages, images, diagnostics


def render_xhtml_page(*, book_title: str, page_record: dict) -> str:
    evidence = page_record.get("evidence") or {}

    def render_content_blocks(content_blocks: list[dict[str, object]]) -> str:
        rendered: list[str] = []
        for block in content_blocks:
            if block.get("kind") == "ordered_list":
                list_style = "upper-alpha" if block.get("list_style") == "upper-alpha" else "decimal"
                items = "\n".join(
                    f"          <li>{html.escape(str(item))}</li>"
                    for item in (block.get("items") or [])
                )
                rendered.append(
                    f'      <ol class="inline-choice-list {html.escape(list_style)}">\n{items}\n      </ol>'
                )
                continue
            rendered.append(f"<p>{html.escape(str(block.get('text') or ''))}</p>")
        return "\n".join(rendered)

    if page_record["kind"] == "image_fallback":
        body = (
            f"<p class=\"page-label\">{html.escape(page_record['nav_label'])}</p>"
            f"<figure><img src=\"../{html.escape(page_record['image_name'])}\" alt=\"{html.escape(page_record['nav_label'])}\"></figure>"
        )
    elif page_record["kind"] == "hybrid_illustrated":
        paragraphs = render_content_blocks(page_record["content_blocks"])
        body = (
            f"{paragraphs}\n"
            f'<figure><img src="../{html.escape(page_record["image_name"])}" alt="{html.escape(page_record["nav_label"])}"></figure>'
        )
    else:
        paragraphs = render_content_blocks(page_record["content_blocks"])
        body = f"<p class=\"page-label\">{html.escape(page_record['nav_label'])}</p>\n{paragraphs}"

    body_attrs = (
        f' data-km-page-kind="{html.escape(str(evidence.get("page_kind") or page_record["kind"]))}"'
        f' data-km-fallback-used="{str(bool(evidence.get("fallback_used"))).lower()}"'
        f' data-km-fallback-reason="{html.escape(str(evidence.get("fallback_reason") or ""))}"'
        f' data-km-fallback-justified="{str(bool(evidence.get("fallback_justified"))).lower()}"'
        f' data-km-text-reconstruction-attempted="{str(bool(evidence.get("text_reconstruction_attempted", True))).lower()}"'
        f' data-km-ocr-needed="{str(bool(evidence.get("ocr_needed"))).lower()}"'
        f' data-km-ocr-applied="{str(bool(evidence.get("ocr_applied"))).lower()}"'
    )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML_NS}">
  <head>
    <title>{html.escape(book_title)} - {html.escape(page_record["nav_label"])}</title>
    <link rel="stylesheet" type="text/css" href="../styles/baseline.css"/>
  </head>
  <body{body_attrs}>
    <section id="{html.escape(page_record["id"])}">
      {body}
    </section>
  </body>
</html>
"""


def render_title_page(*, title: str, author: str) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML_NS}">
  <head>
    <title>{html.escape(title)}</title>
    <link rel="stylesheet" type="text/css" href="styles/baseline.css"/>
  </head>
  <body>
    <section id="title-page">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(author)}</p>
    </section>
  </body>
</html>
"""


def render_nav(*, title: str, pages: list[dict]) -> str:
    links = "\n".join(
        f'        <li><a href="xhtml/{html.escape(page["name"])}#{html.escape(page["id"])}">{html.escape(page["nav_label"])}</a></li>'
        for page in pages
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="{XHTML_NS}" xmlns:epub="http://www.idpf.org/2007/ops">
  <head>
    <title>{html.escape(title)}</title>
  </head>
  <body>
    <nav epub:type="toc" id="toc">
      <h1>{html.escape(title)}</h1>
      <ol>
{links}
      </ol>
    </nav>
  </body>
</html>
"""


def render_ncx(*, identifier: str, title: str, pages: list[dict]) -> str:
    nav_points = []
    for index, page in enumerate(pages, start=1):
        nav_points.append(
            f"""    <navPoint id="nav-{index}" playOrder="{index}">
      <navLabel><text>{html.escape(page["nav_label"])}</text></navLabel>
      <content src="xhtml/{html.escape(page["name"])}#{html.escape(page["id"])}"/>
    </navPoint>"""
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{html.escape(identifier)}"/>
  </head>
  <docTitle><text>{html.escape(title)}</text></docTitle>
  <navMap>
{chr(10).join(nav_points)}
  </navMap>
</ncx>
"""


def render_opf(*, identifier: str, title: str, author: str, language: str, pages: list[dict], images: dict[str, bytes]) -> str:
    manifest_lines = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>',
        '    <item id="css" href="styles/baseline.css" media-type="text/css"/>',
    ]
    spine_lines = ['    <itemref idref="title"/>']

    for page in pages:
        item_id = slugify(page["id"])
        manifest_lines.append(
            f'    <item id="{item_id}" href="xhtml/{page["name"]}" media-type="application/xhtml+xml"/>'
        )
        spine_lines.append(f'    <itemref idref="{item_id}"/>')

    for image_name in images:
        image_id = slugify(image_name)
        manifest_lines.append(
            f'    <item id="{image_id}" href="{image_name}" media-type="image/png"/>'
        )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bookid" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{html.escape(identifier)}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:creator>{html.escape(author)}</dc:creator>
    <dc:language>{html.escape(language)}</dc:language>
  </metadata>
  <manifest>
{chr(10).join(manifest_lines)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(spine_lines)}
  </spine>
</package>
"""


def create_baseline_epub(
    pdf_path: Path,
    output_path: Path,
    *,
    title: str | None = None,
    author: str | None = None,
    language: str | None = None,
    profile: str | None = None,
    force_ocr: bool = False,
) -> dict:
    detected_title, detected_author, detected_language = extract_pdf_metadata(pdf_path)
    title = title or detected_title
    author = author or detected_author
    language = language or detected_language

    pages, images, diagnostics = build_page_records(
        pdf_path,
        conversion_profile=profile,
        force_ocr_requested=force_ocr,
    )
    identifier = f"kindlemaster-{slugify(pdf_path.stem)}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=output_path.parent, suffix=output_path.suffix or ".epub") as handle:
            temp_output = Path(handle.name)

        with zipfile.ZipFile(temp_output, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            zf.writestr(
                "META-INF/container.xml",
                """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
            )
            zf.writestr("EPUB/styles/baseline.css", BASELINE_CSS)
            zf.writestr("EPUB/title.xhtml", render_title_page(title=title, author=author))
            zf.writestr("EPUB/nav.xhtml", render_nav(title=title, pages=pages))
            zf.writestr("EPUB/toc.ncx", render_ncx(identifier=identifier, title=title, pages=pages))
            zf.writestr(
                "EPUB/content.opf",
                render_opf(identifier=identifier, title=title, author=author, language=language, pages=pages, images=images),
            )

            for page in pages:
                zf.writestr(f"EPUB/xhtml/{page['name']}", render_xhtml_page(book_title=title, page_record=page))

            for image_name, image_bytes in images.items():
                zf.writestr(f"EPUB/{image_name}", image_bytes)

        os.replace(temp_output, output_path)
        temp_output = None
    finally:
        if temp_output is not None and temp_output.exists():
            temp_output.unlink(missing_ok=True)

    report = {
        "source_pdf": str(pdf_path),
        "baseline_epub": str(output_path),
        "title": title,
        "author": author,
        "language": language,
        "pdf_page_count": diagnostics["pdf_page_count"],
        "pages_total": len(pages),
        "text_pages": sum(1 for page in pages if page["kind"] == "text_first"),
        "text_first_pages": sum(1 for page in pages if page["kind"] == "text_first"),
        "hybrid_pages": sum(1 for page in pages if page["kind"] == "hybrid_illustrated"),
        "hybrid_illustrated_pages": sum(1 for page in pages if page["kind"] == "hybrid_illustrated"),
        "textual_pages": sum(1 for page in pages if page["kind"] in {"text_first", "hybrid_illustrated"}),
        "image_fallback_pages": sum(1 for page in pages if page["kind"] == "image_fallback"),
        "justified_fallback_pages": sum(1 for page in pages if bool((page.get("evidence") or {}).get("fallback_justified"))),
        "unjustified_fallback_pages": sum(
            1
            for page in pages
            if bool((page.get("evidence") or {}).get("fallback_used"))
            and not bool((page.get("evidence") or {}).get("fallback_justified"))
        ),
        "pages_with_detected_images": diagnostics["pages_with_detected_images"],
        "hybrid_candidate_pages": diagnostics["hybrid_candidate_pages"],
        "hybrid_applied_pages": diagnostics["hybrid_applied_pages"],
        "page_coverage_pass": diagnostics["pdf_page_count"] == len(pages),
        "page_coverage_ratio": round(len(pages) / max(1, diagnostics["pdf_page_count"]), 3),
        "conversion_profile_requested": diagnostics["conversion_profile_requested"],
        "conversion_profile_applied": diagnostics["conversion_profile_applied"],
        "ocr_requested": diagnostics["force_ocr_requested"],
        "ocr_applied": diagnostics["force_ocr_applied"],
        "structured_list_blocks": diagnostics["structured_list_blocks"],
        "structured_list_items": diagnostics["structured_list_items"],
        "page_models": diagnostics["page_models"],
        "warnings": (
            ["force_ocr_requested_but_ocr_stage_not_available"]
            if diagnostics["force_ocr_requested"] and not diagnostics["force_ocr_applied"]
            else []
        ),
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a baseline EPUB from a PDF for Kindle Master")
    parser.add_argument("--pdf", required=True, help="Path to the source PDF")
    parser.add_argument("--output", help="Path to the output EPUB")
    parser.add_argument("--title", help="Override title")
    parser.add_argument("--author", help="Override author")
    parser.add_argument("--language", help="Optional language override for the baseline EPUB")
    parser.add_argument("--report", help="Optional JSON report path")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    output_path = Path(args.output).resolve() if args.output else (BASELINE_OUTPUT_DIR / f"{pdf_path.stem}.epub")
    report_path = Path(args.report).resolve() if args.report else (REPORT_OUTPUT_DIR / f"{pdf_path.stem}-baseline.json")

    report = create_baseline_epub(
        pdf_path,
        output_path,
        title=args.title,
        author=args.author,
        language=args.language,
    )

    _atomic_write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
