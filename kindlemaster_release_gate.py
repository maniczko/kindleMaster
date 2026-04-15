from __future__ import annotations

import argparse
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree
from kindlemaster_image_layout_audit import analyze_image_layout
from kindlemaster_structured_lists import detect_inline_ordered_list
from kindlemaster_text_audit import count_joined_word_candidates, count_split_word_matches


OPF_NS = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
PAGE_LABEL_RE = re.compile(r"^(?:strona|page|s\.)\s*\d{1,4}(?:\s*/\s*\d{1,4})?$", re.IGNORECASE)
HASHY_TITLE_RE = re.compile(r"^[a-f0-9]{16,}$", re.IGNORECASE)
SLUG_TITLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
SPECIAL_SECTION_RE = re.compile(
    r"(?:spis tre|bibliografia|strefa |zesp[Ăło][Ĺ‚l]|sponsor|partner|czĹ‚onkostwo|czlonkostwo|kontakt:|projekt i sk)",
    re.IGNORECASE,
)
ROLE_RE = re.compile(r"(?:redaktor|redaktorka|naczeln|dyrektor|autorka|autor)", re.IGNORECASE)
TITLE_PAGE_NAMES = {"title.xhtml"}
TRUNCATED_NAV_END_WORDS = {"do", "ku", "od"}
SPECIAL_SEMANTIC_PROFILES = {"front_matter", "toc", "back_matter", "promo"}


def normalize_href(base_dir: str, href: str) -> str:
    if not href:
        return ""
    href_only = href.split("#", 1)[0]
    if not href_only:
        return base_dir
    return (Path(base_dir) / href_only).resolve().as_posix()


def relative_to_epub_root(epub_root: Path, abs_path_posix: str) -> str:
    return Path(abs_path_posix).relative_to(epub_root.resolve()).as_posix()


def extract_opf_metadata(zf: zipfile.ZipFile, opf_name: str) -> dict[str, str]:
    opf_root = etree.fromstring(zf.read(opf_name))
    title = "".join(opf_root.findtext(".//dc:title", default="", namespaces=OPF_NS)).strip()
    creator = "".join(opf_root.findtext(".//dc:creator", default="", namespaces=OPF_NS)).strip()
    language = "".join(opf_root.findtext(".//dc:language", default="", namespaces=OPF_NS)).strip()
    return {"title": title, "creator": creator, "language": language}


def count_text_quality(zf: zipfile.ZipFile, xhtml_names: list[str]) -> dict:
    split_words = 0
    joined_boundaries = 0
    page_labels = 0
    heading_merges = 0
    flattened_inline_lists = 0
    for name in xhtml_names:
        soup = BeautifulSoup(zf.read(name), "xml")
        for tag in soup.find_all(["h1", "h2", "h3", "p"]):
            text = " ".join(tag.get_text(" ", strip=True).split())
            if not text:
                continue
            split_words += count_split_word_matches(text)
            joined_boundaries += count_joined_word_candidates(text)
            if detect_inline_ordered_list(text):
                flattened_inline_lists += 1
            if PAGE_LABEL_RE.match(text):
                page_labels += 1
            if tag.name == "h1" and (ROLE_RE.search(text) or PAGE_LABEL_RE.search(text) or len(text) > 160):
                heading_merges += 1
    return {
        "split_word_count": split_words,
        "joined_word_boundary_count": joined_boundaries,
        "page_label_count": page_labels,
        "title_merge_count": heading_merges,
        "flattened_inline_list_count": flattened_inline_lists,
    }


def is_title_page(name: str) -> bool:
    return Path(name).name.lower() in TITLE_PAGE_NAMES


def is_suspicious_nav_label(text: str) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return False
    if cleaned.endswith((",", ";", ":", "-", "\u2013", "\u2014", "(", "/")):
        return True
    if cleaned.count("(") > cleaned.count(")"):
        return True
    words = [word for word in re.split(r"\s+", cleaned) if any(ch.isalpha() for ch in word)]
    if 1 < len(words) <= 4 and words[-1].strip(" ,;:!?").lower() in TRUNCATED_NAV_END_WORDS:
        return True
    for marker in ("?", "!"):
        marker_index = cleaned.rfind(marker)
        if marker_index == -1 or marker_index == len(cleaned) - 1:
            continue
        tail = cleaned[marker_index + 1 :].strip()
        tail_words = [word for word in re.split(r"\s+", tail) if any(ch.isalpha() for ch in word)]
        if 0 < len(tail_words) <= 2 and len(tail) <= 18:
            return True
    return False


