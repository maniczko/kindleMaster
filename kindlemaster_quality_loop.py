from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

from kindlemaster_end_to_end import BASELINE_DIR, FINAL_DIR, REPORT_DIR, ROOT, run_end_to_end
from kindlemaster_manifest import get_publication, repo_relative_path
from kindlemaster_quality_score import compare_epub_quality, score_epub
from kindlemaster_versioning import build_identity, read_display_version


PROJECT_CONTROL = ROOT / "project_control"
BACKLOG_PATH = PROJECT_CONTROL / "backlog.yaml"
STATE_PATH = PROJECT_CONTROL / "quality_loop_state.json"
RUNTIME_STATE_PATH = ROOT / "kindlemaster_runtime" / "quality_loop_state.json"
CANDIDATE_ROOT = ROOT / "kindlemaster_runtime" / "output" / "candidates"
DEFAULT_GUARDS = [
    "newsweek-food-living-2026-01",
    "chess-5334-problems-combinations-and-games",
]
QUALITY_MUTATION_ORDER = [
    "FX-011",
    "FX-012",
    "R4-001",
    "R4-002",
    "R4-003",
    "R3-005",
    "R3-006",
    "R5-001",
    "R5-002",
    "R5-003",
    "R6-001",
    "R6-002",
    "R6-003",
    "T12-005",
    "T12-006",
    "T12-008",
    "T12-012",
    "T12-010",
    "T13-001",
    "T13-002",
    "T13-003",
    "T13-004",
    "T13-005",
]
PYTEST_SUITES = [
    "tests/test_release_metadata.py",
    "tests/test_conversion_traceability.py",
    "tests/test_pdf_list_normalization.py",
    "tests/test_text_quality_thresholds.py",
    "tests/test_navigation_quality.py",
    "tests/test_finalizer_stage_proofs.py",
    "tests/test_front_matter_quality.py",
    "tests/test_typography_ux.py",
    "tests/test_regressions.py",
    "tests/test_release_candidate_immutability.py",
    "tests/test_isolation_boundaries.py",
    "tests/test_scenario_manifest.py",
    "tests/test_corpus_quality_state.py",
    "tests/test_genericity_guards.py",
]
SUMMARY_RE = re.compile(
    r"(?:(?P<passed>\d+)\s+passed)?"
    r"(?:,\s*(?P<failed>\d+)\s+failed)?"
    r"(?:,\s*(?P<errors>\d+)\s+errors?)?"
    r"(?:,\s*(?P<skipped>\d+)\s+skipped)?",
    re.IGNORECASE,
)


def _package_version() -> str:
    version_path = ROOT / "VERSION"
    return read_display_version(version_path)


def _git_commit_short() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            cwd=ROOT,
        )
        return (completed.stdout or "").strip() or "nogit"
    except Exception:
        return "nogit"


def _build_label() -> str:
    return build_identity(_package_version(), _git_commit_short())


def _load_backlog() -> list[dict]:
    payload = yaml.safe_load(BACKLOG_PATH.read_text(encoding="utf-8")) or {}
    return list(payload.get("tasks", []))


def _priority_rank(priority: str) -> int:
    mapping = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return mapping.get(priority or "P9", 9)


def _select_next_task(tasks: list[dict]) -> dict | None:
    task_by_id = {task["id"]: task for task in tasks if "id" in task}
    eligible = []
    for task in tasks:
        if task.get("status") != "TODO":
            continue
        dependencies = task.get("depends_on") or []
        if all(task_by_id.get(dep, {}).get("status") == "DONE" for dep in dependencies):
            eligible.append(task)
    if not eligible:
        return None

    lane_lookup = {task_id: index for index, task_id in enumerate(QUALITY_MUTATION_ORDER)}
    eligible.sort(
        key=lambda task: (
            0 if task["id"] in lane_lookup else 1,
            lane_lookup.get(task["id"], 999),
            _priority_rank(task.get("priority", "P9")),
            str(task.get("phase", "")),
            task["id"],
        )
    )
    return eligible[0]


def _infer_state_kind(tasks: list[dict], selected_task: dict | None) -> str:
    if selected_task is not None:
        return "recovery_active"
    if any(task.get("status") == "IN_PROGRESS" for task in tasks):
        return "recovery_active"
    if any(task.get("status") == "TODO" for task in tasks):
        return "recovery_idle"
    return "maintenance_idle"


