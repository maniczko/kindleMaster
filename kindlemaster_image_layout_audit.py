from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from bs4 import BeautifulSoup


PAGE_LABEL_RE = re.compile(r"^(?:strona|page|s\.)\s*\d{1,4}(?:\s*/\s*\d{1,4})?$", re.IGNORECASE)
SPECIAL_FALLBACK_PROFILES = {"front_matter", "toc", "back_matter", "promo"}


def _ordered_xhtml_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(
        name
        for name in zf.namelist()
        if name.endswith(".xhtml") and not name.endswith("nav.xhtml") and not name.endswith("title.xhtml")
    )


def _normalize_epub_href(base_name: str, href: str) -> str:
    base = PurePosixPath(base_name)
    target = (base.parent / href.split("#", 1)[0]).as_posix()
    parts: list[str] = []
    for part in target.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _extract_semantic_profile(soup: BeautifulSoup) -> str | None:
    for tag in filter(None, [soup.find("body"), soup.find("section")]):
        data_profile = (tag.get("data-km-profile") or "").strip()
        if data_profile:
            return data_profile
        classes = tag.get("class") or []
        for class_name in classes:
            if isinstance(class_name, str) and class_name.startswith("km-profile-"):
                return class_name.removeprefix("km-profile-").replace("-", "_")
        epub_type = " ".join(tag.get("epub:type", "").split()).lower()
        if " toc" in f" {epub_type} " or epub_type.endswith(" toc"):
            return "toc"
        if "frontmatter" in epub_type:
            return "front_matter"
        if "backmatter" in epub_type:
            return "back_matter"
        if "bodymatter" in epub_type:
            return "article"
    return None


def _extract_page_kind_hint(soup: BeautifulSoup) -> str | None:
    for tag in filter(None, [soup.find("body"), soup.find("section")]):
        value = (tag.get("data-km-page-kind") or "").strip()
        if value:
            return value
    return None


def _summarize_xhtml(name: str, soup: BeautifulSoup) -> dict[str, object]:
    paragraphs = [
        " ".join(tag.get_text(" ", strip=True).split())
        for tag in soup.find_all("p")
        if " ".join(tag.get_text(" ", strip=True).split())
    ]
    prose_paragraphs = [text for text in paragraphs if not PAGE_LABEL_RE.match(text)]
    headings = [
        " ".join(tag.get_text(" ", strip=True).split())
        for tag in soup.find_all(["h1", "h2", "h3"])
        if " ".join(tag.get_text(" ", strip=True).split())
    ]
    figures = soup.find_all("figure")
    images = soup.find_all("img")
    figcaptions = sum(1 for figure in figures if figure.find("figcaption"))
    prose_chars = sum(len(text) for text in prose_paragraphs)
    semantic_profile = _extract_semantic_profile(soup) or "article"
    page_kind_hint = _extract_page_kind_hint(soup)
    image_only = bool(images) and prose_chars == 0 and not headings
    page_like = bool(images) and prose_chars <= 140 and len(headings) <= 1
    return {
        "name": name,
        "semantic_profile": semantic_profile,
        "page_kind_hint": page_kind_hint,
        "paragraph_count": len(paragraphs),
        "prose_paragraph_count": len(prose_paragraphs),
        "prose_chars": prose_chars,
        "heading_count": len(headings),
        "figure_count": len(figures),
        "image_count": len(images),
        "figcaption_count": figcaptions,
        "image_only": image_only,
        "page_like": page_like,
    }


def _classify_rendering(summary: dict[str, object]) -> str:
    page_kind_hint = str(summary.get("page_kind_hint") or "").strip()
    if page_kind_hint in {"text_first", "hybrid_illustrated", "image_fallback"}:
        return page_kind_hint
    if bool(summary.get("image_only")):
        return "image_fallback"
    if bool(summary.get("page_like")) and str(summary.get("semantic_profile") or "") in SPECIAL_FALLBACK_PROFILES:
        return "image_fallback"
    if bool(summary.get("page_like")):
        return "hybrid_illustrated"
    return "text_first"


def _fallback_reason(summary: dict[str, object], rendering: str) -> str | None:
    if rendering == "text_first":
        return None
    semantic_profile = str(summary.get("semantic_profile") or "")
    if semantic_profile == "promo":
        return "advertisement"
    if semantic_profile in {"front_matter", "toc", "back_matter"}:
        return "true_non_reflowable_layout"
    if bool(summary.get("figure_count")) and int(summary.get("prose_chars") or 0) == 0:
        return "illustration_only"
    return "safe_text_reconstruction_impossible"


