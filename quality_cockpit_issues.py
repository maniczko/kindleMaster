from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


ISSUE_GROUP_KEYS = ("blockers", "warnings", "review")
FAILED_STATUSES = {"failed", "fail", "error", "blocked"}
WARNING_STATUSES = {"warning", "warnings", "passed_with_warnings", "pass_with_review"}
PASSED_STATUSES = {"passed", "pass", "ok", "success"}


def build_quality_cockpit_issue_groups(
    *,
    validation: Mapping[str, Any] | None = None,
    heading_repair: Mapping[str, Any] | None = None,
    audit: Mapping[str, Any] | None = None,
    size_budget: Mapping[str, Any] | None = None,
    text_cleanup: Mapping[str, Any] | None = None,
    reference_cleanup: Mapping[str, Any] | None = None,
    semantic_cleanup: Mapping[str, Any] | None = None,
    ocr_quality: Mapping[str, Any] | None = None,
    reading_order: Mapping[str, Any] | None = None,
    metadata_health: Mapping[str, Any] | None = None,
    link_health: Mapping[str, Any] | None = None,
    visible_junk: Mapping[str, Any] | None = None,
    epubcheck_detail: Mapping[str, Any] | None = None,
    content_metrics: Mapping[str, Any] | None = None,
    toc_preview: Mapping[str, Any] | None = None,
    asset_summary: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build JSON-serializable cockpit issues from normalized-ish quality signals."""

    collector = _IssueCollector()
    validation = _mapping(validation)
    heading_repair = _mapping(heading_repair)
    audit = _mapping(audit)
    size_budget = _mapping(size_budget)
    text_cleanup = _mapping(text_cleanup)
    reference_cleanup = _mapping(reference_cleanup) or _mapping(text_cleanup.get("reference_cleanup"))
    semantic_cleanup = _mapping(semantic_cleanup)
    ocr_quality = _mapping(ocr_quality)
    reading_order = _mapping(reading_order)
    metadata_health = _mapping(metadata_health)
    link_health = _mapping(link_health)
    visible_junk = _mapping(visible_junk)
    epubcheck_detail = _mapping(epubcheck_detail)
    content_metrics = _mapping(content_metrics)
    toc_preview = _mapping(toc_preview)
    asset_summary = _mapping(asset_summary)

    _collect_validation_issues(collector, validation, epubcheck_detail)
    _collect_size_budget_issues(collector, size_budget)
    _collect_heading_repair_issues(collector, heading_repair)
    _collect_audit_issues(collector, audit)
    _collect_text_cleanup_issues(collector, text_cleanup)
    _collect_reference_cleanup_issues(collector, reference_cleanup)
    _collect_semantic_cleanup_issues(collector, semantic_cleanup)
    _collect_ocr_quality_issues(collector, ocr_quality)
    _collect_reading_order_issues(collector, reading_order)
    _collect_link_health_issues(collector, link_health)
    _collect_metadata_health_issues(collector, metadata_health)
    _collect_visible_junk_issues(collector, visible_junk)
    _collect_content_metric_issues(collector, content_metrics, toc_preview, asset_summary)

    return collector.issue_groups()


def _collect_validation_issues(
    collector: "_IssueCollector",
    validation: Mapping[str, Any],
    epubcheck_detail: Mapping[str, Any],
) -> None:
    validation_status = _status(
        _first_present(
            validation.get("status"),
            _mapping(validation.get("summary")).get("status"),
            validation.get("validation_status"),
        )
    )
    if validation_status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "validation_failed",
            _first_text(
                validation.get("message"),
                _mapping(validation.get("summary")).get("message"),
                "EPUB validation failed.",
            ),
            "validation",
            "Run the validator output through the structural repair path before release.",
        )

    epubcheck_status = _status(
        _first_present(
            epubcheck_detail.get("status"),
            _mapping(validation.get("epubcheck")).get("status"),
            validation.get("epubcheck_status"),
        )
    )
    if epubcheck_status in FAILED_STATUSES:
        messages = _iter_issue_like_items(
            _first_present(epubcheck_detail.get("messages"), _mapping(validation.get("epubcheck")).get("messages"))
        )
        if not messages:
            messages = ({"message": "EPUBCheck reported a blocking validation failure."},)
        for item in messages:
            collector.add(
                "blocker",
                "epubcheck_failed",
                _first_text(item.get("message"), item.get("detail"), item.get("text"), "EPUBCheck failed."),
                "epubcheck",
                "Inspect the EPUBCheck message and repair the referenced package or XHTML file.",
                file=item.get("file") or item.get("path"),
                section=item.get("section"),
                page=item.get("page"),
            )


def _collect_size_budget_issues(collector: "_IssueCollector", size_budget: Mapping[str, Any]) -> None:
    status = _status(_first_present(size_budget.get("status"), size_budget.get("size_budget_status")))
    message = _first_text(size_budget.get("message"), size_budget.get("size_budget_message"), "Size budget needs review.")
    if status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "size_budget_failed",
            message,
            "size_budget",
            "Reduce output weight or choose a conversion strategy that fits the hard budget.",
        )
    elif status in WARNING_STATUSES:
        collector.add(
            "warning",
            "size_budget_warning",
            message,
            "size_budget",
            "Review image density and output size before release.",
        )


def _collect_heading_repair_issues(collector: "_IssueCollector", heading_repair: Mapping[str, Any]) -> None:
    status = _status(heading_repair.get("status"))
    if status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "heading_repair_failed",
            _first_text(heading_repair.get("error"), heading_repair.get("message"), "Heading repair failed."),
            "heading_repair",
            "Keep the previous valid EPUB structure and inspect heading repair diagnostics.",
        )

    review_count = _safe_int(
        _first_present(
            heading_repair.get("review"),
            heading_repair.get("manual_review_count"),
            heading_repair.get("review_count"),
        )
    )
    release_status = _status(_first_present(heading_repair.get("release"), heading_repair.get("release_status")))
    if review_count > 0 or release_status in WARNING_STATUSES:
        collector.add(
            "review",
            "heading_repair_review",
            _plural_message(review_count, "Heading repair left {count} item for manual review.", "Heading repair needs manual review."),
            "heading_repair",
            "Inspect the repaired TOC and demoted heading candidates.",
        )

    for item in _iter_issue_like_items(heading_repair.get("manual_review_pages")):
        collector.add(
            "review",
            "heading_repair_page_review",
            _first_text(item.get("message"), item.get("title"), "Heading repair flagged a page for review."),
            "heading_repair",
            "Confirm whether the page contains a real section heading.",
            page=item.get("page") or item.get("page_index"),
            section=item.get("section") or item.get("title"),
            file=item.get("file"),
        )
    for item in _iter_issue_like_items(heading_repair.get("manual_review_sections")):
        collector.add(
            "review",
            "heading_repair_section_review",
            _first_text(item.get("message"), item.get("title"), "Heading repair flagged a section for review."),
            "heading_repair",
            "Confirm section hierarchy and TOC placement.",
            page=item.get("page"),
            section=item.get("section") or item.get("title"),
            file=item.get("file"),
        )


def _collect_audit_issues(collector: "_IssueCollector", audit: Mapping[str, Any]) -> None:
    for warning in _string_items(audit.get("warnings")):
        collector.add(
            "warning",
            "audit_warning",
            warning,
            "audit",
            "Review the audit warning and decide whether it blocks publication quality.",
        )
    page_items = _iter_issue_like_items(_first_present(audit.get("high_risk_page_list"), audit.get("high_risk_pages")))
    if not page_items and _safe_int(audit.get("high_risk_pages")) > 0:
        collector.add(
            "review",
            "manual_review_page",
            _plural_message(_safe_int(audit.get("high_risk_pages")), "Audit flagged {count} high-risk page.", "Audit flagged high-risk pages."),
            "audit",
            "Manually inspect flagged pages for layout, table, OCR, or reading-order issues.",
        )
    for item in page_items:
        collector.add(
            "review",
            "manual_review_page",
            _first_text(item.get("message"), item.get("title"), "Audit flagged a high-risk page."),
            "audit",
            "Manually inspect the page for layout, table, OCR, or reading-order issues.",
            page=item.get("page") or item.get("page_index"),
            section=item.get("section") or item.get("title"),
            file=item.get("file"),
        )
    section_items = _iter_issue_like_items(_first_present(audit.get("high_risk_section_list"), audit.get("high_risk_sections")))
    if not section_items and _safe_int(audit.get("high_risk_sections")) > 0:
        collector.add(
            "review",
            "manual_review_section",
            _plural_message(_safe_int(audit.get("high_risk_sections")), "Audit flagged {count} high-risk section.", "Audit flagged high-risk sections."),
            "audit",
            "Manually inspect flagged sections for structure and reading-order quality.",
        )
    for item in section_items:
        collector.add(
            "review",
            "manual_review_section",
            _first_text(item.get("message"), item.get("title"), "Audit flagged a high-risk section."),
            "audit",
            "Manually inspect the section for structure and reading-order quality.",
            page=item.get("page"),
            section=item.get("section") or item.get("title"),
            file=item.get("file"),
        )


def _collect_text_cleanup_issues(collector: "_IssueCollector", text_cleanup: Mapping[str, Any]) -> None:
    if _truthy(text_cleanup.get("publish_blocked")) or _status(text_cleanup.get("status")) == "publish_blocked":
        collector.add(
            "blocker",
            "text_cleanup_publish_blocked",
            _first_text(text_cleanup.get("message"), "Text cleanup marked the EPUB as not publishable."),
            "text_cleanup",
            "Inspect blocked cleanup decisions and rerun after source text repair.",
        )

    blocked_count = _safe_int(_first_present(text_cleanup.get("blocked_count"), text_cleanup.get("blocked")))
    if blocked_count > 0:
        collector.add(
            "blocker",
            "text_cleanup_blocked",
            _plural_message(blocked_count, "Text cleanup blocked {count} unsafe change.", "Text cleanup blocked unsafe changes."),
            "text_cleanup",
            "Review blocked text cleanup decisions before release.",
        )

    review_count = _safe_int(_first_present(text_cleanup.get("review_needed_count"), text_cleanup.get("review_count")))
    if review_count > 0:
        collector.add(
            "review",
            "text_cleanup_review",
            _plural_message(review_count, "Text cleanup left {count} item for review.", "Text cleanup needs review."),
            "text_cleanup",
            "Inspect review-needed cleanup decisions for false positives or missed repairs.",
        )

    artifact_rate = _mapping(_first_present(text_cleanup.get("artifact_rate"), text_cleanup.get("text_artifacts")))
    artifact_status = _status(artifact_rate.get("status"))
    artifact_count = _safe_int(artifact_rate.get("artifact_count"))
    artifact_rate_value = _safe_float(artifact_rate.get("artifact_rate_per_1000_words"))
    if artifact_status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "text_artifact_rate_failed",
            f"Reader text has {artifact_rate_value:g} visible artifact(s) per 1000 words.",
            "text_cleanup",
            "Inspect the artifact-rate report and repair split words, OCR junk, visible placeholders, or punctuation spacing before release.",
        )
    elif artifact_status in WARNING_STATUSES or artifact_count > 0:
        collector.add(
            "review",
            "text_artifact_rate_review",
            f"Reader text has {artifact_rate_value:g} visible artifact(s) per 1000 words.",
            "text_cleanup",
            "Review artifact-rate samples before treating this EPUB as premium quality.",
        )


def _collect_reference_cleanup_issues(collector: "_IssueCollector", reference_cleanup: Mapping[str, Any]) -> None:
    status = _status(
        _first_present(
            reference_cleanup.get("status"),
            reference_cleanup.get("quality_gate_status"),
            reference_cleanup.get("reference_quality_gate_status"),
        )
    )
    if status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "reference_cleanup_failed",
            _first_text(reference_cleanup.get("message"), reference_cleanup.get("error"), "Reference cleanup failed a release gate."),
            "reference_cleanup",
            "Repair bibliography/reference cleanup before publication or explicitly accept the unresolved release gate.",
        )

    citations_detected = _safe_int(reference_cleanup.get("citations_detected"))
    citations_covered = _safe_int(reference_cleanup.get("citations_covered"))
    citations_missing = _safe_int(reference_cleanup.get("citations_missing_record"))
    citations_ambiguous = _safe_int(reference_cleanup.get("citations_ambiguous"))
    if citations_missing > 0 or citations_ambiguous > 0 or (citations_detected > 0 and citations_covered < citations_detected):
        collector.add(
            "blocker",
            "reference_coverage_failed",
            "Inline citations are not fully covered by final bibliography records.",
            "reference_cleanup",
            "Rebuild bibliography records from source data or block publication until citation coverage is complete.",
        )

    visible_junk_count = _safe_int(reference_cleanup.get("visible_junk_detected"))
    if visible_junk_count > 0:
        collector.add(
            "blocker",
            "reference_visible_junk",
            _plural_message(visible_junk_count, "Reference cleanup left {count} visible junk marker.", "Reference cleanup left visible junk."),
            "reference_cleanup",
            "Repair bibliography rendering so technical markers are not visible in the EPUB.",
        )

    unresolved_count = _safe_int(reference_cleanup.get("unresolved_fragment_count"))
    if unresolved_count > 0:
        collector.add(
            "review",
            "reference_unresolved_fragments",
            _plural_message(unresolved_count, "Reference cleanup left {count} unresolved fragment.", "Reference cleanup left unresolved fragments."),
            "reference_cleanup",
            "Review unresolved bibliography fragments and recover only high-confidence records.",
        )

    empty_reference_sections = _safe_int(reference_cleanup.get("empty_reference_sections_unresolved"))
    if empty_reference_sections > 0 and citations_detected > 0:
        collector.add(
            "blocker",
            "empty_reference_section",
            _plural_message(empty_reference_sections, "Reference cleanup found {count} empty reference section while citations exist.", "Reference cleanup found empty reference sections while citations exist."),
            "reference_cleanup",
            "Recover the bibliography from the source document or remove unresolved citation markers before release.",
        )
    elif empty_reference_sections > 0:
        collector.add(
            "review",
            "reference_empty_section_review",
            _plural_message(empty_reference_sections, "Reference cleanup found {count} empty reference section.", "Reference cleanup found empty reference sections."),
            "reference_cleanup",
            "Confirm whether the source intentionally has no bibliography, or remove the empty section from the final EPUB.",
        )

    review_records = _iter_issue_like_items(
        _first_present(reference_cleanup.get("review_records"), reference_cleanup.get("manual_review_records"))
    )
    if review_records:
        for item in review_records:
            collector.add(
                "review",
                "reference_review_record",
                _first_text(item.get("message"), item.get("title"), item.get("record"), "Reference record needs review."),
                "reference_cleanup",
                "Confirm the reference title, link, and record boundary.",
                page=item.get("page"),
                section=item.get("section") or item.get("title"),
                file=item.get("file"),
            )
    elif _safe_int(reference_cleanup.get("review_record_count")) > 0:
        collector.add(
            "review",
            "reference_review_record",
            _plural_message(
                _safe_int(reference_cleanup.get("review_record_count")),
                "Reference cleanup left {count} record for review.",
                "Reference cleanup left records for review.",
            ),
            "reference_cleanup",
            "Confirm reviewed reference titles, links, and record boundaries.",
        )


def _collect_semantic_cleanup_issues(collector: "_IssueCollector", semantic_cleanup: Mapping[str, Any]) -> None:
    status = _status(_first_present(semantic_cleanup.get("status"), semantic_cleanup.get("quality_gate_status")))
    if _truthy(semantic_cleanup.get("publish_blocked")) or status in FAILED_STATUSES or status == "publish_blocked":
        collector.add(
            "blocker",
            "semantic_cleanup_failed",
            _first_text(semantic_cleanup.get("message"), semantic_cleanup.get("error"), "Semantic cleanup failed a release gate."),
            "semantic_cleanup",
            "Inspect semantic cleanup diagnostics and preserve the previous valid EPUB until the gate passes.",
        )
    elif status in WARNING_STATUSES:
        collector.add(
            "warning",
            "semantic_cleanup_warning",
            _first_text(semantic_cleanup.get("message"), "Semantic cleanup passed with warnings."),
            "semantic_cleanup",
            "Review semantic cleanup warnings before release.",
        )

    review_count = _safe_int(
        _first_present(
            semantic_cleanup.get("manual_review_count"),
            semantic_cleanup.get("review_count"),
            semantic_cleanup.get("review_needed_count"),
        )
    )
    if review_count > 0:
        collector.add(
            "review",
            "semantic_cleanup_review",
            _plural_message(review_count, "Semantic cleanup left {count} item for review.", "Semantic cleanup needs review."),
            "semantic_cleanup",
            "Confirm headings, paragraphs, lists, tables, and semantic cleanup decisions.",
        )


def _collect_ocr_quality_issues(collector: "_IssueCollector", ocr_quality: Mapping[str, Any]) -> None:
    status = _status(_first_present(ocr_quality.get("status"), ocr_quality.get("quality_gate_status")))
    degraded_count = _safe_int(
        _first_present(
            ocr_quality.get("degraded_count"),
            ocr_quality.get("degradation_count"),
            ocr_quality.get("ocr_degradation_count"),
        )
    )
    if status in FAILED_STATUSES or status in {"degraded", "publish_blocked"} or degraded_count > 0:
        collector.add(
            "blocker",
            "ocr_degradation_failed",
            _first_text(
                ocr_quality.get("message"),
                ocr_quality.get("error"),
                _plural_message(degraded_count, "OCR quality reported {count} degraded page.", "OCR quality failed a release gate."),
            ),
            "ocr_quality",
            "Review low-confidence OCR output and rerun with a stronger OCR path or image fallback.",
        )
    elif status in WARNING_STATUSES:
        collector.add(
            "warning",
            "ocr_quality_warning",
            _first_text(ocr_quality.get("message"), "OCR quality passed with warnings."),
            "ocr_quality",
            "Inspect OCR warning pages before release.",
        )

    review_count = _safe_int(
        _first_present(
            ocr_quality.get("low_confidence_page_count"),
            ocr_quality.get("manual_review_count"),
            ocr_quality.get("review_count"),
        )
    )
    if review_count > 0:
        collector.add(
            "review",
            "ocr_quality_review",
            _plural_message(review_count, "OCR quality left {count} page for review.", "OCR quality needs review."),
            "ocr_quality",
            "Open the flagged OCR pages and confirm text readability.",
        )


def _collect_reading_order_issues(collector: "_IssueCollector", reading_order: Mapping[str, Any]) -> None:
    status = _status(_first_present(reading_order.get("status"), reading_order.get("quality_gate_status")))
    if status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "reading_order_failed",
            _first_text(reading_order.get("message"), reading_order.get("error"), "Reading-order quality failed a release gate."),
            "reading_order",
            "Repair reading order or route the document through a layout-aware conversion path.",
        )
    elif status in WARNING_STATUSES:
        collector.add(
            "warning",
            "reading_order_warning",
            _first_text(reading_order.get("message"), "Reading-order quality passed with warnings."),
            "reading_order",
            "Review suspicious reading-order regions before release.",
        )

    review_count = _safe_int(
        _first_present(
            reading_order.get("manual_review_count"),
            reading_order.get("review_count"),
            reading_order.get("low_confidence_region_count"),
        )
    )
    if review_count > 0:
        collector.add(
            "review",
            "reading_order_review",
            _plural_message(review_count, "Reading order left {count} region for review.", "Reading order needs review."),
            "reading_order",
            "Inspect flagged columns, tables, sidebars, and region ordering.",
        )


def _collect_link_health_issues(collector: "_IssueCollector", link_health: Mapping[str, Any]) -> None:
    status = _status(link_health.get("status"))
    if status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "link_health_failed",
            _first_text(link_health.get("message"), "Link health checks failed."),
            "link_health",
            "Repair broken internal hrefs, fragments, or invalid external URLs.",
        )
    elif status in WARNING_STATUSES:
        collector.add(
            "warning",
            "link_health_warning",
            _first_text(link_health.get("message"), "Link health checks passed with warnings."),
            "link_health",
            "Review link warnings and decide whether they are acceptable for release.",
        )


def _collect_metadata_health_issues(collector: "_IssueCollector", metadata_health: Mapping[str, Any]) -> None:
    status = _status(metadata_health.get("status"))
    if status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "metadata_health_failed",
            _first_text(metadata_health.get("message"), metadata_health.get("error"), "Metadata health failed a release gate."),
            "metadata_health",
            "Fix required reader-facing metadata such as title, creator, language, and identifiers before publication.",
        )

    placeholders = _string_items(metadata_health.get("placeholders"))
    placeholder_count = _safe_int(metadata_health.get("placeholder_count"))
    if placeholders:
        for placeholder in placeholders:
            collector.add(
                "warning",
                "metadata_placeholder",
                f"Metadata contains placeholder value: {placeholder}",
                "metadata_health",
                "Replace placeholder metadata with publication-specific title, author, language, or publisher values.",
            )
    elif placeholder_count > 0:
        collector.add(
            "warning",
            "metadata_placeholder",
            _plural_message(placeholder_count, "Metadata contains {count} placeholder value.", "Metadata contains placeholder values."),
            "metadata_health",
            "Replace placeholder metadata before release.",
        )


def _collect_visible_junk_issues(collector: "_IssueCollector", visible_junk: Mapping[str, Any]) -> None:
    count = _safe_int(_first_present(visible_junk.get("count"), visible_junk.get("detected"), visible_junk.get("visible_junk_detected")))
    status = _status(visible_junk.get("status"))
    if count > 0 or status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "visible_junk_detected",
            _plural_message(count, "Visible junk scan found {count} artifact.", "Visible junk scan found artifacts."),
            "visible_junk",
            "Remove visible technical labels, broken URL fragments, and cleanup markers from EPUB content.",
        )
    elif status in WARNING_STATUSES:
        collector.add(
            "warning",
            "visible_junk_warning",
            _first_text(visible_junk.get("message"), "Visible junk scan needs review."),
            "visible_junk",
            "Inspect suspicious visible text before release.",
        )


def _collect_content_metric_issues(
    collector: "_IssueCollector",
    content_metrics: Mapping[str, Any],
    toc_preview: Mapping[str, Any],
    asset_summary: Mapping[str, Any],
) -> None:
    source_toc_entries = _safe_int(content_metrics.get("source_toc_entries"))
    output_toc_entries = _safe_int(
        _first_present(content_metrics.get("toc_entry_count"), content_metrics.get("output_toc_entries"), toc_preview.get("entry_count"))
    )
    if source_toc_entries > 0 and output_toc_entries == 0:
        collector.add(
            "blocker",
            "source_toc_lost",
            "Source document had TOC/bookmarks, but final EPUB does not report navigation entries.",
            "toc",
            "Rebuild the EPUB navigation from source outline or recovered headings before release.",
        )

    toc_noise_count = _safe_int(_first_present(content_metrics.get("toc_noise_entry_count"), toc_preview.get("noise_entry_count")))
    if toc_noise_count > 0:
        collector.add(
            "review",
            "toc_noise_entry",
            _plural_message(toc_noise_count, "TOC contains {count} low-information entry.", "TOC contains low-information entries."),
            "toc",
            "Demote table labels, orphan numeric headings, and other low-information entries from navigation.",
        )

    source_table_count = _safe_int(content_metrics.get("source_table_count"))
    xhtml_table_count = _safe_int(_first_present(content_metrics.get("xhtml_table_count"), content_metrics.get("table_count")))
    transformed_table_count = _safe_int(content_metrics.get("transformed_table_count"))
    suppressed_table_fragment_count = _safe_int(content_metrics.get("suppressed_table_fragment_count"))
    represented_table_count = xhtml_table_count + transformed_table_count + suppressed_table_fragment_count
    if source_table_count > 0 and represented_table_count == 0:
        collector.add(
            "blocker",
            "table_semantics_lost",
            "Source document had tables, but final EPUB does not report rendered, transformed, or suppressed table decisions.",
            "content_metrics",
            "Preserve tables as semantic XHTML or explicitly accept a table-loss release exception.",
        )
    elif source_table_count > 0 and represented_table_count < source_table_count:
        collector.add(
            "warning",
            "table_semantics_partial",
            f"Only {represented_table_count}/{source_table_count} source table candidates are accounted for.",
            "content_metrics",
            "Inspect table extraction and preserve, transform, or explicitly suppress missing table candidates.",
        )

    low_confidence_table_count = _safe_int(content_metrics.get("low_confidence_table_count"))
    rendered_low_confidence_table_count = _safe_int(content_metrics.get("rendered_low_confidence_table_count"))
    rendered_fragment_table_count = _safe_int(content_metrics.get("rendered_fragment_table_count"))
    if low_confidence_table_count > 0 and rendered_low_confidence_table_count > 0:
        collector.add(
            "review",
            "table_semantics_review",
            _plural_message(low_confidence_table_count, "Table extraction left {count} low-confidence table.", "Table extraction left low-confidence tables."),
            "content_metrics",
            "Review the extracted XHTML tables against the source PDF before publication.",
        )

    if rendered_low_confidence_table_count > 0 or rendered_fragment_table_count > 0:
        collector.add(
            "blocker",
            "table_false_positive_rendered",
            "Low-confidence or fragment table candidates are still rendered in the EPUB.",
            "content_metrics",
            "Suppress false-positive table candidates or recover them as complete semantic tables before release.",
        )

    transformed_table_content_loss_count = _safe_int(content_metrics.get("transformed_table_content_loss_count"))
    if transformed_table_content_loss_count > 0:
        collector.add(
            "blocker",
            "transformed_table_content_lost",
            _plural_message(
                transformed_table_content_loss_count,
                "Detected {count} transformed table with lost row content.",
                "Detected transformed tables with lost row content.",
            ),
            "content_metrics",
            "Preserve transformed table row summaries through EPUB finalization before release.",
        )

    wide_table_count = _safe_int(content_metrics.get("wide_table_count"))
    if wide_table_count > 0:
        collector.add(
            "review",
            "wide_table_review",
            _plural_message(wide_table_count, "EPUB contains {count} wide table.", "EPUB contains wide tables."),
            "content_metrics",
            "Check wide tables on Kindle-like viewport and simplify only if readability suffers.",
        )

    fragment_table_count = _safe_int(content_metrics.get("fragment_table_count"))
    unresolved_fragment_count = max(0, fragment_table_count - suppressed_table_fragment_count)
    if unresolved_fragment_count > 0:
        collector.add(
            "blocker",
            "table_fragment_detected",
            _plural_message(unresolved_fragment_count, "Table extraction produced {count} unresolved fragment table.", "Table extraction produced unresolved fragment tables."),
            "content_metrics",
            "Recover the full table from source geometry or demote the fragment to linear text before release.",
        )

    tiny_tail_sections = _iter_issue_like_items(content_metrics.get("tiny_tail_sections"))
    tiny_tail_count = _safe_int(content_metrics.get("tiny_tail_section_count"))
    if tiny_tail_sections:
        for item in tiny_tail_sections:
            collector.add(
                "blocker",
                "tiny_tail_section",
                _first_text(item.get("message"), item.get("section"), "Tiny trailing section may indicate truncated conversion output."),
                "content_metrics",
                "Inspect the final sections and merge or recover any truncated tail content.",
                section=item.get("section"),
                page=item.get("page") or item.get("page_start"),
            )
    elif tiny_tail_count > 0:
        collector.add(
            "blocker",
            "tiny_tail_section",
            _plural_message(tiny_tail_count, "Detected {count} tiny trailing section.", "Detected tiny trailing sections."),
            "content_metrics",
            "Inspect the final sections and merge or recover any truncated tail content.",
        )

    asset_budget_status = _status(_first_present(asset_summary.get("asset_budget_status"), content_metrics.get("asset_budget_status")))
    if asset_budget_status in FAILED_STATUSES:
        collector.add(
            "blocker",
            "asset_budget_failed",
            "Asset budget failed for this publication class.",
            "asset_summary",
            "Avoid rasterizing semantic content and reduce image-heavy assets before release.",
        )


class _IssueCollector:
    def __init__(self) -> None:
        self._groups: dict[str, list[dict[str, Any]]] = {key: [] for key in ISSUE_GROUP_KEYS}
        self._seen: set[tuple[str, str, str, str]] = set()

    def add(
        self,
        group: str,
        code: str,
        message: str,
        source: str,
        suggested_action: str,
        *,
        page: Any = None,
        section: Any = None,
        file: Any = None,
    ) -> None:
        severity = "blocker" if group == "blockers" else group
        group_key = {"blocker": "blockers", "warning": "warnings", "review": "review"}.get(severity, "")
        if group_key not in self._groups:
            return
        normalized_message = _first_text(message, "Quality issue detected.")
        issue = {
            "severity": severity,
            "code": str(code),
            "message": normalized_message,
            "source": str(source),
            "suggested_action": _first_text(suggested_action, "Review this issue before release."),
        }
        for key, value in (("page", page), ("section", section), ("file", file)):
            coerced = _optional_text_or_int(value)
            if coerced is not None:
                issue[key] = coerced
        dedupe_key = (issue["severity"], issue["code"], issue["source"], issue["message"])
        if dedupe_key in self._seen:
            return
        self._seen.add(dedupe_key)
        self._groups[group_key].append(issue)

    def issue_groups(self) -> dict[str, list[dict[str, Any]]]:
        return {key: list(self._groups[key]) for key in ISSUE_GROUP_KEYS}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_int(value: Any) -> int:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, converted)


def _safe_float(value: Any) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, converted)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "blocked", "failed"}
    return bool(value)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _plural_message(count: int, counted_template: str, fallback: str) -> str:
    if count > 0:
        return counted_template.format(count=count)
    return fallback


def _string_items(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return ()
    items: list[str] = []
    for raw_item in value:
        text = str(raw_item).strip()
        if text:
            items.append(text)
    return tuple(items)


def _iter_issue_like_items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return (value,)
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ()
    items: list[Mapping[str, Any]] = []
    for raw_item in value:
        if isinstance(raw_item, Mapping):
            items.append(raw_item)
        elif isinstance(raw_item, str) and raw_item.strip():
            items.append({"message": raw_item.strip()})
    return tuple(items)


def _optional_text_or_int(value: Any) -> str | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)
