from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import fitz

from kindlemaster_pdf_to_epub import create_baseline_epub


XHTML_NS = "http://www.w3.org/1999/xhtml"
NS = {"xhtml": XHTML_NS}


def _build_synthetic_pdf(pdf_path: Path) -> None:
    doc = fitz.open()
    try:
        text_box = fitz.Rect(72, 72, 520, 220)
        alpha_page = doc.new_page()
        alpha_page.insert_textbox(
            text_box,
            "A. Alpha item in text mode. "
            "B. Beta item in text mode. "
            "C. Gamma item in text mode. "
            "D. Delta item in text mode.",
            fontsize=11,
        )

        numeric_page = doc.new_page()
        numeric_page.insert_textbox(
            text_box,
            "1. First item in text mode. "
            "2. Second item in text mode. "
            "3. Third item in text mode.",
            fontsize=11,
        )

        prose_page = doc.new_page()
        prose_page.insert_textbox(
            fitz.Rect(72, 72, 520, 260),
            "This prose uses A. and B. as abbreviations and stays intact without splitting across lines.",
            fontsize=11,
        )

        doc.save(pdf_path)
    finally:
        doc.close()


def _page_section(epub_path: Path, page_name: str) -> ET.Element:
    with zipfile.ZipFile(epub_path) as zf:
        xhtml = zf.read(f"EPUB/xhtml/{page_name}").decode("utf-8")
    root = ET.fromstring(xhtml)
    section = root.find(".//xhtml:section", NS)
    assert section is not None, f"Expected section in {page_name}"
    return section


def _section_paragraph_texts(section: ET.Element) -> list[str]:
    texts: list[str] = []
    for child in section:
        if child.tag == f"{{{XHTML_NS}}}p":
            texts.append("".join(child.itertext()).strip())
    return texts


def _section_list_texts(section: ET.Element) -> list[list[str]]:
    results: list[list[str]] = []
    for child in section:
        if child.tag != f"{{{XHTML_NS}}}ol":
            continue
        items = []
        for item in child.findall("xhtml:li", NS):
            items.append("".join(item.itertext()).strip())
        results.append(items)
    return results


def test_inline_alpha_and_numeric_sequences_become_ordered_lists(tmp_path: Path) -> None:
    pdf_path = tmp_path / "synthetic-list-layout.pdf"
    epub_path = tmp_path / "synthetic-list-layout.epub"
    _build_synthetic_pdf(pdf_path)

    report = create_baseline_epub(
        pdf_path,
        epub_path,
        title="Synthetic List Layout",
        author="Tester",
        language="en",
        profile="book",
    )

    assert report["page_coverage_pass"] is True
    assert report["text_pages"] == 3
    assert report["image_fallback_pages"] == 0

    alpha_section = _page_section(epub_path, "page-0001.xhtml")
    numeric_section = _page_section(epub_path, "page-0002.xhtml")
    alpha_lists = _section_list_texts(alpha_section)
    numeric_lists = _section_list_texts(numeric_section)

    assert _section_paragraph_texts(alpha_section) == ["Page 1"]
    assert _section_paragraph_texts(numeric_section) == ["Page 2"]
    assert alpha_lists == [[
        "Alpha item in text mode.",
        "Beta item in text mode.",
        "Gamma item in text mode.",
        "Delta item in text mode.",
    ]]
    assert numeric_lists == [[
        "First item in text mode.",
        "Second item in text mode.",
        "Third item in text mode.",
    ]]
    assert report["structured_list_blocks"] >= 2
    assert report["structured_list_items"] >= 7


def test_plain_prose_with_perioded_tokens_is_not_over_split(tmp_path: Path) -> None:
    pdf_path = tmp_path / "synthetic-prose-layout.pdf"
    epub_path = tmp_path / "synthetic-prose-layout.epub"
    _build_synthetic_pdf(pdf_path)

    create_baseline_epub(
        pdf_path,
        epub_path,
        title="Synthetic Prose Layout",
        author="Tester",
        language="en",
        profile="book",
    )

    prose_section = _page_section(epub_path, "page-0003.xhtml")

    assert _section_paragraph_texts(prose_section) == [
        "Page 3",
        "This prose uses A. and B. as abbreviations and stays intact without splitting across lines.",
    ]
    assert _section_list_texts(prose_section) == []
