from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag

from kindle_semantic_cleanup import (
    _build_toc_map,
    _collect_structural_integrity_summary,
    _dedupe_repeated_subsection_toc_labels,
    _evaluate_structural_gate,
    _evaluate_toc_gate,
    _get_spine_xhtml_paths,
    _extract_epub,
    _heading_candidate_looks_like_layout_artifact,
    _inventory_navigation_document,
    _looks_like_promotional_banner,
    _looks_like_table_header_heading,
    _looks_like_synthetic_section_label,
    _looks_like_truncated_heading,
    _is_generic_schema_heading_label,
    _is_pseudo_heading_candidate,
    _locate_opf,
    _looks_like_reference_entry_text,
    _looks_like_reference_section_title,
    _normalize_text,
    _pack_epub,
    _rewrite_navigation,
    _slugify,
    _snapshot_package_metadata,
    _should_include_in_toc,
    _training_book_key,
    _title_fragments_match,
    finalize_epub_for_kindle,
)
from premium_tools import run_epubcheck

INLINE_HEADING_PREFIXES = (
    "Co to jest",
    "Jak działa",
    "Jak dziala",
    "Implikacje biznesowe",
    "Przykład",
    "Przyklad",
    "Wniosek",
    "Ryzyka",
    "Rekomendacje",
    "Architektura",
    "Zależności systemowe",
    "Zaleznosci systemowe",
)

EPUB_SAFE_TAGS = {
    "a",
    "abbr",
    "address",
    "article",
    "aside",
    "audio",
    "b",
    "bdi",
    "bdo",
    "blockquote",
    "body",
    "br",
    "button",
    "caption",
    "cite",
    "code",
    "col",
    "colgroup",
    "data",
    "dd",
    "del",
    "details",
    "dfn",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "head",
    "header",
    "hr",
    "html",
    "i",
    "img",
    "input",
    "ins",
    "kbd",
    "label",
    "li",
    "link",
    "main",
    "meta",
    "nav",
    "object",
    "ol",
    "p",
    "picture",
    "pre",
    "q",
    "ruby",
    "s",
    "samp",
    "script",
    "section",
    "small",
    "span",
    "strong",
    "style",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "template",
    "textarea",
    "tfoot",
    "th",
    "thead",
    "time",
    "title",
    "tr",
    "u",
    "ul",
    "var",
    "video",
    "wbr",
}

DENSE_HANDBOOK_CREDENTIALS = {
    "ba",
    "bsc",
    "capm",
    "cbap",
    "csm",
    "cspo",
    "dba",
    "fca",
    "ice",
    "ieng",
    "iiba",
    "mba",
    "mcomp",
    "mim",
    "msc",
    "msm",
    "oceb",
    "pba",
    "phd",
    "pmi",
    "pmp",
    "togaf",
    "vm",
}

DENSE_HANDBOOK_TITLE_WORDS = {
    "analysis",
    "appendix",
    "architecture",
    "area",
    "assessment",
    "business",
    "capability",
    "chapter",
    "change",
    "concepts",
    "definition",
    "design",
    "elements",
    "framework",
    "glossary",
    "guide",
    "implementation",
    "introduction",
    "knowledge",
    "management",
    "model",
    "monitoring",
    "planning",
    "principles",
    "process",
    "purpose",
    "requirements",
    "section",
    "solution",
    "strategy",
    "techniques",
}

DENSE_GENERIC_TOC_LABELS = {
    "approach",
    "assessment",
    "benefits",
    "business",
    "definition",
    "delivery",
    "description",
    "elements",
    "enterprise",
    "evaluation",
    "guidelines and tools",
    "guidelines/tools",
    "impact",
    "implementation",
    "information",
    "input",
    "inputs",
    "limitations",
    "measures",
    "needs",
    "output",
    "outputs",
    "overview",
    "potential value",
    "process",
    "project",
    "purpose",
    "rationale",
    "requirements",
    "solution",
    "stakeholder",
    "stakeholders",
    "summary",
    "tasks using this output",
    "techniques",
    "usage considerations",
}


@dataclass
class HeadingRepairResult:
    epub_bytes: bytes
    summary: dict[str, Any]
    heading_inventory: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    toc_mapping: list[dict[str, Any]] = field(default_factory=list)
    manual_review_queue: list[dict[str, Any]] = field(default_factory=list)
    qa: dict[str, Any] = field(default_factory=dict)
    epubcheck: dict[str, Any] = field(default_factory=dict)
    heading_diff_markdown: str = ""
    qa_report_markdown: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "heading_inventory": self.heading_inventory,
            "rejected_heading_candidates": self.rejected_candidates,
            "toc_mapping": self.toc_mapping,
            "manual_review_queue": self.manual_review_queue,
            "qa": self.qa,
            "epubcheck": self.epubcheck,
        }


