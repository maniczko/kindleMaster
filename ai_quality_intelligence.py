from __future__ import annotations

import json
import re
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol
from urllib.parse import urldefrag

from bs4 import BeautifulSoup

from ai_ocr_cleanup import AIOcrCleanupProvider, cleanup_suspicious_ocr_fragments, select_suspicious_fragments
from ai_toc_detection import AiTocProvider, DeterministicTocResult, detect_ai_toc_if_needed
from epub_premium_scoring import score_epub_premium_quality


DEFAULT_TEXT_CONTEXT_LIMIT = 12_000
DEFAULT_MAGAZINE_REVIEW_CONTEXT_LIMIT = 10_000
DEFAULT_DENSE_HANDBOOK_REVIEW_CONTEXT_LIMIT = 10_000
MAX_MAGAZINE_REVIEW_TOC_ENTRIES = 60
MAX_MAGAZINE_REVIEW_ARTICLES = 40
MAX_MAGAZINE_REVIEW_FRAGMENTS = 16
MAX_MAGAZINE_REVIEW_PREMIUM_ISSUES = 24
MAX_MAGAZINE_REVIEW_TEXT_CHARS = 360
BENIGN_AI_SKIP_REASONS = {
    "deterministic-confidence-high",
    "magazine-signals-not-detected",
    "magazine-review-provider-unavailable",
    "dense-handbook-signals-not-detected",
    "dense-handbook-review-provider-unavailable",
    "no-suspicious-fragments",
}


