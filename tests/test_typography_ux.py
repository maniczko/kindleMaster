from __future__ import annotations

import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from kindlemaster_quality_score import analyze_typography_ux


def _first_h1_file(epub_path: Path) -> tuple[str, BeautifulSoup]:
    with zipfile.ZipFile(epub_path) as zf:
        for name in sorted(zf.namelist()):
            if (
                not name.endswith(".xhtml")
                or name.endswith("nav.xhtml")
                or name.endswith("title.xhtml")
            ):
                continue
            soup = BeautifulSoup(zf.read(name), "xml")
            if soup.find("h1"):
                return name, soup
    raise AssertionError("No h1-bearing chapter found in the active release EPUB.")


def test_typography_baseline_is_kindle_safe(active_release_epub: Path) -> None:
    typography = analyze_typography_ux(active_release_epub)

    assert 1.35 <= typography["body_line_height"] <= 1.8
    assert typography["heading_hierarchy_pass"] is True
    assert typography["title_author_lead_distinction_pass"] is True
    assert typography["page_marker_hidden"] is True
    assert typography["ux_pass"] is True


def test_semantic_support_classes_exist_and_are_used(active_release_epub: Path) -> None:
    typography = analyze_typography_ux(active_release_epub)

    assert typography["styled_classes"]["byline"] is True
    assert typography["styled_classes"]["lead"] is True
    assert typography["styled_classes"]["figcaption"] is True
    assert typography["class_instances"]["byline"] >= 1
    assert typography["class_instances"]["lead"] >= 1


def test_first_article_opening_has_clean_entry_structure(active_release_epub: Path) -> None:
    _, soup = _first_h1_file(active_release_epub)

    body = soup.find("body")
    assert body is not None

    entry_tags = [tag for tag in body.find_all(["h1", "p"], recursive=True) if tag.get_text(" ", strip=True)]
    assert entry_tags, "Expected a readable opening sequence in the first article file."
    h1_index = next((index for index, tag in enumerate(entry_tags[:4]) if tag.name == "h1"), None)
    assert h1_index is not None, "Expected a visible h1 near the top of the first article opening."
    following = entry_tags[h1_index + 1 : h1_index + 5]
    assert any("lead" in (tag.get("class") or []) or "byline" in (tag.get("class") or []) for tag in following)
