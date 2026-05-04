from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urldefrag

from bs4 import BeautifulSoup

from epub_text_artifacts import analyze_epub_text_artifacts


SEND_TO_KINDLE_EMAIL_SAFE_BYTES = 50 * 1024 * 1024
SEND_TO_KINDLE_WEB_SAFE_BYTES = 200 * 1024 * 1024

NON_CONTENT_LABEL_PATTERNS = (
    r"\bgaleria\b",
    r"\breklama\b",
    r"\bmateria[łl]\s+sponsorowany\b",
    r"\badvertisement\b",
    r"\badvertorial\b",
    r"\bsponsored\b",
)
SUSPICIOUS_AUTHOR_PATTERNS = NON_CONTENT_LABEL_PATTERNS + (
    r"\bliczba\s+tygodnia\b",
    r"\bspis\s+tre[śs]ci\b",
    r"\btable\s+of\s+contents\b",
    r"\bcontents\b",
    r"\bmarketing\b",
    r"\bnauka\s+i\s+zdrowie\b",
    r"\bhistoria\s+z\s+ok[łl]adki\b",
)
GENERIC_TOC_PATTERNS = NON_CONTENT_LABEL_PATTERNS + (
    r"\bspis\s+tre[śs]ci\b",
    r"\btable\s+of\s+contents\b",
    r"\bcontents\b",
    r"\bindex\b",
    r"\bcover\b",
    r"\bdefinicje\b",
    r"\bproces\b",
    r"\bdata\b",
    r"\binput\b",
    r"\boutput\b",
    r"\bobject\s+\d+\b",
    r"\bstate\s+\d+\b",
    r"\brank\s*=",
)
UNSUPPORTED_MEDIA_TYPES = (
    "text/javascript",
    "application/javascript",
    "audio/",
    "video/",
)


@dataclass(frozen=True)
class _SpineDocument:
    file: str
    title: str
    text: str
    text_chars: int
    word_count: int
    heading_count: int
    image_count: int


