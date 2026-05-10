from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOP_LEVEL_STATUS_COMMANDS = {
    "doctor": "python kindlemaster.py doctor",
    "quick": "python kindlemaster.py test --suite quick",
    "corpus": "python kindlemaster.py test --suite corpus",
    "release": "python kindlemaster.py test --suite release",
    "ui_state_screenshots": "python kindlemaster.py test --suite runtime",
    "status": "python kindlemaster.py status",
}

EVIDENCE_FRESHNESS_MAX_AGE_HOURS = 168

WORKFLOW_BASELINE_REPORT_ARTIFACTS = (
    "baseline.json",
    "baseline.md",
    "isolation.json",
)
WORKFLOW_VERIFY_REPORT_ARTIFACTS = (
    "verification.json",
    "verification.md",
    "before_after.json",
    "before_after.md",
    "regression_pack.json",
    "regression_pack.md",
    "smoke_pack.json",
    "smoke_pack.md",
)
REQUIRED_WORKFLOW_REPORT_ARTIFACTS = WORKFLOW_BASELINE_REPORT_ARTIFACTS + WORKFLOW_VERIFY_REPORT_ARTIFACTS

STATUS_PRIORITY = {
    "unavailable": 0,
    "passed": 1,
    "passed_with_warnings": 2,
    "failed": 3,
}


def _normalize_status(value: Any, *, default: str = "unavailable") -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip().lower()
    if normalized == "supported":
        return "passed"
    if normalized == "degraded":
        return "passed_with_warnings"
    if normalized in {"pass", "passed"}:
        return "passed"
    if normalized in {"warning", "warnings", "pass_with_review", "passed_with_warnings"}:
        return "passed_with_warnings"
    if normalized in {"fail", "failed", "error", "unsupported"}:
        return "failed"
    if normalized in {"unavailable", "unknown", "skipped"}:
        return "unavailable"
    return default


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _pick_worse_status(*statuses: str) -> str:
    return max((_normalize_status(status) for status in statuses), key=lambda item: STATUS_PRIORITY.get(item, 0))


def _path_mtime_iso(path: Path) -> str:
    if not path.exists():
        return ""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _latest_path_mtime_iso(path: Path) -> str:
    if not path.exists():
        return ""
    candidates = [path]
    if path.is_dir():
        candidates.extend(item for item in path.rglob("*") if item.exists())
    latest_mtime = max(item.stat().st_mtime for item in candidates)
    return datetime.fromtimestamp(latest_mtime, timezone.utc).isoformat()


