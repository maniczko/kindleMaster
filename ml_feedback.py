from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

from ml_features import ROUTE_LABELS, normalize_route_features, route_feature_payload, route_features_hash


DEFAULT_FEEDBACK_LOG_PATH = Path("reports/ml/feedback/conversion_feedback.jsonl")
DEFAULT_FEEDBACK_EXPORT_DIR = Path("reports/ml/feedback")
FEEDBACK_SCHEMA_VERSION = 1
FEEDBACK_STATUSES = ("accepted", "needs_review", "rejected")
QUALITY_LABELS = ("unknown", "good", "usable", "poor", "blocked")


def append_conversion_feedback_event(
    *,
    source_path: str,
    original_filename: str,
    source_type: str,
    metadata: Mapping[str, Any],
    result: Mapping[str, Any],
    event_path: str | Path | None = None,
) -> dict[str, Any]:
    record = conversion_feedback_record(
        result,
        source_path=source_path,
        case_id=Path(original_filename).stem,
        feedback_status="needs_review",
        quality_label=_quality_label_from_metadata(metadata),
    )
    premium = _mapping(metadata.get("premium_scoring")) or _mapping(_mapping(result.get("quality_report")).get("premium_scoring"))
    quality_selection = _mapping(metadata.get("quality_selection")) or _mapping(
        _mapping(result.get("quality_report")).get("quality_selection")
    )
    ai_verifier = _mapping(metadata.get("ai_quality_verification")) or _mapping(
        _mapping(result.get("quality_report")).get("ai_quality_verification")
    )
    record["event_type"] = "conversion_quality"
    record["conversion"]["source_type"] = source_type
    record["conversion"]["filename"] = original_filename
    record["quality"]["premium_score"] = premium.get("premium_score")
    record["quality"]["premium_status"] = premium.get("status")
    record["quality"]["kindle_ready"] = premium.get("kindle_ready")
    record["quality"]["premium_issue_counts"] = premium.get("issue_counts") or {}
    record["quality"]["quality_selection"] = _quality_selection_summary(quality_selection)
    record["quality"]["ai_quality_verification"] = {
        "status": ai_verifier.get("status"),
        "decision": ai_verifier.get("decision"),
        "confidence": ai_verifier.get("confidence"),
        "model_version": ai_verifier.get("model_version"),
        "features_hash": ai_verifier.get("features_hash"),
        "reason_codes": list(ai_verifier.get("reason_codes") or []),
    }
    append_feedback_record(record, log_path=event_path or DEFAULT_FEEDBACK_LOG_PATH)
    return record


def append_user_feedback(
    *,
    job_id: str,
    feedback: Mapping[str, Any],
    job: Mapping[str, Any],
    event_path: str | Path | None = None,
) -> dict[str, Any]:
    metadata = _mapping(job.get("metadata"))
    record = {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "event_type": "user_feedback",
        "record_id": _record_id(
            created_at=_clean_timestamp(None),
            case_id=str(job_id),
            report_path="",
            source_path=str(job.get("source_path", "") or ""),
            route_label=str(feedback.get("route_label", "") or ""),
            features_hash=str(_mapping(metadata.get("ai_quality_verification")).get("features_hash", "") or ""),
        ),
        "created_at": _clean_timestamp(None),
        "job_id": str(job_id),
        "conversion": {
            "filename": str(job.get("filename", "") or ""),
            "source_type": str(job.get("source_type", "") or ""),
            "source_path": str(job.get("source_path", "") or ""),
            "output_path": str(job.get("output_path", "") or ""),
        },
        "feedback": {
            "status": _clean_choice(feedback.get("status"), FEEDBACK_STATUSES, default="needs_review"),
            "quality_label": _clean_choice(feedback.get("quality_label"), QUALITY_LABELS, default="unknown"),
            "quality_score": _optional_float(feedback.get("quality_score")),
            "route_label": _clean_route_label(feedback.get("route_label", "")),
            "issue_tags": _clean_issue_tags(feedback.get("issue_tags") or feedback.get("tags") or []),
            "notes": str(feedback.get("notes", "") or "").strip(),
            "reviewer": str(feedback.get("reviewer", "") or "").strip(),
        },
        "quality": {
            "premium_score": _mapping(metadata.get("premium_scoring")).get("premium_score"),
            "premium_status": _mapping(metadata.get("premium_scoring")).get("status"),
            "quality_selection": _quality_selection_summary(_mapping(metadata.get("quality_selection"))),
            "ai_quality_verification": _mapping(metadata.get("ai_quality_verification")),
        },
    }
    append_feedback_record(record, log_path=event_path or DEFAULT_FEEDBACK_LOG_PATH)
    return record