def extract_semantic_profile(soup: BeautifulSoup) -> str | None:
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


def run_checks(epub_path: Path) -> dict:
    epub_root = Path("_virtual_epub_root").resolve()
    image_layout = analyze_image_layout(epub_path)
    with zipfile.ZipFile(epub_path) as zf:
        names = set(zf.namelist())
        opf_candidates = [name for name in names if name.endswith(".opf")]
        if not opf_candidates:
            raise RuntimeError("No OPF found in EPUB")
        opf_name = sorted(opf_candidates)[0]
        metadata = extract_opf_metadata(zf, opf_name)

        xhtml_names = sorted(name for name in names if name.endswith(".xhtml") and not name.endswith("nav.xhtml"))
        nav_names = sorted(name for name in names if name.endswith("nav.xhtml"))
        ncx_names = sorted(name for name in names if name.endswith(".ncx"))

        h1_count = 0
        special_heading_count = 0
        missing_stylesheets = []
        nav_dead_targets = []
        ncx_dead_targets = []
        toc_items: list[str] = []
        semantic_profiles: dict[str, str] = {}
        nav_targets: list[str] = []

        for name in xhtml_names:
            soup = BeautifulSoup(zf.read(name), "xml")
            semantic_profiles[name] = extract_semantic_profile(soup) or "article"
            h1_count += len(soup.find_all("h1"))
            if not is_title_page(name):
                for heading in soup.find_all(["h1", "h2", "h3"]):
                    text = " ".join(heading.get_text(" ", strip=True).split())
                    if SPECIAL_SECTION_RE.search(text) or PAGE_LABEL_RE.match(text):
                        special_heading_count += 1
            base_dir = str((epub_root / Path(name).parent).resolve())
            for link in soup.find_all("link", href=True):
                href_target = normalize_href(base_dir, link["href"])
                rel = relative_to_epub_root(epub_root, href_target)
                if rel not in names:
                    missing_stylesheets.append({"file": name, "href": link["href"], "resolved": rel})

        if nav_names:
            nav_name = nav_names[0]
            soup = BeautifulSoup(zf.read(nav_name), "xml")
            base_dir = str((epub_root / Path(nav_name).parent).resolve())
            for anchor in soup.find_all("a", href=True):
                toc_items.append(" ".join(anchor.get_text(" ", strip=True).split()))
                href = anchor["href"]
                href_path, _, fragment = href.partition("#")
                target_abs = normalize_href(base_dir, href_path)
                target_rel = relative_to_epub_root(epub_root, target_abs)
                nav_targets.append(target_rel)
                if target_rel not in names:
                    nav_dead_targets.append({"href": href, "reason": "missing_file"})
                    continue
                if fragment:
                    target_soup = BeautifulSoup(zf.read(target_rel), "xml")
                    if target_soup.find(id=fragment) is None:
                        nav_dead_targets.append({"href": href, "reason": "missing_anchor"})

        if ncx_names:
            ncx_name = ncx_names[0]
            ncx_root = etree.fromstring(zf.read(ncx_name))
            base_dir = str((epub_root / Path(ncx_name).parent).resolve())
            for content in ncx_root.findall(".//{http://www.daisy.org/z3986/2005/ncx/}content"):
                href = content.get("src", "")
                href_path, _, fragment = href.partition("#")
                target_abs = normalize_href(base_dir, href_path)
                target_rel = relative_to_epub_root(epub_root, target_abs)
                if target_rel not in names:
                    ncx_dead_targets.append({"href": href, "reason": "missing_file"})
                    continue
                if fragment:
                    target_soup = BeautifulSoup(zf.read(target_rel), "xml")
                    if target_soup.find(id=fragment) is None:
                        ncx_dead_targets.append({"href": href, "reason": "missing_anchor"})

        quality = count_text_quality(zf, xhtml_names)
        duplicate_low_value_entries = sum(count - 1 for count in Counter(toc_items).values() if count > 1)
        page_label_toc_count = sum(1 for item in toc_items if PAGE_LABEL_RE.match(item))
        author_only_noise_count = sum(1 for item in toc_items if ROLE_RE.search(item) and len(item.split()) <= 4)
        special_section_toc_count = sum(1 for item in toc_items if SPECIAL_SECTION_RE.search(item))
        suspicious_nav_label_count = sum(1 for item in toc_items if is_suspicious_nav_label(item))
        front_matter_target_count = sum(
            1 for target in nav_targets if target in semantic_profiles and semantic_profiles[target] in SPECIAL_SEMANTIC_PROFILES
        )

        checks = {
            "no_opaque_or_slug_human_title": not (
                HASHY_TITLE_RE.match(metadata["title"]) or SLUG_TITLE_RE.match(metadata["title"])
            ),
            "creator_not_unknown_for_release": bool(metadata["creator"]) and metadata["creator"].lower() != "unknown",
            "valid_h1_presence_where_applicable": h1_count > 0,
            "no_title_author_lead_page_merge": quality["title_merge_count"] == 0,
            "special_sections_not_articles": special_heading_count == 0,
            "valid_nav_paths": len(nav_dead_targets) == 0,
            "valid_ncx_paths": len(ncx_dead_targets) == 0,
            "valid_anchors": len(nav_dead_targets) == 0 and len(ncx_dead_targets) == 0,
            "no_duplicate_low_value_entries": duplicate_low_value_entries == 0,
            "no_page_label_dominance": page_label_toc_count == 0,
            "no_author_only_noise": author_only_noise_count == 0,
            "no_front_matter_toc_pollution": front_matter_target_count == 0 and special_section_toc_count == 0,
            "no_truncated_or_dangling_nav_labels": suspicious_nav_label_count == 0,
            "no_nonpackaged_stylesheet_reference": len(missing_stylesheets) == 0,
            "no_flattened_inline_ordered_lists": quality["flattened_inline_list_count"] == 0,
            "no_image_only_toc_targets": image_layout["nav_target_to_image_only_count"] == 0,
            "no_page_like_toc_targets": image_layout["nav_target_to_page_like_count"] == 0,
            "no_unjustified_image_fallback": image_layout["unjustified_fallback_count"] == 0,
            "all_page_image_fallbacks_classified_and_justified": (
                image_layout["unjustified_fallback_count"] == 0
                and (
                    image_layout["justified_fallback_count"]
                    == image_layout["hybrid_illustrated_file_count"] + image_layout["image_fallback_file_count"]
                    or image_layout["text_first_file_count"] == image_layout["xhtml_file_count"]
                )
            ),
        }

        return {
            "epub": str(epub_path),
            "metadata": metadata,
            "counts": {
                "h1_count": h1_count,
                "toc_entry_count": len(toc_items),
                "duplicate_low_value_entries": duplicate_low_value_entries,
                "page_label_toc_count": page_label_toc_count,
                "author_only_noise_count": author_only_noise_count,
                "special_section_toc_count": special_section_toc_count,
                "suspicious_nav_label_count": suspicious_nav_label_count,
                "front_matter_target_count": front_matter_target_count,
                "image_only_file_count": image_layout["image_only_file_count"],
                "page_like_file_count": image_layout["page_like_file_count"],
                "text_first_file_count": image_layout["text_first_file_count"],
                "hybrid_illustrated_file_count": image_layout["hybrid_illustrated_file_count"],
                "image_fallback_file_count": image_layout["image_fallback_file_count"],
                "justified_fallback_count": image_layout["justified_fallback_count"],
                "unjustified_fallback_count": image_layout["unjustified_fallback_count"],
                "image_only_toc_target_count": image_layout["nav_target_to_image_only_count"],
                "page_like_toc_target_count": image_layout["nav_target_to_page_like_count"],
                "special_heading_count": special_heading_count,
                **quality,
            },
            "checks": checks,
            "failed_checks": [name for name, passed in checks.items() if not passed],
            "nav_dead_targets": nav_dead_targets,
            "ncx_dead_targets": ncx_dead_targets,
            "missing_stylesheets": missing_stylesheets,
            "toc_items": toc_items,
            "image_layout": image_layout,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kindle Master release-gate smoke checks")
    parser.add_argument("--epub", required=True, help="Path to EPUB file")
    parser.add_argument("--report-out", help="Optional JSON report output path")
    args = parser.parse_args()

    report = run_checks(Path(args.epub).resolve())
    if args.report_out:
        out = Path(args.report_out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
