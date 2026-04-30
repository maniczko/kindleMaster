from __future__ import annotations

import argparse
from html.parser import HTMLParser
import io
import json
import posixpath
import time
from pathlib import Path
import sys
from typing import Any
from urllib.parse import unquote, urlsplit
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageFilter, ImageStat

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from converter import ConversionConfig, convert_document_to_epub_with_report
from epub_quality_recovery import run_epub_publishing_quality_recovery
from epub_validation import validate_epub_bytes, validate_epub_path
from size_budget_policy import evaluate_size_budget, get_document_size_budget, inspect_epub_archive, load_size_budget_policy


CHESS_IMAGE_OVERSIZE_EDGE = 640
CHESS_IMAGE_HARD_EDGE = 768
CHESS_ASSET_WARN_BYTES = 512 * 1024
CHESS_ASSET_HARD_BYTES = 1024 * 1024
CHESS_TOTAL_ASSET_WARN_BYTES = 2 * 1024 * 1024
CHESS_TOTAL_ASSET_HARD_BYTES = 4 * 1024 * 1024
SMOKE_MODES = ("micro", "quick", "full")
FAST_CASE_SECONDS = 5.0
SLOW_CASE_SECONDS = 30.0
VERY_SLOW_CASE_SECONDS = 120.0
CHESS_FIGURINE_RANGE = range(0x2654, 0x2660)
PUA_RANGES = (
    range(0xE000, 0xF900),
    range(0xF0000, 0x100000),
    range(0x100000, 0x110000),
)


def run_smoke_tests(
    *,
    manifest_path: str | Path = "reference_inputs/manifest.json",
    mode: str = "quick",
    output_dir: str | Path = "output/smoke",
    reports_dir: str | Path = "reports/smoke",
    case_filters: list[str] | None = None,
) -> dict[str, Any]:
    run_started = time.perf_counter()
    mode = mode.strip().lower()
    resolved_manifest = Path(manifest_path).resolve()
    if not resolved_manifest.exists():
        raise FileNotFoundError(f"Reference input manifest not found: {resolved_manifest}")
    policy = load_size_budget_policy()

    resolved_output_dir = Path(output_dir).resolve()
    resolved_reports_dir = Path(reports_dir).resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_reports_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    filters = [token.lower() for token in (case_filters or []) if token.strip()]
    rows: list[dict[str, Any]] = []

    for case in _select_smoke_cases(manifest.get("cases", []), mode=mode, filters=filters):
        case_started = time.perf_counter()
        path = Path(case["target_path"]).resolve()
        row = {
            "id": case["id"],
            "document_class": case["document_class"],
            "input_type": case["input_type"],
            "release_strict": bool(case.get("release_strict", True)),
            "path": str(path),
        }
        artifact_bytes: bytes | None = None
        if case["input_type"] in {"pdf", "docx"}:
            result = convert_document_to_epub_with_report(
                str(path),
                config=ConversionConfig(profile="auto-premium", language=case.get("language", "pl")),
                original_filename=path.name,
                source_type=case["input_type"],
            )
            epub_path = resolved_output_dir / f"{case['id']}.epub"
            epub_path.write_bytes(result["epub_bytes"])
            validation = validate_epub_bytes(result["epub_bytes"], label=str(epub_path))
            artifact_bytes = result["epub_bytes"]
            row.update(
                {
                    "analysis": _json_safe(result.get("analysis", {})),
                    "quality_report": _json_safe(result.get("quality_report", {})),
                    "validation": validation,
                    "output_epub": str(epub_path),
                }
            )
        else:
            validation = validate_epub_path(path)
            row["validation"] = validation
            artifact_bytes = path.read_bytes()
            if mode == "full":
                release_dir = resolved_output_dir / case["id"]
                audit_result = run_epub_publishing_quality_recovery(
                    path,
                    output_dir=release_dir,
                    reports_dir=resolved_reports_dir / case["id"],
                    expected_language=case.get("language", ""),
                )
                row["release_audit"] = audit_result
        if artifact_bytes is not None:
            row["epub_size_bytes"] = len(artifact_bytes)
            row["chess_quality"] = _inspect_epub_chess_quality(artifact_bytes)
            row["asset_quality_gate"] = _evaluate_chess_asset_quality_gate(row["chess_quality"])
            document_class = str(case.get("document_class", ""))
            row["size_gate"] = evaluate_size_budget(
                budget_key=document_class,
                budget=get_document_size_budget(document_class, policy=policy),
                epub_size_bytes=len(artifact_bytes),
                inspection=inspect_epub_archive(artifact_bytes),
                label="klasy dokumentu",
            )
        row["benchmark"] = _build_case_benchmark(
            row=row,
            elapsed_seconds=time.perf_counter() - case_started,
        )
        rows.append(row)

    summary = _build_smoke_summary(rows)
    summary["benchmark"] = _build_benchmark_summary(
        rows,
        elapsed_seconds=time.perf_counter() - run_started,
    )
    payload = {
        "mode": mode,
        "mode_description": _smoke_mode_description(mode),
        "manifest": str(resolved_manifest),
        "summary": summary,
        "cases": rows,
    }
    (resolved_reports_dir / f"smoke_{mode}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (resolved_reports_dir / f"smoke_{mode}.md").write_text(
        _build_smoke_markdown(payload),
        encoding="utf-8",
    )
    return payload


