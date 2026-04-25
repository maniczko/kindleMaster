from __future__ import annotations

from typing import Any

from quality_reporting import build_manual_review_queue_payload


def build_manual_review_markdown(payload_or_items: dict[str, Any] | list[Any] | tuple[Any, ...]) -> str:
    if isinstance(payload_or_items, dict) and isinstance(payload_or_items.get("items"), list):
        payload = payload_or_items
    else:
        payload = build_manual_review_queue_payload(payload_or_items)

    items = payload.get("items", [])
    if not items:
        return "# Manual Review Queue\n\n- None\n"
    lines = ["# Manual Review Queue", ""]
    for item in items:
        if isinstance(item, dict):
            lines.append(
                f"- [{item.get('kind')}] {item.get('file')}: {item.get('subject')} ({item.get('reason')}, confidence {item.get('confidence')})"
            )
        else:
            lines.append(f"- {item}")
    return "\n".join(lines).strip() + "\n"


def build_workflow_quality_report_markdown(payload: dict[str, Any]) -> str:
    verdict = payload.get("verdict", {}) or {}
    raw_signals = payload.get("raw_signals", {}) or {}
    symptoms = payload.get("symptoms", []) or []
    lines = [
        "# Workflow Quality Report",
        "",
        f"- Verdict: `{verdict.get('status', 'unknown')}`",
        f"- Severity: `{verdict.get('severity', 'unknown')}`",
        f"- Blockers: `{verdict.get('blocker_count', 0)}`",
        f"- Warnings: `{verdict.get('warning_count', 0)}`",
        f"- Review items: `{verdict.get('review_count', 0)}`",
        "",
        "## Raw Signals",
        "",
    ]
    for key in sorted(raw_signals):
        lines.append(f"- `{key}`: `{raw_signals[key]}`")
    if verdict.get("reasons"):
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in verdict["reasons"])
    if symptoms:
        lines.extend(["", "## Symptoms", ""])
        lines.extend(f"- {symptom}" for symptom in symptoms)
    return "\n".join(lines).rstrip() + "\n"


