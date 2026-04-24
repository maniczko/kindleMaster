from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    if normalized in {"pass", "passed"}:
        return "passed"
    if normalized in {"warning", "warnings", "pass_with_review", "passed_with_warnings"}:
        return "passed_with_warnings"
    if normalized in {"fail", "failed", "error"}:
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


def _find_latest_completed_workflow(workflows_root: Path) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
    if not workflows_root.exists():
        return None, None, None
    candidates = sorted(
        (item for item in workflows_root.iterdir() if item.is_dir()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        verification = _load_json(candidate / "verification.json")
        before_after = _load_json(candidate / "before_after.json")
        if verification and before_after:
            return candidate, verification, before_after
    return None, None, None


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
    workflow_dir, verification, before_after = _find_latest_completed_workflow(workflows_root)
    if workflow_dir is None or verification is None or before_after is None:
        return {
            "available": False,
            "status": "unavailable",
            "reports_root": str(workflows_root),
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
    }


def _build_governance_summary(repo_root: Path) -> dict[str, Any]:
    workflow_path = repo_root / ".github" / "workflows" / "ready-enforcement.yml"
    doc_path = repo_root / "docs" / "github-ready-enforcement.md"
    return {
        "ready_workflow_present": workflow_path.exists(),
        "ready_workflow_path": str(workflow_path),
        "ready_doc_present": doc_path.exists(),
        "ready_doc_path": str(doc_path),
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

    overall_status = "passed"
    if blockers:
        overall_status = "failed"
    elif warnings:
        overall_status = "passed_with_warnings"

    return overall_status, blockers, warnings


def generate_project_status(
    *,
    repo_root: str | Path = ".",
    reports_root: str | Path = "reports",
    output_json: str | Path = "reports/project_status.json",
    output_md: str | Path = "reports/project_status.md",
) -> dict[str, Any]:
    resolved_repo_root = Path(repo_root).resolve()
    resolved_reports_root = Path(reports_root).resolve()
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

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "summary": {
            "corpus_status": corpus["status"],
            "workflow_status": workflow["status"],
            "proof_profile": corpus.get("proof_profile", "unavailable"),
            "ready_workflow_present": governance["ready_workflow_present"],
            "ready_doc_present": governance["ready_doc_present"],
        },
        "blockers": blockers,
        "warnings": warnings,
        "corpus": corpus,
        "workflow": workflow,
        "governance": governance,
    }

    resolved_output_json = Path(output_json).resolve()
    resolved_output_md = Path(output_md).resolve()
    resolved_output_json.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_md.parent.mkdir(parents=True, exist_ok=True)
    resolved_output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    resolved_output_md.write_text(build_project_status_markdown(payload), encoding="utf-8")
    return payload


def build_project_status_markdown(payload: dict[str, Any]) -> str:
    corpus = payload["corpus"]
    workflow = payload["workflow"]
    governance = payload["governance"]
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
        f"- GitHub READY workflow present: `{governance['ready_workflow_present']}`",
        "",
        "## Evidence",
        "",
        f"- Corpus gate JSON: `{corpus['path']}`",
        f"- Latest workflow reports dir: `{workflow.get('reports_dir', '')}`",
        f"- READY workflow: `{governance['ready_workflow_path']}`",
        "",
    ]
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
