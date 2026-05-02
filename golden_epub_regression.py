from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from epub_text_artifacts import analyze_epub_text_artifacts
from epub_validation import validate_epub_path
from premium_corpus_smoke import inspect_epub


DEFAULT_MANIFEST_PATH = Path("reference_inputs/golden_epub_expectations.json")
DEFAULT_ARTIFACT_ROOT = Path("output/corpus/smoke")
DEFAULT_REPORTS_DIR = Path("reports/golden_epub_regression")
NOISY_TOC_PATTERNS = (
    r"^(?:input|output|task|activity|key|external|data|process)$",
    r"^object\s+\d+$",
    r"^state\s+\d+$",
    r"^rank\s*=",
)


@dataclass(frozen=True)
class GoldenCaseResult:
    case_id: str
    document_class: str
    input_type: str
    status: str
    artifact_path: str
    features: dict[str, Any]
    assertions: list[dict[str, Any]]


def run_golden_epub_regression(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    artifact_root: str | Path = DEFAULT_ARTIFACT_ROOT,
    reports_dir: str | Path = DEFAULT_REPORTS_DIR,
    write_reports: bool = True,
) -> dict[str, Any]:
    manifest = _load_manifest(Path(manifest_path))
    resolved_artifact_root = Path(artifact_root)
    results: list[GoldenCaseResult] = []
    for case in manifest.get("cases", []):
        result = _evaluate_case(case, artifact_root=resolved_artifact_root)
        results.append(result)

    status = _rollup_status([result.status for result in results])
    payload = {
        "version": manifest.get("version", 1),
        "status": status,
        "case_count": len(results),
        "status_counts": dict(Counter(result.status for result in results)),
        "manifest_path": str(Path(manifest_path)),
        "artifact_root": str(resolved_artifact_root),
        "cases": [_case_to_dict(result) for result in results],
    }
    if write_reports:
        resolved_reports_dir = Path(reports_dir)
        resolved_reports_dir.mkdir(parents=True, exist_ok=True)
        (resolved_reports_dir / "golden_epub_regression.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (resolved_reports_dir / "golden_epub_regression.md").write_text(
            build_golden_regression_markdown(payload),
            encoding="utf-8",
        )
    return payload