def _resolve_publication(publication_id: str) -> tuple[dict, Path, Path]:
    publication = get_publication(publication_id)
    if not publication:
        raise ValueError(f"Publication manifest entry not found for {publication_id}.")
    inputs = publication.get("inputs") or {}
    pdf_rel = inputs.get("pdf_path")
    epub_rel = inputs.get("epub_path")
    if not pdf_rel:
        raise ValueError(f"Publication {publication_id} is missing a repo-local pdf_path.")
    pdf_path = (ROOT / pdf_rel).resolve()
    reference_epub = (ROOT / epub_rel).resolve() if epub_rel else None
    if reference_epub is None or not reference_epub.exists():
        raise ValueError(f"Publication {publication_id} is missing a valid reference EPUB path.")
    return publication, pdf_path, reference_epub


def _report_path_for(publication_id: str) -> Path:
    return REPORT_DIR / f"{publication_id}-quality-loop.json"


def _parse_pytest_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for line in reversed(output.splitlines()):
        if " passed" not in line and " failed" not in line and " error" not in line and " skipped" not in line:
            continue
        match = SUMMARY_RE.search(line)
        if not match:
            continue
        for key in counts:
            value = match.group(key)
            counts[key] = int(value) if value else 0
        return counts
    return counts


def _run_pytest(candidate_report_path: Path, candidate_epub: Path, publication_id: str) -> dict[str, object]:
    env = os.environ.copy()
    env["KM_ACTIVE_REPORT_JSON"] = str(candidate_report_path)
    env["KM_ACTIVE_FINAL_EPUB"] = str(candidate_epub)
    env["KM_ACTIVE_PUBLICATION_ID"] = publication_id
    completed = subprocess.run(
        ["python", "-m", "pytest", "-q", *PYTEST_SUITES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=env,
    )
    output = f"{completed.stdout}\n{completed.stderr}".strip()
    counts = _parse_pytest_counts(output)
    failed_lines = [line.strip() for line in output.splitlines() if line.strip().startswith(("FAILED ", "ERROR "))]
    return {
        "command": ["python", "-m", "pytest", "-q", *PYTEST_SUITES],
        "exit_code": completed.returncode,
        "tests_passed": counts["passed"],
        "tests_failed": counts["failed"] + counts["errors"],
        "tests_skipped": counts["skipped"],
        "failed_cases": failed_lines,
        "stdout_tail": "\n".join(output.splitlines()[-40:]),
    }


def _run_guard_publications(guard_ids: list[str]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for guard_id in guard_ids:
        publication = get_publication(guard_id)
        if not publication:
            results.append(
                {
                    "publication_id": guard_id,
                    "status": "missing_manifest_entry",
                    "promotion_blocker": False,
                    "notes": ["Guard publication is not registered in publication_manifest.yaml."],
                }
            )
            continue

        final_path = FINAL_DIR / f"{Path(publication['inputs']['pdf_path']).stem}.epub"
        if not final_path.exists():
            results.append(
                {
                    "publication_id": guard_id,
                    "status": "informational_only_no_accepted_guard",
                    "promotion_blocker": False,
                    "notes": ["Guard publication has no accepted final EPUB yet, so only manifest presence is tracked."],
                }
            )
            continue

        try:
            score = score_epub(final_path, publication_id=guard_id)
            results.append(
                {
                    "publication_id": guard_id,
                    "status": "accepted_guard_present",
                    "promotion_blocker": False,
                    "weighted_score": score["weighted_score"],
                    "failed_checks": score["smoke"]["failed_checks"],
                    "notes": ["Guard publication is tracked against the currently accepted final artifact."],
                }
            )
        except Exception as exc:
            results.append(
                {
                    "publication_id": guard_id,
                    "status": "guard_score_failed",
                    "promotion_blocker": False,
                    "notes": [str(exc)],
                }
            )
    return results


def _promote_candidate(candidate_report: dict, publication: dict) -> dict[str, str | None]:
    final_source = Path(candidate_report["final_epub"]).resolve()
    report_source = Path(candidate_report["source_report"]).resolve()
    pdf_rel = publication["inputs"]["pdf_path"]
    pdf_name = Path(pdf_rel).stem
    final_target = FINAL_DIR / f"{pdf_name}.epub"
    report_target = REPORT_DIR / f"{pdf_name}-end-to-end.json"

    final_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_source, final_target)
    shutil.copy2(report_source, report_target)

    return {
        "final_epub": repo_relative_path(final_target),
        "report_json": repo_relative_path(report_target),
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_quality_loop(
    *,
    publication_id: str,
    max_iterations: int,
    target_score: float,
    guard_publications: list[str],
    stop_on_human_blocker: bool,
    resume_last: bool,
) -> dict[str, object]:
    publication, pdf_path, reference_epub = _resolve_publication(publication_id)
    backlog_tasks = _load_backlog()
    selected_task = _select_next_task(backlog_tasks)
    state_kind = _infer_state_kind(backlog_tasks, selected_task)
    build_label = _build_label()
    accepted_final = FINAL_DIR / f"{pdf_path.stem}.epub"

    if not accepted_final.exists():
        run_end_to_end(pdf_path, publication_id=publication_id, release_mode=bool(publication.get("status", {}).get("release_eligible")))

    last_state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if resume_last and STATE_PATH.exists() else {}
    loop_index = int(last_state.get("iteration_number", 0)) if isinstance(last_state, dict) else 0
    blockers: list[str] = []
    manifest_gaps = publication.get("status", {})
    if stop_on_human_blocker and not bool(manifest_gaps.get("release_eligible")):
        blockers.append("stop_on_human_blocker_triggered_for_non_release_publication")

    latest_iteration: dict[str, object] | None = None
    for offset in range(max_iterations):
        iteration_number = loop_index + offset + 1
        iteration_id = f"QL-{datetime.now():%Y%m%d-%H%M%S}-{iteration_number:02d}"
        candidate_root = CANDIDATE_ROOT / iteration_id
        candidate_baseline_dir = candidate_root / "baseline"
        candidate_final_dir = candidate_root / "final"
        candidate_release_dir = candidate_root / "release_candidate"
        candidate_report_dir = candidate_root / "reports"

        report = run_end_to_end(
            pdf_path,
            publication_id=publication_id,
            release_mode=bool(publication.get("status", {}).get("release_eligible")),
            baseline_dir=candidate_baseline_dir,
            final_dir=candidate_final_dir,
            release_candidate_dir=candidate_release_dir,
            report_dir=candidate_report_dir,
        )
        candidate_report_path = candidate_report_dir / f"{pdf_path.stem}-end-to-end.json"
        report["source_report"] = str(candidate_report_path)

        comparison = compare_epub_quality(
            Path(report["final_epub"]).resolve(),
            publication_id=publication_id,
            accepted_epub=accepted_final.resolve(),
            reference_epub=reference_epub.resolve(),
            baseline_epub=Path(report["baseline_epub"]).resolve(),
        )
        pytest_result = _run_pytest(candidate_report_path, Path(report["final_epub"]).resolve(), publication_id)
        guard_results = _run_guard_publications(guard_publications)

        candidate_score = comparison["candidate"]["weighted_score"]
        finalizer_pass = bool(report.get("finalizer_report", {}).get("final_pass"))
        guard_blockers = [
            result["publication_id"]
            for result in guard_results
            if result.get("promotion_blocker")
        ]
        promotion_allowed = (
            comparison["promotion_allowed"]
            and pytest_result["exit_code"] == 0
            and finalizer_pass
            and not guard_blockers
        )

        promoted_paths = None
        if promotion_allowed:
            promoted_paths = _promote_candidate(report, publication)

        next_task = selected_task["id"] if selected_task else None
        if promotion_allowed and candidate_score >= target_score:
            next_task = "T13-001"

        iteration_report = {
            "iteration_id": iteration_id,
            "iteration_number": iteration_number,
            "publication_id": publication_id,
            "build_label": build_label,
            "selected_issue_or_task": selected_task["id"] if selected_task else None,
            "selected_task_title": selected_task.get("title") if selected_task else None,
            "mutation_lane": (selected_task.get("phase") if selected_task else "QUALITY_LOOP"),
            "candidate_paths": {
                "root": repo_relative_path(candidate_root),
                "baseline_dir": repo_relative_path(candidate_baseline_dir),
                "final_dir": repo_relative_path(candidate_final_dir),
                "report_dir": repo_relative_path(candidate_report_dir),
                "final_epub": repo_relative_path(report["final_epub"]),
                "baseline_epub": repo_relative_path(report["baseline_epub"]),
                "report_json": repo_relative_path(candidate_report_path),
            },
            "active_sample_score_before": comparison["accepted"]["weighted_score"],
            "active_sample_score_after": candidate_score,
            "dual_baseline_delta": comparison["dual_baseline_delta"],
            "hard_regressions": comparison["hard_regressions"],
            "tests_passed": pytest_result["tests_passed"],
            "tests_failed": pytest_result["tests_failed"],
            "tests_skipped": pytest_result["tests_skipped"],
            "failed_cases": pytest_result["failed_cases"],
            "pytest_exit_code": pytest_result["exit_code"],
            "guard_publications": guard_results,
            "promotion_decision": "promoted" if promotion_allowed else "rejected",
            "next_task": next_task,
            "target_score": target_score,
            "candidate_release_blockers": report.get("release_gate", {}).get("blockers", []),
            "candidate_finalizer_pass": finalizer_pass,
            "candidate_quality": comparison["candidate"],
            "accepted_quality": comparison["accepted"],
            "reference_quality": comparison["reference"],
            "baseline_quality": comparison["baseline"],
            "promoted_paths": promoted_paths,
            "stop_reason": None,
        }

        if not promotion_allowed:
            rejection_reasons = []
            if comparison["hard_regressions"]:
                rejection_reasons.extend(comparison["hard_regressions"])
            if pytest_result["exit_code"] != 0:
                rejection_reasons.append("pytest_gate_failed")
            if not finalizer_pass:
                rejection_reasons.append("finalizer_proof_failed")
            if guard_blockers:
                rejection_reasons.append(f"guard_regressions:{','.join(guard_blockers)}")
            if not rejection_reasons and comparison["dual_baseline_delta"]["vs_accepted"] < 0.1:
                rejection_reasons.append("no_measurable_improvement")
            iteration_report["stop_reason"] = ",".join(rejection_reasons)
            blockers = list(rejection_reasons)
            latest_iteration = iteration_report
            break

        latest_iteration = iteration_report
        if candidate_score >= target_score:
            iteration_report["stop_reason"] = "premium_target_reached_for_active_sample"
            break

        iteration_report["stop_reason"] = "candidate_promoted_waiting_for_next_code_mutation"
        break

    if latest_iteration is None:
        raise RuntimeError("Quality loop did not produce any iteration report.")

    state_payload = {
        "iteration_id": latest_iteration["iteration_id"],
        "iteration_number": latest_iteration["iteration_number"],
        "publication_id": publication_id,
        "state_kind": state_kind,
        "selected_issue_or_task": latest_iteration["selected_issue_or_task"],
        "mutation_lane": latest_iteration["mutation_lane"],
        "promotion_decision": latest_iteration["promotion_decision"],
        "blockers": blockers,
        "pytest_suite_count": len(PYTEST_SUITES),
        "candidate": {
            "build_label": build_label,
            "status": latest_iteration["promotion_decision"],
            "publication_id": publication_id,
            "final_epub": latest_iteration["candidate_paths"]["final_epub"],
            "report_json": latest_iteration["candidate_paths"]["report_json"],
            "score": latest_iteration["active_sample_score_after"],
            "premium_gap": latest_iteration["candidate_quality"]["premium_gap"],
        },
        "accepted": {
            "build_label": build_label,
            "publication_id": publication_id,
            "final_epub": repo_relative_path(accepted_final),
            "score": latest_iteration["accepted_quality"]["weighted_score"],
            "premium_gap": latest_iteration["accepted_quality"]["premium_gap"],
        },
        "next_task": latest_iteration["next_task"],
        "report_path": repo_relative_path(_report_path_for(publication_id)),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    _write_json(_report_path_for(publication_id), latest_iteration)
    _write_json(STATE_PATH, state_payload)
    _write_json(RUNTIME_STATE_PATH, state_payload)
    return latest_iteration


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kindle Master autonomous quality loop")
    parser.add_argument("--publication-id", required=True, help="Manifest-backed active publication identifier")
    parser.add_argument("--max-iterations", type=int, default=20, help="Maximum loop iterations for this run")
    parser.add_argument("--target-score", type=float, default=8.8, help="Premium quality target score")
    parser.add_argument(
        "--guard-publications",
        default=",".join(DEFAULT_GUARDS),
        help="Comma-separated manifest publication ids to track as non-regression guards",
    )
    parser.add_argument("--stop-on-human-blocker", action="store_true", help="Stop if a real human blocker is already known")
    parser.add_argument("--resume-last", action="store_true", help="Resume iteration numbering from the last quality-loop state file")
    args = parser.parse_args()

    guard_ids = [item.strip() for item in args.guard_publications.split(",") if item.strip()]
    report = run_quality_loop(
        publication_id=args.publication_id,
        max_iterations=args.max_iterations,
        target_score=args.target_score,
        guard_publications=guard_ids,
        stop_on_human_blocker=args.stop_on_human_blocker,
        resume_last=args.resume_last,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
