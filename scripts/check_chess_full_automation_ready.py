from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_JSON = Path("reports/chess_full_automation_ready.json")
DEFAULT_OUTPUT_MD = Path("reports/chess_full_automation_ready.md")
DEFAULT_CORPUS_GATE_JSON = Path("reports/corpus/corpus_gate.json")
DEFAULT_FEN_CORPUS_JSON = Path("reports/corpus/fen_corpus_90.json")
DEFAULT_PGN_AUDIT_JSON = Path("reports/chess_audit/latest/audit_summary.json")
DEFAULT_PGN_INTAKE_SUMMARY_JSON = Path("reports/chess_fen/pgn_ground_truth_intake/audit_2026_06/pgn_ground_truth_intake_summary.json")
DEFAULT_NEGATIVE_INTAKE_SUMMARY_JSON = Path("reports/chess_fen/negative_sample_intake/audit_2026_06/negative_sample_intake_summary.json")
DEFAULT_PYTHON_CHESS_STATUS_JSON = Path("reports/python_chess_status.json")
DEFAULT_READING_ORDER_JSON = Path("reports/html_reading_order_report.json")
DEFAULT_AUTO_STRICT_VALIDATION_JSON = Path("reports/auto_strict_validation.json")
DEFAULT_EPUB_VALIDATION_JSON = Path("reports/epub_validation.json")


def check_chess_full_automation_ready(
    *,
    corpus_gate_path: str | Path | None = None,
    fen_corpus_path: str | Path | None = None,
    profile_readiness_paths: list[str | Path] | None = None,
    holdout_eval_paths: list[str | Path] | None = None,
    accepted_audit_summary_paths: list[str | Path] | None = None,
    pgn_eval_path: str | Path | None = None,
    pgn_intake_summary_path: str | Path | None = None,
    negative_intake_summary_path: str | Path | None = None,
    reading_order_audit_path: str | Path | None = None,
    auto_strict_validation_path: str | Path | None = None,
    python_chess_status_path: str | Path | None = None,
    epub_validation_path: str | Path | None = None,
    output_json: str | Path = DEFAULT_OUTPUT_JSON,
    output_md: str | Path = DEFAULT_OUTPUT_MD,
    min_profile_count: int = 2,
    min_valid_labels_per_profile: int = 20,
    min_exact_fen_accuracy: float = 0.90,
    min_fen_audit_case_count: int = 20,
    min_pgn_case_count: int = 1,
    min_negative_sample_count: int = 1,
) -> dict[str, Any]:
    corpus_gate_path = _default_single_evidence(corpus_gate_path, DEFAULT_CORPUS_GATE_JSON)
    fen_corpus_path = _default_single_evidence(fen_corpus_path, DEFAULT_FEN_CORPUS_JSON)
    if profile_readiness_paths is None:
        profile_readiness_paths = _default_evidence_glob("reports/chess_fen/evals/*profile_ready*.json")
    if holdout_eval_paths is None:
        holdout_eval_paths = _default_evidence_glob("reports/chess_fen/evals/*holdout*.json")
    if accepted_audit_summary_paths is None:
        accepted_audit_summary_paths = _dedupe_paths(
            _default_evidence_glob("reports/chess_fen/*accepted_audit_summary.json")
            + _default_evidence_glob("reports/chess_fen/**/*accepted_audit_summary.json")
        )
    pgn_eval_path = _default_single_evidence(pgn_eval_path, DEFAULT_PGN_AUDIT_JSON)
    pgn_intake_summary_path = _default_single_evidence(pgn_intake_summary_path, DEFAULT_PGN_INTAKE_SUMMARY_JSON)
    negative_intake_summary_path = _default_single_evidence(negative_intake_summary_path, DEFAULT_NEGATIVE_INTAKE_SUMMARY_JSON)
    reading_order_audit_path = _default_single_evidence(reading_order_audit_path, DEFAULT_READING_ORDER_JSON)
    if not reading_order_audit_path:
        reading_order_audit_path = _first_existing(_default_evidence_glob("reports/**/html_reading_order_report.json"))
    auto_strict_validation_path = _default_single_evidence(auto_strict_validation_path, DEFAULT_AUTO_STRICT_VALIDATION_JSON)
    if not auto_strict_validation_path:
        auto_strict_validation_path = _first_existing(_default_evidence_glob("reports/**/*auto_strict*.json"))
    python_chess_status_path = _default_single_evidence(python_chess_status_path, DEFAULT_PYTHON_CHESS_STATUS_JSON)
    epub_validation_path = _default_single_evidence(epub_validation_path, DEFAULT_EPUB_VALIDATION_JSON)
    if not epub_validation_path:
        epub_validation_path = _first_existing(_default_evidence_glob("reports/**/*epub*validation*.json"))

    evidence = {
        "corpus_gate": _load_evidence(corpus_gate_path),
        "fen_corpus": _load_evidence(fen_corpus_path),
        "profile_readiness": [_load_evidence(path) for path in profile_readiness_paths or []],
        "holdout_evals": [_load_evidence(path) for path in holdout_eval_paths or []],
        "accepted_audits": [_load_evidence(path) for path in accepted_audit_summary_paths or []],
        "pgn_eval": _load_evidence(pgn_eval_path),
        "pgn_intake_summary": _load_evidence(pgn_intake_summary_path),
        "negative_intake_summary": _load_evidence(negative_intake_summary_path),
        "reading_order_audit": _load_evidence(reading_order_audit_path),
        "auto_strict_validation": _load_evidence(auto_strict_validation_path),
        "python_chess": _load_evidence(python_chess_status_path),
        "epub_validation": _load_evidence(epub_validation_path),
    }
    checks: list[dict[str, Any]] = []
    next_actions: list[str] = []

    _check_corpus_gate(evidence["corpus_gate"], checks, next_actions)
    _check_fen_corpus(
        evidence["fen_corpus"],
        checks,
        next_actions,
        min_profile_count=min_profile_count,
        min_valid_labels_per_profile=min_valid_labels_per_profile,
        min_exact_fen_accuracy=min_exact_fen_accuracy,
    )
    _check_profile_readiness(evidence["profile_readiness"], checks, next_actions, min_valid_labels_per_profile)
    _check_fen_evidence_consistency(evidence["fen_corpus"], evidence["profile_readiness"], checks, next_actions)
    _check_holdouts(evidence["holdout_evals"], checks, next_actions, min_exact_fen_accuracy)
    _check_accepted_audits(evidence["accepted_audits"], checks, next_actions)
    _check_fen_audit(evidence["pgn_eval"], checks, next_actions, min_fen_audit_case_count)
    _check_audit_dataset_release_readiness(evidence["pgn_eval"], checks, next_actions)
    _check_pgn_cases(evidence["pgn_eval"], checks, next_actions, min_pgn_case_count, evidence["pgn_intake_summary"])
    _check_pgn_eval(evidence["pgn_eval"], checks, next_actions)
    _check_negative_samples(evidence["pgn_eval"], checks, next_actions, min_negative_sample_count, evidence["negative_intake_summary"])
    _check_reading_order(evidence["reading_order_audit"], checks, next_actions)
    _check_auto_strict(evidence["auto_strict_validation"], checks, next_actions)
    _check_python_chess(evidence["python_chess"], checks, next_actions)
    _check_epub_validation(evidence["epub_validation"], checks, next_actions)
    _check_no_ai_authority(evidence, checks, next_actions)
    _check_side_to_move_policy(evidence, checks, next_actions)

    blockers = [check for check in checks if check["status"] == "failed"]
    payload = {
        "schema_version": "kindlemaster.chess_full_automation_ready.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "question": "Can KindleMaster claim full chess FEN/PGN automation for release?",
        "status": "passed" if not blockers else "failed",
        "release_ready": not blockers,
        "answer": "yes" if not blockers else "no",
        "checks": checks,
        "blockers": blockers,
        "next_required_actions": _dedupe(next_actions),
        "metrics": _automation_metrics(evidence),
        "evidence": _evidence_summary(evidence),
        "pass_conditions": {
            "min_real_scanned_fen_profiles": min_profile_count,
            "min_valid_human_verified_labels_per_profile": min_valid_labels_per_profile,
            "min_exact_fen_accuracy": min_exact_fen_accuracy,
            "min_fen_audit_cases": min_fen_audit_case_count,
            "min_pgn_cases": min_pgn_case_count,
            "min_negative_samples": min_negative_sample_count,
            "false_positive_count": 0,
            "accepted_fen_audit_high_or_critical": 0,
            "pgn_strict_export_replay_accepted_only": True,
            "python_chess_available": True,
        },
    }
    _write_outputs(payload, Path(output_json), Path(output_md))
    return payload


