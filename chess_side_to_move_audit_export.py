from __future__ import annotations

import json
import os
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


AUDIT_EXPORT_SCHEMA = "kindlemaster.chess.side_to_move_audit_export.v1"

SAFE_AUDIT_PATHS = (
    PurePosixPath("reports/chess_fen/why_side_to_move_not_trusted.json"),
    PurePosixPath("reports/chess_fen/why_side_to_move_not_trusted.md"),
    PurePosixPath("reports/chess_fen/why_side_to_move_not_trusted.html"),
    PurePosixPath("reports/chess_fen/side_to_move_coverage_dashboard.json"),
    PurePosixPath("reports/chess_fen/side_to_move_coverage_dashboard.md"),
    PurePosixPath("reports/chess_fen/side_to_move_coverage_dashboard.html"),
    PurePosixPath("reports/chess_fen/crop_qa_regression_diff.json"),
    PurePosixPath("reports/chess_fen/crop_qa_regression_diff.md"),
    PurePosixPath("reports/chess_fen/two_crop_quality_metrics.json"),
    PurePosixPath("reports/chess_fen/two_crop_quality_metrics.md"),
    PurePosixPath("reports/chess_fen/side_marker_assignment.json"),
    PurePosixPath("reports/chess_fen/side_marker_blocker_attribution.json"),
    PurePosixPath("chess_diagrams.json"),
    PurePosixPath("positions.json"),
    PurePosixPath("reports/chess_reader/semantic_book.json"),
)

KEY_METRICS = (
    "diagram_count",
    "side_unknown_count",
    "marker_search_zone_coverage_rate",
    "marker_bbox_detection_rate",
    "marker_crop_generation_rate",
    "marker_crop_quality_pass_rate",
    "trusted_marker_rate",
    "side_to_move_coverage_rate",
    "trusted_side_to_move_rate",
    "full_fen_safe_acceptance_rate",
)

_WHY_REPORT = PurePosixPath("reports/chess_fen/why_side_to_move_not_trusted.json")
_COVERAGE_REPORT = PurePosixPath(
    "reports/chess_fen/side_to_move_coverage_dashboard.json"
)
_CROP_QA_REPORT = PurePosixPath("reports/chess_fen/crop_qa_regression_diff.json")
_TWO_CROP_REPORT = PurePosixPath("reports/chess_fen/two_crop_quality_metrics.json")
_BLOCKER_REPORT = PurePosixPath(
    "reports/chess_fen/side_marker_blocker_attribution.json"
)


def default_audit_search_roots(
    *,
    repo_root: str | Path = ".",
    environ: Mapping[str, str] | None = None,
    temp_root: str | Path | None = None,
) -> list[Path]:
    env = os.environ if environ is None else environ
    repo = Path(repo_root)
    candidates = [
        repo / "output",
        repo / "output" / "artifacts",
        Path(temp_root)
        if temp_root is not None
        else Path(tempfile.gettempdir()) / "kindlemaster",
    ]
    configured_artifact_root = str(
        env.get("KINDLEMASTER_ARTIFACT_ROOT", "") or ""
    ).strip()
    if configured_artifact_root:
        candidates.append(Path(configured_artifact_root))
    return _dedupe_paths(candidates)


def discover_latest_audit_job(search_roots: Iterable[str | Path]) -> dict[str, Any]:
    checked_roots = _dedupe_paths(Path(root) for root in search_roots)
    candidates: dict[Path, int] = {}
    allowed_by_name = _allowed_paths_by_name()

    for search_root in checked_roots:
        if not search_root.is_dir():
            continue
        for directory, child_directories, filenames in os.walk(
            search_root, followlinks=False
        ):
            child_directories[:] = [
                name
                for name in child_directories
                if name not in {".git", ".venv", "node_modules", "__pycache__"}
            ]
            directory_path = Path(directory)
            for filename in filenames:
                allowed_paths = allowed_by_name.get(filename)
                if not allowed_paths:
                    continue
                file_path = directory_path / filename
                for relative_path in allowed_paths:
                    job_root = _job_root_for_match(file_path, relative_path)
                    if job_root is None or not _is_safe_regular_file(
                        job_root, relative_path
                    ):
                        continue
                    try:
                        modified = file_path.stat().st_mtime_ns
                    except OSError:
                        continue
                    candidates[job_root] = max(modified, candidates.get(job_root, 0))

    selected = (
        max(candidates, key=lambda path: (candidates[path], str(path)))
        if candidates
        else None
    )
    return {
        "selected_job_output": str(selected) if selected is not None else "",
        "checked_search_roots": [str(path) for path in checked_roots],
        "candidate_job_outputs": [
            str(path)
            for path, _ in sorted(
                candidates.items(),
                key=lambda item: (item[1], str(item[0])),
                reverse=True,
            )
        ],
    }