def _select_smoke_cases(cases: list[dict[str, Any]], *, mode: str, filters: list[str]) -> list[dict[str, Any]]:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in SMOKE_MODES:
        supported = ", ".join(SMOKE_MODES)
        raise ValueError(f"Unsupported smoke mode: {mode!r}. Supported modes: {supported}.")

    filtered_cases = [case for case in cases if not filters or _case_matches(case, filters)]
    if normalized_mode == "full":
        return filtered_cases
    if normalized_mode == "quick":
        return [case for case in filtered_cases if case.get("quick_smoke", False)]

    explicit_micro = [case for case in filtered_cases if case.get("micro_smoke", False)]
    if explicit_micro:
        return explicit_micro
    quick_cases = [case for case in filtered_cases if case.get("quick_smoke", False)]
    return quick_cases[:1] if quick_cases else filtered_cases[:1]


def _smoke_mode_description(mode: str) -> str:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "micro":
        return "Single fastest evidence slice: explicit micro_smoke cases, otherwise the first quick_smoke case."
    if normalized_mode == "quick":
        return "Curated fast smoke cases marked quick_smoke in the reference manifest."
    if normalized_mode == "full":
        return "All manifest cases, including slower release and conversion fixtures."
    return "Unknown smoke mode."


def _case_matches(case: dict[str, Any], filters: list[str]) -> bool:
    haystacks = [
        str(case.get("id", "")).lower(),
        str(case.get("document_class", "")).lower(),
        str(case.get("notes", "")).lower(),
        str(Path(case.get("target_path", "")).name).lower(),
    ]
    return any(token in haystack for token in filters for haystack in haystacks)


def _empty_chess_quality_metrics() -> dict[str, int | float]:
    return {
        "chess_diagram_tag_count": 0,
        "unique_src_count": 0,
        "duplicate_src_count": 0,
        "pua_count": 0,
        "unicode_figurine_count": 0,
        "max_chess_image_edge_px": 0,
        "oversize_count_gt_640": 0,
        "largest_chess_asset_bytes": 0,
        "total_chess_asset_bytes": 0,
        "avg_chess_asset_bytes": 0,
        "chess_quality_score_min": 0,
        "chess_quality_score_avg": 0,
        "contrast_range_min": 0,
        "contrast_range_avg": 0,
        "edge_mean_min": 0,
        "edge_mean_avg": 0,
    }


def _inspect_epub_chess_quality(epub_bytes: bytes) -> dict[str, int | float]:
    metrics = _empty_chess_quality_metrics()
    try:
        with ZipFile(io.BytesIO(epub_bytes), "r") as archive:
            parser = _ChessQualityHTMLParser()
            html_names = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".xhtml", ".html", ".htm"))
            ]
            for name in html_names:
                try:
                    raw = archive.read(name)
                except KeyError:
                    continue
                parser.feed_document(raw.decode("utf-8", errors="replace"), document_path=name)

            srcs = parser.chess_diagram_srcs
            unique_srcs = sorted(set(srcs))
            metrics.update(
                {
                    "chess_diagram_tag_count": parser.chess_diagram_tag_count,
                    "unique_src_count": len(unique_srcs),
                    "duplicate_src_count": max(0, len(srcs) - len(unique_srcs)),
                    "pua_count": parser.pua_count,
                    "unicode_figurine_count": parser.unicode_figurine_count,
                }
            )
            _attach_chess_asset_metrics(archive=archive, srcs=unique_srcs, metrics=metrics)
    except (BadZipFile, OSError):
        return metrics
    return metrics