def build_recovery_release_report_markdown(
    *,
    release_summary: dict[str, Any],
    metadata_payload: dict[str, Any],
    toc_payload: dict[str, Any],
) -> str:
    lines = [
        "# EPUB Publishing Quality Recovery",
        "",
        f"- Recommendation: {release_summary['recommendation']}",
        f"- Source EPUB: {release_summary['source_epub']}",
        f"- Final EPUB: {release_summary['final_epub']}",
        "",
        "## Gates",
    ]
    for gate_name in ("A", "B", "C", "D", "E", "F"):
        gate = release_summary["gates"].get(gate_name, {})
        lines.append(f"- Gate {gate_name}: {gate.get('status', 'unknown')} - {gate.get('summary', '')}")
    lines.extend(
        [
            "",
            "## Metadata",
            f"- Title: {metadata_payload['after']['primary'].get('title', '')}",
            f"- Author: {metadata_payload['after']['primary'].get('creator', '')}",
            f"- Language: {metadata_payload['after']['primary'].get('language', '')}",
            f"- Metadata changes: {len(metadata_payload.get('changes', []))}",
            "",
            "## TOC",
            f"- Entries: {len(toc_payload.get('entries', []))}",
            f"- Warnings: {len(toc_payload.get('warnings', []))}",
            "",
            "## Baseline vs Final",
            f"- Baseline TOC entries: {release_summary['baseline']['toc_count']}",
            f"- Final TOC entries: {release_summary['final']['toc_count']}",
            f"- Baseline heading count: {release_summary['baseline']['heading_count']}",
            f"- Final heading count: {release_summary['final']['heading_count']}",
            "",
            "## Manual Review",
            f"- Queue size: {release_summary['manual_review_count']}",
            f"- Reader smoke: {release_summary['reader_smoke']['status']} ({release_summary['reader_smoke']['reason']})",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def build_release_pipeline_report_markdown(
    *,
    decision: str,
    source_epub: str,
    final_epub: str,
    epubcheck_payload: dict[str, Any],
    metadata_payload: dict[str, Any],
    toc_payload: dict[str, Any],
    heading_summary: dict[str, Any],
    manual_review_count: int,
) -> str:
    return (
        "# EPUB Release Pipeline\n\n"
        f"- Decision: {decision}\n"
        f"- Source EPUB: {source_epub}\n"
        f"- Final EPUB: {final_epub}\n"
        f"- EPUBCheck: {epubcheck_payload.get('status', 'unavailable')}\n"
        f"- Title: {metadata_payload['after'].get('title', '')}\n"
        f"- Author: {metadata_payload['after'].get('creator', '')}\n"
        f"- Language: {metadata_payload['after'].get('language', '')}\n"
        f"- TOC nav count: {toc_payload['summary'].get('toc_nav_count', 0)}\n"
        f"- Removed headings: {heading_summary.get('removed_count', 0)}\n"
        f"- Manual review count: {manual_review_count}\n"
    )


def build_workflow_baseline_markdown(payload: dict[str, Any], isolation: dict[str, Any]) -> str:
    snapshot = payload.get("snapshot", {})
    lines = [
        "# KindleMaster Workflow Baseline",
        "",
        f"- Run ID: `{payload['run_id']}`",
        f"- Input: `{payload['input_path']}`",
        f"- Input type: `{payload['input_type']}`",
        f"- Change area: `{payload['change_area']}`",
        f"- Baseline status: `{snapshot.get('status', 'failed')}`",
        "",
        "## Isolation",
        "",
        f"- Suspected owner layers: `{', '.join(isolation.get('suspected_owner_layers', []))}`",
        f"- Recommended tests: `{', '.join(isolation.get('recommended_tests', []))}`",
        "",
    ]
    for item in isolation.get("recommended_smoke", []):
        lines.append(f"- Smoke: `{item.get('type')}` :: {item.get('description', '')}")
    if snapshot.get("symptoms"):
        lines.extend(["", "## Symptoms", ""])
        lines.extend(f"- {symptom}" for symptom in snapshot["symptoms"])
    return "\n".join(lines).rstrip() + "\n"


def build_workflow_verification_markdown(payload: dict[str, Any]) -> str:
    verification = payload.get("verification_snapshot", {})
    before_after = payload.get("before_after", {})
    lines = [
        "# KindleMaster Workflow Verification",
        "",
        f"- Run ID: `{payload['run_id']}`",
        f"- Final status: `{payload.get('status', 'failed')}`",
        f"- Baseline status: `{payload.get('baseline_status', 'failed')}`",
        f"- Verify status: `{verification.get('status', 'failed')}`",
        f"- Regression pack: `{(payload.get('regression_pack') or {}).get('status', 'failed')}`",
        f"- Smoke pack: `{(payload.get('smoke_pack') or {}).get('status', 'failed')}`",
        "",
        "## Before vs After",
        "",
        f"- Before: `{(before_after.get('before') or {}).get('status', 'failed')}`",
        f"- After: `{(before_after.get('after') or {}).get('status', 'failed')}`",
        "",
    ]
    for key, value in (before_after.get("delta") or {}).items():
        lines.append(f"- Delta `{key}`: `{value}`")
    if before_after.get("remaining_risks"):
        lines.extend(["", "## Remaining Risks", ""])
        lines.extend(f"- {risk}" for risk in before_after["remaining_risks"])
    return "\n".join(lines).rstrip() + "\n"


def build_workflow_before_after_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# KindleMaster Before/After Comparison",
        "",
        f"- Run ID: `{payload.get('run_id', '')}`",
        f"- Status: `{payload.get('status', 'failed')}`",
        f"- Regression pack: `{payload.get('regression_pack_status', 'failed')}`",
        f"- Smoke: `{payload.get('smoke_status', 'failed')}`",
        "",
        "## Delta Metrics",
        "",
    ]
    for key, value in (payload.get("delta") or {}).items():
        lines.append(f"- `{key}`: `{value}`")
    if payload.get("remaining_risks"):
        lines.extend(["", "## Remaining Risks", ""])
        lines.extend(f"- {risk}" for risk in payload["remaining_risks"])
    return "\n".join(lines).rstrip() + "\n"


def build_regression_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# KindleMaster Regression Pack",
        "",
        f"- Status: `{payload.get('status', 'failed')}`",
        f"- Return code: `{payload.get('returncode', 1)}`",
        f"- Tests: `{', '.join(payload.get('tests', []))}`",
        "",
    ]
    if payload.get("stdout_tail"):
        lines.extend(["## stdout tail", "", "```text", payload["stdout_tail"], "```", ""])
    if payload.get("stderr_tail"):
        lines.extend(["## stderr tail", "", "```text", payload["stderr_tail"], "```", ""])
    return "\n".join(lines).rstrip() + "\n"


def build_smoke_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# KindleMaster Smoke Pack",
        "",
        f"- Status: `{payload.get('status', 'failed')}`",
        "",
    ]
    for item in payload.get("executed", []):
        lines.extend(
            [
                f"## {item.get('mode', 'quick')}",
                "",
                f"- Status: `{item.get('status', 'failed')}`",
                f"- Case filters: `{', '.join(item.get('case_filters', [])) or 'all'}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