def export_side_to_move_audit(
    *,
    job_output: str | Path | None = None,
    latest: bool = False,
    out_path: str | Path | None = None,
    include_html: bool = True,
    json_summary_path: str | Path | None = None,
    search_roots: Iterable[str | Path] | None = None,
    repo_root: str | Path = ".",
    environ: Mapping[str, str] | None = None,
    temp_root: str | Path | None = None,
) -> dict[str, Any]:
    if bool(job_output) == bool(latest):
        raise ValueError("Provide exactly one of job_output or latest=True.")

    discovery = {
        "selected_job_output": "",
        "checked_search_roots": [],
        "candidate_job_outputs": [],
    }
    if latest:
        roots = (
            list(search_roots)
            if search_roots is not None
            else default_audit_search_roots(
                repo_root=repo_root,
                environ=environ,
                temp_root=temp_root,
            )
        )
        discovery = discover_latest_audit_job(roots)
        selected_text = discovery["selected_job_output"]
        selected = Path(selected_text) if selected_text else None
    else:
        selected = Path(job_output) if job_output is not None else None
        discovery["selected_job_output"] = str(selected) if selected is not None else ""
        discovery["checked_search_roots"] = (
            [str(selected)] if selected is not None else []
        )

    base_payload: dict[str, Any] = {
        "schema": AUDIT_EXPORT_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **discovery,
        "include_html": bool(include_html),
        "zip_path": "",
        "json_summary_path": str(json_summary_path) if json_summary_path else "",
        "included_files": [],
        "skipped_unsafe_files": [],
        "parse_warnings": [],
        "found": {
            "why_side_to_move_not_trusted": False,
            "coverage_dashboard": False,
            "crop_qa_diff": False,
        },
        "metrics": {key: None for key in KEY_METRICS},
        "top_blockers": [],
    }

    if selected is None:
        payload = {
            **base_payload,
            "status": "job_not_found",
            "message": "No conversion job containing safe side-to-move/chess diagnostics was found.",
        }
        _write_optional_summary(payload, json_summary_path)
        return payload

    try:
        selected = selected.resolve()
    except OSError:
        selected = selected.absolute()
    base_payload["selected_job_output"] = str(selected)
    if not selected.is_dir():
        payload = {
            **base_payload,
            "status": "job_output_missing",
            "message": f"The selected job output directory does not exist: {selected}",
        }
        _write_optional_summary(payload, json_summary_path)
        return payload

    safe_files, skipped = collect_safe_audit_files(selected, include_html=include_html)
    base_payload["included_files"] = [relative.as_posix() for relative, _ in safe_files]
    base_payload["skipped_unsafe_files"] = skipped
    included_names = set(base_payload["included_files"])
    base_payload["found"] = {
        "why_side_to_move_not_trusted": _WHY_REPORT.as_posix() in included_names,
        "coverage_dashboard": _COVERAGE_REPORT.as_posix() in included_names,
        "crop_qa_diff": _CROP_QA_REPORT.as_posix() in included_names,
    }

    reports, parse_warnings = _load_json_reports(safe_files)
    base_payload["parse_warnings"] = parse_warnings
    base_payload["metrics"] = extract_key_metrics(reports)
    base_payload["top_blockers"] = extract_top_blockers(reports)

    if not safe_files:
        payload = {
            **base_payload,
            "status": "no_diagnostics",
            "message": "The selected job output contains none of the allowlisted side-to-move/chess diagnostics.",
        }
        _write_optional_summary(payload, json_summary_path)
        return payload

    archive_path = (
        Path(out_path)
        if out_path is not None
        else selected / "side_to_move_audit_bundle.zip"
    )
    if not archive_path.is_absolute():
        archive_path = Path.cwd() / archive_path
    archive_path = archive_path.resolve()
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for relative_path, source_path in safe_files:
            archive.write(source_path, arcname=relative_path.as_posix())

    payload = {
        **base_payload,
        "status": "created",
        "message": f"Created a safe audit bundle with {len(safe_files)} diagnostic file(s).",
        "zip_path": str(archive_path),
    }
    _write_optional_summary(payload, json_summary_path)
    return payload