def repair_epub_headings_and_toc(
    epub_bytes: bytes,
    *,
    title_hint: str = "",
    author_hint: str = "",
    language_hint: str = "",
    publication_profile: str | None = None,
) -> HeadingRepairResult:
    if not epub_bytes:
        empty_epubcheck = {"status": "unavailable", "tool": "epubcheck", "messages": []}
        summary = {
            "documents_processed": 0,
            "heading_candidates_detected": 0,
            "headings_kept": 0,
            "headings_promoted": 0,
            "headings_releveled": 0,
            "headings_removed": 0,
            "headings_added": 0,
            "rejected_candidate_count": 0,
            "toc_entries_before": 0,
            "toc_entries_after": 0,
            "toc_broken_target_count": 0,
            "manual_review_count": 0,
            "suspicious_final_heading_count": 0,
            "epubcheck_status": "unavailable",
            "release_status": "fail",
        }
        qa = {
            "baseline": {},
            "final": {},
            "gates": {},
            "release_status": "fail",
            "manual_review_queue": [],
            "epubcheck": empty_epubcheck,
        }
        return HeadingRepairResult(
            epub_bytes=epub_bytes,
            summary=summary,
            qa=qa,
            epubcheck=empty_epubcheck,
            heading_diff_markdown="# Heading Diff Report\n\n- No EPUB content received.\n",
            qa_report_markdown="# QA Report\n\n- Input EPUB was empty.\n",
        )

    before_scan = _scan_epub_heading_candidates(epub_bytes, include_pseudo=True)
    defaults = _resolve_repair_inputs(
        epub_bytes,
        title_hint=title_hint,
        author_hint=author_hint,
        language_hint=language_hint,
    )
    repaired_epub, phase_report = finalize_epub_for_kindle(
        epub_bytes,
        title=defaults["title"],
        author=defaults["author"],
        language=defaults["language"],
        publication_profile=publication_profile,
        return_report=True,
        report_mode="rich",
    )
    repaired_epub, after_scan, raw_toc_map, nav_summary, structural_phase = _normalize_headings_and_rebuild_navigation(
        repaired_epub
    )
    epubcheck = run_epubcheck(repaired_epub)

    inventory_phase = ((phase_report.get("phases") or {}).get("inventory") or {})
    heading_phase = ((phase_report.get("phases") or {}).get("heading_recovery") or {})
    heading_decisions = list(heading_phase.get("decisions") or [])
    manual_review_queue = list(phase_report.get("manual_review_queue") or [])

    heading_inventory = _build_heading_inventory(
        heading_decisions=heading_decisions,
        before_scan=before_scan,
        after_scan=after_scan,
        manual_review_queue=manual_review_queue,
    )
    rejected_candidates = _build_rejected_heading_candidates(heading_inventory)
    toc_phase = _build_post_rebuild_toc_phase(
        toc_map=raw_toc_map,
        nav_summary=nav_summary,
        after_scan=after_scan,
    )
    gates = dict(phase_report.get("gates") or {})
    gates["C"] = _evaluate_heading_gate_after_rebuild(after_scan)
    gates["D"] = _evaluate_toc_gate(toc_phase)
    gates["E"] = _evaluate_structural_gate(structural_phase)
    manual_review_queue = _filter_resolved_manual_review_items(
        manual_review_queue,
        toc_map=raw_toc_map,
        structural_phase=structural_phase,
    )
    toc_mapping = _build_heading_toc_mapping(
        toc_phase=toc_phase,
        heading_inventory=heading_inventory,
    )
    release_status = _derive_release_status(
        gates=gates,
        epubcheck=epubcheck,
        manual_review_queue=manual_review_queue,
    )
    gates["F"] = {
        "status": release_status,
        "blockers": [] if release_status != "fail" else ["Heading/TOC release gate failed."],
        "warnings": [] if release_status == "pass" else (["Manual review queue is not empty."] if release_status == "pass_with_review" else []),
    }
    summary = _build_summary(
        heading_inventory=heading_inventory,
        rejected_candidates=rejected_candidates,
        toc_mapping=toc_mapping,
        inventory_phase=inventory_phase,
        heading_phase=heading_phase,
        toc_phase=toc_phase,
        manual_review_queue=manual_review_queue,
        epubcheck=epubcheck,
        release_status=release_status,
        before_scan=before_scan,
        after_scan=after_scan,
    )
    qa_payload = _build_qa_payload(
        phase_report=phase_report,
        toc_mapping=toc_mapping,
        summary=summary,
        before_scan=before_scan,
        after_scan=after_scan,
        manual_review_queue=manual_review_queue,
        epubcheck=epubcheck,
        release_status=release_status,
        structural_phase=structural_phase,
        gates=gates,
    )
    return HeadingRepairResult(
        epub_bytes=repaired_epub,
        summary=summary,
        heading_inventory=heading_inventory,
        rejected_candidates=rejected_candidates,
        toc_mapping=toc_mapping,
        manual_review_queue=manual_review_queue,
        qa=qa_payload,
        epubcheck=epubcheck,
        heading_diff_markdown=_build_heading_diff_markdown(
            heading_inventory=heading_inventory,
            toc_mapping=toc_mapping,
            summary=summary,
        ),
        qa_report_markdown=_build_qa_markdown(qa_payload),
    )