def _read_text(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _find_latest_existing_path(paths: list[Path]) -> Path | None:
    existing_paths = [path for path in paths if path.exists() and path.is_file()]
    if not existing_paths:
        return None
    return max(existing_paths, key=lambda path: path.stat().st_mtime)


def _extract_payload_status(payload: dict[str, Any], *, lane: str) -> str:
    if lane == "ui_state_screenshots":
        states = payload.get("states")
        if isinstance(states, list) and states:
            has_overflow = any(isinstance(row, dict) and row.get("horizontal_overflow") for row in states)
            return "failed" if has_overflow else "passed"
        return "unavailable"

    if lane == "doctor":
        doctor_statuses: list[str] = []
        surfaces = payload.get("verification_surfaces")
        if isinstance(surfaces, dict):
            doctor_statuses.extend(
                _normalize_status((surfaces.get(name) or {}).get("status"))
                for name in ("quick", "corpus", "release")
            )
        agent_readiness = payload.get("agent_readiness")
        if isinstance(agent_readiness, dict):
            doctor_statuses.append(_normalize_status(agent_readiness.get("status")))
        nested_payload = payload.get("payload")
        if isinstance(nested_payload, dict) and not doctor_statuses:
            nested_surfaces = nested_payload.get("verification_surfaces")
            if isinstance(nested_surfaces, dict):
                doctor_statuses.extend(
                    _normalize_status((nested_surfaces.get(name) or {}).get("status"))
                    for name in ("quick", "corpus", "release")
                )
            nested_readiness = nested_payload.get("agent_readiness")
            if isinstance(nested_readiness, dict):
                doctor_statuses.append(_normalize_status(nested_readiness.get("status")))
        if any(status != "unavailable" for status in doctor_statuses):
            return _pick_worse_status(*doctor_statuses)

    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key in ("overall_status", "status"):
            if key in summary:
                return _normalize_status(summary.get(key))

    for key in ("overall_status", "status", "release_status", "recommendation"):
        if key in payload:
            return _normalize_status(payload.get(key))
    return "unavailable"


def _build_evidence_row(
    *,
    lane: str,
    command: str,
    candidate_paths: list[Path],
    current_status: str | None = None,
    current_path: Path | None = None,
    current_generated_at: str = "",
) -> dict[str, Any]:
    if current_status is not None and current_path is not None:
        return {
            "lane": lane,
            "available": True,
            "status": _normalize_status(current_status),
            "command": command,
            "path": str(current_path),
            "updated_at": current_generated_at,
            "source": "current_status_run",
            "candidate_paths": [str(path) for path in candidate_paths],
        }

    evidence_path = _find_latest_existing_path(candidate_paths)
    if evidence_path is None:
        return {
            "lane": lane,
            "available": False,
            "status": "unavailable",
            "command": command,
            "path": str(candidate_paths[0]) if candidate_paths else "",
            "updated_at": "",
            "source": "missing_artifact",
            "candidate_paths": [str(path) for path in candidate_paths],
        }

    payload = _load_json(evidence_path)
    return {
        "lane": lane,
        "available": payload is not None,
        "status": _extract_payload_status(payload or {}, lane=lane),
        "command": command,
        "path": str(evidence_path),
        "updated_at": _path_mtime_iso(evidence_path),
        "source": "json_artifact" if payload is not None else "unreadable_json_artifact",
        "candidate_paths": [str(path) for path in candidate_paths],
    }


def _apply_evidence_freshness(
    evidence: dict[str, dict[str, Any]],
    *,
    generated_at: str,
    max_age_hours: int = EVIDENCE_FRESHNESS_MAX_AGE_HOURS,
) -> None:
    reference_time = _parse_iso_datetime(generated_at) or datetime.now(timezone.utc)
    for lane, row in evidence.items():
        updated_at = _parse_iso_datetime(row.get("updated_at"))
        if not row.get("available") or updated_at is None:
            row["freshness_status"] = "unavailable"
            row["age_hours"] = None
            row["max_age_hours"] = max_age_hours
            row["freshness_warning"] = "" if row.get("source") == "missing_artifact" else f"Evidence lane `{lane}` freshness could not be determined."
            continue

        age_hours = max(0.0, (reference_time - updated_at).total_seconds() / 3600)
        row["age_hours"] = round(age_hours, 1)
        row["max_age_hours"] = max_age_hours
        if age_hours > max_age_hours:
            row["freshness_status"] = "stale"
            row["freshness_warning"] = (
                f"Evidence lane `{lane}` is stale: `{row.get('path', '')}` was last updated at `{row.get('updated_at', '')}`."
            )
        else:
            row["freshness_status"] = "fresh"
            row["freshness_warning"] = ""


def _collect_evidence_freshness_warnings(dashboard: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for row in dashboard.get("evidence", {}).values():
        warning = str(row.get("freshness_warning", "")).strip()
        if warning:
            warnings.append(warning)
    return warnings


def _derive_evidence_dashboard_status(evidence: dict[str, dict[str, Any]]) -> str:
    statuses = [_normalize_status(row.get("status")) for row in evidence.values()]
    if any(status == "failed" for status in statuses):
        return "failed"
    freshness_statuses = [str(row.get("freshness_status", "")) for row in evidence.values()]
    if any(status == "passed_with_warnings" for status in statuses):
        return "passed_with_warnings"
    if any(status == "unavailable" for status in statuses):
        return "passed_with_warnings"
    if any(status in {"stale", "unknown"} for status in freshness_statuses):
        return "passed_with_warnings"
    return "passed"


def _build_governance_dashboard(
    *,
    reports_root: Path,
    output_json: Path,
    generated_at: str,
    overall_status: str,
) -> dict[str, Any]:
    governance_root = reports_root / "governance"
    evidence = {
        "doctor": _build_evidence_row(
            lane="doctor",
            command=TOP_LEVEL_STATUS_COMMANDS["doctor"],
            candidate_paths=[
                governance_root / "doctor.json",
                reports_root / "doctor.json",
                reports_root / "toolchain.json",
            ],
        ),
        "quick": _build_evidence_row(
            lane="quick",
            command=TOP_LEVEL_STATUS_COMMANDS["quick"],
            candidate_paths=[
                governance_root / "quick.json",
                reports_root / "quick.json",
                reports_root / "suites" / "quick.json",
            ],
        ),
        "corpus": _build_evidence_row(
            lane="corpus",
            command=TOP_LEVEL_STATUS_COMMANDS["corpus"],
            candidate_paths=[
                reports_root / "corpus" / "corpus_gate.json",
                governance_root / "corpus.json",
                reports_root / "corpus.json",
            ],
        ),
        "release": _build_evidence_row(
            lane="release",
            command=TOP_LEVEL_STATUS_COMMANDS["release"],
            candidate_paths=[
                governance_root / "release.json",
                reports_root / "release.json",
                reports_root / "suites" / "release.json",
            ],
        ),
        "ui_state_screenshots": _build_evidence_row(
            lane="ui_state_screenshots",
            command=TOP_LEVEL_STATUS_COMMANDS["ui_state_screenshots"],
            candidate_paths=[
                reports_root / "ui-state-screenshots" / "latest" / "manifest.json",
            ],
        ),
        "status": _build_evidence_row(
            lane="status",
            command=TOP_LEVEL_STATUS_COMMANDS["status"],
            candidate_paths=[output_json],
            current_status=overall_status,
            current_path=output_json,
            current_generated_at=generated_at,
        ),
    }
    _apply_evidence_freshness(evidence, generated_at=generated_at)
    return {
        "status": _derive_evidence_dashboard_status(evidence),
        "freshness_max_age_hours": EVIDENCE_FRESHNESS_MAX_AGE_HOURS,
        "evidence": evidence,
    }


def _extract_top_level_commands(kindlemaster_source: str) -> list[str]:
    top_level_source = kindlemaster_source.split("workflow_subparsers", 1)[0]
    commands = sorted(set(re.findall(r"add_parser\(\s*\"([^\"]+)\"", top_level_source)))
    return [command for command in commands if command not in {"baseline", "verify"}]


def _extract_test_suites(kindlemaster_source: str) -> list[str]:
    match = re.search(r"test_parser\.add_argument\(\s*\"--suite\".*?choices=\((.*?)\)", kindlemaster_source, flags=re.DOTALL)
    if not match:
        return []
    return sorted(set(re.findall(r"\"([^\"]+)\"", match.group(1))))


def _contains_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def _check_command_mirrors(*, kindlemaster_source: str, source_texts: dict[str, str | None]) -> dict[str, Any]:
    commands = _extract_top_level_commands(kindlemaster_source)
    mirrors = ["README.md", ".codex/config.toml", ".codex/README.md", "AGENTS.md"]
    missing_by_source: dict[str, list[str]] = {}
    unavailable_sources: list[str] = []
    for source in mirrors:
        text = source_texts.get(source)
        if text is None:
            unavailable_sources.append(source)
            continue
        missing_commands = [command for command in commands if not _contains_token(text, command)]
        if missing_commands:
            missing_by_source[source] = missing_commands

    status = "failed" if missing_by_source else "passed"
    if unavailable_sources and not missing_by_source:
        status = "unavailable"
    return {
        "id": "first_class_command_mirrors",
        "status": status,
        "authoritative_source": "kindlemaster.py",
        "mirror_sources": mirrors,
        "commands": commands,
        "missing_by_source": missing_by_source,
        "unavailable_sources": unavailable_sources,
    }


def _check_toolchain_suite_mirrors(*, kindlemaster_source: str, source_texts: dict[str, str | None]) -> dict[str, Any]:
    suite_choices = _extract_test_suites(kindlemaster_source)
    expected_suites = [suite for suite in suite_choices if suite in {"quick", "corpus", "release", "browser", "runtime"}]
    mirrors = ["README.md", ".codex/config.toml", ".codex/README.md", "docs/toolchain-matrix.md"]
    missing_by_source: dict[str, list[str]] = {}
    unavailable_sources: list[str] = []
    expected_commands = [f"python kindlemaster.py test --suite {suite}" for suite in expected_suites]
    for source in mirrors:
        text = source_texts.get(source)
        if text is None:
            unavailable_sources.append(source)
            continue
        missing_commands = [command for command in expected_commands if command not in text]
        if missing_commands:
            missing_by_source[source] = missing_commands

    status = "failed" if missing_by_source else "passed"
    if unavailable_sources and not missing_by_source:
        status = "unavailable"
    return {
        "id": "toolchain_suite_command_mirrors",
        "status": status,
        "authoritative_source": "kindlemaster.py --suite choices",
        "mirror_sources": mirrors,
        "suite_choices": suite_choices,
        "checked_suites": expected_suites,
        "missing_by_source": missing_by_source,
        "unavailable_sources": unavailable_sources,
    }


def _check_status_evidence_contract(source_texts: dict[str, str | None]) -> dict[str, Any]:
    expected_by_source = {
        "README.md": [
            "python kindlemaster.py status",
            "reports/project_status.json",
            "reports/project_status.md",
        ],
        ".codex/config.toml": [
            "python kindlemaster.py status",
            "generated output/ and reports/ artifacts are derived evidence",
        ],
        ".codex/README.md": [
            "python kindlemaster.py status",
            "Generated files under `reports/` and `output/` are derived runtime artifacts",
        ],
        "AGENTS.md": [
            "python kindlemaster.py status",
            "reports/project_status.json",
            "reports/project_status.md",
        ],
    }
    missing_by_source: dict[str, list[str]] = {}
    unavailable_sources: list[str] = []
    for source, expected_markers in expected_by_source.items():
        text = source_texts.get(source)
        if text is None:
            unavailable_sources.append(source)
            continue
        missing_markers = [marker for marker in expected_markers if marker not in text]
        if missing_markers:
            missing_by_source[source] = missing_markers

    status = "failed" if missing_by_source else "passed"
    if unavailable_sources and not missing_by_source:
        status = "unavailable"
    return {
        "id": "project_status_evidence_contract",
        "status": status,
        "authoritative_source": "scripts/generate_project_status.py",
        "mirror_sources": list(expected_by_source),
        "expected_markers_by_source": expected_by_source,
        "missing_by_source": missing_by_source,
        "unavailable_sources": unavailable_sources,
    }


def _build_session_override_policy(repo_root: Path, source_texts: dict[str, str | None]) -> dict[str, Any]:
    doc_path = repo_root / "docs" / "governance-dashboard.md"
    doc_text = source_texts.get("docs/governance-dashboard.md")
    required_markers = [
        "Active Session Overrides",
        ".codex/config.toml",
        "current session",
        "repo-local defaults",
    ]
    missing_markers = [] if doc_text is None else [marker for marker in required_markers if marker not in doc_text]
    documented = doc_text is not None and not missing_markers
    return {
        "documented": documented,
        "doc_path": str(doc_path),
        "status": "passed" if documented else "unavailable",
        "missing_markers": missing_markers,
        "policy": (
            "Active session or harness policy wins for the current run when it differs from .codex/config.toml; "
            "repo-local Codex settings remain the default contract for future sessions and collaborators."
        ),
    }


def _build_drift_summary(repo_root: Path) -> dict[str, Any]:
    source_paths = {
        "kindlemaster.py": repo_root / "kindlemaster.py",
        "README.md": repo_root / "README.md",
        ".codex/config.toml": repo_root / ".codex" / "config.toml",
        ".codex/README.md": repo_root / ".codex" / "README.md",
        "AGENTS.md": repo_root / "AGENTS.md",
        "docs/toolchain-matrix.md": repo_root / "docs" / "toolchain-matrix.md",
        "docs/governance-dashboard.md": repo_root / "docs" / "governance-dashboard.md",
    }
    source_texts = {name: _read_text(path) for name, path in source_paths.items()}
    kindlemaster_source = source_texts.get("kindlemaster.py")
    session_override = _build_session_override_policy(repo_root, source_texts)
    if kindlemaster_source is None:
        return {
            "status": "unavailable",
            "checks": [],
            "session_override": session_override,
            "unavailable_sources": ["kindlemaster.py"],
        }

    checks = [
        _check_command_mirrors(kindlemaster_source=kindlemaster_source, source_texts=source_texts),
        _check_toolchain_suite_mirrors(kindlemaster_source=kindlemaster_source, source_texts=source_texts),
        _check_status_evidence_contract(source_texts),
    ]
    failed_checks = [check["id"] for check in checks if check["status"] == "failed"]
    unavailable_checks = [check["id"] for check in checks if check["status"] == "unavailable"]
    status = "failed" if failed_checks else "passed"
    if unavailable_checks and not failed_checks:
        status = "unavailable"
    return {
        "status": status,
        "checks": checks,
        "failed_checks": failed_checks,
        "unavailable_checks": unavailable_checks,
        "session_override": session_override,
    }


def _workflow_record_summary(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        "run_id": record["run_id"],
        "classification": record["classification"],
        "status": record["status"],
        "change_area": record["change_area"],
        "input_type": record["input_type"],
        "reports_dir": record["reports_dir"],
        "updated_at": record["updated_at"],
        "report_complete": record["report_complete"],
        "missing_artifacts": record["missing_artifacts"],
        "unreadable_json_artifacts": record["unreadable_json_artifacts"],
    }


def _classify_workflow_dir(workflow_dir: Path) -> dict[str, Any]:
    artifact_paths = {name: workflow_dir / name for name in REQUIRED_WORKFLOW_REPORT_ARTIFACTS}
    missing_artifacts = [name for name, path in artifact_paths.items() if not path.exists()]

    json_artifacts = {
        "baseline.json": _load_json(workflow_dir / "baseline.json"),
        "isolation.json": _load_json(workflow_dir / "isolation.json"),
        "verification.json": _load_json(workflow_dir / "verification.json"),
        "before_after.json": _load_json(workflow_dir / "before_after.json"),
        "regression_pack.json": _load_json(workflow_dir / "regression_pack.json"),
        "smoke_pack.json": _load_json(workflow_dir / "smoke_pack.json"),
    }
    unreadable_json_artifacts = [
        name
        for name, payload in json_artifacts.items()
        if (workflow_dir / name).exists() and payload is None
    ]

    baseline = json_artifacts["baseline.json"] or {}
    verification = json_artifacts["verification.json"] or {}
    before_after = json_artifacts["before_after.json"] or {}
    report_complete = before_after.get("report_complete") if before_after else None

    has_baseline = any((workflow_dir / name).exists() for name in WORKFLOW_BASELINE_REPORT_ARTIFACTS)
    has_verification = any((workflow_dir / name).exists() for name in WORKFLOW_VERIFY_REPORT_ARTIFACTS)
    is_complete = not missing_artifacts and not unreadable_json_artifacts and report_complete is not False
    if is_complete:
        classification = "complete"
    elif has_baseline and not has_verification:
        classification = "baseline_only"
    else:
        classification = "incomplete"

    return {
        "run_id": workflow_dir.name,
        "classification": classification,
        "status": _normalize_status(verification.get("status") or before_after.get("status") or baseline.get("snapshot", {}).get("status")),
        "change_area": str(verification.get("change_area") or baseline.get("change_area") or ""),
        "input_type": str(verification.get("input_type") or baseline.get("input_type") or ""),
        "reports_dir": str(workflow_dir),
        "updated_at": _latest_path_mtime_iso(workflow_dir),
        "report_complete": report_complete,
        "missing_artifacts": missing_artifacts,
        "unreadable_json_artifacts": unreadable_json_artifacts,
        "_sort_time": _parse_iso_datetime(_latest_path_mtime_iso(workflow_dir)) or datetime.fromtimestamp(0, timezone.utc),
    }


def _build_workflow_completeness_summary(workflows_root: Path) -> dict[str, Any]:
    if not workflows_root.exists():
        return {
            "available": False,
            "reports_root": str(workflows_root),
            "required_report_artifacts": list(REQUIRED_WORKFLOW_REPORT_ARTIFACTS),
            "total_count": 0,
            "complete_count": 0,
            "incomplete_count": 0,
            "baseline_only_count": 0,
            "latest_completed": None,
            "latest_incomplete": None,
        }

    records = [
        _classify_workflow_dir(item)
        for item in workflows_root.iterdir()
        if item.is_dir()
    ]
    records.sort(key=lambda item: item["_sort_time"], reverse=True)
    complete_records = [record for record in records if record["classification"] == "complete"]
    incomplete_records = [record for record in records if record["classification"] != "complete"]
    baseline_only_records = [record for record in records if record["classification"] == "baseline_only"]

    return {
        "available": bool(records),
        "reports_root": str(workflows_root),
        "required_report_artifacts": list(REQUIRED_WORKFLOW_REPORT_ARTIFACTS),
        "total_count": len(records),
        "complete_count": len(complete_records),
        "incomplete_count": len(incomplete_records),
        "baseline_only_count": len(baseline_only_records),
        "latest_completed": _workflow_record_summary(complete_records[0] if complete_records else None),
        "latest_incomplete": _workflow_record_summary(incomplete_records[0] if incomplete_records else None),
        "incomplete_run_ids": [record["run_id"] for record in incomplete_records[:10]],
    }


def _find_latest_completed_workflow(workflows_root: Path) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    completeness = _build_workflow_completeness_summary(workflows_root)
    latest_completed = completeness.get("latest_completed")
    if not latest_completed:
        return None, None, None
    workflow_dir = Path(str(latest_completed["reports_dir"]))
    return workflow_dir, _load_json(workflow_dir / "verification.json"), _load_json(workflow_dir / "before_after.json")


def _collect_workflow_completeness_warnings(workflow: dict[str, Any]) -> list[str]:
    completeness = workflow.get("completeness", {})
    latest_incomplete = completeness.get("latest_incomplete")
    if not latest_incomplete:
        return []
    latest_completed = completeness.get("latest_completed") or {}
    latest_incomplete_time = _parse_iso_datetime(str(latest_incomplete.get("updated_at") or ""))
    latest_completed_time = _parse_iso_datetime(str(latest_completed.get("updated_at") or ""))
    if latest_completed_time and latest_incomplete_time and latest_completed_time > latest_incomplete_time:
        return []
    classification = latest_incomplete.get("classification", "incomplete")
    run_id = latest_incomplete.get("run_id", "")
    missing = latest_incomplete.get("missing_artifacts") or []
    if classification == "baseline_only":
        return [f"Latest incomplete workflow `{run_id}` is baseline-only; run workflow verify to complete before/after evidence."]
    if missing:
        return [f"Latest incomplete workflow `{run_id}` is missing required report artifacts: {', '.join(missing)}."]
    return [f"Latest incomplete workflow `{run_id}` has incomplete workflow evidence."]


def _build_corpus_summary(corpus_gate: dict[str, Any] | None, corpus_gate_path: Path) -> dict[str, Any]:
    if not corpus_gate:
        return {
            "available": False,
            "status": "unavailable",
            "path": str(corpus_gate_path),
            "proof_profile": "unavailable",
            "smoke_status": "unavailable",
            "premium_status": "unavailable",
        }

    smoke_summary = (corpus_gate.get("smoke") or {}).get("summary") or {}
    premium_overall = (corpus_gate.get("premium_corpus") or {}).get("overall") or {}
    return {
        "available": True,
        "status": _normalize_status(corpus_gate.get("overall_status")),
        "path": str(corpus_gate_path),
        "proof_profile": str(corpus_gate.get("proof_profile", "unavailable")),
        "smoke_status": _normalize_status(smoke_summary.get("overall_status")),
        "premium_status": _normalize_status((corpus_gate.get("premium_corpus") or {}).get("overall_status") or premium_overall.get("overall_status")),
        "converted_case_count": int(premium_overall.get("converted_case_count", 0) or 0),
        "analysis_only_case_count": int(premium_overall.get("analysis_only_case_count", 0) or 0),
        "grade_counts": premium_overall.get("grade_counts", {}),
        "blocker_counts": premium_overall.get("blocker_counts", {}),
        "warning_counts": premium_overall.get("warning_counts", {}),
    }


def _build_workflow_summary(workflows_root: Path) -> dict[str, Any]:
    completeness = _build_workflow_completeness_summary(workflows_root)
    workflow_dir, verification, before_after = _find_latest_completed_workflow(workflows_root)
    if workflow_dir is None or verification is None or before_after is None:
        return {
            "available": False,
            "status": "unavailable",
            "reports_root": str(workflows_root),
            "completeness": completeness,
        }

    remaining_risks = before_after.get("remaining_risks") or verification.get("verification_snapshot", {}).get("symptoms") or []
    unresolved_warnings = before_after.get("unresolved_warnings") or []
    return {
        "available": True,
        "status": _normalize_status(verification.get("status")),
        "run_id": str(verification.get("run_id", workflow_dir.name)),
        "change_area": str(verification.get("change_area", "")),
        "reports_dir": str(workflow_dir),
        "verification_json": str(workflow_dir / "verification.json"),
        "before_after_json": str(workflow_dir / "before_after.json"),
        "regression_pack_status": _normalize_status(before_after.get("regression_pack_status")),
        "smoke_status": _normalize_status(before_after.get("smoke_status")),
        "remaining_risks": [str(item) for item in remaining_risks if str(item).strip()],
        "unresolved_warnings": [str(item) for item in unresolved_warnings if str(item).strip()],
        "completeness": completeness,
    }


def _build_governance_summary(repo_root: Path) -> dict[str, Any]:
    workflow_path = repo_root / ".github" / "workflows" / "ready-enforcement.yml"
    doc_path = repo_root / "docs" / "github-ready-enforcement.md"
    drift = _build_drift_summary(repo_root)
    return {
        "ready_workflow_present": workflow_path.exists(),
        "ready_workflow_path": str(workflow_path),
        "ready_doc_present": doc_path.exists(),
        "ready_doc_path": str(doc_path),
        "drift_status": drift["status"],
        "drift_checks": drift["checks"],
        "drift_failed_checks": drift.get("failed_checks", []),
        "drift_unavailable_checks": drift.get("unavailable_checks", []),
        "session_override": drift["session_override"],
    }


def _derive_project_status(
    *,
    corpus: dict[str, Any],
    workflow: dict[str, Any],
    governance: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []

    corpus_status = corpus["status"]
    workflow_status = workflow["status"]

    if corpus_status == "failed":
        blockers.append("Corpus gate is failed.")
    elif corpus_status == "passed_with_warnings":
        warnings.append("Corpus gate is passing with warnings.")
    elif corpus_status == "unavailable":
        warnings.append("Corpus gate evidence is unavailable.")

    if workflow_status == "failed":
        warnings.append("Latest completed workflow verify is failed.")
    elif workflow_status == "passed_with_warnings":
        warnings.append("Latest completed workflow verify is passing with warnings.")
    elif workflow_status == "unavailable":
        warnings.append("No completed workflow verification evidence was found.")

    if not governance.get("ready_workflow_present", False):
        warnings.append("GitHub READY workflow evidence is missing.")
    if not governance.get("ready_doc_present", False):
        warnings.append("GitHub READY enforcement documentation is missing.")
    if governance.get("drift_status") == "failed":
        warnings.append("Governance drift checks detected mismatched command or policy mirrors.")

    return _derive_overall_status(blockers, warnings), blockers, warnings


def _derive_overall_status(blockers: list[str], warnings: list[str]) -> str:
    overall_status = "passed"
    if blockers:
        overall_status = "failed"
    elif warnings:
        overall_status = "passed_with_warnings"

    return overall_status


def generate_project_status(
    *,
    repo_root: str | Path = ".",
    reports_root: str | Path = "reports",
    output_json: str | Path = "reports/project_status.json",
    output_md: str | Path = "reports/project_status.md",
) -> dict[str, Any]:
    resolved_repo_root = Path(repo_root).resolve()
    resolved_reports_root = Path(reports_root).resolve()
    resolved_output_json = Path(output_json).resolve()
    resolved_output_md = Path(output_md).resolve()
    corpus_gate_path = resolved_reports_root / "corpus" / "corpus_gate.json"
    workflows_root = resolved_reports_root / "workflows"

    corpus = _build_corpus_summary(_load_json(corpus_gate_path), corpus_gate_path)
    workflow = _build_workflow_summary(workflows_root)
    governance = _build_governance_summary(resolved_repo_root)
    overall_status, blockers, warnings = _derive_project_status(
        corpus=corpus,
        workflow=workflow,
        governance=governance,
    )
    generated_at = datetime.now(timezone.utc).isoformat()
    dashboard = _build_governance_dashboard(
        reports_root=resolved_reports_root,
        output_json=resolved_output_json,
        generated_at=generated_at,
        overall_status=overall_status,
    )
    warnings.extend(_collect_evidence_freshness_warnings(dashboard))
    warnings.extend(_collect_workflow_completeness_warnings(workflow))
    overall_status = _derive_overall_status(blockers, warnings)
    dashboard = _build_governance_dashboard(
        reports_root=resolved_reports_root,
        output_json=resolved_output_json,
        generated_at=generated_at,
        overall_status=overall_status,
    )

    payload = {
        "generated_at": generated_at,
        "overall_status": overall_status,
        "summary": {
            "corpus_status": corpus["status"],
            "workflow_status": workflow["status"],
            "proof_profile": corpus.get("proof_profile", "unavailable"),
            "ready_workflow_present": governance["ready_workflow_present"],
            "ready_doc_present": governance["ready_doc_present"],
            "dashboard_status": dashboard["status"],
            "drift_status": governance["drift_status"],
            "session_override_documented": governance["session_override"]["documented"],
            "workflow_complete_count": workflow["completeness"]["complete_count"],
            "workflow_incomplete_count": workflow["completeness"]["incomplete_count"],
            "workflow_baseline_only_count": workflow["completeness"]["baseline_only_count"],
        },
        "blockers": blockers,
        "warnings": warnings,
        "corpus": corpus,
        "workflow": workflow,
        "governance": governance,
        "dashboard": dashboard,
    }

    resolved_output_json.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_md.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    resolved_output_md.write_text(build_project_status_markdown(payload), encoding="utf-8")
    return payload


def build_project_status_markdown(payload: dict[str, Any]) -> str:
    corpus = payload["corpus"]
    workflow = payload["workflow"]
    completeness = workflow["completeness"]
    governance = payload["governance"]
    dashboard = payload["dashboard"]
    lines = [
        "# KindleMaster Project Status",
        "",
        f"- Overall status: `{payload['overall_status']}`",
        f"- Generated at: `{payload['generated_at']}`",
        "",
        "## Summary",
        "",
        f"- Corpus gate: `{corpus['status']}`",
        f"- Corpus proof profile: `{corpus.get('proof_profile', 'unavailable')}`",
        f"- Latest completed workflow: `{workflow['status']}`",
        f"- Workflow completeness: `{completeness['complete_count']}` complete / `{completeness['incomplete_count']}` incomplete / `{completeness['baseline_only_count']}` baseline-only",
        f"- GitHub READY workflow present: `{governance['ready_workflow_present']}`",
        f"- VAT-206 dashboard: `{dashboard['status']}`",
        f"- Governance drift: `{governance['drift_status']}`",
        "",
        "## Evidence",
        "",
        f"- Corpus gate JSON: `{corpus['path']}`",
        f"- Latest workflow reports dir: `{workflow.get('reports_dir', '')}`",
        f"- READY workflow: `{governance['ready_workflow_path']}`",
        "",
        "## Workflow Completeness",
        "",
        f"- Reports root: `{completeness['reports_root']}`",
        f"- Complete workflows: `{completeness['complete_count']}`",
        f"- Incomplete workflows: `{completeness['incomplete_count']}`",
        f"- Baseline-only workflows: `{completeness['baseline_only_count']}`",
        f"- Latest completed: `{(completeness.get('latest_completed') or {}).get('run_id', '')}`",
        f"- Latest incomplete: `{(completeness.get('latest_incomplete') or {}).get('run_id', '')}`",
        "",
        "## VAT-206 Governance Dashboard",
        "",
        f"Freshness warning threshold: `{dashboard['freshness_max_age_hours']}` hours.",
        "",
        "| Lane | Status | Freshness | Evidence | Command |",
        "| --- | --- | --- | --- | --- |",
    ]
    for lane, row in dashboard["evidence"].items():
        lines.append(
            f"| `{lane}` | `{row['status']}` | `{row['freshness_status']}` | `{row['path']}` | `{row['command']}` |"
        )
    lines.extend(
        [
            "",
            "## Drift Checks",
            "",
            "| Check | Status | Source |",
            "| --- | --- | --- |",
        ]
    )
    for check in governance["drift_checks"]:
        lines.append(
            f"| `{check['id']}` | `{check['status']}` | `{check['authoritative_source']}` |"
        )
    session_override = governance["session_override"]
    lines.extend(
        [
            "",
            "## Active Session Overrides",
            "",
            f"- Documented: `{session_override['documented']}`",
            f"- Policy: {session_override['policy']}",
            f"- Documentation: `{session_override['doc_path']}`",
            "",
        ]
    )
    if payload["blockers"]:
        lines.extend(["## Blockers", ""])
        for item in payload["blockers"]:
            lines.append(f"- {item}")
        lines.append("")
    if payload["warnings"]:
        lines.extend(["## Warnings", ""])
        for item in payload["warnings"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a derived KindleMaster project status from existing evidence artifacts.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--reports-root", default="reports")
    parser.add_argument("--output-json", default="reports/project_status.json")
    parser.add_argument("--output-md", default="reports/project_status.md")
    args = parser.parse_args()

    payload = generate_project_status(
        repo_root=args.repo_root,
        reports_root=args.reports_root,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["overall_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
