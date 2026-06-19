from __future__ import annotations

import re
import copy
import hashlib
import zipfile
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urldefrag

from bs4 import BeautifulSoup

from epub_text_artifacts import analyze_epub_text_artifacts
from quality_policy import (
    MAGAZINE_PREMIUM_TARGET_SCORE,
    MAGAZINE_READER_ARTIFACT_RATE_MAX,
    MAGAZINE_TOC_COVERAGE_MIN,
    magazine_premium_thresholds,
)


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
    r"^(?:zrodlo|źródło|source|credit)\s*[:.-]",
    r"^(?:pic|picture|figure|fig|rysunek|rys\.|tabela|table)\s*\.?\s+\d+\b",
)
LANGUAGE_LABEL_CONTAMINATION_THRESHOLD = 2
POLISH_STRUCTURAL_LABEL_PATTERNS = (
    ("Co to jest", r"\bco\s+to\s+jest\b"),
    ("Jak działa", r"\bjak\s+dzia[łl]a\b"),
    ("Przykład", r"\bprzyk[łl]ad\b"),
    ("Definicje", r"\bdefinicje\b"),
    ("Reklama", r"\breklama\b"),
    ("Galeria", r"\bgaleria\b"),
    ("Materiał sponsorowany", r"\bmateria[łl]\s+sponsorowany\b"),
)
UNSUPPORTED_MEDIA_TYPES = (
    "text/javascript",
    "application/javascript",
    "audio/",
    "video/",
)
EPUBCHECK_NON_LINEAR_UNREACHABLE_RE = re.compile(
    r"\bOPF-096\b|non[-\s]?linear|unreachable|not\s+reachable",
    re.IGNORECASE,
)
_EPUB_PACKAGE_CACHE: dict[str, dict[str, Any]] = {}


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
    demoted_non_content_page_count = int(package.get("demoted_non_content_page_count", 0) or 0)

    epubcheck_status = str((epubcheck or {}).get("status", "") or "").strip().lower()
    epubcheck_messages = _epubcheck_messages(epubcheck)
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
        if _has_non_linear_unreachable_epubcheck_error(epubcheck_messages):
            issues.append(
                _issue(
                    "blocker",
                    "epubcheck_non_linear_unreachable",
                    "EPUBCheck reports unreachable non-linear spine content.",
                    "epubcheck",
                    "Remove non-linear fragments from spine or link them from an appendix/navigation section.",
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

    language_label_contamination = _detect_language_label_contamination(
        language=language,
        documents=documents,
        toc_entries=toc_entries,
    )
    if language_label_contamination["hit_count"] > LANGUAGE_LABEL_CONTAMINATION_THRESHOLD:
        labels = ", ".join(language_label_contamination["labels"][:5])
        issues.append(
            _issue(
                "blocker",
                "language_label_contamination",
                f"English EPUB contains Polish structural labels above threshold: {labels}.",
                "language",
                "Regenerate magazine structural labels for language=en and verify the EPUB language metadata.",
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
        label
        for label, count in Counter(
            _normalize_label(entry.get("label", ""))
            for entry in toc_entries
            if not _is_standard_structural_toc_entry(entry)
        ).items()
        if label and count > 1
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
    dense_handbook_nav = _dense_handbook_navigation_quality(documents=documents, toc_entries=toc_entries)
    for label in dense_handbook_nav.get("toc_noise_samples", [])[:8]:
        issues.append(
            _issue(
                "review",
                "dense_handbook_toc_noise",
                f"Dense handbook TOC contains procedural or mapping debris: {label}",
                "toc",
                "Keep only chapter, section, and meaningful technique entries in Kindle navigation.",
            )
        )
    if dense_handbook_nav.get("heading_noise_count", 0):
        issues.append(
            _issue(
                "review",
                "dense_handbook_heading_noise",
                (
                    "Dense handbook heading hierarchy contains repeated micro-headings "
                    f"({dense_handbook_nav.get('heading_noise_count', 0)} candidates)."
                ),
                "heading",
                "Demote repeated Elements/Guidelines/Strengths/Limitations headings unless they are unique sections.",
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
    score_caps = _premium_score_caps(
        technical_valid=technical_valid,
        epubcheck_status=epubcheck_status,
        scores=scores,
        issues=issues,
    )
    if score_caps:
        scores["premium_score"] = min(scores["premium_score"], min(score_caps))

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
        "demoted_non_content_page_count": demoted_non_content_page_count,
        "long_chapter_without_heading_count": len(long_without_heading),
        "language_label_contamination": language_label_contamination,
        "unsupported_media_count": len(unsupported_media),
        "text_artifacts": text_artifacts,
        "dense_handbook_navigation_summary": dense_handbook_nav,
        "epubcheck_message_count": len(epubcheck_messages),
        "premium_score_caps": score_caps,
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


def build_magazine_premium_quality_contract(
    *,
    premium_scoring: Mapping[str, Any] | None = None,
    magazine_audit: Mapping[str, Any] | None = None,
    validation_status: str = "",
    text_artifacts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the magazine-specific 9/10 contract from deterministic evidence."""

    scoring = dict(premium_scoring or {})
    audit = dict(magazine_audit or {})
    article_map = dict(audit.get("article_map") or audit.get("magazine_article_map") or {})
    artifacts = dict(text_artifacts or (scoring.get("metrics") or {}).get("text_artifacts") or {})
    issues: list[dict[str, Any]] = []

    validation = str(validation_status or scoring.get("status") or "").strip().lower()
    technical_valid = bool(scoring.get("technical_valid", True))
    premium_score = _float_metric(scoring.get("premium_score"), 0.0)
    artifact_rate = _float_metric(artifacts.get("artifact_rate_per_1000_words"), 0.0)
    artifact_count = int(_float_metric(artifacts.get("artifact_count"), 0.0))
    artifact_word_count = _float_metric(artifacts.get("word_count"), 0.0)
    artifact_counts = artifacts.get("counts") if isinstance(artifacts.get("counts"), Mapping) else {}
    suspicious_url_fragment_count = int(_float_metric(artifact_counts.get("suspicious_url_fragment_count"), 0.0))
    reader_artifact_count = max(0, artifact_count - suspicious_url_fragment_count)
    reader_artifact_rate = artifact_rate
    if artifact_word_count > 0 and artifact_count:
        reader_artifact_rate = round((reader_artifact_count / artifact_word_count) * 1000.0, 3)
    toc_coverage = _float_metric(article_map.get("toc_coverage"), 1.0)
    editorial_count = int(_float_metric(article_map.get("editorial_article_count"), 0.0))
    front_matter_ok = bool(article_map.get("front_matter_before_articles", True))
    non_content_count = int(_float_metric(article_map.get("non_content_chapter_count"), 0.0))
    truncated_title_count = int(_float_metric(article_map.get("truncated_title_count"), 0.0))
    high_risk_article_count = int(_float_metric(article_map.get("high_risk_article_count"), 0.0))
    low_resolution_image_count = int(_float_metric(article_map.get("low_resolution_image_count"), 0.0))

    if not technical_valid or validation == "failed":
        issues.append(
            _issue(
                "blocker",
                "magazine_epub_validation_not_clean",
                "Magazine EPUB is not technically clean enough for premium release.",
                "magazine_premium_quality",
                "Repair EPUBCheck/internal validation before premium-ready status.",
            )
        )
    if editorial_count >= 4 and toc_coverage < MAGAZINE_TOC_COVERAGE_MIN:
        issues.append(
            _issue(
                "blocker",
                "magazine_article_coverage_low",
                f"Magazine TOC covers {toc_coverage:.0%} of editorial articles; premium requires at least {MAGAZINE_TOC_COVERAGE_MIN:.0%}.",
                "magazine_premium_quality",
                "Rebuild article segmentation and TOC coverage from the issue structure.",
            )
        )
    if not front_matter_ok:
        issues.append(
            _issue(
                "blocker",
                "magazine_spine_order_fragmented",
                "Magazine front matter appears after editorial articles.",
                "magazine_premium_quality",
                "Keep cover/front matter/issue contents before article flow or mark it intentionally non-linear.",
            )
        )
    if reader_artifact_rate >= MAGAZINE_READER_ARTIFACT_RATE_MAX:
        issues.append(
            _issue(
                "blocker",
                "magazine_text_artifact_rate_high",
                f"Magazine reader text artifact rate is {reader_artifact_rate:g}/1000 words; premium requires < {MAGAZINE_READER_ARTIFACT_RATE_MAX:g}.",
                "magazine_premium_quality",
                "Repair OCR, dehyphenation, and reading-order artifacts before premium release.",
            )
        )
    if suspicious_url_fragment_count:
        issues.append(
            _issue(
                "review",
                "magazine_url_fragment_review",
                f"{suspicious_url_fragment_count} URL-like fragments need link or masthead review.",
                "magazine_premium_quality",
                "Keep URL/contact fragments out of body OCR scoring, but verify links and masthead text manually.",
            )
        )
    if premium_score and premium_score < MAGAZINE_PREMIUM_TARGET_SCORE:
        issues.append(
            _issue(
                "review",
                "magazine_premium_score_below_9",
                f"Magazine premium score is {premium_score:g}/10; premium target is {MAGAZINE_PREMIUM_TARGET_SCORE:g}/10+.",
                "magazine_premium_quality",
                "Improve deterministic magazine structure, metadata, TOC, images, and text cleanup.",
            )
        )
    if non_content_count:
        issues.append(
            _issue(
                "review",
                "magazine_non_editorial_sections_present",
                f"Magazine contains {non_content_count} non-editorial sections that need deliberate placement.",
                "magazine_premium_quality",
                "Keep ads, galleries, newsletters, and sponsor pages outside accidental primary reading flow.",
            )
        )
    if truncated_title_count:
        issues.append(
            _issue(
                "review",
                "magazine_article_title_truncated",
                f"{truncated_title_count} article titles look weak, truncated, or lead-like.",
                "magazine_premium_quality",
                "Recover concise article titles from issue TOC, page headings, or source outline.",
            )
        )
    if high_risk_article_count:
        issues.append(
            _issue(
                "review",
                "magazine_article_segmentation_needs_review",
                f"{high_risk_article_count} article segments carry high-risk layout flags.",
                "magazine_premium_quality",
                "Sample high-risk article boundaries and add generic segmentation regressions.",
            )
        )
    if low_resolution_image_count:
        issues.append(
            _issue(
                "review",
                "image_low_resolution_for_kindle",
                f"{low_resolution_image_count} images may be too small for Kindle chart/diagram reading.",
                "magazine_premium_quality",
                "Re-export important figures at higher resolution or provide a full-size fallback.",
            )
        )

    deduped = _dedupe_issues(issues)
    blockers = [item for item in deduped if item["severity"] == "blocker"]
    reviews = [item for item in deduped if item["severity"] == "review"]
    status = "failed" if blockers else "passed_with_warnings" if reviews else "passed"
    return {
        "status": status,
        "premium_ready": status == "passed" and premium_score >= MAGAZINE_PREMIUM_TARGET_SCORE,
        "target_score": MAGAZINE_PREMIUM_TARGET_SCORE,
        "thresholds": magazine_premium_thresholds(),
        "metrics": {
            "premium_score": premium_score,
            "editorial_article_count": editorial_count,
            "toc_coverage": toc_coverage,
            "front_matter_before_articles": front_matter_ok,
            "non_content_chapter_count": non_content_count,
            "truncated_title_count": truncated_title_count,
            "high_risk_article_count": high_risk_article_count,
            "low_resolution_image_count": low_resolution_image_count,
            "artifact_rate_per_1000_words": artifact_rate,
            "reader_text_artifact_rate_per_1000_words": reader_artifact_rate,
            "artifact_count": artifact_count,
            "reader_text_artifact_count": reader_artifact_count,
            "suspicious_url_fragment_count": suspicious_url_fragment_count,
        },
        "issues": deduped,
        "issue_counts": dict(Counter(issue["severity"] for issue in deduped)),
        "article_map": article_map,
    }


def refresh_magazine_article_map_from_epub(
    article_map: Mapping[str, Any],
    epub_bytes: bytes,
) -> dict[str, Any]:
    """Refresh magazine TOC coverage against the final EPUB navigation.

    Magazine extraction builds an article map before later heading/nav repair. The
    final EPUB may contain recovered article links that the early source-TOC pass
    could not see, so premium gating should evaluate the final nav when bytes are
    available.
    """

    refreshed = dict(article_map or {})
    rows = [dict(row) for row in refreshed.get("articles", []) if isinstance(row, Mapping)]
    if not rows:
        return refreshed
    try:
        package = _read_epub_package(epub_bytes)
    except Exception:
        return refreshed
    nav_labels = [
        str(entry.get("label", "") or "")
        for entry in package.get("toc_entries", [])
        if isinstance(entry, Mapping) and str(entry.get("label", "") or "").strip()
    ]
    if not nav_labels:
        return refreshed

    for row in rows:
        kind = str(row.get("kind", "") or "")
        if kind not in {"article", "interview"} or row.get("toc_excluded"):
            continue
        title = str(row.get("title", "") or "")
        if not title:
            continue
        row["toc_matched"] = bool(row.get("toc_matched")) or any(
            _magazine_title_similarity(title, label) >= 0.52 for label in nav_labels
        )

    editorial_rows = [
        row
        for row in rows
        if str(row.get("kind", "") or "") in {"article", "interview"} and not bool(row.get("toc_excluded"))
    ]
    editorial_count = len(editorial_rows)
    toc_covered = sum(1 for row in editorial_rows if bool(row.get("toc_matched")))
    toc_coverage = round(toc_covered / editorial_count, 3) if editorial_count else 1.0

    refreshed["articles"] = rows
    refreshed["toc_entry_count"] = len(nav_labels)
    refreshed["toc_covered_article_count"] = toc_covered
    refreshed["editorial_article_count"] = editorial_count
    refreshed["toc_coverage"] = toc_coverage
    refreshed["toc_missing_articles"] = [row for row in editorial_rows if not row.get("toc_matched")][:20]
    refreshed["coverage_source"] = "final_epub_nav"
    blockers = [str(item) for item in refreshed.get("blockers", []) if str(item)]
    if editorial_count >= 4 and toc_coverage < MAGAZINE_TOC_COVERAGE_MIN:
        if "magazine_article_toc_coverage_below_95" not in blockers:
            blockers.append("magazine_article_toc_coverage_below_95")
    else:
        blockers = [item for item in blockers if item != "magazine_article_toc_coverage_below_95"]
    refreshed["blockers"] = blockers
    review = [str(item) for item in refreshed.get("review", []) if str(item)]
    refreshed["status"] = "failed" if blockers else "passed_with_warnings" if review else "passed"
    return refreshed


def apply_magazine_premium_quality_to_scoring(
    premium_scoring: Mapping[str, Any],
    magazine_contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge magazine-specific blockers into the generic premium scoring payload."""

    contract = dict(magazine_contract or {})
    if not contract:
        return dict(premium_scoring or {})
    scoring = dict(premium_scoring or {})
    contract_issues = [dict(item) for item in contract.get("issues", []) if isinstance(item, Mapping)]
    if not contract_issues:
        scoring["magazine_premium_quality"] = contract
        return scoring
    issues = [dict(item) for item in scoring.get("issues", []) if isinstance(item, Mapping)]
    issues.extend(contract_issues)
    issues = _dedupe_issues(issues)
    has_blockers = any(item.get("severity") == "blocker" for item in issues)
    if has_blockers:
        scoring["kindle_ready"] = False
        scoring["premium_ready"] = False
        scoring["release_verdict"] = "release_blocked"
        scoring["status"] = "failed"
        scoring["premium_score"] = min(_float_metric(scoring.get("premium_score"), 0.0), 8.0)
    elif str(contract.get("status", "")) == "passed_with_warnings":
        scoring["premium_ready"] = False
        if scoring.get("release_verdict") == "release_ready":
            scoring["release_verdict"] = "ready_with_review"
        if scoring.get("status") == "passed":
            scoring["status"] = "passed_with_warnings"
    scoring["issues"] = issues
    scoring["issue_counts"] = dict(Counter(issue["severity"] for issue in issues))
    scoring["magazine_premium_quality"] = contract
    scores = dict(scoring.get("scores") or {})
    if scores:
        scores["premium_score"] = scoring.get("premium_score", scores.get("premium_score"))
        scoring["scores"] = scores
    return scoring


def _read_epub_package(epub_bytes: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(epub_bytes).hexdigest()
    cached = _EPUB_PACKAGE_CACHE.get(digest)
    if cached is not None:
        return copy.deepcopy(cached)

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
        demoted_non_content_count = _parse_demoted_non_content_page_count(opf_soup, manifest)
        spine_hrefs = _parse_spine_hrefs(opf_soup, manifest)
        documents = [_read_spine_document(archive, _resolve_package_path(opf_dir, href)) for href in spine_hrefs]
        documents = [doc for doc in documents if doc is not None]
        nav_href = _find_nav_href(manifest)
        toc_entries = _read_nav_entries(archive, _resolve_package_path(opf_dir, nav_href)) if nav_href else []
        result = {
            "container_ok": container_ok,
            "opf_ok": opf_ok,
            "spine_ok": bool(spine_hrefs),
            "metadata": _read_metadata(opf_soup),
            "manifest": list(manifest.values()),
            "documents": documents,
            "toc_entries": toc_entries,
            "demoted_non_content_page_count": demoted_non_content_count,
        }
        _EPUB_PACKAGE_CACHE[digest] = copy.deepcopy(result)
        return result


def _epubcheck_messages(epubcheck: dict[str, Any] | None) -> list[str]:
    raw_messages = (epubcheck or {}).get("messages") or []
    if not isinstance(raw_messages, list):
        raw_messages = [raw_messages]
    return [str(message) for message in raw_messages if str(message).strip()]


def _has_non_linear_unreachable_epubcheck_error(messages: list[str]) -> bool:
    for message in messages:
        normalized = " ".join(message.split())
        lowered = normalized.lower()
        if "opf-096" in lowered:
            return True
        if EPUBCHECK_NON_LINEAR_UNREACHABLE_RE.search(normalized):
            has_non_linear = "non-linear" in lowered or "nonlinear" in lowered
            has_unreachable = "unreachable" in lowered or "not reachable" in lowered
            if has_non_linear and has_unreachable:
                return True
    return False


def _magazine_title_similarity(left: str, right: str) -> float:
    left_norm = _normalize_magazine_title_for_match(left)
    right_norm = _normalize_magazine_title_for_match(right)
    if not left_norm or not right_norm:
        return 0.0
    if len(left_norm) >= 6 and len(right_norm) >= 6 and (left_norm in right_norm or right_norm in left_norm):
        return 1.0
    left_tokens = set(_magazine_title_tokens(left_norm))
    right_tokens = set(_magazine_title_tokens(right_norm))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(min(len(left_tokens), len(right_tokens)), 1)


def _normalize_magazine_title_for_match(value: str) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "").casefold()).strip()
    normalized = re.sub(r"[^\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def _magazine_title_tokens(value: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[^\W_]{3,}", value, flags=re.UNICODE)
        if token not in {"tekst", "foto", "fot", "rys", "oraz", "dla", "przez", "with", "and", "the"}
    ]


def _premium_score_caps(
    *,
    technical_valid: bool,
    epubcheck_status: str,
    scores: dict[str, float],
    issues: list[dict[str, Any]],
) -> list[float]:
    caps: list[float] = []
    issue_codes = {str(issue.get("code", "")) for issue in issues}
    if not technical_valid or epubcheck_status == "failed":
        caps.append(4.5)
    if "epubcheck_non_linear_unreachable" in issue_codes:
        caps.append(4.5)
    if scores.get("text_artifact_score", 10.0) < 5.0:
        caps.append(6.0)
    if issue_codes & {"magazine_spine_order_fragmented", "magazine_issue_toc_coverage_low", "magazine_article_coverage_low"}:
        caps.append(6.0)
    return caps


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


def _parse_demoted_non_content_page_count(
    opf_soup: BeautifulSoup,
    manifest: dict[str, dict[str, str]],
) -> int:
    declared = opf_soup.find("meta", attrs={"property": "kindlemaster:demoted-non-content-pages"})
    if declared is not None:
        try:
            return max(0, int(str(declared.get_text("", strip=True) or "0")))
        except ValueError:
            pass
    count = 0
    for itemref in opf_soup.find_all("itemref"):
        linear = str(itemref.get("linear", "yes") or "yes").lower()
        if linear != "no":
            continue
        idref = str(itemref.get("idref", "") or "")
        item = manifest.get(idref) or {}
        href = item.get("href", "")
        if re.search(r"(?:^|/)page_\d+\.xhtml$", href):
            count += 1
    return count


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


def _detect_language_label_contamination(
    *,
    language: str,
    documents: list[_SpineDocument],
    toc_entries: list[dict[str, str]],
) -> dict[str, Any]:
    counter: Counter[str] = Counter()
    samples: list[dict[str, str]] = []
    result = {
        "language": language,
        "threshold": LANGUAGE_LABEL_CONTAMINATION_THRESHOLD,
        "hit_count": 0,
        "labels": [],
        "samples": samples,
    }
    if not _is_english_language(language):
        return result

    contexts: list[tuple[str, str, str]] = []
    for entry in toc_entries:
        contexts.append(("toc", entry.get("label", ""), ""))
    for doc in documents:
        contexts.append(("heading", doc.title, doc.file))
        contexts.append(("reader_text", doc.text, doc.file))

    for source, text, file_name in contexts:
        normalized = _normalize_label(text)
        if not normalized:
            continue
        for label, pattern in POLISH_STRUCTURAL_LABEL_PATTERNS:
            hit_count = len(re.findall(pattern, normalized, flags=re.IGNORECASE))
            if not hit_count:
                continue
            counter[label] += hit_count
            if len(samples) < 8:
                samples.append(
                    {
                        "label": label,
                        "source": source,
                        "file": file_name,
                        "sample": text[:120],
                    }
                )

    result["hit_count"] = sum(counter.values())
    result["labels"] = [label for label, _count in counter.most_common()]
    return result


def _is_english_language(language: str) -> bool:
    return (language or "").strip().lower().startswith("en")


def _metadata_issue(code: str, message: str) -> dict[str, Any]:
    return _issue("blocker", code, message, "metadata", "Fix required reader-facing metadata before release.")


def _classify_non_content_document(doc: _SpineDocument) -> dict[str, str] | None:
    blob = f"{doc.title} {doc.text[:600]}".lower()
    title = _normalize_label(doc.title).lower()
    contact_signal_count = len(
        re.findall(
            r"(?i)\b(?:https?://|www\.|facebook\s*\.|linkedin\s*\.|instagram\s*\.|youtube\s*\.|slideshare\s*\.|tel\.?|phone|e-?mail|kontakt)\b",
            doc.text[:1200],
        )
    )
    has_non_content_label = _matches_any(blob, NON_CONTENT_LABEL_PATTERNS)
    generic_title = _matches_any(title, NON_CONTENT_LABEL_PATTERNS) and doc.word_count <= 220
    contact_stub = contact_signal_count >= 3 and doc.word_count <= 360
    if has_non_content_label and (generic_title or contact_stub or doc.word_count <= 180):
        return {"file": doc.file, "title": doc.title or doc.text[:80]}
    if doc.image_count >= 1 and doc.text_chars < 120 and not _is_cover_or_nav(doc.file):
        return {"file": doc.file, "title": doc.title or "image-only stub"}
    return None


def _toc_noise_issue(entry: dict[str, str]) -> dict[str, Any] | None:
    label = entry.get("label", "")
    normalized = _normalize_label(label)
    if not normalized:
        return _issue("review", "toc_non_content_entry", "Empty TOC label.", "toc", "Remove empty navigation entries.")
    if _is_standard_structural_toc_entry(entry):
        return None
    if re.match(r"(?i)^index\s+of\s+\w+", normalized):
        return None
    if _matches_any(normalized, GENERIC_TOC_PATTERNS):
        return _issue(
            "review",
            "toc_non_content_entry",
            f"TOC contains a generic or non-content label: {label}",
            "toc",
            "Demote ads, galleries, generic labels, and technical fragments out of primary navigation.",
        )
    if _looks_like_dense_handbook_toc_noise(normalized):
        return _issue(
            "review",
            "dense_handbook_toc_noise",
            f"TOC contains dense-handbook procedural or mapping debris: {label}",
            "toc",
            "Keep only chapter, section, and meaningful technique entries in Kindle navigation.",
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


def _is_standard_structural_toc_entry(entry: dict[str, str]) -> bool:
    label = _normalize_label(entry.get("label", ""))
    href = urldefrag(str(entry.get("href", "") or ""))[0]
    target = PurePosixPath(href).name.lower()
    if label in {"cover", "front cover"} and (target == "cover.xhtml" or target.startswith("cover") or target == "chapter_001.xhtml"):
        return True
    if label in {"contents", "table of contents", "spis tresci"}:
        return True
    if label in {"additional materials", "additional material", "materialy dodatkowe", "materiały dodatkowe"}:
        return True
    if re.match(r"(?i)^additional material\s+\d+$", label):
        return True
    return False


def _dense_handbook_navigation_quality(
    *,
    documents: list[_SpineDocument],
    toc_entries: list[dict[str, str]],
) -> dict[str, Any]:
    if not _looks_like_dense_handbook_documents(documents):
        return {}
    toc_noise_samples = [
        entry.get("label", "")
        for entry in toc_entries
        if _looks_like_dense_handbook_toc_noise(entry.get("label", ""))
    ]
    heading_noise_samples: list[str] = []
    for doc in documents:
        if doc.heading_count <= 0:
            continue
        title = _normalize_label(doc.title)
        if _looks_like_dense_micro_heading_label(title):
            heading_noise_samples.append(doc.title)
    return {
        "profile": "dense_handbook",
        "toc_noise_count": len(toc_noise_samples),
        "toc_noise_samples": toc_noise_samples[:12],
        "heading_noise_count": len(heading_noise_samples),
        "heading_noise_samples": heading_noise_samples[:12],
        "largest_document": max(
            ({"file": doc.file, "word_count": doc.word_count, "heading_count": doc.heading_count} for doc in documents),
            key=lambda item: item["word_count"],
            default={},
        ),
    }


def _looks_like_dense_handbook_documents(documents: list[_SpineDocument]) -> bool:
    total_words = sum(doc.word_count for doc in documents)
    if total_words < 40000:
        return False
    blob = " ".join(doc.title for doc in documents).lower()
    signals = (
        "business analysis",
        "requirements",
        "solution evaluation",
        "strategy analysis",
        "techniques",
        "glossary",
        "appendix",
    )
    return sum(1 for signal in signals if signal in blob) >= 3


def _looks_like_dense_handbook_toc_noise(label: str) -> bool:
    normalized = _normalize_label(label).strip(" .:;")
    lowered = normalized.lower()
    if not normalized:
        return False
    if re.fullmatch(r"step\s+\d+\.?", lowered):
        return True
    if lowered in {"game"}:
        return True
    if re.match(r"^\d+\.\s+[A-Z][^:]{3,80}:\s+.{16,}$", normalized):
        return True
    if len(re.findall(r"\b\d+(?:\.\d+){1,3}\.?\s+[A-Z][A-Za-z]+", normalized)) >= 2:
        return True
    if re.match(r"(?i)^description\s+[A-Z][A-Za-z]+\s+[A-Z][A-Za-z]+\s+\w+\.?$", normalized):
        return True
    return False


def _looks_like_dense_micro_heading_label(label: str) -> bool:
    normalized = _normalize_label(label).strip(" .:;")
    lowered = normalized.lower()
    if re.fullmatch(r"\.\d+\s+(?:strengths|limitations|description)", lowered):
        return True
    if lowered in {"elements", "guidelines and tools"}:
        return True
    return False


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
    if "language_label_contamination" in codes:
        score -= 2.0
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


def _float_metric(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_cover_or_nav(path: str) -> bool:
    lowered = path.lower()
    return "cover" in lowered or "nav" in lowered
