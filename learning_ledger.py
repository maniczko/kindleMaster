from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4


LEARNING_EVENT_SCHEMA = "kindlemaster.learning_ledger.event.v1"
LEARNING_INDEX_SCHEMA = "kindlemaster.learning_ledger.index.v1"
DEFAULT_LEDGER_DIR = Path("reports/ml/learning_ledger")
DEFAULT_EVENTS_PATH = DEFAULT_LEDGER_DIR / "learning_events.jsonl"
DEFAULT_INDEX_PATH = DEFAULT_LEDGER_DIR / "conversion_learning_index.json"
PRIVACY_PAYLOAD = {
    "stores_text": False,
    "stores_source_file": False,
    "stores_fingerprints_only": True,
}


def record_conversion_completed(
    *,
    conversion_id: str,
    source_path: str | Path,
    original_filename: str,
    source_type: str,
    profile_requested: str,
    route_model_mode: str,
    quality_gate_mode: str,
    metadata: Mapping[str, Any],
    result: Mapping[str, Any],
    output_size_bytes: int | None = None,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    input_fingerprint = fingerprint_file(source_path)
    analysis = _mapping(result.get("analysis"))
    quality_report = _mapping(result.get("quality_report"))
    document_summary = _mapping(result.get("document_summary"))
    route_decision = _route_decision(result, metadata)
    ai_verifier = _mapping(metadata.get("ai_quality_verification")) or _mapping(
        quality_report.get("ai_quality_verification")
    )
    premium = _mapping(metadata.get("premium_scoring")) or _mapping(quality_report.get("premium_scoring"))
    event = _base_event(
        event_type="conversion_completed",
        conversion_id=conversion_id or _fallback_conversion_id(original_filename, input_fingerprint),
        input_fingerprint=input_fingerprint,
        source_type=source_type,
    )
    event.update(
        {
            "profile_requested": str(profile_requested or ""),
            "profile_selected": str(
                route_decision.get("selected_profile")
                or document_summary.get("profile")
                or analysis.get("profile")
                or ""
            ),
            "route_model_mode": str(route_decision.get("mode") or route_model_mode or "off"),
            "route_model_version": str(route_decision.get("model_version") or ""),
            "quality_verifier_version": str(ai_verifier.get("model_version") or ""),
            "quality_gate_mode": str(quality_gate_mode or ""),
            "feature_hash": str(
                route_decision.get("input_features_hash")
                or route_decision.get("reported_features_hash")
                or ai_verifier.get("features_hash")
                or ""
            ),
            "quality_score": _optional_float(premium.get("premium_score")),
            "quality_decision": _quality_decision(ai_verifier=ai_verifier, premium=premium, quality_report=quality_report),
            "artifact_health": _artifact_health(metadata=metadata, quality_report=quality_report, output_size_bytes=output_size_bytes),
            "chess_metrics": _chess_metrics(result, metadata),
            "layout_metrics": _layout_metrics(result, metadata),
            "training_eligible": False,
            "cloud_sync_status": str(metadata.get("cloud_sync_status") or "local_only"),
        }
    )
    return append_learning_event(event, repo_root=repo_root)


def record_user_feedback_added(
    *,
    feedback_record: Mapping[str, Any],
    job: Mapping[str, Any] | None = None,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    conversion = _mapping(feedback_record.get("conversion"))
    route = _mapping(feedback_record.get("route"))
    quality = _mapping(feedback_record.get("quality"))
    feedback = _mapping(feedback_record.get("feedback"))
    dataset = _mapping(feedback_record.get("dataset"))
    metadata = _mapping((job or {}).get("metadata"))
    ledger = _mapping(metadata.get("learning_ledger"))
    conversion_id = str(feedback_record.get("job_id") or (job or {}).get("job_id") or feedback_record.get("case_id") or "")
    event = _base_event(
        event_type="user_feedback_added",
        conversion_id=conversion_id,
        input_fingerprint=str(ledger.get("input_fingerprint") or ""),
        source_type=str(conversion.get("source_type") or (job or {}).get("source_type") or ""),
    )
    event.update(
        {
            "profile_requested": str(_mapping(job or {}).get("profile") or ""),
            "profile_selected": str(route.get("selected_profile") or route.get("heuristic_profile") or ""),
            "route_model_mode": str(route.get("mode") or ""),
            "route_model_version": str(route.get("model_version") or ""),
            "quality_verifier_version": str(_mapping(quality.get("ai_quality_verification")).get("model_version") or ""),
            "feature_hash": str(route.get("features_hash") or route.get("reported_features_hash") or ""),
            "quality_score": _optional_float(feedback.get("quality_score")),
            "quality_decision": str(feedback.get("status") or ""),
            "feedback_id": str(feedback_record.get("record_id") or ""),
            "feedback": _feedback_summary(feedback),
            "dataset_version": "",
            "training_eligible": bool(dataset.get("include_in_route_training")),
            "dataset_reason": str(dataset.get("reason") or ""),
            "cloud_sync_status": "local_only",
        }
    )
    return append_learning_event(event, repo_root=repo_root)


def record_dataset_built(
    *,
    dataset_payload: Mapping[str, Any],
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    outputs = _mapping(dataset_payload.get("outputs"))
    event = _base_event(
        event_type="dataset_built",
        conversion_id="",
        input_fingerprint="",
        source_type="",
    )
    event.update(
        {
            "dataset_version": _dataset_version(dataset_payload),
            "dataset_status": str(dataset_payload.get("status") or ""),
            "dataset_paths": _safe_paths(outputs.values()),
            "route_example_count": _int_value(dataset_payload.get("route_example_count")),
            "feedback_record_count": _int_value(dataset_payload.get("feedback_record_count")),
            "feedback_route_example_count": _int_value(dataset_payload.get("feedback_route_example_count")),
            "quality_feedback_example_count": _int_value(dataset_payload.get("quality_feedback_example_count")),
            "training_eligible": str(dataset_payload.get("status") or "") == "ready",
            "cloud_sync_status": "local_only",
        }
    )
    return append_learning_event(event, repo_root=repo_root)


def record_model_trained(
    *,
    training_payload: Mapping[str, Any],
    dataset_path: str | Path,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    metrics = _mapping(training_payload.get("metrics"))
    event = _base_event(
        event_type="model_trained",
        conversion_id="",
        input_fingerprint="",
        source_type="",
    )
    event.update(
        {
            "dataset_version": _dataset_version({"outputs": {"dataset": str(dataset_path)}}),
            "dataset_path": _safe_path(dataset_path),
            "model_version_before": "",
            "model_version_after": _model_version_from_path(training_payload.get("model_path")),
            "model_path": _safe_path(training_payload.get("model_path")),
            "training_status": str(training_payload.get("status") or ""),
            "quality_score": _optional_float(metrics.get("accuracy")),
            "training_metrics": {
                "accuracy": _optional_float(metrics.get("accuracy")),
                "macro_f1": _optional_float(metrics.get("macro_f1")),
                "example_count": _int_value(metrics.get("example_count")),
                "holdout_example_count": _int_value(metrics.get("holdout_example_count")),
                "label_counts": dict(_mapping(metrics.get("label_counts"))),
            },
            "training_eligible": str(training_payload.get("status") or "") == "candidate_trained",
            "cloud_sync_status": "local_only",
        }
    )
    return append_learning_event(event, repo_root=repo_root)


def record_model_promoted(
    *,
    promotion_payload: Mapping[str, Any],
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    event = _base_event(
        event_type="model_promoted",
        conversion_id="",
        input_fingerprint="",
        source_type="",
    )
    event.update(
        {
            "model_version_before": "",
            "model_version_after": _model_version_from_path(promotion_payload.get("model_path")),
            "model_path": _safe_path(promotion_payload.get("model_path")),
            "candidate_path": _safe_path(promotion_payload.get("candidate_path")),
            "promotion_status": str(promotion_payload.get("status") or ""),
            "promotion_gates": {
                "metric_gates": _mapping(promotion_payload.get("metric_gates")),
                "corpus_gate": _mapping(promotion_payload.get("corpus_gate")),
            },
            "training_eligible": str(promotion_payload.get("status") or "") == "promoted",
            "cloud_sync_status": "local_only",
        }
    )
    return append_learning_event(event, repo_root=repo_root)


def append_learning_event(
    event: Mapping[str, Any],
    *,
    repo_root: str | Path = ".",
    events_path: str | Path = DEFAULT_EVENTS_PATH,
    index_path: str | Path = DEFAULT_INDEX_PATH,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    resolved_events = _resolve_path(root, events_path)
    resolved_index = _resolve_path(root, index_path)
    payload = _normalize_event(event)
    resolved_events.parent.mkdir(parents=True, exist_ok=True)
    with resolved_events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    index = build_conversion_learning_index(events_path=resolved_events)
    _write_json_atomic(resolved_index, index)
    return {
        "status": "recorded",
        "event": payload,
        "event_id": payload["event_id"],
        "events_path": str(resolved_events),
        "index_path": str(resolved_index),
    }


def build_conversion_learning_index(*, events_path: str | Path) -> dict[str, Any]:
    events = load_learning_events(events_path)
    conversions: dict[str, dict[str, Any]] = {}
    fingerprints: dict[str, dict[str, Any]] = {}
    event_counts = Counter(str(event.get("event_type") or "") for event in events)
    for event in events:
        conversion_id = str(event.get("conversion_id") or "")
        fingerprint = str(event.get("input_fingerprint") or "")
        if conversion_id:
            row = conversions.setdefault(
                conversion_id,
                {
                    "conversion_id": conversion_id,
                    "input_fingerprint": fingerprint,
                    "event_count": 0,
                    "event_types": {},
                    "latest_event_type": "",
                    "latest_event_id": "",
                    "latest_created_at": "",
                    "feedback_ids": [],
                    "dataset_versions": [],
                    "model_versions": [],
                    "training_eligible": False,
                    "quality_decision": "",
                },
            )
            _update_index_row(row, event)
        if fingerprint:
            row = fingerprints.setdefault(
                fingerprint,
                {
                    "input_fingerprint": fingerprint,
                    "conversion_ids": [],
                    "event_count": 0,
                    "latest_created_at": "",
                },
            )
            if conversion_id and conversion_id not in row["conversion_ids"]:
                row["conversion_ids"].append(conversion_id)
            row["event_count"] += 1
            row["latest_created_at"] = str(event.get("created_at") or row.get("latest_created_at") or "")
    return {
        "schema": LEARNING_INDEX_SCHEMA,
        "generated_at": _utc_now(),
        "event_count": len(events),
        "event_type_counts": dict(event_counts),
        "conversion_count": len(conversions),
        "input_fingerprint_count": len(fingerprints),
        "conversions": conversions,
        "input_fingerprints": fingerprints,
        "privacy": dict(PRIVACY_PAYLOAD),
    }


def load_learning_events(events_path: str | Path = DEFAULT_EVENTS_PATH) -> list[dict[str, Any]]:
    path = Path(events_path)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            events.append(dict(payload))
    return events


def fingerprint_file(path: str | Path) -> str:
    source = Path(path)
    try:
        hasher = sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
        return f"sha256:{hasher.hexdigest()}"
    except OSError:
        return ""


def _base_event(*, event_type: str, conversion_id: str, input_fingerprint: str, source_type: str) -> dict[str, Any]:
    return {
        "schema": LEARNING_EVENT_SCHEMA,
        "event_id": f"ll_{uuid4().hex}",
        "event_type": str(event_type or ""),
        "created_at": _utc_now(),
        "conversion_id": str(conversion_id or ""),
        "input_fingerprint": str(input_fingerprint or ""),
        "source_type": str(source_type or ""),
        "profile_requested": "",
        "profile_selected": "",
        "route_model_mode": "",
        "route_model_version": "",
        "quality_verifier_version": "",
        "feature_hash": "",
        "quality_score": None,
        "quality_decision": "",
        "artifact_health": {},
        "chess_metrics": {},
        "layout_metrics": {},
        "feedback_id": "",
        "dataset_version": "",
        "model_version_before": "",
        "model_version_after": "",
        "training_eligible": False,
        "privacy": dict(PRIVACY_PAYLOAD),
    }


def _normalize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(_base_event(
        event_type=str(event.get("event_type") or ""),
        conversion_id=str(event.get("conversion_id") or ""),
        input_fingerprint=str(event.get("input_fingerprint") or ""),
        source_type=str(event.get("source_type") or ""),
    ))
    payload.update({key: value for key, value in event.items() if key not in {"privacy"}})
    payload["schema"] = LEARNING_EVENT_SCHEMA
    payload["privacy"] = dict(PRIVACY_PAYLOAD)
    payload["training_eligible"] = bool(payload.get("training_eligible"))
    return _json_safe(payload)


def _update_index_row(row: dict[str, Any], event: Mapping[str, Any]) -> None:
    event_type = str(event.get("event_type") or "")
    row["event_count"] += 1
    types = dict(row.get("event_types") or {})
    types[event_type] = int(types.get(event_type, 0)) + 1
    row["event_types"] = types
    row["latest_event_type"] = event_type
    row["latest_event_id"] = str(event.get("event_id") or "")
    row["latest_created_at"] = str(event.get("created_at") or "")
    row["quality_decision"] = str(event.get("quality_decision") or row.get("quality_decision") or "")
    row["training_eligible"] = bool(row.get("training_eligible") or event.get("training_eligible"))
    feedback_id = str(event.get("feedback_id") or "")
    if feedback_id and feedback_id not in row["feedback_ids"]:
        row["feedback_ids"].append(feedback_id)
    dataset_version = str(event.get("dataset_version") or "")
    if dataset_version and dataset_version not in row["dataset_versions"]:
        row["dataset_versions"].append(dataset_version)
    for key in ("model_version_before", "model_version_after"):
        model_version = str(event.get(key) or "")
        if model_version and model_version not in row["model_versions"]:
            row["model_versions"].append(model_version)


def _route_decision(result: Mapping[str, Any], metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    for candidate in (
        _mapping(metadata.get("route_decision")),
        _mapping(_mapping(metadata.get("source_analysis")).get("route_decision")),
        _mapping(result.get("route_decision")),
        _mapping(_mapping(result.get("analysis")).get("route_decision")),
    ):
        if candidate:
            return candidate
    return {}


def _quality_decision(*, ai_verifier: Mapping[str, Any], premium: Mapping[str, Any], quality_report: Mapping[str, Any]) -> str:
    if ai_verifier.get("decision"):
        return str(ai_verifier.get("decision") or "")
    if premium.get("premium_ready") is True:
        return "ready"
    if premium.get("kindle_ready") is True:
        return "review"
    blockers = quality_report.get("quality_blockers") or quality_report.get("blockers") or []
    return "block" if blockers else str(quality_report.get("overall_status") or quality_report.get("status") or "review")


def _artifact_health(*, metadata: Mapping[str, Any], quality_report: Mapping[str, Any], output_size_bytes: int | None) -> dict[str, Any]:
    artifacts = _mapping(metadata.get("artifacts"))
    return {
        "output_size_bytes": _int_value(output_size_bytes or metadata.get("output_size_bytes") or quality_report.get("final_output_size_bytes")),
        "validation_status": str(quality_report.get("validation_status") or ""),
        "epubcheck_status": str(quality_report.get("epubcheck_status") or quality_report.get("validation_status") or ""),
        "artifact_keys": sorted(str(key) for key in artifacts.keys()),
        "artifact_count": len(artifacts),
        "blocker_count": len(_list_value(quality_report.get("quality_blockers") or quality_report.get("blockers"))),
        "warning_count": len(_list_value(quality_report.get("warnings"))),
    }


def _chess_metrics(result: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    quality_report = _mapping(result.get("quality_report"))
    candidates = (
        metadata.get("chess_metrics"),
        quality_report.get("chess_metrics"),
        quality_report.get("chess_fen"),
        _mapping(result.get("document_summary")).get("chess"),
    )
    return _bounded_metrics(candidates, allowed_prefixes=("fen", "pgn", "diagram", "side", "trusted", "accepted"))


def _layout_metrics(result: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    quality_report = _mapping(result.get("quality_report"))
    candidates = (
        metadata.get("layout_metrics"),
        quality_report.get("magazine_premium_quality"),
        quality_report.get("text_cleanup"),
        _mapping(result.get("document_summary")),
    )
    return _bounded_metrics(candidates, allowed_prefixes=("toc", "layout", "article", "asset", "section", "text", "warning"))


def _bounded_metrics(candidates: Iterable[Any], *, allowed_prefixes: tuple[str, ...]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for candidate in candidates:
        for key, value in _mapping(candidate).items():
            normalized = str(key).lower()
            if not normalized.startswith(allowed_prefixes):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                metrics[str(key)] = value
            elif isinstance(value, Mapping):
                metrics[str(key)] = {
                    str(nested_key): nested_value
                    for nested_key, nested_value in value.items()
                    if isinstance(nested_value, (str, int, float, bool)) or nested_value is None
                }
    return metrics


def _feedback_summary(feedback: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": str(feedback.get("status") or ""),
        "quality_label": str(feedback.get("quality_label") or ""),
        "route_label": str(feedback.get("route_label") or ""),
        "issue_tags": [str(item) for item in feedback.get("issue_tags", []) or []][:20],
        "reviewer_present": bool(str(feedback.get("reviewer") or "").strip()),
        "include_in_training": bool(feedback.get("include_in_training")),
        "notes_length": len(str(feedback.get("notes") or "")),
    }


def _dataset_version(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(_json_safe(dict(payload)), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"dataset_{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _model_version_from_path(path: Any) -> str:
    raw_path = Path(str(path or ""))
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(_mapping(payload).get("model_version") or "")


def _fallback_conversion_id(original_filename: str, input_fingerprint: str) -> str:
    stem = Path(str(original_filename or "conversion")).stem or "conversion"
    digest = sha256(f"{stem}|{input_fingerprint}".encode("utf-8")).hexdigest()[:12]
    return f"{stem}-{digest}"


def _safe_paths(paths: Iterable[Any]) -> list[str]:
    return [_safe_path(path) for path in paths if str(path or "").strip()]


def _safe_path(path: Any) -> str:
    if path in (None, ""):
        return ""
    return str(path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temp_path.replace(path)


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
