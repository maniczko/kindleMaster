from __future__ import annotations

import argparse
import io
import json
import re
import tempfile
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from lxml import etree

from converter import ConversionConfig, convert_pdf_to_epub_with_report
from epub_heading_repair import repair_epub_headings_and_toc
from publication_analysis import analyze_publication
from size_budget_policy import evaluate_size_budget, get_document_size_budget, inspect_epub_archive, load_size_budget_policy


@dataclass(frozen=True)
class CorpusCase:
    path: Path
    document_class: str
    notes: str = ""
    analysis_only: bool = False
    release_strict: bool = True


@dataclass(frozen=True)
class CorpusBatchSelection:
    total_cases: int
    matching_cases: int
    selected_cases: int
    skipped_cases: int
    coverage_status: str
    filters: tuple[str, ...]
    shard_count: int | None
    shard_index: int | None
    limit: int | None
    selected_case_labels: tuple[str, ...]


@dataclass(frozen=True)
class CorpusSourceSummary:
    source_mode: str
    manifest_path: str | None
    manifest_case_count: int
    eligible_manifest_cases: int
    skipped_manifest_cases: int
    skipped_case_labels: tuple[str, ...]
    fallback_used: bool
    fallback_reason: str


DEFAULT_MANIFEST_PATH = Path("reference_inputs/manifest.json")


CORPUS: list[CorpusCase] = [
    CorpusCase(
        Path("example/02695ab2e05aab728b4b995caa682f947e8be2c3291ff490579797c5a3cc5e26.pdf"),
        document_class="magazine-layout",
        notes="Layout-heavy editorial PDF with tables and image-led pages.",
    ),
    CorpusCase(
        Path("reference_inputs/pdf/ocr_stress_scan.pdf"),
        document_class="ocr-stress-scan",
        notes="Deterministic OCR-stressed scanned PDF generated from the reference-input bootstrap.",
    ),
    CorpusCase(
        Path("reference_inputs/pdf/document_like_report.pdf"),
        document_class="document-like-report",
        notes="Deterministic multi-page report-style PDF generated from the reference-input bootstrap.",
    ),
    CorpusCase(
        Path("example/BABOK_Guide_v3_Member.pdf"),
        document_class="dense-business-guide",
        notes="Long business-analysis handbook with deep structure and heavy semantics.",
    ),
    CorpusCase(
        Path("example/The Woodpecker Method ( PDFDrive ).pdf"),
        document_class="training-book-diagram",
        notes="Exercise/solution training book with diagrams and bidirectional navigation pressure.",
    ),
    CorpusCase(
        Path("example/tactits_sample_80pages.pdf"),
        document_class="diagram-heavy-book",
        notes="Diagram-heavy tactics sample with dense image-first content.",
    ),
    CorpusCase(
        Path("example/scan_probe.pdf"),
        document_class="ocr-probe-scan",
        notes="Small scan/OCR probe used to catch text-layer and cleanup regressions.",
    ),
]

ANALYSIS_ONLY: list[CorpusCase] = [
    CorpusCase(
        Path("example/tactits.pdf"),
        document_class="large-diagram-corpus",
        notes="Large analysis-only stress case for profile detection.",
        analysis_only=True,
    ),
]

