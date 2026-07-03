from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from conversion_api_contracts import ConversionDownloadState, resolve_conversion_download_state
from quality_cockpit_issues import build_quality_cockpit_issue_groups
from quality_cockpit_preview import build_quality_cockpit_preview


KNOWN_JOB_STATUSES = {"queued", "running", "repairing_headings", "ready", "failed", "timed_out"}
READY_JOB_STATUS = "ready"
FAILED_JOB_STATUS = "failed"
TIMED_OUT_JOB_STATUS = "timed_out"

WARNING_STATUSES = {"warning", "warnings", "passed_with_warnings", "pass_with_review"}
FAILED_STATUSES = {"failed", "fail", "error"}
QUALITY_COMPLETENESS_SECTIONS = (
    ("validation", "Validation"),
    ("epubcheck", "EPUBCheck"),
    ("toc", "TOC"),
    ("metadata", "Metadata"),
    ("links", "Links"),
    ("visible_junk", "Visible junk"),
    ("assets", "Assets"),
    ("text_cleanup", "Text cleanup"),
    ("reference_cleanup", "Reference cleanup"),
    ("semantic_cleanup", "Semantic cleanup"),
    ("ocr_quality", "OCR quality"),
    ("reading_order", "Reading order"),
    ("table_semantics", "Table semantics"),
)
QUALITY_COMPLETENESS_NOT_REPORTED_STATUSES = {"", "not_reported", "unavailable", "unknown", "skipped"}
SEND_TO_KINDLE_EMAIL_SAFE_BYTES = 50 * 1024 * 1024


def _coerce_text(value: Any, *, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


def _coerce_first_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _coerce_text(value)
        if text:
            return text
    return default


def _coerce_status(value: Any, *, default: str = "unknown") -> str:
    normalized = _coerce_text(value, default=default).lower()
    if not normalized:
        return default
    return normalized


def _coerce_non_negative_int(value: Any, *, default: int = 0) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return default
    return max(default, converted)


def _coerce_optional_non_negative_int(value: Any) -> int | None:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    if converted < 0:
        return None
    return converted


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return None


def _coerce_optional_non_negative_number(value: Any) -> int | float | None:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return None
    if converted < 0:
        return None
    if converted.is_integer():
        return int(converted)
    return round(converted, 2)


def _first_non_none(*values: int | None) -> int | None:
    for value in values:
        if value is not None:
            return value
    return None


def _coerce_first_optional_non_negative_int(*values: Any) -> int | None:
    return _first_non_none(*(_coerce_optional_non_negative_int(value) for value in values))


def _coerce_confidence(value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return 0.0
    if converted < 0:
        return 0.0
    if converted > 1:
        return 1.0
    return converted


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_of_dicts(value: Any, *, limit: int) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    items: list[dict[str, Any]] = []
    for raw_item in value:
        if isinstance(raw_item, Mapping):
            items.append(dict(raw_item))
        elif raw_item not in (None, ""):
            items.append({"message": str(raw_item)})
        if len(items) >= limit:
            break
    return tuple(items)


def _string_list(value: Any, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for raw_item in value:
        item = _coerce_text(raw_item)
        if not item:
            continue
        items.append(item)
        if len(items) >= limit:
            break
    return tuple(items)


def _normalize_quality_status(value: Any, *, default: str = "unavailable") -> str:
    normalized = _coerce_status(value, default=default)
    if normalized in {"pass", "passed"}:
        return "passed"
    if normalized in WARNING_STATUSES:
        return "passed_with_warnings"
    if normalized in FAILED_STATUSES:
        return "failed"
    if normalized in {"skipped", "unavailable"}:
        return "unavailable"
    return default


def _phase_for_job_status(status: str) -> str:
    if status == "queued":
        return "queued"
    if status == "running":
        return "converting"
    if status == "repairing_headings":
        return "heading_repair"
    if status == READY_JOB_STATUS:
        return "completed"
    if status == FAILED_JOB_STATUS:
        return "failed"
    if status == TIMED_OUT_JOB_STATUS:
        return "failed"
    return "unknown"


def _severity_for_ready_state(
    *,
    quality_available: bool,
    validation_status: str,
    heading_repair_status: str,
    warning_count: int,
    review_count: int,
    size_budget_status: str,
) -> str:
    if not quality_available:
        return "warning"
    if validation_status == "failed" or size_budget_status == "failed":
        return "error"
    if (
        validation_status == "passed_with_warnings"
        or heading_repair_status == "failed"
        or warning_count > 0
        or review_count > 0
        or size_budget_status == "passed_with_warnings"
    ):
        return "warning"
    return "success"


@dataclass(frozen=True)
class QualityStateAlert:
    level: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class HighRiskPageState:
    page: int | None = None
    title: str = ""
    kind: str = ""
    flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "title": self.title,
            "kind": self.kind,
            "flags": list(self.flags),
        }


@dataclass(frozen=True)
class HighRiskSectionState:
    title: str = ""
    pages: tuple[int, int] | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "pages": list(self.pages) if self.pages else None,
            "flags": list(self.flags),
        }


@dataclass(frozen=True)
class ValidationState:
    status: str = "unavailable"
    tool: str = "unknown"

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "tool": self.tool,
        }


@dataclass(frozen=True)
class HeadingRepairState:
    status: str = "unavailable"
    release: str = "unavailable"
    toc_before: int = 0
    toc_after: int = 0
    removed: int = 0
    review: int = 0
    epubcheck: str = "unavailable"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "release": self.release,
            "toc_before": self.toc_before,
            "toc_after": self.toc_after,
            "removed": self.removed,
            "review": self.review,
            "epubcheck": self.epubcheck,
            "error": self.error,
        }


@dataclass(frozen=True)
class AuditState:
    warning_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    high_risk_pages: int = 0
    high_risk_page_list: tuple[HighRiskPageState, ...] = field(default_factory=tuple)
    high_risk_sections: int = 0
    high_risk_section_list: tuple[HighRiskSectionState, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_count": self.warning_count,
            "warnings": list(self.warnings),
            "high_risk_pages": self.high_risk_pages,
            "high_risk_page_list": [item.to_dict() for item in self.high_risk_page_list],
            "high_risk_sections": self.high_risk_sections,
            "high_risk_section_list": [item.to_dict() for item in self.high_risk_section_list],
        }


@dataclass(frozen=True)
class RenderBudgetState:
    budget_class: str = ""
    attempt: str = ""
    target_warn_bytes: int = 0
    target_hard_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_class": self.budget_class,
            "attempt": self.attempt,
            "target_warn_bytes": self.target_warn_bytes,
            "target_hard_bytes": self.target_hard_bytes,
        }


@dataclass(frozen=True)
class SizeBudgetState:
    status: str = "unavailable"
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "message": self.message,
        }


@dataclass(frozen=True)
class QualityRawSignalsState:
    warning_count: int = 0
    high_risk_pages: int = 0
    high_risk_sections: int = 0
    heading_review_count: int = 0
    output_size_bytes: int | None = None
    target_warn_bytes: int = 0
    target_hard_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_count": self.warning_count,
            "high_risk_pages": self.high_risk_pages,
            "high_risk_sections": self.high_risk_sections,
            "heading_review_count": self.heading_review_count,
            "output_size_bytes": self.output_size_bytes,
            "target_warn_bytes": self.target_warn_bytes,
            "target_hard_bytes": self.target_hard_bytes,
        }


@dataclass(frozen=True)
class QualityVerdictState:
    status: str
    severity: str
    requires_manual_review: bool
    blocks_download: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "severity": self.severity,
            "requires_manual_review": self.requires_manual_review,
            "blocks_download": self.blocks_download,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class QualitySummaryState:
    profile: str = "unknown"
    strategy: str | None = None
    confidence: float = 0.0
    layout: str = "reflowable"
    sections: int = 0
    assets: int = 0
    output_size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "strategy": self.strategy,
            "confidence": self.confidence,
            "layout": self.layout,
            "sections": self.sections,
            "assets": self.assets,
            "output_size_bytes": self.output_size_bytes,
        }


@dataclass(frozen=True)
class QualityCompletenessSectionState:
    key: str
    label: str
    status: str = "not_reported"
    reported: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "reported": self.reported,
            "message": self.message,
        }


@dataclass(frozen=True)
class QualityCompletenessState:
    score: int = 0
    status: str = "not_reported"
    expected_sections: int = len(QUALITY_COMPLETENESS_SECTIONS)
    reported_sections: int = 0
    missing_count: int = len(QUALITY_COMPLETENESS_SECTIONS)
    not_reported_count: int = len(QUALITY_COMPLETENESS_SECTIONS)
    missing_sections: tuple[str, ...] = field(default_factory=tuple)
    not_reported_sections: tuple[str, ...] = field(default_factory=tuple)
    sections: tuple[QualityCompletenessSectionState, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "status": self.status,
            "expected_sections": self.expected_sections,
            "reported_sections": self.reported_sections,
            "missing_count": self.missing_count,
            "not_reported_count": self.not_reported_count,
            "missing_sections": list(self.missing_sections),
            "not_reported_sections": list(self.not_reported_sections),
            "sections": [section.to_dict() for section in self.sections],
        }


@dataclass(frozen=True)
class ConversionQualityStateRequest:
    job_status: str
    job_id: str = ""
    source_type: str = ""
    filename: str = ""
    message: str = ""
    error: str = ""
    error_code: str = ""
    conversion_metadata: Mapping[str, Any] = field(default_factory=dict)
    output_size_bytes: int | None = None
    download_url: str = ""
    output_path: str = ""
    output_path_exists: bool | None = None
    sentry_event_id: str = ""

    @classmethod
    def from_job_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        download_url: str | None = None,
    ) -> "ConversionQualityStateRequest":
        conversion_metadata = _mapping(payload.get("metadata")) or _mapping(payload.get("conversion"))
        return cls(
            job_id=_coerce_text(payload.get("job_id")),
            job_status=_coerce_text(payload.get("status"), default="unknown"),
            source_type=_coerce_text(payload.get("source_type")),
            filename=_coerce_text(payload.get("filename")),
            message=_coerce_text(payload.get("message")),
            error=_coerce_text(payload.get("error")),
            error_code=_coerce_text(payload.get("error_code")),
            conversion_metadata=conversion_metadata,
            output_size_bytes=_coerce_optional_non_negative_int(payload.get("output_size_bytes")),
            download_url=_coerce_text(download_url or payload.get("download_url")),
            output_path=_coerce_text(payload.get("output_path")),
            output_path_exists=_coerce_optional_bool(payload.get("output_path_exists")),
            sentry_event_id=_coerce_first_text(payload.get("sentry_event_id"), conversion_metadata.get("sentry_event_id")),
        )


