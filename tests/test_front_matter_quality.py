from __future__ import annotations

from pathlib import Path
import zipfile

from kindlemaster_quality_score import analyze_front_matter, analyze_typography_ux
from kindlemaster_release_gate import run_checks


def _semantic_profile_counts(epub_path: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    with zipfile.ZipFile(epub_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".xhtml") or name.endswith("nav.xhtml"):
                continue
            text = zf.read(name).decode("utf-8", errors="replace")
            if 'data-km-profile="' not in text:
                continue
            marker = text.split('data-km-profile="', 1)[1].split('"', 1)[0]
            counts[marker] = counts.get(marker, 0) + 1
    return counts


def test_front_matter_readability_thresholds(active_release_epub: Path) -> None:
    front_matter = analyze_front_matter(active_release_epub)

    assert front_matter["front_matter_file_count"] >= 1
    assert front_matter["nav_pollution_count"] == 0
    assert front_matter["heading_noise_count"] <= 1
    assert front_matter["article_heading_leaks"] <= 1


def test_special_sections_do_not_pollute_navigation(active_release_epub: Path) -> None:
    report = run_checks(active_release_epub)

    assert report["checks"]["no_front_matter_toc_pollution"] is True
    assert report["counts"]["front_matter_target_count"] == 0
    assert report["counts"]["special_section_toc_count"] == 0
    assert report["counts"]["special_heading_count"] == 0


def test_organizational_lines_stay_out_of_heading_roles(active_release_epub: Path) -> None:
    typography = analyze_typography_ux(active_release_epub)

    assert typography["styled_classes"]["organizational_line"] is True
    assert typography["class_instances"]["organizational-line"] >= 1


def test_front_matter_pages_have_explicit_semantic_profile_markup(active_release_epub: Path) -> None:
    counts = _semantic_profile_counts(active_release_epub)

    assert counts.get("article", 0) >= 1
    assert counts.get("front_matter", 0) >= 1
    assert counts.get("toc", 0) >= 1