def build_golden_regression_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# KindleMaster Golden EPUB Regression",
        "",
        f"- Status: `{payload.get('status', 'unknown')}`",
        f"- Cases: `{payload.get('case_count', 0)}`",
        f"- Status counts: `{json.dumps(payload.get('status_counts', {}), ensure_ascii=False)}`",
        f"- Manifest: `{payload.get('manifest_path', '')}`",
        f"- Artifact root: `{payload.get('artifact_root', '')}`",
        "",
        "## Cases",
        "",
    ]
    for case in payload.get("cases", []):
        features = case.get("features") or {}
        failed = [item for item in case.get("assertions", []) if item.get("status") == "failed"]
        warnings = [item for item in case.get("assertions", []) if item.get("status") == "passed_with_warnings"]
        lines.extend(
            [
                f"### {case.get('case_id', '<case>')}",
                "",
                f"- Status: `{case.get('status', 'unknown')}`",
                f"- Class: `{case.get('document_class', '')}` / `{case.get('input_type', '')}`",
                f"- Artifact: `{case.get('artifact_path', '')}`",
                f"- Validation: `{features.get('validation_status', 'unknown')}`",
                f"- XHTML / TOC / images / tables: `{features.get('xhtml_count', 0)}` / "
                f"`{features.get('nav_entries', 0)}` / `{features.get('image_count', 0)}` / "
                f"`{features.get('table_count', 0)}`",
                f"- Artifact rate: `{features.get('artifact_rate_per_1000_words', 0)}` per 1000 words",
            ]
        )
        if failed:
            lines.append(f"- Failed assertions: `{', '.join(item.get('id', '') for item in failed)}`")
        if warnings:
            lines.append(f"- Warning assertions: `{', '.join(item.get('id', '') for item in warnings)}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def inspect_epub_golden_features(epub_path: str | Path) -> dict[str, Any]:
    path = Path(epub_path)
    epub_bytes = path.read_bytes()
    stats = inspect_epub(epub_bytes)
    artifact_metrics = analyze_epub_text_artifacts(epub_bytes)
    validation = validate_epub_path(path)
    table_stats = _inspect_tables(epub_bytes)
    toc_headings = _inspect_toc_and_headings(epub_bytes)
    validation_summary = validation.get("summary") or {}
    document_stats = validation.get("document_stats") or {}
    return {
        **stats,
        **table_stats,
        **toc_headings,
        "validation_status": validation_summary.get("status", "unknown"),
        "validation_error_count": validation_summary.get("error_count", 0),
        "validation_warning_count": validation_summary.get("warning_count", 0),
        "documents_with_duplicate_ids": document_stats.get("documents_with_duplicate_ids", 0),
        "artifact_status": artifact_metrics.get("status", "unknown"),
        "artifact_count": artifact_metrics.get("artifact_count", 0),
        "artifact_rate_per_1000_words": artifact_metrics.get("artifact_rate_per_1000_words", 0.0),
        "word_count": artifact_metrics.get("word_count", 0),
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Golden EPUB expectation manifest not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluate_case(case: dict[str, Any], *, artifact_root: Path) -> GoldenCaseResult:
    case_id = str(case.get("id", "")).strip()
    document_class = str(case.get("document_class", "")).strip()
    input_type = str(case.get("input_type", "")).strip()
    artifact_path = _resolve_artifact_path(case, artifact_root=artifact_root)
    if artifact_path is None:
        return GoldenCaseResult(
            case_id=case_id,
            document_class=document_class,
            input_type=input_type,
            status="failed",
            artifact_path="",
            features={},
            assertions=[
                {
                    "id": "artifact_available",
                    "status": "failed",
                    "detail": "No EPUB artifact candidate exists for this golden case.",
                }
            ],
        )
    features = inspect_epub_golden_features(artifact_path)
    assertions = _evaluate_expectations(case.get("expectations") or {}, features)
    return GoldenCaseResult(
        case_id=case_id,
        document_class=document_class,
        input_type=input_type,
        status=_rollup_status([assertion["status"] for assertion in assertions]),
        artifact_path=str(artifact_path),
        features=features,
        assertions=assertions,
    )


def _resolve_artifact_path(case: dict[str, Any], *, artifact_root: Path) -> Path | None:
    candidates: list[str] = list(case.get("artifact_candidates") or [])
    case_id = str(case.get("id", "")).strip()
    if case_id:
        candidates.extend(
            [
                f"{case_id}.epub",
                f"{case_id}/final.epub",
            ]
        )
    for candidate in candidates:
        candidate_path = Path(candidate)
        if not candidate_path.is_absolute():
            candidate_path = artifact_root / candidate_path
        if candidate_path.exists():
            return candidate_path
    return None


def _evaluate_expectations(expectations: dict[str, Any], features: dict[str, Any]) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []

    def add(assertion_id: str, passed: bool, detail: str, *, severity: str = "blocker") -> None:
        assertions.append(
            {
                "id": assertion_id,
                "status": "passed" if passed else ("failed" if severity == "blocker" else "passed_with_warnings"),
                "severity": severity,
                "detail": detail,
            }
        )

    allowed_validation = set(expectations.get("allowed_validation_statuses") or ["passed", "passed_with_warnings"])
    add(
        "validation_status_allowed",
        str(features.get("validation_status")) in allowed_validation,
        f"validation_status={features.get('validation_status')}; allowed={sorted(allowed_validation)}",
    )
    min_xhtml = int(expectations.get("min_xhtml_count", 1))
    add("min_xhtml_count", int(features.get("xhtml_count") or 0) >= min_xhtml, f"xhtml_count={features.get('xhtml_count')}; min={min_xhtml}")
    min_toc = int(expectations.get("min_nav_entries", 1))
    add("min_nav_entries", int(features.get("nav_entries") or 0) >= min_toc, f"nav_entries={features.get('nav_entries')}; min={min_toc}")

    if "min_image_count" in expectations:
        min_images = int(expectations["min_image_count"])
        add("min_image_count", int(features.get("image_count") or 0) >= min_images, f"image_count={features.get('image_count')}; min={min_images}")
    if "min_table_count" in expectations:
        min_tables = int(expectations["min_table_count"])
        add("min_table_count", int(features.get("table_count") or 0) >= min_tables, f"table_count={features.get('table_count')}; min={min_tables}")
    if "max_table_columns" in expectations:
        max_columns = int(expectations["max_table_columns"])
        add(
            "max_table_columns",
            int(features.get("max_table_columns") or 0) <= max_columns,
            f"max_table_columns={features.get('max_table_columns')}; max={max_columns}",
        )
    if "language" in expectations:
        expected_language = str(expectations["language"]).strip().lower()
        actual_language = str(features.get("package_language") or "").strip().lower()
        add("package_language", actual_language == expected_language, f"package_language={actual_language}; expected={expected_language}")
    if expectations.get("metadata_no_placeholders", True):
        add(
            "metadata_no_placeholders",
            not features.get("metadata_placeholder_title") and not features.get("metadata_placeholder_creator"),
            f"title_placeholder={features.get('metadata_placeholder_title')}; creator_placeholder={features.get('metadata_placeholder_creator')}",
        )
    max_artifact_rate = expectations.get("max_artifact_rate_per_1000_words")
    if max_artifact_rate is not None:
        actual_rate = float(features.get("artifact_rate_per_1000_words") or 0.0)
        add(
            "max_artifact_rate_per_1000_words",
            actual_rate <= float(max_artifact_rate),
            f"artifact_rate_per_1000_words={actual_rate}; max={max_artifact_rate}",
            severity=str(expectations.get("artifact_rate_severity") or "blocker"),
        )
    max_duplicate_id_docs = int(expectations.get("max_documents_with_duplicate_ids", 0))
    add(
        "no_duplicate_id_documents",
        int(features.get("documents_with_duplicate_ids") or 0) <= max_duplicate_id_docs,
        f"documents_with_duplicate_ids={features.get('documents_with_duplicate_ids')}; max={max_duplicate_id_docs}",
    )
    add(
        "no_broken_internal_anchors",
        int(features.get("broken_internal_anchors") or 0) <= int(expectations.get("max_broken_internal_anchors", 0)),
        f"broken_internal_anchors={features.get('broken_internal_anchors')}",
    )

    for pattern in expectations.get("forbidden_toc_patterns") or NOISY_TOC_PATTERNS:
        count = _count_pattern_matches(features.get("toc_labels") or [], pattern)
        add("forbidden_toc_pattern", count == 0, f"pattern={pattern}; matches={count}")
    for pattern in expectations.get("forbidden_heading_patterns") or []:
        count = _count_pattern_matches(features.get("heading_labels") or [], pattern)
        add("forbidden_heading_pattern", count == 0, f"pattern={pattern}; matches={count}")
    return assertions


def _count_pattern_matches(values: list[str], pattern: str) -> int:
    regex = re.compile(pattern, re.IGNORECASE)
    return sum(1 for value in values if regex.search(str(value or "").strip()))


def _rollup_status(statuses: list[str]) -> str:
    if not statuses:
        return "skipped"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "passed_with_warnings" for status in statuses):
        return "passed_with_warnings"
    return "passed"


def _inspect_tables(epub_bytes: bytes) -> dict[str, Any]:
    table_count = 0
    max_columns = 0
    row_count = 0
    with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".xhtml", ".html")):
                continue
            soup = BeautifulSoup(archive.read(name), "html.parser")
            for table in soup.find_all("table"):
                table_count += 1
                rows = table.find_all("tr")
                row_count += len(rows)
                for row in rows:
                    max_columns = max(max_columns, len(row.find_all(["td", "th"])))
    return {
        "table_count": table_count,
        "table_row_count": row_count,
        "max_table_columns": max_columns,
    }


