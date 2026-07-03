from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import time_ns
from typing import Any, Callable, Iterable, Mapping

from ml_features import ROUTE_LABELS, route_example_from_analysis
from ml_feedback import (
    load_feedback_records,
    magazine_quality_examples_from_feedback,
    quality_feedback_examples_from_feedback,
    route_examples_from_feedback,
)


Analyzer = Callable[[str], Any]
MIN_ROUTE_EXAMPLES_PER_CLASS = 25
QUALITY_COVERAGE_CLASSES = (
    "magazine",
    "dense_handbook",
    "business_report",
    "scan_ocr",
    "diagram_chess",
    "docx_rich",
)
QUALITY_COVERAGE_MIN_ACCEPTED = 10


def build_ml_datasets(
    *,
    manifest_path: str | Path = "reference_inputs/manifest.json",
    labels_path: str | Path = "reference_inputs/ml_labels.json",
    reports_root: str | Path = "reports",
    output_dir: str | Path = "reports/ml/datasets",
    repo_root: str | Path = ".",
    feedback_log_paths: Iterable[str | Path] | None = None,
    pdf_analyzer: Analyzer | None = None,
    docx_analyzer: Analyzer | None = None,
    fail_on_collisions: bool = False,
    min_examples_per_class: int = MIN_ROUTE_EXAMPLES_PER_CLASS,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    manifest_file = _resolve_path(root, manifest_path)
    labels_file = _resolve_path(root, labels_path)
    output_root = _resolve_path(root, output_dir)
    reports_root_path = _resolve_path(root, reports_root)
    feedback_log_path_list = list(feedback_log_paths or [])
    output_root.mkdir(parents=True, exist_ok=True)

    manifest = _load_json(manifest_file)
    labels_payload = _load_json(labels_file)
    if not isinstance(manifest, Mapping):
        return {"status": "failed", "error": f"Invalid manifest: {manifest_file}"}
    if not isinstance(labels_payload, Mapping):
        return {"status": "failed", "error": f"Invalid ML labels: {labels_file}"}

    labels = _label_map(labels_payload)
    route_examples: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for case in manifest.get("cases", []) or []:
        if not isinstance(case, Mapping):
            continue
        case_id = str(case.get("id", "") or "").strip()
        if str(case.get("ml_training") or "").strip().lower() == "full_corpus_only":
            skipped.append({"case_id": case_id, "reason": "full_corpus_only"})
            continue
        input_type = str(case.get("input_type", "") or "").strip().lower()
        if input_type not in {"pdf", "docx"}:
            skipped.append({"case_id": case_id, "reason": f"unsupported_input_type:{input_type or 'unknown'}"})
            continue
        label = labels.get(case_id, {}).get("route_label", "")
        if label not in ROUTE_LABELS:
            skipped.append({"case_id": case_id, "reason": "missing_or_invalid_route_label"})
            continue
        input_path = _case_input_path(root, case)
        if input_path is None or not input_path.exists():
            skipped.append({"case_id": case_id, "reason": "missing_input", "path": str(input_path or "")})
            continue
        try:
            analysis = _run_analysis_only(input_path, input_type, pdf_analyzer=pdf_analyzer, docx_analyzer=docx_analyzer)
            route_examples.append(
                route_example_from_analysis(
                    case_id=case_id,
                    input_path=input_path,
                    input_type=input_type,
                    label=label,
                    analysis=analysis,
                    document_class=str(case.get("document_class", "") or ""),
                    language=str(case.get("language", "") or ""),
                )
            )
        except Exception as error:
            skipped.append({"case_id": case_id, "reason": "analysis_failed", "error": str(error), "path": str(input_path)})

    feedback_records, feedback_load_skipped = load_feedback_records(
        log_paths=feedback_log_path_list,
        repo_root=root,
    ) if feedback_log_path_list else ([], [])
    feedback_route_examples, feedback_route_skipped = route_examples_from_feedback(feedback_records)
    magazine_quality_examples, magazine_quality_skipped = magazine_quality_examples_from_feedback(feedback_records)
    quality_feedback_examples, quality_feedback_skipped = quality_feedback_examples_from_feedback(feedback_records)
    route_examples.extend(feedback_route_examples)
    quality_coverage = _quality_coverage_summary(quality_feedback_examples)

    heading_reference_examples = list(_collect_heading_reference_examples(reports_root_path))
    route_path = output_root / "route_examples.jsonl"
    feedback_route_path = output_root / "feedback_route_examples.jsonl"
    magazine_quality_path = output_root / "magazine_quality_examples.jsonl"
    quality_feedback_path = output_root / "quality_feedback_examples.jsonl"
    review_path = output_root / "heading_reference_examples.jsonl"
    completeness_path = output_root / "completeness_report.json"
    collision_path = output_root / "feature_collision_report.json"
    _write_jsonl(route_path, route_examples)
    _write_jsonl(feedback_route_path, feedback_route_examples)
    _write_jsonl(magazine_quality_path, magazine_quality_examples)
    _write_jsonl(quality_feedback_path, quality_feedback_examples)
    _write_jsonl(review_path, heading_reference_examples)

    label_counts = dict(Counter(example["label"] for example in route_examples))
    missing_classes = [label for label in ROUTE_LABELS if label_counts.get(label, 0) <= 0]
    under_minimum_classes = [
        {"label": label, "count": label_counts.get(label, 0), "minimum": int(min_examples_per_class)}
        for label in ROUTE_LABELS
        if label_counts.get(label, 0) < int(min_examples_per_class)
    ]
    collision_report = build_feature_collision_report(route_examples)
    collision_path.write_text(json.dumps(collision_report, ensure_ascii=False, indent=2), encoding="utf-8")
    readiness = build_dataset_readiness(
        label_counts=label_counts,
        missing_classes=missing_classes,
        under_minimum_classes=under_minimum_classes,
        collision_report=collision_report,
        min_examples_per_class=min_examples_per_class,
    )
    completeness_status = readiness["status"]
    completeness = {
        "status": completeness_status,
        "route_example_count": len(route_examples),
        "manifest_route_example_count": len(route_examples) - len(feedback_route_examples),
        "feedback_record_count": len(feedback_records),
        "feedback_route_example_count": len(feedback_route_examples),
        "magazine_quality_example_count": len(magazine_quality_examples),
        "quality_feedback_example_count": len(quality_feedback_examples),
        "quality_feedback_role_counts": dict(Counter(example.get("dataset_role", "unknown") for example in quality_feedback_examples)),
        "quality_coverage_status": quality_coverage["status"],
        "quality_coverage": quality_coverage,
        "heading_reference_example_count": len(heading_reference_examples),
        "route_label_counts": label_counts,
        "missing_route_classes": missing_classes,
        "under_minimum_route_classes": under_minimum_classes,
        "dataset_readiness": readiness,
        "feature_collision_report": collision_report,
        "skipped": skipped,
        "feedback_skipped": feedback_load_skipped + feedback_route_skipped,
        "magazine_quality_skipped": magazine_quality_skipped,
        "quality_feedback_skipped": quality_feedback_skipped,
        "outputs": {
            "route_examples": str(route_path),
            "feedback_route_examples": str(feedback_route_path),
            "magazine_quality_examples": str(magazine_quality_path),
            "quality_feedback_examples": str(quality_feedback_path),
            "heading_reference_examples": str(review_path),
            "completeness_report": str(completeness_path),
            "feature_collision_report": str(collision_path),
        },
        "analysis_mode": "analysis_only_no_full_conversion",
        "online_learning": False,
    }
    versioned_payload = _publish_versioned_dataset(
        output_root=output_root,
        route_examples=route_examples,
        feedback_route_examples=feedback_route_examples,
        magazine_quality_examples=magazine_quality_examples,
        quality_feedback_examples=quality_feedback_examples,
        heading_reference_examples=heading_reference_examples,
        completeness=completeness,
        collision_report=collision_report,
        readiness=readiness,
        manifest_path=manifest_file,
        labels_path=labels_file,
        reports_root=reports_root_path,
        feedback_log_paths=[_resolve_path(root, path) for path in feedback_log_path_list],
    )
    completeness.update(versioned_payload)
    completeness_path.write_text(json.dumps(completeness, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        from learning_ledger import record_dataset_built

        ledger_record = record_dataset_built(dataset_payload=completeness, repo_root=root)
        completeness["learning_ledger"] = {
            "status": "recorded",
            "event_id": str(ledger_record.get("event_id", "") or ""),
            "events_path": str(ledger_record.get("events_path", "") or ""),
            "index_path": str(ledger_record.get("index_path", "") or ""),
        }
        completeness_path.write_text(json.dumps(completeness, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        completeness["learning_ledger"] = {"status": "failed", "error": str(error)}
        completeness_path.write_text(json.dumps(completeness, ensure_ascii=False, indent=2), encoding="utf-8")
    if fail_on_collisions and readiness["status"] == "blocked_feature_collision":
        completeness["error"] = "feature_collision"
    return completeness


def build_feature_collision_report(examples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for example in examples:
        feature_hash = str(example.get("features_hash", "") or "").strip()
        if not feature_hash:
            continue
        groups.setdefault(feature_hash, []).append(example)

    collisions: list[dict[str, Any]] = []
    for feature_hash, rows in sorted(groups.items()):
        labels = sorted({str(row.get("label", "") or "") for row in rows if row.get("label")})
        if len(labels) <= 1:
            continue
        collisions.append(
            {
                "features_hash": feature_hash,
                "labels": labels,
                "case_ids": [str(row.get("case_id", "") or "") for row in rows],
                "example_count": len(rows),
            }
        )
    return {
        "status": "blocked_feature_collision" if collisions else "passed",
        "collision_count": len(collisions),
        "collisions": collisions,
    }


def build_dataset_readiness(
    *,
    label_counts: Mapping[str, int],
    missing_classes: list[str],
    under_minimum_classes: list[Mapping[str, Any]],
    collision_report: Mapping[str, Any],
    min_examples_per_class: int,
) -> dict[str, Any]:
    reason_codes: list[str] = []
    if int(collision_report.get("collision_count", 0) or 0) > 0:
        reason_codes.append("feature_hash_label_collision")
    if missing_classes:
        reason_codes.append("missing_route_classes")
    if under_minimum_classes:
        reason_codes.append("under_minimum_examples_per_class")

    if "feature_hash_label_collision" in reason_codes:
        status = "blocked_feature_collision"
    elif reason_codes:
        status = "insufficient_data"
    else:
        status = "ready"
    return {
        "status": status,
        "reason_codes": reason_codes,
        "min_examples_per_class": int(min_examples_per_class),
        "route_label_counts": dict(label_counts),
        "missing_route_classes": list(missing_classes),
        "under_minimum_route_classes": [dict(item) for item in under_minimum_classes],
        "feature_collision_count": int(collision_report.get("collision_count", 0) or 0),
        "promotion_allowed": status == "ready",
    }


def _publish_versioned_dataset(
    *,
    output_root: Path,
    route_examples: list[dict[str, Any]],
    feedback_route_examples: list[dict[str, Any]],
    magazine_quality_examples: list[dict[str, Any]],
    quality_feedback_examples: list[dict[str, Any]],
    heading_reference_examples: list[dict[str, Any]],
    completeness: Mapping[str, Any],
    collision_report: Mapping[str, Any],
    readiness: Mapping[str, Any],
    manifest_path: Path,
    labels_path: Path,
    reports_root: Path,
    feedback_log_paths: list[Path],
) -> dict[str, Any]:
    generated_at = _utc_now()
    dataset_hash = _dataset_content_hash(
        {
            "route_examples": route_examples,
            "feedback_route_examples": feedback_route_examples,
            "magazine_quality_examples": magazine_quality_examples,
            "quality_feedback_examples": quality_feedback_examples,
            "heading_reference_examples": heading_reference_examples,
            "feature_collision_report": collision_report,
            "dataset_readiness": readiness,
        }
    )
    dataset_version = _dataset_version(dataset_hash)
    version_dir = output_root / "versions" / dataset_version
    version_dir.mkdir(parents=True, exist_ok=False)

    versioned_outputs = {
        "route_examples": version_dir / "route_examples.jsonl",
        "feedback_route_examples": version_dir / "feedback_route_examples.jsonl",
        "quality_feedback_examples": version_dir / "quality_feedback_examples.jsonl",
        "magazine_quality_examples": version_dir / "magazine_quality_examples.jsonl",
        "heading_reference_examples": version_dir / "heading_reference_examples.jsonl",
        "dataset_card_json": version_dir / "dataset_card.json",
        "dataset_card_md": version_dir / "dataset_card.md",
        "readiness_report": version_dir / "readiness_report.json",
        "feature_collision_report": version_dir / "feature_collision_report.json",
    }
    _write_jsonl(versioned_outputs["route_examples"], route_examples)
    _write_jsonl(versioned_outputs["feedback_route_examples"], feedback_route_examples)
    _write_jsonl(versioned_outputs["quality_feedback_examples"], quality_feedback_examples)
    _write_jsonl(versioned_outputs["magazine_quality_examples"], magazine_quality_examples)
    _write_jsonl(versioned_outputs["heading_reference_examples"], heading_reference_examples)
    _write_json(versioned_outputs["feature_collision_report"], collision_report)

    dashboard = _build_training_readiness_dashboard(
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        generated_at=generated_at,
        completeness=completeness,
        readiness=readiness,
        collision_report=collision_report,
        route_examples=route_examples,
        quality_feedback_examples=quality_feedback_examples,
        magazine_quality_examples=magazine_quality_examples,
        heading_reference_examples=heading_reference_examples,
    )
    dataset_card = _build_dataset_card(
        dataset_version=dataset_version,
        dataset_hash=dataset_hash,
        generated_at=generated_at,
        completeness=completeness,
        readiness=readiness,
        dashboard=dashboard,
        manifest_path=manifest_path,
        labels_path=labels_path,
        reports_root=reports_root,
        feedback_log_paths=feedback_log_paths,
        version_dir=version_dir,
    )
    _write_json(versioned_outputs["dataset_card_json"], dataset_card)
    versioned_outputs["dataset_card_md"].write_text(_dataset_card_markdown(dataset_card), encoding="utf-8")
    _write_json(versioned_outputs["readiness_report"], dashboard)

    latest_pointer_path = output_root / "latest.json"
    latest_readiness_json = output_root / "latest_readiness.json"
    latest_readiness_html = output_root / "latest_readiness.html"
    latest_pointer = {
        "schema": "kindlemaster.ml.dataset.latest.v1",
        "dataset_version": dataset_version,
        "dataset_hash": dataset_hash,
        "generated_at": generated_at,
        "version_dir": str(version_dir),
        "dataset_card": str(versioned_outputs["dataset_card_json"]),
        "readiness_report": str(versioned_outputs["readiness_report"]),
        "latest_readiness_json": str(latest_readiness_json),
        "latest_readiness_html": str(latest_readiness_html),
    }
    _write_json(latest_pointer_path, latest_pointer)
    _write_json(latest_readiness_json, dashboard)
    latest_readiness_html.write_text(_readiness_dashboard_html(dashboard), encoding="utf-8")

    return {
        "generated_at": generated_at,
        "dataset_version": dataset_version,
        "dataset_hash": dataset_hash,
        "training_readiness_status": str(dashboard["summary"]["training_readiness_status"]),
        "promotion_allowed": bool(dashboard["summary"]["promotion_allowed"]),
        "dataset_card": str(versioned_outputs["dataset_card_json"]),
        "dataset_card_md": str(versioned_outputs["dataset_card_md"]),
        "readiness_report": str(versioned_outputs["readiness_report"]),
        "latest_pointer": str(latest_pointer_path),
        "latest_readiness_json": str(latest_readiness_json),
        "latest_readiness_html": str(latest_readiness_html),
        "version_dir": str(version_dir),
        "versioned_outputs": {key: str(path) for key, path in versioned_outputs.items()},
    }


def _build_training_readiness_dashboard(
    *,
    dataset_version: str,
    dataset_hash: str,
    generated_at: str,
    completeness: Mapping[str, Any],
    readiness: Mapping[str, Any],
    collision_report: Mapping[str, Any],
    route_examples: list[dict[str, Any]],
    quality_feedback_examples: list[dict[str, Any]],
    magazine_quality_examples: list[dict[str, Any]],
    heading_reference_examples: list[dict[str, Any]],
) -> dict[str, Any]:
    quality_coverage = _mapping(completeness.get("quality_coverage"))
    route_status = str(readiness.get("status") or "insufficient_data")
    route_promotion_allowed = bool(readiness.get("promotion_allowed"))
    components = {
        "route_model": {
            "status": route_status,
            "promotion_allowed": route_promotion_allowed,
            "example_count": len(route_examples),
            "route_label_counts": dict(_mapping(readiness.get("route_label_counts"))),
            "missing_route_classes": list(readiness.get("missing_route_classes") or []),
            "under_minimum_route_classes": list(readiness.get("under_minimum_route_classes") or []),
            "reason_codes": list(readiness.get("reason_codes") or []),
        },
        "quality_model": {
            "status": str(quality_coverage.get("status") or "insufficient_data"),
            "example_count": len(quality_feedback_examples),
            "accepted_example_count": int(quality_coverage.get("accepted_example_count", 0) or 0),
            "minimum_accepted_per_class": int(quality_coverage.get("minimum_accepted_per_class", 0) or 0),
            "gaps": list(quality_coverage.get("gaps") or []),
        },
        "chess": _domain_readiness_component(
            name="chess",
            rows=quality_feedback_examples + magazine_quality_examples + heading_reference_examples,
            tokens=("chess", "diagram", "fen", "pgn", "board", "side_marker"),
        ),
        "layout_presentation": _domain_readiness_component(
            name="layout_presentation",
            rows=quality_feedback_examples + magazine_quality_examples,
            tokens=("layout", "presentation", "magazine", "toc", "nav", "article", "image"),
        ),
    }
    training_readiness_status = route_status
    if route_status == "ready" and int(collision_report.get("collision_count", 0) or 0) > 0:
        training_readiness_status = "blocked_feature_collision"
    summary = {
        "dataset_version": dataset_version,
        "dataset_hash": dataset_hash,
        "generated_at": generated_at,
        "can_train_route_model": route_status == "ready",
        "training_readiness_status": training_readiness_status,
        "promotion_allowed": route_promotion_allowed and training_readiness_status == "ready",
        "route_example_count": len(route_examples),
        "feedback_record_count": int(completeness.get("feedback_record_count", 0) or 0),
        "quality_feedback_count": len(quality_feedback_examples),
        "magazine_quality_count": len(magazine_quality_examples),
        "heading_reference_count": len(heading_reference_examples),
        "feature_collision_count": int(collision_report.get("collision_count", 0) or 0),
    }
    return {
        "schema": "kindlemaster.ml.dataset.readiness.v1",
        "summary": summary,
        "components": components,
        "feature_collision_report": dict(collision_report),
        "next_actions": _readiness_next_actions(summary, components),
    }


def _domain_readiness_component(
    *,
    name: str,
    rows: Iterable[Mapping[str, Any]],
    tokens: tuple[str, ...],
) -> dict[str, Any]:
    matched: list[Mapping[str, Any]] = []
    accepted = 0
    label_counts: Counter[str] = Counter()
    for row in rows:
        marker = _row_marker_text(row)
        if not any(token in marker for token in tokens):
            continue
        matched.append(row)
        if str(row.get("feedback_status", "") or "").lower() == "accepted":
            accepted += 1
        label_counts[str(row.get("final_label") or row.get("quality_label") or row.get("label") or "unknown")] += 1
    status = "ready" if accepted >= QUALITY_COVERAGE_MIN_ACCEPTED else "data_gap"
    return {
        "status": status,
        "component": name,
        "example_count": len(matched),
        "accepted_example_count": accepted,
        "minimum_accepted_count": QUALITY_COVERAGE_MIN_ACCEPTED,
        "gap_to_minimum": max(0, QUALITY_COVERAGE_MIN_ACCEPTED - accepted),
        "label_counts": dict(label_counts),
    }


def _row_marker_text(row: Mapping[str, Any]) -> str:
    parts = [
        str(row.get("dataset_role", "") or ""),
        str(row.get("document_class", "") or ""),
        str(row.get("input_type", "") or ""),
        str(row.get("route_label", "") or ""),
        str(row.get("source_path", "") or ""),
        str(row.get("report_path", "") or ""),
        " ".join(str(tag) for tag in row.get("issue_tags", []) or []),
    ]
    return " ".join(parts).lower()


def _readiness_next_actions(summary: Mapping[str, Any], components: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if str(summary.get("training_readiness_status") or "") != "ready":
        actions.append(
            {
                "area": "route_model",
                "reason": str(summary.get("training_readiness_status") or "insufficient_data"),
                "action": "Add labeled route examples or resolve feature collisions before training.",
            }
        )
    for component_name, component in components.items():
        if str(_mapping(component).get("status") or "") in {"ready", "passed"}:
            continue
        actions.append(
            {
                "area": component_name,
                "reason": str(_mapping(component).get("status") or "data_gap"),
                "action": "Collect accepted feedback examples for this publication class before promoting quality-specific models.",
            }
        )
    return actions


def _build_dataset_card(
    *,
    dataset_version: str,
    dataset_hash: str,
    generated_at: str,
    completeness: Mapping[str, Any],
    readiness: Mapping[str, Any],
    dashboard: Mapping[str, Any],
    manifest_path: Path,
    labels_path: Path,
    reports_root: Path,
    feedback_log_paths: list[Path],
    version_dir: Path,
) -> dict[str, Any]:
    return {
        "schema": "kindlemaster.ml.dataset_card.v1",
        "dataset_version": dataset_version,
        "dataset_hash": dataset_hash,
        "generated_at": generated_at,
        "version_dir": str(version_dir),
        "sources": {
            "manifest": str(manifest_path),
            "labels": str(labels_path),
            "reports_root": str(reports_root),
            "feedback_logs": [str(path) for path in feedback_log_paths],
        },
        "counts": {
            "route_examples": int(completeness.get("route_example_count", 0) or 0),
            "manifest_route_examples": int(completeness.get("manifest_route_example_count", 0) or 0),
            "feedback_records": int(completeness.get("feedback_record_count", 0) or 0),
            "feedback_route_examples": int(completeness.get("feedback_route_example_count", 0) or 0),
            "quality_feedback_examples": int(completeness.get("quality_feedback_example_count", 0) or 0),
            "magazine_quality_examples": int(completeness.get("magazine_quality_example_count", 0) or 0),
            "heading_reference_examples": int(completeness.get("heading_reference_example_count", 0) or 0),
        },
        "route_readiness": dict(readiness),
        "training_readiness_status": str(_mapping(dashboard.get("summary")).get("training_readiness_status") or ""),
        "promotion_allowed": bool(_mapping(dashboard.get("summary")).get("promotion_allowed")),
        "privacy": {
            "stores_text": False,
            "stores_source_file": False,
            "stores_features_and_feedback_only": True,
            "cloud_sync_status": "local_only",
        },
        "feedback": {
            "record_count": int(completeness.get("feedback_record_count", 0) or 0),
            "route_example_count": int(completeness.get("feedback_route_example_count", 0) or 0),
            "quality_example_count": int(completeness.get("quality_feedback_example_count", 0) or 0),
            "skipped_count": len(list(completeness.get("feedback_skipped") or [])),
        },
    }


def _dataset_card_markdown(card: Mapping[str, Any]) -> str:
    counts = _mapping(card.get("counts"))
    feedback = _mapping(card.get("feedback"))
    sources = _mapping(card.get("sources"))
    lines = [
        f"# ML Dataset {card.get('dataset_version', '')}",
        "",
        f"- Status: {card.get('training_readiness_status', '')}",
        f"- Promotion allowed: {bool(card.get('promotion_allowed'))}",
        f"- Dataset hash: `{card.get('dataset_hash', '')}`",
        f"- Generated at: {card.get('generated_at', '')}",
        f"- Version dir: `{card.get('version_dir', '')}`",
        "",
        "## Counts",
        "",
        f"- Route examples: {counts.get('route_examples', 0)}",
        f"- Feedback records: {counts.get('feedback_records', 0)}",
        f"- Quality feedback examples: {counts.get('quality_feedback_examples', 0)}",
        f"- Magazine quality examples: {counts.get('magazine_quality_examples', 0)}",
        f"- Heading/reference examples: {counts.get('heading_reference_examples', 0)}",
        "",
        "## Sources",
        "",
        f"- Manifest: `{sources.get('manifest', '')}`",
        f"- Labels: `{sources.get('labels', '')}`",
        f"- Reports root: `{sources.get('reports_root', '')}`",
        f"- Feedback logs: {len(list(sources.get('feedback_logs') or []))}",
        "",
        "## Privacy",
        "",
        "- Stores text: false",
        "- Stores source file: false",
        "- Scope: local feature and feedback datasets",
        "",
        "## Feedback",
        "",
        f"- Feedback route examples: {feedback.get('route_example_count', 0)}",
        f"- Feedback quality examples: {feedback.get('quality_example_count', 0)}",
        f"- Feedback skipped: {feedback.get('skipped_count', 0)}",
        "",
    ]
    return "\n".join(lines)


def _readiness_dashboard_html(report: Mapping[str, Any]) -> str:
    summary = _mapping(report.get("summary"))
    components = _mapping(report.get("components"))
    actions = list(report.get("next_actions") or [])
    cards = [
        ("Can train route model", "yes" if summary.get("can_train_route_model") else "no"),
        ("Training status", summary.get("training_readiness_status", "")),
        ("Promotion allowed", "yes" if summary.get("promotion_allowed") else "no"),
        ("Route examples", summary.get("route_example_count", 0)),
        ("Quality feedback", summary.get("quality_feedback_count", 0)),
        ("Feature collisions", summary.get("feature_collision_count", 0)),
    ]
    card_html = "\n".join(
        f"<div class=\"card\"><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong></div>"
        for label, value in cards
    )
    component_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(name))}</td>"
        f"<td>{html.escape(str(_mapping(component).get('status', '')))}</td>"
        f"<td>{html.escape(str(_mapping(component).get('example_count', '')))}</td>"
        f"<td>{html.escape(str(_mapping(component).get('accepted_example_count', '')))}</td>"
        f"<td>{html.escape(str(_mapping(component).get('gap_to_minimum', '')))}</td>"
        "</tr>"
        for name, component in components.items()
    )
    action_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(_mapping(action).get('area', '')))}</td>"
        f"<td>{html.escape(str(_mapping(action).get('reason', '')))}</td>"
        f"<td>{html.escape(str(_mapping(action).get('action', '')))}</td>"
        "</tr>"
        for action in actions
    ) or "<tr><td colspan=\"3\">No blocking next action.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>KindleMaster ML Dataset Readiness</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; color: #172033; background: #f5f7fb; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 24px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .muted {{ color: #5d6a7d; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 24px 0; }}
    .card {{ border: 1px solid #d6deea; background: #fff; border-radius: 8px; padding: 14px; }}
    .card span {{ display: block; color: #5d6a7d; font-size: 12px; margin-bottom: 8px; }}
    .card strong {{ font-size: 18px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d6deea; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid #e3e8f0; text-align: left; vertical-align: top; }}
    th {{ font-size: 12px; text-transform: uppercase; color: #5d6a7d; background: #f9fbff; }}
    section {{ margin-top: 28px; }}
  </style>
</head>
<body>
<main>
  <h1>KindleMaster ML Dataset Readiness</h1>
  <p class="muted">Dataset version {html.escape(str(summary.get('dataset_version', '')))} generated at {html.escape(str(summary.get('generated_at', '')))}.</p>
  <div class="grid">{card_html}</div>
  <section>
    <h2>Model areas</h2>
    <table>
      <thead><tr><th>Area</th><th>Status</th><th>Examples</th><th>Accepted</th><th>Gap</th></tr></thead>
      <tbody>{component_rows}</tbody>
    </table>
  </section>
  <section>
    <h2>Next actions</h2>
    <table>
      <thead><tr><th>Area</th><th>Reason</th><th>Action</th></tr></thead>
      <tbody>{action_rows}</tbody>
    </table>
  </section>
</main>
</body>
</html>
"""


def _dataset_content_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(_json_safe(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{sha256(raw.encode('utf-8')).hexdigest()}"


def _dataset_version(dataset_hash: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_hash = sha256(f"{dataset_hash}|{time_ns()}".encode("utf-8")).hexdigest()[:10]
    return f"{stamp}-{run_hash}"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_analysis_only(
    input_path: Path,
    input_type: str,
    *,
    pdf_analyzer: Analyzer | None,
    docx_analyzer: Analyzer | None,
) -> Any:
    if input_type == "pdf":
        if pdf_analyzer is None:
            from publication_analysis import analyze_publication

            pdf_analyzer = lambda path: analyze_publication(path, preferred_profile="auto-premium", route_model_mode="off")
        return pdf_analyzer(str(input_path))
    if docx_analyzer is None:
        from docx_conversion import analyze_docx

        docx_analyzer = lambda path: analyze_docx(path, route_model_mode="off")
    return docx_analyzer(str(input_path))


def _collect_heading_reference_examples(reports_root: Path) -> Iterable[dict[str, Any]]:
    if not reports_root.exists():
        return []
    examples: list[dict[str, Any]] = []
    for report_path in reports_root.rglob("*.json"):
        if "reports/ml" in report_path.as_posix().replace("\\", "/"):
            continue
        payload = _load_json(report_path)
        if isinstance(payload, Mapping):
            examples.extend(_extract_review_examples(payload, report_path=report_path))
    return examples


def _extract_review_examples(payload: Mapping[str, Any], *, report_path: Path) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for path, value in _walk_payload(payload):
        if path.endswith("manual_review_queue") and isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    examples.append(_review_example(item, source="heading", report_path=report_path, index=index))
        if path.endswith("records") and isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, Mapping) and item.get("review_flag"):
                    examples.append(_review_example(item, source="reference", report_path=report_path, index=index))
    return examples


def _review_example(item: Mapping[str, Any], *, source: str, report_path: Path, index: int) -> dict[str, Any]:
    confidence = _float_value(item.get("confidence", 0.0))
    return {
        "source": source,
        "report_path": str(report_path),
        "index": index,
        "label": "review_high" if item.get("review_flag") or confidence < 0.65 else "review_standard",
        "features": {
            "confidence": confidence,
            "review_flag": bool(item.get("review_flag", True)),
            "has_unresolved_fragments": bool(item.get("unresolved_fragments")),
            "status": str(item.get("status", item.get("link_status", "")) or ""),
            "reason": str(item.get("reason", "") or ""),
        },
    }


def _quality_coverage_summary(examples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(example) for example in examples if isinstance(example, Mapping)]
    buckets: dict[str, dict[str, Any]] = {
        name: {
            "example_count": 0,
            "accepted_count": 0,
            "final_label_counts": {},
            "gap_to_minimum": QUALITY_COVERAGE_MIN_ACCEPTED,
            "status": "insufficient_data",
        }
        for name in QUALITY_COVERAGE_CLASSES
    }
    unclassified_count = 0
    for row in rows:
        classes = _quality_example_classes(row)
        if not classes:
            unclassified_count += 1
        for class_name in classes:
            if class_name not in buckets:
                continue
            bucket = buckets[class_name]
            bucket["example_count"] += 1
            if str(row.get("feedback_status", "") or "").lower() == "accepted":
                bucket["accepted_count"] += 1
            label = str(row.get("final_label", "") or "unknown")
            label_counts = dict(bucket.get("final_label_counts") or {})
            label_counts[label] = int(label_counts.get(label, 0)) + 1
            bucket["final_label_counts"] = label_counts
    gaps: list[dict[str, Any]] = []
    for class_name, bucket in buckets.items():
        gap = max(0, QUALITY_COVERAGE_MIN_ACCEPTED - int(bucket["accepted_count"]))
        bucket["gap_to_minimum"] = gap
        bucket["status"] = "ready" if gap == 0 else "insufficient_data"
        if gap:
            gaps.append(
                {
                    "class": class_name,
                    "accepted_count": bucket["accepted_count"],
                    "required_count": QUALITY_COVERAGE_MIN_ACCEPTED,
                    "gap": gap,
                }
            )
    return {
        "status": "ready" if not gaps else "insufficient_data",
        "minimum_accepted_per_class": QUALITY_COVERAGE_MIN_ACCEPTED,
        "classes": buckets,
        "gaps": gaps,
        "example_count": len(rows),
        "unclassified_count": unclassified_count,
        "accepted_example_count": sum(1 for row in rows if str(row.get("feedback_status", "") or "").lower() == "accepted"),
        "online_learning": False,
    }


def _quality_example_classes(row: Mapping[str, Any]) -> list[str]:
    markers = " ".join(
        [
            str(row.get("dataset_role", "") or ""),
            str(row.get("document_class", "") or ""),
            str(row.get("input_type", "") or ""),
            str(row.get("route_label", "") or ""),
            " ".join(str(tag) for tag in row.get("issue_tags", []) or []),
            " ".join(str(value) for value in (row.get("route") or {}).values()) if isinstance(row.get("route"), Mapping) else "",
            str(row.get("source_path", "") or ""),
        ]
    ).lower()
    classes: list[str] = []
    if "magazine" in markers:
        classes.append("magazine")
    if any(token in markers for token in ("dense_handbook", "dense", "handbook", "business_guide")):
        classes.append("dense_handbook")
    if any(token in markers for token in ("business_report", "document_like_report", "report")):
        classes.append("business_report")
    if any(token in markers for token in ("scan", "scanned", "ocr")):
        classes.append("scan_ocr")
    if any(token in markers for token in ("diagram", "chess")):
        classes.append("diagram_chess")
    if str(row.get("input_type", "") or "").lower() == "docx" or "docx" in markers:
        classes.append("docx_rich")
    return list(dict.fromkeys(classes))


def _walk_payload(value: Any, *, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            nested_path = f"{prefix}.{key}" if prefix else str(key)
            yield nested_path, nested
            yield from _walk_payload(nested, prefix=nested_path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _walk_payload(nested, prefix=f"{prefix}[{index}]")


def _label_map(labels_payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_cases = labels_payload.get("cases", {})
    if isinstance(raw_cases, Mapping):
        return {str(case_id): dict(payload) for case_id, payload in raw_cases.items() if isinstance(payload, Mapping)}
    return {}


def _case_input_path(root: Path, case: Mapping[str, Any]) -> Path | None:
    for key in ("target_path", "target", "source_path", "source"):
        raw_value = str(case.get(key, "") or "").strip()
        if not raw_value or raw_value.startswith("<generated:"):
            continue
        path = Path(raw_value)
        return path if path.is_absolute() else root / path
    return None


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _load_json(path: Path) -> Any:
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeError:
            continue
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build KindleMaster ML datasets without running full conversion.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--labels", default="reference_inputs/ml_labels.json")
    parser.add_argument("--reports-root", default="reports")
    parser.add_argument("--output-dir", default="reports/ml/datasets")
    parser.add_argument("--feedback-log", action="append", default=[])
    parser.add_argument("--fail-on-collisions", action="store_true")
    parser.add_argument("--min-examples-per-class", type=int, default=MIN_ROUTE_EXAMPLES_PER_CLASS)
    args = parser.parse_args()
    payload = build_ml_datasets(
        manifest_path=args.manifest,
        labels_path=args.labels,
        reports_root=args.reports_root,
        output_dir=args.output_dir,
        feedback_log_paths=args.feedback_log,
        fail_on_collisions=args.fail_on_collisions,
        min_examples_per_class=args.min_examples_per_class,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.fail_on_collisions and payload.get("status") == "blocked_feature_collision":
        return 2
    return 0 if payload.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
