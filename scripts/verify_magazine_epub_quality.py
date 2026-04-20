#!/usr/bin/env python3
"""Verify magazine EPUB quality with focus on TOC usefulness and reading flow."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup


GENERIC_TOC_LABELS = {
    "cover",
    "table of contents",
    "contents",
    "toc",
    "index",
    "back cover",
    "front cover",
    "next",
    "previous",
    "home",
}

NON_EDITORIAL_TITLE_PATTERNS = (
    r"\btable of contents\b",
    r"\bcontents\b",
    r"\btoc\b",
    r"\bindex\b",
    r"\bback cover\b",
    r"\bfront cover\b",
    r"\bcover\b",
    r"\bsample record sheet\b",
    r"\brecord sheet\b",
    r"\bother books\b",
    r"\badvertisement\b",
    r"\badvertorial\b",
    r"\bsponsored\b",
    r"\bmaterial sponsorowany\b",
    r"\bpartner\b",
    r"\breklama\b",
    r"\bgallery\b",
    r"\bappendix\b",
    r"\bcredits\b",
    r"\bcolophon\b",
    r"\bmasthead\b",
)

ISSUE_TOC_HEADING_PATTERNS = (
    r"table of contents",
    r"contents",
    r"toc",
    r"spis tre(?:sci|\u015bci)",
)


@dataclass
class LinkIssue:
    file: str
    href: str
    problem: str


@dataclass
class ChapterAnalysis:
    file: str
    title: str
    classification: str
    text_chars: int
    images: int
    headings: int


@dataclass
class VerificationReport:
    epub: str
    toc_total_links: int
    toc_useful_links: int
    toc_generic_links: int
    toc_usefulness_ratio: float
    issue_toc_entries: int
    issue_toc_covered: int
    issue_toc_coverage: float
    main_flow_chapters: int
    non_editorial_chapters: int
    non_editorial_ratio: float
    broken_links: list[LinkIssue]
    broken_assets: list[LinkIssue]
    chapter_summary: list[ChapterAnalysis]
    verdict: str


class EpubSource:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.is_dir = path.is_dir()
        self.zip = None if self.is_dir else zipfile.ZipFile(path)

    def close(self) -> None:
        if self.zip is not None:
            self.zip.close()

    def exists(self, relpath: str) -> bool:
        if self.is_dir:
            return (self.path / relpath).exists()
        assert self.zip is not None
        return relpath in self.zip.namelist()

    def read_text(self, relpath: str) -> str:
        if self.is_dir:
            return (self.path / relpath).read_text(encoding="utf-8", errors="replace")
        assert self.zip is not None
        return self.zip.read(relpath).decode("utf-8", errors="replace")

    def read_bytes(self, relpath: str) -> bytes:
        if self.is_dir:
            return (self.path / relpath).read_bytes()
        assert self.zip is not None
        return self.zip.read(relpath)

    def list_files(self) -> list[str]:
        if self.is_dir:
            return [str(p.relative_to(self.path)).replace("\\", "/") for p in self.path.rglob("*") if p.is_file()]
        assert self.zip is not None
        return self.zip.namelist()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="Path to an EPUB file or unpacked EPUB directory")
    parser.add_argument("--min-toc-links", type=int, default=5)
    parser.add_argument("--min-issue-toc-coverage", type=float, default=0.75)
    parser.add_argument("--max-non-editorial-ratio", type=float, default=0.35)
    parser.add_argument("--json", dest="json_path", type=Path, help="Write JSON report to this file")
    args = parser.parse_args()

    source = EpubSource(args.epub)
    try:
        report = verify_epub(source, args)
    finally:
        source.close()

    print_report(report)
    if args.json_path:
        args.json_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8")

    failed = (
        report.toc_total_links < args.min_toc_links
        or report.toc_usefulness_ratio < 0.5
        or (
            report.issue_toc_entries > 0
            and report.issue_toc_coverage < args.min_issue_toc_coverage
        )
        or (
            report.main_flow_chapters > 0
            and report.non_editorial_ratio > args.max_non_editorial_ratio
        )
        or bool(report.broken_links)
        or bool(report.broken_assets)
    )
    return 1 if failed else 0


def verify_epub(source: EpubSource, args: argparse.Namespace) -> VerificationReport:
    opf_path = find_opf_path(source)
    opf_dir = str(Path(opf_path).parent).replace("\\", "/")
    opf_soup = BeautifulSoup(source.read_text(opf_path), "xml")
    manifest = parse_manifest(opf_soup)
    spine = parse_spine(opf_soup)
    spine_files = resolve_spine_files(spine, manifest)
    nav_path = find_nav_path(manifest)

    toc_entries = extract_nav_toc_entries(source, nav_path, opf_dir)
    issue_toc_entries = extract_issue_toc_entries(source, spine_files, opf_dir)

    chapter_summary = []
    broken_links: list[LinkIssue] = []
    broken_assets: list[LinkIssue] = []

    for href in spine_files:
        file_path = resolve_package_path(opf_dir, href)
        if not file_path.endswith((".xhtml", ".html")):
            continue
        soup = BeautifulSoup(source.read_text(file_path), "html.parser")
        chapter_summary.append(classify_chapter(file_path, soup))
        broken_links.extend(check_internal_links(source, file_path, soup, opf_dir))
        broken_assets.extend(check_assets(source, file_path, soup, opf_dir))

    # Also check package-level manifests that are not rendered as HTML.
    if opf_path and source.exists(opf_path):
        broken_assets.extend(check_assets_from_text(source, opf_path, source.read_text(opf_path)))

    toc_total = len(toc_entries)
    toc_generic = sum(1 for entry in toc_entries if is_generic_label(entry["text"]))
    toc_useful = toc_total - toc_generic
    toc_usefulness_ratio = toc_useful / toc_total if toc_total else 0.0

    issue_covered = sum(
        1
        for entry in issue_toc_entries
        if any(titles_match(entry, toc_entry["text"]) for toc_entry in toc_entries)
    )
    issue_coverage = issue_covered / len(issue_toc_entries) if issue_toc_entries else 1.0

    main_flow = [chapter for chapter in chapter_summary if chapter.classification not in {"cover", "toc", "back-matter"}]
    non_editorial = [chapter for chapter in main_flow if chapter.classification != "editorial"]
    non_editorial_ratio = len(non_editorial) / len(main_flow) if main_flow else 0.0

    verdict = "PASS"
    if (
        toc_total < args.min_toc_links
        or toc_usefulness_ratio < 0.5
        or (issue_toc_entries and issue_coverage < args.min_issue_toc_coverage)
        or (main_flow and non_editorial_ratio > args.max_non_editorial_ratio)
        or broken_links
        or broken_assets
    ):
        verdict = "FAIL"

    return VerificationReport(
        epub=str(source.path),
        toc_total_links=toc_total,
        toc_useful_links=toc_useful,
        toc_generic_links=toc_generic,
        toc_usefulness_ratio=round(toc_usefulness_ratio, 3),
        issue_toc_entries=len(issue_toc_entries),
        issue_toc_covered=issue_covered,
        issue_toc_coverage=round(issue_coverage, 3),
        main_flow_chapters=len(main_flow),
        non_editorial_chapters=len(non_editorial),
        non_editorial_ratio=round(non_editorial_ratio, 3),
        broken_links=broken_links,
        broken_assets=broken_assets,
        chapter_summary=chapter_summary,
        verdict=verdict,
    )


def find_opf_path(source: EpubSource) -> str:
    container = BeautifulSoup(source.read_text("META-INF/container.xml"), "xml")
    rootfile = container.find("rootfile")
    if rootfile is None:
        raise RuntimeError("container.xml does not contain a rootfile entry")
    full_path = rootfile.get("full-path")
    if not full_path:
        raise RuntimeError("container.xml rootfile missing full-path")
    return full_path


def parse_manifest(opf_soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for item in opf_soup.find_all("item"):
        item_id = item.get("id", "")
        if not item_id:
            continue
        manifest[item_id] = {
            "href": item.get("href", ""),
            "properties": item.get("properties", ""),
            "media_type": item.get("media-type", ""),
        }
    return manifest


def parse_spine(opf_soup: BeautifulSoup) -> list[str]:
    spine = []
    for itemref in opf_soup.find_all("itemref"):
        idref = itemref.get("idref", "")
        if idref:
            spine.append(idref)
    return spine


def find_nav_path(manifest: dict[str, dict[str, str]]) -> str:
    for item in manifest.values():
        if "nav" in item.get("properties", "").split():
            return item["href"]
    # Fallback to common name.
    return "nav.xhtml"


def resolve_path(base_path: str, href: str) -> tuple[str | None, str | None]:
    if not href or href.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
        return None, None
    split = urlsplit(href)
    target = split.path
    fragment = split.fragment or None
    if not target:
        return None, fragment
    if target.startswith("/"):
        target = target.lstrip("/")
    base_dir = Path(base_path).parent
    resolved = (base_dir / target).as_posix()
    return resolved, fragment


def extract_nav_toc_entries(source: EpubSource, nav_path: str, opf_dir: str) -> list[dict[str, str]]:
    nav_full = nav_path if Path(nav_path).is_absolute() else str((Path(opf_dir) / nav_path).as_posix())
    if not source.exists(nav_full):
        # Some packages store nav in the same directory as the OPF, so try raw path.
        nav_full = nav_path
    soup = BeautifulSoup(source.read_text(nav_full), "html.parser")
    toc_nav = None
    for nav in soup.find_all("nav"):
        nav_type = (nav.get("epub:type") or nav.get("type") or "").lower()
        if "toc" in nav_type:
            toc_nav = nav
            break
    if toc_nav is None:
        toc_nav = soup.find("nav")
    entries: list[dict[str, str]] = []
    if toc_nav is None:
        return entries
    for link in toc_nav.find_all("a"):
        text = clean_text(link.get_text(" ", strip=True))
        href = link.get("href", "")
        if text:
            entries.append({"text": text, "href": href})
    return entries


def resolve_spine_files(spine: list[str], manifest: dict[str, dict[str, str]]) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for idref in spine:
        item = manifest.get(idref)
        if not item:
            continue
        if "nav" in item.get("properties", "").split():
            continue
        href = item.get("href", "")
        if not href or href in seen:
            continue
        seen.add(href)
        files.append(href)
    return files


def resolve_package_path(opf_dir: str, href: str) -> str:
    base = Path(opf_dir)
    if base.is_absolute():
        return str((base / href).resolve())
    return str((base / href).as_posix())


def extract_issue_toc_entries(source: EpubSource, spine_files: list[str], opf_dir: str) -> list[str]:
    issue_entries: list[str] = []
    toc_like_pages = []
    for href in spine_files:
        if not href.endswith((".xhtml", ".html")):
            continue
        path = resolve_package_path(opf_dir, href)
        if not source.exists(path):
            path = href
        soup = BeautifulSoup(source.read_text(path), "html.parser")
        if is_issue_toc_page(soup):
            toc_like_pages.append(path)
            for node in soup.select(".toc-entry, li.toc-entry, p.toc-entry"):
                raw = clean_text(node.get_text(" ", strip=True))
                cleaned = normalize_issue_toc_title(raw)
                if cleaned and not is_generic_label(cleaned):
                    issue_entries.append(cleaned)
    # Deduplicate while preserving order.
    seen = set()
    deduped = []
    for entry in issue_entries:
        key = normalize_for_match(entry)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def is_issue_toc_page(soup: BeautifulSoup) -> bool:
    headings = [clean_text(tag.get_text(" ", strip=True)) for tag in soup.find_all(["h1", "h2", "h3"])]
    heading_blob = " ".join(headings).lower()
    if any(re.search(pattern, heading_blob) for pattern in ISSUE_TOC_HEADING_PATTERNS):
        return True
    toc_entries = soup.select(".toc-entry, li.toc-entry, p.toc-entry")
    return len(toc_entries) >= 4


def normalize_issue_toc_title(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"^\d{1,3}\s*[/.:)\-]\s*", "", text)
    text = re.sub(r"^\d{1,3}\.\s*", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" .:-")
    return text


def classify_chapter(file_path: str, soup: BeautifulSoup) -> ChapterAnalysis:
    title = ""
    if soup.title and soup.title.string:
        title = clean_text(soup.title.string)
    for heading in soup.find_all(["h1", "h2", "h3"]):
        candidate = clean_text(heading.get_text(" ", strip=True))
        if candidate:
            title = candidate
            break
    body_text = clean_text(soup.body.get_text(" ", strip=True) if soup.body else soup.get_text(" ", strip=True))
    text_chars = len(body_text)
    images = len(soup.find_all("img"))
    headings = len(soup.find_all(["h1", "h2", "h3"]))
    classification = "editorial"

    lowered = f"{title} {body_text[:1200]}".lower()
    if any(re.search(pattern, lowered) for pattern in NON_EDITORIAL_TITLE_PATTERNS):
        classification = "non-editorial"
    elif len(soup.select(".toc-entry, li.toc-entry, p.toc-entry")) >= 4 or is_issue_toc_page(soup):
        classification = "toc"
    elif "cover" in file_path.lower() and images >= 1 and text_chars < 250:
        classification = "cover"
    elif "back" in lowered and "cover" in lowered:
        classification = "back-matter"
    elif images >= 1 and text_chars < 220:
        classification = "non-editorial"
    elif text_chars < 120 and headings <= 1 and images == 0:
        classification = "non-editorial"

    return ChapterAnalysis(
        file=file_path,
        title=title,
        classification=classification,
        text_chars=text_chars,
        images=images,
        headings=headings,
    )


def check_internal_links(source: EpubSource, file_path: str, soup: BeautifulSoup, opf_dir: str) -> list[LinkIssue]:
    issues: list[LinkIssue] = []
    for tag in soup.find_all(["a", "link", "img", "image"]):
        href = tag.get("href") or tag.get("src") or ""
        if not href:
            continue
        target_path, fragment = resolve_path(file_path, href)
        if target_path is None:
            continue
        if target_path.startswith("http") or target_path.startswith("mailto:"):
            continue
        if not source.exists(target_path):
            issues.append(LinkIssue(file=file_path, href=href, problem="missing target file"))
            continue
        if fragment and target_path.endswith((".xhtml", ".html", ".htm")):
            target_soup = BeautifulSoup(source.read_text(target_path), "html.parser")
            if not target_soup.find(id=fragment) and not target_soup.find(attrs={"name": fragment}):
                issues.append(LinkIssue(file=file_path, href=href, problem="missing fragment target"))
    return issues


def check_assets(source: EpubSource, file_path: str, soup: BeautifulSoup, opf_dir: str) -> list[LinkIssue]:
    issues: list[LinkIssue] = []
    for tag in soup.find_all(["img", "image", "source", "link"]):
        attr = "src" if tag.name in {"img", "image", "source"} else "href"
        href = tag.get(attr, "")
        if not href or href.startswith(("http://", "https://", "data:", "mailto:")):
            continue
        target_path, _ = resolve_path(file_path, href)
        if target_path is None:
            continue
        if not source.exists(target_path):
            issues.append(LinkIssue(file=file_path, href=href, problem=f"missing {attr} asset"))
    return issues


def check_assets_from_text(source: EpubSource, file_path: str, raw_text: str) -> list[LinkIssue]:
    issues: list[LinkIssue] = []
    for match in re.finditer(r'''(?:href|src)=["']([^"']+)["']''', raw_text, re.IGNORECASE):
        href = match.group(1)
        if href.startswith(("http://", "https://", "mailto:", "data:")):
            continue
        target_path, _ = resolve_path(file_path, href)
        if target_path is None:
            continue
        if not source.exists(target_path):
            issues.append(LinkIssue(file=file_path, href=href, problem="missing package asset"))
    return issues


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_match(text: str) -> str:
    cleaned = clean_text(text).lower()
    cleaned = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_generic_label(text: str) -> bool:
    normalized = normalize_for_match(text)
    if not normalized:
        return True
    if normalized in GENERIC_TOC_LABELS:
        return True
    if re.fullmatch(r"(page|chapter|section)\s*\d+", normalized):
        return True
    if re.fullmatch(r"\d+", normalized):
        return True
    return False


def titles_match(left: str, right: str) -> bool:
    a = normalize_for_match(left)
    b = normalize_for_match(right)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 8 and len(b) >= 8 and (a in b or b in a):
        return True
    return False


def print_report(report: VerificationReport) -> None:
    print(f"EPUB: {report.epub}")
    print(f"Verdict: {report.verdict}")
    print("")
    print("TOC usefulness")
    print(f"  links total: {report.toc_total_links}")
    print(f"  useful links: {report.toc_useful_links}")
    print(f"  generic links: {report.toc_generic_links}")
    print(f"  usefulness ratio: {report.toc_usefulness_ratio:.3f}")
    print("")
    print("Issue TOC coverage")
    print(f"  extracted entries: {report.issue_toc_entries}")
    print(f"  covered by nav: {report.issue_toc_covered}")
    print(f"  coverage: {report.issue_toc_coverage:.3f}")
    print("")
    print("Main flow ratio")
    print(f"  main-flow chapters: {report.main_flow_chapters}")
    print(f"  non-editorial chapters: {report.non_editorial_chapters}")
    print(f"  non-editorial ratio: {report.non_editorial_ratio:.3f}")
    print("")
    print("Broken links/assets")
    if not report.broken_links and not report.broken_assets:
        print("  none")
    else:
        for issue in report.broken_links:
            print(f"  link: {issue.file} -> {issue.href} ({issue.problem})")
        for issue in report.broken_assets:
            print(f"  asset: {issue.file} -> {issue.href} ({issue.problem})")
    print("")
    print("Chapter summary")
    for chapter in report.chapter_summary[:20]:
        print(
            f"  {chapter.classification:12} {chapter.file} | "
            f"text={chapter.text_chars} images={chapter.images} headings={chapter.headings} | {chapter.title}"
        )
    if len(report.chapter_summary) > 20:
        print(f"  ... {len(report.chapter_summary) - 20} more chapters")


if __name__ == "__main__":
    raise SystemExit(main())
