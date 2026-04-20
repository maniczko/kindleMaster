from __future__ import annotations

import argparse
import io
import json
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from lxml import etree

from converter import ConversionConfig, convert_pdf_to_epub_with_report
from epub_heading_repair import repair_epub_headings_and_toc
from publication_analysis import analyze_publication


@dataclass(frozen=True)
class CorpusCase:
    path: Path
    document_class: str
    notes: str = ""
    analysis_only: bool = False


CORPUS: list[CorpusCase] = [
    CorpusCase(
        Path("example/02695ab2e05aab728b4b995caa682f947e8be2c3291ff490579797c5a3cc5e26.pdf"),
        document_class="magazine-layout",
        notes="Layout-heavy editorial PDF with tables and image-led pages.",
    ),
    CorpusCase(
        Path("example/BABOK_Guide_v3_Member.pdf"),
        document_class="dense-business-guide",
        notes="Long business-analysis handbook with deep structure and heavy semantics.",
    ),
    CorpusCase(
        Path("example/The Woodpecker Method ( PDFDrive ).pdf"),
        document_class="training-book-diagram",
        notes="Exercise/solution training book with diagrams and bidirectional navigation pressure.",
    ),
    CorpusCase(
        Path("example/tactits_sample_80pages.pdf"),
        document_class="diagram-heavy-book",
        notes="Diagram-heavy tactics sample with dense image-first content.",
    ),
    CorpusCase(
        Path("example/scan_probe.pdf"),
        document_class="ocr-probe-scan",
        notes="Small scan/OCR probe used to catch text-layer and cleanup regressions.",
    ),
]

ANALYSIS_ONLY: list[CorpusCase] = [
    CorpusCase(
        Path("example/tactits.pdf"),
        document_class="large-diagram-corpus",
        notes="Large analysis-only stress case for profile detection.",
        analysis_only=True,
    ),
]

