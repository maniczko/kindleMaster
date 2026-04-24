from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_runtime_services import (
    ConversionRequest,
    build_conversion_summary,
    run_document_conversion,
)
from converter import convert_document_to_epub_with_report
from epub_quality_recovery import run_epub_publishing_quality_recovery
from epub_validation import build_validation_markdown, validate_epub_bytes, validate_epub_path
from kindle_semantic_cleanup import _is_placeholder_author, _resolve_publication_language
from quality_report_markdown import (
    build_regression_markdown,
    build_smoke_markdown,
    build_workflow_baseline_markdown,
    build_workflow_before_after_markdown,
    build_workflow_verification_markdown,
)
from quality_reporting import (
    build_before_after_report,
    collect_workflow_symptoms,
    derive_workflow_snapshot_status,
    extract_workflow_quality_signals,
    merge_statuses,
    normalize_status,
)
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


def _workflow_heading_repair_passthrough(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError("Workflow heading repair passthrough should not run when heading_repair_enabled is false.")


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
        workflow_quality_report: dict[str, Any] | None = None
        conversion_summary: dict[str, Any] | None = None
        audit_expectations: dict[str, Any] = {}
        if input_type in {"pdf", "docx"}:
            requested_language = "pl"
            conversion_outcome = run_document_conversion(
                ConversionRequest(
                    source_path=str(input_path),
                    source_type=input_type,
                    original_filename=input_path.name,
                    profile="auto-premium",
                    language=requested_language,
                    heading_repair_enabled=False,
                ),
                convert_impl=convert_document_to_epub_with_report,
                heading_repair_impl=_workflow_heading_repair_passthrough,
            )
            epub_bytes = conversion_outcome.epub_bytes
            epub_output_path = output_dir / ("before.epub" if phase == "baseline" else "after.epub")
            epub_output_path.write_bytes(epub_bytes)
            output_size_bytes = epub_output_path.stat().st_size
            conversion_summary = build_conversion_summary(
                conversion_outcome,
                filename=input_path.name,
                output_size_bytes=output_size_bytes,
                job_status="ready",
                message="EPUB gotowy do pobrania.",
            )
            conversion_result = conversion_summary
            workflow_quality_report = conversion_outcome.result.get("quality_report")
            audit_expectations = _build_workflow_audit_expectations(
                conversion_summary,
                requested_language=requested_language,
            )
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
            expected_title=str(audit_expectations.get("expected_title", "") or ""),
            expected_author=str(audit_expectations.get("expected_author", "") or ""),
            expected_language=str(audit_expectations.get("expected_language", "") or ""),
            publication_profile=audit_expectations.get("publication_profile") or None,
        )
        _write_json(audit_result_path, audit)

        snapshot["validation"] = validation
        snapshot["audit"] = audit
        if conversion_summary is not None:
            snapshot["conversion"] = conversion_summary
            snapshot["audit_expectations"] = audit_expectations
        snapshot["signals"] = _extract_quality_signals(
            validation=validation,
            audit=audit,
            quality_report=workflow_quality_report,
        )
        snapshot["symptoms"] = _collect_symptoms(
            validation=validation,
            audit=audit,
            quality_report=workflow_quality_report,
        )
        snapshot["status"] = _derive_snapshot_status(
            validation=validation,
            audit=audit,
            quality_report=workflow_quality_report,
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


def _build_workflow_audit_expectations(
    conversion_summary: dict[str, Any] | None,
    *,
    requested_language: str = "",
) -> dict[str, Any]:
    payload = conversion_summary if isinstance(conversion_summary, dict) else {}
    document_summary = payload.get("document_summary")
    document_summary = document_summary if isinstance(document_summary, dict) else {}
    document = payload.get("document")
    document = document if isinstance(document, dict) else {}
    analysis = payload.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    quality_state = payload.get("quality_state")
    quality_state = quality_state if isinstance(quality_state, dict) else {}
    quality_state_summary = quality_state.get("summary")
    quality_state_summary = quality_state_summary if isinstance(quality_state_summary, dict) else {}

    expected_title = _first_workflow_text(
        document_summary.get("title"),
        document.get("title"),
    )
    raw_expected_author = _first_workflow_text(
        document_summary.get("author"),
        document.get("author"),
    )
    expected_author = ""
    if raw_expected_author and not _is_placeholder_author(raw_expected_author):
        expected_author = raw_expected_author

    resolved_language = _resolve_publication_language(
        _first_workflow_text(
            document_summary.get("language"),
            document.get("language"),
            requested_language,
            default="pl",
        ),
        samples=[expected_title] if expected_title else [],
    )
    publication_profile = _first_workflow_text(
        document_summary.get("profile"),
        quality_state_summary.get("profile"),
        analysis.get("profile"),
        document.get("profile"),
    )
    return {
        "expected_title": expected_title,
        "expected_author": expected_author,
        "expected_language": resolved_language,
        "publication_profile": publication_profile,
    }


def _first_workflow_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return default


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
    return build_before_after_report(
        baseline_payload=baseline_payload,
        verification_snapshot=verification_snapshot,
        regression=regression,
        smoke=smoke,
    )


def _derive_snapshot_status(
    *,
    validation: dict[str, Any],
    audit: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> str:
    return derive_workflow_snapshot_status(
        validation=validation,
        audit=audit,
        quality_report=quality_report,
    )


def _normalize_status(raw_status: str) -> str:
    return normalize_status(raw_status)


def _merge_statuses(statuses: list[str]) -> str:
    return merge_statuses(statuses)


def _extract_quality_signals(
    *,
    validation: dict[str, Any],
    audit: dict[str, Any],
    quality_report: dict[str, Any] | None,
) -> dict[str, Any]:
    return extract_workflow_quality_signals(
        validation=validation,
        audit=audit,
        quality_report=quality_report,
    )


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
    return collect_workflow_symptoms(
        validation=validation,
        audit=audit,
        quality_report=quality_report,
    )


def _build_baseline_markdown(payload: dict[str, Any], isolation: dict[str, Any]) -> str:
    return build_workflow_baseline_markdown(payload, isolation)


def _build_verification_markdown(payload: dict[str, Any]) -> str:
    return build_workflow_verification_markdown(payload)


def _build_before_after_markdown(payload: dict[str, Any]) -> str:
    return build_workflow_before_after_markdown(payload)


def _build_regression_markdown(payload: dict[str, Any]) -> str:
    return build_regression_markdown(payload)


def _build_smoke_markdown(payload: dict[str, Any]) -> str:
    return build_smoke_markdown(payload)


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
