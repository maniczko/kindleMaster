from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from lxml import etree

from kindlemaster_manifest import resolve_publication_context, repo_relative_path
from kindlemaster_quality_score import score_epub
from kindle_semantic_cleanup import NS, finalize_epub_for_kindle_detailed
from kindlemaster_pdf_to_epub import ROOT, create_baseline_epub, extract_pdf_metadata


RUNTIME_ROOT = ROOT / "kindlemaster_runtime"
BASELINE_DIR = RUNTIME_ROOT / "output" / "baseline_epub"
FINAL_DIR = RUNTIME_ROOT / "output" / "final_epub"
RELEASE_CANDIDATE_DIR = RUNTIME_ROOT / "output" / "release_candidate"
REPORT_DIR = RUNTIME_ROOT / "output" / "reports"
CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
PAGE_DOCUMENT_PREFIX = "EPUB/xhtml/page-"
PAGE_DOCUMENT_SUFFIX = ".xhtml"


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=path.parent, suffix=path.suffix or ".tmp") as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


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


def _normalize_epub_path(path_value: str) -> str:
    parts: list[str] = []
    for part in path_value.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _resolve_epub_href(base_path: str, href: str) -> str:
    return _normalize_epub_path(str((Path(base_path).parent / href).as_posix()))


def validate_epub_bytes(epub_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        names = set(zf.namelist())
        container = etree.fromstring(zf.read("META-INF/container.xml"))
        rootfile = container.find(".//container:rootfile", CONTAINER_NS)
        opf_rel = rootfile.get("full-path") if rootfile is not None else ""
        opf_root = etree.fromstring(zf.read(opf_rel))
        nav_files = []
        toc_files = []
        for item in opf_root.findall(".//opf:manifest/opf:item", NS):
            href = item.get("href") or ""
            props = item.get("properties") or ""
            rel_path = _resolve_epub_href(opf_rel, href)
            if "nav" in props or href.endswith("nav.xhtml"):
                nav_files.append(rel_path)
            if href.endswith("toc.ncx"):
                toc_files.append(rel_path)
        xhtml_files = [name for name in names if name.endswith(".xhtml")]
        missing_stylesheets: list[str] = []
        for xhtml_name in xhtml_files:
            soup = BeautifulSoup(zf.read(xhtml_name).decode("utf-8", errors="replace"), "xml")
            for link in soup.find_all("link", rel="stylesheet"):
                href = link.get("href") or ""
                target = _resolve_epub_href(xhtml_name, href)
                if href and target not in names:
                    missing_stylesheets.append(f"{xhtml_name} -> {target}")

        nav_dead_targets: list[str] = []
        for nav_file in nav_files:
            if nav_file not in names:
                continue
            nav_soup = BeautifulSoup(zf.read(nav_file).decode("utf-8", errors="replace"), "xml")
            for anchor in nav_soup.find_all("a"):
                href = anchor.get("href") or ""
                file_part, _, fragment = href.partition("#")
                target_file = _resolve_epub_href(nav_file, file_part) if file_part else nav_file
                if target_file not in names:
                    nav_dead_targets.append(href)
                    continue
                if fragment:
                    target_soup = BeautifulSoup(zf.read(target_file).decode("utf-8", errors="replace"), "xml")
                    if target_soup.find(id=fragment) is None:
                        nav_dead_targets.append(href)

        ncx_dead_targets: list[str] = []
        for toc_file in toc_files:
            if toc_file not in names:
                continue
            toc_root = etree.fromstring(zf.read(toc_file))
            for content in toc_root.findall(".//ncx:content", NS):
                src = content.get("src") or ""
                file_part, _, fragment = src.partition("#")
                target_file = _resolve_epub_href(toc_file, file_part) if file_part else toc_file
                if target_file not in names:
                    ncx_dead_targets.append(src)
                    continue
                if fragment:
                    target_soup = BeautifulSoup(zf.read(target_file).decode("utf-8", errors="replace"), "xml")
                    if target_soup.find(id=fragment) is None:
                        ncx_dead_targets.append(src)

        title_page_missing = "EPUB/title.xhtml" not in names
        title_page_empty = False
        if not title_page_missing:
            title_soup = BeautifulSoup(zf.read("EPUB/title.xhtml").decode("utf-8", errors="replace"), "xml")
            title_page_empty = not bool(title_soup.find("h1") or title_soup.find("p"))

    missing_nav = [item for item in nav_files if item not in names]
    missing_toc = [item for item in toc_files if item not in names]
    return {
        "has_mimetype": "mimetype" in names,
        "has_container": "META-INF/container.xml" in names,
        "nav_files": nav_files,
        "toc_files": toc_files,
        "missing_nav": missing_nav,
        "missing_toc": missing_toc,
        "missing_stylesheets": missing_stylesheets,
        "nav_dead_targets": nav_dead_targets,
        "ncx_dead_targets": ncx_dead_targets,
        "title_page_missing": title_page_missing,
        "title_page_empty": title_page_empty,
        "pass": (
            "mimetype" in names
            and "META-INF/container.xml" in names
            and not missing_nav
            and not missing_toc
            and not missing_stylesheets
            and not nav_dead_targets
            and not ncx_dead_targets
            and not title_page_missing
            and not title_page_empty
        ),
    }


def summarize_page_coverage(epub_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        page_documents = [
            name
            for name in zf.namelist()
            if name.startswith(PAGE_DOCUMENT_PREFIX) and name.endswith(PAGE_DOCUMENT_SUFFIX)
        ]
    return {
        "page_document_count": len(page_documents),
        "page_documents": page_documents,
    }


def render_premium_report_markdown(quality_report: dict[str, object]) -> str:
    premium = quality_report.get("premium_report") if isinstance(quality_report.get("premium_report"), dict) else {}
    strengths = premium.get("what_is_good") if isinstance(premium.get("what_is_good"), list) else []
    risks = premium.get("what_is_bad") if isinstance(premium.get("what_is_bad"), list) else []
    strengths_md = "\n".join(f"- {item}" for item in strengths) or "- none"
    risks_md = "\n".join(f"- {item}" for item in risks) or "- none"
    return f"""# Premium Quality Report

## Score

- `score_1_10`: `{premium.get("score_1_10", quality_report.get("weighted_score"))}/10`
- `verdict`: `{premium.get("verdict", "UNKNOWN")}`
- `premium_target`: `{quality_report.get("premium_target", 8.8)}`
- `premium_gap`: `{quality_report.get("premium_gap", 0.0)}`

## What Is Good

{strengths_md}

## What Is Bad

{risks_md}
"""


def run_end_to_end(
    pdf_path: Path,
    *,
    publication_id: str | None = None,
    title: str | None = None,
    author: str | None = None,
    language: str | None = None,
    profile: str | None = None,
    force_ocr: bool = False,
    release_mode: bool = False,
    baseline_dir: Path | None = None,
    final_dir: Path | None = None,
    release_candidate_dir: Path | None = None,
    report_dir: Path | None = None,
) -> dict:
    started_at = time.perf_counter()
    detected_title, detected_author, detected_language = extract_pdf_metadata(pdf_path)
    publication = resolve_publication_context(
        pdf_path=pdf_path,
        publication_id=publication_id,
        title=title,
        author=author,
        language=language,
        fallback_title=detected_title,
        fallback_author=detected_author,
        fallback_language=detected_language,
        release_mode=release_mode,
    )
    effective_metadata = publication["effective_metadata"]
    title = effective_metadata["title"]
    author = effective_metadata["creator"]
    language = effective_metadata["language"]

    baseline_dir = (baseline_dir or BASELINE_DIR).resolve()
    final_dir = (final_dir or FINAL_DIR).resolve()
    release_candidate_dir = (release_candidate_dir or RELEASE_CANDIDATE_DIR).resolve()
    report_dir = (report_dir or REPORT_DIR).resolve()

    baseline_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    release_candidate_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    baseline_path = baseline_dir / f"{pdf_path.stem}.epub"
    final_path = final_dir / f"{pdf_path.stem}.epub"
    report_path = report_dir / f"{pdf_path.stem}-end-to-end.json"
    premium_report_path = report_dir / f"{pdf_path.stem}-premium.md"
    finalizer_artifact_root = report_dir / f"{pdf_path.stem}-finalizer-artifacts"

    baseline_report = create_baseline_epub(
        pdf_path,
        baseline_path,
        title=title,
        author=author,
        language=language,
        profile=profile,
        force_ocr=force_ocr,
    )

    baseline_bytes = baseline_path.read_bytes()
    final_bytes, finalizer_report = finalize_epub_for_kindle_detailed(
        baseline_bytes,
        title=title,
        author=author,
        language=language,
        artifact_dir=finalizer_artifact_root,
    )
    _atomic_write_bytes(final_path, final_bytes)
    baseline_coverage = summarize_page_coverage(baseline_bytes)
    final_coverage = summarize_page_coverage(final_bytes)
    source_pdf_page_count = int(baseline_report.get("pdf_page_count") or baseline_report.get("pages_total") or 0)
    coverage = {
        "source_pdf_page_count": source_pdf_page_count,
        "baseline_page_records": int(baseline_report.get("pages_total") or 0),
        "baseline_page_documents": baseline_coverage["page_document_count"],
        "final_page_documents": final_coverage["page_document_count"],
        "baseline_match": source_pdf_page_count == int(baseline_report.get("pages_total") or 0) == baseline_coverage["page_document_count"],
        "final_match": source_pdf_page_count == final_coverage["page_document_count"],
        "coverage_pass": (
            source_pdf_page_count == int(baseline_report.get("pages_total") or 0)
            and source_pdf_page_count == baseline_coverage["page_document_count"]
            and source_pdf_page_count == final_coverage["page_document_count"]
        ),
        "coverage_ratio": round(final_coverage["page_document_count"] / max(1, source_pdf_page_count), 3),
    }
    conversion_options = {
        "profile_requested": (profile or "auto-premium"),
        "profile_applied": baseline_report.get("conversion_profile_applied"),
        "force_ocr_requested": force_ocr,
        "force_ocr_applied": bool(baseline_report.get("ocr_applied")),
        "warnings": list(baseline_report.get("warnings") or []),
    }
    conversion_duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
    quality_assessment = score_epub(final_path, publication_id=publication["publication_id"])

    report = {
        "mode": "pdf_to_final_epub",
        "run_mode": "release_requested" if release_mode else "exploratory",
        "source_pdf": str(pdf_path),
        "source_pdf_relative": repo_relative_path(pdf_path),
        "baseline_epub": str(baseline_path),
        "baseline_epub_relative": repo_relative_path(baseline_path),
        "final_epub": str(final_path),
        "final_epub_relative": repo_relative_path(final_path),
        "release_candidate_epub": None,
        "release_candidate_epub_relative": None,
        "baseline_report": baseline_report,
        "baseline_validation": validate_epub_bytes(baseline_bytes),
        "final_validation": validate_epub_bytes(final_bytes),
        "finalizer_report": finalizer_report,
        "finalizer_stage_sequence": list(finalizer_report.get("stage_sequence") or []),
        "finalizer_stage_integrity": dict(finalizer_report.get("stage_integrity") or {}),
        "finalizer_navigation_proof": dict(finalizer_report.get("navigation_quality_proof") or {}),
        "finalizer_artifact_manifest": dict(finalizer_report.get("artifact_manifest") or {}),
        "finalizer_artifact_root": str(finalizer_artifact_root),
        "finalizer_artifact_root_relative": repo_relative_path(finalizer_artifact_root),
        "finalizer_artifact_manifest_path": str((finalizer_report.get("artifact_manifest") or {}).get("manifest_path") or ""),
        "finalizer_artifact_manifest_relative": repo_relative_path(Path((finalizer_report.get("artifact_manifest") or {}).get("manifest_path")).resolve()) if (finalizer_report.get("artifact_manifest") or {}).get("manifest_path") else None,
        "finalizer_stage_sequence_valid": bool((finalizer_report.get("stage_integrity") or {}).get("stage_sequence_valid")),
        "finalizer_acceptance_boundaries_ok": bool((finalizer_report.get("stage_integrity") or {}).get("acceptance_boundaries_ok")),
        "finalizer_navigation_stage_integrity_ok": bool((finalizer_report.get("stage_integrity") or {}).get("navigation_stage_integrity_ok")),
        "title": title,
        "author": author,
        "language": language,
        "conversion_duration_ms": conversion_duration_ms,
        "conversion_options": conversion_options,
        "coverage": coverage,
        "quality_assessment": quality_assessment,
        "premium_report_path": str(premium_report_path),
        "premium_report_relative": repo_relative_path(premium_report_path),
        "publication": publication,
        "artifact_lifecycle": {
            "baseline_root": repo_relative_path(baseline_dir),
            "final_root": repo_relative_path(final_dir),
            "release_candidate_root": repo_relative_path(release_candidate_dir),
            "release_candidate_created": False,
            "immutable_release_candidate_required": True,
        },
        "release_gate": {
            "requested_release_mode": release_mode,
            "manifest_required": release_mode,
            "release_eligible": publication["release_ready_metadata"] and release_mode,
            "blockers": publication["release_blockers"],
        },
    }
    if not coverage["coverage_pass"]:
        raise ValueError(
            "Page coverage check failed: not all PDF pages were preserved in the baseline/final EPUB artifacts."
        )
    _atomic_write_text(premium_report_path, render_premium_report_markdown(quality_assessment))
    _atomic_write_text(report_path, json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kindle Master end-to-end PDF to final EPUB flow")
    parser.add_argument("--pdf", required=True, help="Path to the source PDF")
    parser.add_argument("--publication-id", help="Manifest-backed publication identifier")
    parser.add_argument("--title", help="Override title")
    parser.add_argument("--author", help="Override author")
    parser.add_argument("--language", help="Optional language override for the final EPUB")
    parser.add_argument("--profile", help="Requested conversion profile: auto-premium, book, magazine, technical-study, preserve-layout")
    parser.add_argument("--force-ocr", action="store_true", help="Record OCR as requested for the run; if OCR is unavailable the report will show it explicitly")
    parser.add_argument("--release-mode", action="store_true", help="Require manifest-backed release metadata and mark run as release-requested")
    args = parser.parse_args()

    report = run_end_to_end(
        Path(args.pdf).resolve(),
        publication_id=args.publication_id,
        title=args.title,
        author=args.author,
        language=args.language,
        profile=args.profile,
        force_ocr=args.force_ocr,
        release_mode=args.release_mode,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