def _default_single_evidence(path: str | Path | None, default_path: Path) -> str | Path | None:
    if path:
        return path
    return default_path if default_path.exists() else None


def _default_evidence_glob(pattern: str) -> list[Path]:
    return sorted(path for path in Path().glob(pattern) if path.is_file())


def _first_existing(paths: list[str | Path]) -> str | Path | None:
    for path in paths:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    return None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _load_evidence(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"available": False, "path": "", "payload": {}}
    resolved = Path(path)
    if not resolved.exists():
        return {"available": False, "path": str(resolved), "payload": {}}
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except Exception as error:
        return {"available": False, "path": str(resolved), "payload": {}, "error": str(error)}
    return {"available": isinstance(payload, dict), "path": str(resolved), "payload": payload if isinstance(payload, dict) else {}}


def _check_corpus_gate(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    ok = evidence["available"] and _status_passed(payload.get("overall_status") or payload.get("status"))
    _add_check(checks, "corpus_gate_passed", ok, evidence, "Corpus gate output must pass.")
    if not ok:
        next_actions.append("Run corpus gate with standard/full proof profile and resolve failures.")


def _check_fen_corpus(
    evidence: dict[str, Any],
    checks: list[dict[str, Any]],
    next_actions: list[str],
    *,
    min_profile_count: int,
    min_valid_labels_per_profile: int,
    min_exact_fen_accuracy: float,
) -> None:
    payload = evidence["payload"]
    evaluated = int(payload.get("evaluated_case_count") or payload.get("evaluated_profile_count") or len(payload.get("cases") or []))
    missing = max(0, min_profile_count - evaluated)
    _add_check(
        checks,
        "fen_corpus_has_two_real_scanned_profiles",
        evidence["available"] and evaluated >= min_profile_count and int(payload.get("missing_profile_count") or 0) == 0,
        evidence,
        f"Standard/full release proof requires at least {min_profile_count} real scanned FEN profiles.",
        evaluated_profiles=evaluated,
        missing_profiles=missing,
    )
    if missing:
        next_actions.append(f"Add {missing} second real scanned chess FEN profile(s) with at least {min_valid_labels_per_profile} human-verified labels.")

    cases = [case for case in payload.get("cases") or [] if isinstance(case, dict)]
    label_counts = [_valid_label_count(case) for case in cases]
    labels_ok = bool(cases) and all(count >= min_valid_labels_per_profile for count in label_counts)
    _add_check(
        checks,
        "fen_profiles_have_min_human_verified_labels",
        evidence["available"] and labels_ok,
        evidence,
        f"Every FEN profile must have >= {min_valid_labels_per_profile} valid human-verified labels.",
        valid_label_counts=label_counts,
    )
    if not labels_ok:
        next_actions.append("Complete manual FEN label verification until every profile has at least 20 valid labels.")

    exact_accuracy = _float(payload.get("overall_exact_fen_accuracy"), default=None)
    exact_ok = exact_accuracy is not None and exact_accuracy >= min_exact_fen_accuracy
    false_positive_count = int(payload.get("total_false_positive_count") or 0)
    _add_check(checks, "fen_exact_accuracy_gate", evidence["available"] and exact_ok, evidence, "FEN exact accuracy must be >= 0.90.", exact_fen_accuracy=exact_accuracy)
    _add_check(checks, "fen_false_positive_gate", evidence["available"] and false_positive_count == 0, evidence, "FEN false positive count must be zero.", false_positive_count=false_positive_count)


def _check_profile_readiness(items: list[dict[str, Any]], checks: list[dict[str, Any]], next_actions: list[str], min_labels: int) -> None:
    ok = bool(items) and all(item["available"] and _status_passed(item["payload"].get("status")) and bool(item["payload"].get("accepted_for_corpus")) for item in items)
    _add_check(checks, "profile_readiness_passed", ok, {"path": [item["path"] for item in items]}, "Every profile readiness report must pass and be accepted for corpus.")
    if not ok:
        next_actions.append(f"Run profile readiness for every FEN profile with holdout and accepted audit evidence; each needs >= {min_labels} valid labels.")


def _check_fen_evidence_consistency(
    fen_corpus: dict[str, Any],
    profile_readiness_items: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    next_actions: list[str],
) -> None:
    corpus_payload = fen_corpus.get("payload") if isinstance(fen_corpus, dict) else {}
    corpus_cases = [case for case in corpus_payload.get("cases") or [] if isinstance(case, dict)] if isinstance(corpus_payload, dict) else []
    corpus_counts = [_valid_label_count(case) for case in corpus_cases]
    profile_payloads = [item.get("payload", {}) for item in profile_readiness_items if isinstance(item, dict) and item.get("available")]
    profile_counts = [_profile_valid_label_count(profile) for profile in profile_payloads if isinstance(profile, dict)]
    stale = bool(corpus_counts and profile_counts) and max(corpus_counts) > 0 and min(profile_counts) == 0
    ok = not stale
    _add_check(
        checks,
        "fen_profile_evidence_consistent",
        ok,
        {
            "path": {
                "fen_corpus": fen_corpus.get("path", "") if isinstance(fen_corpus, dict) else "",
                "profile_readiness": [item.get("path", "") for item in profile_readiness_items if isinstance(item, dict)],
            }
        },
        "FEN corpus evidence must not contradict current profile readiness label validation.",
        fen_corpus_valid_label_counts=corpus_counts,
        profile_readiness_valid_label_counts=profile_counts,
    )
    if not ok:
        next_actions.append("Regenerate FEN corpus evidence after current label validation/profile readiness; stale corpus proof cannot override failed profile readiness.")


def _check_holdouts(items: list[dict[str, Any]], checks: list[dict[str, Any]], next_actions: list[str], min_accuracy: float) -> None:
    ok = bool(items)
    false_positive_ok = True
    for item in items:
        payload = item["payload"]
        holdout = payload.get("holdout_eval") if isinstance(payload.get("holdout_eval"), dict) else payload
        ok = ok and item["available"] and _status_passed(payload.get("status")) and _status_passed(holdout.get("status"))
        ok = ok and _float(holdout.get("exact_fen_accuracy"), default=0.0) >= min_accuracy
        false_positive_ok = false_positive_ok and int(holdout.get("false_positive_count") or 0) == 0
    _add_check(checks, "holdout_evals_passed", ok, {"path": [item["path"] for item in items]}, "Every holdout eval must pass with exact_fen_accuracy >= 0.90.")
    _add_check(checks, "holdout_false_positive_gate", bool(items) and false_positive_ok, {"path": [item["path"] for item in items]}, "Every holdout false_positive_count must be zero.")
    if not ok or not false_positive_ok:
        next_actions.append("Generate passing holdout eval reports for every FEN profile.")


def _check_accepted_audits(items: list[dict[str, Any]], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    ok = bool(items)
    for item in items:
        payload = item["payload"]
        ok = ok and item["available"] and str(payload.get("status") or "") in {"ok", "not_applicable"}
        ok = ok and int(payload.get("critical_risk_count") or 0) == 0 and int(payload.get("high_risk_count") or 0) == 0
    _add_check(checks, "accepted_fen_audit_zero_high_or_critical", ok, {"path": [item["path"] for item in items]}, "Accepted FEN audit must have zero critical/high risks.")
    if not ok:
        next_actions.append("Resolve accepted FEN audit critical/high risks or move records back to review.")


def _check_fen_audit(
    evidence: dict[str, Any],
    checks: list[dict[str, Any]],
    next_actions: list[str],
    min_fen_audit_case_count: int,
) -> None:
    payload = evidence["payload"]
    fen = payload.get("fen") if isinstance(payload.get("fen"), dict) else {}
    case_count = int(fen.get("case_count") or 0)
    top_blockers = fen.get("top_blockers") if isinstance(fen.get("top_blockers"), dict) else {}
    blocker_coverage = sum(int(value or 0) for value in top_blockers.values())
    enough_cases = evidence["available"] and case_count >= min_fen_audit_case_count
    _add_check(
        checks,
        "fen_audit_cases_evaluated",
        enough_cases,
        evidence,
        f"Release proof must include at least {min_fen_audit_case_count} FEN diagnostic audit case(s).",
        fen_audit_case_count=case_count,
        fen_audit_diagram_detected_count=int(fen.get("diagram_detected_count") or 0),
    )
    if not enough_cases:
        next_actions.append("Populate the FEN audit dataset with enough human-verified diagnostic cases and rerun the pipeline audit.")
    blockers_known = evidence["available"] and case_count > 0 and blocker_coverage >= case_count
    _add_check(
        checks,
        "fen_audit_top_blockers_known",
        blockers_known,
        evidence,
        "Every FEN audit case must have a top blocker or accepted outcome represented in the audit summary.",
        fen_audit_case_count=case_count,
        fen_audit_top_blocker_coverage=blocker_coverage,
    )
    if not blockers_known:
        next_actions.append("Rerun or fix the FEN pipeline audit so every diagnostic FEN case has a top blocker.")


def _check_audit_dataset_release_readiness(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    readiness = payload.get("dataset_release_readiness") if isinstance(payload.get("dataset_release_readiness"), dict) else {}
    blockers = [blocker for blocker in readiness.get("blockers") or [] if isinstance(blocker, dict)]
    ok = evidence["available"] and readiness.get("accepted_for_release_proof") is True and not blockers
    _add_check(
        checks,
        "audit_dataset_release_ready",
        ok,
        evidence,
        "Audit dataset must be explicitly accepted for release proof, not only schema-valid.",
        dataset_release_status=str(readiness.get("status") or "missing"),
        dataset_release_blocker_codes=[str(blocker.get("code") or "") for blocker in blockers],
    )
    if ok:
        return
    if blockers:
        next_actions.append("Resolve audit dataset release-readiness blockers: " + ", ".join(str(blocker.get("code") or "unknown") for blocker in blockers) + ".")
    else:
        next_actions.append("Regenerate pipeline audit with dataset_release_readiness evidence before claiming release readiness.")


def _check_pgn_cases(
    evidence: dict[str, Any],
    checks: list[dict[str, Any]],
    next_actions: list[str],
    min_pgn_case_count: int,
    intake_evidence: dict[str, Any] | None = None,
) -> None:
    payload = evidence["payload"]
    pgn = payload.get("pgn") if isinstance(payload.get("pgn"), dict) else {}
    case_count = int(pgn.get("case_count") or 0)
    feasible_count = int(pgn.get("feasible_count") or 0)
    infeasible_count = int(pgn.get("infeasible_count") or 0)
    ok = evidence["available"] and case_count >= min_pgn_case_count
    _add_check(
        checks,
        "pgn_cases_evaluated",
        ok,
        evidence,
        f"Release proof must include at least {min_pgn_case_count} human-reviewed PGN feasibility case(s).",
        pgn_case_count=case_count,
        pgn_feasible_count=feasible_count,
        pgn_infeasible_count=infeasible_count,
    )
    if not ok:
        next_actions.append(_pgn_intake_next_action(intake_evidence))


def _check_pgn_eval(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    metrics = _pgn_eval_metrics(payload)
    blocker_count = int(metrics.get("blocker_count") or 0)
    strict_only = bool(metrics.get("strict_export_replay_accepted_only", False))
    exported = int(metrics.get("exported_pgn_count") or 0)
    valid = int(metrics.get("valid_pgn_count") or 0)
    ok = evidence["available"] and _status_passed(payload.get("status")) and blocker_count == 0 and strict_only and exported == valid
    _add_check(
        checks,
        "pgn_strict_export_replay_accepted_only",
        ok,
        evidence,
        "PGN strict export must contain only parser/replay accepted records.",
        exported_pgn_count=exported,
        valid_pgn_count=valid,
        blocker_count=blocker_count,
        feasible_pgn_count=int(metrics.get("feasible_pgn_count") or 0),
    )
    if not ok:
        next_actions.append("Run PGN replay/auto-repair eval and ensure strict export contains only replay-accepted records.")


def _check_negative_samples(
    evidence: dict[str, Any],
    checks: list[dict[str, Any]],
    next_actions: list[str],
    min_negative_sample_count: int,
    intake_evidence: dict[str, Any] | None = None,
) -> None:
    payload = evidence["payload"]
    negative = payload.get("negative") if isinstance(payload.get("negative"), dict) else {}
    sample_count = int(negative.get("case_count") or 0)
    evaluable_count = int(negative.get("evaluable_count") or 0)
    runtime_false_positive_count = int(negative.get("false_positive_runtime_count") or 0)
    enough_samples = evidence["available"] and sample_count >= min_negative_sample_count and evaluable_count >= min_negative_sample_count
    _add_check(
        checks,
        "negative_samples_evaluated",
        enough_samples,
        evidence,
        f"Release proof must include at least {min_negative_sample_count} evaluable negative sample(s).",
        negative_sample_count=sample_count,
        negative_evaluable_count=evaluable_count,
    )
    if not enough_samples:
        next_actions.append(_negative_intake_next_action(intake_evidence))
    zero_runtime_false_positives = evidence["available"] and evaluable_count >= min_negative_sample_count and runtime_false_positive_count == 0
    _add_check(
        checks,
        "negative_runtime_false_positive_gate",
        zero_runtime_false_positives,
        evidence,
        "Negative samples must produce zero runtime accepted FEN false positives.",
        negative_sample_count=sample_count,
        negative_evaluable_count=evaluable_count,
        negative_runtime_false_positive_count=runtime_false_positive_count,
    )
    if not zero_runtime_false_positives:
        if evaluable_count < min_negative_sample_count:
            next_actions.append("Add evaluable negative samples before treating the negative false-positive gate as proven.")
        else:
            next_actions.append("Investigate negative-sample runtime false positives before claiming automation readiness.")


def _pgn_intake_next_action(intake_evidence: dict[str, Any] | None) -> str:
    payload = (intake_evidence or {}).get("payload") if isinstance(intake_evidence, dict) else {}
    if not isinstance(payload, dict) or not (intake_evidence or {}).get("available"):
        return "Fill PGN ground-truth intake rows and rerun the pipeline audit feasibility/replay checks."
    template = str(payload.get("template") or "").strip()
    candidate_review = str(payload.get("candidate_review") or "").strip()
    target = str(payload.get("target_pgn_ground_truth") or "").strip()
    counts = payload.get("candidate_counts") if isinstance(payload.get("candidate_counts"), dict) else {}
    rows = int(counts.get("rows") or payload.get("row_count") or 0)
    feasible = int(counts.get("feasible_suggested") or 0)
    parts = ["Fill PGN ground-truth intake rows"]
    if rows:
        parts.append(f"({rows} review candidate(s), {feasible} suggested feasible)")
    if template:
        parts.append(f"in {template}")
    if candidate_review:
        parts.append(f"using review evidence from {candidate_review}")
    if target:
        parts.append(
            "then run scripts/apply_chess_audit_dataset_intake.py as dry-run "
            f"and rerun it with --apply to merge human-verified rows into {target}"
        )
    parts.append("and rerun the pipeline audit feasibility/replay checks.")
    return " ".join(parts)


def _negative_intake_next_action(intake_evidence: dict[str, Any] | None) -> str:
    payload = (intake_evidence or {}).get("payload") if isinstance(intake_evidence, dict) else {}
    if not isinstance(payload, dict) or not (intake_evidence or {}).get("available"):
        return "Add real negative chess-like samples and rerun the pipeline audit false-positive check."
    template = str(payload.get("template") or "").strip()
    candidate_review = str(payload.get("candidate_review") or "").strip()
    target = str(payload.get("target_negative_samples") or "").strip()
    counts = payload.get("candidate_counts") if isinstance(payload.get("candidate_counts"), dict) else {}
    rows = int(counts.get("rows") or payload.get("row_count") or 0)
    candidate_crops = int(counts.get("with_candidate_crop_path") or 0)
    canonical_crops = int(counts.get("with_canonical_crop_path") or 0)
    parts = ["Add real negative chess-like samples"]
    if rows:
        parts.append(f"({rows} review candidate(s), {candidate_crops} candidate crop(s), {canonical_crops} canonical crop(s))")
    if template:
        parts.append(f"in {template}")
    if candidate_review:
        parts.append(f"using review evidence from {candidate_review}")
    if target:
        parts.append(
            "then run scripts/apply_chess_audit_dataset_intake.py as dry-run "
            f"and rerun it with --apply to merge human-verified rows into {target}"
        )
    parts.append("and rerun the pipeline audit false-positive check.")
    return " ".join(parts)


def _check_reading_order(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    warnings = [warning for warning in payload.get("warnings") or [] if isinstance(warning, dict)]
    high_count = int(payload.get("high_severity_warning_count") or sum(1 for warning in warnings if warning.get("severity") == "high"))
    ok = evidence["available"] and high_count == 0
    _add_check(checks, "reading_order_no_high_severity_warnings", ok, evidence, "Reading-order audit must have no high severity warnings.", high_severity_warning_count=high_count)
    if not ok:
        next_actions.append("Resolve high severity reading-order audit warnings.")


def _check_auto_strict(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    status = payload.get("status") or payload.get("overall_status")
    ok = evidence["available"] and _status_passed(status) and bool(payload.get("release_ready", True))
    _add_check(checks, "auto_strict_validation_passed", ok, evidence, "Auto-strict validation report must pass.")
    if not ok:
        next_actions.append("Run and pass auto-strict validation.")


def _check_python_chess(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    nested = payload.get("python_chess") if isinstance(payload.get("python_chess"), dict) else payload
    ok = evidence["available"] and bool(nested.get("available", payload.get("available", False)))
    _add_check(checks, "python_chess_available", ok, evidence, "python-chess and chess.pgn must be available.")
    if not ok:
        next_actions.append("Install/bootstrap python-chess before claiming strict PGN/FEN automation.")


def _check_epub_validation(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    ok = evidence["available"] and _status_passed(payload.get("status") or payload.get("overall_status") or payload.get("validation_status"))
    _add_check(checks, "epub_validation_passed", ok, evidence, "EPUB validation status must pass.")
    if not ok:
        next_actions.append("Run EPUB validation and resolve structural blockers.")


def _check_no_ai_authority(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    bad_markers = ("ai_verified", "arbiter_verified", "high_confidence_only", "approved_without_human", "ai_suggested_fen_promoted")
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    ok = not any(marker in serialized for marker in bad_markers)
    _add_check(checks, "no_ai_or_arbiter_authority_path", ok, {"path": "all_evidence"}, "AI/arbiter/high-confidence-only evidence must never create verified/corpus/runtime accepted output.")
    if not ok:
        next_actions.append("Remove AI/arbiter/high-confidence-only paths from verified/corpus/runtime authority.")


def _check_side_to_move_policy(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    serialized = json.dumps(evidence, ensure_ascii=False).lower()
    unsafe = (
        "side_to_move_inferred_accepted" in serialized
        or "accepted_inferred_side_to_move" in serialized
        or _has_accepted_inferred_side_to_move(evidence)
    )
    ok = not unsafe
    _add_check(checks, "side_to_move_inferred_not_full_fen_accepted", ok, {"path": "all_evidence"}, "Side-to-move inferred must not be accepted as full FEN without explicit evidence.")
    if not ok:
        next_actions.append("Move inferred side-to-move FEN records back to review or add explicit marker/caption evidence.")


def _add_check(checks: list[dict[str, Any]], check_id: str, ok: bool, evidence: dict[str, Any], message: str, **extra: Any) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "passed" if ok else "failed",
            "message": message,
            "evidence_path": evidence.get("path", ""),
            **extra,
        }
    )


def _valid_label_count(case: dict[str, Any]) -> int:
    validation = case.get("label_validation") if isinstance(case.get("label_validation"), dict) else {}
    return int(validation.get("valid_label_count") or case.get("valid_label_count") or 0)


def _profile_valid_label_count(profile: dict[str, Any]) -> int:
    label_validation = profile.get("label_validation") if isinstance(profile.get("label_validation"), dict) else {}
    breakdown = profile.get("profile_readiness_breakdown") if isinstance(profile.get("profile_readiness_breakdown"), dict) else {}
    return int(profile.get("valid_label_count") or label_validation.get("valid_label_count") or breakdown.get("valid_label_count") or 0)


def _float(value: Any, *, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _status_passed(value: Any) -> bool:
    return str(value or "").strip().lower() in {"ok", "pass", "passed", "ready"}


def _pgn_eval_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    pgn_breakdown = summary.get("pgn_breakdown") if isinstance(summary.get("pgn_breakdown"), dict) else {}
    top_blocker_counts = (
        pgn_breakdown.get("top_blocker_counts")
        if isinstance(pgn_breakdown.get("top_blocker_counts"), dict)
        else summary.get("top_blocker_counts")
        if isinstance(summary.get("top_blocker_counts"), dict)
        else {}
    )
    blocker_count = int(
        payload.get("pgn_replay_errors")
        or payload.get("replay_error_count")
        or payload.get("strict_export_blocker_count")
        or top_blocker_counts.get("pgn_replay_failed")
        or summary.get("failed")
        or pgn_breakdown.get("failed_feasible_records")
        or 0
    )
    exported = int(
        payload.get("exported_pgn_count")
        or payload.get("valid_pgn_count")
        or summary.get("runtime_machine_accepted")
        or pgn_breakdown.get("machine_accepted")
        or pgn_breakdown.get("accepted")
        or 0
    )
    valid = int(payload.get("valid_pgn_count") or pgn_breakdown.get("accepted") or exported)
    strict_only = bool(payload.get("strict_export_replay_accepted_only", False))
    if payload.get("schema") == "kindlemaster.auto_chess.pgn_validation.v1":
        strict_only = True
    return {
        "blocker_count": blocker_count,
        "exported_pgn_count": exported,
        "valid_pgn_count": valid,
        "feasible_pgn_count": int(pgn_breakdown.get("feasible_records") or summary.get("pgn_feasible_count") or 0),
        "strict_export_replay_accepted_only": strict_only,
    }


def _has_accepted_inferred_side_to_move(value: Any) -> bool:
    if isinstance(value, dict):
        status = str(value.get("runtime_status") or value.get("status") or "").lower()
        accepted = status in {
            "accepted",
            "fen_machine_accepted",
            "fen_corpus_verified",
            "fen_auto_accepted",
            "fen_full_machine_accepted",
        }
        side_source = str(value.get("side_to_move_source") or value.get("side_to_move_evidence") or value.get("side_to_move_status") or "").lower()
        warnings = [str(item).lower() for item in value.get("warnings") or []] if isinstance(value.get("warnings"), list) else []
        if accepted and (side_source == "inferred" or "side_to_move_inferred" in warnings):
            return True
        return any(_has_accepted_inferred_side_to_move(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_accepted_inferred_side_to_move(item) for item in value)
    return False


def _automation_metrics(evidence: dict[str, Any]) -> dict[str, Any]:
    fen_corpus = evidence.get("fen_corpus", {}).get("payload", {})
    pgn_eval = evidence.get("pgn_eval", {}).get("payload", {})
    pgn_metrics = _pgn_eval_metrics(pgn_eval if isinstance(pgn_eval, dict) else {})
    audit_fen = pgn_eval.get("fen") if isinstance(pgn_eval.get("fen"), dict) else {}
    audit_pgn = pgn_eval.get("pgn") if isinstance(pgn_eval.get("pgn"), dict) else {}
    audit_negative = pgn_eval.get("negative") if isinstance(pgn_eval.get("negative"), dict) else {}
    pgn_intake_payload = evidence.get("pgn_intake_summary", {}).get("payload", {})
    negative_intake_payload = evidence.get("negative_intake_summary", {}).get("payload", {})
    pgn_intake_counts = pgn_intake_payload.get("candidate_counts") if isinstance(pgn_intake_payload.get("candidate_counts"), dict) else {}
    negative_intake_counts = negative_intake_payload.get("candidate_counts") if isinstance(negative_intake_payload.get("candidate_counts"), dict) else {}
    dataset_readiness = pgn_eval.get("dataset_release_readiness") if isinstance(pgn_eval.get("dataset_release_readiness"), dict) else {}
    dataset_readiness_blockers = [
        str(blocker.get("code") or "")
        for blocker in dataset_readiness.get("blockers") or []
        if isinstance(blocker, dict)
    ]
    profile_readiness = [item.get("payload", {}) for item in evidence.get("profile_readiness", []) if isinstance(item, dict)]
    return {
        "fen_profile_count": int(fen_corpus.get("evaluated_case_count") or fen_corpus.get("evaluated_profile_count") or len(fen_corpus.get("cases") or [])),
        "fen_overall_exact_accuracy": _float(fen_corpus.get("overall_exact_fen_accuracy"), default=0.0),
        "fen_total_false_positive_count": int(fen_corpus.get("total_false_positive_count") or 0),
        "fen_audit_case_count": int(audit_fen.get("case_count") or 0),
        "fen_audit_diagram_detected_count": int(audit_fen.get("diagram_detected_count") or 0),
        "fen_audit_crop_present_count": int(audit_fen.get("crop_present_count") or audit_fen.get("crop_available_count") or 0),
        "fen_audit_crop_correct_evidence_count": int(audit_fen.get("crop_correct_evidence_count") or 0),
        "fen_audit_crop_correct_known_count": int(audit_fen.get("crop_correct_known_count") or 0),
        "fen_audit_grid_measured_count": int(audit_fen.get("grid_measured_count") or 0),
        "fen_audit_grid_correct_known_count": int(audit_fen.get("grid_correct_known_count") or 0),
        "fen_audit_grid_confidence_average": audit_fen.get("grid_confidence_average"),
        "fen_audit_placement_exact_count": int(audit_fen.get("placement_exact_count") or 0),
        "fen_audit_full_fen_syntax_valid_count": int(audit_fen.get("full_fen_syntax_valid_count") or 0),
        "fen_audit_full_fen_legal_valid_count": int(audit_fen.get("full_fen_legal_valid_count") or 0),
        "fen_audit_runtime_fen_present_count": int(audit_fen.get("runtime_fen_present_count") or 0),
        "fen_audit_runtime_accepted_count": int(audit_fen.get("runtime_accepted_count") or audit_fen.get("runtime_fen_accepted_count") or audit_fen.get("runtime_fen_present_count") or 0),
        "profile_readiness_count": len(profile_readiness),
        "profile_valid_label_counts": [
            int(profile.get("valid_label_count") or (profile.get("label_validation") or {}).get("valid_label_count") or 0)
            for profile in profile_readiness
            if isinstance(profile, dict)
        ],
        "pgn_audit_case_count": int(audit_pgn.get("case_count") or 0),
        "pgn_audit_infeasible_count": int(audit_pgn.get("infeasible_count") or 0),
        "pgn_audit_ocr_text_present_count": int(audit_pgn.get("ocr_text_present_count") or 0),
        "pgn_audit_candidate_blocks_found_count": int(audit_pgn.get("candidate_blocks_found_count") or 0),
        "pgn_audit_san_tokens_present_count": int(audit_pgn.get("san_tokens_present_count") or 0),
        "pgn_audit_san_token_count": int(audit_pgn.get("san_token_count") or 0),
        "pgn_audit_parse_clean_count": int(audit_pgn.get("parse_clean_count") or 0),
        "pgn_audit_replay_legal_count": int(audit_pgn.get("replay_legal_count") or 0),
        "pgn_audit_final_fen_present_count": int(audit_pgn.get("final_fen_present_count") or 0),
        "pgn_feasible_count": int(pgn_metrics.get("feasible_pgn_count") or audit_pgn.get("feasible_count") or 0),
        "pgn_valid_count": int(pgn_metrics.get("valid_pgn_count") or audit_pgn.get("replay_legal_count") or 0),
        "pgn_exported_count": int(pgn_metrics.get("exported_pgn_count") or audit_pgn.get("exportable_count") or 0),
        "pgn_blocker_count": int(pgn_metrics.get("blocker_count") or 0),
        "pgn_intake_candidate_count": int(pgn_intake_counts.get("rows") or pgn_intake_payload.get("row_count") or 0),
        "pgn_intake_feasible_suggested_count": int(pgn_intake_counts.get("feasible_suggested") or 0),
        "pgn_intake_candidate_movetext_count": int(pgn_intake_counts.get("with_candidate_movetext") or 0),
        "negative_sample_count": int(audit_negative.get("case_count") or 0),
        "negative_evaluable_count": int(audit_negative.get("evaluable_count") or 0),
        "negative_false_positive_candidate_count": int(audit_negative.get("false_positive_candidate_count") or 0),
        "negative_false_positive_runtime_count": int(audit_negative.get("false_positive_runtime_count") or 0),
        "negative_intake_candidate_count": int(negative_intake_counts.get("rows") or negative_intake_payload.get("row_count") or 0),
        "negative_intake_candidate_crop_count": int(negative_intake_counts.get("with_candidate_crop_path") or 0),
        "negative_intake_canonical_crop_count": int(negative_intake_counts.get("with_canonical_crop_path") or 0),
        "audit_dataset_release_status": str(dataset_readiness.get("status") or "missing"),
        "audit_dataset_accepted_for_release_proof": bool(dataset_readiness.get("accepted_for_release_proof", False)),
        "audit_dataset_release_blockers": dataset_readiness_blockers,
    }


def _evidence_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in evidence.items():
        if isinstance(value, list):
            summary[key] = [{"available": item.get("available", False), "path": item.get("path", "")} for item in value]
        else:
            summary[key] = {"available": value.get("available", False), "path": value.get("path", "")}
    return summary


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _write_outputs(payload: dict[str, Any], output_json: Path, output_md: Path) -> None:
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(build_chess_full_automation_ready_markdown(payload), encoding="utf-8")


def build_chess_full_automation_ready_markdown(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics") or {}
    fen_cases = int(metrics.get("fen_audit_case_count") or 0)
    fen_diagram_detected = int(metrics.get("fen_audit_diagram_detected_count") or 0)
    fen_crop_present = int(metrics.get("fen_audit_crop_present_count") or 0)
    fen_crop_evidence = int(metrics.get("fen_audit_crop_correct_evidence_count") or 0)
    fen_crop_known = int(metrics.get("fen_audit_crop_correct_known_count") or 0)
    fen_grid_measured = int(metrics.get("fen_audit_grid_measured_count") or 0)
    fen_grid_known = int(metrics.get("fen_audit_grid_correct_known_count") or 0)
    fen_placement = int(metrics.get("fen_audit_placement_exact_count") or 0)
    fen_full_syntax = int(metrics.get("fen_audit_full_fen_syntax_valid_count") or 0)
    fen_runtime = int(metrics.get("fen_audit_runtime_fen_present_count") or 0)
    fen_runtime_accepted = int(metrics.get("fen_audit_runtime_accepted_count") or 0)
    pgn_cases = int(metrics.get("pgn_audit_case_count") or 0)
    pgn_feasible = int(metrics.get("pgn_feasible_count") or 0)
    pgn_exported = int(metrics.get("pgn_exported_count") or 0)
    release_ready = bool(payload.get("release_ready"))
    fen_placement_automatic = fen_cases > 0 and fen_placement == fen_cases
    full_fen_automatic = release_ready and fen_cases > 0 and fen_runtime_accepted == fen_cases
    pgn_automatic = release_ready and pgn_feasible > 0 and pgn_exported == pgn_feasible
    decision = "merge" if release_ready else "no merge"
    blocker_summary = _primary_blocker_summary(payload)
    lines = [
        "# FEN/PGN Automatic Readiness Report",
        "",
        f"- Question: {payload['question']}",
        f"- Answer: `{payload['answer']}`",
        f"- Status: `{payload['status']}`",
        f"- Generated at: `{payload['generated_at']}`",
        "",
        "## Executive summary",
        "",
        f"- FEN placement automatic: {'yes' if fen_placement_automatic else 'no'}",
        f"- Full FEN automatic: {'yes' if full_fen_automatic else 'no'}",
        f"- PGN automatic for feasible cases: {'yes' if pgn_automatic else 'no'}",
        "",
        "## Dataset",
        "",
        f"- FEN cases: `{fen_cases}`",
        f"- PGN feasible cases: `{pgn_feasible}`",
        f"- PGN infeasible cases: `{int(metrics.get('pgn_audit_infeasible_count') or 0)}`",
        f"- PGN intake candidates waiting for review: `{int(metrics.get('pgn_intake_candidate_count') or 0)}` "
        f"(`{int(metrics.get('pgn_intake_feasible_suggested_count') or 0)}` suggested feasible, "
        f"`{int(metrics.get('pgn_intake_candidate_movetext_count') or 0)}` with candidate movetext)",
        f"- negative samples: `{int(metrics.get('negative_sample_count') or 0)}`",
        f"- negative intake candidates waiting for review: `{int(metrics.get('negative_intake_candidate_count') or 0)}` "
        f"(`{int(metrics.get('negative_intake_candidate_crop_count') or 0)}` candidate crop(s), "
        f"`{int(metrics.get('negative_intake_canonical_crop_count') or 0)}` canonical crop(s))",
        "",
        "## FEN funnel",
        "",
        f"- diagram detected: `{fen_diagram_detected}`",
        f"- crop present: `{fen_crop_present}`",
        f"- crop correctness evidence: `{fen_crop_evidence}`",
        f"- crop correct verified: `{fen_crop_known}`",
        f"- grid correct: `{fen_grid_known}` verified (`{fen_grid_measured}` measured, avg confidence `{metrics.get('fen_audit_grid_confidence_average')}`)",
        f"- placement exact: `{fen_placement}`",
        f"- full FEN valid: `{fen_full_syntax}` syntax-valid",
        f"- runtime FEN present: `{fen_runtime}`",
        f"- runtime accepted: `{fen_runtime_accepted}`",
        f"- false accepted: `{metrics.get('fen_total_false_positive_count', 0)}`",
        "",
        "## PGN funnel",
        "",
        f"- feasible: `{pgn_feasible}`",
        f"- OCR text present: `{metrics.get('pgn_audit_ocr_text_present_count', 0)}`",
        f"- candidate blocks: `{metrics.get('pgn_audit_candidate_blocks_found_count', 0)}`",
        f"- SAN tokens: `{metrics.get('pgn_audit_san_token_count', 0)}` total (`{metrics.get('pgn_audit_san_tokens_present_count', 0)}` cases)",
        f"- parse clean: `{metrics.get('pgn_audit_parse_clean_count', 0)}`",
        f"- replay legal: `{metrics.get('pgn_audit_replay_legal_count', metrics.get('pgn_valid_count', 0))}`",
        f"- final FEN: `{metrics.get('pgn_audit_final_fen_present_count', 0)}`",
        f"- exportable: `{pgn_exported}`",
        "",
        "## Top blockers",
        "",
        f"- FEN blockers: `{_top_blockers(payload, 'fen')}`",
        f"- PGN blockers: `{_top_blockers(payload, 'pgn')}`",
        f"- negative blockers: `{_top_blockers(payload, 'negative')}`",
        "",
        "## Decision",
        "",
        f"- decision: `{decision}`",
        f"- summary: Mamy automatycznie rozpoznany `{_recognized_level(metrics)}`, ale blokuje nas `{blocker_summary}`.",
        "- next 3 actions:",
    ]
    for action in list(payload.get("next_required_actions") or [])[:3]:
        lines.append(f"  - {action}")
    lines.extend([
        "",
        "## Metrics",
        "",
    ])
    for key, value in metrics.items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend([
        "",
        "## Checks",
        "",
        "| Check | Status | Evidence |",
        "| --- | --- | --- |",
    ])
    for check in payload["checks"]:
        lines.append(f"| `{check['id']}` | `{check['status']}` | `{check.get('evidence_path', '')}` |")
    if payload["blockers"]:
        lines.extend(["", "## Blockers", ""])
        for blocker in payload["blockers"]:
            lines.append(f"- `{blocker['id']}`: {blocker['message']}")
    if payload["next_required_actions"]:
        lines.extend(["", "## Next Required Actions", ""])
        for action in payload["next_required_actions"]:
            lines.append(f"- {action}")
    return "\n".join(lines).rstrip() + "\n"


def _top_blockers(payload: dict[str, Any], kind: str) -> str:
    evidence = payload.get("evidence") or {}
    pgn_eval = evidence.get("pgn_eval") if isinstance(evidence.get("pgn_eval"), dict) else {}
    path = str(pgn_eval.get("path") or "")
    if not path:
        return "missing_evidence"
    try:
        audit = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return "unavailable"
    section = audit.get(kind) if isinstance(audit.get(kind), dict) else {}
    blockers = section.get("top_blockers") if isinstance(section.get("top_blockers"), dict) else {}
    return str(blockers or "none_reported")


def _primary_blocker_summary(payload: dict[str, Any]) -> str:
    blockers = payload.get("blockers") or []
    if not blockers:
        return "none"
    first = blockers[0] if isinstance(blockers[0], dict) else {}
    return str(first.get("id") or first.get("message") or "release_evidence_missing")


def _recognized_level(metrics: dict[str, Any]) -> str:
    if int(metrics.get("fen_audit_case_count") or 0) or int(metrics.get("pgn_audit_case_count") or 0):
        return "diagnostic_audit_scaffold"
    return "brak realnych przypadkow w audit dataset"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether KindleMaster can claim full chess FEN/PGN automation for release.")
    parser.add_argument("--corpus-gate", default="")
    parser.add_argument("--fen-corpus", default="")
    parser.add_argument("--profile-readiness", action="append", default=[])
    parser.add_argument("--holdout-eval", action="append", default=[])
    parser.add_argument("--accepted-audit-summary", action="append", default=[])
    parser.add_argument("--pgn-eval", default="")
    parser.add_argument("--pgn-intake-summary", default="")
    parser.add_argument("--negative-intake-summary", default="")
    parser.add_argument("--reading-order-audit", default="")
    parser.add_argument("--auto-strict-validation", default="")
    parser.add_argument("--python-chess-status", default="")
    parser.add_argument("--epub-validation", default="")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    parser.add_argument("--min-fen-audit-case-count", type=int, default=20)
    parser.add_argument("--min-pgn-case-count", type=int, default=1)
    parser.add_argument("--min-negative-sample-count", type=int, default=1)
    args = parser.parse_args()
    payload = check_chess_full_automation_ready(
        corpus_gate_path=args.corpus_gate or None,
        fen_corpus_path=args.fen_corpus or None,
        profile_readiness_paths=args.profile_readiness or None,
        holdout_eval_paths=args.holdout_eval or None,
        accepted_audit_summary_paths=args.accepted_audit_summary or None,
        pgn_eval_path=args.pgn_eval or None,
        pgn_intake_summary_path=args.pgn_intake_summary or None,
        negative_intake_summary_path=args.negative_intake_summary or None,
        reading_order_audit_path=args.reading_order_audit or None,
        auto_strict_validation_path=args.auto_strict_validation or None,
        python_chess_status_path=args.python_chess_status or None,
        epub_validation_path=args.epub_validation or None,
        output_json=args.output_json,
        output_md=args.output_md,
        min_fen_audit_case_count=args.min_fen_audit_case_count,
        min_pgn_case_count=args.min_pgn_case_count,
        min_negative_sample_count=args.min_negative_sample_count,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
