from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping

from chess_crop_qa_benchmark import load_runtime_rows_from_job_output


SCHEMA = "kindlemaster.chess_fen.two_crop_performance.v1"
DEFAULT_REPORT_DIR = Path("reports/performance/chess_two_crop")

TIMING_KEYS = (
    "localization_seconds",
    "marker_analysis_seconds",
    "png_encoding_seconds",
    "file_write_seconds",
    "ambiguity_probe_seconds",
    "total_seconds",
)
SEMANTIC_FIELDS = (
    "diagram_id",
    "page",
    "raw_board_candidate_bbox",
    "tight_board_bbox",
    "board_bbox",
    "board_bbox_derivation",
    "board_crop_quality",
    "board_crop_fail_reason",
    "board_crop_quality_gate",
    "marker_search_zones",
    "selected_marker_zone",
    "marker_bbox",
    "marker_crop_bbox",
    "marker_crop_quality",
    "marker_crop_fail_reason",
    "marker_crop_quality_gate",
    "side_marker_status",
    "side_marker_symbol",
    "side_to_move",
    "side_to_move_detected",
    "strict_fen_side_evidence_trusted",
    "manual_review_required",
    "manual_review_reason",
    "acceptance_blocker_codes",
)
ARTIFACT_PATH_FIELDS = (
    "board_crop_path",
    "side_marker_crop_path",
    "side_marker_search_crop_path",
    "marker_search_zone_preview_path",
    "side_marker_review_crop_path",
    "debug_overlay_path",
    "debug_context_crop_path",
)


def build_two_crop_semantic_snapshot(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _canonical_value(record.get(key)) for key in SEMANTIC_FIELDS}


