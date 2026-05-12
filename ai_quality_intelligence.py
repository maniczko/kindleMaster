from __future__ import annotations

import re
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urldefrag

from bs4 import BeautifulSoup

from ai_ocr_cleanup import AIOcrCleanupProvider, cleanup_suspicious_ocr_fragments
from ai_toc_detection import AiTocProvider, DeterministicTocResult, detect_ai_toc_if_needed
from epub_premium_scoring import score_epub_premium_quality


DEFAULT_TEXT_CONTEXT_LIMIT = 12_000
BENIGN_AI_SKIP_REASONS = {
    "deterministic-confidence-high",
    "no-suspicious-fragments",
}


@dataclass(frozen=True)
class AIQualityProviders:
    ocr_cleanup: AIOcrCleanupProvider | None = None
    toc_detection: AiTocProvider | None = None


def evaluate_ai_quality_intelligence(
    epub_bytes: bytes,
    *,
    providers: AIQualityProviders | None = None,
    premium_scoring: dict[str, Any] | None = None,
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

    estimated_cost = round(
        float(ocr_result.estimated_cost or 0.0)
        + float((toc_result.audit or {}).get("estimated_cost_usd", 0.0) or 0.0),
        6,
    )
    accepted_ai_change_count = int(ocr_result.changed_fragment_count or 0) + len(
        (toc_result.audit.get("changed_entries") or {}).get("added", []) or []
    )
    output_epub_changed = False
    fallback_reasons = _fallback_reasons(
        str(ocr_result.fallback_reason or ""),
        str(toc_result.audit.get("fallback_reason", "") or ""),
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
        ),
        "before_quality_score": before_score,
        "after_quality_score": before_score,
        "score_delta": 0.0,
        "provider": {
            "ocr_cleanup": ocr_result.provider,
            "toc_detection": str(toc_result.audit.get("provider", "none") or "none"),
        },
        "confidence": {
            "toc": toc_result.audit.get("confidence", 0.0),
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
        "learning_signals": learning_signals,
    }


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
) -> str:
    if accepted_ai_change_count:
        return "accepted_pending_application"
    if fallback_reasons:
        return "fallback"
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