def run_heading_repair_pipeline(
    source_epub: Path,
    *,
    output_dir: Path,
    reports_dir: Path,
    title_hint: str = "",
    author_hint: str = "",
    language_hint: str = "",
    publication_profile: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    result = repair_epub_headings_and_toc(
        source_epub.read_bytes(),
        title_hint=title_hint,
        author_hint=author_hint,
        language_hint=language_hint,
        publication_profile=publication_profile,
    )

    repaired_path = output_dir / "repaired.epub"
    repaired_path.write_bytes(result.epub_bytes)
    heading_inventory_payload = {
        "summary": {
            "candidate_count": len(result.heading_inventory),
            "file_count": len({item.get("file", "") for item in result.heading_inventory if item.get("file")}),
            "rejected_count": len(result.rejected_candidates),
        },
        "candidates": result.heading_inventory,
    }
    rejected_payload = {
        "summary": {"rejected_count": len(result.rejected_candidates)},
        "candidates": result.rejected_candidates,
    }
    toc_payload = {
        "summary": {
            "entry_count": len(result.toc_mapping),
            "broken_target_count": sum(1 for item in result.toc_mapping if "missing-anchor" in item.get("issues", [])),
            "review_entry_count": sum(1 for item in result.toc_mapping if item.get("status") != "pass"),
        },
        "entries": result.toc_mapping,
        "epubcheck": result.epubcheck,
    }
    (reports_dir / "heading_inventory.json").write_text(
        json.dumps(heading_inventory_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "rejected_heading_candidates.json").write_text(
        json.dumps(rejected_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "toc_mapping.json").write_text(
        json.dumps(toc_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (reports_dir / "heading_diff_report.md").write_text(result.heading_diff_markdown, encoding="utf-8")
    (reports_dir / "qa_report.md").write_text(result.qa_report_markdown, encoding="utf-8")
    return {
        "output_epub": str(repaired_path),
        "heading_inventory": str(reports_dir / "heading_inventory.json"),
        "rejected_heading_candidates": str(reports_dir / "rejected_heading_candidates.json"),
        "toc_mapping": str(reports_dir / "toc_mapping.json"),
        "heading_diff_report": str(reports_dir / "heading_diff_report.md"),
        "qa_report": str(reports_dir / "qa_report.md"),
        "summary": result.summary,
    }


def _resolve_repair_inputs(
    epub_bytes: bytes,
    *,
    title_hint: str,
    author_hint: str,
    language_hint: str,
) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        metadata = _snapshot_package_metadata(opf_path)
    return {
        "title": _normalize_text(title_hint or str(metadata.get("title", "") or "")) or "Publication",
        "author": _normalize_text(author_hint or str(metadata.get("creator", "") or "")) or "Unknown",
        "language": _normalize_text(language_hint or str(metadata.get("language", "") or "")) or "en",
    }


def _scan_epub_heading_candidates(epub_bytes: bytes, *, include_pseudo: bool) -> dict[str, list[dict[str, Any]]]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        chapter_paths = _get_spine_xhtml_paths(opf_path)
        return {
            chapter_path.name: _scan_heading_candidates_from_text(
                chapter_path.read_text(encoding="utf-8"),
                file_name=chapter_path.name,
                include_pseudo=include_pseudo,
            )
            for chapter_path in chapter_paths
            if chapter_path.exists()
        }


def _normalize_headings_and_rebuild_navigation(
    epub_bytes: bytes,
) -> tuple[bytes, dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        chapter_paths = _get_spine_xhtml_paths(opf_path)

        after_scan: dict[str, list[dict[str, Any]]] = {}
        empty_reference_files: set[str] = set()
        for chapter_path in chapter_paths:
            after_scan[chapter_path.name] = _ensure_heading_ids_and_scan(chapter_path)
            if _chapter_is_empty_reference_section(chapter_path, after_scan[chapter_path.name]):
                empty_reference_files.add(chapter_path.name)

        toc_entries = _build_toc_entries_from_scan(after_scan, excluded_reference_files=empty_reference_files)
        metadata = _snapshot_package_metadata(opf_path)
        _rewrite_navigation(
            root_dir,
            opf_path,
            toc_entries=toc_entries,
            title=_normalize_text(str(metadata.get("title", "") or "")) or "Publication",
            language=_normalize_text(str(metadata.get("language", "") or "")) or "en",
        )
        _strip_missing_landmarks(opf_path.parent / "nav.xhtml", package_dir=opf_path.parent)
        toc_map = _build_toc_map(toc_entries, chapter_paths=chapter_paths, package_dir=opf_path.parent)
        nav_summary = _inventory_navigation_document(opf_path)
        structural_phase = {
            "status": "completed",
            **_collect_structural_integrity_summary(
                opf_path,
                root_dir=root_dir,
                chapter_paths=chapter_paths,
                toc_map=toc_map,
            ),
        }
        return _pack_epub(root_dir), after_scan, toc_map, nav_summary, structural_phase


def _scan_heading_candidates_from_text(
    xhtml_text: str,
    *,
    file_name: str,
    include_pseudo: bool,
) -> list[dict[str, Any]]:
    soup = BeautifulSoup(xhtml_text, "xml")
    body = soup.find("body")
    if body is None:
        return []

    candidates: list[dict[str, Any]] = []
    order = 0
    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span"]):
        if node.find_parent(["figure", "figcaption", "table", "thead", "tbody", "tfoot", "ul", "ol", "li", "dl", "blockquote"]) is not None:
            continue
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        is_real_heading = node.name.startswith("h")
        candidate_type = "real" if is_real_heading else "pseudo"
        level = int(node.name[1]) if is_real_heading else None
        if not is_real_heading:
            if not include_pseudo or not _is_pseudo_heading_candidate(node, text):
                continue
        order += 1
        candidates.append(
            {
                "file_name": file_name,
                "order": order,
                "element": node.name,
                "id": _normalize_text(node.get("id", "")),
                "text": text,
                "level": level,
                "candidate_type": candidate_type,
                "location": _bs4_tag_path(node),
                "classes": _class_list_string(node),
                "previous_text": _neighbor_text(node, previous=True),
                "next_text": _neighbor_text(node, previous=False),
            }
        )
    return candidates


def _ensure_heading_ids_and_scan(chapter_path: Path) -> list[dict[str, Any]]:
    original = chapter_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(original, "xml")
    changed = _sanitize_non_epub_markup(soup)
    changed = _split_inline_heading_paragraphs(soup) or changed
    changed = _promote_supported_pseudo_headings(soup) or changed
    changed = _normalize_consecutive_heading_clusters(soup) or changed
    changed = _demote_heading_noise(soup) or changed
    changed = _ensure_primary_heading(soup) or changed
    id_counts = Counter(
        _normalize_text(str(node.get("id", "") or ""))
        for node in soup.find_all(attrs={"id": True})
        if _normalize_text(str(node.get("id", "") or ""))
    )
    claimed_ids: set[str] = set()

    for heading in soup.find_all(["h1", "h2", "h3"]):
        text = _normalize_text(heading.get_text(" ", strip=True))
        if not text:
            continue
        current_id = _normalize_text(str(heading.get("id", "") or ""))
        if current_id and id_counts.get(current_id, 0) == 1 and current_id not in claimed_ids:
            claimed_ids.add(current_id)
            continue
        new_id = _unique_heading_id(text, claimed_ids)
        if current_id != new_id:
            heading["id"] = new_id
            changed = True
        claimed_ids.add(new_id)

    changed = _dedupe_all_element_ids(soup) or changed
    updated = str(soup) if changed else original
    if changed:
        chapter_path.write_text(updated, encoding="utf-8")
    return _scan_heading_candidates_from_text(updated, file_name=chapter_path.name, include_pseudo=False)


def _ensure_primary_heading(soup: BeautifulSoup) -> bool:
    headings = list(soup.find_all(["h1", "h2", "h3"]))
    if not headings:
        return False
    if any(node.name == "h1" for node in headings):
        return False
    headings[0].name = "h1"
    return True


def _chapter_is_empty_reference_section(chapter_path: Path, headings: list[dict[str, Any]]) -> bool:
    if not any(_looks_like_reference_heading_loose(str(item.get("text", "") or "")) for item in headings):
        return False
    soup = BeautifulSoup(chapter_path.read_text(encoding="utf-8"), "xml")
    body = soup.find("body")
    if body is None:
        return False

    meaningful_blocks = 0
    for node in body.find_all(["li", "p", "div", "span", "td"]):
        if node.find_parent(["table", "thead", "tbody", "tfoot"]) is not None and node.name != "td":
            continue
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text or _looks_like_reference_heading_loose(text):
            continue
        if _looks_like_reference_entry_text(text):
            return False
        if re.search(r"(?i)\bhttps?://|www\.", text):
            return False
        if re.match(r"^\[?[Rr]?\d+\]?\b", text) and len(text.split()) >= 2:
            return False
        if len(text) >= 32:
            meaningful_blocks += 1

    return meaningful_blocks <= 1


def _looks_like_reference_heading_loose(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    if _looks_like_reference_section_title(normalized):
        return True
    return any(marker in normalized for marker in ("referenc", "bibliograf", "sources", "bibliograph", "zrod", "źród"))


def _sanitize_non_epub_markup(soup: BeautifulSoup) -> bool:
    body = soup.find("body")
    if body is None:
        return False
    changed = False
    for node in list(body.find_all(True)):
        tag_name = _normalize_text(getattr(node, "name", "") or "").lower()
        if not tag_name or tag_name in EPUB_SAFE_TAGS or ":" in tag_name:
            continue
        node.unwrap()
        changed = True
    return changed


def _dedupe_all_element_ids(soup: BeautifulSoup) -> bool:
    claimed_ids: set[str] = set()
    changed = False
    for node in soup.find_all(attrs={"id": True}):
        current_id = _normalize_text(str(node.get("id", "") or ""))
        if not current_id:
            continue
        if current_id not in claimed_ids:
            claimed_ids.add(current_id)
            continue
        basis = _normalize_text(node.get_text(" ", strip=True)) or current_id
        new_id = _unique_heading_id(basis, claimed_ids)
        node["id"] = new_id
        claimed_ids.add(new_id)
        changed = True
    return changed


def _promote_supported_pseudo_headings(soup: BeautifulSoup) -> bool:
    body = soup.find("body")
    if body is None:
        return False
    changed = False
    for node in body.find_all(["p", "div", "span"]):
        if node.find_parent(["figure", "figcaption", "table", "thead", "tbody", "tfoot", "ul", "ol", "li", "dl", "blockquote"]) is not None:
            continue
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text or not (_is_pseudo_heading_candidate(node, text) or _is_known_section_label(text)):
            continue
        if _heading_candidate_looks_like_layout_artifact({"text": text, "element": node.name}, repeated_counts=Counter()):
            continue
        if not _has_supporting_content(node):
            continue
        new_level = _promoted_heading_level(node)
        if node.name != f"h{new_level}":
            node.name = f"h{new_level}"
            changed = True
    return changed


def _is_known_section_label(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    return normalized in {label.lower() for label in INLINE_HEADING_PREFIXES}


def _split_inline_heading_paragraphs(soup: BeautifulSoup) -> bool:
    body = soup.find("body")
    if body is None:
        return False
    changed = False
    for node in list(body.find_all(["p", "div", "span"])):
        if node.find_parent(["figure", "figcaption", "table", "thead", "tbody", "tfoot", "ul", "ol", "li", "dl", "blockquote"]) is not None:
            continue
        if node.find(["a", "img", "table", "ul", "ol", "li", "figure", "figcaption"]):
            continue
        text = _normalize_text(node.get_text(" ", strip=True))
        heading_text, body_text = _extract_inline_heading_prefix(text)
        if not heading_text or len(body_text) < 24:
            continue
        if _heading_candidate_looks_like_layout_artifact({"text": heading_text, "element": node.name}, repeated_counts=Counter()):
            continue
        heading_node = soup.new_tag(node.name)
        if node.get("class"):
            heading_node["class"] = node.get("class")
        heading_node.string = heading_text
        body_node = soup.new_tag("p" if node.name == "span" else node.name)
        body_node.string = body_text
        node.insert_before(heading_node)
        node.insert_before(body_node)
        node.extract()
        changed = True
    return changed


def _normalize_consecutive_heading_clusters(soup: BeautifulSoup) -> bool:
    body = soup.find("body")
    if body is None:
        return False
    changed = False
    containers = [body, *body.find_all(["section", "article"])]
    for container in containers:
        children = [node for node in container.children if isinstance(node, Tag)]
        index = 0
        while index < len(children):
            if children[index].name not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                index += 1
                continue
            cluster = [children[index]]
            next_index = index + 1
            while next_index < len(children) and children[next_index].name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                cluster.append(children[next_index])
                next_index += 1
            if len(cluster) >= 2:
                if _should_demote_heading_cluster(cluster):
                    for node in cluster:
                        if node.name != "p":
                            node.name = "p"
                            changed = True
                else:
                    merged_text = _merged_heading_cluster_text(cluster)
                    if merged_text:
                        first = cluster[0]
                        target_level = min(
                            int(node.name[1]) for node in cluster if node.name and len(node.name) == 2 and node.name[1].isdigit()
                        )
                        first.name = f"h{target_level}"
                        first.clear()
                        first.string = merged_text
                        for redundant in cluster[1:]:
                            redundant.decompose()
                        changed = True
            index = next_index
    return changed


def _should_demote_heading_cluster(cluster: list[Tag]) -> bool:
    texts = [_normalize_text(node.get_text(" ", strip=True)) for node in cluster]
    texts = [text for text in texts if text]
    if len(texts) < 2:
        return False
    previous_text = _previous_meaningful_sibling_text(cluster[0])
    figure_context = any(marker in previous_text.lower() for marker in {"figure", "diagram", "image", "shows"})
    short_fragment_count = sum(1 for text in texts if _is_short_heading_fragment(text))
    if any(text[:1] in {"•", "·", "▪", "◦", ""} for text in texts):
        return True
    if any(_looks_like_figure_caption_heading(text) for text in texts):
        return len(texts) >= 3 or figure_context
    if len(texts) >= 6 and short_fragment_count >= len(texts) - 2:
        return True
    if len(texts) >= 4 and figure_context and short_fragment_count >= len(texts) - 1:
        return True
    return False


def _merged_heading_cluster_text(cluster: list[Tag]) -> str:
    texts = [_normalize_text(node.get_text(" ", strip=True)) for node in cluster]
    texts = [text.lstrip("•·▪◦ ").strip() for text in texts if text]
    if len(texts) < 2 or len(texts) > 3:
        return ""
    levels = {
        int(node.name[1])
        for node in cluster
        if node.name and len(node.name) == 2 and node.name[0] == "h" and node.name[1].isdigit()
    }
    if len(levels) != 1:
        return ""
    if any(
        _looks_like_figure_caption_heading(text)
        or _looks_like_synthetic_section_label(text)
        or _looks_like_table_header_heading(text)
        for text in texts
    ):
        return ""
    joined = re.sub(r"\s+", " ", " ".join(texts)).strip(" -:;,.")
    if not joined or _looks_like_truncated_heading(joined):
        return ""
    word_count = len(joined.split())
    if word_count < 3 or word_count > 14:
        return ""
    connector_merge = any(
        part.split()[-1].lower() in {"a", "an", "and", "for", "in", "of", "or", "the", "to", "with"}
        or part.endswith(("/", "&", "-", "–", "—"))
        for part in texts[:-1]
        if part.split()
    )
    short_pair_merge = len(texts) == 2 and len(texts[0].split()) <= 2 and len(texts[1].split()) <= 3
    letterspaced_merge = any(_looks_like_letterspaced_heading(part) for part in texts)
    if not (connector_merge or short_pair_merge or letterspaced_merge):
        return ""
    return joined


def _demote_heading_noise(soup: BeautifulSoup) -> bool:
    body = soup.find("body")
    if body is None:
        return False
    changed = False
    for node in body.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = _normalize_text(node.get_text(" ", strip=True))
        if not text:
            continue
        lowered = text.lower()
        if (
            text[:1] in {"•", "·", "▪", "◦", ""}
            or "page " in lowered
            or "strona " in lowered
            or _looks_like_promotional_banner(text)
            or _looks_like_figure_caption_heading(text)
            or _looks_like_table_header_heading(text)
            or _looks_like_synthetic_section_label(text)
            or (_looks_like_truncated_heading(text) and not _has_supporting_content(node))
        ):
            node.name = "p"
            changed = True
    return changed


def _previous_meaningful_sibling_text(node: Tag) -> str:
    sibling = node.previous_sibling
    while sibling is not None:
        if isinstance(sibling, Tag):
            text = _normalize_text(sibling.get_text(" ", strip=True))
            if text:
                return text
        sibling = sibling.previous_sibling
    return ""


def _is_short_heading_fragment(text: str) -> bool:
    normalized = _normalize_text(text).lstrip("•·▪◦ ").strip()
    if not normalized:
        return False
    if _looks_like_truncated_heading(normalized):
        return True
    words = normalized.split()
    if len(words) <= 2:
        return True
    return len(words) <= 4 and normalized.lower() in DENSE_GENERIC_TOC_LABELS


def _looks_like_letterspaced_heading(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return bool(re.match(r"^(?:[A-Z]\s+){4,}[A-Z][A-Z0-9\sÂ®™-]*$", normalized))


def _looks_like_figure_caption_heading(text: str) -> bool:
    normalized = _normalize_text(text)
    return bool(
        re.match(r"(?i)^(?:figure|table|diagram|chart|exhibit|photo)\s+[A-Za-z0-9.\-: ]{1,120}$", normalized)
        or re.match(r"(?i)^(?:rys(?:unek|\.)|tabela|diagram|wykres)\s+[A-Za-z0-9.\-: ]{1,120}$", normalized)
    )


def _extract_inline_heading_prefix(text: str) -> tuple[str, str]:
    normalized = _normalize_text(text)
    lower_text = normalized.lower()
    for prefix in sorted(INLINE_HEADING_PREFIXES, key=len, reverse=True):
        lower_prefix = prefix.lower()
        if not lower_text.startswith(lower_prefix):
            continue
        remainder = normalized[len(prefix) :].lstrip(" :-–—")
        if not remainder:
            continue
        if not remainder[0].isalnum():
            continue
        if len(remainder.split()) < 3:
            continue
        return prefix, remainder
    return "", ""


def _has_supporting_content(node: Tag) -> bool:
    for sibling in node.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if sibling.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            return False
        text = _normalize_text(sibling.get_text(" ", strip=True))
        if sibling.name in {"p", "div", "span"} and len(text) >= 24:
            return True
        if sibling.name in {"ul", "ol", "dl", "table", "blockquote"}:
            return True
        if text:
            return len(text) >= 24
    return False


def _promoted_heading_level(node: Tag) -> int:
    previous_heading = next(
        (
            candidate
            for candidate in node.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
            if candidate.find_parent(["figure", "figcaption", "table", "thead", "tbody", "tfoot", "ul", "ol", "li", "dl", "blockquote"]) is None
        ),
        None,
    )
    if previous_heading is None or not previous_heading.name or not previous_heading.name[1:].isdigit():
        return 1
    previous_level = int(previous_heading.name[1])
    if previous_level <= 1:
        return 2
    return min(previous_level, 3)


def _unique_heading_id(text: str, claimed_ids: set[str]) -> str:
    base = _slugify(text) or "section"
    candidate = base
    counter = 2
    while candidate in claimed_ids:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _build_toc_entries_from_scan(
    after_scan: dict[str, list[dict[str, Any]]],
    *,
    excluded_reference_files: set[str] | None = None,
) -> list[dict[str, Any]]:
    excluded_reference_files = excluded_reference_files or set()
    total_h3 = sum(1 for items in after_scan.values() for item in items if int(item.get("level") or 0) == 3)
    dense_handbook_mode = _is_dense_handbook_scan(after_scan)
    repeated_label_counts: Counter[str] = Counter()
    if dense_handbook_mode:
        for items in after_scan.values():
            for item in items:
                level = int(item.get("level") or 0)
                text = _normalize_text(str(item.get("text", "") or ""))
                if level <= 0 or level > 3 or not text or not _should_include_in_toc(text, level):
                    continue
                repeated_label_counts[_dense_heading_key(text)] += 1
    toc_entries: list[dict[str, Any]] = []
    generic_schema_counts: Counter[tuple[str, str]] = Counter()
    for file_name, items in after_scan.items():
        skip_reference_file = file_name in excluded_reference_files
        local_h3_count = 0
        local_h2_count = 0
        primary_heading = next((item for item in items if int(item.get("level") or 0) == 1), {})
        chapter_primary_key = _training_book_key(str(primary_heading.get("text", "") or ""))
        front_matter_primary = chapter_primary_key in {"front cover", "title", "copyright", "contents", "table of contents"}
        for item in items:
            level = int(item.get("level") or 0)
            if level <= 0 or level > 3:
                continue
            text = _normalize_text(str(item.get("text", "") or ""))
            if not _should_include_in_toc(text, level):
                continue
            if front_matter_primary and level > 1:
                continue
            if skip_reference_file and _looks_like_reference_heading_loose(text):
                continue
            if dense_handbook_mode and not _should_include_dense_handbook_heading(item, items=items, level=level):
                continue
            generic_key = (file_name, text.lower())
            if _is_generic_schema_heading_label(text):
                generic_schema_counts[generic_key] += 1
                continue
            if dense_handbook_mode and _should_skip_repetitive_dense_heading(text, level=level, repeated_count=repeated_label_counts[_dense_heading_key(text)]):
                continue
            if level == 2:
                local_h2_count += 1
                if dense_handbook_mode and local_h2_count > 4 and not _looks_like_numbered_heading(text):
                    continue
            if level == 3:
                local_h3_count += 1
                if total_h3 > 24 and local_h3_count > (1 if dense_handbook_mode else 3):
                    continue
            toc_entries.append(
                {
                    "file_name": file_name,
                    "id": _normalize_text(str(item.get("id", "") or "")),
                    "text": text,
                    "level": level,
                }
            )
    return _dedupe_repeated_subsection_toc_labels(toc_entries)


def _is_dense_handbook_scan(after_scan: dict[str, list[dict[str, Any]]]) -> bool:
    file_count = len(after_scan)
    candidate_count = sum(len(items) for items in after_scan.values())
    heading_count = sum(
        1
        for items in after_scan.values()
        for item in items
        if int(item.get("level") or 0) in {1, 2, 3}
    )
    return file_count >= 40 or candidate_count >= 220 or heading_count >= 180


def _should_include_dense_handbook_heading(
    candidate: dict[str, Any],
    *,
    items: list[dict[str, Any]],
    level: int,
) -> bool:
    text = _normalize_text(str(candidate.get("text", "") or ""))
    if not text:
        return False
    if _looks_like_person_credential_heading(text):
        return False
    if level == 3 and not (_looks_like_numbered_heading(text) or len(text.split()) >= 3):
        return False
    if len(items) >= 12 and level >= 2 and _looks_like_dense_backmatter_heading(text):
        return False
    return True


def _dense_heading_key(text: str) -> str:
    normalized = _normalize_text(text).lstrip("•·▪◦ ").strip().lower()
    normalized = re.sub(r"^\.\d+\s*", "", normalized)
    return normalized


def _should_skip_repetitive_dense_heading(text: str, *, level: int, repeated_count: int) -> bool:
    normalized = _normalize_text(text).lstrip("•·▪◦ ").strip()
    if not normalized:
        return True
    if normalized[:1] in {"•", "·", "▪", "◦", ""}:
        return True
    if _looks_like_truncated_heading(normalized):
        return True
    if repeated_count <= 1 or _looks_like_numbered_heading(normalized):
        return False
    words = normalized.split()
    if level >= 3 and repeated_count >= 3:
        return True
    if repeated_count >= 8 and len(words) <= 4:
        return True
    if repeated_count >= 5 and (len(words) <= 3 or normalized.lower() in DENSE_GENERIC_TOC_LABELS):
        return True
    return False


def _looks_like_numbered_heading(text: str) -> bool:
    return bool(re.match(r"^\d+(?:\.\d+){0,3}\b", _normalize_text(text)))


def _looks_like_person_credential_heading(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized or len(normalized) > 140:
        return False
    tokens = [token.strip("()[]{}.,;:!?") for token in normalized.replace("/", " ").split()]
    tokens = [token for token in tokens if token]
    if len(tokens) < 2 or len(tokens) > 8:
        return False
    lower_tokens = [token.lower() for token in tokens]
    if any(token in DENSE_HANDBOOK_TITLE_WORDS for token in lower_tokens):
        return False
    credential_count = sum(
        1
        for token in tokens[1:]
        if token.lower() in DENSE_HANDBOOK_CREDENTIALS or (token.isupper() and 2 <= len(token) <= 6)
    )
    titlecase_count = sum(1 for token in tokens if token[:1].isupper())
    short_name_count = sum(1 for token in tokens if len(token) <= 3 and token[:1].isupper())
    return credential_count >= 1 and titlecase_count >= 2 and short_name_count <= 3


def _looks_like_dense_backmatter_heading(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    if _looks_like_person_credential_heading(normalized):
        return True
    if normalized.count(",") >= 2:
        return True
    words = [token.strip("()[]{}.,;:!?") for token in normalized.split() if token.strip("()[]{}.,;:!?")]
    if len(words) >= 4 and all(word[:1].isupper() for word in words[:4]):
        acronym_tail = sum(1 for word in words if word.isupper() and 2 <= len(word) <= 6)
        return acronym_tail >= 1
    return False


def _build_post_rebuild_toc_phase(
    *,
    toc_map: list[dict[str, Any]],
    nav_summary: dict[str, Any],
    after_scan: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    spine_order = list(after_scan.keys())
    last_position = -1
    spine_positions = {file_name: index for index, file_name in enumerate(spine_order)}
    spine_order_matches = True
    for item in toc_map:
        position = spine_positions.get(str(item.get("file", "") or ""), -1)
        if position < last_position:
            spine_order_matches = False
            break
        if position >= 0:
            last_position = position
    return {
        "status": "completed",
        "after": nav_summary,
        "toc_map": toc_map,
        "summary": {
            "entry_count": len(toc_map),
            "broken_target_count": sum(
                1 for item in toc_map if any(issue in {"missing-file", "missing-anchor"} for issue in item.get("issues", []))
            ),
            "duplicate_target_count": sum(1 for item in toc_map if "duplicate-target" in item.get("issues", [])),
            "label_mismatch_count": sum(1 for item in toc_map if "label-heading-mismatch" in item.get("issues", [])),
            "spine_order_matches": spine_order_matches,
        },
    }


def _strip_missing_landmarks(nav_path: Path, *, package_dir: Path) -> None:
    if not nav_path.exists():
        return
    soup = BeautifulSoup(nav_path.read_text(encoding="utf-8"), "xml")
    changed = False
    for nav in soup.find_all("nav"):
        epub_type = " ".join(
            str(value)
            for key, value in nav.attrs.items()
            if key in {"epub:type", "type"} or key.endswith(":type")
        ).lower()
        if "landmarks" not in epub_type:
            continue
        for item in list(nav.find_all("li")):
            anchor = item.find("a", href=True)
            if anchor is None:
                continue
            href = _normalize_text(str(anchor.get("href", "") or ""))
            file_part = href.split("#", 1)[0]
            if file_part and not (package_dir / file_part).exists():
                item.extract()
                changed = True
    if changed:
        nav_path.write_text(str(soup), encoding="utf-8")


def _filter_resolved_manual_review_items(
    items: list[dict[str, Any]],
    *,
    toc_map: list[dict[str, Any]],
    structural_phase: dict[str, Any],
) -> list[dict[str, Any]]:
    toc_issue_reasons = {issue for item in toc_map for issue in item.get("issues", [])}
    structural_issue_reasons = {
        _normalize_text(str(item.get("reason", "") or ""))
        for item in structural_phase.get("broken_internal_links", []) or []
    }
    filtered: list[dict[str, Any]] = []
    for item in items:
        phase = _normalize_text(str(item.get("phase", "") or ""))
        reason = _normalize_text(str(item.get("reason", "") or ""))
        if phase == "toc_rebuild" and reason and reason not in toc_issue_reasons:
            continue
        if phase == "structural_integrity" and reason and reason not in structural_issue_reasons:
            continue
        filtered.append(item)
    return filtered


def _bs4_tag_path(node: Tag) -> str:
    parts: list[str] = []
    current: Tag | None = node
    while current is not None and isinstance(current, Tag):
        if current.name == "[document]":
            break
        index = 1
        sibling = current.previous_sibling
        while sibling is not None:
            if isinstance(sibling, Tag) and sibling.name == current.name:
                index += 1
            sibling = sibling.previous_sibling
        parts.append(f"{current.name}[{index}]")
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return "/" + "/".join(reversed(parts))


def _neighbor_text(node: Tag, *, previous: bool) -> str:
    siblings = node.previous_siblings if previous else node.next_siblings
    for sibling in siblings:
        if not isinstance(sibling, Tag):
            continue
        text = _normalize_text(sibling.get_text(" ", strip=True))
        if text:
            return text
    return ""


def _class_list_string(node: Tag) -> str:
    classes = node.get("class", [])
    if isinstance(classes, str):
        return _normalize_text(classes)
    return " ".join(_normalize_text(str(item)) for item in classes if _normalize_text(str(item)))


def _build_heading_inventory(
    *,
    heading_decisions: list[dict[str, Any]],
    before_scan: dict[str, list[dict[str, Any]]],
    after_scan: dict[str, list[dict[str, Any]]],
    manual_review_queue: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before_remaining = {file_name: list(items) for file_name, items in before_scan.items()}
    after_remaining = {file_name: list(items) for file_name, items in after_scan.items()}
    review_keys = {
        (
            _normalize_text(str(item.get("file", "") or "")),
            _normalize_text(str(item.get("before", "") or "")),
            _normalize_text(str(item.get("reason", "") or "")),
        )
        for item in manual_review_queue
    }

    inventory: list[dict[str, Any]] = []
    for decision in heading_decisions:
        file_name = _normalize_text(str(decision.get("file", "") or ""))
        before_payload = dict((decision.get("before") or {}) or {})
        after_payload = dict((decision.get("after") or {}) or {})
        before_match = _pop_matching_candidate(before_remaining.setdefault(file_name, []), before_payload)
        after_match = _pop_matching_candidate(after_remaining.setdefault(file_name, []), after_payload)
        current = before_match or _fallback_candidate(file_name, before_payload)
        proposed = after_match or _fallback_candidate(file_name, after_payload)
        action_taken = _normalize_text(str(decision.get("status", "") or "unchanged"))
        reason = _normalize_text(str(decision.get("reason", "") or ""))
        confidence = round(float(decision.get("confidence") or 0.0), 4)
        text = _normalize_text(str((current or {}).get("text", "") or (proposed or {}).get("text", "") or ""))
        inventory.append(
            {
                "file": file_name,
                "location": (current or {}).get("location", "") or (proposed or {}).get("location", ""),
                "order": (current or {}).get("order") or (proposed or {}).get("order"),
                "current_tag": (current or {}).get("element", ""),
                "current_level": (current or {}).get("level"),
                "current_id": (current or {}).get("id", ""),
                "text": text,
                "candidate_type": (current or {}).get("candidate_type", "real" if str((current or {}).get("element", "")).startswith("h") else "pseudo"),
                "proposed_role": _proposed_role(action_taken, proposed),
                "proposed_tag": (proposed or {}).get("element", ""),
                "proposed_level": (proposed or {}).get("level"),
                "proposed_id": (proposed or {}).get("id", ""),
                "action_taken": action_taken,
                "reason": reason,
                "confidence": confidence,
                "review_flag": (
                    confidence < 0.75
                    or (file_name, _normalize_text(str(before_payload.get("text", "") or "")), reason) in review_keys
                ),
                "context_before": (current or {}).get("previous_text", ""),
                "context_after": (current or {}).get("next_text", "") or (proposed or {}).get("next_text", ""),
                "classes": (current or {}).get("classes", ""),
            }
        )

    for file_name, remaining in after_remaining.items():
        for candidate in remaining:
            if not str(candidate.get("element", "")).startswith("h"):
                continue
            inventory.append(
                {
                    "file": file_name,
                    "location": candidate.get("location", ""),
                    "order": candidate.get("order"),
                    "current_tag": "",
                    "current_level": None,
                    "current_id": "",
                    "text": candidate.get("text", ""),
                    "candidate_type": "real",
                    "proposed_role": _proposed_role("added", candidate),
                    "proposed_tag": candidate.get("element", ""),
                    "proposed_level": candidate.get("level"),
                    "proposed_id": candidate.get("id", ""),
                    "action_taken": "added",
                    "reason": "reconstructed-heading",
                    "confidence": 0.72,
                    "review_flag": True,
                    "context_before": candidate.get("previous_text", ""),
                    "context_after": candidate.get("next_text", ""),
                    "classes": candidate.get("classes", ""),
                }
            )

    return sorted(
        inventory,
        key=lambda item: (
            str(item.get("file", "") or ""),
            int(item.get("order") or 0),
            str(item.get("location", "") or ""),
        ),
    )


def _pop_matching_candidate(candidates: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any] | None:
    payload_text = _normalize_text(str(payload.get("text", "") or ""))
    payload_id = _normalize_text(str(payload.get("id", "") or ""))
    payload_element = _normalize_text(str(payload.get("element", "") or ""))
    payload_level = payload.get("level")
    best_index = None
    best_score = 0
    for index, candidate in enumerate(candidates):
        score = 0
        if payload_id and payload_id == _normalize_text(str(candidate.get("id", "") or "")):
            score += 5
        candidate_text = _normalize_text(str(candidate.get("text", "") or ""))
        if payload_text and payload_text == candidate_text:
            score += 4
        elif payload_text and candidate_text and _title_fragments_match(payload_text, candidate_text):
            score += 2
        if payload_element and payload_element == _normalize_text(str(candidate.get("element", "") or "")):
            score += 1
        if payload_level is not None and payload_level == candidate.get("level"):
            score += 1
        if score > best_score:
            best_score = score
            best_index = index
    if best_index is None or best_score <= 0:
        return None
    return candidates.pop(best_index)


def _fallback_candidate(file_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "file_name": file_name,
        "order": None,
        "element": _normalize_text(str(payload.get("element", "") or "")),
        "id": _normalize_text(str(payload.get("id", "") or "")),
        "text": _normalize_text(str(payload.get("text", "") or "")),
        "level": payload.get("level"),
        "candidate_type": "real" if str(payload.get("element", "")).startswith("h") else "pseudo",
        "location": "",
        "classes": "",
        "previous_text": "",
        "next_text": "",
    }


def _proposed_role(action_taken: str, proposed: dict[str, Any] | None) -> str:
    if action_taken == "removed":
        return "rejected"
    level = (proposed or {}).get("level")
    if level:
        return f"H{int(level)}"
    if action_taken == "added":
        return "heading"
    return "paragraph"


def _build_rejected_heading_candidates(heading_inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rejected: list[dict[str, Any]] = []
    for item in heading_inventory:
        if item.get("action_taken") != "removed":
            continue
        rejected.append(
            {
                "file": item.get("file", ""),
                "location": item.get("location", ""),
                "text": item.get("text", ""),
                "current_tag": item.get("current_tag", ""),
                "current_level": item.get("current_level"),
                "reason": item.get("reason", ""),
                "confidence": item.get("confidence", 0.0),
            }
        )
    return rejected


def _build_heading_toc_mapping(
    *,
    toc_phase: dict[str, Any],
    heading_inventory: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    toc_entries = list(toc_phase.get("toc_map") or [])
    mapping: list[dict[str, Any]] = []
    for entry in toc_entries:
        file_name = _normalize_text(str(entry.get("file", "") or ""))
        anchor = _normalize_text(str(entry.get("anchor", "") or ""))
        heading_text = _normalize_text(str(entry.get("heading_text", "") or ""))
        source_decision = "validated-heading"
        for candidate in heading_inventory:
            if candidate.get("file") != file_name:
                continue
            if anchor and candidate.get("proposed_id") != anchor:
                continue
            if heading_text and not _title_fragments_match(str(candidate.get("text", "") or ""), heading_text):
                continue
            source_decision = _normalize_text(str(candidate.get("action_taken", "") or "")) or source_decision
            break
        mapping.append(
            {
                "label": _normalize_text(str(entry.get("label", "") or "")),
                "target_file": file_name,
                "target_anchor": anchor,
                "target": _normalize_text(str(entry.get("target", "") or "")),
                "heading_text": heading_text,
                "heading_level": int(entry.get("level", 1) or 1),
                "status": _normalize_text(str(entry.get("status", "") or "pass")),
                "issues": list(entry.get("issues") or []),
                "source_of_decision": source_decision,
            }
        )
    return mapping


def _derive_release_status(
    *,
    gates: dict[str, Any],
    epubcheck: dict[str, Any],
    manual_review_queue: list[dict[str, Any]],
) -> str:
    gate_statuses = [str((gates.get(gate) or {}).get("status", "")) for gate in ("A", "C", "D", "E")]
    if epubcheck.get("status") == "failed" or any(status == "fail" for status in gate_statuses):
        return "fail"
    if manual_review_queue or any(status == "pass_with_review" for status in gate_statuses):
        return "pass_with_review"
    return "pass"


def _evaluate_heading_gate_after_rebuild(after_scan: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    blockers: list[str] = []
    for file_name, items in after_scan.items():
        if file_name == "cover.xhtml":
            continue
        h1_count = sum(1 for item in items if int(item.get("level") or 0) == 1)
        if h1_count != 1:
            blockers.append(f"{file_name} has {h1_count} H1 headings.")
        suspicious = [
            _normalize_text(str(item.get("text", "") or ""))
            for item in items
            if _is_suspicious_final_heading_text(str(item.get("text", "") or ""))
        ]
        if suspicious:
            blockers.append(f"{file_name} still contains suspicious headings: {', '.join(suspicious[:3])}")
    return _gate_result("C", blockers=blockers)


def _is_suspicious_final_heading_text(text: str) -> bool:
    normalized = _normalize_text(text)
    lowered = normalized.lower()
    if not normalized:
        return True
    if any(marker in lowered for marker in ("material sponsorowany", "materiaĹ‚ sponsorowany", "page ", "strona ", "www.")):
        return True
    if _looks_like_promotional_banner(normalized):
        return True
    if _looks_like_table_header_heading(normalized):
        return True
    if _looks_like_synthetic_section_label(normalized):
        return True
    if _looks_like_truncated_heading(normalized):
        return True
    return False


def _gate_result(
    gate_id: str,
    *,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    manual_review: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blockers = blockers or []
    warnings = warnings or []
    manual_review = manual_review or []
    if blockers:
        status = "fail"
        summary = f"Gate {gate_id} failed."
    elif warnings or manual_review:
        status = "pass_with_review"
        summary = f"Gate {gate_id} passed with review."
    else:
        status = "pass"
        summary = f"Gate {gate_id} passed."
    return {
        "gate": gate_id,
        "status": status,
        "summary": summary,
        "blockers": blockers,
        "warnings": warnings,
        "manual_review": manual_review,
    }


def _build_summary(
    *,
    heading_inventory: list[dict[str, Any]],
    rejected_candidates: list[dict[str, Any]],
    toc_mapping: list[dict[str, Any]],
    inventory_phase: dict[str, Any],
    heading_phase: dict[str, Any],
    toc_phase: dict[str, Any],
    manual_review_queue: list[dict[str, Any]],
    epubcheck: dict[str, Any],
    release_status: str,
    before_scan: dict[str, list[dict[str, Any]]],
    after_scan: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    status_counts = Counter(str(item.get("action_taken", "") or "") for item in heading_inventory)
    documents_processed = len(before_scan)
    suspicious_final_heading_count = sum(
        1
        for items in after_scan.values()
        for item in items
        if _is_suspicious_final_heading_text(str(item.get("text", "") or ""))
    )
    return {
        "documents_processed": documents_processed,
        "heading_candidates_detected": len(heading_inventory),
        "headings_kept": int(status_counts.get("kept", 0)),
        "headings_promoted": int(status_counts.get("promoted", 0)),
        "headings_releveled": int(status_counts.get("releveled", 0)),
        "headings_removed": int(status_counts.get("removed", 0)),
        "headings_added": int(status_counts.get("added", 0)),
        "rejected_candidate_count": len(rejected_candidates),
        "toc_entries_before": int(((inventory_phase.get("navigation") or {}).get("entry_count", 0)) or 0),
        "toc_entries_after": int(((toc_phase.get("summary") or {}).get("entry_count", len(toc_mapping))) or len(toc_mapping)),
        "toc_broken_target_count": int(((toc_phase.get("summary") or {}).get("broken_target_count", 0)) or 0),
        "manual_review_count": len(manual_review_queue),
        "suspicious_final_heading_count": suspicious_final_heading_count,
        "chapters_without_h1_after": sorted(
            file_name
            for file_name, counts in _per_file_heading_counts(after_scan).items()
            if counts.get("h1", 0) == 0
        ),
        "epubcheck_status": epubcheck.get("status", "unavailable"),
        "release_status": release_status,
    }


def _build_qa_payload(
    *,
    phase_report: dict[str, Any],
    toc_mapping: list[dict[str, Any]],
    summary: dict[str, Any],
    before_scan: dict[str, list[dict[str, Any]]],
    after_scan: dict[str, list[dict[str, Any]]],
    manual_review_queue: list[dict[str, Any]],
    epubcheck: dict[str, Any],
    release_status: str,
    structural_phase: dict[str, Any],
    gates: dict[str, Any],
) -> dict[str, Any]:
    return {
        "release_status": release_status,
        "baseline": {
            "toc_entry_count": int((((phase_report.get("phases") or {}).get("inventory") or {}).get("navigation") or {}).get("entry_count", 0) or 0),
            "heading_counts": _per_file_heading_counts(before_scan),
        },
        "final": {
            "toc_entry_count": summary["toc_entries_after"],
            "heading_counts": _per_file_heading_counts(after_scan),
        },
        "gates": gates,
        "toc_entries": toc_mapping,
        "manual_review_queue": manual_review_queue,
        "epubcheck": epubcheck,
        "structural_integrity": structural_phase,
    }


def _per_file_heading_counts(scan: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for file_name, items in scan.items():
        counter = Counter()
        for item in items:
            level = item.get("level")
            if level:
                counter[f"h{int(level)}"] += 1
        counts[file_name] = {"h1": int(counter.get("h1", 0)), "h2": int(counter.get("h2", 0)), "h3": int(counter.get("h3", 0))}
    return counts


def _build_heading_diff_markdown(
    *,
    heading_inventory: list[dict[str, Any]],
    toc_mapping: list[dict[str, Any]],
    summary: dict[str, Any],
) -> str:
    promoted = [item for item in heading_inventory if item.get("action_taken") == "promoted"]
    removed = [item for item in heading_inventory if item.get("action_taken") == "removed"]
    releveled = [item for item in heading_inventory if item.get("action_taken") == "releveled"]
    lines = [
        "# Heading Diff Report",
        "",
        "## Summary",
        "",
        f"- Heading candidates audited: {summary['heading_candidates_detected']}",
        f"- Removed false headings: {summary['headings_removed']}",
        f"- Promoted pseudo-headings: {summary['headings_promoted']}",
        f"- Releveled headings: {summary['headings_releveled']}",
        f"- TOC entries after rebuild: {summary['toc_entries_after']}",
    ]

    if removed:
        lines.extend(["", "## Demoted False Headings", ""])
        for item in removed[:20]:
            lines.append(
                f"- {item['file']} {item['location']}: `{item['text']}` -> removed ({item['reason']}, confidence {item['confidence']})"
            )
    if promoted:
        lines.extend(["", "## Promoted Pseudo-Headings", ""])
        for item in promoted[:20]:
            lines.append(
                f"- {item['file']} {item['location']}: `{item['text']}` -> {item['proposed_role']} ({item['reason']}, confidence {item['confidence']})"
            )
    if releveled:
        lines.extend(["", "## Releveled Headings", ""])
        for item in releveled[:20]:
            lines.append(
                f"- {item['file']} {item['location']}: `{item['text']}` -> {item['proposed_role']} ({item['reason']}, confidence {item['confidence']})"
            )
    if toc_mapping:
        lines.extend(["", "## TOC Snapshot", ""])
        for entry in toc_mapping[:20]:
            target = entry["target_anchor"]
            href = f"{entry['target_file']}#{target}" if target else entry["target_file"]
            lines.append(
                f"- L{entry['heading_level']} `{entry['label']}` -> `{href}` [{entry['status']}]"
            )
    return "\n".join(lines).strip() + "\n"


def _build_qa_markdown(qa_payload: dict[str, Any]) -> str:
    lines = [
        "# QA Report",
        "",
        f"- Release status: {qa_payload['release_status']}",
        f"- EPUBCheck: {qa_payload['epubcheck'].get('status', 'unavailable')}",
        f"- Baseline TOC entries: {qa_payload['baseline'].get('toc_entry_count', 0)}",
        f"- Final TOC entries: {qa_payload['final'].get('toc_entry_count', 0)}",
        "",
        "## Gates",
        "",
    ]
    for gate_name in ("A", "B", "C", "D", "E", "F"):
        gate = (qa_payload.get("gates") or {}).get(gate_name) or {}
        lines.append(f"- Gate {gate_name}: {gate.get('status', 'unavailable')}")
        for blocker in gate.get("blockers", [])[:10]:
            lines.append(f"  - blocker: {blocker}")
        for warning in gate.get("warnings", [])[:10]:
            lines.append(f"  - warning: {warning}")

    lines.extend(["", "## Heading Counts", ""])
    for file_name, counts in sorted((qa_payload.get("final") or {}).get("heading_counts", {}).items()):
        lines.append(f"- {file_name}: H1={counts['h1']}, H2={counts['h2']}, H3={counts['h3']}")

    manual_review_queue = list(qa_payload.get("manual_review_queue") or [])
    lines.extend(["", "## Manual Review Queue", ""])
    if not manual_review_queue:
        lines.append("- No unresolved heading/TOC ambiguities.")
    else:
        for item in manual_review_queue[:25]:
            lines.append(
                f"- {item.get('phase', '')} | {item.get('file', '')} | {item.get('reason', '')} | confidence={item.get('confidence', 0.0)}"
            )

    epubcheck_messages = list((qa_payload.get("epubcheck") or {}).get("messages") or [])
    lines.extend(["", "## EPUBCheck", ""])
    if not epubcheck_messages:
        lines.append("- No EPUBCheck messages.")
    else:
        for message in epubcheck_messages[:25]:
            lines.append(f"- {message}")

    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair EPUB heading hierarchy and rebuild navigation TOC.")
    parser.add_argument("epub_path", help="Input EPUB path")
    parser.add_argument("--output-dir", default="output", help="Output directory for repaired EPUB")
    parser.add_argument("--reports-dir", default="reports", help="Directory for JSON/Markdown reports")
    parser.add_argument("--title", default="", help="Optional title hint")
    parser.add_argument("--author", default="", help="Optional author hint")
    parser.add_argument("--language", default="", help="Optional language hint, e.g. pl or en")
    parser.add_argument("--publication-profile", default="", help="Optional publication profile hint")
    args = parser.parse_args()

    source_path = Path(args.epub_path).resolve()
    if not source_path.exists():
        raise SystemExit(f"Input EPUB not found: {source_path}")

    result = run_heading_repair_pipeline(
        source_path,
        output_dir=Path(args.output_dir),
        reports_dir=Path(args.reports_dir),
        title_hint=args.title,
        author_hint=args.author,
        language_hint=args.language,
        publication_profile=args.publication_profile or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
