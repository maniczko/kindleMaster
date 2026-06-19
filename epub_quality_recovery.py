from __future__ import annotations

import argparse
import io
import json
import re
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree

from kindle_semantic_cleanup import (
    NS,
    XHTML_NS,
    _audit_diagram_presentation,
    _collect_repeated_short_texts,
    _detect_cleanup_scope,
    _extract_author_from_chapters,
    _extract_description_from_chapters,
    _extract_epub,
    _extract_magazine_issue_outline,
    _get_spine_xhtml_paths,
    _inject_problem_solution_links,
    _build_non_linear_reachability_toc_entries,
    finalize_epub_for_kindle,
    _is_placeholder_author,
    _is_pre_paginated,
    _locate_opf,
    _normalize_cover_page,
    _pack_epub,
    _process_chapter,
    _repair_generic_package,
    _repair_magazine_package,
    _repair_training_book_package,
    _reorder_opf_spine,
    _resolve_publication_language,
    _rewrite_navigation,
    _rewrite_solution_backlinks,
    _strip_english_output_polish_structural_dom_artifacts,
    _strip_unresolved_fragment_links,
    _synchronize_xhtml_language,
    _title_fragments_match,
    _update_opf_metadata,
    _write_default_css,
    _looks_technical_title,
)
from epub_premium_scoring import score_epub_premium_quality
from epub_quality_selection import select_epub_by_quality
from premium_tools import run_epubcheck
from quality_report_markdown import (
    build_manual_review_markdown,
    build_recovery_release_report_markdown,
)
from quality_reporting import (
    build_epubcheck_payload,
    build_failed_gate,
    build_gate_result,
    build_heading_report_payload,
    build_metadata_diff,
    build_recovery_metadata_payload,
    build_recovery_release_summary,
    build_recovery_structural_payload,
    build_recovery_toc_payload,
    compare_heading_snapshots,
    dedupe_review_items,
    heading_change_reason,
    is_suspicious_heading,
    make_review_item,
    summarize_heading_decisions,
    summarize_inventory,
)


PLACEHOLDER_TITLE_MARKERS = {"unknown", "untitled", "executive summary", "python-docx", "emvc"}
SUSPICIOUS_HEADING_MARKERS = (
    "material sponsorowany",
    "materiał sponsorowany",
    "page ",
    "strona ",
    "www.",
)
UNKNOWN_AUTHOR_FALLBACK = "Unknown Author"


@dataclass
class RecoveryPaths:
    output_dir: Path
    reports_dir: Path
    final_epub: Path
    metadata_diff: Path
    heading_decisions: Path
    toc_map: Path
    structural_integrity: Path
    epubcheck: Path
    premium_scoring: Path
    quality_selection: Path
    stage_report: Path
    release_report: Path
    manual_review_queue: Path


