from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from converter import ConversionConfig, convert_document_to_epub_with_report
from epub_quality_recovery import run_epub_publishing_quality_recovery
from epub_validation import build_validation_markdown, validate_epub_bytes, validate_epub_path
from scripts.run_smoke_tests import run_smoke_tests


CHANGE_AREAS = (
    "app",
    "converter",
    "reference",
    "heading",
    "text",
    "semantic",
    "pipeline",
    "corpus",
)
_AREA_TO_TEST_SECTION = {
    "app": "A",
    "converter": "B",
    "reference": "C",
    "heading": "D",
    "text": "E",
    "semantic": "F",
    "pipeline": "G",
    "corpus": "H",
}
_SAME_INPUT_SMOKE_MARKER = {
    "pdf": "ocr_probe_pdf",
    "docx": "simple_report_docx",
    "epub": "scan_probe_epub",
}
_OWNER_LAYERS = {
    "app": ["app.py", "converter.py", "publication_pipeline.py"],
    "converter": ["converter.py", "publication_pipeline.py", "kindle_semantic_cleanup.py"],
    "reference": ["epub_reference_repair.py", "kindle_semantic_cleanup.py", "converter.py"],
    "heading": ["epub_heading_repair.py", "kindle_semantic_cleanup.py", "epub_quality_recovery.py"],
    "text": ["text_cleanup_engine.py", "text_normalization.py", "converter.py"],
    "semantic": ["kindle_semantic_cleanup.py", "converter.py", "publication_pipeline.py"],
    "pipeline": ["publication_analysis.py", "publication_pipeline.py", "premium_reflow.py"],
    "corpus": ["premium_corpus_smoke.py", "scripts/run_smoke_tests.py", "reference_inputs/manifest.json"],
}
_PROTECTED_INVARIANTS = {
    "app": [
        "convert endpoint keeps a valid EPUB or clear JSON error contract",
        "response headers remain browser-compatible",
        "localhost freshness checks remain mandatory after restart-sensitive changes",
    ],
    "converter": [
        "PDF to EPUB runtime stays integrated with finalize/text/reference/heading cleanup",
        "output EPUB remains structurally valid",
        "quality_report remains machine-readable",
    ],
    "reference": [
        "no invented URLs at low confidence",
        "no visible technical junk in final bibliography",
        "manifest/spine/nav/IDs remain intact",
    ],
    "heading": [
        "TOC points only to real sections",
        "captions and layout debris never enter TOC",
        "anchors remain stable and unique",
    ],
    "text": [
        "text cleanup does not damage URLs, anchors, IDs, or metadata",
        "no blind rewrite of body semantics",
        "PL/EN domain terms remain protected",
    ],
    "semantic": [
        "manifest, spine, nav, anchors, IDs, and XHTML validity stay intact",
        "shared cleanup helpers do not regress unrelated publication classes",
        "metadata and language synchronization remain coherent",
    ],
    "pipeline": [
        "publication routing remains generic across classes",
        "analysis and pipeline outputs stay auditable",
        "release artifacts remain machine-readable",
    ],
    "corpus": [
        "reference corpus remains class-based, not publication-specific",
        "smoke outputs stay reproducible",
        "cross-class regressions are visible before release claims",
    ],
}