def append_conversion_feedback_from_report(
    *,
    report_path: str | Path,
    log_path: str | Path = DEFAULT_FEEDBACK_LOG_PATH,
    source_path: str | Path | None = None,
    output_path: str | Path | None = None,
    case_id: str = "",
    feedback_status: str = "needs_review",
    quality_label: str = "unknown",
    quality_score: float | int | str | None = None,
    route_label: str = "",
    issue_tags: Iterable[str] | None = None,
    notes: str = "",
    reviewer: str = "",
    created_at: str | None = None,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    resolved_report = _resolve_path(root, report_path)
    try:
        report_payload = json.loads(resolved_report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "status": "failed",
            "error": "feedback_report_unavailable",
            "exception": str(error),
            "report_path": str(resolved_report),
        }
    if not isinstance(report_payload, Mapping):
        return {
            "status": "failed",
            "error": "feedback_report_not_object",
            "report_path": str(resolved_report),
        }

    record = conversion_feedback_record(
        report_payload,
        report_path=resolved_report,
        source_path=source_path,
        output_path=output_path,
        case_id=case_id,
        feedback_status=feedback_status,
        quality_label=quality_label,
        quality_score=quality_score,
        route_label=route_label,
        issue_tags=issue_tags,
        notes=notes,
        reviewer=reviewer,
        created_at=created_at,
    )
    append_feedback_record(record, log_path=log_path, repo_root=root)
    return {
        "status": "logged",
        "log_path": str(_resolve_path(root, log_path)),
        "record_id": record["record_id"],
        "case_id": record["case_id"],
        "route_label": record["feedback"]["route_label"],
        "include_in_route_training": record["dataset"]["include_in_route_training"],
        "feature_hash": record["route"]["features_hash"],
    }


def conversion_feedback_record(
    report_payload: Mapping[str, Any],
    *,
    report_path: str | Path | None = None,
    source_path: str | Path | None = None,
    output_path: str | Path | None = None,
    case_id: str = "",
    feedback_status: str = "needs_review",
    quality_label: str = "unknown",
    quality_score: float | int | str | None = None,
    route_label: str = "",
    issue_tags: Iterable[str] | None = None,
    notes: str = "",
    reviewer: str = "",
    created_at: str | None = None,
) -> dict[str, Any]:
    created = _clean_timestamp(created_at)
    conversion = _conversion_summary(
        report_payload,
        report_path=report_path,
        source_path=source_path,
        output_path=output_path,
    )
    route = _route_summary(report_payload)
    feedback = {
        "status": _clean_choice(feedback_status, FEEDBACK_STATUSES, default="needs_review"),
        "quality_label": _clean_choice(quality_label, QUALITY_LABELS, default="unknown"),
        "quality_score": _optional_float(quality_score),
        "route_label": _clean_route_label(route_label),
        "issue_tags": _clean_issue_tags(issue_tags or []),
        "notes": str(notes or "").strip(),
        "reviewer": str(reviewer or "").strip(),
    }
    normalized_case_id = str(case_id or "").strip() or _case_id_from_report(report_payload, conversion, route)
    record_id = _record_id(
        created_at=created,
        case_id=normalized_case_id,
        report_path=conversion["report_path"],
        source_path=conversion["source_path"],
        route_label=feedback["route_label"],
        features_hash=route["features_hash"],
    )
    return {
        "schema_version": FEEDBACK_SCHEMA_VERSION,
        "record_id": record_id,
        "created_at": created,
        "case_id": normalized_case_id,
        "conversion": conversion,
        "route": route,
        "quality": _quality_summary(report_payload),
        "feedback": feedback,
        "dataset": {
            "include_in_route_training": feedback["route_label"] in ROUTE_LABELS and bool(route["features"]),
            "reason": _dataset_reason(feedback["route_label"], route["features"]),
        },
    }


def append_feedback_record(
    record: Mapping[str, Any],
    *,
    log_path: str | Path = DEFAULT_FEEDBACK_LOG_PATH,
    repo_root: str | Path = ".",
) -> Path:
    root = Path(repo_root).resolve()
    resolved_log = _resolve_path(root, log_path)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    with resolved_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
    return resolved_log


def export_feedback_datasets(
    *,
    log_paths: Iterable[str | Path] | str | Path | None = None,
    output_dir: str | Path = DEFAULT_FEEDBACK_EXPORT_DIR,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    resolved_output = _resolve_path(root, output_dir)
    resolved_output.mkdir(parents=True, exist_ok=True)
    records, load_skipped = load_feedback_records(log_paths=log_paths, repo_root=root)
    route_examples, route_skipped = route_examples_from_feedback(records)

    feedback_export_path = resolved_output / "conversion_feedback_export.jsonl"
    route_export_path = resolved_output / "route_feedback_examples.jsonl"
    summary_path = resolved_output / "feedback_summary.json"
    _write_jsonl(feedback_export_path, records)
    _write_jsonl(route_export_path, route_examples)
    summary = {
        "status": "exported",
        "feedback_record_count": len(records),
        "route_example_count": len(route_examples),
        "skipped": load_skipped + route_skipped,
        "outputs": {
            "feedback_export": str(feedback_export_path),
            "route_feedback_examples": str(route_export_path),
            "summary": str(summary_path),
        },
        "online_learning": False,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def export_feedback_dataset(
    *,
    feedback_log: str | Path = DEFAULT_FEEDBACK_LOG_PATH,
    output_path: str | Path = "reports/ml/datasets/quality_feedback_examples.jsonl",
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    records, skipped = load_feedback_records(log_paths=[feedback_log], repo_root=root)
    resolved_output = _resolve_path(root, output_path)
    _write_jsonl(resolved_output, records)
    return {
        "status": "exported",
        "feedback_record_count": len(records),
        "skipped": skipped,
        "output_path": str(resolved_output),
        "online_learning": False,
    }


def load_feedback_records(
    *,
    log_paths: Iterable[str | Path] | str | Path | None = None,
    repo_root: str | Path = ".",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = Path(repo_root).resolve()
    paths = _path_list(log_paths)
    records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for raw_path in paths:
        path = _resolve_path(root, raw_path)
        if not path.exists():
            skipped.append({"source": "feedback", "path": str(path), "reason": "feedback_log_missing"})
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            skipped.append({"source": "feedback", "path": str(path), "reason": "feedback_log_unreadable", "error": str(error)})
            continue
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as error:
                skipped.append(
                    {
                        "source": "feedback",
                        "path": str(path),
                        "line": line_number,
                        "reason": "invalid_feedback_json",
                        "error": str(error),
                    }
                )
                continue
            if isinstance(payload, Mapping):
                records.append(dict(payload))
            else:
                skipped.append({"source": "feedback", "path": str(path), "line": line_number, "reason": "feedback_not_object"})
    return records, skipped


def route_examples_from_feedback(
    records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    examples: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        record_id = str(record.get("record_id", "") or f"feedback-{index}")
        feedback = _mapping(record.get("feedback"))
        route_label = _clean_route_label(feedback.get("route_label", ""))
        route = _mapping(record.get("route"))
        features = _mapping(route.get("features"))
        if route_label not in ROUTE_LABELS:
            skipped.append({"source": "feedback", "record_id": record_id, "reason": "missing_or_invalid_route_label"})
            continue
        if not features:
            skipped.append({"source": "feedback", "record_id": record_id, "reason": "missing_route_features"})
            continue
        normalized_features = normalize_route_features(features)
        conversion = _mapping(record.get("conversion"))
        quality = _mapping(record.get("quality"))
        example = {
            "case_id": str(record.get("case_id", "") or record_id),
            "input_path": str(conversion.get("source_path") or conversion.get("report_path") or ""),
            "input_type": str(conversion.get("source_type") or normalized_features.get("input_type") or "pdf"),
            "document_class": str(conversion.get("document_class", "") or ""),
            "language": str(conversion.get("language", "") or ""),
            "label": route_label,
            "features": normalized_features,
            "features_hash": route_features_hash(normalized_features),
            "heuristic_profile": str(route.get("heuristic_profile", "") or ""),
            "heuristic_confidence": round(_float_value(route.get("heuristic_confidence")), 3),
            "feedback_record_id": record_id,
            "feedback_status": str(feedback.get("status", "") or ""),
            "feedback_quality_label": str(feedback.get("quality_label", "") or ""),
            "feedback_quality_score": feedback.get("quality_score"),
            "feedback_issue_tags": list(feedback.get("issue_tags", []) or []),
            "quality_status": str(quality.get("status", "") or ""),
        }
        examples.append(example)
    return examples, skipped


def _conversion_summary(
    payload: Mapping[str, Any],
    *,
    report_path: str | Path | None,
    source_path: str | Path | None,
    output_path: str | Path | None,
) -> dict[str, Any]:
    document_summary = _mapping(payload.get("document_summary"))
    analysis = _analysis_payload(payload)
    source_type = _source_type(payload, source_path=source_path)
    return {
        "source_type": source_type,
        "source_path": str(source_path or payload.get("source_path", "") or ""),
        "output_path": str(output_path or payload.get("output_path", "") or ""),
        "report_path": str(report_path or ""),
        "output_size_bytes": _output_size(payload),
        "title": str(document_summary.get("title", "") or _mapping(analysis).get("title", "") or ""),
        "author": str(document_summary.get("author", "") or _mapping(analysis).get("author", "") or ""),
        "language": str(document_summary.get("language", "") or _mapping(payload.get("document")).get("language", "") or ""),
        "profile": str(document_summary.get("profile", "") or _mapping(analysis).get("profile", "") or ""),
        "document_class": str(payload.get("document_class", "") or ""),
    }


def _route_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    analysis = _analysis_payload(payload)
    decision = _route_decision(payload)
    features = _route_features(payload)
    return {
        "mode": str(decision.get("mode", "") or ""),
        "heuristic_profile": str(decision.get("heuristic_profile", "") or _mapping(analysis).get("profile", "") or ""),
        "heuristic_confidence": round(_float_value(decision.get("heuristic_confidence", _mapping(analysis).get("confidence", 0.0))), 3),
        "selected_profile": str(decision.get("selected_profile", "") or ""),
        "ml_profile": str(decision.get("ml_profile", "") or ""),
        "ml_confidence": round(_float_value(decision.get("ml_confidence", 0.0)), 6),
        "override_used": bool(decision.get("override_used", False)),
        "model_version": str(decision.get("model_version", "") or ""),
        "reason_codes": list(decision.get("reason_codes", []) or []),
        "features": features,
        "features_hash": route_features_hash(features) if features else str(decision.get("input_features_hash", "") or ""),
        "reported_features_hash": str(decision.get("input_features_hash", "") or ""),
    }


def _route_features(payload: Mapping[str, Any]) -> dict[str, Any]:
    analysis = _analysis_payload(payload)
    if not analysis:
        return {}
    source_type = _source_type(payload)
    try:
        if source_type == "docx":
            from ml_features import docx_route_feature_payload

            return docx_route_feature_payload(analysis)
        return route_feature_payload(analysis, input_type=source_type)
    except Exception:
        return {}


def _quality_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    quality = _mapping(payload.get("quality_report"))
    blockers = _list_value(quality.get("quality_blockers")) or _list_value(quality.get("blockers"))
    alerts = _list_value(quality.get("alerts"))
    warnings = _list_value(quality.get("warnings"))
    status = (
        quality.get("overall_status")
        or quality.get("validation_status")
        or quality.get("status")
        or ("blocked" if blockers else "unknown")
    )
    return {
        "status": str(status or "unknown"),
        "blocker_count": len(blockers),
        "alert_count": len(alerts),
        "warning_count": len(warnings),
        "final_output_size_bytes": _int_value(quality.get("final_output_size_bytes")),
        "size_budget_status": str(quality.get("size_budget_status", "") or ""),
        "quality_selection": _quality_selection_summary(_mapping(quality.get("quality_selection"))),
    }


def _quality_selection_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "status": str(payload.get("status", "") or ""),
        "selected_candidate": str(payload.get("selected_candidate") or payload.get("selected_stage") or ""),
        "rejected_candidate": str(payload.get("rejected_candidate") or payload.get("rejected_stage") or ""),
        "score_delta": _float_value(payload.get("score_delta")),
        "blocker_delta": _int_value(payload.get("blocker_delta")),
        "reason_codes": list(payload.get("reason_codes") or []),
    }


def _quality_label_from_metadata(metadata: Mapping[str, Any]) -> str:
    premium = _mapping(metadata.get("premium_scoring"))
    if premium.get("premium_ready") is True:
        return "good"
    if premium.get("kindle_ready") is True:
        return "usable"
    if str(premium.get("status", "") or "").lower() == "failed":
        return "blocked"
    return "unknown"


def _analysis_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    analysis = payload.get("analysis")
    if isinstance(analysis, Mapping):
        return analysis
    document = _mapping(payload.get("document"))
    document_analysis = document.get("analysis")
    if isinstance(document_analysis, Mapping):
        return document_analysis
    return {}


def _route_decision(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for candidate in (
        payload.get("route_decision"),
        _analysis_payload(payload).get("route_decision"),
        _mapping(payload.get("document")).get("route_decision"),
        _mapping(_mapping(payload.get("document")).get("analysis")).get("route_decision"),
    ):
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _source_type(payload: Mapping[str, Any], *, source_path: str | Path | None = None) -> str:
    raw = str(payload.get("source_type", "") or _analysis_payload(payload).get("source_type", "") or "").strip().lower()
    if raw in {"pdf", "docx"}:
        return raw
    suffix = Path(str(source_path or "")).suffix.lower().lstrip(".")
    return suffix if suffix in {"pdf", "docx"} else "pdf"


def _case_id_from_report(payload: Mapping[str, Any], conversion: Mapping[str, Any], route: Mapping[str, Any]) -> str:
    raw = payload.get("case_id") or Path(str(conversion.get("source_path") or conversion.get("report_path") or "conversion")).stem
    base = str(raw or "conversion").strip() or "conversion"
    digest = sha256(f"{base}|{route.get('features_hash', '')}".encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def _record_id(
    *,
    created_at: str,
    case_id: str,
    report_path: str,
    source_path: str,
    route_label: str,
    features_hash: str,
) -> str:
    payload = {
        "created_at": created_at,
        "case_id": case_id,
        "report_path": report_path,
        "source_path": source_path,
        "route_label": route_label,
        "features_hash": features_hash,
    }
    digest = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]
    return f"fb_{digest}"


def _dataset_reason(route_label: str, features: Mapping[str, Any]) -> str:
    if route_label not in ROUTE_LABELS:
        return "missing_or_invalid_route_label"
    if not features:
        return "missing_route_features"
    return "ready"


def _output_size(payload: Mapping[str, Any]) -> int:
    quality = _mapping(payload.get("quality_report"))
    value = quality.get("final_output_size_bytes")
    if value is not None:
        return _int_value(value)
    epub_bytes = str(payload.get("epub_bytes", "") or "")
    if epub_bytes.startswith("<") and " bytes" in epub_bytes:
        return _int_value(epub_bytes.strip("<>").split()[0])
    return 0


def _clean_timestamp(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw:
        return raw
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_choice(value: Any, choices: tuple[str, ...], *, default: str) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in choices else default


def _clean_route_label(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized if normalized in ROUTE_LABELS else ""


def _clean_issue_tags(values: Iterable[str]) -> list[str]:
    if isinstance(values, str):
        values = [values]
    tags: list[str] = []
    for value in values:
        for part in str(value or "").split(","):
            normalized = part.strip().lower().replace(" ", "_")
            if normalized and normalized not in tags:
                tags.append(normalized)
    return tags


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(max(0.0, min(float(value), 5.0)), 3)
    except (TypeError, ValueError):
        return None


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _path_list(value: Iterable[str | Path] | str | Path | None) -> list[str | Path]:
    if value is None:
        return [DEFAULT_FEEDBACK_LOG_PATH]
    if isinstance(value, (str, Path)):
        return [value]
    return list(value)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")
