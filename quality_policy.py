from __future__ import annotations

from typing import Any, Mapping


MAGAZINE_PREMIUM_TARGET_SCORE = 9.0
MAGAZINE_TOC_COVERAGE_MIN = 0.95
MAGAZINE_READER_ARTIFACT_RATE_MAX = 0.5
MAGAZINE_LOW_RES_IMAGE_MIN_WIDTH = 600

PASS_WITH_REVIEW_STATUSES = {
    "pass_with_review",
    "passed_with_warnings",
    "warning",
    "warnings",
}

ACCEPTED_P2_WARNING_REASONS = {
    "pre_heading_epubcheck_recovered": "accepted_p2_pre_heading_epubcheck_recovered_final_passed",
    "text_artifact_rate_review": "accepted_p2_low_artifact_rate_without_hard_visible_junk",
    "heading_manual_review": "accepted_p2_profile_heading_review_epubcheck_passed",
    "reference_empty_section_review": "accepted_p2_empty_reference_section_without_citations",
    "reference_review_needed": "accepted_p2_magazine_reference_like_text_without_citations_or_visible_junk",
}

MAGAZINE_REVIEW_CODES_ACCEPTED_AS_P2 = {
    "magazine_url_fragment_review",
    "image_low_resolution_for_kindle",
}

MAGAZINE_REVIEW_CODES_REQUIRING_REPAIR = {
    "magazine_non_editorial_sections_present",
    "magazine_article_title_truncated",
    "magazine_article_segmentation_needs_review",
    "magazine_premium_score_below_9",
}


def is_pass_with_review_status(value: str) -> bool:
    return str(value or "").strip().lower() in PASS_WITH_REVIEW_STATUSES


def magazine_premium_thresholds() -> dict[str, float]:
    return {
        "target_score": MAGAZINE_PREMIUM_TARGET_SCORE,
        "toc_coverage_min": MAGAZINE_TOC_COVERAGE_MIN,
        "artifact_rate_per_1000_words_max": MAGAZINE_READER_ARTIFACT_RATE_MAX,
        "low_resolution_image_min_width": float(MAGAZINE_LOW_RES_IMAGE_MIN_WIDTH),
    }


def warning_policy_catalog() -> dict[str, Any]:
    return {
        "accepted_p2_warning_reasons": dict(ACCEPTED_P2_WARNING_REASONS),
        "magazine_review_codes_accepted_as_p2": sorted(MAGAZINE_REVIEW_CODES_ACCEPTED_AS_P2),
        "magazine_review_codes_requiring_repair": sorted(MAGAZINE_REVIEW_CODES_REQUIRING_REPAIR),
        "principles": [
            "download_available is draft availability, not publication approval",
            "pass_with_review never means premium_ready",
            "pre-heading EPUBCheck failures are acceptable only when the final EPUBCheck pass is proven",
            "low-resolution images are review-only unless they are core diagrams without a fallback",
            "truncated titles, incomplete TOC, accidental non-editorial flow, and visible OCR junk remain repair work",
        ],
    }


def accepted_corpus_warning_reason(
    code: str,
    *,
    metrics: Mapping[str, Any],
) -> str:
    """Return a stable P2 acceptance reason for explicitly bounded warnings."""

    normalized_code = str(code or "").strip()
    if normalized_code == "pre_heading_epubcheck_recovered":
        if str(metrics.get("post_heading_epubcheck_status") or "").lower() == "passed":
            return ACCEPTED_P2_WARNING_REASONS[normalized_code]
        return ""

    if normalized_code == "text_artifact_rate_review":
        if (
            _safe_int(metrics.get("artifact_count")) <= 6
            and _safe_float(metrics.get("artifact_rate_per_1000_words")) <= 4.0
            and _safe_int(metrics.get("hard_visible_artifact_count")) == 0
        ):
            return ACCEPTED_P2_WARNING_REASONS[normalized_code]
        return ""

    if normalized_code == "heading_manual_review":
        if (
            str(metrics.get("post_heading_epubcheck_status") or "").lower() == "passed"
            and _safe_int(metrics.get("manual_review_count")) <= 5
            and bool(metrics.get("has_focus_route"))
        ):
            return ACCEPTED_P2_WARNING_REASONS[normalized_code]
        return ""

    if normalized_code == "reference_empty_section_review":
        if (
            _safe_int(metrics.get("citations_detected")) == 0
            and _safe_int(metrics.get("visible_junk_detected")) == 0
            and _safe_int(metrics.get("empty_reference_sections_unresolved")) <= 1
        ):
            return ACCEPTED_P2_WARNING_REASONS[normalized_code]
        return ""

    if normalized_code == "reference_review_needed":
        focus_routes = set(metrics.get("focus_routes") or [])
        if (
            str(metrics.get("reference_status") or "").lower() == "passed"
            and _safe_int(metrics.get("citations_detected")) == 0
            and _safe_int(metrics.get("visible_junk_detected")) == 0
            and _safe_int(metrics.get("unresolved_fragment_count")) == 0
            and "magazine_layout_heavy" in focus_routes
        ):
            return ACCEPTED_P2_WARNING_REASONS[normalized_code]
        return ""

    if normalized_code == "magazine_premium_quality_review":
        issue_codes = set(str(item) for item in (metrics.get("magazine_issue_codes") or []) if str(item))
        if issue_codes and issue_codes <= MAGAZINE_REVIEW_CODES_ACCEPTED_AS_P2:
            return "accepted_p2_magazine_quality_review_without_structure_or_title_blockers"
        return ""

    return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default