class MagazineReviewProvider(Protocol):
    name: str

    def review_magazine(self, context: dict[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError


class DenseHandbookReviewProvider(Protocol):
    name: str

    def review_dense_handbook(self, context: dict[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True)
class AIQualityProviders:
    ocr_cleanup: AIOcrCleanupProvider | None = None
    toc_detection: AiTocProvider | None = None
    magazine_review: MagazineReviewProvider | None = None
    dense_handbook_review: DenseHandbookReviewProvider | None = None


def evaluate_ai_quality_intelligence(
    epub_bytes: bytes,
    *,
    providers: AIQualityProviders | None = None,
    premium_scoring: dict[str, Any] | None = None,
    magazine_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an auditable AI-quality report without requiring network access.

    The current integration is report-first: provider calls are injected
    explicitly and default to no-op fallback. EPUB bytes are not rewritten here,
    so deterministic conversion output remains the source of truth unless a
    future caller intentionally applies accepted AI fragments.
    """

    started = time.perf_counter()
    provider_bundle = providers or AIQualityProviders()
    scoring = dict(premium_scoring or _score_safely(epub_bytes))
    visible_text = _extract_visible_text(epub_bytes)
    toc_entries = _extract_nav_entries(epub_bytes)
    before_score = _coerce_score(scoring.get("premium_score"))

    ocr_result = cleanup_suspicious_ocr_fragments(
        visible_text,
        provider=provider_bundle.ocr_cleanup,
    )
    toc_result = detect_ai_toc_if_needed(
        DeterministicTocResult(
            entries=toc_entries,
            confidence=_deterministic_toc_confidence(scoring=scoring, toc_entries=toc_entries),
        ),
        provider=provider_bundle.toc_detection,
        context={
            "toc_entries": toc_entries,
            "sample_text": visible_text[:DEFAULT_TEXT_CONTEXT_LIMIT],
            "premium_scoring": scoring,
        },
    )
    magazine_review = _evaluate_magazine_review(
        provider=_resolve_magazine_review_provider(provider_bundle),
        epub_bytes=epub_bytes,
        visible_text=visible_text,
        toc_entries=toc_entries,
        scoring=scoring,
        magazine_context=magazine_context or {},
    )
    dense_handbook_review = _evaluate_dense_handbook_review(
        provider=_resolve_dense_handbook_review_provider(provider_bundle),
        epub_bytes=epub_bytes,
        visible_text=visible_text,
        toc_entries=toc_entries,
        scoring=scoring,
    )

    estimated_cost = round(
        float(ocr_result.estimated_cost or 0.0)
        + float((toc_result.audit or {}).get("estimated_cost_usd", 0.0) or 0.0)
        + float((magazine_review or {}).get("estimated_cost_usd", 0.0) or 0.0)
        + float((dense_handbook_review or {}).get("estimated_cost_usd", 0.0) or 0.0),
        6,
    )
    accepted_ai_change_count = int(ocr_result.changed_fragment_count or 0) + len(
        (toc_result.audit.get("changed_entries") or {}).get("added", []) or []
    )
    output_epub_changed = False
    fallback_reasons = _fallback_reasons(
        str(ocr_result.fallback_reason or ""),
        str(toc_result.audit.get("fallback_reason", "") or ""),
        str((magazine_review or {}).get("fallback_reason", "") or ""),
        str((dense_handbook_review or {}).get("fallback_reason", "") or ""),
    )
    learning_signals = _learning_signals(
        accepted_ai_change_count=accepted_ai_change_count,
        fallback_reasons=fallback_reasons,
        before_score=before_score,
        ocr_fragment_count=len(ocr_result.fragments),
        toc_status=str(toc_result.audit.get("status", "") or ""),
        toc_changed_count=len((toc_result.audit.get("changed_entries") or {}).get("added", []) or []),
    )
    return {
        "status": _overall_status(
            accepted_ai_change_count=accepted_ai_change_count,
            fallback_reasons=fallback_reasons,
            ocr_fragments=len(ocr_result.fragments),
            toc_status=str(toc_result.audit.get("status", "") or ""),
            magazine_status=str((magazine_review or {}).get("status", "") or ""),
            dense_handbook_status=str((dense_handbook_review or {}).get("status", "") or ""),
        ),
        "before_quality_score": before_score,
        "after_quality_score": before_score,
        "score_delta": 0.0,
        "provider": {
            "ocr_cleanup": ocr_result.provider,
            "toc_detection": str(toc_result.audit.get("provider", "none") or "none"),
            "magazine_review": str((magazine_review or {}).get("provider", "none") or "none"),
            "dense_handbook_review": str((dense_handbook_review or {}).get("provider", "none") or "none"),
        },
        "confidence": {
            "toc": toc_result.audit.get("confidence", 0.0),
            "magazine_review": (magazine_review or {}).get("confidence", 0.0),
            "dense_handbook_review": (dense_handbook_review or {}).get("confidence", 0.0),
            "ocr_fragments": [
                {"index": item.index, "confidence": item.confidence, "accepted": item.accepted}
                for item in ocr_result.fragments[:20]
            ],
        },
        "estimated_cost_usd": estimated_cost,
        "elapsed_ms": _elapsed_ms(started),
        "changed_fragment_count": int(ocr_result.changed_fragment_count or 0),
        "changed_toc_entry_count": len((toc_result.audit.get("changed_entries") or {}).get("added", []) or []),
        "output_epub_changed": output_epub_changed,
        "deterministic_output_preserved": not output_epub_changed,
        "fallback_reasons": fallback_reasons,
        "ocr_cleanup": _compact_ocr_report(ocr_result.to_dict()),
        "toc_detection": {
            "entries": toc_result.entries[:30],
            "audit": toc_result.audit,
        },
        "magazine_review": magazine_review,
        "dense_handbook_review": dense_handbook_review,
        "learning_signals": learning_signals,
    }


def _evaluate_magazine_review(
    *,
    provider: object | None,
    epub_bytes: bytes,
    visible_text: str,
    toc_entries: list[dict[str, Any]],
    scoring: dict[str, Any],
    magazine_context: dict[str, Any],
) -> dict[str, Any]:
    if not _should_run_magazine_review(scoring=scoring, magazine_context=magazine_context, toc_entries=toc_entries):
        return {
            "status": "skipped",
            "provider": _provider_name(provider),
            "fallback_reason": "magazine-signals-not-detected",
            "output_epub_changed": False,
            **_empty_magazine_review_fields(),
        }
    if provider is None or not hasattr(provider, "review_magazine"):
        return {
            "status": "skipped",
            "provider": _provider_name(provider),
            "fallback_reason": "magazine-review-provider-unavailable",
            "output_epub_changed": False,
            **_empty_magazine_review_fields(),
        }
    context = _build_magazine_review_context(
        epub_bytes=epub_bytes,
        visible_text=visible_text,
        toc_entries=toc_entries,
        scoring=scoring,
        magazine_context=magazine_context,
    )
    try:
        raw = provider.review_magazine(context)  # type: ignore[attr-defined]
    except Exception as exc:
        return {
            "status": "fallback",
            "provider": _provider_name(provider),
            "fallback_reason": f"provider-failed:{exc.__class__.__name__}",
            "output_epub_changed": False,
            **_empty_magazine_review_fields(),
        }
    sanitized = _sanitize_magazine_review_output(raw, context)
    return {
        "status": "reported",
        **sanitized,
        "provider": str(raw.get("provider") or _provider_name(provider)) if isinstance(raw, Mapping) else _provider_name(provider),
        "estimated_cost_usd": _safe_float(raw.get("estimated_cost_usd")) if isinstance(raw, Mapping) else 0.0,
        "metadata": dict(raw.get("metadata") or {}) if isinstance(raw, Mapping) and isinstance(raw.get("metadata"), Mapping) else {},
        "input_policy": "compact_fragments_metrics_only",
        "context_summary": _magazine_context_summary(context),
        "output_epub_changed": False,
    }


def _evaluate_dense_handbook_review(
    *,
    provider: object | None,
    epub_bytes: bytes,
    visible_text: str,
    toc_entries: list[dict[str, Any]],
    scoring: dict[str, Any],
) -> dict[str, Any]:
    if not _should_run_dense_handbook_review(scoring=scoring, toc_entries=toc_entries):
        return {
            "status": "skipped",
            "provider": _provider_name(provider),
            "fallback_reason": "dense-handbook-signals-not-detected",
            "output_epub_changed": False,
            **_empty_dense_handbook_review_fields(),
        }
    if provider is None or not hasattr(provider, "review_dense_handbook"):
        return {
            "status": "skipped",
            "provider": _provider_name(provider),
            "fallback_reason": "dense-handbook-review-provider-unavailable",
            "output_epub_changed": False,
            **_empty_dense_handbook_review_fields(),
        }
    context = _build_dense_handbook_review_context(
        epub_bytes=epub_bytes,
        visible_text=visible_text,
        toc_entries=toc_entries,
        scoring=scoring,
    )
    try:
        raw = provider.review_dense_handbook(context)  # type: ignore[attr-defined]
    except Exception as exc:
        return {
            "status": "fallback",
            "provider": _provider_name(provider),
            "fallback_reason": f"provider-failed:{exc.__class__.__name__}",
            "output_epub_changed": False,
            **_empty_dense_handbook_review_fields(),
        }
    sanitized = _sanitize_dense_handbook_review_output(raw, context)
    return {
        "status": "reported",
        **sanitized,
        "provider": str(raw.get("provider") or _provider_name(provider)) if isinstance(raw, Mapping) else _provider_name(provider),
        "estimated_cost_usd": _safe_float(raw.get("estimated_cost_usd")) if isinstance(raw, Mapping) else 0.0,
        "metadata": dict(raw.get("metadata") or {}) if isinstance(raw, Mapping) and isinstance(raw.get("metadata"), Mapping) else {},
        "input_policy": "compact_fragments_metrics_only",
        "context_summary": _dense_handbook_context_summary(context),
        "output_epub_changed": False,
    }


def _resolve_magazine_review_provider(provider_bundle: AIQualityProviders) -> object | None:
    for candidate in (provider_bundle.magazine_review, provider_bundle.toc_detection, provider_bundle.ocr_cleanup):
        if candidate is not None and callable(getattr(candidate, "review_magazine", None)):
            return candidate
    return None


def _resolve_dense_handbook_review_provider(provider_bundle: AIQualityProviders) -> object | None:
    for candidate in (
        provider_bundle.dense_handbook_review,
        provider_bundle.magazine_review,
        provider_bundle.toc_detection,
        provider_bundle.ocr_cleanup,
    ):
        if candidate is not None and callable(getattr(candidate, "review_dense_handbook", None)):
            return candidate
    return None


def _should_run_magazine_review(
    *,
    scoring: dict[str, Any],
    magazine_context: dict[str, Any],
    toc_entries: list[dict[str, Any]],
) -> bool:
    if magazine_context:
        return True
    metrics = scoring.get("metrics") if isinstance(scoring.get("metrics"), dict) else {}
    if metrics.get("non_content_chapter_count") or metrics.get("toc_noise_entry_count"):
        return True
    issues = [str(item.get("code", "")) for item in scoring.get("issues", []) if isinstance(item, dict)]
    return any(code.startswith("magazine_") or code in {"toc_lead_used_as_title", "toc_non_content_entry"} for code in issues)


def _should_run_dense_handbook_review(
    *,
    scoring: dict[str, Any],
    toc_entries: list[dict[str, Any]],
) -> bool:
    metrics = scoring.get("metrics") if isinstance(scoring.get("metrics"), dict) else {}
    dense_summary = metrics.get("dense_handbook_navigation_summary") if isinstance(metrics.get("dense_handbook_navigation_summary"), dict) else {}
    if dense_summary:
        return True
    issues = [str(item.get("code", "")) for item in scoring.get("issues", []) if isinstance(item, dict)]
    if any(code.startswith("dense_handbook_") for code in issues):
        return True
    if len(toc_entries) >= 35 and any(str(entry.get("label", "")).strip().lower().startswith("step ") for entry in toc_entries):
        return True
    return False


def _build_magazine_review_context(
    *,
    epub_bytes: bytes,
    visible_text: str,
    toc_entries: list[dict[str, Any]],
    scoring: dict[str, Any],
    magazine_context: dict[str, Any],
) -> dict[str, Any]:
    extracted = _extract_magazine_article_context(epub_bytes)
    supplied_article_map = _compact_supplied_article_map(magazine_context.get("article_map"))
    supplied_image_metrics = _compact_supplied_image_metrics(magazine_context.get("image_metrics"))
    suspicious = select_suspicious_fragments(visible_text)
    context = {
        "context_version": "magazine-review-v1",
        "toc_entries": _compact_toc_entries(toc_entries),
        "article_map": supplied_article_map or extracted["article_map"],
        "image_metrics": supplied_image_metrics or extracted["image_metrics"],
        "premium_issues": _compact_premium_issues(scoring.get("issues")),
        "suspicious_fragments": [
            {
                "index": item.index,
                "text": _clip_context_text(item.text, MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "artifact_counts": dict(item.artifact_counts),
            }
            for item in suspicious[:MAX_MAGAZINE_REVIEW_FRAGMENTS]
        ],
        "flow_fragments": extracted["flow_fragments"][:MAX_MAGAZINE_REVIEW_FRAGMENTS],
        "metrics": {
            "premium_score": scoring.get("premium_score"),
            "release_verdict": scoring.get("release_verdict"),
            "toc_entry_count": len(toc_entries),
            "premium_issue_count": len(scoring.get("issues") or []),
        },
    }
    return _bound_magazine_context(context, DEFAULT_MAGAZINE_REVIEW_CONTEXT_LIMIT)


def _build_dense_handbook_review_context(
    *,
    epub_bytes: bytes,
    visible_text: str,
    toc_entries: list[dict[str, Any]],
    scoring: dict[str, Any],
) -> dict[str, Any]:
    metrics = scoring.get("metrics") if isinstance(scoring.get("metrics"), dict) else {}
    dense_summary = metrics.get("dense_handbook_navigation_summary") if isinstance(metrics.get("dense_handbook_navigation_summary"), dict) else {}
    text_artifacts = metrics.get("text_artifacts") if isinstance(metrics.get("text_artifacts"), dict) else {}
    suspicious = select_suspicious_fragments(visible_text)
    context = {
        "context_version": "dense-handbook-review-v1",
        "toc_entries": _compact_toc_entries(toc_entries),
        "heading_noise_samples": [
            {"index": index, "label": sample, "href": "", "level": 0, "evidence": "dense navigation summary"}
            for index, sample in enumerate((dense_summary.get("heading_noise_samples") or [])[:MAX_MAGAZINE_REVIEW_FRAGMENTS])
        ],
        "text_artifact_fragments": [
            {
                "index": item.index,
                "text": _clip_context_text(item.text, MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "artifact_counts": dict(item.artifact_counts),
            }
            for item in suspicious[:MAX_MAGAZINE_REVIEW_FRAGMENTS]
        ],
        "chapter_stats": _extract_dense_chapter_stats(epub_bytes),
        "premium_issues": _compact_premium_issues(scoring.get("issues")),
        "metrics": {
            "premium_score": scoring.get("premium_score"),
            "release_verdict": scoring.get("release_verdict"),
            "toc_noise_count": dense_summary.get("toc_noise_count", 0),
            "heading_noise_count": dense_summary.get("heading_noise_count", 0),
            "artifact_rate_per_1000_words": text_artifacts.get("artifact_rate_per_1000_words"),
            "ignored_text_artifacts": text_artifacts.get("ignored_counts", {}),
        },
    }
    return _bound_dense_handbook_context(context, DEFAULT_DENSE_HANDBOOK_REVIEW_CONTEXT_LIMIT)


def _extract_magazine_article_context(epub_bytes: bytes) -> dict[str, Any]:
    article_map: list[dict[str, Any]] = []
    flow_fragments: list[dict[str, Any]] = []
    image_metrics: dict[str, Any] = {
        "total_images": 0,
        "articles_with_images": 0,
        "image_only_articles": 0,
        "missing_alt_count": 0,
        "per_article": [],
    }
    try:
        with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
            opf_path = _find_opf_path(archive)
            if not opf_path:
                return {"article_map": article_map, "flow_fragments": flow_fragments, "image_metrics": image_metrics}
            opf_soup = BeautifulSoup(archive.read(opf_path), "xml")
            opf_dir = str(PurePosixPath(opf_path).parent)
            if opf_dir == ".":
                opf_dir = ""
            manifest = _read_manifest(opf_soup)
            for itemref in opf_soup.find_all("itemref"):
                idref = str(itemref.get("idref", "") or "")
                item = manifest.get(idref) or {}
                if item.get("media_type") != "application/xhtml+xml":
                    continue
                if "nav" in str(item.get("properties", "")).split():
                    continue
                href = urldefrag(str(item.get("href", "") or ""))[0]
                path = _resolve_opf_href(opf_dir, href)
                if path not in archive.namelist():
                    continue
                summary = _read_article_summary(archive, path=path, href=href, index=len(article_map))
                article_map.append(summary)
                image_metrics["total_images"] += summary["image_count"]
                image_metrics["missing_alt_count"] += summary["missing_alt_count"]
                if summary["image_count"]:
                    image_metrics["articles_with_images"] += 1
                if summary["image_count"] and summary["text_chars"] < 120:
                    image_metrics["image_only_articles"] += 1
                image_metrics["per_article"].append(
                    {
                        "href": summary["href"],
                        "image_count": summary["image_count"],
                        "missing_alt_count": summary["missing_alt_count"],
                        "text_chars": summary["text_chars"],
                    }
                )
                flow_fragments.extend(_flow_fragments_for_article(summary))
                if len(article_map) >= MAX_MAGAZINE_REVIEW_ARTICLES:
                    break
    except (KeyError, zipfile.BadZipFile, OSError):
        return {"article_map": article_map, "flow_fragments": flow_fragments, "image_metrics": image_metrics}

    image_metrics["per_article"] = image_metrics["per_article"][:MAX_MAGAZINE_REVIEW_ARTICLES]
    return {"article_map": article_map, "flow_fragments": flow_fragments, "image_metrics": image_metrics}


def _extract_dense_chapter_stats(epub_bytes: bytes) -> list[dict[str, Any]]:
    stats: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(BytesIO(epub_bytes), "r") as archive:
            for name in sorted(archive.namelist()):
                if not name.lower().endswith((".xhtml", ".html")) or "nav" in PurePosixPath(name).name.lower():
                    continue
                soup = BeautifulSoup(archive.read(name), "html.parser")
                for node in soup(["script", "style", "svg", "math"]):
                    node.decompose()
                headings = soup.find_all(["h1", "h2", "h3"])
                title = ""
                for heading in headings:
                    title = _clip_context_text(heading.get_text(" ", strip=True), 180)
                    if title:
                        break
                text = " ".join((soup.body or soup).get_text(" ", strip=True).split())
                stats.append(
                    {
                        "href": PurePosixPath(name).name,
                        "title": title or PurePosixPath(name).name,
                        "word_count": len(re.findall(r"\b[^\W\d_]{2,}\b", text, re.UNICODE)),
                        "heading_count": len(headings),
                    }
                )
    except (KeyError, zipfile.BadZipFile, OSError):
        return stats
    return sorted(stats, key=lambda item: int(item.get("word_count") or 0), reverse=True)[:MAX_MAGAZINE_REVIEW_ARTICLES]


def _read_manifest(opf_soup: BeautifulSoup) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for item in opf_soup.find_all("item"):
        item_id = str(item.get("id", "") or "")
        if not item_id:
            continue
        manifest[item_id] = {
            "href": str(item.get("href", "") or ""),
            "media_type": str(item.get("media-type", "") or ""),
            "properties": str(item.get("properties", "") or ""),
        }
    return manifest


def _resolve_opf_href(opf_dir: str, href: str) -> str:
    if not href:
        return ""
    if not opf_dir:
        return href
    return str(PurePosixPath(opf_dir) / href)


def _read_article_summary(archive: zipfile.ZipFile, *, path: str, href: str, index: int) -> dict[str, Any]:
    soup = BeautifulSoup(archive.read(path), "html.parser")
    for node in soup(["script", "style", "svg", "math"]):
        node.decompose()
    headings = soup.find_all(["h1", "h2", "h3"])
    title = ""
    for heading in headings:
        title = " ".join(heading.get_text(" ", strip=True).split())
        if title:
            break
    if not title and soup.title:
        title = " ".join(soup.title.get_text(" ", strip=True).split())
    text = " ".join((soup.body or soup).get_text(" ", strip=True).split())
    image_nodes = soup.find_all("img")
    missing_alt_count = sum(1 for image in image_nodes if not str(image.get("alt", "") or "").strip())
    risk_flags = _article_risk_flags(title=title, text=text, image_count=len(image_nodes), heading_count=len(headings), href=href)
    return {
        "index": index,
        "href": href or PurePosixPath(path).name,
        "title": _clip_context_text(title, 180),
        "word_count": len(re.findall(r"\b[^\W\d_]{2,}\b", text, re.UNICODE)),
        "text_chars": len(text),
        "image_count": len(image_nodes),
        "missing_alt_count": missing_alt_count,
        "heading_count": len(headings),
        "text_preview": _clip_context_text(text, MAX_MAGAZINE_REVIEW_TEXT_CHARS),
        "risk_flags": risk_flags,
    }


def _article_risk_flags(*, title: str, text: str, image_count: int, heading_count: int, href: str) -> list[str]:
    flags: list[str] = []
    normalized_title = re.sub(r"\s+", " ", title or "").strip()
    text_chars = len(text or "")
    if not normalized_title:
        flags.append("missing-title")
    if len(normalized_title) > 90 or len(normalized_title.split()) > 12:
        flags.append("title-looks-like-lead")
    if _looks_like_noisy_toc_label(normalized_title):
        flags.append("non-content-label")
    if image_count and text_chars < 120:
        flags.append("image-only-or-caption-stub")
    if text_chars >= 900 and heading_count == 0:
        flags.append("long-article-without-heading")
    if re.search(r"(?i)(?:chapter|section|content)[_-]?\d+", href or "") and not normalized_title:
        flags.append("generic-spine-fragment")
    return flags[:8]


def _flow_fragments_for_article(article: dict[str, Any]) -> list[dict[str, Any]]:
    fragments: list[dict[str, Any]] = []
    for flag in article.get("risk_flags") or []:
        fragments.append(
            {
                "index": _coerce_int(article.get("index"), 0),
                "href": str(article.get("href") or ""),
                "kind": str(flag),
                "title": str(article.get("title") or ""),
                "evidence": _clip_context_text(str(article.get("text_preview") or ""), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
            }
        )
    return fragments


def _compact_toc_entries(toc_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for index, entry in enumerate(toc_entries[:MAX_MAGAZINE_REVIEW_TOC_ENTRIES]):
        if not isinstance(entry, dict):
            continue
        compact.append(
            {
                "index": index,
                "label": _clip_context_text(entry.get("label") or entry.get("title"), 180),
                "href": _clip_context_text(entry.get("href"), 240),
            }
        )
    return compact


def _compact_supplied_article_map(value: Any) -> list[dict[str, Any]]:
    raw_articles = value.get("articles") if isinstance(value, Mapping) else value
    compact: list[dict[str, Any]] = []
    if not isinstance(raw_articles, list):
        return compact
    for index, item in enumerate(raw_articles[:MAX_MAGAZINE_REVIEW_ARTICLES]):
        if not isinstance(item, Mapping):
            continue
        compact.append(
            {
                "index": _coerce_int(item.get("index") or item.get("position"), index),
                "href": _clip_context_text(item.get("href"), 240),
                "title": _clip_context_text(item.get("title"), 180),
                "word_count": _coerce_int(item.get("word_count"), 0),
                "image_count": _coerce_int(item.get("image_count"), 0),
                "heading_count": _coerce_int(item.get("heading_count"), 0),
                "text_preview": _clip_context_text(item.get("text_preview") or item.get("sample_text"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "risk_flags": _compact_string_list(item.get("risk_flags"), limit=8, chars=80),
            }
        )
    return compact


def _compact_supplied_image_metrics(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    metrics: dict[str, Any] = {
        "total_images": _coerce_int(value.get("total_images"), 0),
        "articles_with_images": _coerce_int(value.get("articles_with_images"), 0),
        "image_only_articles": _coerce_int(value.get("image_only_articles"), 0),
        "missing_alt_count": _coerce_int(value.get("missing_alt_count"), 0),
        "low_resolution_image_count": _coerce_int(value.get("low_resolution_image_count"), 0),
    }
    per_article: list[dict[str, Any]] = []
    raw_rows = value.get("per_article") if isinstance(value.get("per_article"), list) else []
    for item in raw_rows[:MAX_MAGAZINE_REVIEW_ARTICLES]:
        if not isinstance(item, Mapping):
            continue
        per_article.append(
            {
                "href": _clip_context_text(item.get("href"), 240),
                "image_count": _coerce_int(item.get("image_count"), 0),
                "missing_alt_count": _coerce_int(item.get("missing_alt_count"), 0),
                "text_chars": _coerce_int(item.get("text_chars"), 0),
            }
        )
    if per_article:
        metrics["per_article"] = per_article
    return metrics


def _compact_premium_issues(value: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return issues
    for item in value[:MAX_MAGAZINE_REVIEW_PREMIUM_ISSUES]:
        if not isinstance(item, Mapping):
            continue
        issues.append(
            {
                "code": _clip_context_text(item.get("code"), 100),
                "severity": _clip_context_text(item.get("severity"), 40),
                "source": _clip_context_text(item.get("source"), 80),
                "file": _clip_context_text(item.get("file"), 240),
                "message": _clip_context_text(item.get("message"), 260),
                "suggested_action": _clip_context_text(item.get("suggested_action"), 260),
            }
        )
    return issues


def _empty_magazine_review_fields() -> dict[str, Any]:
    return {
        "suspected_bad_reading_order": [],
        "truncated_titles": [],
        "toc_missing_articles": [],
        "non_content_misclassified": [],
        "ocr_cleanup_candidates": [],
        "suggested_fixture_tags": [],
        "confidence": 0.0,
        "estimated_cost_usd": 0.0,
        "context_summary": {},
    }


def _empty_dense_handbook_review_fields() -> dict[str, Any]:
    return {
        "toc_debris": [],
        "heading_noise": [],
        "text_artifact_reviews": [],
        "oversized_chapters": [],
        "suggested_fixture_tags": [],
        "confidence": 0.0,
        "estimated_cost_usd": 0.0,
        "context_summary": {},
    }


def _sanitize_magazine_review_output(raw: Any, context: dict[str, Any]) -> dict[str, Any]:
    payload = raw if isinstance(raw, Mapping) else {}
    allowed_hrefs = _allowed_magazine_hrefs(context)
    fragment_text_by_index = _fragment_text_by_index(context)
    return {
        "suspected_bad_reading_order": _sanitize_href_review_items(
            payload.get("suspected_bad_reading_order"),
            allowed_hrefs=allowed_hrefs,
        ),
        "truncated_titles": _sanitize_title_review_items(payload.get("truncated_titles"), allowed_hrefs=allowed_hrefs),
        "toc_missing_articles": _sanitize_missing_article_review_items(
            payload.get("toc_missing_articles"),
            allowed_hrefs=allowed_hrefs,
        ),
        "non_content_misclassified": _sanitize_non_content_review_items(
            payload.get("non_content_misclassified"),
            allowed_hrefs=allowed_hrefs,
        ),
        "ocr_cleanup_candidates": _sanitize_ocr_review_items(
            payload.get("ocr_cleanup_candidates"),
            fragment_text_by_index=fragment_text_by_index,
        ),
        "suggested_fixture_tags": _sanitize_fixture_tags(payload.get("suggested_fixture_tags")),
        "confidence": _clamp_confidence(payload.get("confidence")),
    }


def _sanitize_dense_handbook_review_output(raw: Any, context: dict[str, Any]) -> dict[str, Any]:
    payload = raw if isinstance(raw, Mapping) else {}
    allowed_hrefs = _allowed_dense_hrefs(context)
    fragment_text_by_index = _dense_fragment_text_by_index(context)
    return {
        "toc_debris": _sanitize_dense_href_items(payload.get("toc_debris"), allowed_hrefs=allowed_hrefs),
        "heading_noise": _sanitize_dense_href_items(payload.get("heading_noise"), allowed_hrefs=allowed_hrefs),
        "text_artifact_reviews": _sanitize_dense_artifact_items(
            payload.get("text_artifact_reviews"),
            fragment_text_by_index=fragment_text_by_index,
        ),
        "oversized_chapters": _sanitize_dense_chapter_items(payload.get("oversized_chapters"), allowed_hrefs=allowed_hrefs),
        "suggested_fixture_tags": _sanitize_fixture_tags(payload.get("suggested_fixture_tags")),
        "confidence": _clamp_confidence(payload.get("confidence")),
    }


def _sanitize_dense_href_items(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        href = _allowed_href(item.get("href"), allowed_hrefs) if allowed_hrefs else _clip_context_text(item.get("href"), 240)
        label = _clip_context_text(item.get("label") or item.get("title"), 180)
        if allowed_hrefs and str(item.get("href") or "").strip() and not href:
            continue
        if not label:
            continue
        items.append(
            {
                "href": href,
                "label": label,
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_dense_artifact_items(value: Any, *, fragment_text_by_index: dict[int, str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        index = _coerce_int(item.get("fragment_index") if "fragment_index" in item else item.get("index"), -1)
        if index not in fragment_text_by_index:
            continue
        items.append(
            {
                "fragment_index": index,
                "before": fragment_text_by_index[index],
                "classification": _clip_context_text(item.get("classification"), 80),
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_dense_chapter_items(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        href = _allowed_href(item.get("href"), allowed_hrefs) if allowed_hrefs else _clip_context_text(item.get("href"), 240)
        title = _clip_context_text(item.get("title") or item.get("label"), 180)
        if allowed_hrefs and str(item.get("href") or "").strip() and not href:
            continue
        if not title:
            continue
        items.append(
            {
                "href": href,
                "title": title,
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_href_review_items(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        href = _allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_title_review_items(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        href = _allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "observed_title": _clip_context_text(item.get("observed_title") or item.get("observed"), 180),
                "suggested_title": _clip_context_text(item.get("suggested_title") or item.get("suggested"), 180),
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_missing_article_review_items(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        href = _allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "title": _clip_context_text(item.get("title") or item.get("label"), 180),
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_non_content_review_items(value: Any, *, allowed_hrefs: set[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        href = _allowed_href(item.get("href"), allowed_hrefs)
        if not href:
            continue
        items.append(
            {
                "href": href,
                "label": _clip_context_text(item.get("label") or item.get("title"), 180),
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_ocr_review_items(value: Any, *, fragment_text_by_index: dict[int, str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_mappings(value):
        index = _coerce_int(item.get("fragment_index") if "fragment_index" in item else item.get("index"), -1)
        if index not in fragment_text_by_index:
            continue
        items.append(
            {
                "fragment_index": index,
                "before": fragment_text_by_index[index],
                "suggested": _clip_context_text(item.get("suggested") or item.get("after"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "evidence": _clip_context_text(item.get("evidence") or item.get("reason"), MAX_MAGAZINE_REVIEW_TEXT_CHARS),
                "confidence": _clamp_confidence(item.get("confidence")),
            }
        )
        if len(items) >= MAX_MAGAZINE_REVIEW_FRAGMENTS:
            break
    return items


def _sanitize_fixture_tags(value: Any) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    if not isinstance(value, list):
        return tags
    for item in value:
        tag = re.sub(r"[^a-z0-9_-]+", "-", str(item or "").strip().lower()).strip("-_")
        if len(tag) < 2:
            continue
        tag = tag[:60]
        if tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if len(tags) >= 12:
            break
    return tags


def _allowed_magazine_hrefs(context: dict[str, Any]) -> set[str]:
    hrefs: set[str] = set()
    for key in ("toc_entries", "article_map", "flow_fragments"):
        for item in _iter_mappings(context.get(key)):
            href = str(item.get("href") or "").strip()
            if href:
                hrefs.add(href)
    return hrefs


def _allowed_dense_hrefs(context: dict[str, Any]) -> set[str]:
    hrefs: set[str] = set()
    for key in ("toc_entries", "heading_noise_samples", "chapter_stats"):
        for item in _iter_mappings(context.get(key)):
            href = str(item.get("href") or "").strip()
            if href:
                hrefs.add(href)
    return hrefs


def _fragment_text_by_index(context: dict[str, Any]) -> dict[int, str]:
    fragments: dict[int, str] = {}
    for item in _iter_mappings(context.get("suspicious_fragments")):
        index = _coerce_int(item.get("index"), -1)
        if index >= 0:
            fragments[index] = _clip_context_text(item.get("text"), MAX_MAGAZINE_REVIEW_TEXT_CHARS)
    return fragments


def _dense_fragment_text_by_index(context: dict[str, Any]) -> dict[int, str]:
    fragments: dict[int, str] = {}
    for item in _iter_mappings(context.get("text_artifact_fragments")):
        index = _coerce_int(item.get("index"), -1)
        if index >= 0:
            fragments[index] = _clip_context_text(item.get("text"), MAX_MAGAZINE_REVIEW_TEXT_CHARS)
    return fragments


def _allowed_href(value: Any, allowed_hrefs: set[str]) -> str:
    href = str(value or "").strip()
    if not href or href not in allowed_hrefs:
        return ""
    return href


def _magazine_context_summary(context: dict[str, Any]) -> dict[str, Any]:
    image_metrics = context.get("image_metrics") if isinstance(context.get("image_metrics"), Mapping) else {}
    return {
        "toc_entry_count": len(context.get("toc_entries") or []),
        "article_count": len(context.get("article_map") or []),
        "suspicious_fragment_count": len(context.get("suspicious_fragments") or []),
        "flow_fragment_count": len(context.get("flow_fragments") or []),
        "premium_issue_count": len(context.get("premium_issues") or []),
        "total_images": image_metrics.get("total_images", 0),
        "bounded_context_chars": _json_length(context),
    }


def _dense_handbook_context_summary(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "toc_entry_count": len(context.get("toc_entries") or []),
        "heading_noise_sample_count": len(context.get("heading_noise_samples") or []),
        "text_artifact_fragment_count": len(context.get("text_artifact_fragments") or []),
        "chapter_stat_count": len(context.get("chapter_stats") or []),
        "premium_issue_count": len(context.get("premium_issues") or []),
        "bounded_context_chars": _json_length(context),
    }


def _bound_magazine_context(context: dict[str, Any], limit: int) -> dict[str, Any]:
    bounded = dict(context)
    bounded["context_truncated"] = False
    if _json_length(bounded) <= limit:
        return bounded
    bounded["context_truncated"] = True
    for key in ("premium_issues", "suspicious_fragments", "flow_fragments", "article_map", "toc_entries"):
        items = list(bounded.get(key) or [])
        while items and _json_length(bounded) > limit:
            items.pop()
            bounded[key] = items
    if _json_length(bounded) <= limit:
        return bounded
    for text_limit in (220, 120, 60):
        bounded = _clip_context_strings(bounded, text_limit)
        bounded["context_truncated"] = True
        if _json_length(bounded) <= limit:
            return bounded
    return {
        "context_version": "magazine-review-v1",
        "context_truncated": True,
        "toc_entries": [],
        "article_map": [],
        "image_metrics": {},
        "premium_issues": [],
        "suspicious_fragments": [],
        "flow_fragments": [],
        "metrics": {},
    }


def _bound_dense_handbook_context(context: dict[str, Any], limit: int) -> dict[str, Any]:
    bounded = dict(context)
    bounded["context_truncated"] = False
    if _json_length(bounded) <= limit:
        return bounded
    bounded["context_truncated"] = True
    for key in ("premium_issues", "text_artifact_fragments", "heading_noise_samples", "chapter_stats", "toc_entries"):
        items = list(bounded.get(key) or [])
        while items and _json_length(bounded) > limit:
            items.pop()
            bounded[key] = items
    if _json_length(bounded) <= limit:
        return bounded
    for text_limit in (220, 120, 60):
        bounded = _clip_context_strings(bounded, text_limit)
        bounded["context_truncated"] = True
        if _json_length(bounded) <= limit:
            return bounded
    return {
        "context_version": "dense-handbook-review-v1",
        "context_truncated": True,
        "toc_entries": [],
        "heading_noise_samples": [],
        "text_artifact_fragments": [],
        "chapter_stats": [],
        "premium_issues": [],
        "metrics": {},
    }


def _clip_context_strings(value: Any, text_limit: int) -> Any:
    if isinstance(value, dict):
        return {key: _clip_context_strings(item, text_limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_clip_context_strings(item, text_limit) for item in value]
    if isinstance(value, str):
        return _clip_context_text(value, text_limit)
    return value


def _clip_context_text(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    suffix = " [...clipped]"
    return text[: max(0, limit - len(suffix))] + suffix


def _compact_string_list(value: Any, *, limit: int, chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value[:limit]:
        result.append(_clip_context_text(item, chars))
    return result


def _iter_mappings(value: Any):
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, Mapping):
            yield item


def _json_length(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False))


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _provider_name(provider: object | None) -> str:
    if provider is None:
        return "none"
    return str(getattr(provider, "name", provider.__class__.__name__) or provider.__class__.__name__)


def _score_safely(epub_bytes: bytes) -> dict[str, Any]:
    try:
        return score_epub_premium_quality(epub_bytes)
    except Exception as error:
        return {
            "status": "unavailable",
            "premium_score": 0.0,
            "scores": {},
            "issues": [],
            "error": str(error),
        }


def _coerce_score(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _deterministic_toc_confidence(*, scoring: dict[str, Any], toc_entries: list[dict[str, Any]]) -> float:
    scores = scoring.get("scores") if isinstance(scoring.get("scores"), dict) else {}
    toc_score = _coerce_score(scores.get("toc_quality_score"))
    if toc_score:
        return max(0.0, min(1.0, toc_score / 10.0))
    if not toc_entries:
        return 0.0
    noisy_count = sum(1 for entry in toc_entries if _looks_like_noisy_toc_label(str(entry.get("label", "") or "")))
    return max(0.0, min(1.0, 1.0 - (noisy_count / max(len(toc_entries), 1))))


def _fallback_reasons(*values: str) -> list[str]:
    reasons: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized in BENIGN_AI_SKIP_REASONS:
            continue
        if normalized and normalized not in reasons:
            reasons.append(normalized)
    return reasons


def _overall_status(
    *,
    accepted_ai_change_count: int,
    fallback_reasons: list[str],
    ocr_fragments: int,
    toc_status: str,
    magazine_status: str,
    dense_handbook_status: str,
) -> str:
    if accepted_ai_change_count:
        return "accepted_pending_application"
    if fallback_reasons:
        return "fallback"
    if magazine_status == "reported":
        return "reported"
    if dense_handbook_status == "reported":
        return "reported"
    if ocr_fragments == 0 and toc_status == "skipped":
        return "skipped"
    return "reported"


def _compact_ocr_report(payload: dict[str, Any]) -> dict[str, Any]:
    compact = dict(payload)
    compact.pop("text", None)
    fragments = compact.get("fragments")
    if isinstance(fragments, list):
        compact["fragments"] = fragments[:20]
    return compact


def _extract_visible_text(epub_bytes: bytes) -> str:
    chunks: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
            for name in sorted(archive.namelist()):
                if not name.lower().endswith((".xhtml", ".html")):
                    continue
                soup = BeautifulSoup(archive.read(name), "html.parser")
                for node in soup(["script", "style", "svg", "math"]):
                    node.decompose()
                text = " ".join(soup.get_text(" ", strip=True).split())
                if text:
                    chunks.append(text)
    except zipfile.BadZipFile:
        return ""
    return "\n\n".join(chunks)


def _extract_nav_entries(epub_bytes: bytes) -> list[dict[str, Any]]:
    try:
        with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
            nav_path = _find_nav_path(archive)
            if not nav_path:
                return []
            soup = BeautifulSoup(archive.read(nav_path), "html.parser")
    except (KeyError, zipfile.BadZipFile):
        return []

    toc_nav = None
    for nav in soup.find_all("nav"):
        nav_type = str(nav.get("epub:type") or nav.get("type") or "").lower()
        if "toc" in nav_type:
            toc_nav = nav
            break
    toc_nav = toc_nav or soup.find("nav")
    if toc_nav is None:
        return []

    entries: list[dict[str, Any]] = []
    for anchor in toc_nav.find_all("a"):
        label = " ".join(anchor.get_text(" ", strip=True).split())
        href = str(anchor.get("href", "") or "").strip()
        if label and href:
            entries.append({"label": label, "href": href})
    return entries


def _find_nav_path(archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist())
    if "EPUB/nav.xhtml" in names:
        return "EPUB/nav.xhtml"
    if "nav.xhtml" in names:
        return "nav.xhtml"
    opf_path = _find_opf_path(archive)
    if not opf_path:
        return ""
    opf_dir = str(PurePosixPath(opf_path).parent)
    if opf_dir == ".":
        opf_dir = ""
    soup = BeautifulSoup(archive.read(opf_path), "xml")
    for item in soup.find_all("item"):
        properties = str(item.get("properties", "") or "")
        if "nav" not in properties.split():
            continue
        href = urldefrag(str(item.get("href", "") or ""))[0]
        if not href:
            continue
        return str(PurePosixPath(opf_dir) / href) if opf_dir else href
    return ""


def _find_opf_path(archive: zipfile.ZipFile) -> str:
    try:
        soup = BeautifulSoup(archive.read("META-INF/container.xml"), "xml")
    except KeyError:
        return ""
    rootfile = soup.find("rootfile")
    return str(rootfile.get("full-path", "") if rootfile else "")


def _looks_like_noisy_toc_label(label: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(label or "").strip().lower())
    if not normalized:
        return True
    if len(normalized.split()) > 12 or len(normalized) > 90:
        return True
    return bool(
        re.search(
            r"\b(?:advertisement|advertorial|sponsored|reklama|galeria|object\s+\d+|chart|diagram|caption)\b",
            normalized,
        )
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


def _learning_signals(
    *,
    accepted_ai_change_count: int,
    fallback_reasons: list[str],
    before_score: float,
    ocr_fragment_count: int,
    toc_status: str,
    toc_changed_count: int,
) -> dict[str, Any]:
    actions: list[str] = []
    if ocr_fragment_count:
        actions.append("review_ocr_artifact_patterns")
    if toc_changed_count or toc_status == "accepted":
        actions.append("review_toc_segmentation_heuristics")
    if before_score and before_score < 7:
        actions.append("consider_new_regression_fixture")
    if any("provider" in reason for reason in fallback_reasons):
        actions.append("verify_ai_provider_configuration")
    if accepted_ai_change_count:
        actions.append("promote_accepted_ai_suggestions_to_human_review")
    return {
        "candidate_fix_count": accepted_ai_change_count,
        "ocr_fragment_count": ocr_fragment_count,
        "toc_changed_count": toc_changed_count,
        "should_create_fixture": bool((before_score and before_score < 7) or accepted_ai_change_count >= 2),
        "recommended_actions": actions,
        "self_modifying_code_allowed": False,
    }