def collect_safe_audit_files(
    job_output: str | Path,
    *,
    include_html: bool = True,
) -> tuple[list[tuple[PurePosixPath, Path]], list[str]]:
    root = Path(job_output).resolve()
    files: list[tuple[PurePosixPath, Path]] = []
    skipped: list[str] = []
    for relative_path in SAFE_AUDIT_PATHS:
        if relative_path.suffix.lower() == ".html" and not include_html:
            continue
        candidate = root.joinpath(*relative_path.parts)
        if not candidate.exists():
            continue
        if not _is_safe_regular_file(root, relative_path):
            skipped.append(relative_path.as_posix())
            continue
        files.append((relative_path, candidate))
    return files, skipped


def extract_key_metrics(
    reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, int | float | None]:
    why = _report_summary(reports, _WHY_REPORT)
    coverage = _report_summary(reports, _COVERAGE_REPORT)
    two_crop = _report_summary(reports, _TWO_CROP_REPORT)
    source_candidates: dict[str, tuple[tuple[Mapping[str, Any], str], ...]] = {
        "diagram_count": (
            (why, "diagram_count"),
            (coverage, "diagram_count"),
            (two_crop, "diagram_count"),
        ),
        "side_unknown_count": (
            (why, "side_unknown_count"),
            (coverage, "unknown_count"),
            (two_crop, "side_unknown_count"),
        ),
        "marker_search_zone_coverage_rate": (
            (why, "marker_search_zone_coverage_rate"),
            (two_crop, "marker_search_zone_coverage_rate"),
        ),
        "marker_bbox_detection_rate": (
            (why, "marker_bbox_detection_rate"),
            (two_crop, "marker_bbox_detection_rate"),
        ),
        "marker_crop_generation_rate": (
            (why, "marker_crop_generation_rate"),
            (two_crop, "marker_crop_generation_rate"),
        ),
        "marker_crop_quality_pass_rate": (
            (why, "marker_crop_quality_pass_rate"),
            (two_crop, "marker_crop_quality_pass_rate"),
        ),
        "trusted_marker_rate": (
            (coverage, "trusted_marker_rate"),
            (why, "trusted_marker_rate"),
            (two_crop, "trusted_marker_rate"),
        ),
        "side_to_move_coverage_rate": (
            (coverage, "side_to_move_coverage_rate"),
            (why, "side_to_move_coverage_rate"),
        ),
        "trusted_side_to_move_rate": ((coverage, "trusted_side_to_move_rate"),),
        "full_fen_safe_acceptance_rate": ((coverage, "full_fen_safe_acceptance_rate"),),
    }
    return {
        metric: _first_number(candidates)
        for metric, candidates in source_candidates.items()
    }