def _fallback_justified(summary: dict[str, object], rendering: str) -> bool:
    if rendering == "text_first":
        return False
    semantic_profile = str(summary.get("semantic_profile") or "")
    if semantic_profile in SPECIAL_FALLBACK_PROFILES:
        return True
    if (
        bool(summary.get("figure_count"))
        and int(summary.get("prose_chars") or 0) == 0
        and int(summary.get("heading_count") or 0) == 0
    ):
        return True
    return False


def analyze_image_layout(epub_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(epub_path) as zf:
        xhtml_names = _ordered_xhtml_names(zf)
        summaries: dict[str, dict[str, object]] = {}
        for name in xhtml_names:
            soup = BeautifulSoup(zf.read(name), "xml")
            summaries[name] = _summarize_xhtml(name, soup)

        nav_targets: list[str] = []
        nav_labels: list[str] = []
        if "EPUB/nav.xhtml" in zf.namelist():
            nav_soup = BeautifulSoup(zf.read("EPUB/nav.xhtml"), "xml")
            for anchor in nav_soup.find_all("a", href=True):
                nav_targets.append(_normalize_epub_href("EPUB/nav.xhtml", anchor["href"]))
                nav_labels.append(" ".join(anchor.get_text(" ", strip=True).split()))

        image_only_targets = [target for target in nav_targets if bool((summaries.get(target) or {}).get("image_only"))]
        page_like_targets = [target for target in nav_targets if bool((summaries.get(target) or {}).get("page_like"))]
        total_files = max(1, len(xhtml_names))
        image_only_file_count = sum(1 for summary in summaries.values() if bool(summary["image_only"]))
        page_like_file_count = sum(1 for summary in summaries.values() if bool(summary["page_like"]))
        figure_file_count = sum(1 for summary in summaries.values() if int(summary["figure_count"]) > 0)
        figure_count = sum(int(summary["figure_count"]) for summary in summaries.values())
        figcaption_count = sum(int(summary["figcaption_count"]) for summary in summaries.values())
        rendering_by_file = {name: _classify_rendering(summary) for name, summary in summaries.items()}
        justified_fallback_files = [
            name
            for name, summary in summaries.items()
            if _fallback_justified(summary, rendering_by_file[name])
        ]
        unjustified_fallback_files = [
            name
            for name, summary in summaries.items()
            if rendering_by_file[name] != "text_first" and not _fallback_justified(summary, rendering_by_file[name])
        ]
        text_first_file_count = sum(1 for rendering in rendering_by_file.values() if rendering == "text_first")
        hybrid_illustrated_file_count = sum(
            1 for rendering in rendering_by_file.values() if rendering == "hybrid_illustrated"
        )
        image_fallback_file_count = sum(1 for rendering in rendering_by_file.values() if rendering == "image_fallback")
        image_layout_pass = (
            len(image_only_targets) == 0
            and len(page_like_targets) == 0
            and len(unjustified_fallback_files) == 0
        )
        return {
            "epub": str(epub_path),
            "xhtml_file_count": len(xhtml_names),
            "figure_file_count": figure_file_count,
            "figure_count": figure_count,
            "figcaption_count": figcaption_count,
            "figure_without_figcaption_count": max(0, figure_count - figcaption_count),
            "image_only_file_count": image_only_file_count,
            "page_like_file_count": page_like_file_count,
            "image_only_ratio": round(image_only_file_count / total_files, 3),
            "page_like_ratio": round(page_like_file_count / total_files, 3),
            "text_first_file_count": text_first_file_count,
            "hybrid_illustrated_file_count": hybrid_illustrated_file_count,
            "image_fallback_file_count": image_fallback_file_count,
            "nav_target_count": len(nav_targets),
            "nav_targets_to_image_only": image_only_targets,
            "nav_targets_to_page_like": page_like_targets,
            "nav_target_to_image_only_count": len(image_only_targets),
            "nav_target_to_page_like_count": len(page_like_targets),
            "fallback_reason_by_file": {
                name: _fallback_reason(summary, rendering_by_file[name])
                for name, summary in summaries.items()
                if rendering_by_file[name] != "text_first"
            },
            "justified_fallback_files": justified_fallback_files,
            "unjustified_fallback_files": unjustified_fallback_files,
            "justified_fallback_count": len(justified_fallback_files),
            "unjustified_fallback_count": len(unjustified_fallback_files),
            "nav_labels_sample": nav_labels[:8],
            "semantic_profile_counts": {
                profile: sum(1 for summary in summaries.values() if summary["semantic_profile"] == profile)
                for profile in sorted({str(summary["semantic_profile"]) for summary in summaries.values()})
            },
            "image_layout_pass": image_layout_pass,
        }
