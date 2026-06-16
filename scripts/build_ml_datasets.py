from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
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
        log_paths=feedback_log_paths,
        repo_root=root,
    ) if feedback_log_paths else ([], [])
    feedback_route_examples, feedback_route_skipped = route_examples_from_feedback(feedback_records)
    magazine_quality_examples, magazine_quality_skipped = magazine_quality_examples_from_feedback(feedback_records)
    quality_feedback_examples, quality_feedback_skipped = quality_feedback_examples_from_feedback(feedback_records)
    route_examples.extend(feedback_route_examples)

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


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _resolve_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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
