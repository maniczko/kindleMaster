from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from epub_premium_scoring import score_epub_premium_quality


SCORE_REGRESSION_TOLERANCE = 0.05
READY_VERDICTS = {"ready_with_review", "release_ready"}
BLOCKED_VERDICTS = {"release_blocked", "failed"}
QUALITY_REGRESSION_CODE = "recovery_rejected_due_to_quality_regression"
MONOTONIC_REGRESSION_CODE = "quality_monotonic_regression"


@dataclass(frozen=True)
class QualitySelectionResult:
    selected_bytes: bytes
    selected_epubcheck: dict[str, Any]
    selected_scoring: dict[str, Any]
    baseline_scoring: dict[str, Any]
    candidate_scoring: dict[str, Any]
    report: dict[str, Any]


def select_epub_by_quality(
    baseline_bytes: bytes,
    candidate_bytes: bytes,
    *,
    baseline_label: str,
    candidate_label: str,
    baseline_epubcheck: Mapping[str, Any] | None = None,
    candidate_epubcheck: Mapping[str, Any] | None = None,
) -> QualitySelectionResult:
    """Select the better EPUB candidate without allowing quality regressions."""

    baseline_epubcheck_payload = dict(baseline_epubcheck or {})
    candidate_epubcheck_payload = dict(candidate_epubcheck or {})
    baseline_scoring = score_epub_premium_quality(baseline_bytes, epubcheck=baseline_epubcheck_payload)
    candidate_scoring = score_epub_premium_quality(candidate_bytes, epubcheck=candidate_epubcheck_payload)
    reason_codes = _rejection_reason_codes(
        baseline_scoring=baseline_scoring,
        candidate_scoring=candidate_scoring,
        baseline_epubcheck=baseline_epubcheck_payload,
        candidate_epubcheck=candidate_epubcheck_payload,
    )
    rejected = bool(reason_codes)
    if rejected:
        selected_label = baseline_label
        rejected_label = candidate_label
        selected_bytes = baseline_bytes
        selected_epubcheck = baseline_epubcheck_payload
        selected_scoring = baseline_scoring
        status = "rejected"
        reason_codes = _dedupe([QUALITY_REGRESSION_CODE, MONOTONIC_REGRESSION_CODE, *reason_codes])
    else:
        selected_label = candidate_label
        rejected_label = ""
        selected_bytes = candidate_bytes
        selected_epubcheck = candidate_epubcheck_payload
        selected_scoring = candidate_scoring
        status = "accepted"
        reason_codes = _acceptance_reason_codes(
            baseline_scoring=baseline_scoring,
            candidate_scoring=candidate_scoring,
        )

    baseline_summary = _scoring_summary(baseline_scoring)
    candidate_summary = _scoring_summary(candidate_scoring)
    score_delta = round(candidate_summary["premium_score"] - baseline_summary["premium_score"], 3)
    blocker_delta = int(candidate_summary["blocker_count"] - baseline_summary["blocker_count"])
    report = {
        "status": status,
        "selected_candidate": selected_label,
        "rejected_candidate": rejected_label,
        "selected_stage": selected_label,
        "rejected_stage": rejected_label,
        "reason_codes": reason_codes,
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "baseline_score": baseline_summary["premium_score"],
        "candidate_score": candidate_summary["premium_score"],
        "score_delta": score_delta,
        "blocker_delta": blocker_delta,
        "selected_is_candidate": not rejected,
        "selected_is_recovered": (not rejected) and _looks_like_recovery_label(candidate_label),
    }
    return QualitySelectionResult(
        selected_bytes=selected_bytes,
        selected_epubcheck=selected_epubcheck,
        selected_scoring=selected_scoring,
        baseline_scoring=baseline_scoring,
        candidate_scoring=candidate_scoring,
        report=report,
    )