def run_workflow_baseline(
    input_path: str | Path,
    *,
    change_area: str,
    reports_root: str | Path = "reports/workflows",
    output_root: str | Path = "output/workflows",
) -> dict[str, Any]:
    if change_area not in CHANGE_AREAS:
        raise ValueError(f"Unsupported change_area: {change_area}")

    resolved_input = Path(input_path).resolve()
    if not resolved_input.exists():
        raise FileNotFoundError(resolved_input)

    input_type = _detect_input_type(resolved_input)
    run_id = _generate_run_id(resolved_input, change_area)
    reports_dir = Path(reports_root).resolve() / run_id
    output_dir = Path(output_root).resolve() / run_id
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    snapshot = _capture_snapshot(
        resolved_input,
        input_type=input_type,
        phase="baseline",
        reports_dir=reports_dir,
        output_dir=output_dir,
    )
    recommended_tests = _load_targeted_tests_from_agents(change_area)
    recommended_smoke = _build_smoke_plan(change_area=change_area, input_type=input_type)

    isolation = {
        "run_id": run_id,
        "input_path": str(resolved_input),
        "input_type": input_type,
        "change_area": change_area,
        "suspected_owner_layers": _OWNER_LAYERS[change_area],
        "protected_invariants": _PROTECTED_INVARIANTS[change_area],
        "recommended_tests": recommended_tests,
        "recommended_smoke": recommended_smoke,
        "baseline_status": snapshot["status"],
        "baseline_symptoms": snapshot.get("symptoms", []),
    }

    payload = {
        "run_id": run_id,
        "mode": "baseline",
        "input_path": str(resolved_input),
        "input_type": input_type,
        "change_area": change_area,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reports_dir": str(reports_dir),
        "output_dir": str(output_dir),
        "snapshot": snapshot,
        "artifacts": {
            "baseline_json": str(reports_dir / "baseline.json"),
            "baseline_md": str(reports_dir / "baseline.md"),
            "isolation_json": str(reports_dir / "isolation.json"),
        },
    }

    _write_json(reports_dir / "isolation.json", isolation)
    _write_json(reports_dir / "baseline.json", payload)
    (reports_dir / "baseline.md").write_text(_build_baseline_markdown(payload, isolation), encoding="utf-8")
    return payload


def run_workflow_verify(
    input_path: str | Path,
    *,
    run_id: str,
    reports_root: str | Path = "reports/workflows",
    output_root: str | Path = "output/workflows",
) -> dict[str, Any]:
    resolved_input = Path(input_path).resolve()
    reports_dir = Path(reports_root).resolve() / run_id
    output_dir = Path(output_root).resolve() / run_id
    baseline_path = reports_dir / "baseline.json"
    isolation_path = reports_dir / "isolation.json"

    if not baseline_path.exists() or not isolation_path.exists():
        return {
            "run_id": run_id,
            "mode": "verify",
            "status": "failed",
            "error": "Baseline artifacts not found. Run workflow baseline first.",
        }

    baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    isolation = json.loads(isolation_path.read_text(encoding="utf-8"))
    baseline_input = Path(baseline_payload["input_path"]).resolve()
    if resolved_input != baseline_input:
        return {
            "run_id": run_id,
            "mode": "verify",
            "status": "failed",
            "error": f"Input mismatch. Baseline used {baseline_input}, verify received {resolved_input}.",
        }

    input_type = baseline_payload["input_type"]
    change_area = baseline_payload["change_area"]
    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    verification_snapshot = _capture_snapshot(
        resolved_input,
        input_type=input_type,
        phase="verify",
        reports_dir=reports_dir,
        output_dir=output_dir,
    )

    regression = _run_regression_pack(
        tests=isolation.get("recommended_tests", []),
        reports_dir=reports_dir,
    )
    smoke = _execute_smoke_plan(
        smoke_plan=isolation.get("recommended_smoke", []),
        reports_dir=reports_dir,
        output_dir=output_dir,
    )
    before_after = _build_before_after_report(
        baseline_payload=baseline_payload,
        verification_snapshot=verification_snapshot,
        regression=regression,
        smoke=smoke,
    )

    final_status = _merge_statuses(
        [
            verification_snapshot.get("status", "failed"),
            regression.get("status", "failed"),
            smoke.get("status", "failed"),
            before_after.get("status", "failed"),
        ]
    )
    if not before_after.get("report_complete", False):
        final_status = "failed"

    payload = {
        "run_id": run_id,
        "mode": "verify",
        "input_path": str(resolved_input),
        "input_type": input_type,
        "change_area": change_area,
        "status": final_status,
        "baseline_status": baseline_payload.get("snapshot", {}).get("status", "failed"),
        "verification_snapshot": verification_snapshot,
        "regression_pack": regression,
        "smoke_pack": smoke,
        "before_after": before_after,
        "artifacts": {
            "verification_json": str(reports_dir / "verification.json"),
            "verification_md": str(reports_dir / "verification.md"),
            "before_after_json": str(reports_dir / "before_after.json"),
            "before_after_md": str(reports_dir / "before_after.md"),
        },
    }

    _write_json(reports_dir / "before_after.json", before_after)
    _write_json(reports_dir / "verification.json", payload)
    (reports_dir / "before_after.md").write_text(_build_before_after_markdown(before_after), encoding="utf-8")
    (reports_dir / "verification.md").write_text(_build_verification_markdown(payload), encoding="utf-8")
    return payload


