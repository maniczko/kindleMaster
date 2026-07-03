from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.build_ml_datasets import build_ml_datasets
from scripts.train_route_classifier import (
    PROTECTED_RECALL_CLASSES,
    evaluate_route_classifier,
    promote_route_classifier,
    train_route_classifier,
)


DEFAULT_REPORT_PATH = Path("reports/ml/retrain_all/retrain_all_report.json")


def run_retrain_all(
    *,
    repo_root: str | Path = ".",
    from_feedback: bool = False,
    evaluate: bool = False,
    promote_if_better: bool = False,
    write_ledger: bool = False,
    feedback_log: str | Path = "reports/ml/feedback/conversion_feedback.jsonl",
    dataset_output_dir: str | Path = "reports/ml/datasets",
    candidate_model_dir: str | Path = "models/candidates",
    current_model: str | Path = "models/route_classifier_v1.json",
    corpus_report: str | Path = "reports/corpus/premium_corpus_smoke_report.json",
    min_examples_per_class: int = 25,
    dry_run: bool = False,
    no_promote: bool = False,
    require_human_reviewed: bool = False,
    report_path: str | Path = DEFAULT_REPORT_PATH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    generated_at = _utc_now()
    resolved_report_path = _resolve_path(root, report_path)
    resolved_dataset_dir = _resolve_path(root, dataset_output_dir)
    resolved_candidate_dir = _resolve_path(root, candidate_model_dir)
    resolved_current_model = _resolve_path(root, current_model)
    resolved_corpus_report = _resolve_path(root, corpus_report)
    feedback_paths = [_resolve_path(root, feedback_log)] if from_feedback else []

    payload: dict[str, Any] = {
        "schema": "kindlemaster.ml.retrain_all.v1",
        "generated_at": generated_at,
        "status": "running",
        "dry_run": bool(dry_run),
        "online_learning": False,
        "options": {
            "from_feedback": bool(from_feedback),
            "evaluate": bool(evaluate),
            "promote_if_better": bool(promote_if_better),
            "write_ledger": bool(write_ledger),
            "no_promote": bool(no_promote),
            "require_human_reviewed": bool(require_human_reviewed),
            "min_examples_per_class": int(min_examples_per_class),
        },
        "paths": {
            "dataset_output_dir": str(resolved_dataset_dir),
            "candidate_model_dir": str(resolved_candidate_dir),
            "current_model": str(resolved_current_model),
            "corpus_report": str(resolved_corpus_report),
            "report_path": str(resolved_report_path),
            "feedback_logs": [str(path) for path in feedback_paths],
        },
        "ledger_events": [],
    }

    dataset = build_ml_datasets(
        output_dir=resolved_dataset_dir,
        feedback_log_paths=feedback_paths,
        min_examples_per_class=min_examples_per_class,
        repo_root=root,
        write_ledger=write_ledger,
    )
    payload["dataset"] = dataset
    dataset_version = str(dataset.get("dataset_version") or _timestamp_slug(generated_at))
    dataset_path = _path_from_output(root, dataset, "route_examples", resolved_dataset_dir / "route_examples.jsonl")
    payload["dataset_version"] = dataset_version

    if require_human_reviewed and not _dataset_has_human_reviewed_feedback(dataset):
        payload.update(
            {
                "status": "blocked_dataset_not_ready",
                "blocker": "human_reviewed_feedback_required",
                "promotion_status": "blocked",
                "corpus_gate_status": "not_run",
                "metric_delta": {},
            }
        )
        _record_blocked_event(payload, write_ledger=write_ledger, repo_root=root)
        return _write_final_report(resolved_report_path, payload)

    readiness = _mapping(dataset.get("dataset_readiness"))
    if str(readiness.get("status") or dataset.get("training_readiness_status") or dataset.get("status") or "") != "ready":
        payload.update(
            {
                "status": "blocked_dataset_not_ready",
                "blocker": str(readiness.get("status") or dataset.get("status") or "unknown"),
                "promotion_status": "blocked",
                "corpus_gate_status": "not_run",
                "metric_delta": {},
            }
        )
        _record_blocked_event(payload, write_ledger=write_ledger, repo_root=root)
        return _write_final_report(resolved_report_path, payload)

    resolved_candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_model_path = resolved_candidate_dir / f"route_classifier_{dataset_version}.json"
    candidate_metrics_path = resolved_report_path.parent / f"{candidate_model_path.stem}.metrics.json"
    training = train_route_classifier(
        dataset_path=dataset_path,
        model_path=candidate_model_path,
        report_path=candidate_metrics_path,
        min_examples_per_class=min_examples_per_class,
    )
    payload["training"] = training
    _record_training_event(training, dataset_path=dataset_path, write_ledger=write_ledger, repo_root=root, payload=payload)
    if str(training.get("status") or "") != "candidate_trained":
        payload.update(
            {
                "status": "training_failed",
                "promotion_status": "blocked",
                "corpus_gate_status": "not_run",
                "metric_delta": {},
                "candidate_model_version": "",
                "before_model_version": _model_version(resolved_current_model),
            }
        )
        _record_blocked_event(payload, write_ledger=write_ledger, repo_root=root)
        return _write_final_report(resolved_report_path, payload)

    candidate_path = Path(str(training.get("model_path") or candidate_model_path))
    before_model_version = _model_version(resolved_current_model)
    candidate_model_version = _model_version(candidate_path)
    payload["before_model_version"] = before_model_version
    payload["candidate_model_version"] = candidate_model_version

    current_eval: dict[str, Any] = {"status": "skipped", "reason": "evaluate_flag_not_set"}
    candidate_eval: dict[str, Any] = {"status": "skipped", "reason": "evaluate_flag_not_set"}
    if evaluate:
        current_eval = evaluate_route_classifier(
            dataset_path=dataset_path,
            model_path=resolved_current_model,
            report_path=resolved_report_path.parent / f"{Path(resolved_current_model).stem}.current_evaluation.json",
        )
        candidate_eval = evaluate_route_classifier(
            dataset_path=dataset_path,
            model_path=candidate_path,
            report_path=resolved_report_path.parent / f"{candidate_path.stem}.candidate_evaluation.json",
        )
        _record_evaluation_event(
            candidate_eval,
            dataset_path=dataset_path,
            model_path=candidate_path,
            write_ledger=write_ledger,
            repo_root=root,
            payload=payload,
        )
    payload["evaluation"] = {
        "current": current_eval,
        "candidate": candidate_eval,
    }
    metric_delta = _metric_delta(current_eval=current_eval, candidate_eval=candidate_eval, training=training)
    payload["metric_delta"] = metric_delta

    promotion_decision = _promotion_decision(
        promote_if_better=promote_if_better,
        no_promote=no_promote,
        dry_run=dry_run,
        evaluate=evaluate,
        current_eval=current_eval,
        candidate_eval=candidate_eval,
        training=training,
        metric_delta=metric_delta,
    )
    payload["promotion_decision"] = promotion_decision

    if not promotion_decision["eligible"]:
        payload.update(
            {
                "status": "promotion_blocked",
                "promotion_status": "blocked",
                "corpus_gate_status": "not_run",
                "rollback_snapshot": "",
            }
        )
        _record_blocked_event(payload, write_ledger=write_ledger, repo_root=root)
        return _write_final_report(resolved_report_path, payload)

    if dry_run:
        payload.update(
            {
                "status": "dry_run",
                "promotion_status": "dry_run",
                "corpus_gate_status": "not_run",
                "rollback_snapshot": "",
            }
        )
        _record_blocked_event(payload, write_ledger=write_ledger, repo_root=root)
        return _write_final_report(resolved_report_path, payload)

    rollback_snapshot = _write_rollback_snapshot(resolved_current_model, dataset_version=dataset_version)
    promotion = promote_route_classifier(
        candidate_path=candidate_path,
        model_path=resolved_current_model,
        corpus_report_path=resolved_corpus_report,
    )
    payload["promotion"] = promotion
    payload["rollback_snapshot"] = rollback_snapshot
    payload["corpus_gate_status"] = str(_mapping(promotion.get("corpus_gate")).get("status") or "unknown")
    if str(promotion.get("status") or "") == "promoted":
        payload.update({"status": "promoted", "promotion_status": "promoted"})
        _record_promoted_event(promotion, write_ledger=write_ledger, repo_root=root, payload=payload)
    else:
        payload.update({"status": "promotion_blocked", "promotion_status": "blocked"})
        _record_blocked_event(payload, write_ledger=write_ledger, repo_root=root)
    return _write_final_report(resolved_report_path, payload)


def _promotion_decision(
    *,
    promote_if_better: bool,
    no_promote: bool,
    dry_run: bool,
    evaluate: bool,
    current_eval: Mapping[str, Any],
    candidate_eval: Mapping[str, Any],
    training: Mapping[str, Any],
    metric_delta: Mapping[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if not promote_if_better:
        reasons.append("promote_if_better_not_requested")
    if no_promote:
        reasons.append("no_promote_requested")
    if str(training.get("status") or "") != "candidate_trained":
        reasons.append("candidate_not_trained")
    training_gates = _mapping(_mapping(training.get("metrics")).get("promotion_gates"))
    if training_gates and not bool(training_gates.get("passed")):
        reasons.append("candidate_metric_gates_failed")
    if evaluate:
        if str(current_eval.get("status") or "") != "evaluated":
            reasons.append("current_model_evaluation_unavailable")
        if str(candidate_eval.get("status") or "") != "evaluated":
            reasons.append("candidate_evaluation_unavailable")
        if _float(metric_delta.get("accuracy_delta")) <= 0:
            reasons.append("candidate_not_better")
        reasons.extend(_protected_recall_regressions(current_eval, candidate_eval))
    return {
        "eligible": not reasons and (promote_if_better or dry_run),
        "dry_run": bool(dry_run),
        "reasons": reasons,
    }


def _metric_delta(
    *,
    current_eval: Mapping[str, Any],
    candidate_eval: Mapping[str, Any],
    training: Mapping[str, Any],
) -> dict[str, Any]:
    if str(current_eval.get("status") or "") == "evaluated" and str(candidate_eval.get("status") or "") == "evaluated":
        return {
            "accuracy_delta": round(_float(candidate_eval.get("accuracy")) - _float(current_eval.get("accuracy")), 6),
            "current_accuracy": _float(current_eval.get("accuracy")),
            "candidate_accuracy": _float(candidate_eval.get("accuracy")),
            "source": "evaluation",
        }
    metrics = _mapping(training.get("metrics"))
    return {
        "accuracy_delta": None,
        "candidate_accuracy": _float(metrics.get("accuracy")),
        "source": "training_holdout_only",
    }


def _protected_recall_regressions(current_eval: Mapping[str, Any], candidate_eval: Mapping[str, Any]) -> list[str]:
    current_labels = _mapping(current_eval.get("per_label"))
    candidate_labels = _mapping(candidate_eval.get("per_label"))
    reasons: list[str] = []
    for label in PROTECTED_RECALL_CLASSES:
        current_recall = _float(_mapping(current_labels.get(label)).get("recall"))
        candidate_recall = _float(_mapping(candidate_labels.get(label)).get("recall"))
        if candidate_recall < current_recall:
            reasons.append(f"protected_recall_regressed:{label}")
    return reasons


def _write_rollback_snapshot(model_path: Path, *, dataset_version: str) -> str:
    if not model_path.exists():
        return ""
    snapshot_dir = model_path.parent / "rollback"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{model_path.stem}_{dataset_version}.json"
    shutil.copy2(model_path, snapshot_path)
    return str(snapshot_path)


def _dataset_has_human_reviewed_feedback(dataset: Mapping[str, Any]) -> bool:
    return int(dataset.get("feedback_route_example_count", 0) or 0) > 0 or int(dataset.get("quality_feedback_example_count", 0) or 0) > 0


def _record_training_event(
    training: Mapping[str, Any],
    *,
    dataset_path: Path,
    write_ledger: bool,
    repo_root: Path,
    payload: dict[str, Any],
) -> None:
    if not write_ledger:
        return
    try:
        from learning_ledger import record_model_trained

        record = record_model_trained(training_payload=training, dataset_path=dataset_path, repo_root=repo_root)
        payload["ledger_events"].append(_ledger_summary(record, "model_trained"))
    except Exception as error:
        payload["ledger_events"].append({"event_type": "model_trained", "status": "failed", "error": str(error)})


def _record_evaluation_event(
    evaluation: Mapping[str, Any],
    *,
    dataset_path: Path,
    model_path: Path,
    write_ledger: bool,
    repo_root: Path,
    payload: dict[str, Any],
) -> None:
    if not write_ledger:
        return
    try:
        from learning_ledger import record_model_evaluated

        record = record_model_evaluated(
            evaluation_payload=evaluation,
            dataset_path=dataset_path,
            model_path=model_path,
            repo_root=repo_root,
        )
        payload["ledger_events"].append(_ledger_summary(record, "model_evaluated"))
    except Exception as error:
        payload["ledger_events"].append({"event_type": "model_evaluated", "status": "failed", "error": str(error)})


def _record_promoted_event(
    promotion: Mapping[str, Any],
    *,
    write_ledger: bool,
    repo_root: Path,
    payload: dict[str, Any],
) -> None:
    if not write_ledger:
        return
    try:
        from learning_ledger import record_model_promoted

        record = record_model_promoted(promotion_payload=promotion, repo_root=repo_root)
        payload["ledger_events"].append(_ledger_summary(record, "model_promoted"))
    except Exception as error:
        payload["ledger_events"].append({"event_type": "model_promoted", "status": "failed", "error": str(error)})


def _record_blocked_event(payload: dict[str, Any], *, write_ledger: bool, repo_root: Path) -> None:
    if not write_ledger:
        return
    try:
        from learning_ledger import record_model_promotion_blocked

        record = record_model_promotion_blocked(promotion_payload=payload, repo_root=repo_root)
        payload["ledger_events"].append(_ledger_summary(record, "model_promotion_blocked"))
    except Exception as error:
        payload["ledger_events"].append({"event_type": "model_promotion_blocked", "status": "failed", "error": str(error)})


def _ledger_summary(record: Mapping[str, Any], event_type: str) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "status": str(record.get("status") or ""),
        "event_id": str(record.get("event_id") or ""),
        "events_path": str(record.get("events_path") or ""),
    }


def _write_final_report(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["report_path"] = str(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def _path_from_output(root: Path, dataset: Mapping[str, Any], key: str, fallback: Path) -> Path:
    outputs = _mapping(dataset.get("outputs"))
    raw = outputs.get(key) or fallback
    path = Path(str(raw))
    return path if path.is_absolute() else root / path


def _model_version(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(_mapping(payload).get("model_version") or "")


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_slug(value: str) -> str:
    return "".join(character for character in value if character.isdigit())[:14] or "unknown"