@dataclass(frozen=True)
class ConversionQualityState:
    status: str
    phase: str
    is_terminal: bool
    quality_available: bool
    download_ready: bool
    download_available: bool
    download_state: ConversionDownloadState
    reading_verdict: str
    release_verdict: str
    release_blocked: bool
    quality_blockers: tuple[dict[str, Any], ...]
    user_facing_verdict: dict[str, Any]
    user_facing_reasons: tuple[dict[str, Any], ...]
    send_to_kindle_ready: bool
    send_to_kindle_blockers: tuple[dict[str, Any], ...]
    kindle_delivery: dict[str, Any]
    score: int | float
    sendable: bool
    kindle_ready: bool
    premium_ready: bool
    blockers: tuple[dict[str, Any], ...]
    warnings: tuple[dict[str, Any], ...]
    reports: dict[str, Any]
    artifacts: dict[str, Any]
    sentry_event_id: str
    overall_severity: str
    source_type: str
    filename: str
    message: str
    error: str
    download_url: str
    summary: QualitySummaryState
    validation: ValidationState
    heading_repair: HeadingRepairState
    audit: AuditState
    render_budget: RenderBudgetState
    size_budget: SizeBudgetState
    content_metrics: dict[str, Any]
    text_cleanup: dict[str, Any]
    reference_cleanup: dict[str, Any]
    semantic_cleanup: dict[str, Any]
    ocr_quality: dict[str, Any]
    reading_order: dict[str, Any]
    asset_summary: dict[str, Any]
    magazine_quality_preview: dict[str, Any]
    toc_preview: dict[str, Any]
    epubcheck_detail: dict[str, Any]
    metadata_summary: dict[str, Any]
    metadata_health: dict[str, Any]
    link_health: dict[str, Any]
    visible_junk: dict[str, Any]
    premium_scoring: dict[str, Any]
    quality_selection: dict[str, Any]
    ai_quality: dict[str, Any]
    ai_quality_verification: dict[str, Any]
    quality_policy_verifier: dict[str, Any]
    trained_quality_model_status: str
    route_model_shadow: dict[str, Any]
    model_attribution: dict[str, Any]
    stage_timings: dict[str, Any]
    quality_gate_mode: str
    issue_groups: dict[str, list[dict[str, Any]]]
    quality_completeness: QualityCompletenessState
    raw_signals: QualityRawSignalsState
    verdict: QualityVerdictState
    alerts: tuple[QualityStateAlert, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "phase": self.phase,
            "is_terminal": self.is_terminal,
            "quality_available": self.quality_available,
            "download_ready": self.download_ready,
            "download_available": self.download_available,
            "download_state": self.download_state.to_dict(),
            "reading_verdict": self.reading_verdict,
            "release_verdict": self.release_verdict,
            "release_blocked": self.release_blocked,
            "quality_blockers": [dict(item) for item in self.quality_blockers],
            "user_facing_verdict": self.user_facing_verdict,
            "user_facing_reasons": [dict(item) for item in self.user_facing_reasons],
            "send_to_kindle_ready": self.send_to_kindle_ready,
            "send_to_kindle_blockers": [dict(item) for item in self.send_to_kindle_blockers],
            "kindle_delivery": self.kindle_delivery,
            "score": self.score,
            "sendable": self.sendable,
            "kindle_ready": self.kindle_ready,
            "premium_ready": self.premium_ready,
            "blockers": [dict(item) for item in self.blockers],
            "warnings": [dict(item) for item in self.warnings],
            "reports": self.reports,
            "artifacts": self.artifacts,
            "sentry_event_id": self.sentry_event_id,
            "overall_severity": self.overall_severity,
            "source_type": self.source_type,
            "filename": self.filename,
            "message": self.message,
            "error": self.error,
            "download_url": self.download_url,
            "summary": self.summary.to_dict(),
            "validation": self.validation.to_dict(),
            "heading_repair": self.heading_repair.to_dict(),
            "audit": self.audit.to_dict(),
            "render_budget": self.render_budget.to_dict(),
            "size_budget": self.size_budget.to_dict(),
            "content_metrics": self.content_metrics,
            "text_cleanup": self.text_cleanup,
            "reference_cleanup": self.reference_cleanup,
            "semantic_cleanup": self.semantic_cleanup,
            "ocr_quality": self.ocr_quality,
            "reading_order": self.reading_order,
            "asset_summary": self.asset_summary,
            "magazine_quality_preview": self.magazine_quality_preview,
            "toc_preview": self.toc_preview,
            "epubcheck_detail": self.epubcheck_detail,
            "metadata_summary": self.metadata_summary,
            "metadata_health": self.metadata_health,
            "link_health": self.link_health,
            "visible_junk": self.visible_junk,
            "premium_scoring": self.premium_scoring,
            "quality_selection": self.quality_selection,
            "ai_quality": self.ai_quality,
            "ai_quality_verification": self.ai_quality_verification,
            "quality_policy_verifier": self.quality_policy_verifier,
            "trained_quality_model_status": self.trained_quality_model_status,
            "route_model_shadow": self.route_model_shadow,
            "model_attribution": self.model_attribution,
            "stage_timings": self.stage_timings,
            "quality_gate_mode": self.quality_gate_mode,
            "issue_groups": self.issue_groups,
            "quality_completeness": self.quality_completeness.to_dict(),
            "raw_signals": self.raw_signals.to_dict(),
            "verdict": self.verdict.to_dict(),
            "alerts": [alert.to_dict() for alert in self.alerts],
        }


def _build_high_risk_pages(raw_items: Any) -> tuple[HighRiskPageState, ...]:
    if not isinstance(raw_items, list):
        return ()
    items: list[HighRiskPageState] = []
    for raw_item in raw_items:
        payload = _mapping(raw_item)
        page = _coerce_first_optional_non_negative_int(payload.get("page"), payload.get("page_index"))
        title = _coerce_text(payload.get("title"))
        kind = _coerce_first_text(payload.get("kind"), payload.get("content_type"))
        flags = _string_list(payload.get("flags"), limit=4) or _string_list(payload.get("risk_flags"), limit=4)
        if page is None and not title and not kind and not flags:
            continue
        items.append(HighRiskPageState(page=page, title=title, kind=kind, flags=flags))
        if len(items) >= 20:
            break
    return tuple(items)


def _build_high_risk_sections(raw_items: Any) -> tuple[HighRiskSectionState, ...]:
    if not isinstance(raw_items, list):
        return ()
    items: list[HighRiskSectionState] = []
    for raw_item in raw_items:
        payload = _mapping(raw_item)
        title = _coerce_text(payload.get("title"))
        raw_pages = payload.get("pages")
        if not isinstance(raw_pages, (list, tuple)):
            raw_pages = payload.get("page_range")
        pages: tuple[int, int] | None = None
        if isinstance(raw_pages, (list, tuple)) and len(raw_pages) == 2:
            first = _coerce_optional_non_negative_int(raw_pages[0])
            second = _coerce_optional_non_negative_int(raw_pages[1])
            if first is not None and second is not None:
                pages = (first, second)
        flags = _string_list(payload.get("flags"), limit=4) or _string_list(payload.get("risk_flags"), limit=4)
        if not title and pages is None and not flags:
            continue
        items.append(HighRiskSectionState(title=title, pages=pages, flags=flags))
        if len(items) >= 20:
            break
    return tuple(items)


def _build_alerts(
    *,
    job_status: str,
    quality_available: bool,
    error: str,
    error_code: str,
    validation: ValidationState,
    heading_repair: HeadingRepairState,
    audit: AuditState,
    size_budget: SizeBudgetState,
) -> tuple[QualityStateAlert, ...]:
    alerts: list[QualityStateAlert] = []
    seen: set[tuple[str, str, str]] = set()

    def push(level: str, code: str, message: str) -> None:
        normalized_message = _coerce_text(message)
        if not normalized_message:
            return
        marker = (level, code, normalized_message)
        if marker in seen:
            return
        seen.add(marker)
        alerts.append(QualityStateAlert(level=level, code=code, message=normalized_message))

    if job_status == FAILED_JOB_STATUS:
        push("error", error_code or "conversion_failed", error or "Conversion failed before quality data was available.")
    elif job_status == TIMED_OUT_JOB_STATUS:
        push("error", "conversion_timeout", error or "Conversion timed out before quality data was available.")

    if job_status == READY_JOB_STATUS and not quality_available:
        push("warning", "quality_state_incomplete", "Ready conversion is missing normalized quality metadata.")

    if validation.status == "failed":
        tool_label = validation.tool or "validation"
        push("error", "validation_failed", f"{tool_label} reported blocking validation issues.")

    if heading_repair.status == "failed":
        push(
            "warning",
            "heading_repair_failed",
            heading_repair.error or "Heading repair failed and the base EPUB was preserved.",
        )

    if size_budget.status == "failed":
        push("error", "size_budget_failed", size_budget.message or "Size budget gate failed.")
    elif size_budget.status == "passed_with_warnings":
        push("warning", "size_budget_warning", size_budget.message or "Size budget completed with warnings.")

    if audit.high_risk_pages or audit.high_risk_sections:
        push(
            "warning",
            "manual_review_needed",
            (
                "Premium audit flagged "
                f"{audit.high_risk_sections} section(s) and {audit.high_risk_pages} page(s) for manual review."
            ),
        )

    for warning in audit.warnings[:5]:
        push("warning", "quality_warning", warning)

    return tuple(alerts)


def _build_raw_signals_state(
    *,
    summary: QualitySummaryState,
    heading_repair: HeadingRepairState,
    audit: AuditState,
    render_budget: RenderBudgetState,
) -> QualityRawSignalsState:
    return QualityRawSignalsState(
        warning_count=audit.warning_count,
        high_risk_pages=audit.high_risk_pages,
        high_risk_sections=audit.high_risk_sections,
        heading_review_count=heading_repair.review,
        output_size_bytes=summary.output_size_bytes,
        target_warn_bytes=render_budget.target_warn_bytes,
        target_hard_bytes=render_budget.target_hard_bytes,
    )


def _build_verdict_state(
    *,
    job_status: str,
    overall_severity: str,
    quality_available: bool,
    download_available: bool,
    validation: ValidationState,
    heading_repair: HeadingRepairState,
    audit: AuditState,
    size_budget: SizeBudgetState,
    alerts: tuple[QualityStateAlert, ...],
    issue_groups: Mapping[str, Any] | None = None,
) -> QualityVerdictState:
    if job_status in {FAILED_JOB_STATUS, TIMED_OUT_JOB_STATUS}:
        status = "failed"
    elif job_status != READY_JOB_STATUS:
        status = "pending"
    elif overall_severity == "error":
        status = "failed"
    elif overall_severity == "warning":
        status = "passed_with_warnings"
    elif quality_available:
        status = "passed"
    else:
        status = "unknown"

    groups = _dict_payload(issue_groups)
    blocker_issues = groups.get("blockers") if isinstance(groups.get("blockers"), list) else []
    warning_issues = groups.get("warnings") if isinstance(groups.get("warnings"), list) else []
    review_issues = groups.get("review") if isinstance(groups.get("review"), list) else []
    if blocker_issues:
        status = "failed"
    final_severity = "error" if blocker_issues else "warning" if warning_issues or review_issues else overall_severity

    review_count = heading_repair.review + audit.high_risk_pages + audit.high_risk_sections + len(review_issues)
    reason_codes = [alert.code for alert in alerts]
    reason_codes.extend(
        str(issue.get("code"))
        for issue in [*blocker_issues, *warning_issues, *review_issues]
        if isinstance(issue, Mapping) and issue.get("code")
    )
    if validation.status == "failed":
        reason_codes.append("validation_failed")
    if size_budget.status == "failed":
        reason_codes.append("size_budget_failed")
    if heading_repair.status == "failed":
        reason_codes.append("heading_repair_failed")

    return QualityVerdictState(
        status=status,
        severity=final_severity,
        requires_manual_review=review_count > 0,
        blocks_download=not download_available,
        reasons=tuple(dict.fromkeys(reason_codes)),
    )


