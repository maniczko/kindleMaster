from __future__ import annotations

import argparse
import html
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from kindle_semantic_cleanup import (
    REFERENCE_ENTRY_ID_RE,
    REFERENCE_INLINE_ID_RE,
    REFERENCE_URL_START_RE,
    _collect_reference_link_candidates,
    _dedupe_dom_ids,
    _extract_epub,
    _get_spine_xhtml_paths,
    _infer_reference_name_fields,
    _is_numeric_reference_id,
    _locate_opf,
    _looks_like_invalid_reference_title,
    _looks_like_reference_section_title,
    _normalize_text,
    _normalize_reference_href,
    _pack_epub,
    _reference_display_id_from_number,
    _reference_numeric_value,
    _split_reference_entries_from_text,
    _split_reference_title_and_description,
    _strip_reference_link_candidates,
)
from premium_tools import run_epubcheck


@dataclass
class ReferenceRepairRecord:
    document_path: str
    section_id: str
    ref_id: str
    display_ref_id: str
    source_name: str
    source_title: str
    description: str
    url: str
    links: list[str] = field(default_factory=list)
    confidence: float = 0.0
    review_flag: bool = False
    numbering_repaired: bool = False
    link_status: str = "unresolved"
    original_fragments: list[str] = field(default_factory=list)
    unresolved_fragments: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_path": self.document_path,
            "section_id": self.section_id,
            "ref_id": self.ref_id,
            "display_ref_id": self.display_ref_id,
            "source_name": self.source_name,
            "source_title": self.source_title,
            "description": self.description,
            "url": self.url,
            "links": list(self.links),
            "confidence": round(float(self.confidence or 0.0), 3),
            "review_flag": bool(self.review_flag),
            "numbering_repaired": bool(self.numbering_repaired),
            "link_status": self.link_status,
            "original_fragments": list(self.original_fragments),
            "unresolved_fragments": list(self.unresolved_fragments),
        }


@dataclass
class ReferenceRepairResult:
    epub_bytes: bytes
    summary: dict[str, Any]
    records: list[ReferenceRepairRecord] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    citation_coverage: list[dict[str, Any]] = field(default_factory=list)
    before_after: list[dict[str, str]] = field(default_factory=list)
    epubcheck: dict[str, Any] = field(default_factory=dict)
    markdown_summary: str = ""
    before_after_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "records": [record.to_dict() for record in self.records],
            "sections": self.sections,
            "citation_coverage": self.citation_coverage,
            "before_after": self.before_after,
            "epubcheck": self.epubcheck,
        }


@dataclass
class _DocumentReferenceRepair:
    modified: bool
    document_path: str
    records: list[ReferenceRepairRecord] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    before_after: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _ReferenceUrlCandidate:
    raw: str
    normalized: str
    position: int

    @property
    def repaired(self) -> bool:
        return bool(self.normalized) and self.raw != self.normalized


@dataclass
class _ReferenceDescriptorCandidate:
    source_id: str
    display_source_id: str
    source_name: str
    source_title: str
    description: str
    position: int
    original_text: str
    original_fragments: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)


@dataclass
class _ReferenceScopeUnit:
    position: int
    node_name: str
    html: str
    text: str
    descriptor_text: str
    source_ids: list[str]
    url_candidates: list[_ReferenceUrlCandidate]
    unresolved_fragments: list[str]


REFERENCE_HEADER_ROW_RE = re.compile(
    r"(?i)^(?:id|lp|nr|no)\s+(?:źr[oó]d[łl]o|zrodlo|source|title|tytu[łl])\s+(?:adres|url|link)\b[:\s-]*"
)
REFERENCE_REVIEW_LABEL_RE = re.compile(r"(?i)\b(?:Link requires manual review\.?|Unresolved URL:)\b")
REFERENCE_MULTI_ID_HEAD_RE = re.compile(
    r"(?P<ids>(?:\[\s*[A-Za-z0-9][A-Za-z0-9,\s-]{0,31}\s*\]\s+){2,})(?P<body>.+)$"
)
REFERENCE_WORD_RE = re.compile(r"[A-Za-zÀ-ÿĀ-ž0-9]{3,}")
REFERENCE_CITATION_RANGE_RE = re.compile(
    r"\[\s*(?P<prefix>[A-Za-z]{1,6}-?)(?P<start>\d+)\s*\]\s*[-–—]\s*\[\s*(?:(?P=prefix))?(?P<end>\d+)\s*\]",
    re.IGNORECASE,
)
REFERENCE_CITATION_GROUP_RE = re.compile(r"\[(?P<body>[^\[\]]{1,80})\]")
REFERENCE_CITATION_INLINE_RANGE_RE = re.compile(
    r"\b(?P<prefix>[A-Za-z]{1,6}-?)(?P<start>\d+)\s*[-–—]\s*(?:(?P=prefix))?(?P<end>\d+)\b",
    re.IGNORECASE,
)
REFERENCE_CITATION_TOKEN_RE = re.compile(r"\b(?P<id>[A-Z]{1,6}-?\d+[A-Z0-9-]*)\b", re.IGNORECASE)
REFERENCE_BANNED_OUTPUT_PATTERNS = [
    re.compile(r"Link requires manual review\.?", re.IGNORECASE),
    re.compile(r"Unresolved URL:", re.IGNORECASE),
    re.compile(r"(?i)\bID\s+Źródło\s+Adres\b"),
    re.compile(r"(?i)\bID\s+Zrodlo\s+Adres\b"),
    re.compile(r"https?://the(?:\b|[./])", re.IGNORECASE),
    re.compile(r"%2(?:\b|[^0-9A-Fa-f])"),
]
REFERENCE_PLACEHOLDER_SCOPE_RE = re.compile(
    r"(?i)\b(?:placeholder|reserved|coming soon|to be added|tbd|uzupe[łl]ni[će]|w przygotowaniu)\b"
)
REFERENCE_TOKEN_STOPWORDS = {
    "about",
    "adres",
    "analysis",
    "and",
    "article",
    "available",
    "business",
    "com",
    "content",
    "dam",
    "document",
    "documents",
    "draft",
    "email",
    "for",
    "guide",
    "help",
    "html",
    "http",
    "https",
    "legal",
    "link",
    "links",
    "merchant",
    "merchants",
    "official",
    "page",
    "pages",
    "pdf",
    "pl",
    "public",
    "publications",
    "regional",
    "report",
    "reports",
    "source",
    "sources",
    "support",
    "title",
    "the",
    "url",
    "www",
    "źródło",
    "zrodlo",
}