def build_two_crop_semantic_digest(records: list[Mapping[str, Any]]) -> str:
    snapshots = [build_two_crop_semantic_snapshot(record) for record in records]
    snapshots.sort(
        key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    payload = json.dumps(snapshots, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_two_crop_performance_report(job_output: str | Path) -> dict[str, Any]:
    root = Path(job_output)
    rows = load_runtime_rows_from_job_output(root)
    performance_rows = [
        dict(row.get("two_crop_performance") or {})
        for row in rows
        if isinstance(row.get("two_crop_performance"), Mapping)
    ]
    artifact_inventory = _artifact_inventory(root, rows)
    provenance = _source_provenance(root)
    corpus_available = root.is_dir() and bool(rows)
    complete_report = root / "reports" / "chess_fen" / "two_crop_quality_metrics.json"
    corpus_complete = complete_report.is_file() and len(performance_rows) == len(rows) and bool(rows)
    stage_timings = {key: _metric_summary(performance_rows, key) for key in TIMING_KEYS}
    localization_paths = _count_strings(performance_rows, "localization_path")
    measured_localization_count = sum(localization_paths.values())
    fast_path_count = localization_paths.get("full_grid_fast_path", 0)
    localization_path_timings = {
        path: _metric_summary(
            [row for row in performance_rows if row.get("localization_path") == path],
            "localization_seconds",
        )
        for path in sorted(localization_paths)
    }
    report = {
        "schema": SCHEMA,
        "status": "ok" if corpus_available else "corpus_unavailable",
        "job_output": str(root),
        "evidence": {
            "corpus_available": corpus_available,
            "corpus_complete": corpus_complete,
            "corpus_enforced": provenance["corpus_enforced"],
            "source_sha256": provenance["source_sha256"],
            "evidence_class": "runtime_job_output" if corpus_available else "none",
            "synthetic_substitution_used": False,
        },
        "summary": {
            "runtime_record_count": len(rows),
            "instrumented_record_count": len(performance_rows),
            "semantic_digest": build_two_crop_semantic_digest(rows) if rows else "",
            "tight_board_localization_call_count": _sum_int(
                performance_rows, "tight_board_localization_call_count"
            ),
            "sliding_window_candidate_evaluations": _sum_int(
                performance_rows, "sliding_window_candidate_evaluations"
            ),
            "localization_paths": localization_paths,
            "localization_reason_codes": _count_list_values(
                performance_rows, "localization_reason_codes"
            ),
            "full_grid_fast_path_count": _sum_int(
                performance_rows, "full_grid_fast_path_count"
            ),
            "full_grid_fallback_count": _sum_int(
                performance_rows, "full_grid_fallback_count"
            ),
            "full_grid_fast_path_coverage_rate": round(
                fast_path_count / measured_localization_count,
                6,
            )
            if measured_localization_count
            else 0.0,
            "full_grid_fallback_rate": round(
                localization_paths.get("sliding_window_fallback", 0)
                / measured_localization_count,
                6,
            )
            if measured_localization_count
            else 0.0,
            "full_grid_probe_evaluations": _sum_int(
                performance_rows, "full_grid_probe_evaluations"
            ),
            "false_fast_path_count": _sum_int(performance_rows, "false_fast_path_count"),
            "png_encoded_artifact_count": _sum_int(performance_rows, "png_encoded_artifact_count"),
            "png_encoded_bytes": _sum_int(performance_rows, "png_encoded_bytes"),
            "file_written_artifact_count": _sum_int(performance_rows, "file_written_artifact_count"),
            "file_written_bytes": _sum_int(performance_rows, "file_written_bytes"),
            "single_pass_record_count": sum(
                1 for row in performance_rows if row.get("board_analysis_mode") == "single_pass"
            ),
            "legacy_fallback_record_count": sum(
                1 for row in performance_rows if bool(row.get("legacy_localization_fallback_used"))
            ),
            "legacy_fallback_reasons": _count_strings(
                performance_rows,
                "legacy_localization_fallback_reason",
            ),
            "ambiguity_probe_evaluations": _sum_int(
                performance_rows,
                "ambiguity_probe_evaluations",
            ),
            "artifact_count": artifact_inventory["artifact_count"],
            "artifact_bytes": artifact_inventory["artifact_bytes"],
        },
        "stage_timings": stage_timings,
        "localization_path_timings": localization_path_timings,
        "artifact_inventory": artifact_inventory,
        "items": [
            {
                "diagram_id": str(row.get("diagram_id") or row.get("id") or ""),
                "page": row.get("page"),
                "semantic_digest": build_two_crop_semantic_digest([row]),
                "performance": dict(row.get("two_crop_performance") or {}),
            }
            for row in sorted(rows, key=lambda item: str(item.get("diagram_id") or item.get("id") or ""))
        ],
    }
    return report


def write_two_crop_performance_reports(
    report: Mapping[str, Any],
    report_dir: str | Path = DEFAULT_REPORT_DIR,
) -> tuple[Path, Path]:
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "baseline.json"
    markdown_path = output_dir / "baseline.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(two_crop_performance_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def two_crop_performance_markdown(report: Mapping[str, Any]) -> str:
    evidence = report.get("evidence") if isinstance(report.get("evidence"), Mapping) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    timings = report.get("stage_timings") if isinstance(report.get("stage_timings"), Mapping) else {}
    path_timings = (
        report.get("localization_path_timings")
        if isinstance(report.get("localization_path_timings"), Mapping)
        else {}
    )
    lines = [
        "# Chess Two-Crop Performance Baseline",
        "",
        f"- status: `{report.get('status', 'unknown')}`",
        f"- corpus available: `{str(bool(evidence.get('corpus_available'))).lower()}`",
        f"- corpus complete: `{str(bool(evidence.get('corpus_complete'))).lower()}`",
        f"- corpus enforced: `{str(bool(evidence.get('corpus_enforced'))).lower()}`",
        f"- synthetic substitution used: `{str(bool(evidence.get('synthetic_substitution_used'))).lower()}`",
        f"- runtime records: `{summary.get('runtime_record_count', 0)}`",
        f"- instrumented records: `{summary.get('instrumented_record_count', 0)}`",
        f"- semantic digest: `{summary.get('semantic_digest', '')}`",
        f"- localization calls: `{summary.get('tight_board_localization_call_count', 0)}`",
        f"- sliding-window evaluations: `{summary.get('sliding_window_candidate_evaluations', 0)}`",
        f"- localization paths: `{json.dumps(summary.get('localization_paths', {}), sort_keys=True)}`",
        f"- localization reasons: `{json.dumps(summary.get('localization_reason_codes', {}), sort_keys=True)}`",
        f"- full-grid fast paths: `{summary.get('full_grid_fast_path_count', 0)}`",
        f"- full-grid fallbacks: `{summary.get('full_grid_fallback_count', 0)}`",
        f"- full-grid fast-path coverage: `{summary.get('full_grid_fast_path_coverage_rate', 0.0)}`",
        f"- full-grid fallback rate: `{summary.get('full_grid_fallback_rate', 0.0)}`",
        f"- full-grid probe evaluations: `{summary.get('full_grid_probe_evaluations', 0)}`",
        f"- false fast paths: `{summary.get('false_fast_path_count', 0)}`",
        f"- single-pass records: `{summary.get('single_pass_record_count', 0)}`",
        f"- legacy fallback records: `{summary.get('legacy_fallback_record_count', 0)}`",
        f"- legacy fallback reasons: `{json.dumps(summary.get('legacy_fallback_reasons', {}), sort_keys=True)}`",
        f"- ambiguity probe evaluations: `{summary.get('ambiguity_probe_evaluations', 0)}`",
        f"- artifacts: `{summary.get('artifact_count', 0)}` files / `{summary.get('artifact_bytes', 0)}` bytes",
        "",
        "## Stage Timings",
        "",
        "| Stage | Count | Median (s) | P95 (s) | Total (s) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for key in TIMING_KEYS:
        metric = timings.get(key) if isinstance(timings.get(key), Mapping) else {}
        lines.append(
            f"| `{key}` | {metric.get('count', 0)} | {metric.get('median', 0.0)} | "
            f"{metric.get('p95', 0.0)} | {metric.get('total', 0.0)} |"
        )
    lines.extend(
        [
            "",
            "## Localization Path Timings",
            "",
            "| Path | Count | Median (s) | P95 (s) | Total (s) |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for path in sorted(path_timings):
        metric = path_timings.get(path) if isinstance(path_timings.get(path), Mapping) else {}
        lines.append(
            f"| `{path}` | {metric.get('count', 0)} | {metric.get('median', 0.0)} | "
            f"{metric.get('p95', 0.0)} | {metric.get('total', 0.0)} |"
        )
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            "This report measures existing runtime output only. Missing runtime output remains "
            "`corpus_available=false`; generated fixtures are never substituted as real-corpus evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def _metric_summary(rows: list[Mapping[str, Any]], key: str) -> dict[str, Any]:
    values = sorted(float(row.get(key) or 0.0) for row in rows if row.get(key) is not None)
    if not values:
        return {"count": 0, "min": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0, "total": 0.0}
    p95_index = max(0, math.ceil(len(values) * 0.95) - 1)
    return {
        "count": len(values),
        "min": round(values[0], 6),
        "median": round(float(statistics.median(values)), 6),
        "p95": round(values[p95_index], 6),
        "max": round(values[-1], 6),
        "total": round(sum(values), 6),
    }


def _sum_int(rows: list[Mapping[str, Any]], key: str) -> int:
    return sum(int(row.get(key) or 0) for row in rows)


def _count_strings(rows: list[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _count_list_values(rows: list[Mapping[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        values = row.get(key) if isinstance(row.get(key), list) else []
        for raw in values:
            value = str(raw or "").strip()
            if value:
                counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _artifact_inventory(root: Path, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    resolved_root = root.resolve() if root.exists() else root.absolute()
    found: dict[str, int] = {}
    for row in rows:
        for field in ARTIFACT_PATH_FIELDS:
            relative = str(row.get(field) or "").strip().replace("\\", "/")
            if not relative or relative.startswith(("data:", "/")) or ".." in Path(relative).parts:
                continue
            path = (root / relative).resolve()
            try:
                path.relative_to(resolved_root)
            except ValueError:
                continue
            if path.is_file():
                found[relative] = path.stat().st_size
    return {
        "artifact_count": len(found),
        "artifact_bytes": sum(found.values()),
        "by_path": dict(sorted(found.items())),
    }


def _source_provenance(root: Path) -> dict[str, Any]:
    candidates = (
        root / "reports" / "chess_fen" / "fixed_edition_manifest.json",
        root / "reports" / "chess_fen" / "source_manifest.json",
        root / "artifact_manifest.json",
        root / "auto_chess_flow.json",
    )
    source_sha256 = ""
    corpus_enforced = False
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, Mapping):
            continue
        source_sha256 = source_sha256 or _first_string(
            payload,
            "source_sha256",
            "source_pdf_sha256",
            "document_sha256",
        )
        corpus_enforced = corpus_enforced or bool(payload.get("corpus_enforced") or payload.get("enforced"))
    return {"source_sha256": source_sha256, "corpus_enforced": corpus_enforced}


def _first_string(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, float):
        return round(value, 8)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)