def run_epub_publishing_quality_recovery(
    epub_path: str | Path,
    *,
    output_dir: str | Path = "output",
    reports_dir: str | Path = "reports",
    expected_title: str = "",
    expected_author: str = "",
    expected_description: str = "",
    expected_language: str = "",
    publication_profile: str | None = None,
    strict_premium: bool = False,
) -> dict[str, Any]:
    source_path = Path(epub_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    paths = _prepare_output_paths(output_dir=Path(output_dir), reports_dir=Path(reports_dir))
    audit_started = time.perf_counter()
    completed_stages: list[str] = []
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="read_source")
    original_bytes = source_path.read_bytes()
    original_inventory = _inventory_epub(original_bytes, label="baseline")
    completed_stages.append("baseline_inventory")
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="epubcheck_baseline")
    baseline_epubcheck = run_epubcheck(original_bytes)
    completed_stages.append("epubcheck_baseline")
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="gate_inventory")
    gates: dict[str, dict[str, Any]] = {}
    manual_review: list[dict[str, Any]] = []
    quality_selection_stages: list[dict[str, Any]] = []

    gates["A"] = _evaluate_gate_a(original_inventory)
    manual_review.extend(gates["A"].get("manual_review", []))
    completed_stages.append("gate_inventory")
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="metadata_phase")

    working_bytes = original_bytes
    metadata_diff: dict[str, Any] = {"before": original_inventory["metadata"], "after": original_inventory["metadata"], "changes": []}
    heading_report: dict[str, Any] = {"summary": {}, "decisions": [], "manual_review": []}
    toc_report: dict[str, Any] = original_inventory["toc"]
    structural_report: dict[str, Any] = original_inventory["structural_integrity"]
    final_inventory = original_inventory
    metadata_epubcheck = {"status": "unavailable", "tool": "epubcheck", "messages": []}
    final_epubcheck = {"status": "unavailable", "tool": "epubcheck", "messages": []}

    if gates["A"]["status"] != "fail":
        metadata_candidate_bytes, attempted_metadata_diff, metadata_candidate_epubcheck = _run_metadata_phase(
            working_bytes,
            source_path=source_path,
            expected_title=expected_title,
            expected_author=expected_author,
            expected_description=expected_description,
            expected_language=expected_language,
            publication_profile=publication_profile,
        )
        metadata_selection = select_epub_by_quality(
            original_bytes,
            metadata_candidate_bytes,
            baseline_label="source",
            candidate_label="post_metadata",
            baseline_epubcheck=baseline_epubcheck,
            candidate_epubcheck=metadata_candidate_epubcheck,
        )
        metadata_selection_report = {**metadata_selection.report, "phase": "metadata"}
        quality_selection_stages.append(metadata_selection_report)
        working_bytes = metadata_selection.selected_bytes
        metadata_epubcheck = metadata_selection.selected_epubcheck
        metadata_after_inventory = _inventory_epub(working_bytes, label="post_metadata")
        if metadata_selection.report["status"] == "rejected":
            metadata_diff = {
                "before": original_inventory["metadata"],
                "after": metadata_after_inventory["metadata"],
                "changes": [],
                "rejected_changes": attempted_metadata_diff.get("changes", []),
                "quality_selection": metadata_selection_report,
            }
            manual_review.append(
                _make_review_item(
                    "quality_selection",
                    "package",
                    "Metadata phase rejected because it reduced EPUB quality.",
                    "metadata-quality-regression",
                    0.86,
                )
            )
        else:
            metadata_diff = attempted_metadata_diff
        gates["B"] = _evaluate_gate_b(
            before=original_inventory,
            after=metadata_after_inventory,
            metadata_diff=metadata_diff,
            epubcheck=metadata_epubcheck,
            baseline_epubcheck=baseline_epubcheck,
        )
        manual_review.extend(gates["B"].get("manual_review", []))
        completed_stages.append("metadata_phase")
        _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="heading_toc_recovery")

        pre_recovery_bytes = working_bytes
        pre_recovery_epubcheck = metadata_epubcheck
        (
            recovery_candidate_bytes,
            recovery_heading_report,
            recovery_toc_report,
            recovery_structural_report,
            recovery_epubcheck,
        ) = _run_recovery_phases(
            pre_recovery_bytes,
            source_path=source_path,
            expected_title=expected_title,
            expected_author=expected_author,
            expected_description=expected_description,
            expected_language=expected_language,
            publication_profile=publication_profile,
        )
        recovery_selection = select_epub_by_quality(
            pre_recovery_bytes,
            recovery_candidate_bytes,
            baseline_label="pre_recovery",
            candidate_label="recovered",
            baseline_epubcheck=pre_recovery_epubcheck,
            candidate_epubcheck=recovery_epubcheck,
        )
        recovery_selection_report = {**recovery_selection.report, "phase": "recovery"}
        quality_selection_stages.append(recovery_selection_report)
        working_bytes = recovery_selection.selected_bytes
        final_epubcheck = recovery_selection.selected_epubcheck
        final_inventory = _inventory_epub(working_bytes, label="final")
        if recovery_selection.report["status"] == "rejected":
            heading_report = _build_rejected_recovery_heading_report(
                recovery_selection_report,
                attempted_heading_report=recovery_heading_report,
            )
            toc_report = final_inventory["toc"]
            structural_report = final_inventory["structural_integrity"]
            manual_review.append(
                _make_review_item(
                    "quality_selection",
                    "final.epub",
                    "Recovery phase rejected because it reduced EPUB quality.",
                    "recovery-quality-regression",
                    0.9,
                )
            )
        else:
            heading_report = recovery_heading_report
            toc_report = recovery_toc_report
            structural_report = recovery_structural_report
        gates["C"] = _evaluate_gate_c(heading_report, final_inventory)
        gates["D"] = _evaluate_gate_d(toc_report, final_inventory)
        gates["E"] = _evaluate_gate_e(structural_report)
        manual_review.extend(heading_report.get("manual_review", []))
        manual_review.extend(gates["C"].get("manual_review", []))
        manual_review.extend(gates["D"].get("manual_review", []))
        manual_review.extend(gates["E"].get("manual_review", []))
        completed_stages.append("heading_toc_recovery")
        _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="gate_release")
    else:
        gates.setdefault("B", _failed_gate("B", "Inventory gate failed; metadata phase skipped."))

    if "C" not in gates:
        gates["C"] = _failed_gate("C", "Heading recovery skipped because an earlier gate failed.")
    if "D" not in gates:
        gates["D"] = _failed_gate("D", "TOC rebuild skipped because an earlier gate failed.")
    if "E" not in gates:
        gates["E"] = _failed_gate("E", "Structural repair skipped because an earlier gate failed.")

    gates["F"] = _evaluate_gate_f(
        gates=gates,
        final_inventory=final_inventory,
        epubcheck=final_epubcheck,
        manual_review=manual_review,
    )
    completed_stages.append("gate_release")
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="strict_premium_scoring")

    recommendation = gates["F"]["status"]
    working_bytes = working_bytes or original_bytes
    quality_selection = _build_quality_selection_report(quality_selection_stages)
    premium_scoring = score_epub_premium_quality(working_bytes, epubcheck=final_epubcheck)
    completed_stages.append("strict_premium_scoring")
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="write_reports")
    if strict_premium:
        gates["G"] = _evaluate_gate_g(premium_scoring)
        if gates["G"]["status"] == "fail":
            recommendation = "fail"
        elif recommendation == "pass" and gates["G"]["status"] == "pass_with_review":
            recommendation = "pass_with_review"

    paths.final_epub.write_bytes(working_bytes)

    metadata_payload = build_recovery_metadata_payload(
        metadata_diff=metadata_diff,
        original_metadata=original_inventory["metadata"],
        final_metadata=final_inventory["metadata"],
        gate=gates["B"],
    )
    toc_payload = build_recovery_toc_payload(toc_report=toc_report, gate=gates["D"])
    structural_payload = build_recovery_structural_payload(structural_report=structural_report, gate=gates["E"])
    epubcheck_payload = build_epubcheck_payload(
        final_epubcheck=final_epubcheck,
        metadata_phase_epubcheck=metadata_epubcheck,
    )
    release_summary = build_recovery_release_summary(
        source_epub=source_path,
        final_epub=paths.final_epub,
        recommendation=recommendation,
        gates=gates,
        original_inventory=original_inventory,
        final_inventory=final_inventory,
        baseline_epubcheck_status=baseline_epubcheck.get("status", "unavailable"),
        manual_review_count=len(manual_review),
    )

    paths.metadata_diff.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.heading_decisions.write_text(json.dumps(heading_report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.toc_map.write_text(json.dumps(toc_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.structural_integrity.write_text(json.dumps(structural_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.epubcheck.write_text(json.dumps(epubcheck_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.premium_scoring.write_text(json.dumps(premium_scoring, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.quality_selection.write_text(json.dumps(quality_selection, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.manual_review_queue.write_text(build_manual_review_markdown(manual_review), encoding="utf-8")
    release_summary["premium_scoring"] = premium_scoring
    release_summary["quality_selection"] = quality_selection
    release_summary["strict_premium"] = strict_premium
    paths.release_report.write_text(
        build_recovery_release_report_markdown(
            release_summary=release_summary,
            metadata_payload=metadata_payload,
            toc_payload=toc_payload,
        ),
        encoding="utf-8",
    )
    completed_stages.append("write_reports")
    _write_stage_report(paths, started=audit_started, completed_stages=completed_stages, current_stage="", status="completed")

    return {
        "decision": recommendation,
        "final_epub": str(paths.final_epub),
        "reports": {
            "metadata_diff": str(paths.metadata_diff),
            "heading_decisions": str(paths.heading_decisions),
            "toc_map": str(paths.toc_map),
            "structural_integrity": str(paths.structural_integrity),
            "epubcheck": str(paths.epubcheck),
            "premium_scoring": str(paths.premium_scoring),
            "quality_selection": str(paths.quality_selection),
            "stage_report": str(paths.stage_report),
            "release_report": str(paths.release_report),
            "manual_review_queue": str(paths.manual_review_queue),
        },
        "gates": gates,
        "premium_scoring": premium_scoring,
        "quality_selection": quality_selection,
    }


def _run_metadata_phase(
    epub_bytes: bytes,
    *,
    source_path: Path,
    expected_title: str,
    expected_author: str,
    expected_description: str,
    expected_language: str,
    publication_profile: str | None,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    before_inventory = _inventory_epub(epub_bytes, label="before_metadata")
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        if _is_pre_paginated(opf_path):
            return epub_bytes, {"before": before_inventory["metadata"], "after": before_inventory["metadata"], "changes": []}, run_epubcheck(epub_bytes)

        chapter_paths = _get_spine_xhtml_paths(opf_path)
        if not chapter_paths:
            return epub_bytes, {"before": before_inventory["metadata"], "after": before_inventory["metadata"], "changes": []}, run_epubcheck(epub_bytes)

        chapter_title_hint = _extract_primary_title_from_chapters(chapter_paths)
        current_title = before_inventory["metadata"]["primary"].get("title", "")
        issue_title_hint = _derive_issue_title_from_source_path(source_path)
        if not expected_title and _should_prefer_source_issue_title(
            current_title=current_title,
            source_issue_title=issue_title_hint,
        ):
            current_title = issue_title_hint
        title = _pick_metadata_value(
            requested=expected_title,
            current=current_title,
            fallback=chapter_title_hint or source_path.stem,
        )
        author = _pick_author_value(
            requested=expected_author,
            current=before_inventory["metadata"]["primary"].get("creator", ""),
            chapter_paths=chapter_paths,
        )
        language = _pick_language_value(expected_language, before_inventory["metadata"]["primary"].get("language", ""))
        description_seed = expected_description or _extract_description_from_chapters(
            chapter_paths,
            title=title,
            author=author or UNKNOWN_AUTHOR_FALLBACK,
        )
        toc_entries = before_inventory["toc"].get("entries", [])

        _update_opf_metadata(
            opf_path,
            title=title,
            author=author or UNKNOWN_AUTHOR_FALLBACK,
            language=language,
            chapter_paths=chapter_paths,
            toc_entries=toc_entries,
            description_seed=description_seed,
        )
        if _metadata_phase_should_repair_magazine_nav(
            chapter_paths,
            toc_entries=toc_entries,
            publication_profile=publication_profile,
        ):
            _repair_magazine_nav_and_safe_headings_for_audit(
                opf_path,
                chapter_paths=chapter_paths,
                language=language,
            )
        metadata_bytes = _pack_epub(root_dir)

    after_inventory = _inventory_epub(metadata_bytes, label="after_metadata")
    metadata_diff = _build_metadata_diff(before_inventory["metadata"], after_inventory["metadata"])
    for field, expected in (
        ("title", expected_title),
        ("creator", expected_author),
        ("description", expected_description),
        ("language", expected_language),
    ):
        expected = (expected or "").strip()
        if expected and after_inventory["metadata"]["primary"].get(field, "") != expected:
            metadata_diff.setdefault("conflicts", []).append(
                {
                    "field": field,
                    "before": before_inventory["metadata"]["primary"].get(field, ""),
                    "after": after_inventory["metadata"]["primary"].get(field, ""),
                    "expected": expected,
                }
            )
    return metadata_bytes, metadata_diff, run_epubcheck(metadata_bytes)


def _metadata_phase_should_repair_magazine_nav(
    chapter_paths: list[Path],
    *,
    toc_entries: list[dict[str, Any]],
    publication_profile: str | None,
) -> bool:
    profile = (publication_profile or "").strip().lower()
    if profile in {"magazine_reflow", "full_magazine", "magazine"}:
        return True
    if len(toc_entries or []) >= 20:
        return True
    try:
        issue_outline = _extract_magazine_issue_outline(chapter_paths)
    except Exception:
        return False
    return len(issue_outline.get("entries") or []) >= 8


def _repair_magazine_nav_and_safe_headings_for_audit(
    opf_path: Path,
    *,
    chapter_paths: list[Path],
    language: str,
) -> None:
    _repair_magazine_nav_labels_for_audit(opf_path, language=language)
    for chapter_path in chapter_paths:
        _promote_safe_audit_heading(chapter_path)


def _repair_magazine_nav_labels_for_audit(opf_path: Path, *, language: str) -> None:
    nav_path = opf_path.parent / "nav.xhtml"
    if not nav_path.exists():
        return
    parser = etree.XMLParser(recover=True)
    try:
        tree = etree.parse(str(nav_path), parser)
    except Exception:
        return
    toc_navs = tree.getroot().xpath(
        ".//xhtml:nav[contains(@epub:type, 'toc') or contains(@*[local-name()='type'], 'toc')]",
        namespaces={"xhtml": XHTML_NS, "epub": "http://www.idpf.org/2007/ops"},
    )
    if not toc_navs:
        return
    changed = False
    additional_index = 1
    english = (language or "").strip().lower().startswith("en")
    for anchor in list(toc_navs[0].xpath(".//xhtml:a", namespaces={"xhtml": XHTML_NS})):
        href = (anchor.get("href") or "").strip()
        label = " ".join("".join(anchor.itertext()).split())
        normalized = _normalize_audit_label(label)
        target_file, _fragment = _split_href(href)
        if normalized in {"contents", "table of contents"}:
            parent_li = anchor.getparent()
            while parent_li is not None and etree.QName(parent_li).localname != "li":
                parent_li = parent_li.getparent()
            if parent_li is not None and parent_li.getparent() is not None:
                parent_li.getparent().remove(parent_li)
                changed = True
            continue
        if english and target_file.lower().startswith("cover") and label != "Cover":
            _set_anchor_label(anchor, "Cover")
            changed = True
            continue
        if _looks_like_audit_page_label(label):
            _set_anchor_label(anchor, f"Additional Material {additional_index}")
            additional_index += 1
            changed = True
    if changed:
        tree.write(str(nav_path), encoding="utf-8", xml_declaration=True)


def _promote_safe_audit_heading(chapter_path: Path) -> None:
    parser = etree.XMLParser(recover=True)
    try:
        tree = etree.parse(str(chapter_path), parser)
    except Exception:
        return
    root = tree.getroot()
    headings = root.xpath(".//xhtml:h1|.//xhtml:h2|.//xhtml:h3", namespaces={"xhtml": XHTML_NS})
    if headings:
        return
    text = " ".join("".join(root.itertext()).split())
    if len(text) < 900:
        return
    title_nodes = root.xpath(".//xhtml:title", namespaces={"xhtml": XHTML_NS})
    title = " ".join("".join(title_nodes[0].itertext()).split()) if title_nodes else ""
    if not _looks_like_safe_audit_heading_title(title):
        return
    section_nodes = root.xpath(".//xhtml:section", namespaces={"xhtml": XHTML_NS})
    body_nodes = root.xpath(".//xhtml:body", namespaces={"xhtml": XHTML_NS})
    container = section_nodes[0] if section_nodes else (body_nodes[0] if body_nodes else root)
    first_block = None
    for child in container:
        if not isinstance(child.tag, str):
            continue
        child_text = " ".join("".join(child.itertext()).split())
        if child_text:
            first_block = child
            break
    if first_block is None:
        return
    first_text = " ".join("".join(first_block.itertext()).split())
    if _normalize_audit_label(first_text) != _normalize_audit_label(title):
        return
    first_block.tag = f"{{{XHTML_NS}}}h1"
    if not first_block.get("id"):
        first_block.set("id", _audit_slug(title) or "section")
    tree.write(str(chapter_path), encoding="utf-8", xml_declaration=True)


def _set_anchor_label(anchor: etree._Element, label: str) -> None:
    for child in list(anchor):
        anchor.remove(child)
    anchor.text = label


def _normalize_audit_label(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _looks_like_audit_page_label(value: str) -> bool:
    normalized = (value or "").strip(" .:-")
    return bool(re.fullmatch(r"\d{1,4}", normalized) or re.fullmatch(r"(?i)(?:page|strona)\s+\d{1,4}", normalized))


def _looks_like_safe_audit_heading_title(value: str) -> bool:
    normalized = " ".join((value or "").split())
    if not normalized or _looks_like_audit_page_label(normalized) or _looks_technical_title(normalized):
        return False
    words = normalized.split()
    return 2 <= len(words) <= 14 and len(normalized) <= 100 and any(character.isalpha() for character in normalized)


def _audit_slug(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "-", (value or "").lower()).strip("-")
    return slug[:80]


def _run_recovery_phases(
    epub_bytes: bytes,
    *,
    source_path: Path,
    expected_title: str,
    expected_author: str,
    expected_description: str,
    expected_language: str,
    publication_profile: str | None,
) -> tuple[bytes, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    try:
        from epub_heading_repair import repair_epub_headings_and_toc
        from text_normalization import TextCleanupConfig, clean_epub_text_package

        working_bytes = epub_bytes
        language_hint = expected_language or "en"
        semantic_cleaned = False
        try:
            cleanup_result = clean_epub_text_package(
                working_bytes,
                config=TextCleanupConfig(
                    language_hint=language_hint,
                    release_gate="soft",
                ),
                publication_profile=publication_profile,
            )
            working_bytes = cleanup_result.epub_bytes
            semantic_cleaned = True
        except Exception:
            pass

        if _should_run_magazine_semantic_cleanup(working_bytes, publication_profile=publication_profile):
            semantic_title, semantic_author, semantic_language = _metadata_hints_for_semantic_cleanup(
                working_bytes,
                expected_title=expected_title,
                expected_author=expected_author,
                expected_language=language_hint,
            )
            try:
                semantic_result = finalize_epub_for_kindle(
                    working_bytes,
                    title=semantic_title,
                    author=semantic_author,
                    language=semantic_language,
                    publication_profile=publication_profile or "magazine_reflow",
                    return_report=False,
                )
                if isinstance(semantic_result, tuple):
                    working_bytes = semantic_result[0]
                else:
                    working_bytes = semantic_result
            except Exception:
                pass

        heading_result = repair_epub_headings_and_toc(
            working_bytes,
            title_hint=expected_title,
            author_hint=expected_author,
            language_hint=language_hint,
            publication_profile=publication_profile,
            already_semantic_cleaned=semantic_cleaned,
        )
        final_bytes = heading_result.epub_bytes
        final_inventory = _inventory_epub(final_bytes, label="final")
        toc_report = {
            **final_inventory["toc"],
            "baseline_entry_count": len(_inventory_epub(epub_bytes, label="toc_baseline")["toc"].get("entries", [])),
        }
        heading_report = build_heading_report_payload(
            summary={
                **heading_result.summary,
                "removed_count": int(heading_result.summary.get("headings_removed", 0) or 0),
                "recovered_count": int(
                    (heading_result.summary.get("headings_added", 0) or 0)
                    + (heading_result.summary.get("headings_promoted", 0) or 0)
                    + (heading_result.summary.get("headings_releveled", 0) or 0)
                ),
            },
            decisions=heading_result.heading_inventory,
            manual_review=heading_result.manual_review_queue,
        )
        structural_report = final_inventory["structural_integrity"]
        return final_bytes, heading_report, toc_report, structural_report, heading_result.epubcheck
    except Exception:
        pass

    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        chapter_paths = _get_spine_xhtml_paths(opf_path)
        repeated_counts = _collect_repeated_short_texts(chapter_paths)
        keep_first_seen: set[str] = set()
        processed = {}
        solution_targets: dict[str, str] = {}
        toc_entries: list[dict[str, Any]] = []
        problem_refs_by_chapter: dict[str, list[dict[str, Any]]] = {}
        raw_author_candidate = ""
        raw_language_samples: list[str] = []
        raw_description_candidate = ""
        heading_decisions: list[dict[str, Any]] = []
        manual_review: list[dict[str, Any]] = []

        for chapter_path in chapter_paths:
            if chapter_path.name == "cover.xhtml":
                _normalize_cover_page(chapter_path, title=expected_title or source_path.stem, language=expected_language or "en")
                continue

            before_snapshot = _extract_heading_snapshot(chapter_path)
            if not raw_author_candidate:
                raw_author_candidate = _extract_author_from_chapters([chapter_path])
            if not raw_description_candidate:
                raw_description_candidate = _extract_description_from_chapters(
                    [chapter_path],
                    title=expected_title or source_path.stem,
                    author=raw_author_candidate or expected_author or UNKNOWN_AUTHOR_FALLBACK,
                )
            if len(raw_language_samples) < 6:
                raw_language_samples.append(_extract_text_sample(chapter_path))

            chapter_result = _process_chapter(
                chapter_path,
                repeated_counts=repeated_counts,
                keep_first_seen=keep_first_seen,
                title=expected_title or source_path.stem,
                author=expected_author or raw_author_candidate or UNKNOWN_AUTHOR_FALLBACK,
                language=expected_language or "en",
                publication_profile=publication_profile,
            )
            processed[chapter_path] = chapter_result
            toc_entries.extend(chapter_result.nav_entries)
            solution_targets.update(chapter_result.solution_targets)
            for ref in chapter_result.problem_refs:
                problem_refs_by_chapter.setdefault(ref["problem_file"], []).append(ref)

            after_snapshot = _extract_heading_snapshot(chapter_path)
            file_decisions, file_review = _compare_heading_snapshots(
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                file_name=chapter_path.name,
            )
            heading_decisions.extend(file_decisions)
            manual_review.extend(file_review)

        exercise_problem_targets: dict[str, str] = {}
        for problem_file, refs in problem_refs_by_chapter.items():
            for ref in refs:
                exercise_num = ref.get("exercise_num", "")
                if exercise_num and problem_file:
                    exercise_problem_targets.setdefault(exercise_num, f"{problem_file}#exercise-{exercise_num}")

        for chapter_path, chapter_result in processed.items():
            updated_xhtml = _inject_problem_solution_links(
                chapter_result.xhtml,
                chapter_name=chapter_path.name,
                solution_targets=solution_targets,
                ordered_problem_refs=problem_refs_by_chapter.get(chapter_path.name, []),
            )
            updated_xhtml = _rewrite_solution_backlinks(
                updated_xhtml,
                exercise_problem_targets=exercise_problem_targets,
            )
            chapter_path.write_text(updated_xhtml, encoding="utf-8")

        cleanup_scope = _detect_cleanup_scope(
            chapter_paths,
            title=expected_title or source_path.stem,
            publication_profile=publication_profile,
        )
        chapter_title_hint = _extract_primary_title_from_chapters(chapter_paths)
        title_hint = _pick_metadata_value(
            requested=expected_title,
            current="",
            fallback=chapter_title_hint or source_path.stem,
        )
        author_hint = expected_author or raw_author_candidate or UNKNOWN_AUTHOR_FALLBACK
        language_hint = _pick_language_value(expected_language, "en")
        if cleanup_scope == "training-book":
            package_overrides = _repair_training_book_package(
                chapter_paths,
                title=title_hint,
                author=author_hint,
                language=language_hint,
            )
        elif cleanup_scope == "magazine":
            package_overrides = _repair_magazine_package(
                chapter_paths,
                title=title_hint,
                author=author_hint,
                language=language_hint,
            )
        else:
            package_overrides = _repair_generic_package(
                chapter_paths,
                title=title_hint,
                author=author_hint,
                language=language_hint,
                toc_entries=toc_entries,
                cleanup_scope=cleanup_scope,
            )

        resolved_title = _pick_metadata_value(
            requested=expected_title,
            current=str(package_overrides.get("title") or title_hint),
            fallback=source_path.stem,
        )
        resolved_author = _pick_author_value(
            requested=expected_author,
            current=str(package_overrides.get("author") or author_hint),
            chapter_paths=chapter_paths,
        )
        resolved_language = _resolve_publication_language(
            _pick_language_value(expected_language, str(package_overrides.get("language") or language_hint)),
            samples=raw_language_samples,
        )
        resolved_toc_entries = list(package_overrides.get("toc_entries") or toc_entries)
        spine_order = list(package_overrides.get("spine_order") or [])
        non_linear_spine_files = {
            str(name)
            for name in (package_overrides.get("non_linear_spine_files") or [])
            if str(name)
        }
        if not spine_order:
            spine_order = [
                path.name
                for path in chapter_paths
                if path.name != "cover.xhtml" and path.name not in non_linear_spine_files
            ]
        if non_linear_spine_files:
            resolved_toc_entries = [
                entry
                for entry in resolved_toc_entries
                if str(entry.get("file_name") or "") not in non_linear_spine_files
            ]
            resolved_toc_entries.extend(
                _build_non_linear_reachability_toc_entries(
                    chapter_paths,
                    non_linear_files=non_linear_spine_files,
                    language=resolved_language,
                    parent_file=spine_order[0] if spine_order else "",
                )
            )

        for chapter_path in chapter_paths:
            if chapter_path.name != "cover.xhtml":
                _strip_english_output_polish_structural_dom_artifacts(chapter_path, language=resolved_language)
        _strip_unresolved_fragment_links(chapter_paths)
        _audit_diagram_presentation(opf_path.parent, language=resolved_language)
        _write_default_css(root_dir)
        _update_opf_metadata(
            opf_path,
            title=resolved_title,
            author=resolved_author,
            language=resolved_language,
            chapter_paths=chapter_paths,
            toc_entries=resolved_toc_entries,
            description_seed=expected_description or raw_description_candidate,
        )
        _rewrite_navigation(root_dir, opf_path, toc_entries=resolved_toc_entries, title=resolved_title, language=resolved_language)
        _synchronize_xhtml_language(opf_path.parent, language=resolved_language)
        _reorder_opf_spine(opf_path, spine_order, non_linear_files=non_linear_spine_files)
        final_bytes = _pack_epub(root_dir)

    final_inventory = _inventory_epub(final_bytes, label="final")
    toc_report = {
        **final_inventory["toc"],
        "baseline_entry_count": len(_inventory_epub(epub_bytes, label="toc_baseline")["toc"].get("entries", [])),
    }
    heading_report = build_heading_report_payload(
        summary=_summarize_heading_decisions(heading_decisions, final_inventory),
        decisions=heading_decisions,
        manual_review=manual_review,
    )
    structural_report = final_inventory["structural_integrity"]
    return final_bytes, heading_report, toc_report, structural_report, run_epubcheck(final_bytes)


def _should_run_magazine_semantic_cleanup(epub_bytes: bytes, *, publication_profile: str | None) -> bool:
    profile = (publication_profile or "").strip().lower()
    if profile in {"magazine_reflow", "full_magazine", "magazine"}:
        return True
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root_dir = Path(temp_dir)
            _extract_epub(epub_bytes, root_dir)
            opf_path = _locate_opf(root_dir)
            chapter_paths = _get_spine_xhtml_paths(opf_path)
            issue_outline = _extract_magazine_issue_outline(chapter_paths)
    except Exception:
        return False
    return len(issue_outline.get("entries") or []) >= 8


def _metadata_hints_for_semantic_cleanup(
    epub_bytes: bytes,
    *,
    expected_title: str,
    expected_author: str,
    expected_language: str,
) -> tuple[str, str, str]:
    try:
        inventory = _inventory_epub(epub_bytes, label="pre_semantic_cleanup")
    except Exception:
        return expected_title, expected_author, expected_language or "en"
    primary = inventory.get("metadata", {}).get("primary", {})
    title = expected_title or str(primary.get("title", "") or "")
    author = expected_author or str(primary.get("creator", "") or "")
    language = expected_language or str(primary.get("language", "") or "") or "en"
    return title, author, language


def _build_rejected_recovery_heading_report(
    selection_report: dict[str, Any],
    *,
    attempted_heading_report: dict[str, Any],
) -> dict[str, Any]:
    attempted_summary = dict(attempted_heading_report.get("summary") or {})
    return build_heading_report_payload(
        summary={
            "status": "rejected",
            "release_status": "pass_with_review",
            "removed_count": 0,
            "recovered_count": 0,
            "attempted_removed_count": int(attempted_summary.get("removed_count", 0) or 0),
            "attempted_recovered_count": int(attempted_summary.get("recovered_count", 0) or 0),
            "quality_selection_status": "rejected",
            "quality_selection_reason": "recovery_rejected_due_to_quality_regression",
        },
        decisions=[],
        manual_review=[
            _make_review_item(
                "quality_selection",
                "final.epub",
                ", ".join(selection_report.get("reason_codes", []) or []),
                "recovery-quality-regression",
                0.9,
            )
        ],
    )


def _build_quality_selection_report(stages: list[dict[str, Any]]) -> dict[str, Any]:
    if not stages:
        return {
            "status": "not_applied",
            "selected_candidate": "",
            "rejected_candidate": "",
            "selected_stage": "",
            "rejected_stage": "",
            "selected_score": None,
            "rejected_score": None,
            "reason_codes": [],
            "stages": [],
        }
    rejected_stages = [stage for stage in stages if stage.get("status") == "rejected"]
    representative = rejected_stages[-1] if rejected_stages else stages[-1]
    reason_codes: list[str] = []
    reason_source = rejected_stages if rejected_stages else stages
    for stage in reason_source:
        reason_codes.extend(str(code) for code in (stage.get("reason_codes") or []) if code)
    selected_candidate = str(representative.get("selected_candidate") or representative.get("selected_stage") or "")
    rejected_candidate = str(representative.get("rejected_candidate") or representative.get("rejected_stage") or "")
    return {
        **representative,
        "status": "rejected" if rejected_stages else "accepted",
        "selected_score": _candidate_score_for_label(representative, selected_candidate),
        "rejected_score": _candidate_score_for_label(representative, rejected_candidate),
        "reason_codes": _dedupe_texts(reason_codes),
        "stages": stages,
    }


def _candidate_score_for_label(stage: dict[str, Any], label: str) -> float | None:
    if not label:
        return None
    for candidate in stage.get("candidates", []) or []:
        if str(candidate.get("label") or "") == label:
            try:
                return float(candidate.get("premium_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                return None
    if label == stage.get("selected_candidate") and stage.get("selected_is_candidate"):
        return _float_or_none(stage.get("candidate_score"))
    if label == stage.get("selected_candidate"):
        return _float_or_none(stage.get("baseline_score"))
    if label == stage.get("rejected_candidate"):
        return _float_or_none(stage.get("candidate_score"))
    return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _write_stage_report(
    paths: RecoveryPaths,
    *,
    started: float,
    completed_stages: list[str],
    current_stage: str,
    status: str = "running",
) -> None:
    payload = {
        "status": status,
        "completed_stages": list(completed_stages),
        "current_stage": current_stage,
        "last_completed_stage": completed_stages[-1] if completed_stages else "",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    try:
        paths.stage_report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _prepare_output_paths(*, output_dir: Path, reports_dir: Path) -> RecoveryPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    return RecoveryPaths(
        output_dir=output_dir,
        reports_dir=reports_dir,
        final_epub=output_dir / "final.epub",
        metadata_diff=reports_dir / "metadata_diff.json",
        heading_decisions=reports_dir / "heading_decisions.json",
        toc_map=reports_dir / "toc_map.json",
        structural_integrity=reports_dir / "structural_integrity.json",
        epubcheck=reports_dir / "epubcheck.json",
        premium_scoring=reports_dir / "premium_scoring.json",
        quality_selection=reports_dir / "quality_selection.json",
        stage_report=reports_dir / "release_audit_stage_report.json",
        release_report=reports_dir / "release_report.md",
        manual_review_queue=reports_dir / "manual_review_queue.md",
    )


def _inventory_epub(epub_bytes: bytes, *, label: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root_dir = Path(temp_dir)
        _extract_epub(epub_bytes, root_dir)
        opf_path = _locate_opf(root_dir)
        metadata = _read_metadata_snapshot(opf_path)
        spine_paths = _get_spine_xhtml_paths(opf_path)
        all_spine_paths = _get_all_spine_xhtml_paths(opf_path)
        spine_files = [path.name for path in spine_paths]
        all_spine_files = [path.name for path in all_spine_paths]
        headings = {path.name: _extract_heading_snapshot(path) for path in spine_paths}
        toc_headings = {path.name: _extract_heading_snapshot(path) for path in all_spine_paths}
        toc = _inspect_toc(opf_path, spine_files=all_spine_files)
        structural = _inspect_structural_integrity(opf_path, spine_paths=spine_paths, toc=toc)
        return {
            "label": label,
            "metadata": metadata,
            "spine_files": spine_files,
            "all_spine_files": all_spine_files,
            "headings": headings,
            "toc_headings": toc_headings,
            "toc": toc,
            "structural_integrity": structural,
        }


def _get_all_spine_xhtml_paths(opf_path: Path) -> list[Path]:
    root = etree.parse(str(opf_path)).getroot()
    manifest_by_id = {
        item.get("id"): item
        for item in root.findall(".//opf:manifest/opf:item", namespaces=NS)
        if item.get("id")
    }
    paths: list[Path] = []
    for itemref in root.findall(".//opf:spine/opf:itemref", namespaces=NS):
        manifest_item = manifest_by_id.get(itemref.get("idref"))
        if manifest_item is None:
            continue
        href = manifest_item.get("href") or ""
        media_type = manifest_item.get("media-type") or ""
        if media_type != "application/xhtml+xml" or href.endswith("nav.xhtml"):
            continue
        paths.append((opf_path.parent / href).resolve())
    return paths


def _read_metadata_snapshot(opf_path: Path) -> dict[str, Any]:
    root = etree.parse(str(opf_path)).getroot()
    titles = [text.strip() for text in root.xpath(".//dc:title/text()", namespaces=NS) if text and text.strip()]
    creators = [text.strip() for text in root.xpath(".//dc:creator/text()", namespaces=NS) if text and text.strip()]
    descriptions = [text.strip() for text in root.xpath(".//dc:description/text()", namespaces=NS) if text and text.strip()]
    languages = [text.strip() for text in root.xpath(".//dc:language/text()", namespaces=NS) if text and text.strip()]
    identifiers = [text.strip() for text in root.xpath(".//dc:identifier/text()", namespaces=NS) if text and text.strip()]
    modified_nodes = root.xpath(".//opf:meta[@property='dcterms:modified']/text()", namespaces=NS)
    return {
        "primary": {
            "title": titles[0] if titles else "",
            "creator": creators[0] if creators else "",
            "description": descriptions[0] if descriptions else "",
            "language": languages[0] if languages else "",
            "identifier": identifiers[0] if identifiers else "",
            "modified": modified_nodes[0].strip() if modified_nodes and modified_nodes[0] else "",
        },
        "all": {
            "titles": titles,
            "creators": creators,
            "descriptions": descriptions,
            "languages": languages,
            "identifiers": identifiers,
            "modified": [text.strip() for text in modified_nodes if text and text.strip()],
        },
    }


def _extract_heading_snapshot(chapter_path: Path) -> list[dict[str, Any]]:
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(chapter_path), parser)
    root = tree.getroot()
    headings: list[dict[str, Any]] = []
    for index, element in enumerate(root.xpath(".//xhtml:h1|.//xhtml:h2|.//xhtml:h3", namespaces={"xhtml": XHTML_NS}), start=1):
        text = " ".join("".join(element.itertext()).split())
        headings.append(
            {
                "file": chapter_path.name,
                "tag": etree.QName(element).localname,
                "level": int(etree.QName(element).localname[1]),
                "id": element.get("id", ""),
                "text": text,
                "order": index,
                "xpath": tree.getpath(element),
            }
        )
    return headings


def _inspect_toc(opf_path: Path, *, spine_files: list[str]) -> dict[str, Any]:
    nav_path = opf_path.parent / "nav.xhtml"
    if not nav_path.exists():
        return {"entries": [], "warnings": ["Missing nav.xhtml"], "toc_nav_count": 0}

    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(nav_path), parser)
    root = tree.getroot()
    navs = root.xpath(
        ".//xhtml:nav[contains(@epub:type, 'toc') or contains(@*[local-name()='type'], 'toc')]",
        namespaces={"xhtml": XHTML_NS, "epub": "http://www.idpf.org/2007/ops"},
    )
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    if len(navs) != 1:
        warnings.append(f"Expected exactly one toc nav, found {len(navs)}.")
    if navs:
        toc_nav = navs[0]
        for order, anchor in enumerate(toc_nav.xpath(".//xhtml:ol//xhtml:a", namespaces={"xhtml": XHTML_NS}), start=1):
            href = (anchor.get("href") or "").strip()
            label = " ".join("".join(anchor.itertext()).split())
            file_name, fragment = _split_href(href)
            entries.append(
                {
                    "order": order,
                    "label": label,
                    "href": href,
                    "file": file_name,
                    "anchor": fragment,
                    "spine_index": spine_files.index(file_name) if file_name in spine_files else -1,
                }
            )

    duplicate_labels = [label for label, count in Counter(entry["label"] for entry in entries if entry["label"]).items() if count > 1]
    if duplicate_labels:
        warnings.append(f"Duplicate TOC labels: {', '.join(sorted(duplicate_labels)[:10])}")
    return {
        "entries": entries,
        "warnings": warnings,
        "toc_nav_count": len(navs),
    }


def _inspect_structural_integrity(opf_path: Path, *, spine_paths: list[Path], toc: dict[str, Any]) -> dict[str, Any]:
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    manifest_items = root.findall(".//opf:manifest/opf:item", namespaces=NS)
    spine_itemrefs = root.findall(".//opf:spine/opf:itemref", namespaces=NS)
    manifest_by_href = {item.get("href", ""): item for item in manifest_items if item.get("href")}
    manifest_by_id = {item.get("id", ""): item for item in manifest_items if item.get("id")}
    href_by_id = {item.get("id", ""): item.get("href", "") for item in manifest_items if item.get("id")}
    duplicate_ids: list[dict[str, Any]] = []
    broken_refs: list[dict[str, Any]] = []
    id_index: dict[str, set[str]] = {}

    for chapter_path in spine_paths:
        chapter_tree = etree.parse(str(chapter_path), parser)
        seen: set[str] = set()
        chapter_dupes: list[str] = []
        for element in chapter_tree.getroot().xpath(".//*[@id]"):
            element_id = element.get("id", "")
            if not element_id:
                continue
            if element_id in seen:
                chapter_dupes.append(element_id)
            seen.add(element_id)
        if chapter_dupes:
            duplicate_ids.append({"file": chapter_path.name, "ids": sorted(set(chapter_dupes))})
        id_index[chapter_path.name] = seen

    for chapter_path in spine_paths:
        chapter_tree = etree.parse(str(chapter_path), parser)
        root_element = chapter_tree.getroot()
        for element in root_element.xpath(".//*[@href]"):
            local_name = etree.QName(element).localname if isinstance(element.tag, str) else ""
            if local_name == "link":
                continue
            href = (element.get("href") or "").strip()
            if not href or "://" in href or href.startswith("mailto:"):
                continue
            target_file, fragment = _split_href(href, current_file=chapter_path.name)
            target_path = (chapter_path.parent / target_file).resolve()
            if not target_path.exists():
                broken_refs.append({"file": chapter_path.name, "href": href, "reason": "missing-target-file"})
                continue
            if fragment and fragment not in id_index.get(target_path.name, set()):
                broken_refs.append({"file": chapter_path.name, "href": href, "reason": "missing-target-anchor"})
        for element in root_element.xpath(".//*[@aria-labelledby]"):
            for label_id in (element.get("aria-labelledby") or "").split():
                if label_id and label_id not in id_index.get(chapter_path.name, set()):
                    broken_refs.append({"file": chapter_path.name, "href": f"#{label_id}", "reason": "missing-aria-labelledby"})

    spine_files = [href_by_id.get(itemref.get("idref", ""), "") for itemref in spine_itemrefs if itemref.get("idref")]
    nav_items = [item for item in manifest_items if "nav" in (item.get("properties") or "").split()]
    missing_from_manifest = [path.name for path in spine_paths if path.name not in manifest_by_href]

    toc_out_of_order = False
    last_index = -1
    for entry in toc.get("entries", []):
        spine_index = entry.get("spine_index", -1)
        if spine_index < last_index:
            toc_out_of_order = True
            break
        if spine_index >= 0:
            last_index = spine_index

    return {
        "manifest_count": len(manifest_items),
        "spine_count": len(spine_itemrefs),
        "nav_item_count": len(nav_items),
        "missing_from_manifest": missing_from_manifest,
        "duplicate_ids": duplicate_ids,
        "broken_references": broken_refs,
        "spine_files": spine_files,
        "nav_present": bool(nav_items),
        "toc_out_of_order": toc_out_of_order,
    }


def _compare_heading_snapshots(
    *,
    before_snapshot: list[dict[str, Any]],
    after_snapshot: list[dict[str, Any]],
    file_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return compare_heading_snapshots(
        before_snapshot=before_snapshot,
        after_snapshot=after_snapshot,
        file_name=file_name,
    )


def _heading_change_reason(text: str, *, removed: bool) -> tuple[str, float]:
    return heading_change_reason(text, removed=removed)


def _build_metadata_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return build_metadata_diff(before, after)


def _evaluate_gate_a(inventory: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    structural = inventory["structural_integrity"]
    if not inventory["metadata"]["all"]["identifiers"]:
        blockers.append("Package document missing dc:identifier.")
    if inventory["toc"].get("toc_nav_count", 0) == 0:
        blockers.append("Navigation document missing toc nav.")
    if not inventory["spine_files"]:
        blockers.append("Spine is empty.")
    return _gate_result("A", blockers=blockers, warnings=inventory["toc"].get("warnings", []))


def _evaluate_gate_b(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    metadata_diff: dict[str, Any],
    epubcheck: dict[str, Any],
    baseline_epubcheck: dict[str, Any],
) -> dict[str, Any]:
    blockers = []
    warnings = []
    review = []
    primary = after["metadata"]["primary"]
    if _is_placeholder_title(primary.get("title", "")):
        blockers.append("Primary dc:title is a placeholder or technical label.")
    if _is_placeholder_author(primary.get("creator", "")):
        blockers.append("Primary dc:creator is a placeholder.")
    if not _looks_like_utc_modified(primary.get("modified", "")):
        blockers.append("dcterms:modified missing or invalid.")
    if epubcheck.get("status") == "failed" and baseline_epubcheck.get("status") != "failed":
        blockers.append("Metadata-only package failed EPUBCheck.")
    elif epubcheck.get("status") == "failed" and baseline_epubcheck.get("status") == "failed":
        warnings.append("Metadata phase inherited pre-existing EPUBCheck issues from baseline.")
    if metadata_diff.get("conflicts"):
        warnings.append("Metadata conflicts detected between previous and repaired values.")
        for conflict in metadata_diff["conflicts"]:
            review.append(_make_review_item("metadata", "package", conflict["field"], "metadata-conflict", 0.66))
    return _gate_result("B", blockers=blockers, warnings=warnings, manual_review=review)


def _evaluate_gate_c(heading_report: dict[str, Any], final_inventory: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    warnings = []
    review = list(heading_report.get("manual_review", []))
    content_file_count = 0
    heading_count = 0
    for file_name, headings in final_inventory["headings"].items():
        if file_name == "cover.xhtml":
            continue
        content_file_count += 1
        heading_count += len(headings)
        h1_count = sum(1 for heading in headings if heading["level"] == 1)
        if h1_count > 1:
            warnings.append(f"{file_name} has {h1_count} H1 headings; review hierarchy.")
        elif h1_count == 0:
            has_substructure = any(int(heading.get("level", 0) or 0) in {2, 3} for heading in headings)
            if has_substructure:
                warnings.append(f"{file_name} is a continuation file without its own H1.")
            else:
                warnings.append(f"{file_name} has no heading elements; review as a continuation file.")
        suspicious = [heading["text"] for heading in headings if _is_suspicious_heading(heading["text"])]
        if suspicious:
            blockers.append(f"{file_name} still contains suspicious headings: {', '.join(suspicious[:3])}")
    if content_file_count and heading_count == 0:
        blockers.append("No heading structure detected in content documents.")
    if heading_report.get("summary", {}).get("removed_count", 0) == 0 and heading_report.get("summary", {}).get("recovered_count", 0) == 0:
        warnings.append("No heading changes detected; verify whether recovery was needed.")
    return _gate_result("C", blockers=blockers, warnings=warnings, manual_review=review)


def _evaluate_gate_d(toc_report: dict[str, Any], final_inventory: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    warnings = list(toc_report.get("warnings", []))
    review = []
    if toc_report.get("toc_nav_count") != 1:
        blockers.append("TOC nav count is not exactly one.")
    for entry in toc_report.get("entries", []):
        if entry.get("spine_index", -1) < 0:
            blockers.append(f"TOC entry points outside spine: {entry.get('href')}")
        target_heading_map = final_inventory.get("toc_headings") or final_inventory["headings"]
        target_headings = target_heading_map.get(entry.get("file", ""), [])
        if entry.get("anchor") and entry["anchor"] not in {heading.get("id") for heading in target_headings}:
            blockers.append(f"TOC entry points to missing anchor: {entry.get('href')}")
    if final_inventory["structural_integrity"].get("toc_out_of_order"):
        blockers.append("TOC order is inconsistent with spine.")
    duplicate_labels = [label for label, count in Counter(item["label"] for item in toc_report.get("entries", []) if item.get("label")).items() if count > 1]
    for label in duplicate_labels:
        review.append(_make_review_item("toc", "nav.xhtml", label, "duplicate-toc-label", 0.63))
    return _gate_result("D", blockers=blockers, warnings=warnings, manual_review=review)


def _evaluate_gate_e(structural_report: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    warnings = []
    if structural_report.get("missing_from_manifest"):
        blockers.append("Some spine files are missing from manifest.")
    if not structural_report.get("nav_present"):
        blockers.append("Manifest does not expose nav document.")
    if structural_report.get("duplicate_ids"):
        blockers.append("Duplicate IDs detected in XHTML documents.")
    if structural_report.get("broken_references"):
        blockers.append("Broken internal references detected.")
    if structural_report.get("toc_out_of_order"):
        warnings.append("TOC order differs from spine order.")
    return _gate_result("E", blockers=blockers, warnings=warnings)


def _evaluate_gate_f(
    *,
    gates: dict[str, dict[str, Any]],
    final_inventory: dict[str, Any],
    epubcheck: dict[str, Any],
    manual_review: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = []
    warnings = []
    if any(gates.get(gate, {}).get("status") == "fail" for gate in ("A", "B", "C", "D", "E")):
        blockers.append("One or more earlier gates failed.")
    if epubcheck.get("status") == "failed":
        blockers.append("Final EPUBCheck failed.")
    primary = final_inventory["metadata"]["primary"]
    if _is_placeholder_title(primary.get("title", "")):
        blockers.append("Reader title would still show a placeholder.")
    if _is_placeholder_author(primary.get("creator", "")):
        blockers.append("Reader author would still show a placeholder.")
    if manual_review:
        warnings.append("Manual review queue is not empty.")
    result = _gate_result("F", blockers=blockers, warnings=warnings, manual_review=manual_review)
    if result["status"] == "pass" and manual_review:
        result["status"] = "pass_with_review"
        result["summary"] = "Technical gates passed, but manual review items remain."
    return result


def _evaluate_gate_g(premium_scoring: dict[str, Any]) -> dict[str, Any]:
    blockers = []
    warnings = []
    review = []
    if not premium_scoring.get("technical_valid"):
        blockers.append("Strict premium scoring: technical validity is not clean.")
    if premium_scoring.get("mail_sendable") == "no":
        blockers.append("Strict premium scoring: EPUB is not safe for Send to Kindle delivery limits.")
    if not premium_scoring.get("kindle_ready"):
        blockers.append("Strict premium scoring: EPUB is not Kindle-ready quality.")
    if float(premium_scoring.get("premium_score", 0.0) or 0.0) < 7.0:
        blockers.append(f"Strict premium score is below release threshold: {premium_scoring.get('premium_score', 0)}/10.")

    for issue in premium_scoring.get("issues", []) or []:
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "") or "").lower()
        message = str(issue.get("message", "") or "").strip()
        code = str(issue.get("code", "") or "premium-scoring").strip()
        if not message:
            continue
        if severity == "blocker":
            blockers.append(f"{code}: {message}")
        elif severity == "warning":
            warnings.append(f"{code}: {message}")
        else:
            review.append(_make_review_item("premium_scoring", str(issue.get("file", "") or "epub"), code, "strict-premium-review", 0.7))

    return _gate_result("G", blockers=blockers, warnings=warnings, manual_review=review)


def _gate_result(
    gate_id: str,
    *,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    manual_review: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_gate_result(
        gate_id,
        blockers=blockers,
        warnings=warnings,
        manual_review=manual_review,
    )


def _failed_gate(gate_id: str, message: str) -> dict[str, Any]:
    return build_failed_gate(gate_id, message)


def _summarize_heading_decisions(decisions: list[dict[str, Any]], final_inventory: dict[str, Any]) -> dict[str, Any]:
    return summarize_heading_decisions(decisions, final_inventory)


def _summarize_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    return summarize_inventory(inventory)


def _split_href(href: str, *, current_file: str = "") -> tuple[str, str]:
    if "#" in href:
        file_part, fragment = href.split("#", 1)
    else:
        file_part, fragment = href, ""
    file_part = file_part or current_file
    return PurePosixPath(file_part).name, fragment


def _extract_text_sample(chapter_path: Path) -> str:
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(chapter_path), parser)
    return " ".join("".join(tree.getroot().itertext()).split())[:800]


def _extract_primary_title_from_chapters(chapter_paths) -> str:
    parser = etree.XMLParser(recover=True)
    for chapter_path in chapter_paths:
        try:
            tree = etree.parse(str(chapter_path), parser)
        except Exception:
            continue
        for query in (".//xhtml:h1", ".//xhtml:h2"):
            nodes = tree.getroot().xpath(query, namespaces={"xhtml": XHTML_NS})
            for node in nodes:
                text = " ".join("".join(node.itertext()).split()).strip()
                if text and not _is_placeholder_title(text):
                    return text
    return ""


def _pick_metadata_value(*, requested: str, current: str, fallback: str) -> str:
    requested = (requested or "").strip()
    if requested:
        return requested
    current = (current or "").strip()
    if current and not _is_placeholder_title(current):
        return current
    return fallback


def _derive_issue_title_from_source_path(source_path: Path) -> str:
    candidate = source_path.stem
    candidate = re.sub(r"\s*\(\d+\)\s*$", "", candidate)
    candidate = candidate.replace("_", " ")
    candidate = re.sub(r"\s+", " ", candidate).strip()
    candidate = re.sub(r"\s+,", ",", candidate)
    candidate = re.sub(r"\s+-\s+", " - ", candidate)
    if not _looks_like_issue_filename_title(candidate):
        return ""
    return candidate


def _should_prefer_source_issue_title(*, current_title: str, source_issue_title: str) -> bool:
    source_issue_title = (source_issue_title or "").strip()
    current_title = (current_title or "").strip()
    if not source_issue_title:
        return False
    if not current_title or _is_placeholder_title(current_title):
        return True
    if _title_fragments_match(current_title, source_issue_title):
        return False
    words = re.findall(r"[A-Za-z0-9]+", current_title)
    current_has_issue_date = _looks_like_issue_filename_title(current_title)
    return len(words) >= 10 and not current_has_issue_date


def _looks_like_issue_filename_title(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return False
    month_pattern = (
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
    )
    has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", normalized))
    has_month = bool(re.search(month_pattern, normalized, flags=re.IGNORECASE))
    has_issue_marker = bool(re.search(r"\b(?:issue|edition|vol\.?|no\.?|magazine|weekly|monthly)\b", normalized, flags=re.IGNORECASE))
    return has_year and (has_month or has_issue_marker)


def _pick_author_value(*, requested: str, current: str, chapter_paths) -> str:
    requested = (requested or "").strip()
    if requested:
        return requested
    current = (current or "").strip()
    if current and not _is_placeholder_author(current):
        return current
    recovered = _extract_author_from_chapters(chapter_paths)
    return recovered or "Unknown Author"


def _pick_language_value(requested: str, current: str) -> str:
    requested = (requested or "").strip()
    if requested:
        return requested
    current = (current or "").strip()
    if current:
        return current
    return "en"


def _is_placeholder_title(value: str) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return True
    if normalized.lower() in PLACEHOLDER_TITLE_MARKERS:
        return True
    return _looks_technical_title(normalized)


def _looks_like_utc_modified(value: str) -> bool:
    return bool(value and len(value) == 20 and value.endswith("Z") and "T" in value)


def _is_suspicious_heading(text: str) -> bool:
    return is_suspicious_heading(text)


def _make_review_item(kind: str, file_name: str, subject: str, reason: str, confidence: float) -> dict[str, Any]:
    return make_review_item(kind, file_name, subject, reason, confidence)


def _dedupe_review_items(items: list[Any]) -> list[Any]:
    return dedupe_review_items(items)


def _build_manual_review_markdown(items: list[Any]) -> str:
    return build_manual_review_markdown(items)


def _build_release_report_markdown(
    *,
    release_summary: dict[str, Any],
    metadata_payload: dict[str, Any],
    toc_payload: dict[str, Any],
) -> str:
    return build_recovery_release_report_markdown(
        release_summary=release_summary,
        metadata_payload=metadata_payload,
        toc_payload=toc_payload,
    )
    lines = [
        "# EPUB Publishing Quality Recovery",
        "",
        f"- Recommendation: {release_summary['recommendation']}",
        f"- Source EPUB: {release_summary['source_epub']}",
        f"- Final EPUB: {release_summary['final_epub']}",
        "",
        "## Gates",
    ]
    for gate_name in ("A", "B", "C", "D", "E", "F"):
        gate = release_summary["gates"].get(gate_name, {})
        lines.append(f"- Gate {gate_name}: {gate.get('status', 'unknown')} — {gate.get('summary', '')}")
    lines.extend(
        [
            "",
            "## Metadata",
            f"- Title: {metadata_payload['after']['primary'].get('title', '')}",
            f"- Author: {metadata_payload['after']['primary'].get('creator', '')}",
            f"- Language: {metadata_payload['after']['primary'].get('language', '')}",
            f"- Metadata changes: {len(metadata_payload.get('changes', []))}",
            "",
            "## TOC",
            f"- Entries: {len(toc_payload.get('entries', []))}",
            f"- Warnings: {len(toc_payload.get('warnings', []))}",
            "",
            "## Baseline vs Final",
            f"- Baseline TOC entries: {release_summary['baseline']['toc_count']}",
            f"- Final TOC entries: {release_summary['final']['toc_count']}",
            f"- Baseline heading count: {release_summary['baseline']['heading_count']}",
            f"- Final heading count: {release_summary['final']['heading_count']}",
            "",
            "## Manual Review",
            f"- Queue size: {release_summary['manual_review_count']}",
            f"- Reader smoke: {release_summary['reader_smoke']['status']} ({release_summary['reader_smoke']['reason']})",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover EPUB publishing quality after PDF->EPUB conversion.")
    parser.add_argument("epub_path", help="Input EPUB path")
    parser.add_argument("--output-dir", default="output", help="Directory for final EPUB")
    parser.add_argument("--reports-dir", default="reports", help="Directory for JSON/Markdown reports")
    parser.add_argument("--title", default="", help="Expected business title")
    parser.add_argument("--author", default="", help="Expected business author")
    parser.add_argument("--description", default="", help="Expected publication description")
    parser.add_argument("--language", default="", help="Expected publication language")
    parser.add_argument("--profile", default="", help="Optional publication profile hint")
    args = parser.parse_args()

    result = run_epub_publishing_quality_recovery(
        args.epub_path,
        output_dir=args.output_dir,
        reports_dir=args.reports_dir,
        expected_title=args.title,
        expected_author=args.author,
        expected_description=args.description,
        expected_language=args.language,
        publication_profile=args.profile or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["decision"] in {"pass", "pass_with_review"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
