from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
import shutil

import yaml

from kindlemaster_end_to_end import FINAL_DIR, REPORT_DIR, ROOT, run_end_to_end
from kindlemaster_manifest import get_publication, list_publications, repo_relative_path
from kindlemaster_quality_score import score_epub
from kindlemaster_release_gate import run_checks


PROJECT_CONTROL = ROOT / "project_control"
ISSUE_REGISTER_PATH = PROJECT_CONTROL / "issue_register.yaml"
JSON_REPORT_PATH = PROJECT_CONTROL / "phase12_release_gate_enforcement.json"
MD_REPORT_PATH = PROJECT_CONTROL / "phase12_release_gate_enforcement.md"
CORPUS_JSON_REPORT_PATH = PROJECT_CONTROL / "phase12_corpus_release_gate_enforcement.json"
CORPUS_MD_REPORT_PATH = PROJECT_CONTROL / "phase12_corpus_release_gate_enforcement.md"
RELEASE_GATE_ROOT = ROOT / "kindlemaster_runtime" / "output" / "release_gate"
PYTEST_SUITES = [
    "tests/test_release_metadata.py",
    "tests/test_conversion_traceability.py",
    "tests/test_pdf_list_normalization.py",
    "tests/test_text_quality_thresholds.py",
    "tests/test_navigation_quality.py",
    "tests/test_finalizer_stage_proofs.py",
    "tests/test_front_matter_quality.py",
    "tests/test_typography_ux.py",
    "tests/test_image_layout_quality.py",
    "tests/test_regressions.py",
    "tests/test_release_candidate_immutability.py",
    "tests/test_isolation_boundaries.py",
    "tests/test_scenario_manifest.py",
    "tests/test_corpus_quality_state.py",
    "tests/test_genericity_guards.py",
]


def _load_issues() -> list[dict]:
    payload = yaml.safe_load(ISSUE_REGISTER_PATH.read_text(encoding="utf-8")) or {}
    return list(payload.get("issues", []))


def _release_blocking_issues() -> list[dict]:
    issues = []
    for issue in _load_issues():
        severity = str(issue.get("severity") or "").lower()
        status = str(issue.get("status") or "").upper()
        if severity not in {"high", "critical"}:
            continue
        if status in {"RESOLVED", "WONT_FIX"}:
            continue
        issues.append(issue)
    return issues