def extract_top_blockers(
    reports: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    why = _report_summary(reports, _WHY_REPORT)
    blocker_report = _report_summary(reports, _BLOCKER_REPORT)
    counts = why.get("by_primary_blocker")
    if not isinstance(counts, Mapping) or not counts:
        counts = blocker_report.get("by_primary_side_marker_blocker")
    if not isinstance(counts, Mapping):
        return []
    normalized = []
    for code, count in counts.items():
        if str(code) in {"no_blocker_trusted", "none"}:
            continue
        if isinstance(count, bool) or not isinstance(count, (int, float)):
            continue
        normalized.append(
            {
                "code": str(code),
                "count": int(count) if float(count).is_integer() else float(count),
            }
        )
    normalized.sort(key=lambda item: (-float(item["count"]), item["code"]))
    return normalized[: max(0, limit)]


def format_audit_export_console(payload: Mapping[str, Any]) -> str:
    found = payload.get("found") if isinstance(payload.get("found"), Mapping) else {}
    metrics = (
        payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    )
    blockers = (
        payload.get("top_blockers")
        if isinstance(payload.get("top_blockers"), list)
        else []
    )
    lines = [
        "STATUS:",
        f"- result: {payload.get('status') or 'unknown'}",
        f"- selected job output: {payload.get('selected_job_output') or 'not found'}",
        f"- found why_side_to_move_not_trusted: {_yes_no(found.get('why_side_to_move_not_trusted'))}",
        f"- found coverage dashboard: {_yes_no(found.get('coverage_dashboard'))}",
        f"- found crop QA diff: {_yes_no(found.get('crop_qa_diff'))}",
        f"- zip path: {payload.get('zip_path') or 'not created'}",
        "",
        "KEY METRICS:",
    ]
    for metric in KEY_METRICS:
        value = metrics.get(metric)
        lines.append(f"- {metric}: {value if value is not None else 'n/a'}")
    lines.extend(["", "TOP BLOCKERS:"])
    if blockers:
        for index, blocker in enumerate(blockers[:5], start=1):
            lines.append(f"{index}. {blocker.get('code')}: {blocker.get('count')}")
    else:
        lines.append("1. none reported")
    message = str(payload.get("message") or "").strip()
    if message:
        lines.extend(["", f"MESSAGE: {message}"])
    checked = payload.get("checked_search_roots")
    if payload.get("status") != "created" and isinstance(checked, list):
        lines.extend(["", "CHECKED DIRECTORIES:"])
        lines.extend(f"- {path}" for path in checked)
    return "\n".join(lines).rstrip() + "\n"


def _load_json_reports(
    safe_files: Iterable[tuple[PurePosixPath, Path]],
) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    reports: dict[str, Mapping[str, Any]] = {}
    warnings: list[str] = []
    for relative_path, source_path in safe_files:
        if relative_path.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            warnings.append(f"{relative_path.as_posix()}: {type(error).__name__}")
            continue
        if isinstance(payload, Mapping):
            reports[relative_path.as_posix()] = payload
        else:
            warnings.append(
                f"{relative_path.as_posix()}: root JSON value is not an object"
            )
    return reports, warnings


def _report_summary(
    reports: Mapping[str, Mapping[str, Any]],
    relative_path: PurePosixPath,
) -> Mapping[str, Any]:
    report = reports.get(relative_path.as_posix())
    if not isinstance(report, Mapping):
        return {}
    summary = report.get("summary")
    return summary if isinstance(summary, Mapping) else {}


def _first_number(
    candidates: Iterable[tuple[Mapping[str, Any], str]],
) -> int | float | None:
    for mapping, key in candidates:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return value
    return None


def _write_optional_summary(
    payload: Mapping[str, Any], path: str | Path | None
) -> None:
    if path is None:
        return
    output = Path(path)
    if not output.is_absolute():
        output = Path.cwd() / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _allowed_paths_by_name() -> dict[str, list[PurePosixPath]]:
    result: dict[str, list[PurePosixPath]] = {}
    for relative_path in SAFE_AUDIT_PATHS:
        result.setdefault(relative_path.name, []).append(relative_path)
    return result


def _job_root_for_match(file_path: Path, relative_path: PurePosixPath) -> Path | None:
    if len(file_path.parts) < len(relative_path.parts):
        return None
    actual_suffix = tuple(
        part.casefold() for part in file_path.parts[-len(relative_path.parts) :]
    )
    expected_suffix = tuple(part.casefold() for part in relative_path.parts)
    if actual_suffix != expected_suffix:
        return None
    job_root = file_path
    for _ in relative_path.parts:
        job_root = job_root.parent
    try:
        return job_root.resolve()
    except OSError:
        return job_root.absolute()


def _is_safe_regular_file(root: Path, relative_path: PurePosixPath) -> bool:
    candidate = root.joinpath(*relative_path.parts)
    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    try:
        if candidate.is_symlink() or not candidate.is_file():
            return False
    except OSError:
        return False
    return relative_path.suffix.lower() in {".json", ".md", ".html"}


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            normalized = path.resolve()
        except OSError:
            normalized = path.absolute()
        key = os.path.normcase(str(normalized))
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"