def _dict_payload(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _quality_issue_list(issue_groups: Mapping[str, Any] | None, key: str) -> tuple[dict[str, Any], ...]:
    groups = _dict_payload(issue_groups)
    raw_items = groups.get(key)
    if not isinstance(raw_items, list):
        return ()
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        payload = _dict_payload(raw_item)
        if not payload:
            continue
        code = _coerce_text(payload.get("code"))
        message = _coerce_text(payload.get("message"))
        source = _coerce_text(payload.get("source"), default="quality")
        if not code and not message:
            continue
        normalized = {
            "severity": _coerce_text(payload.get("severity"), default="blocker") or "blocker",
            "code": code or "quality_blocker",
            "message": message or "Quality blocker reported.",
            "source": source or "quality",
        }
        suggested_action = _coerce_text(payload.get("suggested_action"))
        if suggested_action:
            normalized["suggested_action"] = suggested_action
        for optional_key in ("page", "section", "file"):
            if optional_key in payload:
                normalized[optional_key] = payload[optional_key]
        items.append(normalized)
    return tuple(items)


def _build_quality_blockers(
    *,
    job_status: str,
    error: str,
    error_code: str,
    issue_groups: Mapping[str, Any] | None,
    alerts: tuple[QualityStateAlert, ...],
) -> tuple[dict[str, Any], ...]:
    blockers = list(_quality_issue_list(issue_groups, "blockers"))
    seen_codes = {_coerce_text(item.get("code")) for item in blockers}
    if job_status == TIMED_OUT_JOB_STATUS and "conversion_timeout" not in seen_codes:
        blockers.insert(
            0,
            {
                "severity": "blocker",
                "code": "conversion_timeout",
                "message": error or "Conversion timed out before an EPUB was available.",
                "source": "conversion",
                "suggested_action": "Retry the conversion after checking the source file and local runtime.",
            },
        )
    elif job_status == FAILED_JOB_STATUS and (error_code or "conversion_failed") not in seen_codes:
        blocker_code = error_code or "conversion_failed"
        conversion_error = error or next((alert.message for alert in alerts if alert.code == blocker_code), "")
        suggested_action = (
            "Use a shorter sample in the browser UI or run the full document as an offline CLI/batch conversion."
            if blocker_code == "interactive_runtime_budget_exceeded"
            else "Fix the conversion error and run the job again."
        )
        blockers.insert(
            0,
            {
                "severity": "blocker",
                "code": blocker_code,
                "message": conversion_error or "Conversion failed before an EPUB was available.",
                "source": "conversion",
                "suggested_action": suggested_action,
            },
        )
    return tuple(blockers)


def _coerce_public_score(
    *,
    premium_scoring: Mapping[str, Any],
    conversion_metadata: Mapping[str, Any],
    quality_completeness: QualityCompletenessState,
) -> int | float:
    nested_scores = _dict_payload(premium_scoring.get("scores"))
    score = _coerce_optional_non_negative_number(
        _first_non_empty_value(
            premium_scoring.get("premium_score"),
            nested_scores.get("premium_score"),
            premium_scoring.get("score"),
            conversion_metadata.get("quality_score"),
            quality_completeness.score,
        )
    )
    return score if score is not None else 0


def _first_non_empty_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _premium_bool(premium_scoring: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = premium_scoring.get(key)
    if isinstance(value, bool):
        return value
    parsed = _coerce_optional_bool(value)
    if parsed is not None:
        return parsed
    return default


def _public_sendable(*, premium_scoring: Mapping[str, Any], download_available: bool) -> bool:
    mail_sendable = _coerce_text(premium_scoring.get("mail_sendable")).lower()
    if mail_sendable:
        return mail_sendable in {"yes", "likely", "web_only", "true", "sendable"}
    return download_available


def _dedupe_public_issues(items: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        code = _coerce_text(item.get("code"))
        message = _coerce_text(item.get("message"))
        source = _coerce_text(item.get("source"), default="quality")
        marker = (code, message, source)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(dict(item))
    return tuple(deduped)


def _build_public_warnings(
    *,
    issue_groups: Mapping[str, Any],
    alerts: tuple[QualityStateAlert, ...],
) -> tuple[dict[str, Any], ...]:
    warnings = [*_quality_issue_list(issue_groups, "warnings"), *_quality_issue_list(issue_groups, "review")]
    warnings.extend(
        {
            "severity": "warning",
            "code": alert.code,
            "message": alert.message,
            "source": "quality_state",
        }
        for alert in alerts
        if alert.level == "warning"
    )
    return _dedupe_public_issues(warnings)


def _build_public_reports(*, job_status: str, job_id: str) -> dict[str, str]:
    if job_status != READY_JOB_STATUS or not job_id:
        return {}
    return {
        "json": f"/convert/report/{job_id}.json",
        "markdown": f"/convert/report/{job_id}.md",
    }


def _build_public_artifacts(*, download_url: str) -> dict[str, str]:
    if not download_url:
        return {}
    return {"download_url": download_url}


def _has_structural_reader_failure(
    *,
    validation: ValidationState,
    epubcheck_detail: Mapping[str, Any],
    issue_groups: Mapping[str, Any] | None,
) -> bool:
    if validation.status == "failed":
        return True
    if _normalize_cockpit_status(epubcheck_detail.get("status"), default="not_reported") == "failed":
        return True
    for issue in _quality_issue_list(issue_groups, "blockers"):
        code = _coerce_text(issue.get("code"))
        source = _coerce_text(issue.get("source"))
        if code in {"validation_failed", "epubcheck_failed", "link_health_failed"}:
            return True
        if source in {"validation", "epubcheck"}:
            return True
    return False


def _build_reading_verdict(
    *,
    job_status: str,
    download_available: bool,
    release_blocked: bool,
    overall_severity: str,
    quality_available: bool,
    validation: ValidationState,
    epubcheck_detail: Mapping[str, Any],
    issue_groups: Mapping[str, Any] | None,
) -> str:
    if job_status != READY_JOB_STATUS or not download_available:
        return "failed"
    if _has_structural_reader_failure(
        validation=validation,
        epubcheck_detail=epubcheck_detail,
        issue_groups=issue_groups,
    ):
        return "failed"
    has_warning_or_review = bool(_quality_issue_list(issue_groups, "warnings") or _quality_issue_list(issue_groups, "review"))
    if release_blocked or overall_severity == "warning" or has_warning_or_review or not quality_available:
        return "ready_with_review"
    return "ready"


def _build_release_verdict(
    *,
    job_status: str,
    download_available: bool,
    release_blocked: bool,
    reading_verdict: str,
    overall_severity: str,
    quality_available: bool,
    issue_groups: Mapping[str, Any] | None,
) -> str:
    if job_status in {FAILED_JOB_STATUS, TIMED_OUT_JOB_STATUS}:
        return "failed"
    if job_status != READY_JOB_STATUS or not download_available:
        return "failed"
    if release_blocked or reading_verdict == "failed":
        return "release_blocked"
    has_warning_or_review = bool(_quality_issue_list(issue_groups, "warnings") or _quality_issue_list(issue_groups, "review"))
    if reading_verdict == "ready_with_review" or overall_severity == "warning" or has_warning_or_review or not quality_available:
        return "ready_with_review"
    return "release_ready"


def _normalize_user_facing_reason(item: Mapping[str, Any], *, fallback_severity: str) -> dict[str, Any]:
    code = _coerce_text(item.get("code"), default="quality_review")
    message = _coerce_text(item.get("message"), default="Quality review is recommended.")
    source = _coerce_text(item.get("source"), default="quality")
    severity = _coerce_text(item.get("severity"), default=fallback_severity) or fallback_severity
    reason = {
        "severity": severity,
        "code": code,
        "message": message,
        "source": source or "quality",
    }
    suggested_action = _coerce_text(item.get("suggested_action"))
    if suggested_action:
        reason["suggested_action"] = suggested_action
    for optional_key in ("page", "section", "file"):
        if optional_key in item:
            reason[optional_key] = item[optional_key]
    return reason


def _build_user_facing_reasons(
    *,
    release_verdict: str,
    quality_blockers: tuple[dict[str, Any], ...],
    issue_groups: Mapping[str, Any] | None,
    alerts: tuple[QualityStateAlert, ...],
    limit: int = 5,
) -> tuple[dict[str, Any], ...]:
    reasons: list[dict[str, Any]] = []
    seen_codes: set[str] = set()

    def push(item: Mapping[str, Any], *, fallback_severity: str) -> None:
        if len(reasons) >= limit:
            return
        reason = _normalize_user_facing_reason(item, fallback_severity=fallback_severity)
        code = _coerce_text(reason.get("code"))
        if code in seen_codes:
            return
        seen_codes.add(code)
        reasons.append(reason)

    for blocker in quality_blockers:
        push(blocker, fallback_severity="blocker")
    for warning in _quality_issue_list(issue_groups, "warnings"):
        push(warning, fallback_severity="warning")
    for review in _quality_issue_list(issue_groups, "review"):
        push(review, fallback_severity="review")
    for alert in alerts:
        push(
            {
                "severity": "warning" if alert.level != "error" else "blocker",
                "code": alert.code,
                "message": alert.message,
                "source": "runtime",
            },
            fallback_severity="warning",
        )

    if not reasons and release_verdict == "ready_with_review":
        push(
            {
                "severity": "review",
                "code": "quality_review_recommended",
                "message": "Quality evidence indicates this EPUB should be checked before publication.",
                "source": "quality",
            },
            fallback_severity="review",
        )
    elif not reasons and release_verdict in {"release_blocked", "failed"}:
        push(
            {
                "severity": "blocker",
                "code": "release_not_ready",
                "message": "This EPUB is not ready for publication.",
                "source": "quality",
            },
            fallback_severity="blocker",
        )
    return tuple(reasons[:limit])


def _build_user_facing_verdict(
    *,
    job_status: str,
    release_verdict: str,
    reading_verdict: str,
    download_available: bool,
    release_blocked: bool,
    reasons: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    if release_verdict == "release_ready":
        decision = "ready"
        status = "ready"
        tone = "ready"
        label = "Publikuj"
        detail = "EPUB jest gotowy do czytania i publikacji."
    elif release_verdict == "ready_with_review":
        decision = "review"
        status = "review"
        tone = "review"
        label = "Kontrola"
        detail = "EPUB wygenerowany, ale wymaga kontroli jakości."
    elif job_status in {FAILED_JOB_STATUS, TIMED_OUT_JOB_STATUS} or release_verdict == "failed":
        decision = "blocked"
        status = "failed"
        tone = "failed"
        label = "Nie publikuj"
        detail = "Konwersja lub walidacja zakończyła się błędem."
    elif release_blocked or release_verdict == "release_blocked":
        decision = "blocked"
        status = "blocked"
        tone = "failed"
        label = "Nie publikuj"
        detail = "EPUB wygenerowany, ale wymaga naprawy przed publikacją."
    else:
        decision = "review"
        status = "pending"
        tone = "review"
        label = "Kontrola"
        detail = "Dowody jakości są niepełne albo niedostępne."
    if not download_available:
        download_label = "Pobranie niedostępne"
    elif decision == "blocked":
        download_label = "Pobierz szkic EPUB"
    else:
        download_label = "Pobierz EPUB"
    return {
        "decision": decision,
        "status": status,
        "tone": tone,
        "label": label,
        "detail": detail,
        "download_label": download_label,
        "download_available": download_available,
        "release_verdict": release_verdict,
        "reading_verdict": reading_verdict,
        "release_blocked": release_blocked,
        "top_reason_count": len(reasons),
    }


def _build_magazine_quality_preview(
    *,
    conversion_metadata: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    issue_groups: Mapping[str, Any] | None,
    premium_scoring: Mapping[str, Any] | None,
    text_cleanup: Mapping[str, Any] | None,
    asset_summary: Mapping[str, Any] | None,
    ai_quality_verification: Mapping[str, Any] | None,
    limit: int = 10,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(
        sample_type: str,
        *,
        severity: str,
        title: str,
        evidence: str,
        location: Mapping[str, Any] | None = None,
        suggested_action: str = "",
        source: str = "deterministic",
    ) -> None:
        if len(samples) >= limit:
            return
        normalized_title = _coerce_text(title, default=sample_type)
        normalized_evidence = _coerce_text(evidence, default=normalized_title)
        location_payload = {
            key: value
            for key, value in dict(location or {}).items()
            if value not in (None, "", [])
        }
        key = "|".join(
            [
                sample_type,
                normalized_title.lower(),
                normalized_evidence[:100].lower(),
                str(location_payload.get("href") or location_payload.get("file") or location_payload.get("page") or ""),
            ]
        )
        if key in seen:
            return
        seen.add(key)
        samples.append(
            {
                "type": sample_type,
                "severity": _coerce_text(severity, default="review") or "review",
                "title": normalized_title,
                "evidence": normalized_evidence,
                "location": location_payload,
                "suggested_action": _coerce_text(suggested_action),
                "source": _coerce_text(source, default="deterministic") or "deterministic",
            }
        )

    scoring = _mapping(premium_scoring)
    scoring_metrics = _mapping(scoring.get("metrics"))
    magazine_contract = _mapping(scoring.get("magazine_premium_quality"))
    article_map = _mapping(
        magazine_contract.get("article_map")
        or conversion_metadata.get("magazine_article_map")
        or _mapping(quality_report.get("magazine_premium_quality")).get("article_map")
        or quality_report.get("magazine_article_map")
        or quality_report.get("article_map")
    )

    for item in _list_of_dicts(article_map.get("truncated_titles"), limit=4):
        push(
            "truncated_title",
            severity="review",
            title=item.get("title") or "Podejrzany tytuł artykułu",
            evidence=item.get("reason") or item.get("title") or "Tytuł wygląda na urwany, zbyt długi albo lead-like.",
            location={"href": item.get("href") or item.get("chapter_href"), "page": item.get("page") or item.get("page_start")},
            suggested_action="Zweryfikuj tytuł artykułu i skróć wpis TOC.",
        )

    for item in _list_of_dicts(article_map.get("toc_missing_articles"), limit=4):
        push(
            "suspicious_toc",
            severity="warning",
            title=item.get("title") or "Artykuł poza TOC",
            evidence="Artykuł nie ma pewnego pokrycia w spisie treści magazynu.",
            location={"href": item.get("href") or item.get("chapter_href"), "page": item.get("page") or item.get("page_start")},
            suggested_action="Dodaj lub napraw wpis TOC dla realnego artykułu.",
        )

    for item in _list_of_dicts(article_map.get("articles"), limit=20):
        kind = _coerce_text(item.get("kind") or item.get("content_type")).lower()
        if kind in {"ad", "advertisement", "gallery", "sponsored", "newsletter"} or bool(item.get("toc_excluded")):
            push(
                "non_content_in_flow",
                severity="warning",
                title=item.get("title") or "Materiał nietreściowy w przepływie",
                evidence=f"Sekcja typu {kind or 'non-content'} wymaga świadomego umieszczenia poza głównym czytaniem.",
                location={"href": item.get("href") or item.get("chapter_href"), "page": item.get("page") or item.get("page_start")},
                suggested_action="Przenieś do materiałów dodatkowych albo wyłącz z linear flow.",
            )

    for issue in [
        *_quality_issue_list(issue_groups, "blockers"),
        *_quality_issue_list(issue_groups, "warnings"),
        *_quality_issue_list(issue_groups, "review"),
        *_list_of_dicts(scoring.get("issues"), limit=40),
    ]:
        code = _coerce_text(issue.get("code"))
        if code in {"toc_lead_used_as_title", "toc_non_content_entry", "toc_duplicate_label", "magazine_issue_toc_coverage_low", "magazine_article_coverage_low"}:
            sample_type = "suspicious_toc"
            suggested_action = "Sprawdź, czy TOC zawiera realny tytuł artykułu, a nie lead albo etykietę layoutu."
        elif code in {"ocr_glued_words_detected", "ocr_artifacts_need_review", "ocr_suspicious_unicode", "text_artifact_rate_failed", "magazine_text_artifact_rate_high"}:
            sample_type = "text_artifact"
            suggested_action = "Sprawdź próbki OCR i reguły dehyphenacji/sklejonych słów."
        elif code in {"magazine_non_content_chapter", "magazine_non_editorial_in_spine"}:
            sample_type = "non_content_in_flow"
            suggested_action = "Przenieś reklamę, galerię lub sponsor stub poza główny flow."
        elif code == "magazine_article_title_truncated":
            sample_type = "truncated_title"
            suggested_action = "Zweryfikuj tytuły artykułów i pokrycie TOC."
        elif code == "image_low_resolution_for_kindle":
            sample_type = "low_res_image"
            suggested_action = "Wyeksportuj wykres w większej rozdzielczości albo dodaj pełnowymiarowy fallback."
        else:
            continue
        push(
            sample_type,
            severity=_coerce_text(issue.get("severity"), default="review") or "review",
            title=code or sample_type,
            evidence=issue.get("message") or issue.get("detail") or code or sample_type,
            location={"file": issue.get("file"), "page": issue.get("page"), "section": issue.get("section")},
            suggested_action=issue.get("suggested_action") or suggested_action,
        )

    artifact_payload = _mapping(scoring_metrics.get("text_artifacts") or _mapping(text_cleanup).get("artifact_rate"))
    per_document = sorted(
        _list_of_dicts(artifact_payload.get("per_document"), limit=50),
        key=lambda item: int(item.get("artifact_count") or 0),
        reverse=True,
    )
    for item in per_document[:3]:
        if int(item.get("artifact_count") or 0) <= 0:
            continue
        push(
            "text_artifact",
            severity="review",
            title=item.get("document_path") or "Widoczne artefakty tekstu",
            evidence=f"{item.get('artifact_count')} artefaktów / {item.get('artifact_rate_per_1000_words')} na 1000 słów.",
            location={"file": item.get("document_path")},
            suggested_action="Otwórz ten rozdział i sprawdź sklejone lub rozbite słowa.",
        )

    for item in _list_of_dicts(_mapping(asset_summary).get("low_resolution_images"), limit=4):
        push(
            "low_res_image",
            severity="review",
            title=item.get("name") or item.get("path") or "Niska rozdzielczość obrazu",
            evidence=f"{item.get('width', '?')}x{item.get('height', '?')} px może być za mało dla wykresu na Kindle.",
            location={"file": item.get("path") or item.get("href") or item.get("name")},
            suggested_action="Sprawdź czy to wykres/diagram; jeśli tak, wygeneruj większy wariant.",
        )

    magazine_review = _mapping(_mapping(ai_quality_verification).get("magazine_review"))
    for item in _list_of_dicts(magazine_review.get("truncated_titles"), limit=3):
        push(
            "truncated_title",
            severity="review",
            title=item.get("title") or "AI: podejrzany tytuł",
            evidence=item.get("reason") or "AI reviewer oznaczył tytuł jako podejrzany.",
            location={"href": item.get("href")},
            suggested_action="Potwierdź deterministyczną regułą przed zmianą pipeline.",
            source="ai_review",
        )
    for item in _list_of_dicts(magazine_review.get("non_content_misclassified"), limit=3):
        push(
            "non_content_in_flow",
            severity="review",
            title=item.get("label") or item.get("title") or "AI: non-content w flow",
            evidence=item.get("reason") or "AI reviewer oznaczył materiał nietreściowy.",
            location={"href": item.get("href")},
            suggested_action="Potwierdź klasyfikację i dodaj test regresyjny.",
            source="ai_review",
        )
    for item in _list_of_dicts(magazine_review.get("ocr_cleanup_candidates"), limit=3):
        push(
            "text_artifact",
            severity="review",
            title=item.get("text") or item.get("label") or "AI: fragment OCR",
            evidence=item.get("reason") or item.get("suggested_fix") or "AI reviewer oznaczył fragment OCR.",
            location={"href": item.get("href")},
            suggested_action="Nie zmieniaj automatycznie; dodaj deterministyczną regułę po akceptacji.",
            source="ai_review",
        )

    return {
        "status": "reported" if samples else "not_reported",
        "sample_count": len(samples),
        "sample_limit": limit,
        "problem_samples": samples[:limit],
    }


def _build_send_to_kindle_blockers(
    *,
    job_status: str,
    download_available: bool,
    release_verdict: str,
    validation: ValidationState,
    epubcheck_detail: Mapping[str, Any],
    size_budget: SizeBudgetState,
    output_size_bytes: int | None,
    asset_summary: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    blockers: list[dict[str, Any]] = []

    def push(code: str, message: str, source: str, suggested_action: str) -> None:
        if any(item.get("code") == code for item in blockers):
            return
        blockers.append(
            {
                "severity": "blocker",
                "code": code,
                "message": message,
                "source": source,
                "suggested_action": suggested_action,
            }
        )

    if job_status != READY_JOB_STATUS or not download_available:
        push(
            "kindle_delivery_not_available",
            "EPUB file is not available for Send-to-Kindle delivery.",
            "download",
            "Finish conversion successfully and verify the download link.",
        )
        return tuple(blockers)

    if release_verdict != "release_ready":
        push(
            "kindle_delivery_release_not_ready",
            "EPUB is generated, but release quality is not ready for Kindle delivery.",
            "quality_state",
            "Resolve release blockers or review warnings before sending the file to Kindle.",
        )

    epubcheck_status = _normalize_cockpit_status(epubcheck_detail.get("status"), default=validation.status)
    if validation.status != "passed" or epubcheck_status != "passed":
        push(
            "kindle_delivery_validation_failed",
            "EPUBCheck or structural validation is not passing.",
            "validation",
            "Run EPUB validation and repair package, XHTML, link, or navigation issues before Send-to-Kindle.",
        )

    if output_size_bytes is None:
        push(
            "kindle_delivery_not_verified",
            "EPUB output size is not reported, so email delivery cannot be verified.",
            "size",
            "Report final EPUB size before marking the file as Send-to-Kindle ready.",
        )
    elif output_size_bytes > SEND_TO_KINDLE_EMAIL_SAFE_BYTES:
        push(
            "kindle_delivery_email_size_limit",
            f"EPUB is larger than the conservative email delivery budget ({SEND_TO_KINDLE_EMAIL_SAFE_BYTES} bytes).",
            "size",
            "Reduce assets or use a non-email Send-to-Kindle path after manual verification.",
        )

    asset_budget_status = _normalize_cockpit_status(asset_summary.get("asset_budget_status"), default="not_reported")
    if size_budget.status == "failed" or asset_budget_status == "failed":
        push(
            "kindle_delivery_size_budget_failed",
            "EPUB failed the publication size budget for Kindle delivery.",
            "size_budget",
            "Reduce image/media payloads or choose a lower-size render preset before Send-to-Kindle.",
        )
    elif size_budget.status == "passed_with_warnings" or asset_budget_status == "passed_with_warnings":
        push(
            "kindle_delivery_size_budget_review",
            "EPUB only passed the size budget with warnings.",
            "size_budget",
            "Review final EPUB size and asset budget warnings before Send-to-Kindle.",
        )

    image_quality = _dict_payload(asset_summary.get("image_quality"))
    cover = _dict_payload(image_quality.get("cover")) or _dict_payload(asset_summary.get("cover"))
    cover_status = _normalize_cockpit_status(cover.get("status"), default="not_reported")
    cover_issues = [str(item) for item in cover.get("issues", []) if str(item).strip()] if isinstance(cover.get("issues"), list) else []
    if cover_status in {"failed", "passed_with_warnings", "warning"} or cover_issues:
        push(
            "kindle_delivery_cover_image_quality",
            "Cover image aspect ratio or pixel size is risky for Kindle delivery.",
            "asset_summary",
            "Regenerate or replace the cover with a portrait image that meets Kindle cover dimensions.",
        )

    progressive_jpeg_count = _coerce_first_optional_non_negative_int(
        image_quality.get("progressive_jpeg_count"),
        asset_summary.get("progressive_jpeg_count"),
    )
    if progressive_jpeg_count and progressive_jpeg_count > 0:
        push(
            "kindle_delivery_progressive_jpeg",
            f"EPUB contains {progressive_jpeg_count} progressive JPEG image(s), which are risky for Kindle delivery.",
            "asset_summary",
            "Re-encode delivery images as baseline JPEG or PNG before Send-to-Kindle.",
        )

    low_resolution_count = _coerce_first_optional_non_negative_int(
        image_quality.get("low_resolution_count"),
        asset_summary.get("low_resolution_count"),
    )
    if low_resolution_count and low_resolution_count > 0:
        push(
            "kindle_delivery_low_resolution_images",
            f"EPUB contains {low_resolution_count} low-resolution image(s).",
            "asset_summary",
            "Replace low-resolution images or rerender at a higher image preset before Send-to-Kindle.",
        )

    unsupported_media_count = _coerce_non_negative_int(asset_summary.get("unsupported_media_count"))
    script_count = _coerce_non_negative_int(asset_summary.get("script_count"))
    media_risk_count = _coerce_non_negative_int(
        _coerce_first_optional_non_negative_int(
            image_quality.get("media_risk_count"),
            asset_summary.get("media_risk_count"),
        )
    )
    if unsupported_media_count > 0 or script_count > 0 or media_risk_count > 0:
        push(
            "kindle_delivery_unsupported_assets",
            "EPUB contains scripts or unsupported media that are risky for Kindle delivery.",
            "asset_summary",
            "Remove scripts, audio, video, fonts, or unsupported media from the delivery EPUB.",
        )

    return tuple(blockers)


def _build_kindle_delivery_payload(
    *,
    conversion_metadata: Mapping[str, Any],
    quality_report: Mapping[str, Any],
    automated_ready: bool,
    automated_blockers: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    reported = _dict_payload(conversion_metadata.get("kindle_delivery")) or _dict_payload(quality_report.get("kindle_delivery"))
    reported_status = _coerce_text(reported.get("status"), default="").lower()
    if automated_blockers:
        status = "failed"
    elif reported_status in {"previewer_passed", "send_to_kindle_passed", "failed"}:
        status = reported_status
    else:
        status = "not_verified"
    blockers = [dict(item) for item in automated_blockers]
    if automated_ready and status == "not_verified":
        blockers.append(
            {
                "severity": "review",
                "code": "kindle_delivery_not_verified",
                "message": "Kindle Previewer and Send-to-Kindle delivery have not been manually verified.",
                "source": "kindle_delivery",
                "suggested_action": "Open the EPUB in Kindle Previewer and complete a Send-to-Kindle delivery check before claiming 10/10.",
            }
        )
    return {
        "status": status,
        "automated_ready": automated_ready,
        "manual_required_for_premium": True,
        "blockers": blockers,
        "evidence": _dict_payload(reported.get("evidence")),
    }


def _not_reported_section() -> dict[str, Any]:
    return {"status": "not_reported"}


def _not_reported_health(label: str) -> dict[str, Any]:
    return {
        "label": label,
        "status": "not_reported",
        "count": None,
        "message": "",
    }


def _has_existing_file(path_value: str) -> bool:
    if not path_value:
        return False
    try:
        return Path(path_value).exists()
    except OSError:
        return False


def _normalize_cockpit_status(value: Any, *, default: str = "not_reported") -> str:
    normalized = _coerce_status(value, default=default)
    if normalized in {"pass", "passed", "ok", "success"}:
        return "passed"
    if normalized in WARNING_STATUSES:
        return "passed_with_warnings"
    if normalized in FAILED_STATUSES or normalized == "blocked":
        return "failed"
    if normalized in {"not_reported", "unavailable", "skipped"}:
        return normalized
    return normalized or default


def _normalize_optional_payload(value: Any, *, reported_status: str = "reported") -> dict[str, Any]:
    payload = _dict_payload(value)
    if not payload:
        return _not_reported_section()
    normalized = dict(payload)
    normalized.setdefault("status", reported_status)
    return normalized


def _trained_quality_model_status(verifier: Mapping[str, Any]) -> str:
    training_status = _coerce_text(verifier.get("training_status")).lower()
    model_version = _coerce_text(verifier.get("model_version")).lower()
    if (
        training_status.startswith("trained")
        or training_status == "promoted"
        or model_version.startswith("quality-model")
    ):
        return "trained"
    return "policy_only_not_trained"


def _normalize_content_metrics_payload(value: Any) -> dict[str, Any]:
    normalized = _normalize_optional_payload(value)
    if normalized.get("status") == "not_reported":
        return normalized
    table_summary = _dict_payload(normalized.get("table_summary"))
    for key in (
        "source_table_count",
        "xhtml_table_count",
        "table_cell_count",
        "table_row_count",
        "table_page_count",
        "multi_page_table_count",
        "wide_table_count",
        "low_confidence_table_count",
        "fragment_table_count",
        "false_positive_table_candidate_count",
        "suppressed_table_fragment_count",
        "rendered_low_confidence_table_count",
        "rendered_fragment_table_count",
        "transformed_table_preservation_count",
        "transformed_table_content_loss_count",
        "table_shape_histogram",
    ):
        if key not in normalized and key in table_summary:
            normalized[key] = table_summary[key]
    normalized.setdefault("table_cell_coverage", table_summary.get("table_cell_coverage", 1.0))
    normalized.setdefault("table_row_count", 0)
    normalized.setdefault("fragment_table_count", 0)
    return normalized


def _normalize_text_cleanup_payload(value: Any) -> dict[str, Any]:
    payload = _dict_payload(value)
    if not payload:
        return _not_reported_section()
    normalized = dict(payload)
    if not normalized.get("status"):
        if _truthy_value(normalized.get("publish_blocked")) or _coerce_non_negative_int(normalized.get("blocked_count")) > 0:
            normalized["status"] = "failed"
        elif _coerce_non_negative_int(normalized.get("review_needed_count")) > 0:
            normalized["status"] = "passed_with_warnings"
        else:
            normalized["status"] = "passed"
    return normalized


def _normalize_reference_cleanup_payload(value: Any) -> dict[str, Any]:
    payload = _dict_payload(value)
    if not payload:
        return _not_reported_section()
    normalized = dict(payload)
    status = _coerce_first_text(
        normalized.get("status"),
        normalized.get("quality_gate_status"),
        normalized.get("reference_quality_gate_status"),
    )
    if status:
        normalized["status"] = _normalize_cockpit_status(status)
    elif _coerce_non_negative_int(normalized.get("visible_junk_detected")) > 0:
        normalized["status"] = "failed"
    elif (
        _coerce_non_negative_int(normalized.get("unresolved_fragment_count")) > 0
        or _coerce_non_negative_int(normalized.get("review_record_count")) > 0
    ):
        normalized["status"] = "passed_with_warnings"
    else:
        normalized["status"] = "passed"
    return normalized


def _truthy_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "blocked", "failed"}
    return bool(value)


def _build_preview_inputs(
    *,
    conversion_metadata: Mapping[str, Any],
    content_metrics: Mapping[str, Any],
    validation_details: Mapping[str, Any],
    document_summary: Mapping[str, Any],
    heading_repair: HeadingRepairState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preview_metadata = dict(conversion_metadata)
    for key, value in document_summary.items():
        preview_metadata.setdefault(key, value)
    if "author" in document_summary and "creator" not in preview_metadata:
        preview_metadata["creator"] = document_summary.get("author")
    preview_metadata["heading_repair"] = heading_repair.to_dict()

    preview_quality_report = {
        **dict(content_metrics),
        **dict(validation_details),
    }
    return preview_metadata, preview_quality_report


def _has_cockpit_preview_input(
    *,
    conversion_metadata: Mapping[str, Any],
    content_metrics: Mapping[str, Any],
    validation_details: Mapping[str, Any],
    document_summary: Mapping[str, Any],
    output_path: str,
) -> bool:
    explicit_keys = {
        "asset_summary",
        "toc_preview",
        "epubcheck_detail",
        "metadata_summary",
    }
    return (
        bool(content_metrics)
        or bool(validation_details)
        or bool(document_summary)
        or any(key in conversion_metadata for key in explicit_keys)
        or _has_existing_file(output_path)
    )


def _build_cockpit_preview_sections(
    *,
    conversion_metadata: Mapping[str, Any],
    content_metrics: Mapping[str, Any],
    validation_details: Mapping[str, Any],
    document_summary: Mapping[str, Any],
    heading_repair: HeadingRepairState,
    output_path: str,
) -> dict[str, dict[str, Any]]:
    if not _has_cockpit_preview_input(
        conversion_metadata=conversion_metadata,
        content_metrics=content_metrics,
        validation_details=validation_details,
        document_summary=document_summary,
        output_path=output_path,
    ):
        return {
            "asset_summary": _not_reported_section(),
            "toc_preview": _not_reported_section(),
            "epubcheck_detail": _not_reported_section(),
            "metadata_summary": _not_reported_section(),
        }

    preview_metadata, preview_quality_report = _build_preview_inputs(
        conversion_metadata=conversion_metadata,
        content_metrics=content_metrics,
        validation_details=validation_details,
        document_summary=document_summary,
        heading_repair=heading_repair,
    )
    preview = build_quality_cockpit_preview(
        preview_metadata,
        quality_report=preview_quality_report,
        epub_path=output_path if _has_existing_file(output_path) else None,
    )
    asset_summary = _merge_preview_section(
        _dict_payload(preview.get("asset_summary")),
        _dict_payload(conversion_metadata.get("asset_summary")),
    )

    return {
        "asset_summary": asset_summary,
        "toc_preview": _dict_payload(conversion_metadata.get("toc_preview")) or _dict_payload(preview.get("toc_preview")),
        "epubcheck_detail": _dict_payload(conversion_metadata.get("epubcheck_detail")) or _dict_payload(preview.get("epubcheck_detail")),
        "metadata_summary": _dict_payload(conversion_metadata.get("metadata_summary")) or _dict_payload(preview.get("metadata_summary")),
    }


def _merge_preview_section(preview: Mapping[str, Any], explicit: Mapping[str, Any]) -> dict[str, Any]:
    if not preview:
        return dict(explicit)
    if not explicit:
        return dict(preview)
    merged = dict(preview)
    for key, value in explicit.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            nested = dict(merged[key])
            nested.update(value)
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _metadata_health_from_summary(metadata_summary: Mapping[str, Any]) -> dict[str, Any]:
    if not metadata_summary or metadata_summary.get("status") == "not_reported":
        return _not_reported_health("Metadata")
    placeholders = [
        str(item)
        for item in (metadata_summary.get("placeholders_detected") or metadata_summary.get("placeholders") or [])
        if str(item).strip()
    ]
    if placeholders:
        return {
            "label": "Metadata",
            "status": "passed_with_warnings",
            "count": len(placeholders),
            "message": "Placeholder metadata fields: " + ", ".join(placeholders[:4]),
            "placeholders": placeholders[:8],
            "placeholder_count": len(placeholders),
        }
    if any(_coerce_text(metadata_summary.get(key)) for key in ("title", "creator", "author", "language")):
        return {
            "label": "Metadata",
            "status": "passed",
            "count": 0,
            "message": "Reader-facing metadata is available.",
        }
    return _not_reported_health("Metadata")


def _link_health_from_validation(validation_details: Mapping[str, Any]) -> dict[str, Any]:
    if not validation_details:
        return _not_reported_health("Links")
    broken_count = sum(
        _coerce_non_negative_int(validation_details.get(key))
        for key in (
            "internal_link_error_count",
            "external_link_error_count",
            "broken_href_error_count",
            "duplicate_id_error_count",
        )
    )
    if broken_count > 0:
        return {
            "label": "Links",
            "status": "failed",
            "count": broken_count,
            "broken_count": broken_count,
            "message": f"{broken_count} link or anchor issue(s) detected.",
        }
    return {
        "label": "Links",
        "status": "passed",
        "count": 0,
        "broken_count": 0,
        "message": "No link or anchor issues reported.",
    }


def _visible_junk_from_reference_cleanup(reference_cleanup: Mapping[str, Any]) -> dict[str, Any]:
    if not reference_cleanup or reference_cleanup.get("status") == "not_reported":
        return _not_reported_health("Visible junk")
    count = _coerce_non_negative_int(reference_cleanup.get("visible_junk_detected"))
    if count > 0:
        return {
            "label": "Visible junk",
            "status": "failed",
            "count": count,
            "message": f"{count} visible cleanup artifact(s) detected.",
        }
    return {
        "label": "Visible junk",
        "status": "passed",
        "count": 0,
        "message": "No visible cleanup artifacts reported.",
    }


def _normalize_health_payload(value: Any, *, label: str, fallback: Mapping[str, Any]) -> dict[str, Any]:
    payload = _dict_payload(value)
    if not payload:
        return dict(fallback)
    normalized = dict(payload)
    normalized.setdefault("label", label)
    normalized.setdefault("status", "not_reported")
    normalized.setdefault("message", "")
    if "count" not in normalized:
        normalized["count"] = _coerce_first_optional_non_negative_int(
            normalized.get("error_count"),
            normalized.get("broken_count"),
            normalized.get("warning_count"),
            normalized.get("placeholder_count"),
        )
    return normalized


def _quality_completeness_message(payload: Mapping[str, Any], *, reported: bool) -> str:
    message = _coerce_first_text(
        payload.get("message"),
        payload.get("summary"),
        payload.get("detail"),
    )
    if message:
        return message
    if reported:
        return _coerce_first_text(payload.get("status"), default="Reported") or "Reported"
    return "Not reported"


def _quality_completeness_section(
    key: str,
    label: str,
    payload: Mapping[str, Any] | None,
    *,
    status: Any = None,
) -> QualityCompletenessSectionState:
    section_payload = _dict_payload(payload)
    raw_status = status if status is not None else section_payload.get("status")
    normalized_status = _normalize_cockpit_status(raw_status, default="not_reported")
    reported = normalized_status not in QUALITY_COMPLETENESS_NOT_REPORTED_STATUSES
    message = _quality_completeness_message(section_payload, reported=reported)
    if reported and _coerce_status(message) in QUALITY_COMPLETENESS_NOT_REPORTED_STATUSES:
        message = normalized_status or "Reported"
    return QualityCompletenessSectionState(
        key=key,
        label=label,
        status=normalized_status or "not_reported",
        reported=reported,
        message=message,
    )


def _epubcheck_completeness_status(
    *,
    validation: ValidationState,
    epubcheck_detail: Mapping[str, Any],
) -> str:
    status = _normalize_cockpit_status(epubcheck_detail.get("status"), default="not_reported")
    if status not in QUALITY_COMPLETENESS_NOT_REPORTED_STATUSES:
        return status
    if validation.tool.lower() == "epubcheck" and validation.status not in QUALITY_COMPLETENESS_NOT_REPORTED_STATUSES:
        return validation.status
    return status


def _build_quality_completeness_state(
    *,
    validation: ValidationState,
    epubcheck_detail: Mapping[str, Any],
    toc_preview: Mapping[str, Any],
    metadata_health: Mapping[str, Any],
    link_health: Mapping[str, Any],
    visible_junk: Mapping[str, Any],
    asset_summary: Mapping[str, Any],
    text_cleanup: Mapping[str, Any],
    reference_cleanup: Mapping[str, Any],
    semantic_cleanup: Mapping[str, Any],
    ocr_quality: Mapping[str, Any],
    reading_order: Mapping[str, Any],
    content_metrics: Mapping[str, Any],
) -> QualityCompletenessState:
    table_semantics = _table_semantics_completeness_payload(content_metrics)
    section_lookup = {
        "validation": _quality_completeness_section(
            "validation",
            "Validation",
            validation.to_dict(),
            status=validation.status,
        ),
        "epubcheck": _quality_completeness_section(
            "epubcheck",
            "EPUBCheck",
            epubcheck_detail,
            status=_epubcheck_completeness_status(validation=validation, epubcheck_detail=epubcheck_detail),
        ),
        "toc": _quality_completeness_section("toc", "TOC", toc_preview),
        "metadata": _quality_completeness_section("metadata", "Metadata", metadata_health),
        "links": _quality_completeness_section("links", "Links", link_health),
        "visible_junk": _quality_completeness_section("visible_junk", "Visible junk", visible_junk),
        "assets": _quality_completeness_section("assets", "Assets", asset_summary),
        "text_cleanup": _quality_completeness_section("text_cleanup", "Text cleanup", text_cleanup),
        "reference_cleanup": _quality_completeness_section(
            "reference_cleanup",
            "Reference cleanup",
            reference_cleanup,
        ),
        "semantic_cleanup": _quality_completeness_section("semantic_cleanup", "Semantic cleanup", semantic_cleanup),
        "ocr_quality": _quality_completeness_section("ocr_quality", "OCR quality", ocr_quality),
        "reading_order": _quality_completeness_section("reading_order", "Reading order", reading_order),
        "table_semantics": _quality_completeness_section("table_semantics", "Table semantics", table_semantics),
    }
    sections = tuple(section_lookup[key] for key, _label in QUALITY_COMPLETENESS_SECTIONS)
    expected_count = len(sections)
    reported_count = sum(1 for section in sections if section.reported)
    missing_sections = tuple(section.key for section in sections if not section.reported)
    not_reported_sections = tuple(
        section.key for section in sections if section.status in QUALITY_COMPLETENESS_NOT_REPORTED_STATUSES
    )
    score = int(round((reported_count / expected_count) * 100)) if expected_count else 0
    status = "complete" if reported_count == expected_count else "not_reported" if reported_count == 0 else "partial"
    return QualityCompletenessState(
        score=score,
        status=status,
        expected_sections=expected_count,
        reported_sections=reported_count,
        missing_count=expected_count - reported_count,
        not_reported_count=len(not_reported_sections),
        missing_sections=missing_sections,
        not_reported_sections=not_reported_sections,
        sections=sections,
    )


def _table_semantics_completeness_payload(content_metrics: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _dict_payload(content_metrics)
    if not metrics:
        return _not_reported_section()
    source_count = _coerce_optional_non_negative_int(metrics.get("source_table_count"))
    xhtml_count = _coerce_optional_non_negative_int(metrics.get("xhtml_table_count"))
    if source_count is None and xhtml_count is None:
        return _not_reported_section()
    if source_count is None:
        source_count = 0
    if xhtml_count is None:
        xhtml_count = 0
    transformed_count = _coerce_optional_non_negative_int(metrics.get("transformed_table_count")) or 0
    suppressed_count = _coerce_optional_non_negative_int(metrics.get("suppressed_table_fragment_count")) or 0
    represented_count = xhtml_count + transformed_count + suppressed_count
    if source_count > 0 and represented_count <= 0:
        status = "failed"
        message = f"Source tables reported but no rendered, transformed, or suppressed table decisions found ({represented_count}/{source_count})."
    elif source_count > 0 and represented_count < source_count:
        status = "passed_with_warnings"
        message = f"Partial table semantics reported ({represented_count}/{source_count})."
    elif source_count > 0:
        status = "passed"
        message = f"Table semantics accounted for ({represented_count}/{source_count})."
    else:
        status = "passed"
        message = "No source tables reported."
    rendered_low_confidence_count = _coerce_optional_non_negative_int(metrics.get("rendered_low_confidence_table_count")) or 0
    rendered_fragment_count = _coerce_optional_non_negative_int(metrics.get("rendered_fragment_table_count")) or 0
    transformed_loss_count = _coerce_optional_non_negative_int(metrics.get("transformed_table_content_loss_count")) or 0
    if rendered_low_confidence_count > 0 or rendered_fragment_count > 0 or transformed_loss_count > 0:
        status = "failed"
        message = (
            "Table semantics include rendered low-confidence fragments or transformed table content loss."
        )
    return {
        "status": status,
        "source_table_count": source_count,
        "xhtml_table_count": xhtml_count,
        "transformed_table_count": transformed_count,
        "represented_table_count": represented_count,
        "table_cell_count": _coerce_optional_non_negative_int(metrics.get("table_cell_count")) or 0,
        "table_row_count": _coerce_optional_non_negative_int(metrics.get("table_row_count")) or 0,
        "table_cell_coverage": metrics.get("table_cell_coverage", 1.0),
        "table_page_count": _coerce_optional_non_negative_int(metrics.get("table_page_count")) or 0,
        "multi_page_table_count": _coerce_optional_non_negative_int(metrics.get("multi_page_table_count")) or 0,
        "wide_table_count": _coerce_optional_non_negative_int(metrics.get("wide_table_count")) or 0,
        "low_confidence_table_count": _coerce_optional_non_negative_int(metrics.get("low_confidence_table_count")) or 0,
        "fragment_table_count": _coerce_optional_non_negative_int(metrics.get("fragment_table_count")) or 0,
        "false_positive_table_candidate_count": _coerce_optional_non_negative_int(metrics.get("false_positive_table_candidate_count")) or 0,
        "suppressed_table_fragment_count": _coerce_optional_non_negative_int(metrics.get("suppressed_table_fragment_count")) or 0,
        "rendered_low_confidence_table_count": rendered_low_confidence_count,
        "rendered_fragment_table_count": rendered_fragment_count,
        "transformed_table_preservation_count": _coerce_optional_non_negative_int(metrics.get("transformed_table_preservation_count")) or 0,
        "transformed_table_content_loss_count": transformed_loss_count,
        "table_summary": _dict_payload(metrics.get("table_summary")),
        "message": message,
    }


def assemble_quality_state(request: ConversionQualityStateRequest) -> ConversionQualityState:
    job_status = _coerce_status(request.job_status)
    if job_status not in KNOWN_JOB_STATUSES:
        job_status = "unknown"

    conversion_metadata = _mapping(request.conversion_metadata)
    analysis = _mapping(conversion_metadata.get("analysis"))
    quality_report = _mapping(conversion_metadata.get("quality_report"))
    document_summary = _mapping(conversion_metadata.get("document_summary"))
    quality_available = job_status == READY_JOB_STATUS and bool(conversion_metadata)
    heading_default_status = "skipped" if quality_available else "unavailable"

    output_size_bytes = _coerce_first_optional_non_negative_int(
        request.output_size_bytes,
        conversion_metadata.get("output_size_bytes"),
        conversion_metadata.get("final_output_size_bytes"),
        quality_report.get("final_output_size_bytes"),
    )

    source_type = _coerce_first_text(
        request.source_type,
        conversion_metadata.get("source_type"),
        default="pdf",
    ).lower() or "pdf"
    summary = QualitySummaryState(
        profile=_coerce_first_text(
            conversion_metadata.get("profile"),
            analysis.get("profile"),
            default="unknown",
        )
        or "unknown",
        strategy=(
            _coerce_first_text(
                conversion_metadata.get("strategy"),
                analysis.get("legacy_strategy"),
                analysis.get("strategy"),
            )
            or None
        ),
        confidence=_coerce_confidence(
            conversion_metadata.get("confidence")
            if conversion_metadata.get("confidence") is not None
            else analysis.get("confidence")
        ),
        layout=_coerce_first_text(
            conversion_metadata.get("layout"),
            document_summary.get("layout_mode"),
            default="reflowable",
        )
        or "reflowable",
        sections=_coerce_first_optional_non_negative_int(
            conversion_metadata.get("sections"),
            document_summary.get("section_count"),
        )
        or 0,
        assets=_coerce_first_optional_non_negative_int(
            conversion_metadata.get("assets"),
            document_summary.get("asset_count"),
        )
        or 0,
        output_size_bytes=output_size_bytes,
    )

    validation = ValidationState(
        status=_normalize_quality_status(
            _coerce_first_text(
                conversion_metadata.get("validation"),
                quality_report.get("validation_status"),
                default="unavailable",
            )
        ),
        tool=_coerce_first_text(
            conversion_metadata.get("validation_tool"),
            quality_report.get("validation_tool"),
            default="unknown",
        )
        or "unknown",
    )

    heading_payload = _mapping(conversion_metadata.get("heading_repair")) or _mapping(
        conversion_metadata.get("heading_repair_report")
    )
    heading_repair = HeadingRepairState(
        status=_coerce_first_text(heading_payload.get("status"), default=heading_default_status) or heading_default_status,
        release=_coerce_first_text(
            heading_payload.get("release"),
            heading_payload.get("release_status"),
            default="unavailable",
        )
        or "unavailable",
        toc_before=_coerce_first_optional_non_negative_int(
            heading_payload.get("toc_before"),
            heading_payload.get("toc_entries_before"),
        )
        or 0,
        toc_after=_coerce_first_optional_non_negative_int(
            heading_payload.get("toc_after"),
            heading_payload.get("toc_entries_after"),
        )
        or 0,
        removed=_coerce_first_optional_non_negative_int(
            heading_payload.get("removed"),
            heading_payload.get("headings_removed"),
        )
        or 0,
        review=_coerce_first_optional_non_negative_int(
            heading_payload.get("review"),
            heading_payload.get("manual_review_count"),
        )
        or 0,
        epubcheck=_coerce_first_text(
            heading_payload.get("epubcheck"),
            heading_payload.get("epubcheck_status"),
            default="unavailable",
        )
        or "unavailable",
        error=_coerce_text(heading_payload.get("error")),
    )

    warnings = _string_list(conversion_metadata.get("warning_list"), limit=12) or _string_list(
        quality_report.get("warnings"),
        limit=12,
    )
    high_risk_page_source = conversion_metadata.get("high_risk_page_list")
    if not isinstance(high_risk_page_source, list):
        high_risk_page_source = quality_report.get("high_risk_pages")
    high_risk_page_list = _build_high_risk_pages(high_risk_page_source)
    high_risk_section_source = conversion_metadata.get("high_risk_section_list")
    if not isinstance(high_risk_section_source, list):
        high_risk_section_source = quality_report.get("high_risk_sections")
    high_risk_section_list = _build_high_risk_sections(high_risk_section_source)
    audit = AuditState(
        warning_count=max(
            _coerce_first_optional_non_negative_int(
                conversion_metadata.get("warnings"),
                len(quality_report.get("warnings", []) or []),
            )
            or 0,
            len(warnings),
        ),
        warnings=warnings,
        high_risk_pages=max(
            _coerce_first_optional_non_negative_int(
                conversion_metadata.get("high_risk_pages"),
                len(quality_report.get("high_risk_pages", []) or []),
            )
            or 0,
            len(high_risk_page_list),
        ),
        high_risk_page_list=high_risk_page_list,
        high_risk_sections=max(
            _coerce_first_optional_non_negative_int(
                conversion_metadata.get("high_risk_sections"),
                len(quality_report.get("high_risk_sections", []) or []),
            )
            or 0,
            len(high_risk_section_list),
        ),
        high_risk_section_list=high_risk_section_list,
    )

    render_budget = RenderBudgetState(
        budget_class=_coerce_first_text(
            conversion_metadata.get("render_budget_class"),
            quality_report.get("render_budget_class"),
            analysis.get("render_budget_class"),
        ),
        attempt=_coerce_first_text(
            conversion_metadata.get("render_budget_attempt"),
            quality_report.get("render_budget_attempt"),
        ),
        target_warn_bytes=_coerce_first_optional_non_negative_int(
            conversion_metadata.get("target_warn_bytes"),
            quality_report.get("target_warn_bytes"),
        )
        or 0,
        target_hard_bytes=_coerce_first_optional_non_negative_int(
            conversion_metadata.get("target_hard_bytes"),
            quality_report.get("target_hard_bytes"),
        )
        or 0,
    )
    size_budget = SizeBudgetState(
        status=_normalize_quality_status(
            _coerce_first_text(
                conversion_metadata.get("size_budget_status"),
                quality_report.get("size_budget_status"),
                default="unavailable",
            )
        ),
        message=_coerce_first_text(
            conversion_metadata.get("size_budget_message"),
            quality_report.get("size_budget_message"),
        ),
    )

    content_metrics = _normalize_content_metrics_payload(
        conversion_metadata.get("content_metrics") or quality_report,
    )
    validation_details = _dict_payload(conversion_metadata.get("validation_details"))
    text_cleanup = _normalize_text_cleanup_payload(
        conversion_metadata.get("text_cleanup") or quality_report.get("text_cleanup")
    )
    reference_cleanup = _normalize_reference_cleanup_payload(
        conversion_metadata.get("reference_cleanup") or _dict_payload(text_cleanup.get("reference_cleanup"))
    )
    semantic_cleanup = _normalize_optional_payload(
        conversion_metadata.get("semantic_cleanup") or quality_report.get("semantic_cleanup")
    )
    ocr_quality = _normalize_optional_payload(
        conversion_metadata.get("ocr_quality")
        or conversion_metadata.get("ocr_degradation")
        or quality_report.get("ocr_quality")
        or quality_report.get("ocr_degradation")
    )
    reading_order = _normalize_optional_payload(
        conversion_metadata.get("reading_order") or quality_report.get("reading_order")
    )
    document_summary_payload = _dict_payload(conversion_metadata.get("document_summary")) or document_summary
    preview_sections = _build_cockpit_preview_sections(
        conversion_metadata=conversion_metadata,
        content_metrics=content_metrics if content_metrics.get("status") != "not_reported" else {},
        validation_details=validation_details,
        document_summary=document_summary_payload,
        heading_repair=heading_repair,
        output_path=request.output_path,
    )
    asset_summary = _normalize_optional_payload(preview_sections.get("asset_summary"))
    toc_preview = _normalize_optional_payload(preview_sections.get("toc_preview"))
    epubcheck_detail = _normalize_optional_payload(preview_sections.get("epubcheck_detail"))
    metadata_summary = _normalize_optional_payload(preview_sections.get("metadata_summary"))
    metadata_health = _normalize_health_payload(
        conversion_metadata.get("metadata_health"),
        label="Metadata",
        fallback=_metadata_health_from_summary(metadata_summary),
    )
    link_health = _normalize_health_payload(
        conversion_metadata.get("link_health"),
        label="Links",
        fallback=_link_health_from_validation(validation_details),
    )
    visible_junk = _normalize_health_payload(
        conversion_metadata.get("visible_junk"),
        label="Visible junk",
        fallback=_visible_junk_from_reference_cleanup(reference_cleanup),
    )
    premium_scoring = _normalize_optional_payload(
        conversion_metadata.get("premium_scoring") or quality_report.get("premium_scoring")
    )
    quality_selection = _normalize_optional_payload(
        conversion_metadata.get("quality_selection") or quality_report.get("quality_selection")
    )
    ai_quality = _normalize_optional_payload(
        conversion_metadata.get("ai_quality") or quality_report.get("ai_quality")
    )
    ai_quality_verification = _normalize_optional_payload(
        conversion_metadata.get("ai_quality_verification") or quality_report.get("ai_quality_verification")
    )
    quality_policy_verifier = _normalize_optional_payload(
        conversion_metadata.get("quality_policy_verifier")
        or conversion_metadata.get("ai_quality_verification")
        or quality_report.get("ai_quality_verification")
    )
    route_model_shadow = _normalize_optional_payload(
        conversion_metadata.get("route_model_shadow")
        or _dict_payload(conversion_metadata.get("source_analysis")).get("route_decision")
        or quality_report.get("route_model_shadow")
    )
    model_attribution = _normalize_optional_payload(conversion_metadata.get("model_attribution"))
    if not model_attribution:
        model_attribution = {
            "schema": "kindlemaster.model_attribution.v1",
            "route_model_version": _coerce_first_text(
                conversion_metadata.get("route_model_version"),
                route_model_shadow.get("model_version"),
            ),
            "quality_verifier_version": _coerce_first_text(
                conversion_metadata.get("quality_verifier_version"),
                quality_policy_verifier.get("model_version"),
            ),
            "chess_fen_profile_version": _coerce_first_text(conversion_metadata.get("chess_fen_profile_version")),
            "model_registry_version": _coerce_first_text(conversion_metadata.get("model_registry_version")),
        }
    trained_quality_model_status = _coerce_first_text(
        conversion_metadata.get("trained_quality_model_status"),
        quality_report.get("trained_quality_model_status"),
        default=_trained_quality_model_status(quality_policy_verifier),
    )
    stage_timings = _dict_payload(conversion_metadata.get("stage_timings") or quality_report.get("stage_timings"))
    quality_gate_mode = _coerce_first_text(
        conversion_metadata.get("quality_gate_mode"),
        quality_report.get("quality_gate_mode"),
        default="",
    ).strip().lower()
    issue_groups = build_quality_cockpit_issue_groups(
        validation=validation.to_dict(),
        heading_repair=heading_repair.to_dict(),
        audit=audit.to_dict(),
        size_budget=size_budget.to_dict(),
        text_cleanup=text_cleanup,
        reference_cleanup=reference_cleanup,
        semantic_cleanup=semantic_cleanup,
        ocr_quality=ocr_quality,
        reading_order=reading_order,
        metadata_health=metadata_health,
        link_health=link_health,
        visible_junk=visible_junk,
        epubcheck_detail=epubcheck_detail,
        content_metrics=content_metrics,
        toc_preview=toc_preview,
        asset_summary=asset_summary,
        premium_scoring=premium_scoring,
    )
    magazine_quality_preview = _build_magazine_quality_preview(
        conversion_metadata=conversion_metadata,
        quality_report=quality_report,
        issue_groups=issue_groups,
        premium_scoring=premium_scoring,
        text_cleanup=text_cleanup,
        asset_summary=asset_summary,
        ai_quality_verification=ai_quality_verification,
    )
    quality_completeness = _build_quality_completeness_state(
        validation=validation,
        epubcheck_detail=epubcheck_detail,
        toc_preview=toc_preview,
        metadata_health=metadata_health,
        link_health=link_health,
        visible_junk=visible_junk,
        asset_summary=asset_summary,
        text_cleanup=text_cleanup,
        reference_cleanup=reference_cleanup,
        semantic_cleanup=semantic_cleanup,
        ocr_quality=ocr_quality,
        reading_order=reading_order,
        content_metrics=content_metrics,
    )

    overall_severity = "info"
    if job_status in {FAILED_JOB_STATUS, TIMED_OUT_JOB_STATUS}:
        overall_severity = "error"
    elif job_status == READY_JOB_STATUS:
        overall_severity = _severity_for_ready_state(
            quality_available=quality_available,
            validation_status=validation.status,
            heading_repair_status=heading_repair.status,
            warning_count=audit.warning_count,
            review_count=heading_repair.review + audit.high_risk_pages + audit.high_risk_sections,
            size_budget_status=size_budget.status,
        )
        if issue_groups.get("blockers"):
            overall_severity = "error"
        elif overall_severity != "error" and (issue_groups.get("warnings") or issue_groups.get("review")):
            overall_severity = "warning"
        elif overall_severity != "error" and quality_completeness.status != "complete":
            overall_severity = "warning"

    alerts = _build_alerts(
        job_status=job_status,
        quality_available=quality_available,
        error=_coerce_text(request.error),
        error_code=_coerce_text(request.error_code),
        validation=validation,
        heading_repair=heading_repair,
        audit=audit,
        size_budget=size_budget,
    )
    download_state = resolve_conversion_download_state(
        job_status=job_status,
        output_path=request.output_path,
        download_url=request.download_url,
        output_path_exists=request.output_path_exists,
    )
    download_available = download_state.download_available
    quality_blockers = _build_quality_blockers(
        job_status=job_status,
        error=_coerce_text(request.error),
        error_code=_coerce_text(request.error_code),
        issue_groups=issue_groups,
        alerts=alerts,
    )
    if job_status == READY_JOB_STATUS and quality_gate_mode == "draft":
        draft_blocker = {
            "severity": "blocker",
            "code": "runtime_quality_gate_draft",
            "message": "Conversion completed in draft quality-gate mode and is not approved for publication.",
            "source": "quality_gate",
            "suggested_action": "Review premium scoring, local quality policy verifier output, and user feedback before promoting this EPUB.",
        }
        if not any(item.get("code") == draft_blocker["code"] for item in quality_blockers):
            quality_blockers = (*quality_blockers, draft_blocker)
    release_blocked = job_status in {FAILED_JOB_STATUS, TIMED_OUT_JOB_STATUS} or bool(quality_blockers)
    reading_verdict = _build_reading_verdict(
        job_status=job_status,
        download_available=download_available,
        release_blocked=release_blocked,
        overall_severity=overall_severity,
        quality_available=quality_available,
        validation=validation,
        epubcheck_detail=epubcheck_detail,
        issue_groups=issue_groups,
    )
    release_verdict = _build_release_verdict(
        job_status=job_status,
        download_available=download_available,
        release_blocked=release_blocked,
        reading_verdict=reading_verdict,
        overall_severity=overall_severity,
        quality_available=quality_available,
        issue_groups=issue_groups,
    )
    user_facing_reasons = _build_user_facing_reasons(
        release_verdict=release_verdict,
        quality_blockers=quality_blockers,
        issue_groups=issue_groups,
        alerts=alerts,
    )
    user_facing_verdict = _build_user_facing_verdict(
        job_status=job_status,
        release_verdict=release_verdict,
        reading_verdict=reading_verdict,
        download_available=download_available,
        release_blocked=release_blocked,
        reasons=user_facing_reasons,
    )
    send_to_kindle_blockers = _build_send_to_kindle_blockers(
        job_status=job_status,
        download_available=download_available,
        release_verdict=release_verdict,
        validation=validation,
        epubcheck_detail=epubcheck_detail,
        size_budget=size_budget,
        output_size_bytes=output_size_bytes,
        asset_summary=asset_summary,
    )
    send_to_kindle_ready = not send_to_kindle_blockers
    kindle_delivery = _build_kindle_delivery_payload(
        conversion_metadata=conversion_metadata,
        quality_report=quality_report,
        automated_ready=send_to_kindle_ready,
        automated_blockers=send_to_kindle_blockers,
    )
    raw_signals = _build_raw_signals_state(
        summary=summary,
        heading_repair=heading_repair,
        audit=audit,
        render_budget=render_budget,
    )
    verdict = _build_verdict_state(
        job_status=job_status,
        overall_severity=overall_severity,
        quality_available=quality_available,
        download_available=download_available,
        validation=validation,
        heading_repair=heading_repair,
        audit=audit,
        size_budget=size_budget,
        alerts=alerts,
        issue_groups=issue_groups,
    )
    public_score = _coerce_public_score(
        premium_scoring=premium_scoring,
        conversion_metadata=conversion_metadata,
        quality_completeness=quality_completeness,
    )
    public_sendable = _public_sendable(
        premium_scoring=premium_scoring,
        download_available=download_available,
    )
    public_kindle_ready = _premium_bool(
        premium_scoring,
        "kindle_ready",
        default=send_to_kindle_ready,
    )
    public_premium_ready = _premium_bool(
        premium_scoring,
        "premium_ready",
        default=False,
    )
    public_warnings = _build_public_warnings(issue_groups=issue_groups, alerts=alerts)

    return ConversionQualityState(
        status=job_status,
        phase=_phase_for_job_status(job_status),
        is_terminal=job_status in {READY_JOB_STATUS, FAILED_JOB_STATUS, TIMED_OUT_JOB_STATUS},
        quality_available=quality_available,
        download_ready=download_state.download_ready,
        download_available=download_available,
        download_state=download_state,
        reading_verdict=reading_verdict,
        release_verdict=release_verdict,
        release_blocked=release_blocked,
        quality_blockers=quality_blockers,
        user_facing_verdict=user_facing_verdict,
        user_facing_reasons=user_facing_reasons,
        send_to_kindle_ready=send_to_kindle_ready,
        send_to_kindle_blockers=send_to_kindle_blockers,
        kindle_delivery=kindle_delivery,
        score=public_score,
        sendable=public_sendable,
        kindle_ready=public_kindle_ready,
        premium_ready=public_premium_ready,
        blockers=quality_blockers,
        warnings=public_warnings,
        reports=_build_public_reports(job_status=job_status, job_id=request.job_id),
        artifacts=_build_public_artifacts(download_url=_coerce_text(download_state.download_url)),
        sentry_event_id=_coerce_first_text(request.sentry_event_id, conversion_metadata.get("sentry_event_id")),
        overall_severity=overall_severity,
        source_type=source_type,
        filename=_coerce_text(request.filename),
        message=_coerce_text(request.message),
        error=_coerce_text(request.error),
        download_url=_coerce_text(download_state.download_url),
        summary=summary,
        validation=validation,
        heading_repair=heading_repair,
        audit=audit,
        render_budget=render_budget,
        size_budget=size_budget,
        content_metrics=content_metrics,
        text_cleanup=text_cleanup,
        reference_cleanup=reference_cleanup,
        semantic_cleanup=semantic_cleanup,
        ocr_quality=ocr_quality,
        reading_order=reading_order,
        asset_summary=asset_summary,
        magazine_quality_preview=magazine_quality_preview,
        toc_preview=toc_preview,
        epubcheck_detail=epubcheck_detail,
        metadata_summary=metadata_summary,
        metadata_health=metadata_health,
        link_health=link_health,
        visible_junk=visible_junk,
        premium_scoring=premium_scoring,
        quality_selection=quality_selection,
        ai_quality=ai_quality,
        ai_quality_verification=ai_quality_verification,
        quality_policy_verifier=quality_policy_verifier,
        trained_quality_model_status=trained_quality_model_status,
        route_model_shadow=route_model_shadow,
        model_attribution=model_attribution,
        stage_timings=stage_timings,
        quality_gate_mode=quality_gate_mode,
        issue_groups=issue_groups,
        quality_completeness=quality_completeness,
        raw_signals=raw_signals,
        verdict=verdict,
        alerts=alerts,
    )


def assemble_quality_state_dict(request: ConversionQualityStateRequest) -> dict[str, Any]:
    return assemble_quality_state(request).to_dict()