def _rejection_reason_codes(
    *,
    baseline_scoring: Mapping[str, Any],
    candidate_scoring: Mapping[str, Any],
    baseline_epubcheck: Mapping[str, Any],
    candidate_epubcheck: Mapping[str, Any],
) -> list[str]:
    reasons: list[str] = []
    baseline_summary = _scoring_summary(baseline_scoring)
    candidate_summary = _scoring_summary(candidate_scoring)
    if (
        candidate_summary["premium_score"] + SCORE_REGRESSION_TOLERANCE < baseline_summary["premium_score"]
        and (
            baseline_summary["kindle_ready"]
            or baseline_summary["release_verdict"] in READY_VERDICTS
        )
    ):
        reasons.append("premium_score_regressed")
    if candidate_summary["blocker_count"] > baseline_summary["blocker_count"]:
        reasons.append("blocker_count_increased")
    if baseline_summary["kindle_ready"] and not candidate_summary["kindle_ready"]:
        reasons.append("kindle_ready_regressed")
    if baseline_summary["technical_valid"] and not candidate_summary["technical_valid"]:
        reasons.append("technical_validity_regressed")
    if (
        baseline_summary["release_verdict"] in READY_VERDICTS
        and candidate_summary["release_verdict"] in BLOCKED_VERDICTS
    ):
        reasons.append("release_verdict_regressed")
    if _status_failed(candidate_epubcheck.get("status")) and not _status_failed(baseline_epubcheck.get("status")):
        reasons.append("epubcheck_regressed")
    if _issue_code_count(candidate_scoring, "magazine_non_content_chapter") > _issue_code_count(
        baseline_scoring,
        "magazine_non_content_chapter",
    ):
        reasons.append("new_magazine_non_content_chapter")
    return _dedupe(reasons)


def _acceptance_reason_codes(
    *,
    baseline_scoring: Mapping[str, Any],
    candidate_scoring: Mapping[str, Any],
) -> list[str]:
    baseline = _scoring_summary(baseline_scoring)
    candidate = _scoring_summary(candidate_scoring)
    reasons = ["quality_no_hard_regression"]
    if candidate["premium_score"] > baseline["premium_score"] + SCORE_REGRESSION_TOLERANCE:
        reasons.append("premium_score_improved")
    if candidate["blocker_count"] < baseline["blocker_count"]:
        reasons.append("blocker_count_reduced")
    if not baseline["kindle_ready"] and candidate["kindle_ready"]:
        reasons.append("kindle_ready_improved")
    return reasons


def _scoring_summary(scoring: Mapping[str, Any]) -> dict[str, Any]:
    issue_counts = dict(scoring.get("issue_counts") or {})
    issues = [dict(item) for item in scoring.get("issues", []) if isinstance(item, Mapping)]
    blocker_count = int(issue_counts.get("blocker", 0) or sum(1 for item in issues if item.get("severity") == "blocker"))
    warning_count = int(issue_counts.get("warning", 0) or sum(1 for item in issues if item.get("severity") == "warning"))
    review_count = int(issue_counts.get("review", 0) or sum(1 for item in issues if item.get("severity") == "review"))
    return {
        "status": str(scoring.get("status", "") or ""),
        "premium_score": _float_value(scoring.get("premium_score")),
        "technical_valid": bool(scoring.get("technical_valid")),
        "kindle_ready": bool(scoring.get("kindle_ready")),
        "premium_ready": bool(scoring.get("premium_ready")),
        "release_verdict": str(scoring.get("release_verdict", "") or ""),
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "review_count": review_count,
        "issue_codes": _issue_codes(issues),
    }


def _issue_codes(issues: list[dict[str, Any]]) -> list[str]:
    return _dedupe([str(issue.get("code", "") or "") for issue in issues])


def _issue_code_count(scoring: Mapping[str, Any], code: str) -> int:
    return sum(
        1
        for issue in scoring.get("issues", []) or []
        if isinstance(issue, Mapping) and str(issue.get("code", "") or "") == code
    )


def _status_failed(status: Any) -> bool:
    return str(status or "").strip().lower() in {"failed", "fail", "error"}


def _looks_like_recovery_label(label: str) -> bool:
    normalized = str(label or "").strip().lower()
    return any(token in normalized for token in ("recovery", "recovered", "repair", "repaired", "heading"))


def _float_value(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
