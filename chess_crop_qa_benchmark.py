from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "kindlemaster.chess_fen.crop_qa_benchmark.v1"
DIFF_SCHEMA = "kindlemaster.chess_fen.crop_qa_regression_diff.v1"
REVIEW_ONLY_POLICY = "manual_crop_qa_labels_evaluate_only_no_direct_fen_publication"

FRAGMENTARY_ISSUE_TYPE = "fragmentary_board_crop"
CONFLICT_ISSUE_TYPE = "system_suggestion_mismatch"
MARKER_REVIEW_ISSUE_TYPE = "marker_search_review_only"


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_runtime_rows(path: str | Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    raw_path = Path(path)
    if raw_path.suffix.lower() == ".jsonl":
        return load_jsonl(raw_path)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, dict):
        for key in ("items", "records", "rows", "diagrams"):
            value = payload.get(key)
            if isinstance(value, list):
                return [dict(row) for row in value if isinstance(row, Mapping)]
        queue = payload.get("queue")
        if isinstance(queue, dict) and isinstance(queue.get("items"), list):
            return [dict(row) for row in queue["items"] if isinstance(row, Mapping)]
    return []


JOB_OUTPUT_RUNTIME_FILES = (
    "reports/chess_fen/why_side_to_move_not_trusted.json",
    "reports/chess_fen/two_crop_quality_metrics.json",
    "reports/chess_fen/side_marker_assignment.json",
    "reports/chess_fen/side_marker_blocker_attribution.json",
    "chess_diagrams.json",
    "positions.json",
    "data/diagrams.json",
)


def load_runtime_rows_from_job_output(job_output: str | Path | None) -> list[dict[str, Any]]:
    if job_output is None:
        return []
    root = Path(job_output)
    if not root.is_dir():
        return []
    merged: dict[str, dict[str, Any]] = {}
    for relative in JOB_OUTPUT_RUNTIME_FILES:
        path = root / relative
        if not path.is_file():
            continue
        for row in load_runtime_rows(path):
            diagram_id = str(row.get("diagram_id") or row.get("id") or "").strip()
            if not diagram_id:
                continue
            target = merged.setdefault(diagram_id, {"diagram_id": diagram_id, "runtime_sources": []})
            target["runtime_sources"].append(relative)
            _merge_non_empty(target, row)
    return list(merged.values())