def repair_epub_reference_sections(
    epub_bytes: bytes,
    *,
    language_hint: str | None = None,
    source_pdf_path: str | None = None,
) -> ReferenceRepairResult:
    if not epub_bytes:
        return ReferenceRepairResult(
            epub_bytes=epub_bytes,
            summary={
                "documents_processed": 0,
                "documents_modified": 0,
                "sections_rebuilt": 0,
                "records_detected": 0,
                "records_reconstructed": 0,
                "records_flagged_for_review": 0,
                "urls_repaired": 0,
                "urls_unresolved": 0,
                "numbering_issues_fixed": 0,
                "visible_junk_detected": 0,
                "quality_gate_status": "unavailable",
                "epubcheck_status": "unavailable",
                "entries_rebuilt": 0,
                "review_entry_count": 0,
                "unresolved_fragment_count": 0,
                "clickable_link_count": 0,
                "repaired_link_count": 0,
                "scope_replaced_count": 0,
                "citation_coverage": [],
                "citations_detected": 0,
                "citations_covered": 0,
                "citations_missing_record": 0,
                "citations_ambiguous": 0,
                "unused_reference_records": [],
                "unused_reference_record_count": 0,
                "empty_reference_sections_detected": 0,
                "reference_quality_gate_status": "unavailable",
            },
            citation_coverage=[],
            epubcheck={"status": "unavailable", "tool": "epubcheck", "messages": []},
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        chapter_paths = _get_spine_xhtml_paths(opf_path)
        citation_scan = _collect_citation_usage(chapter_paths, root_dir=root_dir)
        source_record_map = _extract_source_pdf_reference_records(source_pdf_path)

        records: list[ReferenceRepairRecord] = []
        sections: list[dict[str, Any]] = []
        before_after: list[dict[str, str]] = []
        modified_documents = 0

        for chapter_path in chapter_paths:
            repair = _repair_reference_document(
                chapter_path,
                root_dir=root_dir,
                language_hint=language_hint,
                citation_order_map=citation_scan["citation_order_map"],
                source_record_map=source_record_map,
            )
            if repair.modified:
                modified_documents += 1
            records.extend(repair.records)
            sections.extend(repair.sections)
            before_after.extend(repair.before_after)

        repaired_epub = _pack_epub(root_dir)
        epubcheck = run_epubcheck(repaired_epub)
        citation_coverage = _build_citation_coverage(citation_scan=citation_scan, records=records)
        summary = _build_reference_summary(
            chapter_count=len(chapter_paths),
            modified_documents=modified_documents,
            records=records,
            sections=sections,
            citation_scan=citation_scan,
            citation_coverage=citation_coverage,
            epubcheck=epubcheck,
        )
        return ReferenceRepairResult(
            epub_bytes=repaired_epub,
            summary=summary,
            records=records,
            sections=sections,
            citation_coverage=citation_coverage,
            before_after=before_after,
            epubcheck=epubcheck,
            markdown_summary=_build_summary_markdown(summary, sections=sections, citation_coverage=citation_coverage),
            before_after_markdown=_build_before_after_markdown(before_after),
        )


def run_reference_repair_pipeline(
    source_epub: Path,
    *,
    output_dir: Path,
    reports_dir: Path,
    language_hint: str | None = None,
    source_pdf_path: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    result = repair_epub_reference_sections(
        source_epub.read_bytes(),
        language_hint=language_hint,
        source_pdf_path=source_pdf_path,
    )

    repaired_path = output_dir / "repaired.epub"
    repaired_path.write_bytes(result.epub_bytes)
    (reports_dir / "reference_repair_report.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "reference_repair_summary.md").write_text(result.markdown_summary, encoding="utf-8")
    (reports_dir / "reference_before_after.md").write_text(result.before_after_markdown, encoding="utf-8")
    return {
        "output_epub": str(repaired_path),
        "report_json": str(reports_dir / "reference_repair_report.json"),
        "report_markdown": str(reports_dir / "reference_repair_summary.md"),
        "before_after_markdown": str(reports_dir / "reference_before_after.md"),
        "summary": result.summary,
    }


def _repair_reference_document(
    chapter_path: Path,
    *,
    root_dir: Path,
    language_hint: str | None,
    citation_order_map: dict[str, int],
    source_record_map: dict[str, ReferenceRepairRecord],
) -> _DocumentReferenceRepair:
    original = chapter_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(original, "xml")
    body = soup.find("body")
    if body is None:
        return _DocumentReferenceRepair(modified=False, document_path=_safe_rel(chapter_path, root_dir))

    container = _resolve_reference_container(body)
    child_nodes = [node for node in container.children if isinstance(node, Tag)]
    if not child_nodes:
        return _DocumentReferenceRepair(modified=False, document_path=_safe_rel(chapter_path, root_dir))

    chapter_title = soup.find("title").get_text(" ", strip=True) if soup.find("title") else ""
    scopes = _find_reference_scopes(child_nodes, chapter_title=chapter_title)
    if not scopes:
        return _DocumentReferenceRepair(modified=False, document_path=_safe_rel(chapter_path, root_dir))

    document_path = _safe_rel(chapter_path, root_dir)
    records: list[ReferenceRepairRecord] = []
    sections: list[dict[str, Any]] = []
    before_after: list[dict[str, str]] = []
    modified = False

    for scope_index, (start, end, scope_kind) in enumerate(reversed(scopes), start=1):
        scope_nodes = child_nodes[start:end]
        if not scope_nodes:
            continue
        before_html = "".join(str(node) for node in scope_nodes)
        section_title = _normalize_text(scope_nodes[0].get_text(" ", strip=True)) if scope_kind == "explicit" else ""
        section_id = _derive_reference_section_id(scope_nodes[0], fallback_index=len(scopes) - scope_index + 1)
        fragment_html, detail_records, section_summary = _repair_reference_scope(
            scope_nodes,
            document_path=document_path,
            section_id=section_id,
            section_title=section_title,
            language_hint=language_hint,
            citation_order_map=citation_order_map,
            source_record_map=source_record_map,
        )
        if detail_records:
            records.extend(detail_records)
        if section_summary:
            sections.append(section_summary)
        if not fragment_html:
            continue

        replacement_soup = BeautifulSoup(
            f'<wrapper xmlns:epub="http://www.idpf.org/2007/ops">{fragment_html}</wrapper>',
            "xml",
        )
        wrapper = replacement_soup.find("wrapper")
        if wrapper is None:
            continue
        replacement_nodes = [node.extract() for node in list(wrapper.children) if isinstance(node, Tag)]
        if not replacement_nodes:
            continue
        for replacement in replacement_nodes:
            scope_nodes[0].insert_before(replacement)
        for node in scope_nodes:
            node.extract()
        modified = True
        before_after.append(
            {
                "document_path": document_path,
                "section_id": section_id,
                "before": before_html,
                "after": fragment_html,
            }
        )

    if not modified:
        return _DocumentReferenceRepair(
            modified=False,
            document_path=document_path,
            records=records,
            sections=sections,
            before_after=before_after,
        )

    _dedupe_dom_ids(body)
    chapter_path.write_text(str(soup), encoding="utf-8")
    return _DocumentReferenceRepair(
        modified=True,
        document_path=document_path,
        records=records,
        sections=sections,
        before_after=before_after,
    )


def _extract_source_pdf_reference_records(source_pdf_path: str | None) -> dict[str, ReferenceRepairRecord]:
    if not source_pdf_path:
        return {}

    resolved_path = Path(source_pdf_path).resolve()
    if not resolved_path.exists() or resolved_path.suffix.lower() != ".pdf":
        return {}

    try:
        import pdfplumber  # type: ignore
    except Exception:
        return {}

    source_records: dict[str, ReferenceRepairRecord] = {}
    try:
        with pdfplumber.open(str(resolved_path)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                page_rows = _extract_source_pdf_reference_rows_from_page(page)
                if not page_rows:
                    continue
                records = _build_source_pdf_reference_records_from_rows(
                    page_rows,
                    document_path=f"source-pdf:page-{page_number}",
                    section_id=f"source-pdf-page-{page_number}",
                )
                for record in records:
                    canonical_ref_id = _canonical_reference_id(record.display_ref_id or record.ref_id)
                    if not canonical_ref_id or not record.links or not record.source_title:
                        continue
                    existing = source_records.get(canonical_ref_id)
                    if existing is None or _reference_record_priority(record) > _reference_record_priority(existing):
                        source_records[canonical_ref_id] = record
    except Exception:
        return {}
    return source_records


def _extract_source_pdf_reference_rows_from_page(page: Any) -> list[dict[str, Any]]:
    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    except Exception:
        return []
    if not words:
        return []

    grouped_rows: dict[int, list[dict[str, Any]]] = {}
    for word in words:
        try:
            top_value = float(word.get("top", 0.0) or 0.0)
        except Exception:
            continue
        if top_value < 40 or top_value > 790:
            continue
        row_key = int(round(top_value / 3.0) * 3)
        grouped_rows.setdefault(row_key, []).append(word)

    if not grouped_rows:
        return []

    sorted_rows = [(row_key, sorted(row_words, key=lambda item: float(item.get("x0", 0.0) or 0.0))) for row_key, row_words in sorted(grouped_rows.items())]
    header_info = _find_source_pdf_reference_table_header(sorted_rows)
    if header_info is None:
        return []

    header_index, id_boundary, source_boundary = header_info
    page_rows: list[dict[str, Any]] = []
    seen_reference_id = False
    for row_key, row_words in sorted_rows[header_index + 1 :]:
        row = _row_from_pdf_words(row_words, row_key=row_key, id_boundary=id_boundary, source_boundary=source_boundary)
        row_text = " ".join(part for part in [row["id"], row["src"], row["url"]] if part)
        normalized_text = _normalize_text(row_text)
        if not normalized_text:
            continue
        if "opracowanie szkoleniowe" in normalized_text.lower():
            break
        if seen_reference_id and not row["id"] and not row["url"] and not row["src"]:
            continue
        if _looks_like_reference_header_row(normalized_text):
            continue
        if row["src"].startswith("Ostatnia uwaga") or row["id"].startswith("Ostatnia uwaga"):
            break
        if row["id"] or row["src"] or row["url"]:
            page_rows.append(row)
            if row["id"]:
                seen_reference_id = True
    return page_rows if sum(1 for row in page_rows if row["id"]) >= 2 else []


def _find_source_pdf_reference_table_header(
    sorted_rows: list[tuple[int, list[dict[str, Any]]]]
) -> tuple[int, float, float] | None:
    for index, (_row_key, row_words) in enumerate(sorted_rows):
        id_anchor = None
        source_anchor = None
        url_anchor = None
        for word in row_words:
            text = _normalize_text(str(word.get("text", "") or ""))
            lowered = text.lower()
            if id_anchor is None and lowered in {"id", "lp", "nr", "no"}:
                id_anchor = float(word.get("x0", 0.0) or 0.0)
            elif source_anchor is None and lowered in {"źródło", "zrodlo", "source", "title"}:
                source_anchor = float(word.get("x0", 0.0) or 0.0)
            elif url_anchor is None and lowered in {"adres", "url", "link"}:
                url_anchor = float(word.get("x0", 0.0) or 0.0)
        if id_anchor is None or source_anchor is None or url_anchor is None:
            continue
        id_boundary = (id_anchor + source_anchor) / 2.0
        source_boundary = (source_anchor + url_anchor) / 2.0
        if id_boundary >= source_boundary:
            continue
        return index, id_boundary, source_boundary
    return None


def _row_from_pdf_words(
    row_words: list[dict[str, Any]],
    *,
    row_key: int,
    id_boundary: float,
    source_boundary: float,
) -> dict[str, Any]:
    id_parts: list[str] = []
    source_parts: list[str] = []
    url_parts: list[str] = []
    for word in row_words:
        text = _normalize_text(str(word.get("text", "") or ""))
        if not text:
            continue
        x0 = float(word.get("x0", 0.0) or 0.0)
        if x0 < id_boundary:
            id_parts.append(text)
        elif x0 < source_boundary:
            source_parts.append(text)
        else:
            url_parts.append(text)
    id_match = re.search(r"\[R\d+\]", " ".join(id_parts))
    return {
        "id": id_match.group(0) if id_match else "",
        "src": _normalize_text(" ".join(source_parts)),
        "url": _normalize_text(" ".join(url_parts)),
        "row": row_key,
    }


def _build_source_pdf_reference_records_from_rows(
    rows: list[dict[str, Any]],
    *,
    document_path: str,
    section_id: str,
) -> list[ReferenceRepairRecord]:
    id_rows = [row for row in rows if row.get("id")]
    if not id_rows:
        return []

    centers = [int(row.get("row", 0) or 0) for row in id_rows]
    boundaries: list[float] = [float("-inf")]
    for start, end in zip(centers, centers[1:]):
        boundaries.append((start + end) / 2.0)
    boundaries.append(float("inf"))

    records: list[ReferenceRepairRecord] = []
    for index, row in enumerate(id_rows):
        bucket = [
            candidate
            for candidate in rows
            if boundaries[index] < float(candidate.get("row", 0) or 0) <= boundaries[index + 1]
        ]
        record = _build_source_pdf_reference_record(
            row.get("id", ""),
            bucket,
            document_path=document_path,
            section_id=section_id,
        )
        if record is not None:
            records.append(record)
    return records


def _build_source_pdf_reference_record(
    ref_id: str,
    rows: list[dict[str, Any]],
    *,
    document_path: str,
    section_id: str,
) -> ReferenceRepairRecord | None:
    display_ref_id = _display_reference_id(ref_id)
    if not display_ref_id:
        return None

    source_lines = [_normalize_text(str(row.get("src", "") or "")) for row in rows if _normalize_text(str(row.get("src", "") or ""))]
    source_text = _normalize_text(" ".join(source_lines))
    if not source_text:
        return None

    descriptor = _build_descriptor_candidate(f"{display_ref_id} {source_text}", position=0)
    descriptor.source_id = display_ref_id
    descriptor.display_source_id = display_ref_id

    url_candidates = _build_source_pdf_url_candidates(rows)
    record = _record_from_descriptor(
        descriptor,
        url_candidates,
        document_path=document_path,
        section_id=section_id,
        force_review=not bool(url_candidates),
    )
    if url_candidates:
        record.confidence = max(record.confidence, 0.97)
        record.review_flag = False
        record.link_status = _classify_link_status(record.links[0] if record.links else "")
    return record


def _build_source_pdf_url_candidates(rows: list[dict[str, Any]]) -> list[_ReferenceUrlCandidate]:
    fragments = [_normalize_text(str(row.get("url", "") or "")) for row in rows if _normalize_text(str(row.get("url", "") or ""))]
    if not fragments:
        return []

    groups: list[list[str]] = []
    current_group: list[str] = []
    for fragment in fragments:
        if REFERENCE_URL_START_RE.search(fragment):
            if current_group:
                groups.append(current_group)
            current_group = [fragment]
        elif current_group:
            current_group.append(fragment)
    if current_group:
        groups.append(current_group)

    candidates: list[_ReferenceUrlCandidate] = []
    seen: set[str] = set()
    for position, group in enumerate(groups):
        raw = "".join(part.replace(" ", "") for part in group)
        normalized = _normalize_reference_href(raw)
        if not normalized or not normalized.lower().startswith(("http://", "https://")) or normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(_ReferenceUrlCandidate(raw=raw, normalized=normalized, position=position))
    return candidates


def _augment_scope_records_from_source_pdf(
    records: list[ReferenceRepairRecord],
    rendered_records: list[ReferenceRepairRecord],
    *,
    core_units: list[_ReferenceScopeUnit],
    source_record_map: dict[str, ReferenceRepairRecord],
    document_path: str,
    section_id: str,
) -> tuple[list[ReferenceRepairRecord], list[ReferenceRepairRecord]]:
    if not source_record_map:
        return records, rendered_records

    scope_ids = _scope_source_ids(core_units)
    if not scope_ids:
        return records, rendered_records

    existing_by_id: dict[str, ReferenceRepairRecord] = {}
    for record in records:
        canonical_ref_id = _canonical_reference_id(record.display_ref_id or record.ref_id)
        if not canonical_ref_id or canonical_ref_id not in scope_ids:
            continue
        previous = existing_by_id.get(canonical_ref_id)
        if previous is None or _reference_record_priority(record) > _reference_record_priority(previous):
            existing_by_id[canonical_ref_id] = record

    merged_records: list[ReferenceRepairRecord] = []
    merged_rendered: list[ReferenceRepairRecord] = []
    for canonical_ref_id in scope_ids:
        source_record = source_record_map.get(canonical_ref_id)
        chosen = _clone_reference_record(source_record, document_path=document_path, section_id=section_id) if source_record else existing_by_id.get(canonical_ref_id)
        if chosen is None:
            continue
        merged_records.append(chosen)
        if _should_render_record(chosen):
            merged_rendered.append(chosen)
    return merged_records or records, merged_rendered or rendered_records


def _scope_source_ids(core_units: list[_ReferenceScopeUnit]) -> list[str]:
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for unit in core_units:
        for raw_id in unit.source_ids:
            canonical_ref_id = _canonical_reference_id(raw_id)
            if not canonical_ref_id or canonical_ref_id in seen:
                continue
            seen.add(canonical_ref_id)
            ordered_ids.append(canonical_ref_id)
    return ordered_ids


def _clone_reference_record(
    record: ReferenceRepairRecord,
    *,
    document_path: str,
    section_id: str,
) -> ReferenceRepairRecord:
    return ReferenceRepairRecord(
        document_path=document_path,
        section_id=section_id,
        ref_id=record.ref_id,
        display_ref_id=record.display_ref_id,
        source_name=record.source_name,
        source_title=record.source_title,
        description=record.description,
        url=record.url,
        links=list(record.links),
        confidence=float(record.confidence or 0.0),
        review_flag=bool(record.review_flag),
        numbering_repaired=bool(record.numbering_repaired),
        link_status=record.link_status,
        original_fragments=list(record.original_fragments),
        unresolved_fragments=list(record.unresolved_fragments),
    )


def _reference_record_priority(record: ReferenceRepairRecord) -> tuple[int, float, int]:
    return (
        1 if _should_render_record(record) else 0,
        float(record.confidence or 0.0),
        len(record.links),
    )


def _resolve_reference_container(body: Tag) -> Tag:
    top_level = [node for node in body.children if isinstance(node, Tag)]
    if len(top_level) == 1 and top_level[0].name in {"section", "article"}:
        return top_level[0]
    return body


def _find_reference_scopes(nodes: list[Tag], *, chapter_title: str) -> list[tuple[int, int, str]]:
    scopes: list[tuple[int, int, str]] = []
    for index, node in enumerate(nodes):
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text or not _looks_like_reference_scope_title(text):
            continue
        level = _node_heading_level(node)
        end = len(nodes)
        for scan in range(index + 1, len(nodes)):
            candidate_level = _node_heading_level(nodes[scan])
            if candidate_level and candidate_level <= level and not _looks_like_reference_scope_title(
                _normalize_text(nodes[scan].get_text(" ", strip=True))
            ):
                end = scan
                break
        scopes.append((index, end, "explicit"))
    if scopes:
        return _dedupe_scopes(scopes)

    if _looks_like_reference_scope_title(chapter_title) and _looks_like_implicit_reference_scope(nodes):
        return [(0, len(nodes), "implicit")]
    if _looks_like_implicit_reference_scope(nodes):
        return [(0, len(nodes), "implicit")]
    return []
def _looks_like_reference_scope_title(text: str) -> bool:
    normalized = _normalize_text(text)
    if _looks_like_reference_section_title(normalized):
        return True
    folded = normalized.lower()
    markers = (
        "referencje",
        "referencje publiczne",
        "reference list",
        "public references",
        "lista źródeł",
        "lista zrodel",
    )
    return any(folded == marker or folded.startswith(f"{marker} ") or folded.endswith(f" {marker}") for marker in markers)


def _collect_citation_usage(chapter_paths: list[Path], *, root_dir: Path) -> dict[str, Any]:
    citation_usage: dict[str, dict[str, Any]] = {}
    empty_reference_sections: list[dict[str, Any]] = []

    for chapter_index, chapter_path in enumerate(chapter_paths):
        try:
            chapter_source = chapter_path.read_text(encoding="utf-8")
        except Exception:
            continue
        soup = BeautifulSoup(chapter_source, "xml")
        body = soup.find("body")
        if body is None:
            continue
        container = _resolve_reference_container(body)
        child_nodes = [node for node in container.children if isinstance(node, Tag)]
        if not child_nodes:
            continue

        document_path = _safe_rel(chapter_path, root_dir)
        chapter_title = soup.find("title").get_text(" ", strip=True) if soup.find("title") else ""
        scopes = _find_reference_scopes(child_nodes, chapter_title=chapter_title)
        excluded_indices: set[int] = set()
        for start, end, _scope_kind in scopes:
            excluded_indices.update(range(start, end))
            scope_nodes = child_nodes[start:end]
            if _scope_is_empty_or_placeholder(scope_nodes):
                section_id = _derive_reference_section_id(scope_nodes[0], fallback_index=start + 1) if scope_nodes else ""
                section_title = _normalize_text(scope_nodes[0].get_text(" ", strip=True)) if scope_nodes else ""
                empty_reference_sections.append(
                    {
                        "document_path": document_path,
                        "section_id": section_id,
                        "section_title": section_title,
                    }
                )

        for node_index, node in enumerate(child_nodes):
            if node_index in excluded_indices or node.name == "nav":
                continue
            text = _normalize_text(node.get_text(" ", strip=True))
            if not text:
                continue
            citation_ids = _extract_citation_ids_from_text(text)
            if not citation_ids:
                continue
            for match_index, ref_id in enumerate(citation_ids):
                canonical_ref_id = _canonical_reference_id(ref_id)
                if not canonical_ref_id:
                    continue
                entry = citation_usage.setdefault(
                    canonical_ref_id,
                    {
                        "ref_id": _display_reference_id(canonical_ref_id),
                        "canonical_ref_id": canonical_ref_id,
                        "use_count": 0,
                        "locations": [],
                        "_first_occurrence": (chapter_index, node_index, match_index),
                    },
                )
                entry["use_count"] += 1
                entry["locations"].append(
                    {
                        "document_path": document_path,
                        "node_index": node_index,
                        "context": _truncate_context(text),
                    }
                )
                first_occurrence = entry.get("_first_occurrence", (chapter_index, node_index, match_index))
                if (chapter_index, node_index, match_index) < first_occurrence:
                    entry["_first_occurrence"] = (chapter_index, node_index, match_index)

    citations = sorted(citation_usage.values(), key=lambda item: item.get("_first_occurrence", (10**6, 10**6, 10**6)))
    citation_order_map: dict[str, int] = {}
    for index, citation in enumerate(citations):
        citation_order_map[citation["canonical_ref_id"]] = index
        citation.pop("_first_occurrence", None)

    return {
        "citations": citations,
        "citation_order_map": citation_order_map,
        "empty_reference_sections": empty_reference_sections,
    }


def _scope_is_empty_or_placeholder(scope_nodes: list[Tag]) -> bool:
    units = _build_scope_units(scope_nodes)
    content_units = [
        unit
        for unit in units
        if unit.text and not (unit.node_name in {"h1", "h2", "h3", "h4", "h5", "h6"} and _looks_like_reference_scope_title(unit.text))
    ]
    if not content_units:
        return True
    if any(_unit_looks_like_referenceish(unit) for unit in content_units):
        return False
    combined = _normalize_text(" ".join(unit.text for unit in content_units if unit.text))
    if not combined:
        return True
    if REFERENCE_PLACEHOLDER_SCOPE_RE.search(combined):
        return True
    return len(combined.split()) <= 12


def _extract_citation_ids_from_text(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized or "[" not in normalized or "]" not in normalized:
        return []

    citation_ids: list[str] = []
    covered_spans: list[tuple[int, int]] = []
    seen: set[str] = set()

    for match in REFERENCE_CITATION_RANGE_RE.finditer(normalized):
        for ref_id in _expand_citation_range(match.group("prefix"), match.group("start"), match.group("end")):
            if ref_id not in seen:
                seen.add(ref_id)
                citation_ids.append(ref_id)
        covered_spans.append(match.span())

    for match in REFERENCE_CITATION_GROUP_RE.finditer(normalized):
        if _span_overlaps(match.span(), covered_spans):
            continue
        body = _normalize_text(match.group("body"))
        if not body:
            continue
        group_spans: list[tuple[int, int]] = []
        for range_match in REFERENCE_CITATION_INLINE_RANGE_RE.finditer(body):
            for ref_id in _expand_citation_range(range_match.group("prefix"), range_match.group("start"), range_match.group("end")):
                if ref_id not in seen:
                    seen.add(ref_id)
                    citation_ids.append(ref_id)
            group_spans.append(range_match.span())
        for token_match in REFERENCE_CITATION_TOKEN_RE.finditer(body):
            if _span_overlaps(token_match.span(), group_spans):
                continue
            ref_id = _display_reference_id(_canonical_reference_id(token_match.group("id")))
            if ref_id and ref_id not in seen:
                seen.add(ref_id)
                citation_ids.append(ref_id)
    return citation_ids


def _expand_citation_range(prefix: str, start_value: str, end_value: str) -> list[str]:
    try:
        start = int(start_value)
        end = int(end_value)
    except Exception:
        return []
    if start <= 0 or end <= 0 or end < start or end - start > 64:
        return []
    normalized_prefix = _normalize_text(prefix).upper()
    return [f"[{normalized_prefix}{value}]" for value in range(start, end + 1)]


def _span_overlaps(span: tuple[int, int], covered_spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < covered_end and end > covered_start for covered_start, covered_end in covered_spans)


def _canonical_reference_id(ref_id: str) -> str:
    normalized = _normalize_text(ref_id)
    if not normalized:
        return ""
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.rstrip(".")
    if normalized.endswith(")"):
        normalized = normalized[:-1]
    return normalized.upper()


def _display_reference_id(ref_id: str) -> str:
    canonical_ref_id = _canonical_reference_id(ref_id)
    if not canonical_ref_id:
        return ""
    return f"[{canonical_ref_id}]"


def _truncate_context(text: str, *, limit: int = 180) -> str:
    normalized = _normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _sort_records_by_citation_order(
    records: list[ReferenceRepairRecord],
    *,
    citation_order_map: dict[str, int],
) -> list[ReferenceRepairRecord]:
    if not records or not citation_order_map:
        return list(records)
    decorated = list(enumerate(records))
    default_rank = len(citation_order_map) + len(records) + 1
    decorated.sort(
        key=lambda item: (
            citation_order_map.get(_canonical_reference_id(item[1].display_ref_id or item[1].ref_id), default_rank + item[0]),
            item[0],
        )
    )
    return [record for _index, record in decorated]


def _build_citation_coverage(
    *,
    citation_scan: dict[str, Any],
    records: list[ReferenceRepairRecord],
) -> list[dict[str, Any]]:
    record_buckets: dict[str, list[ReferenceRepairRecord]] = {}
    for record in records:
        canonical_ref_id = _canonical_reference_id(record.display_ref_id or record.ref_id)
        if not canonical_ref_id:
            continue
        record_buckets.setdefault(canonical_ref_id, []).append(record)

    coverage: list[dict[str, Any]] = []
    cited_ids: set[str] = set()
    for citation in citation_scan.get("citations", []):
        canonical_ref_id = citation.get("canonical_ref_id", "")
        cited_ids.add(canonical_ref_id)
        matching_records = record_buckets.get(canonical_ref_id, [])
        rendered_records = [record for record in matching_records if _should_render_record(record)]
        max_confidence = max((float(record.confidence or 0.0) for record in matching_records), default=0.0)
        if not matching_records:
            status = "missing_record"
        elif len(matching_records) > 1 or len(rendered_records) != 1 or rendered_records[0].review_flag:
            status = "ambiguous_record"
        else:
            status = "covered"
        coverage.append(
            {
                "ref_id": citation.get("ref_id") or _display_reference_id(canonical_ref_id),
                "canonical_ref_id": canonical_ref_id,
                "use_count": int(citation.get("use_count", 0) or 0),
                "locations": list(citation.get("locations", [])),
                "status": status,
                "record_exists": bool(matching_records),
                "confidence": round(max_confidence, 3),
            }
        )

    for canonical_ref_id, matching_records in record_buckets.items():
        if canonical_ref_id in cited_ids:
            continue
        max_confidence = max((float(record.confidence or 0.0) for record in matching_records), default=0.0)
        best_record = next((record for record in matching_records if _should_render_record(record)), matching_records[0])
        coverage.append(
            {
                "ref_id": best_record.display_ref_id or best_record.ref_id or _display_reference_id(canonical_ref_id),
                "canonical_ref_id": canonical_ref_id,
                "use_count": 0,
                "locations": [],
                "status": "unused_record",
                "record_exists": True,
                "confidence": round(max_confidence, 3),
            }
        )

    order_map = citation_scan.get("citation_order_map", {})
    default_rank = len(order_map) + len(coverage) + 1
    coverage.sort(
        key=lambda item: (
            order_map.get(item.get("canonical_ref_id", ""), default_rank),
            item.get("use_count", 0) == 0,
            item.get("ref_id", ""),
        )
    )
    return coverage


def _looks_like_implicit_reference_scope(nodes: list[Tag]) -> bool:
    units = _build_scope_units(nodes)
    if len(units) < 3:
        return False
    referenceish = [unit for unit in units if _unit_looks_like_referenceish(unit)]
    url_units = [unit for unit in units if unit.url_candidates or unit.unresolved_fragments]
    return len(referenceish) >= 3 and len(url_units) >= 2


def _dedupe_scopes(scopes: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    deduped: list[tuple[int, int, str]] = []
    previous_end = -1
    for start, end, kind in sorted(scopes):
        if start < previous_end:
            continue
        deduped.append((start, end, kind))
        previous_end = end
    return deduped


def _node_heading_level(node: Tag) -> int:
    if node.name and len(node.name) == 2 and node.name[0].lower() == "h" and node.name[1].isdigit():
        return max(1, min(int(node.name[1]), 6))
    text = _normalize_text(node.get_text(" ", strip=True))
    if node.name in {"p", "div", "span"} and _looks_like_reference_scope_title(text):
        return 2
    return 0


def _repair_reference_scope(
    scope_nodes: list[Tag],
    *,
    document_path: str,
    section_id: str,
    section_title: str,
    language_hint: str | None,
    citation_order_map: dict[str, int],
    source_record_map: dict[str, ReferenceRepairRecord],
) -> tuple[str, list[ReferenceRepairRecord], dict[str, Any]]:
    units = _build_scope_units(scope_nodes)
    if not units:
        return "", [], {}

    heading_unit = next(
        (
            unit
            for unit in units
            if unit.node_name in {"h1", "h2", "h3", "h4", "h5", "h6"}
            and _looks_like_reference_scope_title(unit.text)
        ),
        None,
    )
    if not section_title and heading_unit is not None:
        section_title = heading_unit.text
    language = (language_hint or "").lower().strip()
    if not section_title:
        section_title = "Źródła" if language.startswith("pl") else "References"

    referenceish_indices = [index for index, unit in enumerate(units) if _unit_looks_like_referenceish(unit)]
    if not referenceish_indices:
        return "", [], {}

    first_reference_index = referenceish_indices[0]
    last_reference_index = referenceish_indices[-1]
    while last_reference_index >= first_reference_index and _looks_like_narrative_tail(units[last_reference_index]):
        last_reference_index -= 1
    if last_reference_index < first_reference_index:
        return "", [], {}

    prefix_units = units[:first_reference_index]
    core_units = units[first_reference_index : last_reference_index + 1]
    suffix_units = units[last_reference_index + 1 :]

    split_record_count = 0
    records: list[ReferenceRepairRecord] = []
    rendered_records: list[ReferenceRepairRecord] = []
    cluster_units: list[_ReferenceScopeUnit] = []

    def flush_cluster() -> None:
        nonlocal split_record_count
        if not cluster_units:
            return
        cluster_records, cluster_rendered, cluster_split_count = _resolve_reference_cluster(
            cluster_units,
            document_path=document_path,
            section_id=section_id,
        )
        split_record_count += cluster_split_count
        records.extend(cluster_records)
        rendered_records.extend(cluster_rendered)
        cluster_units.clear()

    for unit in core_units:
        direct_record = _build_direct_record_from_unit(
            unit,
            document_path=document_path,
            section_id=section_id,
        )
        if direct_record is not None:
            flush_cluster()
            records.append(direct_record)
            if _should_render_record(direct_record):
                rendered_records.append(direct_record)
            continue
        cluster_units.append(unit)
    flush_cluster()
    records, rendered_records = _augment_scope_records_from_source_pdf(
        records,
        rendered_records,
        core_units=core_units,
        source_record_map=source_record_map,
        document_path=document_path,
        section_id=section_id,
    )

    rendered_records = _sort_records_by_citation_order(rendered_records, citation_order_map=citation_order_map)
    numbering_issues_fixed = _apply_conservative_numbering(rendered_records)
    fragment_html, visible_junk_detected = _render_reference_scope_html(
        rendered_records,
        prefix_units=prefix_units,
        suffix_units=suffix_units,
        section_id=section_id,
        section_title=section_title,
        heading_level=_node_heading_level(scope_nodes[0]) if scope_nodes else 1,
        language_hint=language_hint,
    )

    clickable_link_count = sum(len(record.links) for record in rendered_records)
    review_entry_count = sum(1 for record in records if record.review_flag)
    unresolved_fragment_count = sum(len(record.unresolved_fragments) for record in records)
    repaired_link_count = sum(
        1
        for record in records
        for fragment in record.original_fragments
        if fragment.startswith("__repaired_url__:")
    )
    report = {
        "sections_detected": 1,
        "records_detected": len(records),
        "entries_rebuilt": len(rendered_records),
        "records_clean": sum(1 for record in rendered_records if not record.review_flag),
        "records_review_only": sum(1 for record in records if record.review_flag and not _should_render_record(record)),
        "split_record_count": split_record_count,
        "clickable_link_count": clickable_link_count,
        "repaired_link_count": repaired_link_count,
        "review_entry_count": review_entry_count,
        "unresolved_fragment_count": unresolved_fragment_count,
        "numbering_issue_count": numbering_issues_fixed,
        "scope_replaced_count": 1 if fragment_html else 0,
        "visible_junk_detected": visible_junk_detected,
        "quality_gate_status": "failed" if visible_junk_detected else "passed",
    }
    section_summary = {
        "document_path": document_path,
        "section_id": section_id,
        "section_title": section_title,
        "report": report,
        "record_count": len(records),
        "rendered_record_count": len(rendered_records),
    }
    return fragment_html, records, section_summary


def _build_scope_units(scope_nodes: list[Tag]) -> list[_ReferenceScopeUnit]:
    units: list[_ReferenceScopeUnit] = []
    position = 0
    for node in scope_nodes:
        if node.name in {"ul", "ol"}:
            for item in node.find_all("li", recursive=False):
                unit = _build_scope_unit(item, position=position)
                if unit is not None:
                    units.append(unit)
                    position += 1
            continue
        if node.name == "table":
            for row in node.find_all("tr"):
                unit = _build_scope_unit(row, position=position)
                if unit is not None:
                    units.append(unit)
                    position += 1
            continue
        unit = _build_scope_unit(node, position=position)
        if unit is not None:
            units.append(unit)
            position += 1
    return units


def _build_scope_unit(node: Tag, *, position: int) -> _ReferenceScopeUnit | None:
    text = _node_reference_text(node)
    html_fragment = str(node)
    if not text and not html_fragment:
        return None
    descriptor_text = _cleanup_reference_descriptor_text(_strip_reference_link_candidates(text or ""))
    url_candidates = _extract_url_candidates(text or "", position=position)
    unresolved_fragments = _extract_unresolved_fragments(text or "", url_candidates=url_candidates)
    return _ReferenceScopeUnit(
        position=position,
        node_name=(node.name or "").lower(),
        html=html_fragment,
        text=text,
        descriptor_text=descriptor_text,
        source_ids=_extract_inline_reference_ids(descriptor_text or text),
        url_candidates=url_candidates,
        unresolved_fragments=unresolved_fragments,
    )


def _node_reference_text(node: Tag) -> str:
    wrapper = BeautifulSoup(str(node), "xml")
    root = wrapper.find(node.name) or wrapper.find(True)
    if root is None:
        return ""
    for line_break in root.find_all("br"):
        line_break.replace_with("\n")
    for anchor in root.find_all("a"):
        href = _normalize_text(anchor.get("href", ""))
        anchor_text = _normalize_text(anchor.get_text(" ", strip=True))
        parts = [part for part in [anchor_text, href] if part]
        anchor.replace_with(" ".join(dict.fromkeys(parts)))
    return _normalize_text(root.get_text("\n", strip=True))


def _cleanup_reference_descriptor_text(text: str) -> str:
    normalized = REFERENCE_REVIEW_LABEL_RE.sub(" ", text or "")
    return _normalize_text(normalized).strip(" ;,:-")


def _strip_reference_header_row_prefix(text: str) -> tuple[str, bool]:
    normalized = _cleanup_reference_descriptor_text(text)
    match = REFERENCE_HEADER_ROW_RE.match(normalized)
    if not match:
        return normalized, False
    return normalized[match.end() :].strip(" ;,:-"), True


def _looks_like_reference_header_row(text: str) -> bool:
    return bool(REFERENCE_HEADER_ROW_RE.match(_cleanup_reference_descriptor_text(text)))
def _extract_inline_reference_ids(text: str) -> list[str]:
    normalized = _cleanup_reference_descriptor_text(text)
    ids: list[str] = []
    seen: set[str] = set()
    for match in REFERENCE_INLINE_ID_RE.finditer(normalized):
        value = _normalize_text(match.group("id"))
        if value and value not in seen:
            seen.add(value)
            ids.append(value)
    if not ids:
        match = REFERENCE_ENTRY_ID_RE.match(normalized)
        if match:
            value = _normalize_text(match.group("id"))
            if value:
                ids.append(value)
    return ids


def _extract_url_candidates(text: str, *, position: int) -> list[_ReferenceUrlCandidate]:
    candidates: list[_ReferenceUrlCandidate] = []
    seen: set[str] = set()
    for candidate in _collect_reference_link_candidates(text):
        raw = _normalize_text(str(candidate.get("raw", "") or ""))
        normalized = _normalize_text(str(candidate.get("normalized", "") or ""))
        key = normalized or raw
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(_ReferenceUrlCandidate(raw=raw, normalized=normalized, position=position))
    return candidates


def _extract_unresolved_fragments(text: str, *, url_candidates: list[_ReferenceUrlCandidate]) -> list[str]:
    fragments: list[str] = []
    seen = {candidate.raw for candidate in url_candidates if candidate.normalized}
    for candidate in _collect_reference_link_candidates(text):
        raw = _normalize_text(str(candidate.get("raw", "") or ""))
        normalized = _normalize_text(str(candidate.get("normalized", "") or ""))
        if not raw or normalized or raw in seen:
            continue
        seen.add(raw)
        fragments.append(raw)
    return fragments


def _unit_looks_like_referenceish(unit: _ReferenceScopeUnit) -> bool:
    if unit.node_name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        return False
    if unit.url_candidates or unit.unresolved_fragments or unit.source_ids:
        return True
    if _looks_like_reference_header_row(unit.text):
        return True
    return _looks_like_descriptor_text(unit.descriptor_text)


def _looks_like_descriptor_text(text: str) -> bool:
    normalized = _cleanup_reference_descriptor_text(text)
    if not normalized or len(normalized.split()) > 28:
        return False
    if _looks_like_reference_header_row(normalized):
        return True
    if len(normalized.split()) < 2:
        return False
    if " - " in normalized or " / " in normalized or ": " in normalized:
        return True
    if normalized.startswith("[") or bool(re.match(r"^[A-Z]{1,6}-?\d+", normalized)):
        return True
    return len(normalized.split()) <= 12 and normalized[0].isupper()


def _looks_like_narrative_tail(unit: _ReferenceScopeUnit) -> bool:
    if unit.url_candidates or unit.unresolved_fragments or unit.source_ids:
        return False
    text = unit.descriptor_text or unit.text
    if not text:
        return True
    if _looks_like_reference_header_row(text):
        return False
    word_count = len(text.split())
    if word_count <= 18 and (" - " in text or " / " in text or ": " in text):
        return False
    return word_count >= 14 and text.endswith(".")


def _build_direct_record_from_unit(
    unit: _ReferenceScopeUnit,
    *,
    document_path: str,
    section_id: str,
) -> ReferenceRepairRecord | None:
    if not unit.url_candidates:
        return None
    if not unit.descriptor_text:
        return None
    if len(unit.url_candidates) > 1 and (len(unit.source_ids) != 1 or not _looks_like_descriptor_text(unit.descriptor_text)):
        return None
    descriptors, _ = _descriptor_candidates_from_text(unit.descriptor_text or unit.text, position=unit.position)
    if len(descriptors) != 1:
        return None
    descriptor = descriptors[0]
    if "multi_id_contamination" in descriptor.flags or not _descriptor_title_has_signal(descriptor):
        return None
    record = _record_from_descriptor(
        descriptor,
        unit.url_candidates,
        document_path=document_path,
        section_id=section_id,
        force_review=bool(unit.unresolved_fragments),
    )
    return record if record.links else None


def _descriptor_candidates_from_text(text: str, *, position: int) -> tuple[list[_ReferenceDescriptorCandidate], int]:
    cleaned = _cleanup_reference_descriptor_text(text)
    if not cleaned:
        return [], 0
    cleaned, had_header_prefix = _strip_reference_header_row_prefix(cleaned)
    if not cleaned:
        return [], 0

    if REFERENCE_MULTI_ID_HEAD_RE.match(cleaned):
        candidate = _build_descriptor_candidate(cleaned, position=position)
        candidate.flags.add("multi_id_contamination")
        if had_header_prefix:
            candidate.flags.add("header_bleed")
        return [candidate], 0

    split_candidates = _split_reference_entries_from_text(cleaned)
    if len(split_candidates) >= 2:
        descriptors: list[_ReferenceDescriptorCandidate] = []
        for candidate_text in split_candidates:
            descriptor = _build_descriptor_candidate(candidate_text, position=position)
            if not _descriptor_has_content(descriptor):
                descriptors = []
                break
            descriptors.append(descriptor)
        if descriptors:
            if had_header_prefix:
                descriptors[0].flags.add("header_bleed")
            return descriptors, max(0, len(descriptors) - 1)

    descriptor = _build_descriptor_candidate(cleaned, position=position)
    if had_header_prefix:
        descriptor.flags.add("header_bleed")
    return [descriptor], 0


def _build_descriptor_candidate(text: str, *, position: int) -> _ReferenceDescriptorCandidate:
    cleaned = _cleanup_reference_descriptor_text(text)
    source_id = ""
    body = cleaned
    match = REFERENCE_ENTRY_ID_RE.match(cleaned)
    if match:
        source_id = _normalize_text(match.group("id"))
        body = _normalize_text(match.group("body"))
    title, description = _split_reference_title_and_description(body)
    title = _cleanup_reference_descriptor_text(title)
    description = _cleanup_reference_descriptor_text(description)
    source_name, source_title = _infer_reference_name_fields(title)
    descriptor = _ReferenceDescriptorCandidate(
        source_id=source_id,
        display_source_id=source_id,
        source_name=_normalize_text(source_name),
        source_title=_normalize_text(source_title or title),
        description=_normalize_text(description),
        position=position,
        original_text=_normalize_text(text),
        original_fragments=[_normalize_text(text)] if _normalize_text(text) else [],
    )
    if _looks_like_invalid_reference_title(descriptor.source_title) or not descriptor.source_title:
        descriptor.flags.add("invalid_title")
    if _looks_like_reference_header_row(text):
        descriptor.flags.add("header_bleed")
    return descriptor


def _descriptor_has_content(descriptor: _ReferenceDescriptorCandidate) -> bool:
    return bool(descriptor.source_title or descriptor.description or descriptor.source_id)


def _descriptor_title_has_signal(descriptor: _ReferenceDescriptorCandidate) -> bool:
    if "invalid_title" in descriptor.flags:
        return False
    title = descriptor.source_title
    if not title or len(title) < 3:
        return False
    return bool(_reference_tokens(title)) or len(title.split()) >= 2


def _resolve_reference_cluster(
    units: list[_ReferenceScopeUnit],
    *,
    document_path: str,
    section_id: str,
) -> tuple[list[ReferenceRepairRecord], list[ReferenceRepairRecord], int]:
    descriptors: list[_ReferenceDescriptorCandidate] = []
    url_candidates: list[_ReferenceUrlCandidate] = []
    split_record_count = 0

    for unit in units:
        candidate_text = unit.descriptor_text if unit.descriptor_text else ("" if unit.url_candidates else unit.text)
        unit_descriptors, unit_split_count = _descriptor_candidates_from_text(candidate_text, position=unit.position)
        split_record_count += unit_split_count
        descriptors.extend(candidate for candidate in unit_descriptors if _descriptor_has_content(candidate))
        url_candidates.extend(unit.url_candidates)

    if not descriptors and not url_candidates:
        return [], [], split_record_count

    records: list[ReferenceRepairRecord] = []
    rendered: list[ReferenceRepairRecord] = []

    if descriptors and url_candidates:
        assignments = _assign_urls_to_descriptors(descriptors, url_candidates)
        assigned_urls = {id(url_candidate) for bucket in assignments.values() for url_candidate in bucket}
        for descriptor in descriptors:
            assigned = assignments.get(id(descriptor), [])
            record = _record_from_descriptor(
                descriptor,
                assigned,
                document_path=document_path,
                section_id=section_id,
                force_review=not assigned,
            )
            records.append(record)
            if _should_render_record(record):
                rendered.append(record)
        for url_candidate in url_candidates:
            if id(url_candidate) in assigned_urls:
                continue
            records.append(
                _review_only_record(
                    document_path=document_path,
                    section_id=section_id,
                    original_fragments=[url_candidate.raw],
                    unresolved_fragments=[url_candidate.raw],
                )
            )
    elif descriptors:
        for descriptor in descriptors:
            records.append(
                _record_from_descriptor(
                    descriptor,
                    [],
                    document_path=document_path,
                    section_id=section_id,
                    force_review=True,
                )
            )
    else:
        for url_candidate in url_candidates:
            records.append(
                _review_only_record(
                    document_path=document_path,
                    section_id=section_id,
                    original_fragments=[url_candidate.raw],
                    unresolved_fragments=[url_candidate.raw],
                )
            )

    return records, rendered, split_record_count


def _assign_urls_to_descriptors(
    descriptors: list[_ReferenceDescriptorCandidate],
    url_candidates: list[_ReferenceUrlCandidate],
) -> dict[int, list[_ReferenceUrlCandidate]]:
    assignments: dict[int, list[_ReferenceUrlCandidate]] = {id(descriptor): [] for descriptor in descriptors}
    if not descriptors or not url_candidates:
        return assignments
    if len(descriptors) == 1:
        assignments[id(descriptors[0])] = list(url_candidates)
        return assignments

    scored_pairs: list[tuple[float, int, int]] = []
    for descriptor_index, descriptor in enumerate(descriptors):
        for url_index, url_candidate in enumerate(url_candidates):
            scored_pairs.append((_descriptor_url_score(descriptor, url_candidate), descriptor_index, url_index))

    used_descriptors: set[int] = set()
    used_urls: set[int] = set()
    for score, descriptor_index, url_index in sorted(scored_pairs, reverse=True):
        if score < 0.32 or descriptor_index in used_descriptors or url_index in used_urls:
            continue
        descriptor = descriptors[descriptor_index]
        assignments[id(descriptor)].append(url_candidates[url_index])
        used_descriptors.add(descriptor_index)
        used_urls.add(url_index)

    for url_index, url_candidate in enumerate(url_candidates):
        if url_index in used_urls:
            continue
        best_unassigned_index = None
        best_unassigned_score = 0.0
        best_any_index = None
        best_any_score = 0.0
        for descriptor_index, descriptor in enumerate(descriptors):
            score = _descriptor_url_score(descriptor, url_candidate)
            if not assignments[id(descriptor)] and score > best_unassigned_score:
                best_unassigned_score = score
                best_unassigned_index = descriptor_index
            if score > best_any_score:
                best_any_score = score
                best_any_index = descriptor_index
        if best_unassigned_index is not None and best_unassigned_score >= 0.22:
            assignments[id(descriptors[best_unassigned_index])].append(url_candidate)
            used_urls.add(url_index)
            continue
        if best_any_index is not None and best_any_score >= 0.3:
            assignments[id(descriptors[best_any_index])].append(url_candidate)
            used_urls.add(url_index)
    return assignments


def _descriptor_url_score(descriptor: _ReferenceDescriptorCandidate, url_candidate: _ReferenceUrlCandidate) -> float:
    descriptor_tokens = _reference_tokens(" ".join(part for part in [descriptor.source_title, descriptor.description, descriptor.source_id] if part))
    url_tokens = _reference_tokens(_normalize_text(url_candidate.normalized or url_candidate.raw))
    overlap = len(descriptor_tokens & url_tokens)
    score = 0.0
    if overlap:
        score += min(0.55, 0.18 * overlap)
    if descriptor_tokens:
        score += min(0.18, 0.02 * len(descriptor_tokens))
    distance = abs(descriptor.position - url_candidate.position)
    if distance == 0:
        score += 0.2
    elif distance == 1:
        score += 0.16
    elif distance == 2:
        score += 0.1
    elif distance == 3:
        score += 0.05
    if "multi_id_contamination" in descriptor.flags:
        score -= 0.35
    if "invalid_title" in descriptor.flags:
        score -= 0.15
    if "header_bleed" in descriptor.flags:
        score -= 0.1
    return max(0.0, min(score, 0.99))


def _reference_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in REFERENCE_WORD_RE.findall((text or "").lower()):
        if token in REFERENCE_TOKEN_STOPWORDS or len(token) <= 2:
            continue
        tokens.add(token)
    return tokens


def _record_from_descriptor(
    descriptor: _ReferenceDescriptorCandidate,
    url_candidates: list[_ReferenceUrlCandidate],
    *,
    document_path: str,
    section_id: str,
    force_review: bool,
) -> ReferenceRepairRecord:
    links = [candidate.normalized for candidate in url_candidates if candidate.normalized]
    original_fragments = list(descriptor.original_fragments)
    for candidate in url_candidates:
        if candidate.repaired:
            original_fragments.append(f"__repaired_url__:{candidate.raw}->{candidate.normalized}")
    unresolved_fragments = [candidate.raw for candidate in url_candidates if not candidate.normalized]
    avg_score = (
        sum(_descriptor_url_score(descriptor, candidate) for candidate in url_candidates) / max(len(url_candidates), 1)
        if url_candidates
        else 0.0
    )
    confidence = 0.15
    if descriptor.source_title:
        confidence += 0.22
    if descriptor.source_id:
        confidence += 0.08
    if links:
        confidence += 0.3
    confidence += min(0.24, avg_score)
    if descriptor.description:
        confidence += 0.05
    if "multi_id_contamination" in descriptor.flags:
        confidence -= 0.35
    if "header_bleed" in descriptor.flags:
        confidence -= 0.12
    if "invalid_title" in descriptor.flags:
        confidence -= 0.2
    if unresolved_fragments:
        confidence -= min(0.2, 0.08 * len(unresolved_fragments))
    confidence = max(0.0, min(round(confidence, 3), 0.99))
    review_flag = force_review or confidence < 0.85 or bool(unresolved_fragments) or "multi_id_contamination" in descriptor.flags
    return ReferenceRepairRecord(
        document_path=document_path,
        section_id=section_id,
        ref_id=descriptor.source_id,
        display_ref_id=descriptor.display_source_id,
        source_name=descriptor.source_name,
        source_title=descriptor.source_title,
        description=descriptor.description,
        url=links[0] if links else "",
        links=links,
        confidence=confidence,
        review_flag=review_flag,
        numbering_repaired=False,
        link_status=_classify_link_status(links[0] if links else ""),
        original_fragments=original_fragments,
        unresolved_fragments=unresolved_fragments,
    )


def _review_only_record(
    *,
    document_path: str,
    section_id: str,
    original_fragments: list[str],
    unresolved_fragments: list[str],
) -> ReferenceRepairRecord:
    return ReferenceRepairRecord(
        document_path=document_path,
        section_id=section_id,
        ref_id="",
        display_ref_id="",
        source_name="",
        source_title="",
        description="",
        url="",
        links=[],
        confidence=0.2,
        review_flag=True,
        numbering_repaired=False,
        link_status="unresolved",
        original_fragments=[_normalize_text(fragment) for fragment in original_fragments if _normalize_text(fragment)],
        unresolved_fragments=[_normalize_text(fragment) for fragment in unresolved_fragments if _normalize_text(fragment)],
    )


def _should_render_record(record: ReferenceRepairRecord) -> bool:
    if not record.links or not record.source_title:
        return False
    if any(pattern.search(record.source_title) for pattern in REFERENCE_BANNED_OUTPUT_PATTERNS):
        return False
    if len(_extract_inline_reference_ids(record.source_title)) > 1:
        return False
    return record.confidence >= 0.65


def _apply_conservative_numbering(records: list[ReferenceRepairRecord]) -> int:
    if not records:
        return 0
    explicit_ids = [record.ref_id for record in records if record.ref_id]
    if not explicit_ids:
        return 0
    if any(not _is_numeric_reference_id(source_id) for source_id in explicit_ids):
        for record in records:
            if record.ref_id and not record.display_ref_id:
                record.display_ref_id = record.ref_id
        return 0

    repaired = 0
    expected = None
    for record in records:
        if record.ref_id:
            record.display_ref_id = record.display_ref_id or record.ref_id
            value = _reference_numeric_value(record.ref_id)
            if value is not None:
                expected = value
            continue
        if expected is None:
            continue
        expected += 1
        record.display_ref_id = _reference_display_id_from_number(expected)
        record.numbering_repaired = True
        repaired += 1
    return repaired


def _render_reference_scope_html(
    records: list[ReferenceRepairRecord],
    *,
    prefix_units: list[_ReferenceScopeUnit],
    suffix_units: list[_ReferenceScopeUnit],
    section_id: str,
    section_title: str,
    heading_level: int,
    language_hint: str | None,
) -> tuple[str, int]:
    if not records:
        return "", 0
    language = (language_hint or "").lower().strip()
    section_title = section_title or ("Źródła" if language.startswith("pl") else "References")
    heading_level = max(1, min(heading_level or 1, 6))

    parts: list[str] = [
        f'<section epub:type="bibliography" id="{html.escape(section_id)}" class="reference-bibliography">',
        f'<h{heading_level} id="{html.escape(f"{section_id}-heading")}">{html.escape(section_title)}</h{heading_level}>',
    ]
    for unit in prefix_units:
        if unit.node_name in {"h1", "h2", "h3", "h4", "h5", "h6"} and _looks_like_reference_scope_title(unit.text):
            continue
        parts.append(unit.html)
    parts.append("<ol>")
    for index, record in enumerate(records, start=1):
        parts.append(_render_reference_list_item(record, index=index))
    parts.append("</ol></section>")
    for unit in suffix_units:
        if unit.html:
            parts.append(unit.html)

    fragment_html = "".join(parts)
    visible_junk_detected = sum(len(pattern.findall(fragment_html)) for pattern in REFERENCE_BANNED_OUTPUT_PATTERNS)
    return fragment_html, visible_junk_detected


def _render_reference_list_item(record: ReferenceRepairRecord, *, index: int) -> str:
    ref_display = record.display_ref_id or record.ref_id
    anchor_id = "".join(char for char in (ref_display or f"ref-{index:03d}").lower() if char.isalnum() or char in {"-", "_"}) or f"ref-{index:03d}"
    label_parts: list[str] = []
    if ref_display:
        label_parts.append(f'<span class="reference-id"><strong>{html.escape(ref_display)}</strong></span>')
    label_parts.append(f'<span class="reference-title">{html.escape(record.source_title)}</span>')
    if record.description:
        label_parts.append(f' - <span class="reference-description">{html.escape(record.description)}</span>')
    links_markup = "".join(
        f'<p class="reference-links"><a class="reference-link" href="{html.escape(link)}">{html.escape(link)}</a></p>'
        for link in record.links
    )
    review_class = " review-needed" if record.review_flag else ""
    return (
        f'<li id="{html.escape(anchor_id)}" class="reference-entry{review_class}">'
        f'<p class="reference-label">{" ".join(label_parts)}</p>'
        f"{links_markup}</li>"
    )


def _classify_link_status(url: str) -> str:
    normalized = _normalize_text(url)
    if not normalized:
        return "unresolved"
    try:
        parsed = urlsplit(normalized)
    except Exception:
        return "unresolved"
    host = (parsed.hostname or "").lower()
    if parsed.scheme in {"http", "https"} and host and "." in host:
        return "syntactically_valid"
    if normalized.startswith("https://doi.org/10.") or normalized.startswith("http://doi.org/10."):
        return "syntactically_valid"
    return "likely_valid"


def _derive_reference_section_id(node: Tag, *, fallback_index: int) -> str:
    for candidate in (
        _normalize_text(node.get("id", "")),
        _normalize_text(node.get_text(" ", strip=True)).lower().replace(" ", "-"),
    ):
        cleaned = "".join(char for char in candidate if char.isalnum() or char in {"-", "_"}).strip("-_")
        if cleaned:
            return cleaned
    return f"references-{fallback_index:03d}"


def _build_reference_summary(
    *,
    chapter_count: int,
    modified_documents: int,
    records: list[ReferenceRepairRecord],
    sections: list[dict[str, Any]],
    citation_scan: dict[str, Any],
    citation_coverage: list[dict[str, Any]],
    epubcheck: dict[str, Any],
) -> dict[str, Any]:
    sections_rebuilt = sum(int(section.get("report", {}).get("scope_replaced_count", 0) or 0) for section in sections)
    rendered_records = sum(int(section.get("rendered_record_count", 0) or 0) for section in sections)
    records_flagged = sum(1 for record in records if record.review_flag)
    urls_repaired = sum(int(section.get("report", {}).get("repaired_link_count", 0) or 0) for section in sections)
    urls_unresolved = sum(len(record.unresolved_fragments) for record in records)
    numbering_fixed = sum(int(section.get("report", {}).get("numbering_issue_count", 0) or 0) for section in sections)
    visible_junk_detected = sum(int(section.get("report", {}).get("visible_junk_detected", 0) or 0) for section in sections)
    clickable_link_count = sum(int(section.get("report", {}).get("clickable_link_count", 0) or 0) for section in sections)
    citations_detected = sum(1 for item in citation_coverage if int(item.get("use_count", 0) or 0) > 0)
    citations_covered = sum(1 for item in citation_coverage if item.get("status") == "covered")
    citations_missing_record = sum(1 for item in citation_coverage if item.get("status") == "missing_record")
    citations_ambiguous = sum(1 for item in citation_coverage if item.get("status") == "ambiguous_record")
    unused_reference_records = [
        item.get("ref_id", "")
        for item in citation_coverage
        if item.get("status") == "unused_record" and item.get("ref_id")
    ]
    empty_reference_sections_detected = len(citation_scan.get("empty_reference_sections", []))
    reference_quality_gate_failed = any(
        [
            bool(visible_junk_detected),
            citations_missing_record > 0,
            citations_ambiguous > 0,
            empty_reference_sections_detected > 0,
            citations_detected > 0 and rendered_records == 0,
        ]
    )
    reference_quality_gate_status = "failed" if reference_quality_gate_failed else "passed"
    summary = {
        "documents_processed": chapter_count,
        "documents_modified": modified_documents,
        "sections_rebuilt": sections_rebuilt,
        "records_detected": len(records),
        "records_reconstructed": rendered_records,
        "records_flagged_for_review": records_flagged,
        "urls_repaired": urls_repaired,
        "urls_unresolved": urls_unresolved,
        "numbering_issues_fixed": numbering_fixed,
        "visible_junk_detected": visible_junk_detected,
        "quality_gate_status": reference_quality_gate_status,
        "epubcheck_status": epubcheck.get("status", "unavailable"),
        "entries_rebuilt": rendered_records,
        "review_entry_count": records_flagged,
        "unresolved_fragment_count": urls_unresolved,
        "clickable_link_count": clickable_link_count,
        "repaired_link_count": urls_repaired,
        "scope_replaced_count": sections_rebuilt,
        "citation_coverage": citation_coverage,
        "citations_detected": citations_detected,
        "citations_covered": citations_covered,
        "citations_missing_record": citations_missing_record,
        "citations_ambiguous": citations_ambiguous,
        "unused_reference_records": unused_reference_records,
        "unused_reference_record_count": len(unused_reference_records),
        "empty_reference_sections_detected": empty_reference_sections_detected,
        "reference_quality_gate_status": reference_quality_gate_status,
    }
    return summary


def _build_summary_markdown(
    summary: dict[str, Any],
    *,
    sections: list[dict[str, Any]],
    citation_coverage: list[dict[str, Any]],
) -> str:
    lines = [
        "# Reference Repair Summary",
        "",
        f"- Documents processed: {summary['documents_processed']}",
        f"- Documents modified: {summary['documents_modified']}",
        f"- Sections rebuilt: {summary['sections_rebuilt']}",
        f"- Records detected: {summary['records_detected']}",
        f"- Records reconstructed: {summary['records_reconstructed']}",
        f"- Records flagged for review: {summary['records_flagged_for_review']}",
        f"- URLs repaired: {summary['urls_repaired']}",
        f"- Unresolved URL fragments: {summary['urls_unresolved']}",
        f"- Numbering issues fixed: {summary['numbering_issues_fixed']}",
        f"- Visible junk detected: {summary['visible_junk_detected']}",
        f"- Quality gate: {summary['reference_quality_gate_status']}",
        f"- EPUBCheck: {summary['epubcheck_status']}",
        f"- Citations detected: {summary['citations_detected']}",
        f"- Citations covered: {summary['citations_covered']}",
        f"- Citations missing record: {summary['citations_missing_record']}",
        f"- Citations ambiguous: {summary['citations_ambiguous']}",
        f"- Empty reference sections detected: {summary['empty_reference_sections_detected']}",
    ]
    if sections:
        lines.extend(["", "## Modified Sections"])
        for section in sections:
            report = section.get("report", {})
            lines.append(
                f"- {section['document_path']}#{section['section_id']}: {section['record_count']} records, "
                f"{section.get('rendered_record_count', 0)} rendered, "
                f"{report.get('review_entry_count', 0)} review items"
            )
    if citation_coverage:
        lines.extend(["", "## Citation Coverage"])
        for item in citation_coverage:
            lines.append(
                f"- {item.get('ref_id', '')}: {item.get('status', 'unknown')} "
                f"(uses: {item.get('use_count', 0)}, confidence: {item.get('confidence', 0.0)})"
            )
        blockers = [
            item.get("ref_id", "")
            for item in citation_coverage
            if item.get("status") in {"missing_record", "ambiguous_record"} and item.get("ref_id")
        ]
        unused = [item.get("ref_id", "") for item in citation_coverage if item.get("status") == "unused_record" and item.get("ref_id")]
        if blockers:
            lines.extend(["", "## Coverage Blockers"])
            lines.extend(f"- {ref_id}" for ref_id in blockers)
        if unused:
            lines.extend(["", "## Unused Reference Records"])
            lines.extend(f"- {ref_id}" for ref_id in unused)
    return "\n".join(lines).strip() + "\n"


def _build_before_after_markdown(before_after: list[dict[str, str]]) -> str:
    if not before_after:
        return "# Reference Before / After\n\n- No modified reference sections.\n"
    lines = ["# Reference Before / After", ""]
    for item in before_after:
        lines.extend(
            [
                f"## {item['document_path']}#{item['section_id']}",
                "",
                "### Before",
                "```html",
                item["before"],
                "```",
                "",
                "### After",
                "```html",
                item["after"],
                "```",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _safe_rel(path: Path, root_dir: Path) -> str:
    try:
        return path.relative_to(root_dir).as_posix()
    except Exception:
        return path.name


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair broken EPUB references and hyperlink sections.")
    parser.add_argument("epub_path", help="Input EPUB path")
    parser.add_argument("--output-dir", default="output", help="Output directory for repaired EPUB")
    parser.add_argument("--reports-dir", default="reports", help="Directory for JSON/Markdown reports")
    parser.add_argument("--language", default="", help="Optional language hint, e.g. pl or en")
    args = parser.parse_args()

    source_path = Path(args.epub_path).resolve()
    if not source_path.exists():
        raise SystemExit(f"Input EPUB not found: {source_path}")

    result = run_reference_repair_pipeline(
        source_path,
        output_dir=Path(args.output_dir),
        reports_dir=Path(args.reports_dir),
        language_hint=args.language or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