PLACEHOLDER_TITLE_RE = re.compile(r"^(?:emvc|executive summary|unknown|untitled|legacy)$", re.IGNORECASE)
PLACEHOLDER_AUTHOR_RE = re.compile(r"^(?:unknown|python-docx|legacy)$", re.IGNORECASE)
VISIBLE_JUNK_PATTERNS = {
    "unresolved_url_label": re.compile(r"Unresolved URL:", re.IGNORECASE),
    "manual_review_label": re.compile(r"Link requires manual review\.", re.IGNORECASE),
    "half_url_https_the": re.compile(r"https?://the(?:\b|[./])", re.IGNORECASE),
    "broken_percent_tail": re.compile(r"https?://[^\s\"']+%2(?:\b|[^\da-fA-F])"),
}
BROKEN_HREF_PATTERNS = {
    "glued_protocol": re.compile(r"https?://https?://", re.IGNORECASE),
    "half_url_https_the": re.compile(r"https?://the(?:\b|[./])", re.IGNORECASE),
    "orphan_percent_tail": re.compile(r"%2(?:\b|[^\da-fA-F])"),
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _resolve_opf_name(names: list[str]) -> str | None:
    for candidate in names:
        if candidate.endswith(".opf"):
            return candidate
    return None


def _extract_nav_depth(ol_tag) -> int:
    if ol_tag is None:
        return 0
    depths = [1]
    for li_tag in ol_tag.find_all("li"):
        nested = li_tag.find("ol")
        if nested is not None:
            depths.append(1 + _extract_nav_depth(nested))
    return max(depths, default=1)


def inspect_epub(epub_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as archive:
        names = archive.namelist()
        xhtml_names = [name for name in names if name.endswith(".xhtml")]
        content_xhtml = [name for name in xhtml_names if not name.endswith("nav.xhtml") and not name.endswith("cover.xhtml")]
        image_names = [name for name in names if re.search(r"\.(png|jpg|jpeg|gif|svg)$", name, re.IGNORECASE)]

        opf_name = _resolve_opf_name(names)
        package_title = ""
        package_creator = ""
        package_language = ""
        if opf_name:
            try:
                tree = etree.fromstring(archive.read(opf_name))
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                package_title = "".join(tree.xpath("string(//*[local-name()='metadata']/*[local-name()='title'][1])")).strip()
                package_creator = "".join(tree.xpath("string(//*[local-name()='metadata']/*[local-name()='creator'][1])")).strip()
                package_language = "".join(tree.xpath("string(//*[local-name()='metadata']/*[local-name()='language'][1])")).strip()
            except Exception:
                package_title = ""
                package_creator = ""
                package_language = ""

        nav_entry_count = 0
        nav_depth = 0
        toc_labels: list[str] = []
        if "EPUB/nav.xhtml" in names:
            nav_soup = BeautifulSoup(archive.read("EPUB/nav.xhtml"), "xml")
            toc_nav = nav_soup.find("nav", attrs={"epub:type": "toc"}) or nav_soup.find("nav", {"type": "toc"})
            toc_root = toc_nav.find("ol") if toc_nav else None
            nav_entry_count = len(toc_root.find_all("li")) if toc_root else 0
            nav_depth = _extract_nav_depth(toc_root)
            toc_labels = [
                " ".join(a_tag.get_text(" ", strip=True).split())
                for a_tag in (toc_root.find_all("a") if toc_root else [])
                if a_tag.get_text(" ", strip=True)
            ]

        visible_junk_counts = Counter()
        broken_href_counts = Counter()
        heading_counts = Counter()
        broken_internal_anchors = 0

        for xhtml_name in xhtml_names:
            try:
                soup = BeautifulSoup(archive.read(xhtml_name), "xml")
            except Exception:
                continue
            full_text = soup.get_text(" ", strip=True)
            for key, pattern in VISIBLE_JUNK_PATTERNS.items():
                visible_junk_counts[key] += len(pattern.findall(full_text))
            for heading_tag in ("h1", "h2", "h3"):
                heading_counts[heading_tag] += len(soup.find_all(heading_tag))
            ids = {tag.get("id") for tag in soup.find_all(True) if tag.get("id")}
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"].strip()
                for key, pattern in BROKEN_HREF_PATTERNS.items():
                    if pattern.search(href):
                        broken_href_counts[key] += 1
                if href.startswith("#") and href[1:] and href[1:] not in ids:
                    broken_internal_anchors += 1

        return {
            "xhtml_count": len(content_xhtml),
            "image_count": len(image_names),
            "nav_entries": nav_entry_count,
            "nav_depth": nav_depth,
            "toc_labels_sample": toc_labels[:10],
            "package_title": package_title,
            "package_creator": package_creator,
            "package_language": package_language,
            "metadata_placeholder_title": bool(package_title and PLACEHOLDER_TITLE_RE.fullmatch(package_title.strip())),
            "metadata_placeholder_creator": bool(package_creator and PLACEHOLDER_AUTHOR_RE.fullmatch(package_creator.strip())),
            "visible_junk_counts": dict(visible_junk_counts),
            "broken_href_counts": dict(broken_href_counts),
            "broken_internal_anchors": broken_internal_anchors,
            "heading_counts": dict(heading_counts),
        }


def _build_case_blockers(
    *,
    quality: dict[str, Any],
    inspect: dict[str, Any],
    heading_summary: dict[str, Any],
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if quality.get("validation_status") != "passed":
        blockers.append({"code": "epubcheck_failed", "detail": str(quality.get("validation_status"))})
    if sum(inspect.get("visible_junk_counts", {}).values()) > 0:
        blockers.append({"code": "visible_reference_or_url_junk", "detail": json.dumps(inspect.get("visible_junk_counts", {}), ensure_ascii=False)})
    if sum(inspect.get("broken_href_counts", {}).values()) > 0:
        blockers.append({"code": "broken_href_patterns", "detail": json.dumps(inspect.get("broken_href_counts", {}), ensure_ascii=False)})
    if inspect.get("broken_internal_anchors", 0) > 0:
        blockers.append({"code": "broken_internal_anchors", "detail": str(inspect.get("broken_internal_anchors", 0))})
    if inspect.get("metadata_placeholder_title"):
        blockers.append({"code": "placeholder_title", "detail": inspect.get("package_title", "")})
    if inspect.get("metadata_placeholder_creator"):
        blockers.append({"code": "placeholder_creator", "detail": inspect.get("package_creator", "")})
    if heading_summary.get("epubcheck_status") == "failed":
        blockers.append({"code": "heading_repair_epubcheck_failed", "detail": "Heading/TOC repair broke EPUB validity."})
    return blockers


def _build_case_warnings(
    *,
    summary: dict[str, Any],
    quality: dict[str, Any],
    inspect: dict[str, Any],
    heading_summary: dict[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    text_cleanup = quality.get("text_cleanup") or {}
    review_needed = _safe_int(text_cleanup.get("review_needed_count", 0))
    blocked = _safe_int(text_cleanup.get("blocked_count", 0))
    if review_needed >= 200:
        warnings.append({"code": "high_review_noise", "detail": f"review_needed_count={review_needed}"})
    if blocked >= 500:
        warnings.append({"code": "high_blocked_noise", "detail": f"blocked_count={blocked}"})
    if inspect.get("nav_entries", 0) <= 2 and _safe_int(summary.get("section_count", 0)) >= 5:
        warnings.append({"code": "shallow_toc", "detail": f"nav_entries={inspect.get('nav_entries', 0)} section_count={summary.get('section_count', 0)}"})
    if heading_summary.get("release_status") == "fail":
        warnings.append({"code": "heading_release_fail", "detail": "Heading/TOC repair did not reach release-ready status."})
    elif heading_summary.get("release_status") == "pass_with_review":
        warnings.append({"code": "heading_manual_review", "detail": f"manual_review_count={heading_summary.get('manual_review_count', 0)}"})
    if inspect.get("package_language", "").lower() not in {"pl", "en"}:
        warnings.append({"code": "unexpected_language", "detail": inspect.get("package_language", "")})
    return warnings


def _derive_case_grade(blockers: list[dict[str, str]], warnings: list[dict[str, str]]) -> str:
    if blockers:
        return "fail"
    if warnings:
        return "pass_with_review"
    return "pass"


def _run_analysis_only_case(case: CorpusCase) -> dict[str, Any]:
    if not case.path.exists():
        return {"file": str(case.path), "document_class": case.document_class, "status": "missing"}
    analysis = analyze_publication(str(case.path), preferred_profile="auto-premium")
    return {
        "file": case.path.name,
        "document_class": case.document_class,
        "mode": "analysis-only",
        "notes": case.notes,
        "analysis": analysis.to_dict() if hasattr(analysis, "to_dict") else analysis,
    }


def _run_conversion_case(case: CorpusCase, *, run_heading_repair: bool) -> dict[str, Any]:
    if not case.path.exists():
        return {"file": str(case.path), "document_class": case.document_class, "status": "missing"}

    result = convert_pdf_to_epub_with_report(
        str(case.path),
        config=ConversionConfig(profile="auto-premium"),
        original_filename=case.path.name,
    )
    analysis = result["analysis"]
    summary = result.get("document_summary", {})
    quality = result.get("quality_report", {})
    converted_epub_bytes = result["epub_bytes"]
    inspect = inspect_epub(converted_epub_bytes)

    heading_summary: dict[str, Any] = {
        "status": "skipped",
        "release_status": "skipped",
        "toc_entries_before": inspect.get("nav_entries", 0),
        "toc_entries_after": inspect.get("nav_entries", 0),
        "headings_removed": 0,
        "manual_review_count": 0,
        "epubcheck_status": "unavailable",
    }
    repaired_inspect = inspect
    if run_heading_repair:
        heading_result = repair_epub_headings_and_toc(
            converted_epub_bytes,
            title_hint=str(summary.get("title", "")),
            author_hint=str(summary.get("author", "")),
            language_hint=str(inspect.get("package_language", "")),
            publication_profile=(analysis.profile if hasattr(analysis, "profile") else None),
        )
        heading_summary = {
            "status": "completed",
            "release_status": heading_result.summary.get("release_status", "unavailable"),
            "toc_entries_before": heading_result.summary.get("toc_entries_before", inspect.get("nav_entries", 0)),
            "toc_entries_after": heading_result.summary.get("toc_entries_after", inspect.get("nav_entries", 0)),
            "headings_removed": heading_result.summary.get("headings_removed", 0),
            "manual_review_count": heading_result.summary.get("manual_review_count", 0),
            "epubcheck_status": heading_result.summary.get("epubcheck_status", "unavailable"),
        }
        repaired_inspect = inspect_epub(heading_result.epub_bytes)

    blockers = _build_case_blockers(
        quality=quality,
        inspect=repaired_inspect,
        heading_summary=heading_summary,
    )
    warnings = _build_case_warnings(
        summary=summary,
        quality=quality,
        inspect=repaired_inspect,
        heading_summary=heading_summary,
    )
    grade = _derive_case_grade(blockers, warnings)

    return {
        "file": case.path.name,
        "document_class": case.document_class,
        "mode": "convert-and-audit",
        "notes": case.notes,
        "analysis": analysis.to_dict() if hasattr(analysis, "to_dict") else analysis,
        "summary": summary,
        "quality": quality,
        "epub_stats": inspect,
        "heading_repair": heading_summary,
        "post_heading_epub_stats": repaired_inspect,
        "grade": grade,
        "blockers": blockers,
        "warnings": warnings,
        "size_bytes": len(converted_epub_bytes),
    }


def _build_overall_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    converted = [row for row in rows if row.get("mode") == "convert-and-audit"]
    grade_counts = Counter(row.get("grade", "unknown") for row in converted)
    blocker_counts = Counter(blocker["code"] for row in converted for blocker in row.get("blockers", []))
    warning_counts = Counter(warning["code"] for row in converted for warning in row.get("warnings", []))
    class_grade = {
        row["document_class"]: row.get("grade", "unknown")
        for row in converted
    }
    return {
        "converted_case_count": len(converted),
        "analysis_only_case_count": len([row for row in rows if row.get("mode") == "analysis-only"]),
        "grade_counts": dict(grade_counts),
        "blocker_counts": dict(blocker_counts),
        "warning_counts": dict(warning_counts),
        "class_grade": class_grade,
    }


def _build_markdown_report(rows: list[dict[str, Any]], overall: dict[str, Any]) -> str:
    lines = [
        "# Premium Corpus Smoke",
        "",
        "## Summary",
        "",
        f"- Converted cases: {overall['converted_case_count']}",
        f"- Analysis-only cases: {overall['analysis_only_case_count']}",
        f"- Grade counts: `{json.dumps(overall['grade_counts'], ensure_ascii=False)}`",
        f"- Repeated blockers: `{json.dumps(overall['blocker_counts'], ensure_ascii=False)}`",
        f"- Repeated warnings: `{json.dumps(overall['warning_counts'], ensure_ascii=False)}`",
        "",
        "## Cases",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                f"### {row.get('file')}",
                "",
                f"- Class: `{row.get('document_class', 'unknown')}`",
                f"- Mode: `{row.get('mode', 'unknown')}`",
            ]
        )
        if row.get("notes"):
            lines.append(f"- Notes: {row['notes']}")
        if row.get("mode") == "analysis-only":
            analysis = row.get("analysis", {})
            lines.extend(
                [
                    f"- Detected profile: `{analysis.get('profile', 'unknown')}`",
                    f"- Reason: {analysis.get('profile_reason', '')}",
                    "",
                ]
            )
            continue
        quality = row.get("quality", {})
        text_cleanup = quality.get("text_cleanup") or {}
        heading_repair = row.get("heading_repair") or {}
        epub_stats = row.get("post_heading_epub_stats") or row.get("epub_stats") or {}
        lines.extend(
            [
                f"- Grade: `{row.get('grade', 'unknown')}`",
                f"- EPUBCheck: `{quality.get('validation_status', 'unavailable')}`",
                f"- Profile: `{(row.get('analysis') or {}).get('profile', 'unknown')}`",
                f"- TOC entries/depth: `{epub_stats.get('nav_entries', 0)}` / `{epub_stats.get('nav_depth', 0)}`",
                f"- Metadata: title=`{epub_stats.get('package_title', '')}`, creator=`{epub_stats.get('package_creator', '')}`, language=`{epub_stats.get('package_language', '')}`",
                f"- Text cleanup: auto=`{text_cleanup.get('auto_fix_count', 0)}` review=`{text_cleanup.get('review_needed_count', 0)}` blocked=`{text_cleanup.get('blocked_count', 0)}`",
                f"- Reference cleanup: `{json.dumps((text_cleanup.get('reference_cleanup') or {}), ensure_ascii=False)}`",
                f"- Heading repair: release=`{heading_repair.get('release_status', 'skipped')}` toc `{heading_repair.get('toc_entries_before', 0)} -> {heading_repair.get('toc_entries_after', 0)}` removed=`{heading_repair.get('headings_removed', 0)}` review=`{heading_repair.get('manual_review_count', 0)}`",
            ]
        )
        if row.get("blockers"):
            lines.append("- Blockers:")
            for blocker in row["blockers"]:
                lines.append(f"  - `{blocker['code']}`: {blocker['detail']}")
        if row.get("warnings"):
            lines.append("- Warnings:")
            for warning in row["warnings"]:
                lines.append(f"  - `{warning['code']}`: {warning['detail']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _persist_reports(*, rows: list[dict[str, Any]], json_path: Path, md_path: Path) -> None:
    overall = _build_overall_summary(rows)
    payload = {"overall": overall, "cases": rows}
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_build_markdown_report(rows, overall), encoding="utf-8")


def _case_matches_filters(case: CorpusCase, filters: list[str]) -> bool:
    if not filters:
        return True
    haystacks = [case.path.name.lower(), case.document_class.lower(), case.notes.lower()]
    return any(any(token in haystack for haystack in haystacks) for token in filters)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run premium KindleMaster corpus smoke across mixed document classes.")
    parser.add_argument("--output-json", default="reports/premium_corpus_smoke_report.json")
    parser.add_argument("--output-md", default="reports/premium_corpus_smoke_report.md")
    parser.add_argument("--skip-heading-repair", action="store_true")
    parser.add_argument("--case", action="append", default=[], help="Run only matching cases by filename or document class.")
    args = parser.parse_args()

    case_filters = [token.strip().lower() for token in args.case if token.strip()]
    json_path = Path(args.output_json)
    md_path = Path(args.output_md)
    rows: list[dict[str, Any]] = []
    for case in ANALYSIS_ONLY:
        if not _case_matches_filters(case, case_filters):
            continue
        print(f"[analysis-only] {case.path.name} ({case.document_class})")
        rows.append(_run_analysis_only_case(case))
        _persist_reports(rows=rows, json_path=json_path, md_path=md_path)
    for case in CORPUS:
        if not _case_matches_filters(case, case_filters):
            continue
        print(f"[convert] {case.path.name} ({case.document_class})")
        rows.append(_run_conversion_case(case, run_heading_repair=not args.skip_heading_repair))
        _persist_reports(rows=rows, json_path=json_path, md_path=md_path)

    overall = _build_overall_summary(rows)
    payload = {"overall": overall, "cases": rows}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