def _capture_snapshot(
    input_path: Path,
    *,
    input_type: str,
    phase: str,
    reports_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    validation_path = reports_dir / f"{phase}_validation.json"
    validation_md_path = reports_dir / f"{phase}_validation.md"
    audit_result_path = reports_dir / f"{phase}_audit_result.json"
    conversion_result_path = reports_dir / f"{phase}_conversion.json"

    snapshot: dict[str, Any] = {
        "phase": phase,
        "input_path": str(input_path),
        "input_type": input_type,
        "status": "failed",
        "symptoms": [],
        "artifacts": {},
    }

    try:
        conversion_result: dict[str, Any] | None = None
        conversion_summary: dict[str, Any] | None = None
        if input_type in {"pdf", "docx"}:
            conversion_result = convert_document_to_epub_with_report(
                str(input_path),
                config=ConversionConfig(profile="auto-premium"),
                original_filename=input_path.name,
                source_type=input_type,
            )
            epub_bytes = conversion_result["epub_bytes"]
            conversion_summary = {
                key: value
                for key, value in conversion_result.items()
                if key != "epub_bytes"
            }
            epub_output_path = output_dir / ("before.epub" if phase == "baseline" else "after.epub")
            epub_output_path.write_bytes(epub_bytes)
            validation = validate_epub_bytes(epub_bytes, label=str(epub_output_path))
            _write_json(conversion_result_path, conversion_summary)
            snapshot["artifacts"]["epub_output"] = str(epub_output_path)
            snapshot["artifacts"]["conversion_json"] = str(conversion_result_path)
            audit_target = epub_output_path
        else:
            validation = validate_epub_path(input_path)
            audit_target = input_path

        _write_json(validation_path, validation)
        validation_md_path.write_text(build_validation_markdown(validation), encoding="utf-8")

        audit_reports_dir = reports_dir / f"{phase}_audit"
        audit_output_dir = output_dir / f"{phase}_audit"
        audit = run_epub_publishing_quality_recovery(
            audit_target,
            output_dir=audit_output_dir,
            reports_dir=audit_reports_dir,
        )
        _write_json(audit_result_path, audit)

        snapshot["validation"] = validation
        snapshot["audit"] = audit
        if conversion_summary is not None:
            snapshot["conversion"] = conversion_summary
        snapshot["signals"] = _extract_quality_signals(
            validation=validation,
            audit=audit,
            quality_report=(conversion_result or {}).get("quality_report"),
        )
        snapshot["symptoms"] = _collect_symptoms(
            validation=validation,
            audit=audit,
            quality_report=(conversion_result or {}).get("quality_report"),
        )
        snapshot["status"] = _derive_snapshot_status(
            validation=validation,
            audit=audit,
            quality_report=(conversion_result or {}).get("quality_report"),
        )
        snapshot["artifacts"].update(
            {
                "validation_json": str(validation_path),
                "validation_md": str(validation_md_path),
                "audit_result_json": str(audit_result_path),
                "audit_reports_dir": str(audit_reports_dir),
                "audit_output_dir": str(audit_output_dir),
            }
        )
        return snapshot
    except Exception as exc:  # pragma: no cover - defensive wrapper
        snapshot["error"] = str(exc)
        snapshot["symptoms"] = [f"snapshot failed: {exc}"]
        return snapshot


def _detect_input_type(input_path: Path) -> str:
    suffix = input_path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".epub":
        return "epub"
    raise ValueError(f"Unsupported input type: {input_path.suffix}")


def _generate_run_id(input_path: Path, change_area: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", input_path.stem.lower()).strip("-") or "input"
    return f"{timestamp}-{change_area}-{slug[:40]}"


def _load_targeted_tests_from_agents(change_area: str) -> list[str]:
    agents_path = Path("AGENTS.md").resolve()
    text = agents_path.read_text(encoding="utf-8")
    section_letter = _AREA_TO_TEST_SECTION[change_area]
    sections = re.split(r"(?m)^### ([A-H])\. ", text)
    content_by_section: dict[str, str] = {}
    for index in range(1, len(sections), 2):
        content_by_section[sections[index]] = sections[index + 1]
    content = content_by_section.get(section_letter, "")
    if not content:
        raise RuntimeError(f"Unable to find test section {section_letter} in AGENTS.md")

    tests: list[str] = []
    for block in re.findall(r"```powershell\s+(.*?)```", content, flags=re.S):
        for line in block.splitlines():
            line = line.strip()
            prefix = "python -m unittest "
            if not line.startswith(prefix):
                continue
            tests.extend(token for token in line[len(prefix) :].split() if token.endswith(".py"))
    deduped = list(dict.fromkeys(tests))
    if not deduped:
        raise RuntimeError(f"No unittest commands found for change_area={change_area}")
    return deduped


def _build_smoke_plan(*, change_area: str, input_type: str) -> list[dict[str, Any]]:
    plan = [
        {
            "type": "same_input_rerun",
            "description": "The verify step must re-run the exact same input before broader checks.",
        }
    ]
    if change_area == "corpus":
        plan.append(
            {
                "type": "curated_smoke",
                "mode": "full",
                "case_filters": [],
                "description": "Run the corpus/full smoke path for corpus changes.",
            }
        )
        return plan

    if change_area in {"app", "converter", "semantic", "pipeline"}:
        plan.append(
            {
                "type": "curated_smoke",
                "mode": "quick",
                "case_filters": [],
                "description": "Shared/runtime changes require the full quick smoke pack.",
            }
        )
        return plan

    plan.append(
        {
            "type": "curated_smoke",
            "mode": "quick",
            "case_filters": [_SAME_INPUT_SMOKE_MARKER[input_type]],
            "description": "Run one quick curated smoke matching the input type after the same-input rerun.",
        }
    )
    return plan


def _run_regression_pack(*, tests: list[str], reports_dir: Path) -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest", *tests]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    payload = {
        "status": "passed" if completed.returncode == 0 else "failed",
        "command": command,
        "tests": tests,
        "returncode": completed.returncode,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(completed.stderr.splitlines()[-40:]),
    }
    _write_json(reports_dir / "regression_pack.json", payload)
    (reports_dir / "regression_pack.md").write_text(_build_regression_markdown(payload), encoding="utf-8")
    return payload


def _execute_smoke_plan(*, smoke_plan: list[dict[str, Any]], reports_dir: Path, output_dir: Path) -> dict[str, Any]:
    executed: list[dict[str, Any]] = []
    statuses: list[str] = []
    for index, item in enumerate(smoke_plan):
        if item.get("type") != "curated_smoke":
            continue
        smoke_reports_dir = reports_dir / f"smoke_{index}"
        smoke_output_dir = output_dir / f"smoke_{index}"
        payload = run_smoke_tests(
            mode=item.get("mode", "quick"),
            reports_dir=smoke_reports_dir,
            output_dir=smoke_output_dir,
            case_filters=item.get("case_filters") or [],
        )
        status = payload.get("summary", {}).get("overall_status", "failed")
        executed.append(
            {
                "mode": item.get("mode", "quick"),
                "case_filters": item.get("case_filters") or [],
                "status": status,
                "reports_dir": str(smoke_reports_dir),
                "output_dir": str(smoke_output_dir),
            }
        )
        statuses.append(status)

    status = _merge_statuses(statuses or ["passed"])
    payload = {
        "status": status,
        "executed": executed,
    }
    _write_json(reports_dir / "smoke_pack.json", payload)
    (reports_dir / "smoke_pack.md").write_text(_build_smoke_markdown(payload), encoding="utf-8")
    return payload


def _build_before_after_report(
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
    status = _merge_statuses(
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


def _derive_snapshot_status(
    *,
    validation: dict[str, Any],
    audit: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> str:
    statuses = [
        _normalize_status((validation.get("summary") or {}).get("status", "failed")),
        _normalize_status(audit.get("decision", "failed")),
    ]
    if quality_report:
        statuses.append(_normalize_status(quality_report.get("validation_status", "passed")))
        warnings = quality_report.get("warnings") or []
        if warnings and statuses[-1] == "passed":
            statuses[-1] = "passed_with_warnings"
    return _merge_statuses(statuses)


def _normalize_status(raw_status: str) -> str:
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


def _merge_statuses(statuses: list[str]) -> str:
    normalized = [_normalize_status(status) for status in statuses]
    if any(status == "failed" for status in normalized):
        return "failed"
    if any(status == "passed_with_warnings" for status in normalized):
        return "passed_with_warnings"
    return "passed"


def _extract_quality_signals(
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
        "validation_status": _normalize_status((validation.get("summary") or {}).get("status", "failed")),
        "epubcheck_status": _normalize_status((validation.get("epubcheck") or {}).get("status", "failed")),
        "error_count": int((validation.get("summary") or {}).get("error_count", 0)),
        "warning_count": int((validation.get("summary") or {}).get("warning_count", 0)),
        "internal_link_error_count": len(internal_errors),
        "external_link_error_count": len(external_errors),
        "broken_href_error_count": _count_broken_href_errors(internal_errors + external_errors + package_errors),
        "duplicate_id_error_count": sum(1 for error in internal_errors if "duplicate id" in error.lower()),
        "reference_cleanup_status": _normalize_status(reference_cleanup.get("quality_gate_status", "unavailable")) if reference_cleanup else "passed_with_warnings",
        "reference_visible_junk_detected": int(reference_cleanup.get("visible_junk_detected", 0)) if reference_cleanup else 0,
        "heading_gate_status": _normalize_status(((gates.get("C") or {}).get("status", "unavailable"))),
        "toc_gate_status": _normalize_status(((gates.get("D") or {}).get("status", "unavailable"))),
        "text_cleanup_review_needed_count": int(text_cleanup.get("review_needed_count", 0)) if text_cleanup else 0,
        "text_cleanup_blocked_count": int(text_cleanup.get("blocked_count", 0)) if text_cleanup else 0,
    }


def _count_broken_href_errors(errors: list[str]) -> int:
    count = 0
    for error in errors:
        lowered = error.lower()
        if "fragment" in lowered or "could not be found" in lowered or "missing" in lowered or "href" in lowered:
            count += 1
    return count


def _collect_symptoms(
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
        warnings = quality_report.get("warnings") or []
        symptoms.extend(warnings[:3])
        text_cleanup = quality_report.get("text_cleanup") or {}
        if text_cleanup.get("review_needed_count"):
            symptoms.append(f"text cleanup review_needed={text_cleanup['review_needed_count']}")
        reference_cleanup = text_cleanup.get("reference_cleanup") or {}
        if reference_cleanup.get("visible_junk_detected"):
            symptoms.append(f"reference visible_junk_detected={reference_cleanup['visible_junk_detected']}")
    return list(dict.fromkeys(symptoms))


def _numeric_signal_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key in sorted(set(before) | set(after)):
        if isinstance(before.get(key, 0), int) and isinstance(after.get(key, 0), int):
            keys.append(key)
    return keys


def _build_baseline_markdown(payload: dict[str, Any], isolation: dict[str, Any]) -> str:
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


def _build_verification_markdown(payload: dict[str, Any]) -> str:
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


def _build_before_after_markdown(payload: dict[str, Any]) -> str:
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


def _build_regression_markdown(payload: dict[str, Any]) -> str:
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


def _build_smoke_markdown(payload: dict[str, Any]) -> str:
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_ready(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_ready(value.to_dict())
    return str(value)