def _inspect_toc_and_headings(epub_bytes: bytes) -> dict[str, Any]:
    toc_labels: list[str] = []
    heading_labels: list[str] = []
    with zipfile.ZipFile(BytesIO(epub_bytes)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".xhtml", ".html")):
                continue
            soup = BeautifulSoup(archive.read(name), "html.parser")
            for heading in soup.find_all(["h1", "h2", "h3"]):
                label = " ".join(heading.get_text(" ", strip=True).split())
                if label:
                    heading_labels.append(label)
            if name.lower().endswith("nav.xhtml"):
                toc_nav = soup.find("nav", attrs={"epub:type": "toc"}) or soup.find("nav", {"type": "toc"})
                toc_root = toc_nav.find("ol") if toc_nav else None
                if toc_root:
                    for a_tag in toc_root.find_all("a"):
                        label = " ".join(a_tag.get_text(" ", strip=True).split())
                        if label:
                            toc_labels.append(label)
    return {
        "toc_labels": toc_labels,
        "heading_labels": heading_labels,
        "toc_label_count": len(toc_labels),
        "heading_label_count": len(heading_labels),
    }


def _case_to_dict(result: GoldenCaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case_id,
        "document_class": result.document_class,
        "input_type": result.input_type,
        "status": result.status,
        "artifact_path": result.artifact_path,
        "features": result.features,
        "assertions": result.assertions,
    }
