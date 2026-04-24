from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SUSPICIOUS_HEADING_MARKERS = (
    "material sponsorowany",
    "materia\xc5\x82 sponsorowany",
    "page ",
    "strona ",
    "www.",
)
_EPUBCHECK_UNAVAILABLE_WARNING_MARKERS = (
    "formalna walidacja epubcheck nie mogla zostac wykonana",
    "formalna walidacja epubcheck nie mogła zostać wykonana",
)


def normalize_status(raw_status: str) -> str:
    normalized = (raw_status or "").strip().lower()
    if normalized in {"pass", "passed"}:
        return "passed"
    if normalized in {"pass_with_review", "passed_with_warnings", "warning", "warnings"}:
        return "passed_with_warnings"
    if normalized in {"fail", "failed", "error"}:
        return "failed"
    if normalized == "unavailable":
        return "passed_with_warnings"
    return "failed"


def merge_statuses(statuses: Iterable[str]) -> str:
    normalized = [normalize_status(status) for status in statuses]
    if any(status == "failed" for status in normalized):
        return "failed"
    if any(status == "passed_with_warnings" for status in normalized):
        return "passed_with_warnings"
    return "passed"


def extract_workflow_quality_signals(
    *,
    validation: dict[str, Any],
    audit: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> dict[str, Any]:
    internal_errors = (validation.get("internal_links") or {}).get("errors", [])
    external_errors = (validation.get("external_links") or {}).get("errors", [])
    package_errors = (validation.get("package") or {}).get("errors", [])
    text_cleanup = ((quality_report or {}).get("text_cleanup") or {}) if quality_report else {}
    reference_cleanup = text_cleanup.get("reference_cleanup") or {}
    gates = audit.get("gates") or {}

    return {
        "validation_status": normalize_status((validation.get("summary") or {}).get("status", "failed")),
        "epubcheck_status": normalize_status((validation.get("epubcheck") or {}).get("status", "failed")),
        "error_count": int((validation.get("summary") or {}).get("error_count", 0)),
        "warning_count": int((validation.get("summary") or {}).get("warning_count", 0)),
        "internal_link_error_count": len(internal_errors),
        "external_link_error_count": len(external_errors),
        "broken_href_error_count": count_broken_href_errors(internal_errors + external_errors + package_errors),
        "duplicate_id_error_count": sum(1 for error in internal_errors if "duplicate id" in error.lower()),
        "reference_cleanup_status": normalize_status(reference_cleanup.get("quality_gate_status", "unavailable"))
        if reference_cleanup
        else "passed_with_warnings",
        "reference_visible_junk_detected": int(reference_cleanup.get("visible_junk_detected", 0))
        if reference_cleanup
        else 0,
        "heading_gate_status": normalize_status(((gates.get("C") or {}).get("status", "unavailable"))),
        "toc_gate_status": normalize_status(((gates.get("D") or {}).get("status", "unavailable"))),
        "text_cleanup_review_needed_count": int(text_cleanup.get("review_needed_count", 0)) if text_cleanup else 0,
        "text_cleanup_blocked_count": int(text_cleanup.get("blocked_count", 0)) if text_cleanup else 0,
    }


def _is_epubcheck_unavailable_warning(message: str) -> bool:
    normalized = str(message or "").strip().lower()
    return any(marker in normalized for marker in _EPUBCHECK_UNAVAILABLE_WARNING_MARKERS)


def _quality_report_validation_is_superseded(
    *,
    validation: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> bool:
    if not quality_report:
        return False

    explicit_validation_status = normalize_status((validation.get("summary") or {}).get("status", "failed"))
    explicit_epubcheck_status = normalize_status((validation.get("epubcheck") or {}).get("status", "failed"))
    if explicit_validation_status != "passed" or explicit_epubcheck_status != "passed":
        return False

    raw_quality_status = str(quality_report.get("validation_status", "") or "").strip().lower()
    if raw_quality_status not in {"unavailable", "passed_with_warnings", "warning", "warnings"}:
        return False

    warnings = [str(item).strip() for item in (quality_report.get("warnings") or []) if str(item).strip()]
    if not warnings:
        return raw_quality_status == "unavailable"
    return all(_is_epubcheck_unavailable_warning(item) for item in warnings)


def collect_workflow_symptoms(
    *,
    validation: dict[str, Any],
    audit: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> list[str]:
    symptoms: list[str] = []
    summary = validation.get("summary") or {}
    if summary.get("status") == "failed":
        symptoms.append(f"validation failed with {summary.get('error_count', 0)} errors")
    internal_errors = (validation.get("internal_links") or {}).get("errors", [])
    if internal_errors:
        symptoms.append(internal_errors[0])
    external_errors = (validation.get("external_links") or {}).get("errors", [])
    if external_errors:
        symptoms.append(external_errors[0])
    audit_gates = audit.get("gates") or {}
    for gate_name in ("A", "B", "C", "D", "E", "F"):
        gate = audit_gates.get(gate_name) or {}
        if gate.get("status") in {"fail", "passed_with_warnings", "pass_with_review"}:
            gate_message = gate.get("message") or gate.get("reason") or gate.get("summary") or ""
            if gate_message:
                symptoms.append(f"gate {gate_name}: {gate_message}")
    if quality_report:
        warnings = list(quality_report.get("warnings") or [])
        if _quality_report_validation_is_superseded(validation=validation, quality_report=quality_report):
            warnings = [warning for warning in warnings if not _is_epubcheck_unavailable_warning(str(warning))]
        symptoms.extend(warnings[:3])
        text_cleanup = quality_report.get("text_cleanup") or {}
        if text_cleanup.get("review_needed_count"):
            symptoms.append(f"text cleanup review_needed={text_cleanup['review_needed_count']}")
        reference_cleanup = text_cleanup.get("reference_cleanup") or {}
        if reference_cleanup.get("visible_junk_detected"):
            symptoms.append(f"reference visible_junk_detected={reference_cleanup['visible_junk_detected']}")
    return list(dict.fromkeys(symptoms))


def derive_workflow_snapshot_status(
    *,
    validation: dict[str, Any],
    audit: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> str:
    statuses = [
        normalize_status((validation.get("summary") or {}).get("status", "failed")),
        normalize_status(audit.get("decision", "failed")),
    ]
    if quality_report and not _quality_report_validation_is_superseded(
        validation=validation,
        quality_report=quality_report,
    ):
        statuses.append(normalize_status(quality_report.get("validation_status", "passed")))
        warnings = quality_report.get("warnings") or []
        if warnings and statuses[-1] == "passed":
            statuses[-1] = "passed_with_warnings"
    return merge_statuses(statuses)


def build_before_after_report(
    *,
    baseline_payload: dict[str, Any],
    verification_snapshot: dict[str, Any],
    regression: dict[str, Any],
    smoke: dict[str, Any],
) -> dict[str, Any]:
    before_snapshot = baseline_payload.get("snapshot", {})
    before_signals = before_snapshot.get("signals", {})
    after_signals = verification_snapshot.get("signals", {})
    delta = {
        key: after_signals.get(key) - before_signals.get(key)
        for key in _numeric_signal_keys(before_signals, after_signals)
    }
    remaining_risks = list(dict.fromkeys((verification_snapshot.get("symptoms") or [])[:10]))
    status = merge_statuses(
        [
            verification_snapshot.get("status", "failed"),
            regression.get("status", "failed"),
            smoke.get("status", "failed"),
        ]
    )
    if regression.get("status") == "failed" or smoke.get("status") == "failed":
        status = "failed"

    return {
        "run_id": baseline_payload["run_id"],
        "status": status,
        "report_complete": True,
        "before": {
            "status": before_snapshot.get("status", "failed"),
            "signals": before_signals,
            "artifacts": before_snapshot.get("artifacts", {}),
        },
        "after": {
            "status": verification_snapshot.get("status", "failed"),
            "signals": after_signals,
            "artifacts": verification_snapshot.get("artifacts", {}),
        },
        "delta": delta,
        "regression_pack_status": regression.get("status", "failed"),
        "smoke_status": smoke.get("status", "failed"),
        "remaining_risks": remaining_risks,
        "unresolved_warnings": remaining_risks,
    }


def make_review_item(kind: str, file_name: str, subject: str, reason: str, confidence: float) -> dict[str, Any]:
    return {
        "kind": kind,
        "file": file_name,
        "subject": subject,
        "reason": reason,
        "confidence": round(confidence, 2),
    }


def dedupe_review_items(items: list[Any]) -> list[Any]:
    deduped: list[Any] = []
    seen = set()
    for item in items:
        if isinstance(item, dict):
            marker = (item.get("kind"), item.get("file"), item.get("subject"), item.get("reason"))
        else:
            marker = ("legacy-review-item", str(item).strip())
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def build_manual_review_queue_payload(items: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any]:
    normalized_items = dedupe_review_items(list(items or []))
    kind_counts: Counter[str] = Counter()
    distinct_files: set[str] = set()

    for item in normalized_items:
        if isinstance(item, dict):
            kind = str(item.get("kind", "") or "unknown")
            file_name = str(item.get("file", "") or "").strip()
        else:
            kind = "legacy-review-item"
            file_name = ""
        kind_counts[kind] += 1
        if file_name:
            distinct_files.add(file_name)

    return {
        "summary": {
            "queue_size": len(normalized_items),
            "distinct_file_count": len(distinct_files),
            "kind_counts": dict(kind_counts),
        },
        "items": normalized_items,
    }


def build_gate_result(
    gate_id: str,
    *,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    manual_review: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blockers = blockers or []
    warnings = warnings or []
    manual_review = manual_review or []
    if blockers:
        status = "fail"
        summary = f"Gate {gate_id} failed."
    elif warnings or manual_review:
        status = "pass_with_review"
        summary = f"Gate {gate_id} passed with review."
    else:
        status = "pass"
        summary = f"Gate {gate_id} passed."
    return {
        "gate": gate_id,
        "status": status,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "manual_review": dedupe_review_items(manual_review),
    }


def build_failed_gate(gate_id: str, message: str) -> dict[str, Any]:
    return {
        "gate": gate_id,
        "status": "fail",
        "summary": message,
        "blockers": [message],
        "warnings": [],
        "manual_review": [],
    }


def is_suspicious_heading(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return True
    if any(marker in lowered for marker in SUSPICIOUS_HEADING_MARKERS):
        return True
    if lowered.startswith("go to solution"):
        return True
    if normalized.isdigit():
        return True
    return False


def heading_change_reason(text: str, *, removed: bool) -> tuple[str, float]:
    normalized = " ".join((text or "").split()).strip()
    lowered = normalized.lower()
    if any(marker in lowered for marker in SUSPICIOUS_HEADING_MARKERS):
        return ("layout-artifact-filtered" if removed else "recovered-section-heading"), 0.92 if removed else 0.7
    if len(normalized) <= 4:
        return ("short-ambiguous-heading", 0.58)
    if normalized.isupper() and len(normalized) <= 40:
        return ("uppercase-layout-candidate", 0.66)
    return ("semantic-heading-normalization", 0.76 if removed else 0.74)


def compare_heading_snapshots(
    *,
    before_snapshot: list[dict[str, Any]],
    after_snapshot: list[dict[str, Any]],
    file_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    before_by_key = {(item["level"], item["text"]): item for item in before_snapshot}
    after_by_key = {(item["level"], item["text"]): item for item in after_snapshot}

    for key, before in before_by_key.items():
        if key in after_by_key:
            continue
        reason, confidence = heading_change_reason(before.get("text", ""), removed=True)
        decision = {
            "file": file_name,
            "status": "removed",
            "before": before,
            "after": None,
            "reason": reason,
            "confidence": confidence,
        }
        decisions.append(decision)
        if confidence < 0.65 or reason in {"short-ambiguous-heading", "uppercase-layout-candidate"}:
            review.append(make_review_item("heading", file_name, before.get("text", ""), reason, confidence))

    for key, after in after_by_key.items():
        if key in before_by_key:
            continue
        reason, confidence = heading_change_reason(after.get("text", ""), removed=False)
        decision = {
            "file": file_name,
            "status": "recovered",
            "before": None,
            "after": after,
            "reason": reason,
            "confidence": confidence,
        }
        decisions.append(decision)
        if confidence < 0.65 or reason in {"short-ambiguous-heading", "uppercase-layout-candidate"}:
            review.append(make_review_item("heading", file_name, after.get("text", ""), reason, confidence))

    return decisions, review


def summarize_heading_decisions(decisions: list[dict[str, Any]], final_inventory: dict[str, Any]) -> dict[str, Any]:
    status_counts = Counter(decision["status"] for decision in decisions)
    suspicious_remaining = sum(
        1
        for headings in final_inventory["headings"].values()
        for heading in headings
        if is_suspicious_heading(heading["text"])
    )
    return {
        "removed_count": status_counts.get("removed", 0),
        "recovered_count": status_counts.get("recovered", 0),
        "chapter_count": len(final_inventory["headings"]),
        "suspicious_heading_count": suspicious_remaining,
    }


def build_heading_report_payload(
    *,
    summary: dict[str, Any],
    decisions: list[dict[str, Any]],
    manual_review: list[Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "summary": summary,
        "decisions": decisions,
    }
    if manual_review is not None:
        payload["manual_review"] = dedupe_review_items(manual_review)
    return payload


def build_metadata_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes = []
    for field in ("title", "creator", "description", "language", "identifier", "modified"):
        before_value = before["primary"].get(field, "")
        after_value = after["primary"].get(field, "")
        if before_value != after_value:
            changes.append({"field": field, "before": before_value, "after": after_value})
    return {
        "before": before,
        "after": after,
        "changes": changes,
        "conflicts": [],
    }


def summarize_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": inventory["metadata"]["primary"].get("title", ""),
        "author": inventory["metadata"]["primary"].get("creator", ""),
        "language": inventory["metadata"]["primary"].get("language", ""),
        "spine_count": len(inventory.get("spine_files", [])),
        "toc_count": len(inventory.get("toc", {}).get("entries", [])),
        "heading_count": sum(len(entries) for entries in inventory.get("headings", {}).values()),
        "duplicate_id_files": len(inventory.get("structural_integrity", {}).get("duplicate_ids", [])),
        "broken_ref_count": len(inventory.get("structural_integrity", {}).get("broken_references", [])),
    }


def build_recovery_metadata_payload(
    *,
    metadata_diff: dict[str, Any],
    original_metadata: dict[str, Any],
    final_metadata: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "before": metadata_diff.get("before", original_metadata),
        "after": metadata_diff.get("after", final_metadata),
        "changes": metadata_diff.get("changes", []),
        "conflicts": metadata_diff.get("conflicts", []),
        "gate": gate,
    }


def build_recovery_toc_payload(*, toc_report: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    return {
        **toc_report,
        "gate": gate,
    }


def build_recovery_structural_payload(*, structural_report: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    return {
        **structural_report,
        "gate": gate,
    }


def build_epubcheck_payload(
    *,
    final_epubcheck: dict[str, Any],
    metadata_phase_epubcheck: dict[str, Any],
) -> dict[str, Any]:
    return {
        **final_epubcheck,
        "metadata_phase": metadata_phase_epubcheck,
    }


def build_recovery_release_summary(
    *,
    source_epub: str | Path,
    final_epub: str | Path,
    recommendation: str,
    gates: dict[str, dict[str, Any]],
    original_inventory: dict[str, Any],
    final_inventory: dict[str, Any],
    baseline_epubcheck_status: str,
    manual_review_count: int,
) -> dict[str, Any]:
    return {
        "source_epub": str(source_epub),
        "final_epub": str(Path(final_epub).resolve()),
        "recommendation": recommendation,
        "gates": gates,
        "baseline": summarize_inventory(original_inventory),
        "final": summarize_inventory(final_inventory),
        "baseline_epubcheck_status": baseline_epubcheck_status,
        "manual_review_count": manual_review_count,
        "reader_smoke": {"status": "not_run", "reason": "No reader engines available in CLI pipeline."},
    }


def build_release_pipeline_metadata_payload(
    *,
    baseline_inventory: dict[str, Any],
    final_inventory: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "before": {
            "title": baseline_inventory["metadata"]["primary"].get("title", ""),
            "creator": baseline_inventory["metadata"]["primary"].get("creator", ""),
            "language": baseline_inventory["metadata"]["primary"].get("language", ""),
            "description": baseline_inventory["metadata"]["primary"].get("description", ""),
        },
        "after": {
            "title": final_inventory["metadata"]["primary"].get("title", ""),
            "creator": final_inventory["metadata"]["primary"].get("creator", ""),
            "language": final_inventory["metadata"]["primary"].get("language", ""),
            "description": final_inventory["metadata"]["primary"].get("description", ""),
        },
        "changes": [],
    }
    if payload["before"] != payload["after"]:
        for key in ("title", "creator", "language", "description"):
            if payload["before"].get(key) != payload["after"].get(key):
                payload["changes"].append(
                    {
                        "field": key,
                        "before": payload["before"].get(key, ""),
                        "after": payload["after"].get(key, ""),
                    }
                )
    return payload


def build_release_pipeline_toc_payload(*, final_inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": {
            "toc_nav_count": final_inventory.get("toc", {}).get("toc_nav_count", 0),
            "entry_count": len(final_inventory.get("toc", {}).get("entries", [])),
            "warning_count": len(final_inventory.get("toc", {}).get("warnings", [])),
        },
        "entries": final_inventory.get("toc", {}).get("entries", []),
        "warnings": final_inventory.get("toc", {}).get("warnings", []),
    }


def build_release_pipeline_decision(*, epubcheck_payload: dict[str, Any], manual_review_count: int = 0) -> str:
    if (epubcheck_payload.get("status") or "").strip().lower() == "failed":
        return "fail"
    if manual_review_count > 0:
        return "pass_with_review"
    return "pass"


def count_broken_href_errors(errors: list[str]) -> int:
    count = 0
    for error in errors:
        lowered = error.lower()
        if "fragment" in lowered or "could not be found" in lowered or "missing" in lowered or "href" in lowered:
            count += 1
    return count


def _numeric_signal_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in sorted(set(before) | set(after)):
        if isinstance(before.get(key, 0), int) and isinstance(after.get(key, 0), int):
            keys.append(key)
    return keys
