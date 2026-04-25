from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


KNOWN_JOB_STATUSES = {"queued", "running", "repairing_headings", "ready", "failed"}
READY_JOB_STATUS = "ready"
FAILED_JOB_STATUS = "failed"

WARNING_STATUSES = {"warning", "warnings", "passed_with_warnings", "pass_with_review"}
FAILED_STATUSES = {"failed", "fail", "error"}


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
class ConversionQualityStateRequest:
    job_status: str
    source_type: str = ""
    filename: str = ""
    message: str = ""
    error: str = ""
    conversion_metadata: Mapping[str, Any] = field(default_factory=dict)
    output_size_bytes: int | None = None
    download_url: str = ""

    @classmethod
    def from_job_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        download_url: str | None = None,
    ) -> "ConversionQualityStateRequest":
        conversion_metadata = _mapping(payload.get("metadata")) or _mapping(payload.get("conversion"))
        return cls(
            job_status=_coerce_text(payload.get("status"), default="unknown"),
            source_type=_coerce_text(payload.get("source_type")),
            filename=_coerce_text(payload.get("filename")),
            message=_coerce_text(payload.get("message")),
            error=_coerce_text(payload.get("error")),
            conversion_metadata=conversion_metadata,
            output_size_bytes=_coerce_optional_non_negative_int(payload.get("output_size_bytes")),
            download_url=_coerce_text(download_url or payload.get("download_url")),
        )


@dataclass(frozen=True)
class ConversionQualityState:
    status: str
    phase: str
    is_terminal: bool
    quality_available: bool
    download_ready: bool
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
        push("error", "conversion_failed", error or "Conversion failed before quality data was available.")

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
    validation: ValidationState,
    heading_repair: HeadingRepairState,
    audit: AuditState,
    size_budget: SizeBudgetState,
    alerts: tuple[QualityStateAlert, ...],
) -> QualityVerdictState:
    if job_status == FAILED_JOB_STATUS:
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

    review_count = heading_repair.review + audit.high_risk_pages + audit.high_risk_sections
    reason_codes = [alert.code for alert in alerts]
    if validation.status == "failed":
        reason_codes.append("validation_failed")
    if size_budget.status == "failed":
        reason_codes.append("size_budget_failed")
    if heading_repair.status == "failed":
        reason_codes.append("heading_repair_failed")

    return QualityVerdictState(
        status=status,
        severity=overall_severity,
        requires_manual_review=review_count > 0,
        blocks_download=job_status == FAILED_JOB_STATUS or (job_status == READY_JOB_STATUS and overall_severity == "error"),
        reasons=tuple(dict.fromkeys(reason_codes)),
    )


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

    overall_severity = "info"
    if job_status == FAILED_JOB_STATUS:
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

    alerts = _build_alerts(
        job_status=job_status,
        quality_available=quality_available,
        error=_coerce_text(request.error),
        validation=validation,
        heading_repair=heading_repair,
        audit=audit,
        size_budget=size_budget,
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
        validation=validation,
        heading_repair=heading_repair,
        audit=audit,
        size_budget=size_budget,
        alerts=alerts,
    )

    return ConversionQualityState(
        status=job_status,
        phase=_phase_for_job_status(job_status),
        is_terminal=job_status in {READY_JOB_STATUS, FAILED_JOB_STATUS},
        quality_available=quality_available,
        download_ready=job_status == READY_JOB_STATUS,
        overall_severity=overall_severity,
        source_type=source_type,
        filename=_coerce_text(request.filename),
        message=_coerce_text(request.message),
        error=_coerce_text(request.error),
        download_url=_coerce_text(request.download_url),
        summary=summary,
        validation=validation,
        heading_repair=heading_repair,
        audit=audit,
        render_budget=render_budget,
        size_budget=size_budget,
        raw_signals=raw_signals,
        verdict=verdict,
        alerts=alerts,
    )


def assemble_quality_state_dict(request: ConversionQualityStateRequest) -> dict[str, Any]:
    return assemble_quality_state(request).to_dict()
