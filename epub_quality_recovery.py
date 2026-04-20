from __future__ import annotations

import argparse
import io
import json
import tempfile
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
    _get_spine_xhtml_paths,
    _inject_problem_solution_links,
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
    _strip_unresolved_fragment_links,
    _synchronize_xhtml_language,
    _update_opf_metadata,
    _write_default_css,
    _looks_technical_title,
)
from premium_tools import run_epubcheck


PLACEHOLDER_TITLE_MARKERS = {"unknown", "untitled", "executive summary", "python-docx", "emvc"}
SUSPICIOUS_HEADING_MARKERS = (
    "material sponsorowany",
    "materiał sponsorowany",
    "page ",
    "strona ",
    "www.",
)


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
) -> dict[str, Any]:
    source_path = Path(epub_path).resolve()
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    paths = _prepare_output_paths(output_dir=Path(output_dir), reports_dir=Path(reports_dir))
    original_bytes = source_path.read_bytes()
    original_inventory = _inventory_epub(original_bytes, label="baseline")
    baseline_epubcheck = run_epubcheck(original_bytes)
    gates: dict[str, dict[str, Any]] = {}
    manual_review: list[dict[str, Any]] = []

    gates["A"] = _evaluate_gate_a(original_inventory)
    manual_review.extend(gates["A"].get("manual_review", []))

    working_bytes = original_bytes
    metadata_diff: dict[str, Any] = {"before": original_inventory["metadata"], "after": original_inventory["metadata"], "changes": []}
    heading_report: dict[str, Any] = {"summary": {}, "decisions": [], "manual_review": []}
    toc_report: dict[str, Any] = original_inventory["toc"]
    structural_report: dict[str, Any] = original_inventory["structural_integrity"]
    final_inventory = original_inventory
    metadata_epubcheck = {"status": "unavailable", "tool": "epubcheck", "messages": []}
    final_epubcheck = {"status": "unavailable", "tool": "epubcheck", "messages": []}

    if gates["A"]["status"] != "fail":
        working_bytes, metadata_diff, metadata_epubcheck = _run_metadata_phase(
            working_bytes,
            source_path=source_path,
            expected_title=expected_title,
            expected_author=expected_author,
            expected_description=expected_description,
            expected_language=expected_language,
            publication_profile=publication_profile,
        )
        metadata_after_inventory = _inventory_epub(working_bytes, label="post_metadata")
        gates["B"] = _evaluate_gate_b(
            before=original_inventory,
            after=metadata_after_inventory,
            metadata_diff=metadata_diff,
            epubcheck=metadata_epubcheck,
            baseline_epubcheck=baseline_epubcheck,
        )
        manual_review.extend(gates["B"].get("manual_review", []))

        if gates["B"]["status"] != "fail":
            working_bytes, heading_report, toc_report, structural_report, final_epubcheck = _run_recovery_phases(
                working_bytes,
                source_path=source_path,
                expected_title=expected_title,
                expected_author=expected_author,
                expected_description=expected_description,
                expected_language=expected_language,
                publication_profile=publication_profile,
            )
            final_inventory = _inventory_epub(working_bytes, label="final")
            gates["C"] = _evaluate_gate_c(heading_report, final_inventory)
            gates["D"] = _evaluate_gate_d(toc_report, final_inventory)
            gates["E"] = _evaluate_gate_e(structural_report)
            manual_review.extend(heading_report.get("manual_review", []))
            manual_review.extend(gates["C"].get("manual_review", []))
            manual_review.extend(gates["D"].get("manual_review", []))
            manual_review.extend(gates["E"].get("manual_review", []))
        else:
            toc_report = metadata_after_inventory["toc"]
            structural_report = metadata_after_inventory["structural_integrity"]
            final_inventory = metadata_after_inventory
            final_epubcheck = metadata_epubcheck
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

    recommendation = gates["F"]["status"]
    working_bytes = working_bytes or original_bytes
    paths.final_epub.write_bytes(working_bytes)

    metadata_payload = {
        "before": metadata_diff.get("before", original_inventory["metadata"]),
        "after": metadata_diff.get("after", final_inventory["metadata"]),
        "changes": metadata_diff.get("changes", []),
        "conflicts": metadata_diff.get("conflicts", []),
        "gate": gates["B"],
    }
    toc_payload = {
        **toc_report,
        "gate": gates["D"],
    }
    structural_payload = {
        **structural_report,
        "gate": gates["E"],
    }
    epubcheck_payload = {
        **final_epubcheck,
        "metadata_phase": metadata_epubcheck,
    }
    release_summary = {
        "source_epub": str(source_path),
        "final_epub": str(paths.final_epub.resolve()),
        "recommendation": recommendation,
        "gates": gates,
        "baseline": _summarize_inventory(original_inventory),
        "final": _summarize_inventory(final_inventory),
        "baseline_epubcheck_status": baseline_epubcheck.get("status", "unavailable"),
        "manual_review_count": len(manual_review),
        "reader_smoke": {"status": "not_run", "reason": "No reader engines available in CLI pipeline."},
    }

    paths.metadata_diff.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.heading_decisions.write_text(json.dumps(heading_report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.toc_map.write_text(json.dumps(toc_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.structural_integrity.write_text(json.dumps(structural_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.epubcheck.write_text(json.dumps(epubcheck_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    paths.manual_review_queue.write_text(_build_manual_review_markdown(manual_review), encoding="utf-8")
    paths.release_report.write_text(
        _build_release_report_markdown(release_summary=release_summary, metadata_payload=metadata_payload, toc_payload=toc_payload),
        encoding="utf-8",
    )

    return {
        "decision": recommendation,
        "final_epub": str(paths.final_epub),
        "reports": {
            "metadata_diff": str(paths.metadata_diff),
            "heading_decisions": str(paths.heading_decisions),
            "toc_map": str(paths.toc_map),
            "structural_integrity": str(paths.structural_integrity),
            "epubcheck": str(paths.epubcheck),
            "release_report": str(paths.release_report),
            "manual_review_queue": str(paths.manual_review_queue),
        },
        "gates": gates,
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
        title = _pick_metadata_value(
            requested=expected_title,
            current=before_inventory["metadata"]["primary"].get("title", ""),
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
            author=author or "Unknown",
        )
        toc_entries = before_inventory["toc"].get("entries", [])

        _update_opf_metadata(
            opf_path,
            title=title,
            author=author or "Unknown",
            language=language,
            chapter_paths=chapter_paths,
            toc_entries=toc_entries,
            description_seed=description_seed,
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
                    author=raw_author_candidate or expected_author or "Unknown",
                )
            if len(raw_language_samples) < 6:
                raw_language_samples.append(_extract_text_sample(chapter_path))

            chapter_result = _process_chapter(
                chapter_path,
                repeated_counts=repeated_counts,
                keep_first_seen=keep_first_seen,
                title=expected_title or source_path.stem,
                author=expected_author or raw_author_candidate or "Unknown",
                language=expected_language or "en",
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
        author_hint = expected_author or raw_author_candidate or "Unknown"
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
        if not spine_order:
            spine_order = [path.name for path in chapter_paths if path.name != "cover.xhtml"]

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
        _reorder_opf_spine(opf_path, spine_order)
        final_bytes = _pack_epub(root_dir)

    final_inventory = _inventory_epub(final_bytes, label="final")
    toc_report = {
        **final_inventory["toc"],
        "baseline_entry_count": len(_inventory_epub(epub_bytes, label="toc_baseline")["toc"].get("entries", [])),
    }
    heading_report = {
        "summary": _summarize_heading_decisions(heading_decisions, final_inventory),
        "decisions": heading_decisions,
        "manual_review": _dedupe_review_items(manual_review),
    }
    structural_report = final_inventory["structural_integrity"]
    return final_bytes, heading_report, toc_report, structural_report, run_epubcheck(final_bytes)


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
        spine_files = [path.name for path in spine_paths]
        headings = {path.name: _extract_heading_snapshot(path) for path in spine_paths}
        toc = _inspect_toc(opf_path, spine_files=spine_files)
        structural = _inspect_structural_integrity(opf_path, spine_paths=spine_paths, toc=toc)
        return {
            "label": label,
            "metadata": metadata,
            "spine_files": spine_files,
            "headings": headings,
            "toc": toc,
            "structural_integrity": structural,
        }


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
    decisions: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    before_by_key = {(item["level"], item["text"]): item for item in before_snapshot}
    after_by_key = {(item["level"], item["text"]): item for item in after_snapshot}

    for key, before in before_by_key.items():
        if key in after_by_key:
            continue
        reason, confidence = _heading_change_reason(before.get("text", ""), removed=True)
        decision = {
            "file": file_name,
            "status": "removed",
            "before": before,
            "after": None,
            "reason": reason,
            "confidence": confidence,
        }
        decisions.append(decision)
        if confidence < 0.65 or reason in {"short-ambiguous-heading", "uppercase-layout-candidate"}:
            review.append(_make_review_item("heading", file_name, before.get("text", ""), reason, confidence))

    for key, after in after_by_key.items():
        if key in before_by_key:
            continue
        reason, confidence = _heading_change_reason(after.get("text", ""), removed=False)
        decision = {
            "file": file_name,
            "status": "recovered",
            "before": None,
            "after": after,
            "reason": reason,
            "confidence": confidence,
        }
        decisions.append(decision)
        if confidence < 0.65 or reason in {"short-ambiguous-heading", "uppercase-layout-candidate"}:
            review.append(_make_review_item("heading", file_name, after.get("text", ""), reason, confidence))

    return decisions, review


def _heading_change_reason(text: str, *, removed: bool) -> tuple[str, float]:
    normalized = " ".join((text or "").split()).strip()
    lowered = normalized.lower()
    if any(marker in lowered for marker in SUSPICIOUS_HEADING_MARKERS):
        return ("layout-artifact-filtered" if removed else "recovered-section-heading"), 0.92 if removed else 0.7
    if len(normalized) <= 4:
        return ("short-ambiguous-heading", 0.58)
    if normalized.isupper() and len(normalized) <= 40:
        return ("uppercase-layout-candidate", 0.66)
    return ("semantic-heading-normalization", 0.76 if removed else 0.74)


def _build_metadata_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes = []
    for field in ("title", "creator", "description", "language", "identifier", "modified"):
        before_value = before["primary"].get(field, "")
        after_value = after["primary"].get(field, "")
        if before_value != after_value:
            changes.append({"field": field, "before": before_value, "after": after_value})
    return {
        "before": before,
        "after": after,
        "changes": changes,
        "conflicts": [],
    }


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
    for file_name, headings in final_inventory["headings"].items():
        if file_name == "cover.xhtml":
            continue
        h1_count = sum(1 for heading in headings if heading["level"] == 1)
        if h1_count != 1:
            blockers.append(f"{file_name} has {h1_count} H1 headings.")
        suspicious = [heading["text"] for heading in headings if _is_suspicious_heading(heading["text"])]
        if suspicious:
            blockers.append(f"{file_name} still contains suspicious headings: {', '.join(suspicious[:3])}")
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
        target_headings = final_inventory["headings"].get(entry.get("file", ""), [])
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
        "manual_review": _dedupe_review_items(manual_review),
    }


def _failed_gate(gate_id: str, message: str) -> dict[str, Any]:
    return {
        "gate": gate_id,
        "status": "fail",
        "summary": message,
        "blockers": [message],
        "warnings": [],
        "manual_review": [],
    }


def _summarize_heading_decisions(decisions: list[dict[str, Any]], final_inventory: dict[str, Any]) -> dict[str, Any]:
    status_counts = Counter(decision["status"] for decision in decisions)
    suspicious_remaining = sum(
        1
        for headings in final_inventory["headings"].values()
        for heading in headings
        if _is_suspicious_heading(heading["text"])
    )
    return {
        "removed_count": status_counts.get("removed", 0),
        "recovered_count": status_counts.get("recovered", 0),
        "chapter_count": len(final_inventory["headings"]),
        "suspicious_heading_count": suspicious_remaining,
    }


def _summarize_inventory(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": inventory["metadata"]["primary"].get("title", ""),
        "author": inventory["metadata"]["primary"].get("creator", ""),
        "language": inventory["metadata"]["primary"].get("language", ""),
        "spine_count": len(inventory.get("spine_files", [])),
        "toc_count": len(inventory.get("toc", {}).get("entries", [])),
        "heading_count": sum(len(entries) for entries in inventory.get("headings", {}).values()),
        "duplicate_id_files": len(inventory.get("structural_integrity", {}).get("duplicate_ids", [])),
        "broken_ref_count": len(inventory.get("structural_integrity", {}).get("broken_references", [])),
    }


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


def _pick_author_value(*, requested: str, current: str, chapter_paths) -> str:
    requested = (requested or "").strip()
    if requested:
        return requested
    current = (current or "").strip()
    if current and not _is_placeholder_author(current):
        return current
    recovered = _extract_author_from_chapters(chapter_paths)
    return recovered or "Unknown"


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
    normalized = " ".join((text or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return True
    if any(marker in lowered for marker in SUSPICIOUS_HEADING_MARKERS):
        return True
    if lowered.startswith("go to solution"):
        return True
    if normalized.isdigit():
        return True
    return False


def _make_review_item(kind: str, file_name: str, subject: str, reason: str, confidence: float) -> dict[str, Any]:
    return {
        "kind": kind,
        "file": file_name,
        "subject": subject,
        "reason": reason,
        "confidence": round(confidence, 2),
    }


def _dedupe_review_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()
    for item in items:
        marker = (item.get("kind"), item.get("file"), item.get("subject"), item.get("reason"))
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(item)
    return deduped


def _build_manual_review_markdown(items: list[dict[str, Any]]) -> str:
    items = _dedupe_review_items(items)
    if not items:
        return "# Manual Review Queue\n\n- None\n"
    lines = ["# Manual Review Queue", ""]
    for item in items:
        lines.append(
            f"- [{item.get('kind')}] {item.get('file')}: {item.get('subject')} ({item.get('reason')}, confidence {item.get('confidence')})"
        )
    return "\n".join(lines).strip() + "\n"


def _build_release_report_markdown(
    *,
    release_summary: dict[str, Any],
    metadata_payload: dict[str, Any],
    toc_payload: dict[str, Any],
) -> str:
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
