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
    _write_jsonl(route_path, route_examples)
    _write_jsonl(feedback_route_path, feedback_route_examples)
    _write_jsonl(magazine_quality_path, magazine_quality_examples)
    _write_jsonl(quality_feedback_path, quality_feedback_examples)
    _write_jsonl(review_path, heading_reference_examples)

    label_counts = dict(Counter(example["label"] for example in route_examples))
    missing_classes = [label for label in ROUTE_LABELS if label_counts.get(label, 0) <= 0]
    completeness_status = "ready" if not missing_classes else "insufficient_data"
    completeness = {
        "status": completeness_status,
        "route_example_count": len(route_examples),
        "manifest_route_example_count": len(route_examples) - len(feedback_route_examples),
        "feedback_record_count": len(feedback_records),
        "feedback_route_example_count": len(feedback_route_examples),
        "magazine_quality_example_count": len(magazine_quality_examples),
        "quality_feedback_example_count": len(quality_feedback_examples),
        "quality_feedback_role_counts": dict(Counter(example.get("dataset_role", "unknown") for example in quality_feedback_examples)),
        "heading_reference_example_count": len(heading_reference_examples),
        "route_label_counts": label_counts,
        "missing_route_classes": missing_classes,
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
        },
        "analysis_mode": "analysis_only_no_full_conversion",
        "online_learning": False,
    }
    completeness_path.write_text(json.dumps(completeness, ensure_ascii=False, indent=2), encoding="utf-8")
    return completeness


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build KindleMaster ML datasets without running full conversion.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--labels", default="reference_inputs/ml_labels.json")
    parser.add_argument("--reports-root", default="reports")
    parser.add_argument("--output-dir", default="reports/ml/datasets")
    parser.add_argument("--feedback-log", action="append", default=[])
    args = parser.parse_args()
    payload = build_ml_datasets(
        manifest_path=args.manifest,
        labels_path=args.labels,
        reports_root=args.reports_root,
        output_dir=args.output_dir,
        feedback_log_paths=args.feedback_log,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