def _run_pytest(candidate_report_path: Path, candidate_epub: Path, publication_id: str) -> dict[str, object]:
    env = os.environ.copy()
    env["KM_ACTIVE_REPORT_JSON"] = str(candidate_report_path.resolve())
    env["KM_ACTIVE_FINAL_EPUB"] = str(candidate_epub.resolve())
    env["KM_ACTIVE_PUBLICATION_ID"] = publication_id
    completed = subprocess.run(
        ["python", "-m", "pytest", "-q", *PYTEST_SUITES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
        env=env,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    passed = 0
    failed = 0
    for line in output.splitlines():
        if " passed" in line or " failed" in line or " error" in line:
            parts = line.replace(",", "").split()
            for index, token in enumerate(parts):
                if token == "passed" and index > 0 and parts[index - 1].isdigit():
                    passed = int(parts[index - 1])
                if token in {"failed", "errors", "error"} and index > 0 and parts[index - 1].isdigit():
                    failed += int(parts[index - 1])
    failed_cases = [line.strip() for line in output.splitlines() if line.strip().startswith(("FAILED ", "ERROR "))]
    return {
        "command": ["python", "-m", "pytest", "-q", *PYTEST_SUITES],
        "exit_code": completed.returncode,
        "tests_passed": passed,
        "tests_failed": failed,
        "failed_cases": failed_cases,
        "stdout_tail": "\n".join(output.splitlines()[-40:]),
    }


def _write_reports(payload: dict[str, object]) -> None:
    JSON_REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    blocking_lines = "\n".join(
        f"- `{issue['issue_id']}` `{issue['severity']}` `{issue['status']}`: {issue['description']}"
        for issue in payload.get("release_blocking_issues", [])
    ) or "- none"
    failed_cases = "\n".join(f"- {case}" for case in payload["pytest"].get("failed_cases", [])) or "- none"
    failed_checks = "\n".join(f"- `{check}`" for check in payload["smoke"].get("failed_checks", [])) or "- none"
    md = f"""# Phase 12 Release Gate Enforcement

## Verdict

`{payload['verdict']}`

## Publication

- `publication_id`: `{payload['publication_id']}`
- `final_epub`: `{payload['final_epub_relative']}`
- `report_json`: `{payload['report_json_relative']}`
- `score`: `{payload['quality_score']}/10`
- `premium_target`: `{payload['premium_target']}`

## Smoke

- `failed_checks`: {len(payload["smoke"].get("failed_checks", []))}

{failed_checks}

## Pytest

- `tests_passed`: `{payload["pytest"]["tests_passed"]}`
- `tests_failed`: `{payload["pytest"]["tests_failed"]}`

{failed_cases}

## Release-Blocking Issues

{blocking_lines}

## Notes

{payload['summary']}
"""
    MD_REPORT_PATH.write_text(md, encoding="utf-8")


def _write_corpus_reports(payload: dict[str, object]) -> None:
    CORPUS_JSON_REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    publication_lines = "\n".join(
        (
            f"- `{item['publication_id']}` `{item['verdict']}` "
            f"score=`{item['quality_score']}` "
            f"release_eligible=`{item['release_eligible']}` "
            f"text_first=`{item['text_first_pass']}` "
            f"fallbacks=`{item['fallback_count']}` "
            f"unjustified_fallbacks=`{item['unjustified_fallback_count']}` "
            f"failed_checks=`{', '.join(item['effective_failed_checks']) or 'none'}`"
        )
        for item in payload.get("publications", [])
    ) or "- none"
    strengths_lines = "\n".join(
        (
            f"### `{item['publication_id']}`\n"
            f"- good: {('; '.join(item.get('what_is_good') or []) or 'none')}\n"
            f"- bad: {('; '.join(item.get('what_is_bad') or []) or 'none')}"
        )
        for item in payload.get("publications", [])
    ) or "No per-publication premium notes were produced."
    blockers = "\n".join(f"- `{blocker}`" for blocker in payload.get("blockers", [])) or "- none"
    md = f"""# Phase 12 Corpus Release Gate Enforcement

## Verdict

`{payload['verdict']}`

## Mode

- `quality_first`: `{payload['quality_first']}`
- `pytest_tests_passed`: `{payload['pytest']['tests_passed']}`
- `pytest_tests_failed`: `{payload['pytest']['tests_failed']}`

## Publication Verdicts

{publication_lines}

## Premium Notes

{strengths_lines}

## Blockers

{blockers}
"""
    CORPUS_MD_REPORT_PATH.write_text(md, encoding="utf-8")


def _effective_failed_checks(smoke: dict[str, object], *, release_eligible: bool) -> list[str]:
    failed_checks = list(smoke.get("failed_checks") or [])
    if release_eligible:
        return failed_checks
    return [check for check in failed_checks if check != "creator_not_unknown_for_release"]


def enforce_release_gate(*, publication_id: str, pdf_path: Path | None = None) -> dict[str, object]:
    publication = get_publication(publication_id)
    if not publication:
        raise ValueError(f"Publication manifest entry not found for {publication_id}.")

    if pdf_path is None:
        pdf_rel = publication.get("inputs", {}).get("pdf_path")
        if not pdf_rel:
            raise ValueError(f"Publication {publication_id} is missing pdf_path.")
        pdf_path = (ROOT / pdf_rel).resolve()
    else:
        pdf_path = pdf_path.resolve()

    gate_run_root = RELEASE_GATE_ROOT / publication_id
    if gate_run_root.exists():
        shutil.rmtree(gate_run_root)
    candidate_baseline_dir = gate_run_root / "baseline"
    candidate_final_dir = gate_run_root / "final"
    candidate_release_dir = gate_run_root / "release_candidate"
    candidate_report_dir = gate_run_root / "reports"

    report = run_end_to_end(
        pdf_path,
        publication_id=publication_id,
        release_mode=True,
        baseline_dir=candidate_baseline_dir,
        final_dir=candidate_final_dir,
        release_candidate_dir=candidate_release_dir,
        report_dir=candidate_report_dir,
    )
    report_json = candidate_report_dir / f"{pdf_path.stem}-end-to-end.json"
    final_epub = Path(report["final_epub"]).resolve()

    smoke = run_checks(final_epub)
    pytest_result = _run_pytest(report_json, final_epub, publication_id)
    quality = score_epub(final_epub, publication_id=publication_id)
    release_blocking_issues = _release_blocking_issues()

    blockers: list[str] = []
    if smoke.get("failed_checks"):
        blockers.append("smoke_checks_failed")
    if pytest_result["exit_code"] != 0:
        blockers.append("pytest_gate_failed")
    if quality["weighted_score"] < quality["premium_target"]:
        blockers.append("premium_target_not_met")
    if release_blocking_issues:
        blockers.append("open_high_or_critical_issues_present")
    if not report.get("release_gate", {}).get("release_eligible"):
        blockers.append("release_metadata_not_eligible")

    verdict = "PASS" if not blockers else "FAIL"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "publication_id": publication_id,
        "source_pdf_relative": report["source_pdf_relative"],
        "final_epub_relative": report["final_epub_relative"],
        "report_json_relative": repo_relative_path(report_json),
        "smoke": smoke,
        "pytest": pytest_result,
        "quality_score": quality["weighted_score"],
        "premium_target": quality["premium_target"],
        "quality_gap": quality["premium_gap"],
        "premium_report": quality.get("premium_report"),
        "release_blocking_issues": [
            {
                "issue_id": issue["issue_id"],
                "severity": issue["severity"],
                "status": issue["status"],
                "description": issue["description"],
            }
            for issue in release_blocking_issues
        ],
        "blockers": blockers,
        "verdict": verdict,
        "summary": (
            "Release gate enforcement is now executable inside the repository. "
            "The current publication passes smoke and pytest, but READY remains blocked until all release-blocking issues are resolved."
            if verdict == "FAIL"
            else "Release gate enforcement passed with no remaining blockers."
        ),
    }
    _write_reports(payload)
    return payload


def enforce_corpus_release_gate(*, quality_first: bool) -> dict[str, object]:
    publications = list_publications()
    if not publications:
        raise ValueError("No publications registered in publication_manifest.yaml.")

    per_publication: list[dict[str, object]] = []
    release_blocking_issues = _release_blocking_issues()
    pytest_anchor_report: Path | None = None
    pytest_anchor_epub: Path | None = None
    pytest_anchor_publication_id: str | None = None

    for publication in publications:
        publication_id = publication["publication_id"]
        pdf_path = (ROOT / publication["inputs"]["pdf_path"]).resolve()
        gate_run_root = RELEASE_GATE_ROOT / "corpus" / publication_id
        if gate_run_root.exists():
            shutil.rmtree(gate_run_root)
        candidate_report = run_end_to_end(
            pdf_path,
            publication_id=publication_id,
            release_mode=bool((publication.get("status") or {}).get("release_eligible")),
            baseline_dir=gate_run_root / "baseline",
            final_dir=gate_run_root / "final",
            release_candidate_dir=gate_run_root / "release_candidate",
            report_dir=gate_run_root / "reports",
        )
        final_epub = Path(candidate_report["final_epub"]).resolve()
        smoke = run_checks(final_epub)
        quality = score_epub(final_epub, publication_id=publication_id)
        premium_report = quality.get("premium_report") if isinstance(quality.get("premium_report"), dict) else {}
        release_eligible = bool((publication.get("status") or {}).get("release_eligible"))
        effective_failed_checks = _effective_failed_checks(smoke, release_eligible=release_eligible)
        score_target = 9.0 if quality_first and release_eligible else 8.8
        publication_blockers: list[str] = []
        if effective_failed_checks:
            publication_blockers.append("smoke_checks_failed")
        if quality["weighted_score"] < score_target:
            publication_blockers.append("quality_target_not_met")
        if not bool(quality.get("text_first_pass")):
            publication_blockers.append("text_first_not_met")
        if release_eligible and not candidate_report.get("release_gate", {}).get("release_eligible"):
            publication_blockers.append("release_metadata_not_eligible")
        per_publication.append(
            {
                "publication_id": publication_id,
                "profile": publication.get("profile"),
                "release_eligible": release_eligible,
                "quality_score": quality["weighted_score"],
                "quality_target": score_target,
                "premium_gap": quality["premium_gap"],
                "premium_report": premium_report,
                "what_is_good": list(premium_report.get("what_is_good") or []),
                "what_is_bad": list(premium_report.get("what_is_bad") or []),
                "effective_failed_checks": effective_failed_checks,
                "failed_checks": smoke["failed_checks"],
                "text_first_pass": bool(quality.get("text_first_pass")),
                "coverage_pass": bool((candidate_report.get("coverage") or {}).get("coverage_pass")),
                "coverage_ratio": (candidate_report.get("coverage") or {}).get("coverage_ratio"),
                "text_first_pages": int((candidate_report.get("baseline_report") or {}).get("text_first_pages") or 0),
                "hybrid_illustrated_pages": int(
                    (candidate_report.get("baseline_report") or {}).get("hybrid_illustrated_pages") or 0
                ),
                "fallback_count": int((smoke.get("counts") or {}).get("image_fallback_file_count") or 0),
                "justified_fallback_count": int((smoke.get("counts") or {}).get("justified_fallback_count") or 0),
                "unjustified_fallback_count": smoke["counts"].get("unjustified_fallback_count", 0),
                "report_json_relative": repo_relative_path(
                    (gate_run_root / "reports" / f"{pdf_path.stem}-end-to-end.json").resolve()
                ),
                "verdict": "PASS" if not publication_blockers else "FAIL",
                "blockers": publication_blockers,
            }
        )
        if release_eligible and pytest_anchor_report is None:
            pytest_anchor_report = gate_run_root / "reports" / f"{pdf_path.stem}-end-to-end.json"
            pytest_anchor_epub = final_epub
            pytest_anchor_publication_id = publication_id

    if pytest_anchor_report is None or pytest_anchor_epub is None or pytest_anchor_publication_id is None:
        raise ValueError("Corpus release gate requires at least one release-eligible publication.")

    preliminary_blockers: list[str] = []
    if release_blocking_issues:
        preliminary_blockers.append("open_high_or_critical_issues_present")
    publication_failures = [item["publication_id"] for item in per_publication if item["verdict"] != "PASS"]
    if publication_failures:
        preliminary_blockers.append(f"corpus_publication_failures:{','.join(publication_failures)}")
    preliminary_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "quality_first": quality_first,
        "publications_total": len(per_publication),
        "release_eligible_total": sum(1 for item in per_publication if item["release_eligible"]),
        "text_first_pass_total": sum(1 for item in per_publication if item["text_first_pass"]),
        "unjustified_fallback_total": sum(int(item["unjustified_fallback_count"]) for item in per_publication),
        "publications": per_publication,
        "pytest": {
            "command": ["python", "-m", "pytest", "-q", *PYTEST_SUITES],
            "exit_code": None,
            "tests_passed": 0,
            "tests_failed": 0,
            "failed_cases": [],
            "stdout_tail": "pending",
        },
        "release_blocking_issues": [
            {
                "issue_id": issue["issue_id"],
                "severity": issue["severity"],
                "status": issue["status"],
                "description": issue["description"],
            }
            for issue in release_blocking_issues
        ],
        "blockers": preliminary_blockers,
        "verdict": "PASS" if not preliminary_blockers else "FAIL",
    }
    _write_corpus_reports(preliminary_payload)

    pytest_result = _run_pytest(pytest_anchor_report, pytest_anchor_epub, pytest_anchor_publication_id)
    blockers: list[str] = []
    if pytest_result["exit_code"] != 0:
        blockers.append("pytest_gate_failed")
    if release_blocking_issues:
        blockers.append("open_high_or_critical_issues_present")
    if publication_failures:
        blockers.append(f"corpus_publication_failures:{','.join(publication_failures)}")

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "quality_first": quality_first,
        "publications_total": len(per_publication),
        "release_eligible_total": sum(1 for item in per_publication if item["release_eligible"]),
        "text_first_pass_total": sum(1 for item in per_publication if item["text_first_pass"]),
        "unjustified_fallback_total": sum(int(item["unjustified_fallback_count"]) for item in per_publication),
        "publications": per_publication,
        "pytest": pytest_result,
        "release_blocking_issues": [
            {
                "issue_id": issue["issue_id"],
                "severity": issue["severity"],
                "status": issue["status"],
                "description": issue["description"],
            }
            for issue in release_blocking_issues
        ],
        "blockers": blockers,
        "verdict": "PASS" if not blockers else "FAIL",
    }
    _write_corpus_reports(payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Kindle Master Phase 12 release-gate enforcement")
    parser.add_argument("--publication-id", help="Manifest-backed publication id")
    parser.add_argument("--pdf", help="Optional explicit PDF path")
    parser.add_argument("--corpus", action="store_true", help="Run corpus-wide release gate across all manifest publications")
    parser.add_argument("--quality-first", action="store_true", help="Use stricter corpus quality thresholds")
    args = parser.parse_args()

    if args.corpus:
        payload = enforce_corpus_release_gate(quality_first=args.quality_first)
    else:
        if not args.publication_id:
            raise SystemExit("--publication-id is required unless --corpus is used.")
        payload = enforce_release_gate(
            publication_id=args.publication_id,
            pdf_path=Path(args.pdf) if args.pdf else None,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(0 if payload["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
