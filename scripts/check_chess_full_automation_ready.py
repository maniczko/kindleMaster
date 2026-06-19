from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_JSON = Path("reports/chess_full_automation_ready.json")
DEFAULT_OUTPUT_MD = Path("reports/chess_full_automation_ready.md")


def check_chess_full_automation_ready(
    *,
    corpus_gate_path: str | Path | None = None,
    fen_corpus_path: str | Path | None = None,
    profile_readiness_paths: list[str | Path] | None = None,
    holdout_eval_paths: list[str | Path] | None = None,
    accepted_audit_summary_paths: list[str | Path] | None = None,
    pgn_eval_path: str | Path | None = None,
    reading_order_audit_path: str | Path | None = None,
    auto_strict_validation_path: str | Path | None = None,
    python_chess_status_path: str | Path | None = None,
    epub_validation_path: str | Path | None = None,
    output_json: str | Path = DEFAULT_OUTPUT_JSON,
    output_md: str | Path = DEFAULT_OUTPUT_MD,
    min_profile_count: int = 2,
    min_valid_labels_per_profile: int = 20,
    min_exact_fen_accuracy: float = 0.90,
) -> dict[str, Any]:
    evidence = {
        "corpus_gate": _load_evidence(corpus_gate_path),
        "fen_corpus": _load_evidence(fen_corpus_path),
        "profile_readiness": [_load_evidence(path) for path in profile_readiness_paths or []],
        "holdout_evals": [_load_evidence(path) for path in holdout_eval_paths or []],
        "accepted_audits": [_load_evidence(path) for path in accepted_audit_summary_paths or []],
        "pgn_eval": _load_evidence(pgn_eval_path),
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
    _check_holdouts(evidence["holdout_evals"], checks, next_actions, min_exact_fen_accuracy)
    _check_accepted_audits(evidence["accepted_audits"], checks, next_actions)
    _check_pgn_eval(evidence["pgn_eval"], checks, next_actions)
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
        "evidence": _evidence_summary(evidence),
        "pass_conditions": {
            "min_real_scanned_fen_profiles": min_profile_count,
            "min_valid_human_verified_labels_per_profile": min_valid_labels_per_profile,
            "min_exact_fen_accuracy": min_exact_fen_accuracy,
            "false_positive_count": 0,
            "accepted_fen_audit_high_or_critical": 0,
            "pgn_strict_export_replay_accepted_only": True,
            "python_chess_available": True,
        },
    }
    _write_outputs(payload, Path(output_json), Path(output_md))
    return payload


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


def _check_pgn_eval(evidence: dict[str, Any], checks: list[dict[str, Any]], next_actions: list[str]) -> None:
    payload = evidence["payload"]
    blocker_count = int(payload.get("pgn_replay_errors") or payload.get("replay_error_count") or payload.get("strict_export_blocker_count") or 0)
    strict_only = bool(payload.get("strict_export_replay_accepted_only", False))
    exported = int(payload.get("exported_pgn_count") or payload.get("valid_pgn_count") or 0)
    valid = int(payload.get("valid_pgn_count") or exported)
    ok = evidence["available"] and _status_passed(payload.get("status")) and blocker_count == 0 and strict_only and exported == valid
    _add_check(checks, "pgn_strict_export_replay_accepted_only", ok, evidence, "PGN strict export must contain only parser/replay accepted records.", exported_pgn_count=exported, valid_pgn_count=valid, blocker_count=blocker_count)
    if not ok:
        next_actions.append("Run PGN replay/auto-repair eval and ensure strict export contains only replay-accepted records.")


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
    ok = evidence["available"] and _status_passed(payload.get("status")) and bool(payload.get("release_ready", True))
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
    unsafe = "side_to_move_inferred_accepted" in serialized or "accepted_inferred_side_to_move" in serialized
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


def _float(value: Any, *, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _status_passed(value: Any) -> bool:
    return str(value or "").strip().lower() in {"ok", "pass", "passed", "ready"}


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
    lines = [
        "# Chess Full Automation Release Proof",
        "",
        f"- Question: {payload['question']}",
        f"- Answer: `{payload['answer']}`",
        f"- Status: `{payload['status']}`",
        f"- Generated at: `{payload['generated_at']}`",
        "",
        "## Checks",
        "",
        "| Check | Status | Evidence |",
        "| --- | --- | --- |",
    ]
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether KindleMaster can claim full chess FEN/PGN automation for release.")
    parser.add_argument("--corpus-gate", default="")
    parser.add_argument("--fen-corpus", default="")
    parser.add_argument("--profile-readiness", action="append", default=[])
    parser.add_argument("--holdout-eval", action="append", default=[])
    parser.add_argument("--accepted-audit-summary", action="append", default=[])
    parser.add_argument("--pgn-eval", default="")
    parser.add_argument("--reading-order-audit", default="")
    parser.add_argument("--auto-strict-validation", default="")
    parser.add_argument("--python-chess-status", default="")
    parser.add_argument("--epub-validation", default="")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_OUTPUT_MD))
    args = parser.parse_args()
    payload = check_chess_full_automation_ready(
        corpus_gate_path=args.corpus_gate or None,
        fen_corpus_path=args.fen_corpus or None,
        profile_readiness_paths=args.profile_readiness,
        holdout_eval_paths=args.holdout_eval,
        accepted_audit_summary_paths=args.accepted_audit_summary,
        pgn_eval_path=args.pgn_eval or None,
        reading_order_audit_path=args.reading_order_audit or None,
        auto_strict_validation_path=args.auto_strict_validation or None,
        python_chess_status_path=args.python_chess_status or None,
        epub_validation_path=args.epub_validation or None,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