def score_epub_premium_quality(
    epub_bytes: bytes,
    *,
    epubcheck: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score reader-facing EPUB quality separately from technical validity."""

    try:
        package = _read_epub_package(epub_bytes)
    except Exception as exc:  # pragma: no cover - defensive contract
        issue = _issue(
            "blocker",
            "technical_epub_unreadable",
            f"EPUB package could not be read: {exc}",
            "technical",
            "Repair ZIP/container/package structure before release audit.",
        )
        return _payload(
            scores={
                "technical_validity_score": 1.0,
                "mail_sendability_score": 1.0,
                "metadata_score": 1.0,
                "toc_quality_score": 1.0,
                "chapter_structure_score": 1.0,
                "text_artifact_score": 1.0,
                "non_content_artifact_score": 1.0,
                "kindle_readability_score": 1.0,
                "premium_score": 1.0,
            },
            issues=[issue],
            metrics={"file_size_bytes": len(epub_bytes)},
            technical_valid=False,
            mail_sendable="no",
            kindle_ready=False,
            premium_ready=False,
        )

    issues: list[dict[str, Any]] = []
    metadata = package["metadata"]
    toc_entries = package["toc_entries"]
    documents = package["documents"]
    manifest = package["manifest"]

    epubcheck_status = str((epubcheck or {}).get("status", "") or "").strip().lower()
    technical_valid = bool(package["container_ok"] and package["opf_ok"] and package["spine_ok"])
    if epubcheck_status == "failed":
        technical_valid = False
        issues.append(
            _issue(
                "blocker",
                "epubcheck_failed",
                "EPUBCheck failed; technical validation is not clean.",
                "epubcheck",
                "Repair EPUBCheck errors before any Kindle-ready decision.",
            )
        )

    title = metadata.get("title", "")
    creator = metadata.get("creator", "")
    language = metadata.get("language", "")
    if not title:
        issues.append(_metadata_issue("metadata_missing_title", "Missing reader-facing title."))
    if not creator:
        issues.append(_metadata_issue("metadata_missing_author", "Missing reader-facing author/creator."))
    elif _matches_any(creator, SUSPICIOUS_AUTHOR_PATTERNS):
        issues.append(
            _issue(
                "blocker",
                "suspicious_metadata_author",
                f"Creator looks like a magazine section or non-author label: {creator}",
                "metadata",
                "Infer creator from publisher/masthead metadata or leave it uncertain; do not use section labels as author.",
            )
        )
    if not language:
        issues.append(_metadata_issue("metadata_missing_language", "Missing EPUB language metadata."))
    if title and _looks_like_single_article_magazine_title(title, documents):
        issues.append(
            _issue(
                "review",
                "metadata_title_may_be_article_heading",
                "Title looks like a single article heading while the EPUB contains a multi-article issue.",
                "metadata",
                "Use the magazine/issue title when the EPUB represents a full issue.",
            )
        )

    non_content_docs = [_classify_non_content_document(doc) for doc in documents]
    non_content_docs = [item for item in non_content_docs if item is not None]
    for item in non_content_docs[:12]:
        issues.append(
            _issue(
                "blocker",
                "magazine_non_content_chapter",
                f"Non-content magazine fragment is present in the reading spine: {item['title'] or item['file']}",
                "chapter_structure",
                "Remove or demote pure ads, galleries, and sponsored stubs from the main reading flow.",
                file=item["file"],
            )
        )

    long_without_heading = [
        doc for doc in documents if doc.text_chars >= 900 and doc.heading_count == 0 and not _is_cover_or_nav(doc.file)
    ]
    for doc in long_without_heading[:8]:
        issues.append(
            _issue(
                "review",
                "long_chapter_without_heading",
                f"Long XHTML file has no heading structure: {doc.file}",
                "chapter_structure",
                "Review segmentation and add a real article/section heading if this is not a continuation.",
                file=doc.file,
            )
        )

    toc_noise = [_toc_noise_issue(entry) for entry in toc_entries]
    toc_noise = [issue for issue in toc_noise if issue is not None]
    issues.extend(toc_noise[:16])
    duplicate_toc_labels = [
        label for label, count in Counter(_normalize_label(entry.get("label", "")) for entry in toc_entries).items() if label and count > 1
    ]
    for label in duplicate_toc_labels[:8]:
        issues.append(
            _issue(
                "review",
                "toc_duplicate_label",
                f"TOC repeats label: {label}",
                "toc",
                "Deduplicate repeated navigation labels unless they are intentional recurring columns.",
            )
        )

    text_artifacts = analyze_epub_text_artifacts(epub_bytes)
    artifact_status = str(text_artifacts.get("status", "") or "").lower()
    if artifact_status == "failed":
        issues.append(
            _issue(
                "blocker",
                "ocr_glued_words_detected",
                (
                    "Visible text artifacts exceed premium threshold "
                    f"({text_artifacts.get('artifact_rate_per_1000_words', 0)} per 1000 words)."
                ),
                "text_artifacts",
                "Repair OCR/extraction artifacts before calling the EPUB Kindle-ready.",
            )
        )
    elif artifact_status == "passed_with_warnings":
        issues.append(
            _issue(
                "review",
                "ocr_artifacts_need_review",
                (
                    "Visible text artifacts need review "
                    f"({text_artifacts.get('artifact_rate_per_1000_words', 0)} per 1000 words)."
                ),
                "text_artifacts",
                "Sample chapters with split/glued words and verify readability.",
            )
        )
    if int((text_artifacts.get("counts") or {}).get("ocr_junk_count", 0) or 0) > 0:
        issues.append(
            _issue(
                "blocker",
                "ocr_suspicious_unicode",
                "Suspicious OCR/mojibake characters are visible in reader text.",
                "text_artifacts",
                "Normalize OCR/mojibake artifacts or mark the conversion as draft-only.",
            )
        )

    unsupported_media = [
        item
        for item in manifest
        if any(str(item.get("media_type", "")).lower().startswith(marker) for marker in UNSUPPORTED_MEDIA_TYPES)
    ]
    file_size = len(epub_bytes)
    mail_sendable = "likely"
    if file_size > SEND_TO_KINDLE_WEB_SAFE_BYTES or unsupported_media:
        mail_sendable = "no"
    elif file_size > SEND_TO_KINDLE_EMAIL_SAFE_BYTES:
        mail_sendable = "web_only"

    scores = {
        "technical_validity_score": 9.0 if technical_valid else 2.0,
        "mail_sendability_score": _mail_sendability_score(file_size=file_size, unsupported_media_count=len(unsupported_media)),
        "metadata_score": _metadata_score(metadata=metadata, issues=issues),
        "toc_quality_score": _toc_score(toc_entries=toc_entries, toc_noise_count=len(toc_noise), duplicate_count=len(duplicate_toc_labels)),
        "chapter_structure_score": _chapter_structure_score(
            documents=documents,
            non_content_count=len(non_content_docs),
            long_without_heading_count=len(long_without_heading),
        ),
        "text_artifact_score": _text_artifact_score(text_artifacts),
        "non_content_artifact_score": max(1.0, round(10.0 - (len(non_content_docs) * 1.1), 1)),
        "kindle_readability_score": 0.0,
    }
    scores["kindle_readability_score"] = round(
        (
            scores["toc_quality_score"] * 0.22
            + scores["chapter_structure_score"] * 0.24
            + scores["text_artifact_score"] * 0.28
            + scores["metadata_score"] * 0.16
            + scores["non_content_artifact_score"] * 0.10
        ),
        1,
    )
    scores["premium_score"] = round(
        (
            scores["technical_validity_score"] * 0.08
            + scores["mail_sendability_score"] * 0.07
            + scores["metadata_score"] * 0.15
            + scores["toc_quality_score"] * 0.16
            + scores["chapter_structure_score"] * 0.18
            + scores["text_artifact_score"] * 0.20
            + scores["non_content_artifact_score"] * 0.16
        ),
        1,
    )

    blocker_codes = {issue["code"] for issue in issues if issue["severity"] == "blocker"}
    kindle_ready = (
        technical_valid
        and mail_sendable in {"likely", "web_only"}
        and scores["premium_score"] >= 7.0
        and not blocker_codes
    )
    premium_ready = kindle_ready and scores["premium_score"] >= 9.0 and not any(
        issue["severity"] in {"warning", "review"} for issue in issues
    )
    if not kindle_ready:
        issues.append(
            _issue(
                "blocker",
                "kindle_ready_blocked_by_quality",
                "EPUB is technically sendable, but visible quality issues block Kindle-ready status.",
                "premium_scoring",
                "Fix metadata, TOC, reader text, and non-content chapters before labeling this EPUB Kindle Ready.",
            )
        )

    metrics = {
        "file_size_bytes": file_size,
        "toc_entry_count": len(toc_entries),
        "toc_noise_entry_count": len(toc_noise),
        "toc_duplicate_label_count": len(duplicate_toc_labels),
        "spine_document_count": len(documents),
        "non_content_chapter_count": len(non_content_docs),
        "long_chapter_without_heading_count": len(long_without_heading),
        "unsupported_media_count": len(unsupported_media),
        "text_artifacts": text_artifacts,
    }
    return _payload(
        scores=scores,
        issues=issues,
        metrics=metrics,
        technical_valid=technical_valid,
        mail_sendable=mail_sendable,
        kindle_ready=kindle_ready,
        premium_ready=premium_ready,
    )


def _read_epub_package(epub_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
        names = set(archive.namelist())
        container_ok = "META-INF/container.xml" in names
        opf_path = _find_opf_path(archive) if container_ok else ""
        opf_ok = bool(opf_path and opf_path in names)
        if not opf_ok:
            raise ValueError("Missing OPF package document.")
        opf_dir = str(PurePosixPath(opf_path).parent)
        if opf_dir == ".":
            opf_dir = ""
        opf_soup = BeautifulSoup(archive.read(opf_path), "xml")
        manifest = _parse_manifest(opf_soup)
        spine_hrefs = _parse_spine_hrefs(opf_soup, manifest)
        documents = [_read_spine_document(archive, _resolve_package_path(opf_dir, href)) for href in spine_hrefs]
        documents = [doc for doc in documents if doc is not None]
        nav_href = _find_nav_href(manifest)
        toc_entries = _read_nav_entries(archive, _resolve_package_path(opf_dir, nav_href)) if nav_href else []
        return {
            "container_ok": container_ok,
            "opf_ok": opf_ok,
            "spine_ok": bool(spine_hrefs),
            "metadata": _read_metadata(opf_soup),
            "manifest": list(manifest.values()),
            "documents": documents,
            "toc_entries": toc_entries,
        }


def _find_opf_path(archive: zipfile.ZipFile) -> str:
    soup = BeautifulSoup(archive.read("META-INF/container.xml"), "xml")
    rootfile = soup.find("rootfile")
    return str(rootfile.get("full-path", "") if rootfile else "")


def _parse_manifest(opf_soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for item in opf_soup.find_all("item"):
        item_id = str(item.get("id", "") or "")
        if not item_id:
            continue
        manifest[item_id] = {
            "id": item_id,
            "href": str(item.get("href", "") or ""),
            "properties": str(item.get("properties", "") or ""),
            "media_type": str(item.get("media-type", "") or ""),
        }
    return manifest


def _parse_spine_hrefs(opf_soup: BeautifulSoup, manifest: dict[str, dict[str, str]]) -> list[str]:
    hrefs: list[str] = []
    for itemref in opf_soup.find_all("itemref"):
        idref = str(itemref.get("idref", "") or "")
        item = manifest.get(idref) or {}
        href = item.get("href", "")
        media_type = item.get("media_type", "")
        properties = item.get("properties", "")
        linear = str(itemref.get("linear", "yes") or "yes").lower()
        if not href or media_type != "application/xhtml+xml":
            continue
        if "nav" in properties.split() or linear == "no":
            continue
        hrefs.append(href)
    return hrefs


def _find_nav_href(manifest: dict[str, dict[str, str]]) -> str:
    for item in manifest.values():
        if "nav" in item.get("properties", "").split():
            return item.get("href", "")
    return "nav.xhtml"


def _resolve_package_path(opf_dir: str, href: str) -> str:
    if not href:
        return ""
    href = urldefrag(href)[0]
    if not opf_dir:
        return href
    return str(PurePosixPath(opf_dir) / href)


def _read_metadata(opf_soup: BeautifulSoup) -> dict[str, str]:
    def first(name: str) -> str:
        node = opf_soup.find(name)
        return " ".join(node.get_text(" ", strip=True).split()) if node else ""

    return {
        "title": first("dc:title") or first("title"),
        "creator": first("dc:creator") or first("creator"),
        "language": first("dc:language") or first("language"),
        "identifier": first("dc:identifier") or first("identifier"),
        "publisher": first("dc:publisher") or first("publisher"),
        "date": first("dc:date") or first("date"),
    }


def _read_spine_document(archive: zipfile.ZipFile, path: str) -> _SpineDocument | None:
    if not path or path not in archive.namelist():
        return None
    soup = BeautifulSoup(archive.read(path), "html.parser")
    for node in soup(["script", "style", "svg", "math"]):
        node.decompose()
    heading_nodes = soup.find_all(["h1", "h2", "h3"])
    title = ""
    for heading in heading_nodes:
        title = " ".join(heading.get_text(" ", strip=True).split())
        if title:
            break
    if not title and soup.title:
        title = " ".join(soup.title.get_text(" ", strip=True).split())
    text = " ".join((soup.body or soup).get_text(" ", strip=True).split())
    return _SpineDocument(
        file=PurePosixPath(path).name,
        title=title,
        text=text,
        text_chars=len(text),
        word_count=len(re.findall(r"\b[^\W\d_]{2,}\b", text, re.UNICODE)),
        heading_count=len(heading_nodes),
        image_count=len(soup.find_all("img")),
    )


def _read_nav_entries(archive: zipfile.ZipFile, path: str) -> list[dict[str, str]]:
    if path not in archive.namelist():
        path = PurePosixPath(path).name
    if path not in archive.namelist():
        return []
    soup = BeautifulSoup(archive.read(path), "html.parser")
    toc_nav = None
    for nav in soup.find_all("nav"):
        nav_type = str(nav.get("epub:type") or nav.get("type") or "").lower()
        if "toc" in nav_type:
            toc_nav = nav
            break
    toc_nav = toc_nav or soup.find("nav")
    if toc_nav is None:
        return []
    entries: list[dict[str, str]] = []
    for anchor in toc_nav.find_all("a"):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if label:
            entries.append({"label": label, "href": str(anchor.get("href", "") or "")})
    return entries


def _metadata_issue(code: str, message: str) -> dict[str, Any]:
    return _issue("blocker", code, message, "metadata", "Fix required reader-facing metadata before release.")


def _classify_non_content_document(doc: _SpineDocument) -> dict[str, str] | None:
    blob = f"{doc.title} {doc.text[:600]}".lower()
    if _matches_any(blob, NON_CONTENT_LABEL_PATTERNS):
        return {"file": doc.file, "title": doc.title or doc.text[:80]}
    if doc.image_count >= 1 and doc.text_chars < 120 and not _is_cover_or_nav(doc.file):
        return {"file": doc.file, "title": doc.title or "image-only stub"}
    return None


def _toc_noise_issue(entry: dict[str, str]) -> dict[str, Any] | None:
    label = entry.get("label", "")
    normalized = _normalize_label(label)
    if not normalized:
        return _issue("review", "toc_non_content_entry", "Empty TOC label.", "toc", "Remove empty navigation entries.")
    if _matches_any(normalized, GENERIC_TOC_PATTERNS):
        return _issue(
            "review",
            "toc_non_content_entry",
            f"TOC contains a generic or non-content label: {label}",
            "toc",
            "Demote ads, galleries, generic labels, and technical fragments out of primary navigation.",
        )
    if len(normalized) > 90 or len(normalized.split()) > 12:
        return _issue(
            "review",
            "toc_lead_used_as_title",
            f"TOC entry looks like a lead paragraph rather than a title: {label[:120]}",
            "toc",
            "Shorten navigation labels to article or section titles.",
        )
    return None


def _looks_like_single_article_magazine_title(title: str, documents: list[_SpineDocument]) -> bool:
    if not title:
        return False
    editorial_docs = [doc for doc in documents if doc.text_chars > 800]
    return len(editorial_docs) >= 5 and title.isupper() and len(title.split()) >= 3


def _mail_sendability_score(*, file_size: int, unsupported_media_count: int) -> float:
    if unsupported_media_count:
        return 2.0
    if file_size <= SEND_TO_KINDLE_EMAIL_SAFE_BYTES:
        return 9.0
    if file_size <= SEND_TO_KINDLE_WEB_SAFE_BYTES:
        return 7.0
    return 2.0


def _metadata_score(*, metadata: dict[str, str], issues: list[dict[str, Any]]) -> float:
    score = 10.0
    codes = {issue["code"] for issue in issues}
    if "metadata_missing_title" in codes:
        score -= 4.0
    if "metadata_missing_author" in codes:
        score -= 3.0
    if "metadata_missing_language" in codes:
        score -= 3.0
    if "suspicious_metadata_author" in codes:
        score -= 5.0
    if "metadata_title_may_be_article_heading" in codes:
        score -= 1.5
    if not metadata.get("publisher"):
        score -= 0.8
    return round(max(1.0, score), 1)


def _toc_score(*, toc_entries: list[dict[str, str]], toc_noise_count: int, duplicate_count: int) -> float:
    if not toc_entries:
        return 2.0
    noise_ratio = toc_noise_count / len(toc_entries)
    score = 10.0 - (toc_noise_count * 1.4) - (noise_ratio * 4.0) - min(3.0, duplicate_count * 0.6)
    if len(toc_entries) < 4:
        score -= 2.0
    return round(max(1.0, score), 1)


def _chapter_structure_score(
    *,
    documents: list[_SpineDocument],
    non_content_count: int,
    long_without_heading_count: int,
) -> float:
    if not documents:
        return 1.0
    score = 10.0 - (non_content_count * 0.8) - (long_without_heading_count * 0.9)
    if non_content_count / max(1, len(documents)) > 0.2:
        score -= 1.5
    return round(max(1.0, score), 1)


def _text_artifact_score(text_artifacts: dict[str, Any]) -> float:
    status = str(text_artifacts.get("status", "") or "").lower()
    rate = float(text_artifacts.get("artifact_rate_per_1000_words", 0.0) or 0.0)
    score = 10.0 - min(7.0, rate * 1.4)
    if status == "failed":
        score = min(score, 4.5)
    elif status == "passed_with_warnings":
        score = min(score, 7.0)
    elif status == "unavailable":
        score = min(score, 6.0)
    return round(max(1.0, score), 1)


def _payload(
    *,
    scores: dict[str, float],
    issues: list[dict[str, Any]],
    metrics: dict[str, Any],
    technical_valid: bool,
    mail_sendable: str,
    kindle_ready: bool,
    premium_ready: bool,
) -> dict[str, Any]:
    premium_score = scores["premium_score"]
    status = "passed" if premium_ready else "passed_with_warnings" if kindle_ready else "failed"
    deduped_issues = _dedupe_issues(issues)
    issue_counts = Counter(issue["severity"] for issue in deduped_issues)
    release_verdict = "release_ready" if premium_ready else "ready_with_review" if kindle_ready else "release_blocked"
    return {
        "status": status,
        "technical_valid": technical_valid,
        "mail_sendable": mail_sendable,
        "kindle_ready": kindle_ready,
        "premium_ready": premium_ready,
        "release_verdict": release_verdict,
        **scores,
        "scores": dict(scores),
        "issue_counts": dict(issue_counts),
        "issues": deduped_issues,
        "metrics": metrics,
        "summary": (
            f"Premium score {premium_score}/10; "
            f"Kindle ready: {'yes' if kindle_ready else 'no'}; "
            f"mail sendable: {mail_sendable}."
        ),
    }


def _issue(
    severity: str,
    code: str,
    message: str,
    source: str,
    suggested_action: str,
    *,
    file: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "message": message,
        "source": source,
        "suggested_action": suggested_action,
    }
    if file:
        item["file"] = file
    return item


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for issue in issues:
        key = (
            str(issue.get("severity", "")),
            str(issue.get("code", "")),
            str(issue.get("source", "")),
            str(issue.get("message", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    normalized = _normalize_label(text)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)


def _normalize_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _is_cover_or_nav(path: str) -> bool:
    lowered = path.lower()
    return "cover" in lowered or "nav" in lowered