PLACEHOLDER_TITLE_RE = re.compile(r"^(?:emvc|executive summary|unknown|untitled|legacy)$", re.IGNORECASE)
PLACEHOLDER_AUTHOR_RE = re.compile(r"^(?:unknown|python-docx|legacy)$", re.IGNORECASE)
VISIBLE_JUNK_PATTERNS = {
    "unresolved_url_label": re.compile(r"Unresolved URL:", re.IGNORECASE),
    "manual_review_label": re.compile(r"Link requires manual review\.", re.IGNORECASE),
    "half_url_https_the": re.compile(r"https?://the(?:\b|[./])", re.IGNORECASE),
    "broken_percent_tail": re.compile(r"https?://[^\s\"']+%2(?:\b|[^\da-fA-F])"),
}
BROKEN_HREF_PATTERNS = {
    "glued_protocol": re.compile(r"https?://https?://", re.IGNORECASE),
    "half_url_https_the": re.compile(r"https?://the(?:\b|[./])", re.IGNORECASE),
    "orphan_percent_tail": re.compile(r"%2(?:\b|[^\da-fA-F])"),
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _resolve_opf_name(names: list[str]) -> str | None:
    for candidate in names:
        if candidate.endswith(".opf"):
            return candidate
    return None


def _extract_nav_depth(ol_tag) -> int:
    if ol_tag is None:
        return 0
    depths = [1]
    for li_tag in ol_tag.find_all("li"):
        nested = li_tag.find("ol")
        if nested is not None:
            depths.append(1 + _extract_nav_depth(nested))
    return max(depths, default=1)


def inspect_epub(epub_bytes: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(epub_bytes)) as archive:
        names = archive.namelist()
        xhtml_names = [name for name in names if name.endswith(".xhtml")]
        content_xhtml = [name for name in xhtml_names if not name.endswith("nav.xhtml") and not name.endswith("cover.xhtml")]
        image_names = [name for name in names if re.search(r"\.(png|jpg|jpeg|gif|svg)$", name, re.IGNORECASE)]

        opf_name = _resolve_opf_name(names)
        package_title = ""
        package_creator = ""
        package_language = ""
        if opf_name:
            try:
                tree = etree.fromstring(archive.read(opf_name))
                ns = {"dc": "http://purl.org/dc/elements/1.1/"}
                package_title = "".join(tree.xpath("string(//*[local-name()='metadata']/*[local-name()='title'][1])")).strip()
                package_creator = "".join(tree.xpath("string(//*[local-name()='metadata']/*[local-name()='creator'][1])")).strip()
                package_language = "".join(tree.xpath("string(//*[local-name()='metadata']/*[local-name()='language'][1])")).strip()
            except Exception:
                package_title = ""
                package_creator = ""
                package_language = ""

        nav_entry_count = 0
        nav_depth = 0
        toc_labels: list[str] = []
        if "EPUB/nav.xhtml" in names:
            nav_soup = BeautifulSoup(archive.read("EPUB/nav.xhtml"), "xml")
            toc_nav = nav_soup.find("nav", attrs={"epub:type": "toc"}) or nav_soup.find("nav", {"type": "toc"})
            toc_root = toc_nav.find("ol") if toc_nav else None
            nav_entry_count = len(toc_root.find_all("li")) if toc_root else 0
            nav_depth = _extract_nav_depth(toc_root)
            toc_labels = [
                " ".join(a_tag.get_text(" ", strip=True).split())
                for a_tag in (toc_root.find_all("a") if toc_root else [])
                if a_tag.get_text(" ", strip=True)
            ]

        visible_junk_counts = Counter()
        broken_href_counts = Counter()
        heading_counts = Counter()
        broken_internal_anchors = 0

        for xhtml_name in xhtml_names:
            try:
                soup = BeautifulSoup(archive.read(xhtml_name), "xml")
            except Exception:
                continue
            full_text = soup.get_text(" ", strip=True)
            for key, pattern in VISIBLE_JUNK_PATTERNS.items():
                visible_junk_counts[key] += len(pattern.findall(full_text))
            for heading_tag in ("h1", "h2", "h3"):
                heading_counts[heading_tag] += len(soup.find_all(heading_tag))
            ids = {tag.get("id") for tag in soup.find_all(True) if tag.get("id")}
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"].strip()
                for key, pattern in BROKEN_HREF_PATTERNS.items():
                    if pattern.search(href):
                        broken_href_counts[key] += 1
                if href.startswith("#") and href[1:] and href[1:] not in ids:
                    broken_internal_anchors += 1

        return {
            "xhtml_count": len(content_xhtml),
            "image_count": len(image_names),
            "nav_entries": nav_entry_count,
            "nav_depth": nav_depth,
            "toc_labels_sample": toc_labels[:10],
            "package_title": package_title,
            "package_creator": package_creator,
            "package_language": package_language,
            "metadata_placeholder_title": bool(package_title and PLACEHOLDER_TITLE_RE.fullmatch(package_title.strip())),
            "metadata_placeholder_creator": bool(package_creator and PLACEHOLDER_AUTHOR_RE.fullmatch(package_creator.strip())),
            "visible_junk_counts": dict(visible_junk_counts),
            "broken_href_counts": dict(broken_href_counts),
            "broken_internal_anchors": broken_internal_anchors,
            "heading_counts": dict(heading_counts),
        }


def _build_case_blockers(
    *,
    quality: dict[str, Any],
    inspect: dict[str, Any],
    heading_summary: dict[str, Any],
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if quality.get("validation_status") == "failed" and not _epubcheck_recovered_by_heading_repair(
        quality=quality,
        heading_summary=heading_summary,
    ):
        blockers.append(
            {
                "code": "epubcheck_failed",
                "detail": (
                    f"pre_heading={quality.get('validation_status')}; "
                    f"post_heading={heading_summary.get('epubcheck_status', 'unavailable')}"
                ),
            }
        )
    if sum(inspect.get("visible_junk_counts", {}).values()) > 0:
        blockers.append({"code": "visible_reference_or_url_junk", "detail": json.dumps(inspect.get("visible_junk_counts", {}), ensure_ascii=False)})
    if sum(inspect.get("broken_href_counts", {}).values()) > 0:
        blockers.append({"code": "broken_href_patterns", "detail": json.dumps(inspect.get("broken_href_counts", {}), ensure_ascii=False)})
    if inspect.get("broken_internal_anchors", 0) > 0:
        blockers.append({"code": "broken_internal_anchors", "detail": str(inspect.get("broken_internal_anchors", 0))})
    if inspect.get("metadata_placeholder_title"):
        blockers.append({"code": "placeholder_title", "detail": inspect.get("package_title", "")})
    if inspect.get("metadata_placeholder_creator"):
        blockers.append({"code": "placeholder_creator", "detail": inspect.get("package_creator", "")})
    if heading_summary.get("epubcheck_status") == "failed":
        blockers.append({"code": "heading_repair_epubcheck_failed", "detail": "Heading/TOC repair broke EPUB validity."})
    return blockers


def _epubcheck_recovered_by_heading_repair(
    *,
    quality: dict[str, Any],
    heading_summary: dict[str, Any],
) -> bool:
    return (
        quality.get("validation_status") != "passed"
        and heading_summary.get("status") == "completed"
        and heading_summary.get("epubcheck_status") == "passed"
    )


def _analysis_payload(analysis: Any) -> dict[str, Any]:
    if hasattr(analysis, "to_dict") and callable(analysis.to_dict):
        return analysis.to_dict()
    return analysis if isinstance(analysis, dict) else {}


def _build_release_fallback_signal(
    *,
    analysis: Any,
    quality: dict[str, Any],
    case: CorpusCase,
) -> dict[str, Any]:
    payload = _analysis_payload(analysis)
    profile = str(payload.get("profile", "") or "").strip()
    reason = str(payload.get("profile_reason", "") or quality.get("fallback_reason", "") or "").strip()
    validation_tool = str(quality.get("validation_tool", "") or "").strip()
    used = profile == "legacy-fallback" or validation_tool == "legacy"
    severity = "blocker" if used and case.release_strict else ("warning" if used else "none")
    return {
        "used": used,
        "mode": profile or validation_tool or "unknown",
        "reason": reason,
        "validation_tool": validation_tool,
        "severity": severity,
        "release_strict": case.release_strict,
    }


def _apply_release_strictness(
    case: CorpusCase,
    *,
    blockers: list[dict[str, str]],
    warnings: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    if case.release_strict:
        return blockers, warnings

    relaxed_blockers = [
        item
        for item in blockers
        if item.get("code") not in {"placeholder_title", "placeholder_creator"}
    ]
    relaxed_warnings = [
        item
        for item in warnings
        if item.get("code") not in {"heading_manual_review"}
    ]
    return relaxed_blockers, relaxed_warnings


def _build_case_warnings(
    *,
    summary: dict[str, Any],
    quality: dict[str, Any],
    inspect: dict[str, Any],
    heading_summary: dict[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if _epubcheck_recovered_by_heading_repair(quality=quality, heading_summary=heading_summary):
        warnings.append(
            {
                "code": "pre_heading_epubcheck_recovered",
                "detail": (
                    f"pre_heading={quality.get('validation_status')}; "
                    f"post_heading={heading_summary.get('epubcheck_status')}"
                ),
            }
        )
    elif quality.get("validation_status") == "passed_with_warnings":
        warnings.append(
            {
                "code": "epub_validation_warning",
                "detail": str(
                    quality.get("validation_summary")
                    or quality.get("validation_tool")
                    or "validation_status=passed_with_warnings"
                ),
            }
        )
    text_cleanup = quality.get("text_cleanup") or {}
    review_needed = _safe_int(text_cleanup.get("review_needed_count", 0))
    blocked = _safe_int(text_cleanup.get("blocked_count", 0))
    if review_needed >= 200:
        warnings.append({"code": "high_review_noise", "detail": f"review_needed_count={review_needed}"})
    if blocked >= 500:
        warnings.append({"code": "high_blocked_noise", "detail": f"blocked_count={blocked}"})
    if inspect.get("nav_entries", 0) <= 2 and _safe_int(summary.get("section_count", 0)) >= 5:
        warnings.append({"code": "shallow_toc", "detail": f"nav_entries={inspect.get('nav_entries', 0)} section_count={summary.get('section_count', 0)}"})
    if heading_summary.get("release_status") == "fail":
        warnings.append({"code": "heading_release_fail", "detail": "Heading/TOC repair did not reach release-ready status."})
    elif heading_summary.get("release_status") == "pass_with_review":
        warnings.append({"code": "heading_manual_review", "detail": f"manual_review_count={heading_summary.get('manual_review_count', 0)}"})
    if inspect.get("package_language", "").lower() not in {"pl", "en"}:
        warnings.append({"code": "unexpected_language", "detail": inspect.get("package_language", "")})
    return warnings


def _fallback_detail(signal: dict[str, Any]) -> str:
    reason = str(signal.get("reason", "") or "").strip()
    mode = str(signal.get("mode", "unknown") or "unknown")
    validation_tool = str(signal.get("validation_tool", "") or "").strip()
    detail = f"mode={mode}"
    if validation_tool:
        detail += f"; validation_tool={validation_tool}"
    if reason:
        detail += f"; reason={reason[:240]}"
    return detail


def _derive_case_grade(blockers: list[dict[str, str]], warnings: list[dict[str, str]]) -> str:
    if blockers:
        return "fail"
    if warnings:
        return "pass_with_review"
    return "pass"


def _normalize_case_filters(case_filters: list[str] | None) -> list[str]:
    return [token.strip().lower() for token in (case_filters or []) if token.strip()]


def _resolve_manifest_root(manifest_payload: dict[str, Any]) -> Path:
    root_dir = Path(str(manifest_payload.get("root_dir", ".")))
    if root_dir.is_absolute():
        return root_dir
    return root_dir.resolve()


def _resolve_manifest_target_path(raw_path: str, *, manifest_root: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (manifest_root / candidate).resolve()


def _load_manifest_conversion_cases(
    *,
    manifest_path: str | Path | None = DEFAULT_MANIFEST_PATH,
) -> tuple[list[CorpusCase], CorpusSourceSummary]:
    if manifest_path is None:
        return list(CORPUS), CorpusSourceSummary(
            source_mode="legacy-static",
            manifest_path=None,
            manifest_case_count=0,
            eligible_manifest_cases=0,
            skipped_manifest_cases=0,
            skipped_case_labels=(),
            fallback_used=True,
            fallback_reason="manifest_disabled",
        )

    resolved_manifest = Path(manifest_path).resolve()
    if not resolved_manifest.exists():
        return list(CORPUS), CorpusSourceSummary(
            source_mode="legacy-static-fallback",
            manifest_path=str(resolved_manifest),
            manifest_case_count=0,
            eligible_manifest_cases=0,
            skipped_manifest_cases=0,
            skipped_case_labels=(),
            fallback_used=True,
            fallback_reason="manifest_missing",
        )

    try:
        manifest_payload = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        return list(CORPUS), CorpusSourceSummary(
            source_mode="legacy-static-fallback",
            manifest_path=str(resolved_manifest),
            manifest_case_count=0,
            eligible_manifest_cases=0,
            skipped_manifest_cases=0,
            skipped_case_labels=(),
            fallback_used=True,
            fallback_reason=f"manifest_unreadable:{exc.__class__.__name__}",
        )

    manifest_root = _resolve_manifest_root(manifest_payload)
    manifest_cases = manifest_payload.get("cases", [])
    eligible_cases: list[CorpusCase] = []
    skipped_labels: list[str] = []
    for case in manifest_cases:
        input_type = str(case.get("input_type", "")).lower()
        case_id = str(case.get("id", "unknown"))
        if input_type != "pdf":
            skipped_labels.append(f"{case_id} ({input_type or 'unknown'})")
            continue
        target_path = str(case.get("target_path") or case.get("target") or "").strip()
        if not target_path:
            skipped_labels.append(f"{case_id} (missing-target)")
            continue
        eligible_cases.append(
            CorpusCase(
                _resolve_manifest_target_path(target_path, manifest_root=manifest_root),
                document_class=str(case.get("document_class", "unknown")),
                notes=str(case.get("notes", "")),
                release_strict=bool(case.get("release_strict", True)),
            )
        )

    return eligible_cases, CorpusSourceSummary(
        source_mode="manifest-backed",
        manifest_path=str(resolved_manifest),
        manifest_case_count=len(manifest_cases),
        eligible_manifest_cases=len(eligible_cases),
        skipped_manifest_cases=len(skipped_labels),
        skipped_case_labels=tuple(skipped_labels),
        fallback_used=False,
        fallback_reason="",
    )


def _build_case_pool(
    *,
    manifest_path: str | Path | None = DEFAULT_MANIFEST_PATH,
) -> list[tuple[str, CorpusCase]]:
    pool: list[tuple[str, CorpusCase]] = []
    for case in ANALYSIS_ONLY:
        pool.append(("analysis-only", case))
    conversion_cases, _ = _load_manifest_conversion_cases(manifest_path=manifest_path)
    for case in conversion_cases:
        pool.append(("convert-and-audit", case))
    return pool


def _select_corpus_batch(
    *,
    case_filters: list[str] | None,
    manifest_path: str | Path | None = DEFAULT_MANIFEST_PATH,
    shard_count: int | None = None,
    shard_index: int | None = None,
    limit: int | None = None,
) -> tuple[list[tuple[str, CorpusCase]], CorpusBatchSelection]:
    normalized_filters = _normalize_case_filters(case_filters)
    cases = _build_case_pool(manifest_path=manifest_path)
    matching_cases = [
        case_entry
        for case_entry in cases
        if _case_matches_filters(case_entry[1], normalized_filters)
    ]

    selected_cases = list(matching_cases)
    if shard_count is not None or shard_index is not None:
        if shard_count is None or shard_index is None:
            raise ValueError("Shard selection requires both shard_count and shard_index.")
        if shard_count < 1:
            raise ValueError("shard_count must be at least 1.")
        if shard_index < 1 or shard_index > shard_count:
            raise ValueError("shard_index must be within the shard_count range.")
        selected_cases = [
            case_entry
            for index, case_entry in enumerate(matching_cases)
            if index % shard_count == shard_index - 1
        ]

    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1.")
        selected_cases = selected_cases[:limit]

    coverage_status = (
        "complete"
        if len(selected_cases) == len(cases) and not normalized_filters and shard_count in {None, 1} and shard_index in {None, 1} and limit is None
        else "partial"
    )
    selection = CorpusBatchSelection(
        total_cases=len(cases),
        matching_cases=len(matching_cases),
        selected_cases=len(selected_cases),
        skipped_cases=max(0, len(matching_cases) - len(selected_cases)),
        coverage_status=coverage_status,
        filters=tuple(normalized_filters),
        shard_count=shard_count,
        shard_index=shard_index,
        limit=limit,
        selected_case_labels=tuple(f"{case.path.name} ({case.document_class})" for _, case in selected_cases),
    )
    return selected_cases, selection


def _run_analysis_only_case(case: CorpusCase) -> dict[str, Any]:
    if not case.path.exists():
        return {"file": str(case.path), "document_class": case.document_class, "status": "missing"}
    analysis = analyze_publication(str(case.path), preferred_profile="auto-premium")
    return {
        "file": case.path.name,
        "document_class": case.document_class,
        "mode": "analysis-only",
        "notes": case.notes,
        "analysis": analysis.to_dict() if hasattr(analysis, "to_dict") else analysis,
    }


def _run_conversion_case(case: CorpusCase, *, run_heading_repair: bool) -> dict[str, Any]:
    if not case.path.exists():
        return {"file": str(case.path), "document_class": case.document_class, "status": "missing"}

    result = convert_pdf_to_epub_with_report(
        str(case.path),
        config=ConversionConfig(profile="auto-premium"),
        original_filename=case.path.name,
    )
    analysis = result["analysis"]
    summary = result.get("document_summary", {})
    quality = result.get("quality_report", {})
    converted_epub_bytes = result["epub_bytes"]
    inspect = inspect_epub(converted_epub_bytes)
    policy = load_size_budget_policy()
    size_gate = evaluate_size_budget(
        budget_key=case.document_class,
        budget=get_document_size_budget(case.document_class, policy=policy),
        epub_size_bytes=len(converted_epub_bytes),
        inspection=inspect_epub_archive(converted_epub_bytes),
        label="klasy dokumentu",
    )

    heading_summary: dict[str, Any] = {
        "status": "skipped",
        "release_status": "skipped",
        "toc_entries_before": inspect.get("nav_entries", 0),
        "toc_entries_after": inspect.get("nav_entries", 0),
        "headings_removed": 0,
        "manual_review_count": 0,
        "epubcheck_status": "unavailable",
    }
    repaired_inspect = inspect
    if run_heading_repair:
        heading_result = repair_epub_headings_and_toc(
            converted_epub_bytes,
            title_hint=str(summary.get("title", "")),
            author_hint=str(summary.get("author", "")),
            language_hint=str(inspect.get("package_language", "")),
            publication_profile=(analysis.profile if hasattr(analysis, "profile") else None),
        )
        heading_summary = {
            "status": "completed",
            "release_status": heading_result.summary.get("release_status", "unavailable"),
            "toc_entries_before": heading_result.summary.get("toc_entries_before", inspect.get("nav_entries", 0)),
            "toc_entries_after": heading_result.summary.get("toc_entries_after", inspect.get("nav_entries", 0)),
            "headings_removed": heading_result.summary.get("headings_removed", 0),
            "manual_review_count": heading_result.summary.get("manual_review_count", 0),
            "epubcheck_status": heading_result.summary.get("epubcheck_status", "unavailable"),
        }
        repaired_inspect = inspect_epub(heading_result.epub_bytes)

    blockers = _build_case_blockers(
        quality=quality,
        inspect=repaired_inspect,
        heading_summary=heading_summary,
    )
    warnings = _build_case_warnings(
        summary=summary,
        quality=quality,
        inspect=repaired_inspect,
        heading_summary=heading_summary,
    )
    blockers, warnings = _apply_release_strictness(
        case,
        blockers=blockers,
        warnings=warnings,
    )
    release_fallback = _build_release_fallback_signal(
        analysis=analysis,
        quality=quality,
        case=case,
    )
    if release_fallback["used"]:
        if release_fallback["severity"] == "blocker":
            blockers.append({"code": "legacy_fallback_used", "detail": _fallback_detail(release_fallback)})
        else:
            warnings.append({"code": "legacy_fallback_used", "detail": _fallback_detail(release_fallback)})
    if size_gate["status"] == "failed":
        blockers.append({"code": "size_budget_failed", "detail": size_gate["message"]})
    elif size_gate["status"] == "passed_with_warnings":
        warnings.append({"code": "size_budget_warning", "detail": size_gate["message"]})
    grade = _derive_case_grade(blockers, warnings)

    return {
        "file": case.path.name,
        "document_class": case.document_class,
        "mode": "convert-and-audit",
        "notes": case.notes,
        "release_strict": case.release_strict,
        "analysis": analysis.to_dict() if hasattr(analysis, "to_dict") else analysis,
        "summary": summary,
        "quality": quality,
        "epub_stats": inspect,
        "heading_repair": heading_summary,
        "post_heading_epub_stats": repaired_inspect,
        "size_gate": size_gate,
        "release_fallback": release_fallback,
        "grade": grade,
        "blockers": blockers,
        "warnings": warnings,
        "size_bytes": len(converted_epub_bytes),
    }


def _build_overall_summary(
    rows: list[dict[str, Any]],
    *,
    batch_selection: CorpusBatchSelection | None = None,
    source_summary: CorpusSourceSummary | None = None,
) -> dict[str, Any]:
    converted = [row for row in rows if row.get("mode") == "convert-and-audit"]
    grade_counts = Counter(row.get("grade", "unknown") for row in converted)
    blocker_counts = Counter(blocker["code"] for row in converted for blocker in row.get("blockers", []))
    warning_counts = Counter(warning["code"] for row in converted for warning in row.get("warnings", []))
    fallback_counts = Counter(
        (row.get("release_fallback") or {}).get("severity", "none")
        for row in converted
        if (row.get("release_fallback") or {}).get("used")
    )
    class_grade = {
        row["document_class"]: row.get("grade", "unknown")
        for row in converted
    }
    proof_scope = batch_selection.coverage_status if batch_selection is not None else "complete"
    overall_status = "passed"
    if grade_counts.get("fail", 0) or not converted:
        overall_status = "failed"
    elif grade_counts.get("pass_with_review", 0) or proof_scope == "partial" or (
        source_summary is not None and source_summary.fallback_used
    ):
        overall_status = "passed_with_warnings"
    return {
        "converted_case_count": len(converted),
        "analysis_only_case_count": len([row for row in rows if row.get("mode") == "analysis-only"]),
        "grade_counts": dict(grade_counts),
        "blocker_counts": dict(blocker_counts),
        "warning_counts": dict(warning_counts),
        "release_fallback_counts": dict(fallback_counts),
        "recovered_epubcheck_count": warning_counts.get("pre_heading_epubcheck_recovered", 0),
        "class_grade": class_grade,
        "overall_status": overall_status,
        "proof_scope": proof_scope,
        "source_mode": source_summary.source_mode if source_summary is not None else "legacy-static",
    }


def _build_markdown_report(
    rows: list[dict[str, Any]],
    overall: dict[str, Any],
    *,
    batch_selection: CorpusBatchSelection | None = None,
    source_summary: CorpusSourceSummary | None = None,
) -> str:
    lines = [
        "# Premium Corpus Smoke",
        "",
    ]
    if batch_selection is not None:
        selection_label = "complete" if batch_selection.coverage_status == "complete" else "partial"
        lines.extend(
            [
                "## Run scope",
                "",
                f"- Coverage: `{selection_label}`",
                f"- Total cases: `{batch_selection.total_cases}`",
                f"- Matching cases: `{batch_selection.matching_cases}`",
                f"- Selected cases: `{batch_selection.selected_cases}`",
                f"- Skipped cases: `{batch_selection.skipped_cases}`",
                f"- Filters: `{', '.join(batch_selection.filters) if batch_selection.filters else 'none'}`",
                (
                    f"- Shard: `{batch_selection.shard_index}/{batch_selection.shard_count}`"
                    if batch_selection.shard_count and batch_selection.shard_index
                    else "- Shard: `none`"
                ),
                f"- Limit: `{batch_selection.limit}`" if batch_selection.limit is not None else "- Limit: `none`",
            ]
        )
        if batch_selection.selected_case_labels:
            lines.append(f"- Selected batch: `{', '.join(batch_selection.selected_case_labels)}`")
        if source_summary is not None:
            lines.append(f"- Corpus source: `{source_summary.source_mode}`")
            if source_summary.manifest_path:
                lines.append(f"- Manifest: `{source_summary.manifest_path}`")
            lines.append(f"- Eligible manifest PDF cases: `{source_summary.eligible_manifest_cases}`")
            lines.append(f"- Skipped manifest cases: `{source_summary.skipped_manifest_cases}`")
            if source_summary.skipped_case_labels:
                lines.append(f"- Skipped inputs: `{', '.join(source_summary.skipped_case_labels)}`")
            if source_summary.fallback_used:
                lines.append(f"- Fallback reason: `{source_summary.fallback_reason}`")
        lines.append("")
    lines.extend(
        [
        "## Summary",
        "",
        f"- Overall status: `{overall['overall_status']}`",
        f"- Proof scope: `{overall.get('proof_scope', 'complete')}`",
        f"- Converted cases: {overall['converted_case_count']}",
        f"- Analysis-only cases: {overall['analysis_only_case_count']}",
        f"- Grade counts: `{json.dumps(overall['grade_counts'], ensure_ascii=False)}`",
        f"- Repeated blockers: `{json.dumps(overall['blocker_counts'], ensure_ascii=False)}`",
        f"- Repeated warnings: `{json.dumps(overall['warning_counts'], ensure_ascii=False)}`",
        f"- Release fallback counts: `{json.dumps(overall.get('release_fallback_counts', {}), ensure_ascii=False)}`",
        f"- Recovered EPUBCheck cases: `{overall.get('recovered_epubcheck_count', 0)}`",
        "",
        "## Cases",
        "",
        ]
    )
    for row in rows:
        lines.extend(
            [
                f"### {row.get('file')}",
                "",
                f"- Class: `{row.get('document_class', 'unknown')}`",
                f"- Mode: `{row.get('mode', 'unknown')}`",
            ]
        )
        if row.get("notes"):
            lines.append(f"- Notes: {row['notes']}")
        if row.get("mode") == "analysis-only":
            analysis = row.get("analysis", {})
            lines.extend(
                [
                    f"- Detected profile: `{analysis.get('profile', 'unknown')}`",
                    f"- Reason: {analysis.get('profile_reason', '')}",
                    "",
                ]
            )
            continue
        quality = row.get("quality", {})
        text_cleanup = quality.get("text_cleanup") or {}
        heading_repair = row.get("heading_repair") or {}
        epub_stats = row.get("post_heading_epub_stats") or row.get("epub_stats") or {}
        lines.extend(
            [
                f"- Grade: `{row.get('grade', 'unknown')}`",
                f"- EPUBCheck: `{quality.get('validation_status', 'unavailable')}`",
                f"- Profile: `{(row.get('analysis') or {}).get('profile', 'unknown')}`",
                f"- TOC entries/depth: `{epub_stats.get('nav_entries', 0)}` / `{epub_stats.get('nav_depth', 0)}`",
                f"- Metadata: title=`{epub_stats.get('package_title', '')}`, creator=`{epub_stats.get('package_creator', '')}`, language=`{epub_stats.get('package_language', '')}`",
                f"- Text cleanup: auto=`{text_cleanup.get('auto_fix_count', 0)}` review=`{text_cleanup.get('review_needed_count', 0)}` blocked=`{text_cleanup.get('blocked_count', 0)}`",
                f"- Reference cleanup: `{json.dumps((text_cleanup.get('reference_cleanup') or {}), ensure_ascii=False)}`",
                f"- Heading repair: release=`{heading_repair.get('release_status', 'skipped')}` toc `{heading_repair.get('toc_entries_before', 0)} -> {heading_repair.get('toc_entries_after', 0)}` removed=`{heading_repair.get('headings_removed', 0)}` review=`{heading_repair.get('manual_review_count', 0)}`",
            ]
        )
        size_gate = row.get("size_gate") or {}
        if size_gate:
            lines.append(f"- Size gate: `{size_gate.get('status', 'unknown')}`")
            lines.append(
                f"- Size budget: `{size_gate.get('epub_size_bytes', 0)}` B against warn `{size_gate.get('warn_bytes')}` / hard `{size_gate.get('hard_bytes')}`"
            )
        if row.get("blockers"):
            lines.append("- Blockers:")
            for blocker in row["blockers"]:
                lines.append(f"  - `{blocker['code']}`: {blocker['detail']}")
        if row.get("warnings"):
            lines.append("- Warnings:")
            for warning in row["warnings"]:
                lines.append(f"  - `{warning['code']}`: {warning['detail']}")
        fallback_signal = row.get("release_fallback") or {}
        if fallback_signal.get("used"):
            lines.append(f"- Release fallback: `{fallback_signal.get('severity', 'unknown')}` ({_fallback_detail(fallback_signal)})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _persist_reports(
    *,
    rows: list[dict[str, Any]],
    json_path: Path,
    md_path: Path,
    batch_selection: CorpusBatchSelection | None = None,
    source_summary: CorpusSourceSummary | None = None,
) -> None:
    overall = _build_overall_summary(rows, batch_selection=batch_selection, source_summary=source_summary)
    payload = {
        "overall_status": overall["overall_status"],
        "overall": overall,
        "cases": rows,
    }
    if batch_selection is not None:
        payload["run_scope"] = asdict(batch_selection)
    if source_summary is not None:
        payload["corpus_source"] = asdict(source_summary)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(
        _build_markdown_report(rows, overall, batch_selection=batch_selection, source_summary=source_summary),
        encoding="utf-8",
    )


def run_premium_corpus_smoke(
    *,
    manifest_path: str | Path | None = DEFAULT_MANIFEST_PATH,
    output_json: str | Path = "reports/premium_corpus_smoke_report.json",
    output_md: str | Path = "reports/premium_corpus_smoke_report.md",
    skip_heading_repair: bool = False,
    case_filters: list[str] | None = None,
    limit: int | None = None,
    shard_count: int | None = None,
    shard_index: int | None = None,
    progress: bool = False,
) -> dict[str, Any]:
    normalized_filters = _normalize_case_filters(case_filters)
    selected_cases, batch_selection = _select_corpus_batch(
        case_filters=normalized_filters,
        manifest_path=manifest_path,
        shard_count=shard_count,
        shard_index=shard_index,
        limit=limit,
    )
    source_summary = _load_manifest_conversion_cases(manifest_path=manifest_path)[1]
    json_path = Path(output_json)
    md_path = Path(output_md)

    if progress and batch_selection.coverage_status == "partial":
        selection_bits = [f"{batch_selection.selected_cases}/{batch_selection.matching_cases} cases"]
        if batch_selection.filters:
            selection_bits.append(f"filters={', '.join(batch_selection.filters)}")
        if batch_selection.shard_count and batch_selection.shard_index:
            selection_bits.append(f"shard={batch_selection.shard_index}/{batch_selection.shard_count}")
        if batch_selection.limit is not None:
            selection_bits.append(f"limit={batch_selection.limit}")
        print(f"[batch] partial corpus run: {', '.join(selection_bits)}")

    rows: list[dict[str, Any]] = []
    for mode, case in selected_cases:
        if progress:
            print(f"[{mode}] {case.path.name} ({case.document_class})")
        if mode == "analysis-only":
            rows.append(_run_analysis_only_case(case))
        else:
            rows.append(_run_conversion_case(case, run_heading_repair=not skip_heading_repair))
        _persist_reports(
            rows=rows,
            json_path=json_path,
            md_path=md_path,
            batch_selection=batch_selection,
            source_summary=source_summary,
        )

    overall = _build_overall_summary(rows, batch_selection=batch_selection, source_summary=source_summary)
    return {
        "overall_status": overall["overall_status"],
        "overall": overall,
        "cases": rows,
        "run_scope": asdict(batch_selection),
        "corpus_source": asdict(source_summary),
    }


def _case_matches_filters(case: CorpusCase, filters: list[str]) -> bool:
    if not filters:
        return True
    haystacks = [case.path.name.lower(), case.document_class.lower(), case.notes.lower()]
    return any(any(token in haystack for haystack in haystacks) for token in filters)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run premium KindleMaster corpus smoke across mixed document classes.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--output-json", default="reports/premium_corpus_smoke_report.json")
    parser.add_argument("--output-md", default="reports/premium_corpus_smoke_report.md")
    parser.add_argument("--skip-heading-repair", action="store_true")
    parser.add_argument("--case", action="append", default=[], help="Run only matching cases by filename or document class.")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of selected cases after filters and sharding.")
    parser.add_argument("--shard-count", type=int, default=None, help="Split the matched corpus into this many deterministic shards.")
    parser.add_argument("--shard-index", type=int, default=None, help="1-based shard index to execute when sharding is enabled.")
    args = parser.parse_args()

    try:
        payload = run_premium_corpus_smoke(
            manifest_path=args.manifest,
            output_json=args.output_json,
            output_md=args.output_md,
            skip_heading_repair=args.skip_heading_repair,
            case_filters=args.case,
            limit=args.limit,
            shard_count=args.shard_count,
            shard_index=args.shard_index,
            progress=True,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if payload["overall"].get("overall_status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