class _ChessQualityHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.document_path = ""
        self.chess_diagram_tag_count = 0
        self.chess_diagram_srcs: list[str] = []
        self.pua_count = 0
        self.unicode_figurine_count = 0

    def feed_document(self, data: str, *, document_path: str) -> None:
        self.document_path = document_path.replace("\\", "/")
        super().feed(data)
        self.close()
        self.reset()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(attrs)

    def handle_data(self, data: str) -> None:
        for char in data:
            codepoint = ord(char)
            if any(codepoint in pua_range for pua_range in PUA_RANGES):
                self.pua_count += 1
            if codepoint in CHESS_FIGURINE_RANGE:
                self.unicode_figurine_count += 1

    def _handle_tag(self, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        classes = {token for token in attr_map.get("class", "").split() if token}
        if "chess-diagram" not in classes:
            return
        self.chess_diagram_tag_count += 1
        src = attr_map.get("src", "").strip()
        if src:
            resolved_src = _resolve_epub_href(self.document_path, src)
            if resolved_src:
                self.chess_diagram_srcs.append(resolved_src)


def _resolve_epub_href(document_path: str, href: str) -> str:
    parsed = urlsplit(href.strip())
    if parsed.scheme or parsed.netloc:
        return ""
    raw_path = unquote(parsed.path).replace("\\", "/")
    if not raw_path:
        return ""
    if raw_path.startswith("/"):
        return posixpath.normpath(raw_path.lstrip("/"))
    base_dir = posixpath.dirname(document_path)
    return posixpath.normpath(posixpath.join(base_dir, raw_path)).lstrip("./")


def _attach_chess_asset_metrics(*, archive: ZipFile, srcs: list[str], metrics: dict[str, int | float]) -> None:
    info_by_name = {info.filename: info for info in archive.infolist() if not info.is_dir()}
    asset_sizes: list[int] = []
    contrast_ranges: list[float] = []
    edge_means: list[float] = []
    quality_scores: list[float] = []
    for src in srcs:
        if not src:
            continue
        info = info_by_name.get(src)
        if info is None:
            continue
        asset_size = int(info.file_size)
        asset_sizes.append(asset_size)
        metrics["largest_chess_asset_bytes"] = max(int(metrics["largest_chess_asset_bytes"]), asset_size)
        try:
            with Image.open(io.BytesIO(archive.read(src))) as raw_image:
                edge = max(raw_image.size)
                score_metrics = _score_chess_asset_image(raw_image)
        except (OSError, KeyError):
            continue
        contrast_ranges.append(score_metrics["contrast_range"])
        edge_means.append(score_metrics["edge_mean"])
        quality_scores.append(score_metrics["quality_score"])
        metrics["max_chess_image_edge_px"] = max(int(metrics["max_chess_image_edge_px"]), int(edge))
        if edge > CHESS_IMAGE_OVERSIZE_EDGE:
            metrics["oversize_count_gt_640"] = int(metrics["oversize_count_gt_640"]) + 1
    if asset_sizes:
        total_asset_bytes = sum(asset_sizes)
        metrics["total_chess_asset_bytes"] = total_asset_bytes
        metrics["avg_chess_asset_bytes"] = round(total_asset_bytes / len(asset_sizes), 2)
    if quality_scores:
        metrics["chess_quality_score_min"] = round(min(quality_scores), 4)
        metrics["chess_quality_score_avg"] = round(sum(quality_scores) / len(quality_scores), 4)
        metrics["contrast_range_min"] = round(min(contrast_ranges), 4)
        metrics["contrast_range_avg"] = round(sum(contrast_ranges) / len(contrast_ranges), 4)
        metrics["edge_mean_min"] = round(min(edge_means), 4)
        metrics["edge_mean_avg"] = round(sum(edge_means) / len(edge_means), 4)


def _score_chess_asset_image(raw_image: Image.Image) -> dict[str, float]:
    image = raw_image.convert("L")
    low, high = image.getextrema()
    contrast = float(high - low)
    edge_image = image.filter(ImageFilter.FIND_EDGES)
    edge_mean = float(ImageStat.Stat(edge_image).mean[0])
    return {
        "contrast_range": contrast,
        "edge_mean": edge_mean,
        "quality_score": contrast + edge_mean,
    }


def _build_smoke_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    failures = 0
    warnings = 0
    size_failures = 0
    size_warnings = 0
    asset_failures = 0
    asset_warnings = 0
    for row in rows:
        validation_status = _effective_case_validation_status(row)
        size_status = (row.get("size_gate") or {}).get("status", "passed")
        asset_status = (row.get("asset_quality_gate") or {}).get("status", "passed")
        status = _merge_statuses(_merge_statuses(validation_status, size_status), asset_status)
        if status == "failed":
            failures += 1
        elif status == "passed_with_warnings":
            warnings += 1
        if size_status == "failed":
            size_failures += 1
        elif size_status == "passed_with_warnings":
            size_warnings += 1
        if asset_status == "failed":
            asset_failures += 1
        elif asset_status == "passed_with_warnings":
            asset_warnings += 1
    overall = "failed" if failures else ("passed_with_warnings" if warnings else "passed")
    return {
        "cases_run": len(rows),
        "failed_cases": failures,
        "warning_cases": warnings,
        "size_failed_cases": size_failures,
        "size_warning_cases": size_warnings,
        "asset_failed_cases": asset_failures,
        "asset_warning_cases": asset_warnings,
        "overall_status": overall,
    }


def _effective_case_validation_status(row: dict[str, Any]) -> str:
    source_status = _source_validation_status(row, default="failed")
    release_audit = row.get("release_audit") or {}
    if not release_audit:
        return source_status

    release_status = _release_decision_to_validation_status(str(release_audit.get("decision", "") or ""))
    if release_status == "failed":
        if row.get("release_strict") is False and source_status != "failed":
            return "passed_with_warnings"
        return "failed"
    if source_status == "failed":
        return "passed_with_warnings"
    return _merge_statuses(source_status, release_status)


def _source_validation_status(row: dict[str, Any], *, default: str = "unavailable") -> str:
    return ((row.get("validation") or {}).get("summary") or {}).get("status", default)


def _release_decision_to_validation_status(decision: str) -> str:
    normalized = decision.strip().lower()
    if normalized == "pass":
        return "passed"
    if normalized == "pass_with_review":
        return "passed_with_warnings"
    if normalized == "fail":
        return "failed"
    return "failed"


def _build_case_benchmark(*, row: dict[str, Any], elapsed_seconds: float) -> dict[str, Any]:
    rounded_elapsed = round(float(elapsed_seconds), 4)
    validation_status = _effective_case_validation_status(row)
    source_validation_status = _source_validation_status(row)
    release_status = ""
    if row.get("release_audit"):
        release_status = _release_decision_to_validation_status(str(row["release_audit"].get("decision", "") or ""))
    quality_report = row.get("quality_report") or {}
    analysis = row.get("analysis") or {}
    size_gate = row.get("size_gate") or {}
    inspection = size_gate.get("inspection") or {}
    chess_quality = row.get("chess_quality") or _empty_chess_quality_metrics()
    asset_quality_gate = row.get("asset_quality_gate") or _evaluate_chess_asset_quality_gate(chess_quality)
    fallback_mode = _detect_fallback_mode(analysis=analysis, quality_report=quality_report)
    profile_hint = _build_profile_hint(row=row, analysis=analysis, quality_report=quality_report, fallback_mode=fallback_mode)
    missing_metrics: list[str] = []
    if not inspection:
        missing_metrics.append("archive_inspection")
    if row.get("epub_size_bytes") is None:
        missing_metrics.append("epub_size_bytes")
    if fallback_mode == "unknown":
        missing_metrics.append("fallback_mode")
    return {
        "elapsed_seconds": rounded_elapsed,
        "duration_bucket": _duration_bucket(rounded_elapsed),
        "duration_hint": _duration_hint(row=row, elapsed_seconds=rounded_elapsed),
        "profile_hint": profile_hint,
        "output_size_bytes": int(row.get("epub_size_bytes") or 0),
        "image_count": int(inspection.get("image_count", 0) or 0),
        "fallback_mode": fallback_mode,
        "validation_status": validation_status,
        "source_validation_status": source_validation_status,
        "release_audit_status": release_status,
        "chess_quality": chess_quality,
        "asset_quality_gate": asset_quality_gate,
        "metrics_missing": missing_metrics,
    }


def _duration_bucket(elapsed_seconds: float) -> str:
    if elapsed_seconds >= VERY_SLOW_CASE_SECONDS:
        return "very_slow"
    if elapsed_seconds >= SLOW_CASE_SECONDS:
        return "slow"
    if elapsed_seconds >= FAST_CASE_SECONDS:
        return "moderate"
    return "fast"


def _duration_hint(*, row: dict[str, Any], elapsed_seconds: float) -> str:
    bucket = _duration_bucket(elapsed_seconds)
    case_id = str(row.get("id", "unknown") or "unknown")
    if bucket == "very_slow":
        return f"very_slow: reserve for full/corpus gates; isolate with --case {case_id} when diagnosing"
    if bucket == "slow":
        return f"slow: isolate with --case {case_id} or use --mode micro while iterating"
    if bucket == "moderate":
        return "moderate: acceptable for targeted smoke, watch trend across repeated runs"
    return "fast: suitable for micro and quick iteration"


def _build_profile_hint(
    *,
    row: dict[str, Any],
    analysis: dict[str, Any],
    quality_report: dict[str, Any],
    fallback_mode: str,
) -> str:
    tokens: list[str] = []
    input_type = str(row.get("input_type", "") or "").strip()
    document_class = str(row.get("document_class", "") or "").strip()
    profile = _first_non_empty_value(
        analysis,
        quality_report,
        keys=("profile", "publication_profile", "publication_type", "route", "conversion_route", "pipeline_route"),
    )
    if input_type:
        tokens.append(f"input:{input_type}")
    if document_class:
        tokens.append(f"class:{document_class}")
    if profile:
        tokens.append(f"profile:{profile}")
    if fallback_mode and fallback_mode != "unknown":
        tokens.append(f"fallback:{fallback_mode}")
    return ", ".join(tokens) if tokens else "unavailable"


def _first_non_empty_value(*sources: dict[str, Any], keys: tuple[str, ...]) -> str:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            rendered = str(value).strip()
            if rendered:
                return rendered
    return ""


def _detect_fallback_mode(*, analysis: dict[str, Any], quality_report: dict[str, Any]) -> str:
    profile = str((analysis or {}).get("profile", "") or "").strip()
    validation_tool = str((quality_report or {}).get("validation_tool", "") or "").strip()
    if profile == "legacy-fallback" or validation_tool == "legacy":
        return "legacy-fallback"
    if profile:
        return "premium"
    return "unknown"


def _evaluate_chess_asset_quality_gate(chess_quality: dict[str, Any]) -> dict[str, Any]:
    diagram_count = int(chess_quality.get("chess_diagram_tag_count") or 0)
    if diagram_count <= 0:
        return {
            "status": "not_applicable",
            "message": "No chess diagram assets detected.",
            "budget": "chess_crisp_low_size",
        }

    max_edge = int(chess_quality.get("max_chess_image_edge_px") or 0)
    largest_asset = int(chess_quality.get("largest_chess_asset_bytes") or 0)
    total_assets = int(chess_quality.get("total_chess_asset_bytes") or 0)
    oversize_count = int(chess_quality.get("oversize_count_gt_640") or 0)
    reasons: list[str] = []
    status = "passed"

    if max_edge > CHESS_IMAGE_HARD_EDGE:
        status = "failed"
        reasons.append(f"max edge {max_edge}px exceeds hard budget {CHESS_IMAGE_HARD_EDGE}px")
    elif max_edge > CHESS_IMAGE_OVERSIZE_EDGE or oversize_count > 0:
        status = "passed_with_warnings"
        reasons.append(f"{oversize_count or 1} diagram asset exceeds {CHESS_IMAGE_OVERSIZE_EDGE}px warning edge")

    if largest_asset > CHESS_ASSET_HARD_BYTES:
        status = "failed"
        reasons.append(f"largest chess asset {largest_asset} B exceeds hard budget {CHESS_ASSET_HARD_BYTES} B")
    elif largest_asset > CHESS_ASSET_WARN_BYTES and status != "failed":
        status = "passed_with_warnings"
        reasons.append(f"largest chess asset {largest_asset} B exceeds warning budget {CHESS_ASSET_WARN_BYTES} B")

    if total_assets > CHESS_TOTAL_ASSET_HARD_BYTES:
        status = "failed"
        reasons.append(f"total chess assets {total_assets} B exceed hard budget {CHESS_TOTAL_ASSET_HARD_BYTES} B")
    elif total_assets > CHESS_TOTAL_ASSET_WARN_BYTES and status != "failed":
        status = "passed_with_warnings"
        reasons.append(f"total chess assets {total_assets} B exceed warning budget {CHESS_TOTAL_ASSET_WARN_BYTES} B")

    return {
        "status": status,
        "budget": "chess_crisp_low_size",
        "message": "; ".join(reasons) if reasons else "Chess diagram assets fit crisp low-size budgets.",
        "warn_max_edge_px": CHESS_IMAGE_OVERSIZE_EDGE,
        "hard_max_edge_px": CHESS_IMAGE_HARD_EDGE,
        "warn_largest_asset_bytes": CHESS_ASSET_WARN_BYTES,
        "hard_largest_asset_bytes": CHESS_ASSET_HARD_BYTES,
        "warn_total_asset_bytes": CHESS_TOTAL_ASSET_WARN_BYTES,
        "hard_total_asset_bytes": CHESS_TOTAL_ASSET_HARD_BYTES,
        "max_edge_px": max_edge,
        "oversize_count_gt_640": oversize_count,
        "largest_asset_bytes": largest_asset,
        "total_asset_bytes": total_assets,
    }


def _build_benchmark_summary(rows: list[dict[str, Any]], *, elapsed_seconds: float) -> dict[str, Any]:
    classes = {str(row.get("document_class", "") or "") for row in rows if row.get("document_class")}
    slowest = sorted(
        (
            {
                "id": row.get("id", "unknown"),
                "document_class": row.get("document_class", ""),
                "elapsed_seconds": (row.get("benchmark") or {}).get("elapsed_seconds", 0),
                "duration_bucket": (row.get("benchmark") or {}).get("duration_bucket", "unavailable"),
                "duration_hint": (row.get("benchmark") or {}).get("duration_hint", ""),
                "profile_hint": (row.get("benchmark") or {}).get("profile_hint", "unavailable"),
                "validation_status": (row.get("benchmark") or {}).get("validation_status", "unavailable"),
                "fallback_mode": (row.get("benchmark") or {}).get("fallback_mode", "unknown"),
            }
            for row in rows
        ),
        key=lambda item: float(item.get("elapsed_seconds") or 0),
        reverse=True,
    )[:5]
    missing_metric_cases = [
        row.get("id", "unknown")
        for row in rows
        if (row.get("benchmark") or {}).get("metrics_missing")
    ]
    return {
        "total_elapsed_seconds": round(float(elapsed_seconds), 4),
        "case_count": len(rows),
        "class_count": len(classes),
        "classes": sorted(classes),
        "fast_case_threshold_seconds": FAST_CASE_SECONDS,
        "slow_case_threshold_seconds": SLOW_CASE_SECONDS,
        "very_slow_case_threshold_seconds": VERY_SLOW_CASE_SECONDS,
        "slowest_cases": slowest,
        "missing_metric_cases": missing_metric_cases,
    }


def _merge_statuses(validation_status: str, size_status: str) -> str:
    priority = {"not_applicable": 0, "passed": 0, "passed_with_warnings": 1, "failed": 2}
    return validation_status if priority.get(validation_status, 2) >= priority.get(size_status, 2) else size_status


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _build_smoke_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# KindleMaster Smoke Report",
        "",
        f"- Mode: `{payload.get('mode', 'unknown')}`",
        f"- Mode description: `{payload.get('mode_description', '')}`",
        f"- Cases run: `{payload.get('summary', {}).get('cases_run', 0)}`",
        f"- Overall status: `{payload.get('summary', {}).get('overall_status', 'unknown')}`",
        "",
    ]
    benchmark = (payload.get("summary") or {}).get("benchmark") or {}
    if benchmark:
        lines.extend(
            [
                "## Benchmark",
                "",
                f"- Total elapsed: `{benchmark.get('total_elapsed_seconds', 0)}` seconds",
                f"- Classes covered: `{benchmark.get('class_count', 0)}`",
                f"- Missing metric cases: `{', '.join(benchmark.get('missing_metric_cases', [])) or 'none'}`",
                "",
            ]
        )
        slowest_cases = benchmark.get("slowest_cases") or []
        if slowest_cases:
            lines.append("- Slowest cases:")
            for item in slowest_cases:
                lines.append(
                    f"  - `{item.get('id', 'unknown')}`: `{item.get('elapsed_seconds', 0)}` seconds, "
                    f"profile `{item.get('profile_hint', 'unavailable')}`, "
                    f"hint `{item.get('duration_hint', '')}`"
                )
            lines.append("")
    for row in payload.get("cases", []):
        validation = row.get("validation", {})
        benchmark = row.get("benchmark") or {}
        lines.extend(
            [
                f"## {row.get('id', 'unknown')}",
                "",
                f"- Class: `{row.get('document_class', '')}`",
                f"- Input type: `{row.get('input_type', '')}`",
                f"- Validation: `{(validation.get('summary') or {}).get('status', 'unknown')}`",
                f"- Effective status: `{_effective_case_validation_status(row)}`",
                f"- Benchmark: `{benchmark.get('elapsed_seconds', 0)}` seconds, "
                f"profile `{benchmark.get('profile_hint', 'unavailable')}`, "
                f"fallback `{benchmark.get('fallback_mode', 'unknown')}`, "
                f"hint `{benchmark.get('duration_hint', '')}`",
            ]
        )
        chess_quality = row.get("chess_quality") or (benchmark.get("chess_quality") or {})
        if chess_quality:
            lines.append(
                "- Chess quality: "
                f"diagrams `{chess_quality.get('chess_diagram_tag_count', 0)}`, "
                f"unique src `{chess_quality.get('unique_src_count', 0)}`, "
                f"duplicate src `{chess_quality.get('duplicate_src_count', 0)}`, "
                f"PUA `{chess_quality.get('pua_count', 0)}`, "
                f"figurines `{chess_quality.get('unicode_figurine_count', 0)}`, "
                f"max edge `{chess_quality.get('max_chess_image_edge_px', 0)}` px, "
                f"oversize >640 `{chess_quality.get('oversize_count_gt_640', 0)}`, "
                f"largest asset `{chess_quality.get('largest_chess_asset_bytes', 0)}` B, "
                f"total asset `{chess_quality.get('total_chess_asset_bytes', 0)}` B, "
                f"avg asset `{chess_quality.get('avg_chess_asset_bytes', 0)}` B, "
                f"quality min/avg `{chess_quality.get('chess_quality_score_min', 0)}`/"
                f"`{chess_quality.get('chess_quality_score_avg', 0)}`, "
                f"contrast min/avg `{chess_quality.get('contrast_range_min', 0)}`/"
                f"`{chess_quality.get('contrast_range_avg', 0)}`, "
                f"edge mean min/avg `{chess_quality.get('edge_mean_min', 0)}`/"
                f"`{chess_quality.get('edge_mean_avg', 0)}`"
            )
        asset_gate = row.get("asset_quality_gate") or (benchmark.get("asset_quality_gate") or {})
        if asset_gate:
            lines.append(
                f"- Asset quality gate: `{asset_gate.get('status', 'unknown')}` "
                f"budget `{asset_gate.get('budget', 'unknown')}` - {asset_gate.get('message', '')}"
            )
        if row.get("size_gate"):
            size_gate = row["size_gate"]
            lines.append(f"- Size gate: `{size_gate.get('status', 'unknown')}`")
            lines.append(f"- EPUB size: `{size_gate.get('epub_size_bytes', 0)}` B")
            if size_gate.get("warn_bytes") is not None:
                lines.append(
                    f"- Size budget: warn `{size_gate['warn_bytes']}` B / hard `{size_gate['hard_bytes']}` B"
                )
            largest_assets = ((size_gate.get("inspection") or {}).get("largest_assets") or [])[:3]
            if largest_assets:
                lines.append("- Largest assets:")
                for asset in largest_assets:
                    lines.append(f"  - `{asset['name']}` -> `{asset['size_bytes']}` B")
        if row.get("release_audit"):
            lines.append(f"- Release audit: `{row['release_audit'].get('decision', 'unknown')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run KindleMaster smoke tests on curated reference inputs.")
    parser.add_argument("--manifest", default="reference_inputs/manifest.json")
    parser.add_argument("--mode", choices=SMOKE_MODES, default="quick")
    parser.add_argument("--output-dir", default="output/smoke")
    parser.add_argument("--reports-dir", default="reports/smoke")
    parser.add_argument("--case", action="append", default=[], help="Optional filter by case id, class, or filename.")
    args = parser.parse_args()

    payload = run_smoke_tests(
        manifest_path=args.manifest,
        mode=args.mode,
        output_dir=args.output_dir,
        reports_dir=args.reports_dir,
        case_filters=args.case,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("summary", {}).get("overall_status") != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