def load_manifest(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    raw_path = Path(path)
    if not raw_path.is_file():
        return {}
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    return dict(payload) if isinstance(payload, Mapping) else {}


def evaluate_crop_qa_benchmark(
    labels_path: str | Path,
    *,
    actual_path: str | Path | None = None,
    job_output_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    labels = [_normalize_expected(row) for row in load_jsonl(labels_path)]
    manifest = load_manifest(manifest_path)
    expected_by_id: dict[str, dict[str, Any]] = {str(row.get("diagram_id")): row for row in labels}
    _merge_acceptance_subsets(expected_by_id, manifest)
    actual_rows = load_runtime_rows_from_job_output(job_output_path) if job_output_path else load_runtime_rows(actual_path)
    actual_by_id = {
        str(row.get("diagram_id") or row.get("id") or ""): dict(row)
        for row in actual_rows
        if str(row.get("diagram_id") or row.get("id") or "").strip()
    }
    actual_by_fingerprint = {
        str(row.get("diagram_fingerprint")): dict(row)
        for row in actual_rows
        if str(row.get("diagram_fingerprint") or "").strip()
    }

    regressions: list[dict[str, Any]] = []
    improved: list[dict[str, Any]] = []
    manual_review_required: list[dict[str, Any]] = []
    missing_actual: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    by_issue_type: Counter[str] = Counter()
    by_primary_blocker: Counter[str] = Counter()
    by_runtime_classification: Counter[str] = Counter()

    for diagram_id, expected in sorted(expected_by_id.items()):
        by_issue_type[str(expected.get("issue_type") or "unknown")] += 1
        expected_fingerprint = str(expected.get("diagram_fingerprint") or "").strip()
        actual = actual_by_fingerprint.get(expected_fingerprint) if expected_fingerprint else None
        match_method = "diagram_fingerprint" if actual is not None else ""
        if actual is None:
            actual = actual_by_id.get(diagram_id)
            match_method = "diagram_id" if actual is not None else ""
        if actual is None:
            result = _row_result(expected, {}, "missing_actual", "runtime_record_missing")
            missing_actual.append(result)
            by_runtime_classification[str(result.get("runtime_classification") or "unknown")] += 1
            continue
        result = _classify_result(expected, actual)
        result["match_method"] = match_method
        matched.append(result)
        by_primary_blocker[str(result.get("primary_blocker") or "unknown")] += 1
        by_runtime_classification[str(result.get("runtime_classification") or "unknown")] += 1
        status = str(result.get("status") or "")
        if status == "regression":
            regressions.append(result)
        elif status == "improved":
            improved.append(result)
        elif status == "manual_review_required":
            manual_review_required.append(result)

    summary = {
        "benchmark_record_count": len(labels),
        "expected_case_count": len(expected_by_id),
        "matched_actual_count": len(matched),
        "missing_actual_count": len(missing_actual),
        "regression_count": len(regressions),
        "improved_count": len(improved),
        "manual_review_required_count": len(manual_review_required),
        "by_issue_type": dict(sorted(by_issue_type.items())),
        "by_primary_blocker": dict(sorted(by_primary_blocker.items())),
        "by_runtime_classification": dict(sorted(by_runtime_classification.items())),
        "actual_runtime_source_count": len(actual_rows),
        "job_output": str(job_output_path or ""),
        "policy": REVIEW_ONLY_POLICY,
    }
    status = "failed" if regressions else "ok"
    return {
        "schema": DIFF_SCHEMA,
        "status": status,
        "summary": summary,
        "regressions": regressions,
        "improved": improved,
        "manual_review_required": manual_review_required,
        "missing_actual": missing_actual,
        "matched": matched,
    }


def write_crop_qa_diff_reports(report: Mapping[str, Any], out_json: str | Path) -> tuple[Path, Path]:
    json_path = Path(out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = json_path.with_suffix(".md")
    md_path.write_text(crop_qa_diff_markdown(report), encoding="utf-8")
    return json_path, md_path


def crop_qa_diff_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# Chess Crop QA Regression Diff",
        "",
        f"- status: {report.get('status', 'unknown')}",
        f"- benchmark records: {summary.get('benchmark_record_count', 0)}",
        f"- expected cases incl. subsets: {summary.get('expected_case_count', 0)}",
        f"- actual runtime rows: {summary.get('actual_runtime_source_count', 0)}",
        f"- matched actual rows: {summary.get('matched_actual_count', 0)}",
        f"- regressions: {summary.get('regression_count', 0)}",
        f"- improved: {summary.get('improved_count', 0)}",
        f"- manual review required: {summary.get('manual_review_required_count', 0)}",
        f"- missing actual: {summary.get('missing_actual_count', 0)}",
        f"- policy: {summary.get('policy', REVIEW_ONLY_POLICY)}",
        "",
    ]
    lines.extend(["## Runtime Classifications", ""])
    runtime_counts = summary.get("by_runtime_classification") if isinstance(summary.get("by_runtime_classification"), Mapping) else {}
    if runtime_counts:
        lines.extend(["| Classification | Count |", "| --- | ---: |"])
        for key, count in runtime_counts.items():
            lines.append(f"| {_md(str(key))} | {count} |")
        lines.append("")
    else:
        lines.extend(["- none", ""])

    lines.extend(["## Primary Blockers", ""])
    blocker_counts = summary.get("by_primary_blocker") if isinstance(summary.get("by_primary_blocker"), Mapping) else {}
    if blocker_counts:
        lines.extend(["| Blocker | Count |", "| --- | ---: |"])
        for key, count in blocker_counts.items():
            lines.append(f"| {_md(str(key))} | {count} |")
        lines.append("")
    else:
        lines.extend(["- none", ""])

    for section_key, title in (
        ("regressions", "Regressions"),
        ("improved", "Improved"),
        ("manual_review_required", "Manual Review Required"),
        ("missing_actual", "Missing Actual Rows"),
        ("matched", "Matched Actual Rows"),
    ):
        rows = report.get(section_key) if isinstance(report.get(section_key), list) else []
        lines.extend([f"## {title}", ""])
        if not rows:
            lines.extend(["- none", ""])
            continue
        lines.extend(["| Diagram | Issue | Status | Reason | Expected | Actual |", "| --- | --- | --- | --- | --- | --- |"])
        for row in rows:
            lines.append(
                "| {diagram} | {issue} | {status} | {reason} | {expected} | {actual} |".format(
                    diagram=_md(str(row.get("diagram_id") or "")),
                    issue=_md(str(row.get("issue_type") or "")),
                    status=_md(str(row.get("status") or "")),
                    reason=_md(str(row.get("reason") or "")),
                    expected=_md(json.dumps(row.get("expected") or {}, ensure_ascii=False, sort_keys=True)),
                    actual=_md(json.dumps(row.get("actual") or {}, ensure_ascii=False, sort_keys=True)),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _normalize_expected(row: Mapping[str, Any]) -> dict[str, Any]:
    manual_marker = str(row.get("manual_visible_marker") or row.get("visible_marker") or "").strip()
    manual_side = str(row.get("manual_side_to_move") or row.get("final_label") or "").strip().lower()
    diagram_id = str(row.get("diagram_id") or "").strip()
    issue_type = str(row.get("issue_type") or row.get("primary_side_marker_blocker") or "").strip()
    if manual_marker == "filled_triangle" and not manual_side:
        manual_side = "b"
    if manual_marker == "outline_triangle" and not manual_side:
        manual_side = "w"
    marker_status = _marker_crop_status_from_marker(manual_marker)
    if marker_status in {"bad_crop", "unclear", "multiple", "cropped_marker", "none"} and not issue_type:
        issue_type = MARKER_REVIEW_ISSUE_TYPE
    return {
        "diagram_id": diagram_id,
        "diagram_fingerprint": str(row.get("diagram_fingerprint") or ""),
        "page": row.get("page"),
        "diagram_crop_status": str(row.get("diagram_crop_status") or "ok"),
        "marker_crop_status": str(row.get("marker_crop_status") or marker_status),
        "visible_marker": _visible_marker_symbol(manual_marker),
        "final_label": manual_side if manual_side in {"w", "b"} else "",
        "issue_type": issue_type or "manual_label",
        "QA_note": str(row.get("QA_note") or row.get("manual_notes") or ""),
        "recommended_fix": str(row.get("recommended_fix") or ""),
        "policy": REVIEW_ONLY_POLICY,
    }


def _merge_acceptance_subsets(expected_by_id: dict[str, dict[str, Any]], manifest: Mapping[str, Any]) -> None:
    subsets = manifest.get("acceptance_subsets") if isinstance(manifest.get("acceptance_subsets"), Mapping) else {}
    for diagram_id in subsets.get("fragmentary_board_crops", []) or []:
        row = expected_by_id.setdefault(str(diagram_id), _subset_row(str(diagram_id)))
        row["diagram_crop_status"] = "bad_crop"
        row["issue_type"] = FRAGMENTARY_ISSUE_TYPE
    for diagram_id in subsets.get("marker_system_conflicts", []) or []:
        row = expected_by_id.setdefault(str(diagram_id), _subset_row(str(diagram_id)))
        row["final_label"] = "b"
        row["visible_marker"] = "▼"
        row["issue_type"] = CONFLICT_ISSUE_TYPE
    for diagram_id in subsets.get("marker_search_review_only", []) or []:
        row = expected_by_id.setdefault(str(diagram_id), _subset_row(str(diagram_id)))
        if row.get("marker_crop_status") == "ok":
            row["marker_crop_status"] = "bad_crop"
        if row.get("issue_type") != FRAGMENTARY_ISSUE_TYPE:
            row["issue_type"] = MARKER_REVIEW_ISSUE_TYPE


def _subset_row(diagram_id: str) -> dict[str, Any]:
    return {
        "diagram_id": diagram_id,
        "page": None,
        "diagram_crop_status": "unknown",
        "marker_crop_status": "unknown",
        "visible_marker": "",
        "final_label": "",
        "issue_type": "acceptance_subset",
        "QA_note": "Acceptance subset id from issue contract; image/crop not stored in repo.",
        "recommended_fix": "Evaluate against runtime output when source artifacts are available.",
        "policy": REVIEW_ONLY_POLICY,
    }


def _classify_result(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    expected_issue = str(expected.get("issue_type") or "")
    expected_marker_status = str(expected.get("marker_crop_status") or "")
    actual_side = str(actual.get("side_to_move") or actual.get("side_to_move_detected") or actual.get("system_side_to_move") or "").strip().lower()
    actual_marker_status = str(actual.get("side_marker_status") or actual.get("system_side_marker_status") or "").strip()
    actual_board_quality = str(actual.get("board_crop_quality") or actual.get("diagram_crop_status") or "").strip()
    actual_marker_quality = str(actual.get("marker_crop_quality") or actual.get("marker_crop_status") or "").strip()
    trusted = _trusted_marker(actual_marker_status)
    expected_side = str(expected.get("final_label") or "").strip().lower()

    if expected_issue == FRAGMENTARY_ISSUE_TYPE and actual_board_quality in {"pass", "ok"}:
        return _row_result(expected, actual, "regression", "fragmentary_board_crop_passed")
    if expected_issue == CONFLICT_ISSUE_TYPE and actual_side == "w":
        return _row_result(expected, actual, "regression", "visible_black_marker_promoted_to_white")
    if trusted and expected_side in {"w", "b"} and actual_side in {"w", "b"} and actual_side != expected_side:
        return _row_result(expected, actual, "regression", "trusted_marker_wrong_side")
    if expected_issue == MARKER_REVIEW_ISSUE_TYPE and trusted:
        return _row_result(expected, actual, "regression", "review_only_marker_promoted_to_trusted")
    if expected_marker_status in {"none", "unclear", "multiple", "bad_crop", "cropped_marker"} and trusted:
        return _row_result(expected, actual, "regression", "unsafe_marker_status_promoted_to_trusted")

    if expected_issue == FRAGMENTARY_ISSUE_TYPE and actual_board_quality == "fail":
        return _row_result(expected, actual, "improved", "fragmentary_board_crop_blocked")
    if expected_issue == CONFLICT_ISSUE_TYPE and actual_side in {"b", "unknown", ""}:
        status = "improved" if actual_side == "b" and trusted else "manual_review_required"
        return _row_result(expected, actual, status, "black_marker_not_promoted_to_white")
    if trusted and expected_side in {"w", "b"} and actual_side == expected_side:
        return _row_result(expected, actual, "improved", "trusted_classifier_correct_side")
    if expected_issue == MARKER_REVIEW_ISSUE_TYPE and not trusted:
        return _row_result(expected, actual, "manual_review_required", "review_only_marker_not_trusted")
    if actual_marker_quality == "fail" or actual_marker_status in {"marker_conflict", "ambiguous_marker", "marker_missing"}:
        return _row_result(expected, actual, "manual_review_required", "runtime_kept_review_gate")
    return _row_result(expected, actual, "matched", "no_regression_detected")


def _row_result(expected: Mapping[str, Any], actual: Mapping[str, Any], status: str, reason: str) -> dict[str, Any]:
    side_marker_status = actual.get("side_marker_status") or actual.get("system_side_marker_status")
    side_to_move = actual.get("side_to_move") or actual.get("side_to_move_detected") or actual.get("system_side_to_move")
    return {
        "diagram_id": str(expected.get("diagram_id") or actual.get("diagram_id") or ""),
        "diagram_fingerprint": str(
            expected.get("diagram_fingerprint") or actual.get("diagram_fingerprint") or ""
        ),
        "issue_type": str(expected.get("issue_type") or ""),
        "status": status,
        "reason": reason,
        "runtime_classification": _runtime_classification(expected, actual),
        "primary_blocker": _primary_blocker(actual),
        "expected": {
            "diagram_crop_status": expected.get("diagram_crop_status"),
            "marker_crop_status": expected.get("marker_crop_status"),
            "visible_marker": expected.get("visible_marker"),
            "final_label": expected.get("final_label"),
        },
        "actual": {
            "board_crop_quality": actual.get("board_crop_quality") or actual.get("diagram_crop_status"),
            "marker_crop_quality": actual.get("marker_crop_quality") or actual.get("marker_crop_status"),
            "side_marker_status": side_marker_status,
            "side_to_move": side_to_move,
            "primary_blocker": _primary_blocker(actual),
            "runtime_sources": actual.get("runtime_sources") or [],
        },
    }


def _marker_crop_status_from_marker(value: str) -> str:
    if value in {"outline_triangle", "filled_triangle"}:
        return "ok"
    if value in {"none", "unclear", "multiple", "bad_crop", "cropped_marker"}:
        return value
    return "unknown"


def _visible_marker_symbol(value: str) -> str:
    if value == "outline_triangle":
        return "△"
    if value == "filled_triangle":
        return "▼"
    return value


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _merge_non_empty(target: dict[str, Any], row: Mapping[str, Any]) -> None:
    for key, value in row.items():
        if key == "runtime_sources":
            continue
        if value in (None, "", [], {}):
            continue
        existing = target.get(key)
        if existing in (None, "", [], {}):
            target[key] = value


def _primary_blocker(actual: Mapping[str, Any]) -> str:
    return str(
        actual.get("primary_blocker")
        or actual.get("primary_side_marker_blocker")
        or actual.get("full_fen_blocker")
        or actual.get("manual_review_reason")
        or ""
    )


def _runtime_classification(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> str:
    if not actual:
        return "runtime_did_not_find_diagram"
    side = _side(actual)
    expected_side = str(expected.get("final_label") or "").strip().lower()
    marker_status = str(actual.get("side_marker_status") or actual.get("system_side_marker_status") or "").strip().lower()
    marker_crop_exists = _truthy(actual.get("side_marker_crop_exists")) or _truthy(actual.get("has_side_marker_crop")) or bool(
        actual.get("side_marker_crop_path")
    )
    marker_bbox_exists = _truthy(actual.get("marker_bbox_exists")) or bool(actual.get("marker_bbox") or actual.get("side_marker_bbox"))
    marker_search_zone_count = _int(actual.get("marker_search_zone_count"))
    if marker_search_zone_count <= 0 and actual.get("marker_search_zones"):
        marker_search_zone_count = len(actual.get("marker_search_zones") or {})
    trusted = _trusted_marker(marker_status)

    if trusted and expected_side in {"w", "b"} and side == expected_side:
        return "runtime_classifier_trusted_correct_side"
    if trusted and expected_side in {"w", "b"} and side in {"w", "b"} and side != expected_side:
        return "runtime_classifier_trusted_wrong_side"
    if marker_crop_exists and marker_status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker", "inferred_only"}:
        return "runtime_created_marker_crop_but_classifier_missing"
    if marker_bbox_exists and not marker_crop_exists:
        return "runtime_found_marker_bbox_but_crop_missing"
    if marker_search_zone_count > 0 and marker_status in {"", "marker_missing", "side_to_move_marker_missing", "missing", "no_marker", "inferred_only"}:
        return "runtime_found_diagram_but_marker_missing"
    if marker_status in {"marker_conflict", "ambiguous_marker"} or "conflict" in marker_status or "ambiguous" in marker_status:
        return "runtime_kept_manual_review_safe"
    if not trusted and side not in {"w", "b"}:
        return "runtime_kept_manual_review_safe"
    if trusted:
        return "runtime_classifier_trusted_without_manual_label"
    return "runtime_matched_without_regression"


def _side(actual: Mapping[str, Any]) -> str:
    return str(actual.get("side_to_move") or actual.get("side_to_move_detected") or actual.get("system_side_to_move") or "").strip().lower()


def _trusted_marker(status: str) -> bool:
    status_lower = str(status or "").strip().lower()
    return status_lower == "trusted_marker" or status_lower.startswith("trusted_")


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "pass"}
    return bool(value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
