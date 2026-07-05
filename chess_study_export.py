from __future__ import annotations

import html
import base64
import csv
import hashlib
import io
import json
import math
import re
import shutil
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import fitz
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageOps

from chess_position_recognizer import validate_fen
from chess_side_marker_blockers import build_side_marker_blocker_attribution, side_marker_blocker_attribution_markdown
from pymupdf_chess_extractor import (
    _apply_scan_chess_side_to_move_context_evidence,
    _apply_scan_chess_two_crop_quality_gate,
    _apply_scan_chess_two_crop_side_marker_if_trusted,
    _infer_scan_chess_side_to_move_marker_evidence,
    _scan_chess_local_side_marker_assignment_evidence,
    _scan_chess_side_marker_metadata_from_payload,
    _scan_chess_two_crop_review_artifacts,
)
from scripts.audit_chess_html import audit_chess_html
from scripts.chess_diagram_detection import detect_chess_diagrams


STUDY_STATUSES = {
    "accepted",
    "needs_review",
    "missing_fen",
    "missing_pgn",
    "illegal_pgn",
    "low_confidence",
    "unlinked_solution",
}

QUALITY_PROFILES = {"smoke", "default", "masterkindle"}
FINAL_READER_ARTIFACT_TYPE = "final_pdf_two_crop_reader"
SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE = "source_html_evidence_only"
SEMANTIC_BOOK_SCHEMA = "kindlemaster.chess_reader.semantic_book.v1"
QUALITY_THRESHOLDS = {
    "smoke": {
        "pages": 1,
        "page_images": 0,
        "pages_with_extractable_text": 0,
        "copyable_text_characters": 0,
        "diagrams_total": 0,
        "notation_fragments_total": 0,
        "accepted_pgn": 0,
    },
    "default": {
        "pages": 1,
        "page_images": 0,
        "pages_with_extractable_text": 1,
        "copyable_text_characters": 1,
        "diagrams_total": 0,
        "notation_fragments_total": 0,
        "accepted_pgn": 0,
    },
    "masterkindle": {
        "pages": 266,
        "page_images": 266,
        "pages_with_extractable_text": 260,
        "copyable_text_characters": 250_000,
        "diagrams_total": 540,
        "fen_accepted": 520,
        "notation_fragments_total": 560,
        "accepted_pgn": 1,
    },
}

YUSUPOV_CHAPTERS = [
    (1, "Mating motifs"),
    (2, "Mating motifs 2"),
    (3, "Basic opening principles"),
    (4, "Simple pawn endings"),
    (5, "Double check"),
    (6, "The value of the pieces"),
    (7, "The discovered attack"),
    (8, "Centralizing the pieces"),
    (9, "Mate in two moves"),
    (10, "The opposition"),
    (11, "The pin"),
    (12, "The double attack"),
    (13, "Realizing a material advantage"),
    (14, "Open files and Outposts"),
    (15, "Combinations"),
    (16, "Queen against pawn"),
    (17, "Stalemate motifs"),
    (18, "Forced variations"),
    (19, "Combinations involving promotion"),
    (20, "Weak points"),
    (21, "Pawn combinations"),
    (22, "The wrong bishop"),
    (23, "Smothered mate"),
    (24, "Gambits"),
]

FINAL_TEST_RE = re.compile(r"\bfinal\s+test\b", re.IGNORECASE)
APPENDIX_PATTERNS = {
    "index_of_composers_and_analysts": re.compile(r"index\s+of\s+composers\s+and\s+analysts", re.IGNORECASE),
    "index_of_games": re.compile(r"index\s+of\s+games", re.IGNORECASE),
    "recommended_books": re.compile(r"recommended\s+books", re.IGNORECASE),
}
EXERCISE_LABEL_RE = re.compile(r"\bEx\.\s*(?P<chapter>\d{1,2})[-.](?P<number>\d{1,2})\b", re.IGNORECASE)
FINAL_LABEL_RE = re.compile(r"\bF[-.]\s*(?P<number>\d{1,2})\b", re.IGNORECASE)
UNMAPPED_CHESS_GLYPH_WARNING = "unmapped_chess_glyphs"
NOTATION_GLYPH_DIAGNOSTIC_LIMIT = 500
NOTATION_GLYPH_SAMPLE_LIMIT = 50
SUSPECT_NOTATION_GLYPH_RE = re.compile(
    r"(?:\ufffd|@|[a-z]{1,3}[ldht][a-h][1-8]|[a-z]{1,3}x[a-h][1-8]|[a-z][ldht]\d)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChessStudyConfig:
    pdf: Path
    html: Path | None
    out: Path
    diagram_pages: int = 0
    diagram_page_ranges: str = ""
    diagram_dpi: int = 160
    min_grid_confidence: float = 0.50
    max_candidates_per_page: int = 6
    quality_profile: str = "default"
    render_pages: bool = False
    ocr_fallback: bool = False
    strict_thresholds: bool = False
    low_confidence_diagram_review: bool = False
    low_confidence_min_grid_confidence: float = 0.30
    low_confidence_max_candidates_per_page: int = 12
    glyph_context_pages: str = ""
    review_sample_limit: int = 0
    diagram_review_labels: Path | None = None
    glyph_mapping_file: Path | None = None
    diagram_alignment_review: bool = False


@dataclass(frozen=True)
class StudyLayoutElement:
    type: str
    page: int
    bbox: list[float]
    reading_order: int
    source_kind: str
    text: str = ""
    ref_id: str = ""
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "page": self.page,
            "bbox": list(self.bbox),
            "reading_order": self.reading_order,
            "source_kind": self.source_kind,
            "text": self.text,
            "ref_id": self.ref_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class StudyTextBlock:
    page: int
    block_index: int
    line_index: int
    span_index: int
    reading_order: int
    text: str
    normalized_text: str
    bbox: list[float]
    font: str = ""
    size: float = 0.0
    type: str = "text"
    glyph_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "block_index": self.block_index,
            "line_index": self.line_index,
            "span_index": self.span_index,
            "reading_order": self.reading_order,
            "type": self.type,
            "text": self.text,
            "normalized_text": self.normalized_text,
            "bbox": list(self.bbox),
            "font": self.font,
            "size": round(float(self.size or 0.0), 3),
            "glyph_diagnostics": list(self.glyph_diagnostics),
        }


@dataclass(frozen=True)
class StudyPage:
    page: int
    pdf_page_index: int
    width: float
    height: float
    page_image: str
    raw_text: str
    normalized_text: str
    paragraphs: list[str]
    blocks: list[StudyTextBlock]
    elements: list[StudyLayoutElement] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "pdf_page_index": self.pdf_page_index,
            "width": round(float(self.width or 0.0), 3),
            "height": round(float(self.height or 0.0), 3),
            "page_image": self.page_image,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "paragraphs": list(self.paragraphs),
            "blocks": [block.to_dict() for block in self.blocks],
            "elements": [element.to_dict() for element in self.elements],
        }


@dataclass(frozen=True)
class StudyDiagram:
    id: str
    page: int
    visual_order_on_page: int
    bbox: list[float]
    label: str
    side_to_move: str
    fen: str
    fen_candidate: str
    status: str
    confidence: float
    source_crop: str
    board_crop_path: str
    side_marker_crop_path: str
    side_marker_search_crop_path: str
    marker_search_zone_preview_path: str
    marker_search_zone_preview_bbox: list[float]
    side_marker_review_crop_path: str
    side_marker_review_crop_kind: str
    debug_overlay_path: str
    board_bbox: list[float]
    side_marker_bbox: list[float]
    marker_search_zones: dict[str, Any]
    selected_marker_zone: str
    marker_bbox: list[float]
    marker_crop_bbox: list[float]
    board_crop_quality: str
    board_crop_fail_reason: list[str]
    marker_crop_quality: str
    marker_crop_fail_reason: list[str]
    side_to_move_detected: str
    side_to_move_confidence: float | str
    manual_review_required: bool
    manual_review_reason: str
    side_to_move_status: str
    side_to_move_evidence: str
    side_marker_symbol: str
    side_marker_status: str
    side_marker_source: str
    side_marker_confidence: float | str
    side_marker_assignment_trace: dict[str, Any]
    strict_fen_side_evidence_trusted: bool
    placement: str
    placement_status: str
    full_fen: str
    full_fen_status: str
    fen_suppressed_reason: str
    rendered_svg: str
    rendered_png: str
    review_reason: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page": self.page,
            "visual_order_on_page": self.visual_order_on_page,
            "bbox": list(self.bbox),
            "label": self.label,
            "side_to_move": self.side_to_move,
            "fen": self.fen,
            "fen_candidate": self.fen_candidate,
            "status": self.status,
            "confidence": round(float(self.confidence or 0.0), 3),
            "source_crop": self.source_crop,
            "board_crop_path": self.board_crop_path,
            "side_marker_crop_path": self.side_marker_crop_path,
            "side_marker_search_crop_path": self.side_marker_search_crop_path,
            "marker_search_zone_preview_path": self.marker_search_zone_preview_path,
            "marker_search_zone_preview_bbox": list(self.marker_search_zone_preview_bbox),
            "side_marker_review_crop_path": self.side_marker_review_crop_path,
            "side_marker_review_crop_kind": self.side_marker_review_crop_kind,
            "debug_overlay_path": self.debug_overlay_path,
            "board_bbox": list(self.board_bbox),
            "side_marker_bbox": list(self.side_marker_bbox),
            "marker_search_zones": dict(self.marker_search_zones),
            "selected_marker_zone": self.selected_marker_zone,
            "marker_bbox": list(self.marker_bbox),
            "marker_crop_bbox": list(self.marker_crop_bbox),
            "board_crop_quality": self.board_crop_quality,
            "board_crop_fail_reason": list(self.board_crop_fail_reason),
            "marker_crop_quality": self.marker_crop_quality,
            "marker_crop_fail_reason": list(self.marker_crop_fail_reason),
            "side_to_move_detected": self.side_to_move_detected,
            "side_to_move_confidence": self.side_to_move_confidence,
            "manual_review_required": bool(self.manual_review_required),
            "manual_review_reason": self.manual_review_reason,
            "side_to_move_status": self.side_to_move_status,
            "side_to_move_evidence": self.side_to_move_evidence,
            "side_marker_symbol": self.side_marker_symbol,
            "side_marker_status": self.side_marker_status,
            "side_marker_source": self.side_marker_source,
            "side_marker_confidence": self.side_marker_confidence,
            "side_marker_assignment_trace": dict(self.side_marker_assignment_trace),
            "strict_fen_side_evidence_trusted": bool(self.strict_fen_side_evidence_trusted),
            "placement": self.placement,
            "placement_status": self.placement_status,
            "full_fen": self.full_fen,
            "full_fen_status": self.full_fen_status,
            "fen_suppressed_reason": self.fen_suppressed_reason,
            "rendered_svg": self.rendered_svg,
            "rendered_png": self.rendered_png,
            "review_reason": self.review_reason,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class StudyNotationFragment:
    id: str
    page: int
    diagram_id: str
    source_page: int
    source_diagram: str
    raw_text: str
    normalized_text: str
    comments: list[str]
    pgn: str
    status: str
    warnings: list[str]
    bbox: list[float] = field(default_factory=list)
    glyph_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page": self.page,
            "diagram_id": self.diagram_id,
            "source_page": self.source_page,
            "source_diagram": self.source_diagram,
            "raw_text": self.raw_text,
            "normalized_text": self.normalized_text,
            "comments": list(self.comments),
            "pgn": self.pgn,
            "status": self.status,
            "warnings": list(self.warnings),
            "bbox": list(self.bbox),
            "glyph_diagnostics": list(self.glyph_diagnostics),
        }


@dataclass(frozen=True)
class StudyPosition:
    id: str
    type: str
    chapter_no: int | None
    label: str
    diagram_page: int
    solution_page: int | None
    side_to_move: str
    fen: str
    solution_pgn: str
    points: int | None
    status: str
    warnings: list[str]
    source_crop: str
    rendered_diagram: str


@dataclass(frozen=True)
class StudyAuditSummary:
    profile: str
    status: str
    pages: int
    page_images: int
    pages_with_extractable_text: int
    copyable_text_characters: int
    diagrams_total: int
    fen_accepted: int
    notation_fragments_total: int
    notation_glyph_diagnostics: int
    unmapped_notation_fragments: int
    accepted_pgn: int


def run_chess_study_export(
    pdf: str | Path,
    *,
    html_path: str | Path | None = None,
    out_dir: str | Path = "output/yusupov_study",
    diagram_pages: int = 0,
    diagram_page_ranges: str = "",
    diagram_dpi: int = 160,
    min_grid_confidence: float = 0.50,
    max_candidates_per_page: int = 6,
    quality_profile: str = "default",
    render_pages: bool = False,
    ocr_fallback: bool = False,
    strict_thresholds: bool = False,
    low_confidence_diagram_review: bool = False,
    low_confidence_min_grid_confidence: float = 0.30,
    low_confidence_max_candidates_per_page: int = 12,
    glyph_context_pages: str = "",
    review_sample_limit: int = 0,
    diagram_review_labels: str | Path | None = None,
    glyph_mapping_file: str | Path | None = None,
    diagram_alignment_review: bool = False,
) -> dict[str, Any]:
    normalized_profile = _normalize_quality_profile(quality_profile)
    effective_render_pages = bool(render_pages) or normalized_profile == "masterkindle"
    config = ChessStudyConfig(
        pdf=Path(pdf),
        html=_resolve_html_input(html_path) if html_path else None,
        out=Path(out_dir),
        diagram_pages=diagram_pages,
        diagram_page_ranges=diagram_page_ranges,
        diagram_dpi=diagram_dpi,
        min_grid_confidence=min_grid_confidence,
        max_candidates_per_page=max_candidates_per_page,
        quality_profile=normalized_profile,
        render_pages=effective_render_pages,
        ocr_fallback=ocr_fallback,
        strict_thresholds=strict_thresholds,
        low_confidence_diagram_review=low_confidence_diagram_review,
        low_confidence_min_grid_confidence=low_confidence_min_grid_confidence,
        low_confidence_max_candidates_per_page=low_confidence_max_candidates_per_page,
        glyph_context_pages=glyph_context_pages,
        review_sample_limit=review_sample_limit,
        diagram_review_labels=Path(diagram_review_labels) if diagram_review_labels else None,
        glyph_mapping_file=Path(glyph_mapping_file) if glyph_mapping_file else None,
        diagram_alignment_review=diagram_alignment_review,
    )
    _ensure_output_dirs(config.out)

    page_model = ingest_study_pdf(config)
    current_audit = audit_current_html(config) if config.html else _empty_current_audit()
    structure = extract_study_structure(config.pdf, config.out, html_path=config.html)
    segments = segment_study_pages(config.pdf, structure, config.out, html_path=config.html)
    diagrams = detect_study_diagrams(config)
    positions = build_study_positions(diagrams, segments, config.out)
    notation_fragments = extract_study_notation_fragments(
        page_model,
        positions,
        config.out,
        glyph_context_pages=config.glyph_context_pages,
        glyph_mapping_file=config.glyph_mapping_file,
    )
    pgn_payload = build_study_pgn(positions, config.out, notation_fragments=notation_fragments)
    exercises = build_study_exercises(positions, config.out)
    final_test = build_study_final_test(positions, config.out)
    qa_report = validate_study_export(
        config,
        current_audit=current_audit,
        structure=structure,
        segments=segments,
        diagrams=diagrams,
        positions=positions,
        page_model=page_model,
        notation_fragments=notation_fragments,
        pgn_payload=pgn_payload,
        exercises=exercises,
        final_test=final_test,
    )
    source_gate: dict[str, Any] | None = None
    if config.html and config.html.is_file():
        source_gate = _source_html_quality_gate(config.html, config.out, pdf_path=config.pdf, qa_report=qa_report)
        _write_source_html_quality_gate(config.out, source_gate)
        _write_study_side_marker_blocker_attribution(config.out, diagrams.get("diagrams", []) or [], source_gate=source_gate)
    render_study_html(
        config.out,
        structure=structure,
        positions=positions,
        qa_report=qa_report,
        page_model=page_model,
        notation_fragments=notation_fragments,
        source_pdf=config.pdf,
        source_html=config.html,
        source_gate=source_gate,
    )
    render_qa_html(config.out, qa_report)
    if source_gate and source_gate.get("used_as_final_reader"):
        rebuild_chess_source_html_export(
            config.html,
            config.out,
            pdf_path=config.pdf,
            qa_report=qa_report,
            source_gate=source_gate,
        )
    return qa_report


def rebuild_chess_source_html_export(
    html_path: str | Path,
    out_dir: str | Path,
    *,
    pdf_path: str | Path | None = None,
    qa_report: dict[str, Any] | None = None,
    source_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild a semantic, asset-backed study reader from the PDF-layout HTML artifact.

    The source HTML is treated as evidence only: extracted text, diagram crops, page
    order and review records. FEN/PGN values are accepted only after deterministic
    validation; otherwise the reader shows a clean review state.
    """
    source = Path(html_path)
    out = Path(out_dir)
    data_dir = out / "data"
    reports_dir = out / "reports"
    diagram_dir = out / "assets" / "diagrams"
    page_preview_dir = out / "assets" / "pages-preview"
    for directory in [out, data_dir, reports_dir, diagram_dir, page_preview_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    effective_source_gate = dict(source_gate or {})
    if source_gate is None:
        effective_source_gate = _source_html_quality_gate(source, out, pdf_path=pdf_path, qa_report=qa_report)
        _write_source_html_quality_gate(out, effective_source_gate)
    if effective_source_gate.get("source_html_evidence_only") or not effective_source_gate.get("used_as_final_reader", True):
        return {
            "schema": "kindlemaster.semantic_chess_html.v1",
            "source_html": str(source),
            "source_pdf": str(pdf_path or ""),
            "title": "",
            "chapters": [],
            "pages": [],
            "pgn_records": [],
            "summary": dict(effective_source_gate.get("summary") or {}),
            "source_html_quality_gate": effective_source_gate,
            "source_html_evidence_path": str(effective_source_gate.get("source_html_evidence_path") or ""),
            "final_reader_missing": True,
        }

    soup = BeautifulSoup(source.read_text(encoding="utf-8", errors="replace"), "html.parser")
    pages = _extract_source_html_pages(
        soup,
        diagram_dir=diagram_dir,
        page_preview_dir=page_preview_dir,
        source_html=source,
    )
    pgn_records = _extract_source_html_pgn_records(soup)
    _link_source_pgn_records_to_pages(pgn_records, pages)
    chapters = _source_html_chapters(pages)
    accepted_pgn = [record for record in pgn_records if record.get("status") == "accepted"]
    games_pgn = "\n\n".join(str(record.get("pgn") or "").strip() for record in accepted_pgn if record.get("pgn")).strip()
    (data_dir / "games.pgn").write_text(games_pgn + ("\n" if games_pgn else ""), encoding="utf-8")

    book_payload = {
        "schema": "kindlemaster.semantic_chess_html.v1",
        "source_html": str(source),
        "source_pdf": str(pdf_path or ""),
        "title": "Build Up Your Chess - The Fundamentals",
        "chapters": chapters,
        "pages": pages,
        "pgn_records": pgn_records,
        "summary": _source_html_summary(pages, pgn_records, source, pdf_path=pdf_path, qa_report=qa_report),
    }
    book_payload = _attach_engine_analysis_to_book(book_payload, out)
    artifact_manifest = _build_artifact_manifest(
        artifact_type=FINAL_READER_ARTIFACT_TYPE,
        pipeline_mode="source_html_semantic_reader",
        source_pdf=pdf_path,
        source_html=source,
        source_gate=effective_source_gate,
        summary=book_payload["summary"],
        diagrams=[diagram for page in pages for diagram in page.get("diagrams", [])],
    )
    book_payload["artifact_manifest"] = artifact_manifest
    semantic_book = build_chess_reader_semantic_book(book_payload)
    book_payload["semantic_book"] = semantic_book
    diagrams_payload = {
        "schema": "kindlemaster.semantic_chess_diagrams.v1",
        "diagrams": [diagram for page in pages for diagram in page.get("diagrams", [])],
        "summary": _source_diagram_asset_summary([diagram for page in pages for diagram in page.get("diagrams", [])]),
    }
    _write_json(data_dir / "book.json", book_payload)
    _write_json(data_dir / "diagrams.json", diagrams_payload)
    _write_source_html_reports(book_payload, diagrams_payload, reports_dir)
    _write_chess_reader_semantic_book_reports(out, semantic_book)
    _write_artifact_manifest(data_dir / "artifact_manifest.json", artifact_manifest)
    (out / "styles.css").write_text(_semantic_source_styles_css(), encoding="utf-8")
    (out / "app.js").write_text(_semantic_source_app_js(), encoding="utf-8")
    (out / "index.html").write_text(_semantic_source_index_html(book_payload), encoding="utf-8")
    _write_final_reader_health_gate(
        out,
        artifact_manifest=artifact_manifest,
        summary=book_payload["summary"],
        diagrams=[diagram for page in pages for diagram in page.get("diagrams", [])],
    )
    return book_payload


def _extract_source_html_pages(
    soup: BeautifulSoup,
    *,
    diagram_dir: Path,
    page_preview_dir: Path,
    source_html: Path | None = None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    page_nodes = soup.select(".chess-book-page, .pdf-page, section[data-page]")
    if not page_nodes:
        page_nodes = [soup]
    for page_index, node in enumerate(page_nodes, start=1):
        page_number = _source_page_number(node, fallback=page_index)
        page_record: dict[str, Any] = {
            "page": page_number,
            "source_order": page_index,
            "page_preview": _extract_source_page_preview(node, page_number=page_number, out_dir=page_preview_dir),
            "text_blocks": _extract_source_text_blocks(node, page_number=page_number),
            "text_chunks": [],
            "diagrams": [],
            "pgn_records": [],
        }
        page_record["text_chunks"] = _source_text_chunks(page_record["text_blocks"])
        page_record["diagrams"] = _extract_source_diagrams(
            node,
            page_number=page_number,
            out_dir=diagram_dir,
            source_html=source_html,
        )
        pages.append(page_record)
    return pages


def _source_html_quality_gate(
    html_path: str | Path,
    out_dir: str | Path,
    *,
    pdf_path: str | Path | None = None,
    qa_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = Path(html_path)
    out = Path(out_dir)
    soup = BeautifulSoup(source.read_text(encoding="utf-8", errors="replace"), "html.parser")
    page_nodes = soup.select(".chess-book-page, .pdf-page, section[data-page]") or [soup]
    rows: list[dict[str, Any]] = []
    for page_index, page_node in enumerate(page_nodes, start=1):
        page_number = _source_page_number(page_node, fallback=page_index)
        captions = [
            block
            for block in _extract_source_text_blocks(page_node, page_number=page_number)
            if re.search(r"\b(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\b", block.get("text", ""), re.IGNORECASE)
        ]
        for diagram_index, diagram_node in enumerate(page_node.select(".book-diagram"), start=1):
            image = diagram_node.select_one("img")
            src = str(image.get("src") or "") if image else ""
            bbox = _source_style_box(str(diagram_node.get("style") or ""))
            alt = _scrub_local_links(str(image.get("alt") or "")) if image else ""
            caption = _nearest_source_caption(bbox, captions) or alt or diagram_node.get_text(" ", strip=True)
            fen_candidate = _extract_fen_candidate_from_node(diagram_node)
            image_status = _source_html_image_status(src, source)
            rows.append(
                {
                    "id": f"p{page_number:03d}_d{diagram_index:03d}",
                    "page": page_number,
                    "image_status": image_status,
                    "src_empty": image_status == "empty",
                    "src_localhost": image_status == "localhost_url",
                    "src_unresolved": image_status in {"relative_unresolved", "remote_unresolved"},
                    "src_resolved": _source_html_image_status_resolved(image_status),
                    "asset_missing_reason": _source_html_image_missing_reason(image_status),
                    "has_fen_or_marker_evidence": _source_html_diagram_has_evidence(diagram_node, image),
                    "fen_accepted": _source_html_fen_accepted(fen_candidate),
                    "side_to_move": _infer_side_to_move(caption),
                    "caption": caption,
                }
            )
    diagrams_total = len(rows)
    source_img_empty_count = len([row for row in rows if row["src_empty"]])
    source_img_localhost_count = len([row for row in rows if row["src_localhost"]])
    source_img_unresolved_count = len([row for row in rows if row["src_unresolved"]])
    source_img_resolved_count = len([row for row in rows if row["src_resolved"]])
    source_img_problem_count = source_img_empty_count + source_img_localhost_count + source_img_unresolved_count
    evidence_count = len([row for row in rows if row["has_fen_or_marker_evidence"]])
    fen_accepted = len([row for row in rows if row["fen_accepted"]])
    side_unknown_count = len([row for row in rows if row["side_to_move"] == "unknown"])
    image_problem_rate = _safe_ratio(source_img_problem_count, diagrams_total)
    side_unknown_rate = _safe_ratio(side_unknown_count, diagrams_total)
    reasons: list[str] = []
    if diagrams_total > 0 and image_problem_rate > 0.5:
        reasons.append("diagram_image_sources_degraded")
    if diagrams_total > 0 and evidence_count == 0:
        reasons.append("source_html_lacks_fen_marker_or_crop_evidence")
    if diagrams_total > 0 and fen_accepted == 0 and side_unknown_rate >= 0.8:
        reasons.append("all_or_most_diagrams_have_unknown_side_and_no_accepted_fen")
    reason_set = set(reasons)
    semantic_evidence_missing = {
        "source_html_lacks_fen_marker_or_crop_evidence",
        "all_or_most_diagrams_have_unknown_side_and_no_accepted_fen",
    }.issubset(reason_set)
    degraded = "diagram_image_sources_degraded" in reason_set or semantic_evidence_missing
    summary = {
        "diagrams_total": diagrams_total,
        "source_img_empty_count": source_img_empty_count,
        "empty_diagram_image_count": source_img_empty_count,
        "resolved_diagram_image_count": source_img_resolved_count,
        "source_img_resolved_count": source_img_resolved_count,
        "source_img_localhost_count": source_img_localhost_count,
        "source_img_unresolved_count": source_img_unresolved_count,
        "source_img_problem_count": source_img_problem_count,
        "source_img_problem_rate": image_problem_rate,
        "source_fen_or_marker_evidence_count": evidence_count,
        "fen_accepted": fen_accepted,
        "side_unknown_count": side_unknown_count,
        "side_unknown_rate": side_unknown_rate,
        "qa_status": (qa_report or {}).get("status", ""),
    }
    evidence_path = ""
    evidence_manifest_path = ""
    if degraded:
        evidence_path = _copy_source_html_evidence(
            source,
            out,
            pdf_path=pdf_path,
            summary=summary,
            decision="reject_degraded_source_html",
        )
        if evidence_path:
            evidence_manifest_path = "reports/source_html_evidence_manifest.json"
    return {
        "schema": "kindlemaster.chess_study.source_html_quality_gate.v1",
        "source_html": str(source),
        "source_pdf": str(pdf_path or ""),
        "used_as_final_reader": not degraded,
        "source_html_evidence_only": degraded,
        "source_html_evidence_path": evidence_path,
        "source_html_evidence_manifest_path": evidence_manifest_path,
        "decision": "reject_degraded_source_html" if degraded else "use_source_html_as_final_reader",
        "reasons": reasons,
        "summary": summary,
        "items": rows[:500],
    }


def _source_html_image_status(src: str, source: Path) -> str:
    value = str(src or "").strip()
    if not value:
        return "empty"
    lowered = value.lower()
    if lowered.startswith("data:image/"):
        return "data_image"
    if "localhost" in lowered or "127.0.0.1" in lowered:
        return "localhost_resolved" if _source_html_local_image_path(value, source) else "localhost_url"
    if lowered.startswith("http://") or lowered.startswith("https://"):
        return "remote_unresolved"
    if _source_html_local_image_path(value, source):
        return "artifact_resolved" if _source_html_asset_path_text(value).startswith("/") else "relative_resolved"
    return "relative_unresolved"


def _source_html_image_status_resolved(status: str) -> bool:
    return str(status or "") in {"data_image", "relative_resolved", "artifact_resolved", "localhost_resolved"}


def _source_html_image_missing_reason(status: str) -> str:
    return {
        "empty": "empty_src",
        "localhost_url": "localhost_asset_unresolved",
        "remote_unresolved": "remote_image_not_embedded",
        "relative_unresolved": "source_asset_unresolved",
    }.get(str(status or ""), "")


def _source_html_asset_path_text(src: str) -> str:
    value = str(src or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        path_text = parsed.path
    else:
        path_text = value.split("#", 1)[0].split("?", 1)[0]
    return unquote(path_text).replace("\\", "/").strip()


def _source_html_local_image_candidates(src: str, source: Path) -> list[Path]:
    clean = _source_html_asset_path_text(src)
    if not clean:
        return []
    source_dir = source.parent
    candidates: list[Path] = []
    clean_path = Path(clean)
    if clean_path.is_absolute():
        candidates.append(clean_path)
    if not clean.startswith("/"):
        candidates.append(source_dir / clean)
    relative = clean.lstrip("/")
    if relative:
        candidates.append(source_dir / relative)
    asset_name = Path(relative).name
    if asset_name:
        candidates.extend(
            [
                source_dir / asset_name,
                source_dir / "assets" / asset_name,
                source_dir / "assets" / "diagrams" / asset_name,
                source_dir / "artifact" / asset_name,
                source_dir / "artifacts" / asset_name,
            ]
        )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _source_html_local_image_path(src: str, source: Path) -> Path | None:
    for candidate in _source_html_local_image_candidates(src, source):
        if candidate.is_file():
            return candidate
    return None


def _source_html_diagram_has_evidence(diagram_node: Any, image_node: Any | None) -> bool:
    evidence_keys = {
        "data_fen",
        "data_fen_candidate",
        "fen",
        "side_marker_status",
        "side_marker_bbox",
        "side_marker_confidence",
        "side_marker_crop_path",
        "side_to_move",
        "side_to_move_evidence",
        "board_crop_path",
        "board_bbox",
    }
    for node in [diagram_node, image_node]:
        if not node:
            continue
        for key, value in getattr(node, "attrs", {}).items():
            normalized = str(key or "").replace("-", "_").lower()
            if normalized in evidence_keys and str(value or "").strip():
                return True
    return False


def _source_html_fen_accepted(fen: str) -> bool:
    value = str(fen or "").strip()
    if not value:
        return False
    valid, warnings = validate_fen(value)
    return bool(valid and not warnings)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _generated_at_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _commit_sha_with_reason() -> tuple[str, str]:
    commit = _current_git_commit()
    if commit:
        return commit, ""
    return "", "git_rev_parse_head_unavailable"


def _first_int_from_mapping(source: Mapping[str, Any], keys: Iterable[str], default: int = 0) -> int:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return _safe_int(value)
    return default


def _count_rows_with_fen_evidence(rows: Iterable[Mapping[str, Any]]) -> int:
    evidence_keys = ("fen", "full_fen", "fen_candidate", "placement", "placement_fen")
    return sum(
        1
        for row in rows
        if any(str(row.get(key) or "").strip() for key in evidence_keys)
    )


def _artifact_metrics(
    *,
    summary: Mapping[str, Any] | None = None,
    positions: Iterable[Mapping[str, Any]] | None = None,
    diagrams: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, int]:
    summary = dict(summary or {})
    position_list = list(positions or [])
    diagram_list = list(diagrams or [])
    side_rows = position_list or diagram_list
    side_unknown_count = _first_int_from_mapping(summary, ("side_unknown_count",), -1)
    if side_unknown_count < 0:
        side_unknown_count = len([row for row in side_rows if str(row.get("side_to_move") or "unknown") == "unknown"])
    trusted_marker_count = _first_int_from_mapping(summary, ("trusted_marker_count", "trusted_marker_assignments"), -1)
    if trusted_marker_count < 0:
        trusted_marker_count = len(
            [
                row
                for row in side_rows
                if str(row.get("side_marker_status") or "").startswith("trusted_")
                or str(row.get("side_marker_status") or "") == "trusted_marker"
            ]
        )
    side_marker_crop_count = _first_int_from_mapping(summary, ("side_marker_crop_count",), -1)
    if side_marker_crop_count < 0:
        side_marker_crop_count = len([row for row in side_rows if str(row.get("side_marker_crop_path") or "").strip()])
    board_crop_count = _first_int_from_mapping(summary, ("board_crop_count",), -1)
    if board_crop_count < 0:
        board_crop_count = len(
            [
                row
                for row in side_rows
                if str(row.get("board_crop_path") or row.get("source_crop") or "").strip()
            ]
        )
    fen_evidence_count = _first_int_from_mapping(
        summary,
        ("fen_evidence_count", "placement_accepted_count", "fen_candidate_count", "full_fen_accepted_count"),
        -1,
    )
    if fen_evidence_count < 0:
        fen_evidence_count = _count_rows_with_fen_evidence(position_list or diagram_list)
    return {
        "side_unknown_count": side_unknown_count,
        "trusted_marker_count": trusted_marker_count,
        "side_marker_crop_count": side_marker_crop_count,
        "board_crop_count": board_crop_count,
        "empty_img_src_count": _first_int_from_mapping(
            summary,
            (
                "empty_img_src_count",
                "source_img_empty_count",
                "empty_diagram_image_count",
                "asset_missing_empty_src_count",
            ),
            0,
        ),
        "diagrams_total": _first_int_from_mapping(
            summary,
            ("diagrams_total", "strict_diagrams_total", "total"),
            len(position_list) or len(diagram_list),
        ),
        "fen_accepted": _first_int_from_mapping(summary, ("fen_accepted", "fens_accepted", "full_fen_accepted_count"), 0),
        "fen_evidence_count": fen_evidence_count,
    }


def _build_artifact_manifest(
    *,
    artifact_type: str,
    pipeline_mode: str,
    source_pdf: str | Path | None = None,
    source_html: str | Path | None = None,
    source_gate: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
    positions: Iterable[Mapping[str, Any]] | None = None,
    diagrams: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    commit_sha, commit_sha_reason = _commit_sha_with_reason()
    gate = dict(source_gate or {})
    manifest = {
        "schema": "kindlemaster.chess_study.artifact_manifest.v1",
        "artifact_type": artifact_type,
        "pipeline_mode": pipeline_mode,
        "generated_at": _generated_at_utc(),
        "source_pdf": str(source_pdf or ""),
        "source_html": str(source_html or ""),
        "commit_sha": commit_sha,
        "commit_sha_reason": commit_sha_reason,
        "source_html_quality_gate": {
            "decision": str(gate.get("decision") or ""),
            "source_html_evidence_only": bool(gate.get("source_html_evidence_only")),
            "used_as_final_reader": bool(gate.get("used_as_final_reader")),
            "reasons": list(gate.get("reasons") or []),
        },
    }
    manifest.update(_artifact_metrics(summary=summary, positions=positions, diagrams=diagrams))
    return manifest


def _write_artifact_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    _write_json(path, dict(manifest))


def _artifact_data_attrs(manifest: Mapping[str, Any]) -> str:
    gate = manifest.get("source_html_quality_gate") if isinstance(manifest.get("source_html_quality_gate"), Mapping) else {}
    attrs = {
        "data-artifact-type": manifest.get("artifact_type", ""),
        "data-pipeline-mode": manifest.get("pipeline_mode", ""),
        "data-commit-sha": manifest.get("commit_sha", ""),
        "data-source-html-gate-decision": gate.get("decision", "") if isinstance(gate, Mapping) else "",
        "data-generated-at": manifest.get("generated_at", ""),
    }
    return " ".join(f'{name}="{html.escape(str(value), quote=True)}"' for name, value in attrs.items())


def _artifact_provenance_banner_html(manifest: Mapping[str, Any]) -> str:
    gate = manifest.get("source_html_quality_gate") if isinstance(manifest.get("source_html_quality_gate"), Mapping) else {}
    decision = str(gate.get("decision") or "") if isinstance(gate, Mapping) else ""
    warning = ""
    if manifest.get("artifact_type") == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE:
        warning = "<strong>Evidence-only report. This is not the final reader download.</strong>"
    return f"""<section class="artifact-provenance" aria-label="Artifact provenance">
  <h2>Artifact provenance</h2>
  <p>Artifact type: <strong>{html.escape(str(manifest.get('artifact_type') or ''))}</strong></p>
  <p>Pipeline mode: <code>{html.escape(str(manifest.get('pipeline_mode') or ''))}</code></p>
  <p>Source HTML gate: <code>{html.escape(decision)}</code></p>
  {f'<p class="artifact-warning">{warning}</p>' if warning else ''}
</section>"""


def _inject_artifact_attrs_and_banner(html_text: str, manifest: Mapping[str, Any]) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    if soup.body is None:
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<title>Evidence-only source HTML - KindleMaster</title></head>"
            f"<body {_artifact_data_attrs(manifest)}>{_inline_artifact_banner_html(manifest)}"
            f"<pre>{html.escape(html_text)}</pre></body></html>"
        )
    for name, value in _artifact_attrs_dict(manifest).items():
        soup.body[name] = value
    if soup.html is not None:
        for name, value in _artifact_attrs_dict(manifest).items():
            soup.html[name] = value
    title = soup.find("title")
    if manifest.get("artifact_type") == SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE:
        if title is None:
            if soup.head is None and soup.html is not None:
                head = soup.new_tag("head")
                soup.html.insert(0, head)
            if soup.head is not None:
                title = soup.new_tag("title")
                soup.head.append(title)
        if title is not None:
            title.string = "Evidence-only source HTML - KindleMaster"
    soup.body.insert(0, BeautifulSoup(_inline_artifact_banner_html(manifest), "html.parser"))
    return str(soup)


def _artifact_attrs_dict(manifest: Mapping[str, Any]) -> dict[str, str]:
    gate = manifest.get("source_html_quality_gate") if isinstance(manifest.get("source_html_quality_gate"), Mapping) else {}
    return {
        "data-artifact-type": str(manifest.get("artifact_type") or ""),
        "data-pipeline-mode": str(manifest.get("pipeline_mode") or ""),
        "data-commit-sha": str(manifest.get("commit_sha") or ""),
        "data-source-html-gate-decision": str(gate.get("decision") or "") if isinstance(gate, Mapping) else "",
        "data-generated-at": str(manifest.get("generated_at") or ""),
    }


def _inline_artifact_banner_html(manifest: Mapping[str, Any]) -> str:
    return f"""<section style="border:2px solid #9a1b1b;background:#fff5e4;color:#201713;padding:12px;margin:0 0 16px;font-family:Arial,sans-serif" class="artifact-provenance" aria-label="Artifact provenance">
  <strong>Artifact provenance</strong>
  <div>Artifact type: <code>{html.escape(str(manifest.get('artifact_type') or ''))}</code></div>
  <div>Pipeline mode: <code>{html.escape(str(manifest.get('pipeline_mode') or ''))}</code></div>
  <div>Evidence-only source HTML: this report must not be used as the final reader download.</div>
</section>"""


def _copy_source_html_evidence(
    source: Path,
    out: Path,
    *,
    pdf_path: str | Path | None = None,
    summary: Mapping[str, Any] | None = None,
    decision: str = "reject_degraded_source_html",
) -> str:
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / "source_html_evidence.html"
    manifest = _build_artifact_manifest(
        artifact_type=SOURCE_HTML_EVIDENCE_ARTIFACT_TYPE,
        pipeline_mode="source_html_evidence_report",
        source_pdf=pdf_path,
        source_html=source,
        source_gate={
            "decision": decision,
            "source_html_evidence_only": True,
            "used_as_final_reader": False,
        },
        summary=summary,
    )
    try:
        source_text = source.read_text(encoding="utf-8", errors="replace")
        target.write_text(_inject_artifact_attrs_and_banner(source_text, manifest), encoding="utf-8")
        _write_artifact_manifest(reports_dir / "source_html_evidence_manifest.json", manifest)
    except OSError:
        return ""
    return str(target.relative_to(out)).replace("\\", "/")


def _write_source_html_quality_gate(out_dir: str | Path, payload: dict[str, Any]) -> None:
    out = Path(out_dir)
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_json(reports_dir / "source_html_quality_gate.json", payload)


def _unique_html_nodes(nodes: Iterable[Any]) -> list[Any]:
    unique: list[Any] = []
    seen: set[int] = set()
    for node in nodes:
        key = id(node)
        if key in seen:
            continue
        seen.add(key)
        unique.append(node)
    return unique


def _count_rows_with_value(rows: Iterable[Mapping[str, Any]], key: str) -> int:
    return sum(1 for row in rows if str(row.get(key) or "").strip())


def _trusted_marker_status(value: Any) -> bool:
    status = str(value or "").strip()
    return status == "trusted_marker" or status.startswith("trusted_")


def _side_unknown_rows(rows: Iterable[Mapping[str, Any]]) -> int:
    return sum(1 for row in rows if str(row.get("side_to_move") or row.get("side_to_move_code") or "unknown") not in {"w", "b", "white", "black"})


def _html_side_unknown_count(cards: list[Any], soup: BeautifulSoup) -> int:
    count = 0
    for card in cards:
        text = card.get_text(" ", strip=True)
        if re.search(r"\bSide\s+to\s+move:\s*unknown\b", text, flags=re.IGNORECASE):
            count += 1
    if count:
        return count
    return len(re.findall(r"Side\s+to\s+move:\s*unknown", soup.get_text(" ", strip=True), flags=re.IGNORECASE))


def _first_int_from_text(value: str) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def _scorebar_metric_int(soup: BeautifulSoup, labels: Iterable[str]) -> int:
    wanted = {str(label).strip().lower() for label in labels}
    for score in soup.select(".score"):
        label_node = score.select_one(".score-label")
        value_node = score.select_one(".score-value")
        label = label_node.get_text(" ", strip=True).lower() if label_node else ""
        if label in wanted:
            return _first_int_from_text(value_node.get_text(" ", strip=True) if value_node else score.get_text(" ", strip=True))
    text = soup.get_text(" ", strip=True)
    for label in wanted:
        pattern = re.escape(label).replace("\\ ", r"\s+")
        match = re.search(rf"{pattern}\s*[:\-]?\s*(\d+)", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def _broken_final_reader_signature_conditions(
    *,
    diagram_cards_count: int,
    fen_accepted: int,
    fen_evidence_count: int,
    side_unknown_rate: float,
    data_side_marker_attr_count: int,
    side_marker_crop_count: int,
    asset_missing_empty_src_count: int,
    empty_img_src_count: int,
) -> dict[str, bool]:
    return {
        "diagrams_present": diagram_cards_count > 0,
        "fen_accepted_zero": fen_accepted == 0,
        "fen_evidence_zero": fen_evidence_count == 0,
        "side_unknown_rate_gte_0_8": side_unknown_rate >= 0.8,
        "missing_side_marker_attrs": data_side_marker_attr_count == 0,
        "missing_side_marker_crops": side_marker_crop_count == 0,
        "asset_missing_empty_src": asset_missing_empty_src_count > 0 or empty_img_src_count > 0,
    }


def _build_final_reader_health_gate(
    *,
    html_text: str,
    artifact_manifest: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
    positions: Iterable[Mapping[str, Any]] | None = None,
    diagrams: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = dict(artifact_manifest or {})
    summary = dict(summary or {})
    row_list = list(positions or []) or list(diagrams or [])
    soup = BeautifulSoup(html_text or "", "html.parser")
    diagram_cards = _unique_html_nodes(
        list(soup.select("article.card[data-position-status]"))
        + list(soup.select("figure.diagram-card"))
        + list(soup.select('[data-kind="diagram"]'))
    )
    side_marker_nodes = list(soup.select("[data-side-marker-status]"))
    html_trusted_marker_count = sum(
        1 for node in side_marker_nodes if _trusted_marker_status(node.get("data-side-marker-status"))
    )
    row_side_marker_status_count = _count_rows_with_value(row_list, "side_marker_status")
    row_trusted_marker_count = sum(1 for row in row_list if _trusted_marker_status(row.get("side_marker_status")))
    board_crop_count = max(
        len(soup.select('[data-has-board-crop="true"]')),
        _count_rows_with_value(row_list, "board_crop_path"),
        _count_rows_with_value(row_list, "source_crop"),
    )
    side_marker_crop_count = max(
        len(soup.select('[data-has-side-marker-crop="true"]')),
        _count_rows_with_value(row_list, "side_marker_crop_path"),
    )
    scorebar_diagrams_count = _scorebar_metric_int(soup, ("diagrams",))
    scorebar_fen_accepted = _scorebar_metric_int(soup, ("fen accepted", "fen"))
    scorebar_needs_review = _scorebar_metric_int(soup, ("needs review",))
    empty_img_src_count = sum(1 for image in soup.find_all("img") if not str(image.get("src") or "").strip())
    html_fen_evidence_count = sum(
        1 for node in soup.select("[data-fen]") if str(node.get("data-fen") or "").strip()
    ) + len(
        [
            node
            for node in soup.select(".candidate code, pre.fen code")
            if node.get_text(" ", strip=True)
        ]
    )
    asset_missing_empty_src_count = max(
        len(soup.select('[data-asset-missing-reason="empty_src"]')),
        sum(1 for row in row_list if str(row.get("asset_missing_reason") or "") == "empty_src"),
        _first_int_from_mapping(summary, ("asset_missing_empty_src_count", "empty_diagram_image_count"), 0),
    )
    side_unknown_count = max(
        _html_side_unknown_count(diagram_cards, soup),
        _safe_int(manifest.get("side_unknown_count")),
        _side_unknown_rows(row_list),
    )
    diagram_cards_count = max(
        len(diagram_cards),
        scorebar_diagrams_count,
        _safe_int(manifest.get("diagrams_total")),
        _first_int_from_mapping(summary, ("diagrams_total", "strict_diagrams_total", "total"), 0),
        len(row_list),
    )
    trusted_marker_count = max(
        html_trusted_marker_count,
        row_trusted_marker_count,
        _safe_int(manifest.get("trusted_marker_count")),
        _first_int_from_mapping(summary, ("trusted_marker_count", "trusted_marker_assignments"), 0),
    )
    data_side_marker_attr_count = max(len(side_marker_nodes), row_side_marker_status_count)
    fen_accepted = max(
        scorebar_fen_accepted,
        _safe_int(manifest.get("fen_accepted")),
        _first_int_from_mapping(summary, ("fen_accepted", "fens_accepted", "full_fen_accepted_count"), 0),
    )
    fen_evidence_count = max(
        html_fen_evidence_count,
        _safe_int(manifest.get("fen_evidence_count")),
        _first_int_from_mapping(
            summary,
            ("fen_evidence_count", "placement_accepted_count", "fen_candidate_count", "full_fen_accepted_count"),
            0,
        ),
        _count_rows_with_fen_evidence(row_list),
        fen_accepted,
    )
    needs_review_count = max(
        scorebar_needs_review,
        _first_int_from_mapping(summary, ("needs_review_count", "fen_needs_review", "fens_needs_review"), 0),
    )
    side_unknown_rate = round(side_unknown_count / diagram_cards_count, 4) if diagram_cards_count else 0.0
    broken_signature_conditions = _broken_final_reader_signature_conditions(
        diagram_cards_count=diagram_cards_count,
        fen_accepted=fen_accepted,
        fen_evidence_count=fen_evidence_count,
        side_unknown_rate=side_unknown_rate,
        data_side_marker_attr_count=data_side_marker_attr_count,
        side_marker_crop_count=side_marker_crop_count,
        asset_missing_empty_src_count=asset_missing_empty_src_count,
        empty_img_src_count=empty_img_src_count,
    )
    blockers: list[str] = []
    warnings: list[str] = []
    artifact_type = str(manifest.get("artifact_type") or "")
    if artifact_type != FINAL_READER_ARTIFACT_TYPE:
        blockers.append("not_final_reader_artifact")
    if empty_img_src_count:
        blockers.append("empty_img_src")
    if diagram_cards_count and fen_accepted == 0:
        if fen_evidence_count == 0:
            blockers.append("fen_accepted_zero")
        else:
            warnings.append("fen_requires_review")
    mass_unknown_threshold = max(2, math.ceil(max(diagram_cards_count, 1) * 0.5))
    if side_unknown_count >= mass_unknown_threshold:
        blockers.append("mass_side_to_move_unknown")
    elif side_unknown_count:
        warnings.append("side_to_move_unknown_present")
    if artifact_type == FINAL_READER_ARTIFACT_TYPE and diagram_cards_count and data_side_marker_attr_count == 0 and trusted_marker_count == 0:
        blockers.append("missing_side_marker_evidence")
    if (
        broken_signature_conditions["diagrams_present"]
        and broken_signature_conditions["fen_accepted_zero"]
        and broken_signature_conditions["side_unknown_rate_gte_0_8"]
        and broken_signature_conditions["missing_side_marker_attrs"]
    ):
        blockers.append("broken_latest_html_signature")
    if asset_missing_empty_src_count:
        blockers.append("asset_missing_empty_src")
    decision = "fail" if blockers else "pass"
    return {
        "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
        "generated_at": _generated_at_utc(),
        "decision": decision,
        "status": "FAIL" if blockers else ("PASS_WITH_WARNINGS" if warnings else "PASS"),
        "artifact_type": artifact_type,
        "pipeline_mode": str(manifest.get("pipeline_mode") or ""),
        "diagram_cards_count": diagram_cards_count,
        "side_unknown_count": side_unknown_count,
        "side_unknown_rate": side_unknown_rate,
        "data_side_marker_attr_count": data_side_marker_attr_count,
        "trusted_marker_count": trusted_marker_count,
        "side_marker_crop_count": side_marker_crop_count,
        "board_crop_count": board_crop_count,
        "fen_accepted": fen_accepted,
        "fen_evidence_count": fen_evidence_count,
        "needs_review_count": needs_review_count,
        "empty_img_src_count": empty_img_src_count,
        "asset_missing_empty_src_count": asset_missing_empty_src_count,
        "broken_signature_conditions": broken_signature_conditions,
        "blockers": blockers,
        "warnings": warnings,
    }


def _build_final_reader_health_gate_from_records(
    *,
    artifact_manifest: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
    positions: Iterable[Mapping[str, Any]] | None = None,
    diagrams: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = dict(artifact_manifest or {})
    summary = dict(summary or {})
    row_list = list(positions or []) or list(diagrams or [])
    artifact_type = FINAL_READER_ARTIFACT_TYPE if manifest.get("artifact_type") == FINAL_READER_ARTIFACT_TYPE else "unknown"
    pipeline_mode = str(manifest.get("pipeline_mode") or "")
    if pipeline_mode not in {"pdf_two_crop_reader", "source_html_semantic_reader"}:
        pipeline_mode = "unknown"
    side_unknown_count = max(_safe_int(manifest.get("side_unknown_count")), _side_unknown_rows(row_list))
    trusted_marker_count = max(
        sum(1 for row in row_list if _trusted_marker_status(row.get("side_marker_status"))),
        _safe_int(manifest.get("trusted_marker_count")),
        _first_int_from_mapping(summary, ("trusted_marker_count", "trusted_marker_assignments"), 0),
    )
    diagram_cards_count = max(
        _safe_int(manifest.get("diagrams_total")),
        _first_int_from_mapping(summary, ("diagrams_total", "strict_diagrams_total", "total"), 0),
        len(row_list),
    )
    data_side_marker_attr_count = _count_rows_with_value(row_list, "side_marker_status")
    side_marker_crop_count = _count_rows_with_value(row_list, "side_marker_crop_path")
    fen_accepted = max(
        _safe_int(manifest.get("fen_accepted")),
        _first_int_from_mapping(summary, ("fen_accepted", "fens_accepted", "full_fen_accepted_count"), 0),
    )
    fen_evidence_count = max(
        _safe_int(manifest.get("fen_evidence_count")),
        _first_int_from_mapping(
            summary,
            ("fen_evidence_count", "placement_accepted_count", "fen_candidate_count", "full_fen_accepted_count"),
            0,
        ),
        _count_rows_with_fen_evidence(row_list),
        fen_accepted,
    )
    needs_review_count = _first_int_from_mapping(
        summary,
        ("needs_review_count", "fen_needs_review", "fens_needs_review"),
        0,
    )
    side_unknown_rate = round(side_unknown_count / diagram_cards_count, 4) if diagram_cards_count else 0.0
    empty_img_src_count = _first_int_from_mapping(summary, ("empty_img_src_count", "source_img_empty_count"), 0)
    asset_missing_empty_src_count = max(
        sum(1 for row in row_list if str(row.get("asset_missing_reason") or "") == "empty_src"),
        _first_int_from_mapping(summary, ("asset_missing_empty_src_count", "empty_diagram_image_count"), 0),
    )
    broken_signature_conditions = _broken_final_reader_signature_conditions(
        diagram_cards_count=diagram_cards_count,
        fen_accepted=fen_accepted,
        fen_evidence_count=fen_evidence_count,
        side_unknown_rate=side_unknown_rate,
        data_side_marker_attr_count=data_side_marker_attr_count,
        side_marker_crop_count=side_marker_crop_count,
        asset_missing_empty_src_count=asset_missing_empty_src_count,
        empty_img_src_count=empty_img_src_count,
    )
    blockers: list[str] = []
    warnings: list[str] = []
    if artifact_type != FINAL_READER_ARTIFACT_TYPE:
        blockers.append("not_final_reader_artifact")
    if empty_img_src_count:
        blockers.append("empty_img_src")
    if diagram_cards_count and fen_accepted == 0:
        if fen_evidence_count == 0:
            blockers.append("fen_accepted_zero")
        else:
            warnings.append("fen_requires_review")
    mass_unknown_threshold = max(2, math.ceil(max(diagram_cards_count, 1) * 0.5))
    if side_unknown_count >= mass_unknown_threshold:
        blockers.append("mass_side_to_move_unknown")
    elif side_unknown_count:
        warnings.append("side_to_move_unknown_present")
    if artifact_type == FINAL_READER_ARTIFACT_TYPE and diagram_cards_count and data_side_marker_attr_count == 0 and trusted_marker_count == 0:
        blockers.append("missing_side_marker_evidence")
    if (
        broken_signature_conditions["diagrams_present"]
        and broken_signature_conditions["fen_accepted_zero"]
        and broken_signature_conditions["side_unknown_rate_gte_0_8"]
        and broken_signature_conditions["missing_side_marker_attrs"]
    ):
        blockers.append("broken_latest_html_signature")
    if asset_missing_empty_src_count:
        blockers.append("asset_missing_empty_src")
    return {
        "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
        "generated_at": _generated_at_utc(),
        "decision": "fail" if blockers else "pass",
        "status": "FAIL" if blockers else ("PASS_WITH_WARNINGS" if warnings else "PASS"),
        "artifact_type": artifact_type,
        "pipeline_mode": pipeline_mode,
        "diagram_cards_count": diagram_cards_count,
        "side_unknown_count": side_unknown_count,
        "side_unknown_rate": side_unknown_rate,
        "data_side_marker_attr_count": data_side_marker_attr_count,
        "trusted_marker_count": trusted_marker_count,
        "side_marker_crop_count": side_marker_crop_count,
        "board_crop_count": max(_count_rows_with_value(row_list, "board_crop_path"), _count_rows_with_value(row_list, "source_crop")),
        "fen_accepted": fen_accepted,
        "fen_evidence_count": fen_evidence_count,
        "needs_review_count": needs_review_count,
        "empty_img_src_count": empty_img_src_count,
        "asset_missing_empty_src_count": asset_missing_empty_src_count,
        "broken_signature_conditions": broken_signature_conditions,
        "blockers": blockers,
        "warnings": warnings,
    }


def _write_final_reader_health_gate(
    out_dir: str | Path,
    *,
    artifact_manifest: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
    positions: Iterable[Mapping[str, Any]] | None = None,
    diagrams: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = _build_final_reader_health_gate_from_records(
        artifact_manifest=artifact_manifest,
        summary=summary,
        positions=positions,
        diagrams=diagrams,
    )
    _write_json(Path(out_dir) / "reports" / "final_reader_health_gate.json", payload)
    return payload


def _extract_source_page_preview(node: Any, *, page_number: int, out_dir: Path) -> str:
    image = node.select_one("img.book-page-bg")
    if not image:
        return ""
    return _save_data_uri_asset(
        str(image.get("src") or ""),
        out_dir,
        filename_stem=f"page_{page_number:03d}",
        default_ext=".jpg",
        relative_prefix="assets/pages-preview",
    )


def _extract_source_text_blocks(node: Any, *, page_number: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for index, block in enumerate(node.select(".book-text")):
        text = _scrub_local_links(block.get_text(" ", strip=True))
        if not text or _is_technical_audit_text(text):
            continue
        bbox = _source_style_box(str(block.get("style") or ""))
        blocks.append(
            {
                "id": f"text-p{page_number:03d}-{index + 1:04d}",
                "page": page_number,
                "reading_order": _source_reading_order(block, fallback=index),
                "bbox": bbox,
                "text": text,
                "kind": _source_text_kind(text),
            }
        )
    blocks.sort(key=_source_order_key)
    return blocks


def _extract_source_diagrams(
    node: Any,
    *,
    page_number: int,
    out_dir: Path,
    source_html: Path | None = None,
) -> list[dict[str, Any]]:
    diagrams: list[dict[str, Any]] = []
    captions = [
        block
        for block in _extract_source_text_blocks(node, page_number=page_number)
        if re.search(r"\b(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\b", block.get("text", ""), re.IGNORECASE)
    ]
    for index, diagram in enumerate(node.select(".book-diagram"), start=1):
        image = diagram.select_one("img")
        bbox = _source_style_box(str(diagram.get("style") or ""))
        diagram_id = f"p{page_number:03d}_d{index:03d}"
        image_src = str(image.get("src") or "") if image else ""
        image_asset = _resolve_source_diagram_image_asset(
            image_src,
            source_html=source_html,
            out_dir=out_dir,
            filename_stem=diagram_id,
            default_ext=".png",
            relative_prefix="assets/diagrams",
        )
        image_path = str(image_asset.get("image_path") or "")
        alt = _scrub_local_links(str(image.get("alt") or "")) if image else ""
        caption = _nearest_source_caption(bbox, captions) or alt or f"Diagram on page {page_number}"
        fen_candidate = _extract_fen_candidate_from_node(diagram)
        fen_status = _source_fen_status(fen_candidate, image_path=image_path)
        diagrams.append(
            {
                "id": diagram_id,
                "page": page_number,
                "reading_order": _source_reading_order(diagram, fallback=10_000 + index),
                "bbox": bbox,
                "caption": caption,
                "alt": alt,
                "image_path": image_path,
                "image_source_status": image_asset.get("image_source_status") or "empty",
                "asset_missing_reason": image_asset.get("asset_missing_reason") or "",
                "source_image_src": _source_image_src_for_record(image_src),
                "fen": fen_candidate if fen_status["validation_status"] == "accepted" else "",
                "fen_candidate": fen_candidate,
                "confidence": fen_status["confidence"],
                "side_to_move": _infer_side_to_move(caption),
                "validation_status": fen_status["validation_status"],
                "review_reason": fen_status["review_reason"],
            }
        )
    diagrams.sort(key=_source_order_key)
    return diagrams


def _source_image_src_for_record(src: str) -> str:
    value = str(src or "").strip()
    lowered = value.lower()
    if not value or lowered.startswith("data:image/") or "localhost" in lowered or "127.0.0.1" in lowered:
        return ""
    return value


def _resolve_source_diagram_image_asset(
    src: str,
    *,
    source_html: Path | None,
    out_dir: Path,
    filename_stem: str,
    default_ext: str,
    relative_prefix: str,
) -> dict[str, str]:
    value = str(src or "").strip()
    source = source_html or Path()
    status = _source_html_image_status(value, source) if source_html else ("data_image" if value.lower().startswith("data:image/") else "empty")
    if not value:
        return {"image_path": "", "image_source_status": "empty", "asset_missing_reason": "empty_src"}
    if value.lower().startswith("data:image/"):
        image_path = _save_data_uri_asset(
            value,
            out_dir,
            filename_stem=filename_stem,
            default_ext=default_ext,
            relative_prefix=relative_prefix,
        )
        return {
            "image_path": image_path,
            "image_source_status": "data_image" if image_path else "data_image_decode_failed",
            "asset_missing_reason": "" if image_path else "data_image_decode_failed",
        }
    if not source_html:
        return {
            "image_path": "",
            "image_source_status": "source_html_missing",
            "asset_missing_reason": "source_html_missing",
        }
    local_path = _source_html_local_image_path(value, source)
    if local_path is None:
        return {
            "image_path": "",
            "image_source_status": status,
            "asset_missing_reason": _source_html_image_missing_reason(status) or "source_asset_unresolved",
        }
    suffix = local_path.suffix.lower()
    if suffix not in {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}:
        suffix = default_ext
    target = out_dir / f"{_safe_filename(filename_stem)}{suffix}"
    try:
        shutil.copyfile(local_path, target)
    except OSError:
        return {
            "image_path": "",
            "image_source_status": "copy_failed",
            "asset_missing_reason": "source_asset_copy_failed",
        }
    return {
        "image_path": str(Path(relative_prefix) / target.name).replace("\\", "/"),
        "image_source_status": status if _source_html_image_status_resolved(status) else "relative_resolved",
        "asset_missing_reason": "",
    }


def _extract_source_html_pgn_records(soup: BeautifulSoup) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, node in enumerate(soup.select(".book-pgn-record"), start=1):
        page_node = node.find_parent(class_="chess-book-page") or node.find_parent(attrs={"data-page": True})
        source_page = _source_page_number(page_node, fallback=1) if page_node else 1
        raw_text = _scrub_local_links(node.get_text(" ", strip=True))
        visible_text = _visible_review_notation_text(raw_text)
        label_match = re.search(r"\b(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\b", raw_text, flags=re.IGNORECASE)
        label = label_match.group(0).replace(" .", ".") if label_match else f"Notation {index}"
        pgn_candidate = _extract_embedded_pgn_candidate(node)
        accepted = bool(pgn_candidate and _pgn_has_required_headers(pgn_candidate) and _pgn_has_source_page(pgn_candidate) and _pgn_replay_clean(pgn_candidate))
        warnings = _source_pgn_warnings(raw_text, pgn_candidate, accepted=accepted)
        records.append(
            {
                "id": f"pgn_{index:04d}",
                "source_page": source_page,
                "logical_page": source_page,
                "reading_order": _source_reading_order(node, fallback=20_000 + index),
                "bbox": _source_style_box(str(node.get("style") or "")),
                "label": label,
                "raw_text": raw_text,
                "visible_review_text": visible_text,
                "pgn": pgn_candidate if accepted else "",
                "status": "accepted" if accepted else "needs-human-review",
                "warnings": warnings,
            }
        )
    return records


def _link_source_pgn_records_to_pages(records: list[dict[str, Any]], pages: list[dict[str, Any]]) -> None:
    pages_by_number = {int(page.get("page") or 0): page for page in pages}
    caption_page_by_label: dict[str, int] = {}
    for page in pages:
        for block in page.get("text_blocks", []) or []:
            for label in re.findall(r"\b(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\b", str(block.get("text") or ""), re.IGNORECASE):
                caption_page_by_label[_normalize_source_label(label)] = int(page.get("page") or 0)
    for record in records:
        label_page = caption_page_by_label.get(_normalize_source_label(str(record.get("label") or "")))
        if label_page:
            record["logical_page"] = label_page
        page = pages_by_number.get(int(record.get("logical_page") or 0)) or pages_by_number.get(int(record.get("source_page") or 0))
        if page is not None:
            page.setdefault("pgn_records", []).append(record)


def _source_html_chapters(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = [
        {"id": "preface", "title": "Preface", "start_page": _first_source_page_matching(pages, r"\bPreface\b") or 1},
        {"id": "introduction", "title": "Introduction", "start_page": _first_source_page_matching(pages, r"\bIntroduction\b") or 1},
    ]
    for chapter_no, title in YUSUPOV_CHAPTERS:
        page = _first_source_page_matching(pages, rf"\b{chapter_no}\s+{re.escape(title)}\b|Chapter\s+{chapter_no}\b")
        chapters.append(
            {
                "id": f"chapter-{chapter_no:02d}",
                "chapter_no": chapter_no,
                "title": title,
                "start_page": page,
            }
        )
    chapters.extend(
        [
            {"id": "exercises", "title": "Exercises", "start_page": _first_source_page_matching(pages, r"\bExercises\b")},
            {"id": "solutions", "title": "Solutions", "start_page": _first_source_page_matching(pages, r"\bSolutions\b")},
            {"id": "appendices", "title": "Appendices", "start_page": _first_source_page_matching(pages, r"\bIndex of Games\b|Recommended Books")},
            {"id": "index", "title": "Index", "start_page": _first_source_page_matching(pages, r"\bIndex\b")},
        ]
    )
    return chapters


def _source_html_summary(
    pages: list[dict[str, Any]],
    pgn_records: list[dict[str, Any]],
    source: Path,
    *,
    pdf_path: str | Path | None,
    qa_report: dict[str, Any] | None,
) -> dict[str, Any]:
    diagrams = [diagram for page in pages for diagram in page.get("diagrams", [])]
    diagram_asset_summary = _source_diagram_asset_summary(diagrams)
    accepted_fen = [diagram for diagram in diagrams if diagram.get("validation_status") == "accepted"]
    accepted_pgn = [record for record in pgn_records if record.get("status") == "accepted"]
    source_text = source.read_text(encoding="utf-8", errors="replace")
    pdf_pages = 0
    if pdf_path and Path(pdf_path).is_file():
        try:
            with fitz.open(Path(pdf_path)) as document:
                pdf_pages = int(document.page_count)
        except Exception:
            pdf_pages = 0
    return {
        "source_html": str(source),
        "source_pdf": str(pdf_path or ""),
        "source_html_bytes": source.stat().st_size if source.is_file() else 0,
        "pdf_pages": pdf_pages,
        "html_pages": len(pages),
        "missing_pages": _missing_page_numbers(pdf_pages, pages),
        "text_blocks": sum(len(page.get("text_blocks", [])) for page in pages),
        "diagrams_total": len(diagrams),
        "empty_diagram_image_count": diagram_asset_summary["empty_diagram_image_count"],
        "resolved_diagram_image_count": diagram_asset_summary["resolved_diagram_image_count"],
        "fen_accepted": len(accepted_fen),
        "fen_needs_review": len(diagrams) - len(accepted_fen),
        "pgn_total": len(pgn_records),
        "accepted_pgn": len(accepted_pgn),
        "pgn_needs_review": len(pgn_records) - len(accepted_pgn),
        "copy_fen_buttons": len(accepted_fen),
        "copy_pgn_buttons": len(accepted_pgn),
        "localhost_links_removed": source_text.count("localhost") + source_text.count("127.0.0.1"),
        "base64_images_extracted": source_text.count("data:image"),
        "qa_status": (qa_report or {}).get("status", ""),
        "status_policy": "accepted_requires_deterministic_validation",
    }


def _source_diagram_asset_summary(diagrams: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "total": len(diagrams),
        "fen_accepted": sum(1 for diagram in diagrams if diagram.get("validation_status") == "accepted"),
        "empty_diagram_image_count": sum(1 for diagram in diagrams if not str(diagram.get("image_path") or "")),
        "resolved_diagram_image_count": sum(1 for diagram in diagrams if str(diagram.get("image_path") or "")),
        "asset_missing_reasons": _top_counts(
            str(diagram.get("asset_missing_reason") or "")
            for diagram in diagrams
            if str(diagram.get("asset_missing_reason") or "")
        ),
    }


def _write_source_html_reports(book: dict[str, Any], diagrams_payload: dict[str, Any], reports_dir: Path) -> None:
    summary = dict(book.get("summary") or {})
    diagrams = list(diagrams_payload.get("diagrams") or [])
    pgn_records = list(book.get("pgn_records") or [])
    lines = [
        "# Conversion Audit",
        "",
        "## Summary",
        "",
        f"- Source HTML: `{summary.get('source_html')}`",
        f"- Source PDF: `{summary.get('source_pdf')}`",
        f"- PDF pages: `{summary.get('pdf_pages')}`",
        f"- HTML pages: `{summary.get('html_pages')}`",
        f"- Missing pages: `{summary.get('missing_pages')}`",
        f"- Text blocks: `{summary.get('text_blocks')}`",
        f"- Diagrams: `{summary.get('diagrams_total')}`",
        f"- Diagram images resolved: `{summary.get('resolved_diagram_image_count')}`",
        f"- Diagram images empty: `{summary.get('empty_diagram_image_count')}`",
        f"- FEN accepted: `{summary.get('fen_accepted')}`",
        f"- FEN needs review: `{summary.get('fen_needs_review')}`",
        f"- PGN records: `{summary.get('pgn_total')}`",
        f"- PGN accepted: `{summary.get('accepted_pgn')}`",
        f"- PGN needs review: `{summary.get('pgn_needs_review')}`",
        f"- Localhost links removed: `{summary.get('localhost_links_removed')}`",
        f"- Base64 images extracted: `{summary.get('base64_images_extracted')}`",
        "",
        "## Main blockers",
        "",
        "- FEN is accepted only after deterministic board recognition and `python-chess` validation.",
        "- PGN is accepted only after parser/replay validation.",
        "- OCR/glyph noise remains in review reports until manually mapped and retested.",
        "",
        "## Review items",
        "",
    ]
    for diagram in diagrams[:80]:
        if diagram.get("validation_status") != "accepted":
            lines.append(f"- FEN review `{diagram.get('id')}` page {diagram.get('page')}: {diagram.get('review_reason')}")
    for record in pgn_records[:80]:
        if record.get("status") != "accepted":
            lines.append(f"- PGN review `{record.get('id')}` page {record.get('logical_page')}: {', '.join(record.get('warnings') or [])}")
    (reports_dir / "conversion-audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_csv(reports_dir / "fen-review.csv", diagrams)
    _write_csv(reports_dir / "pgn-review.csv", pgn_records)
    ocr_lines = ["# OCR Issues", ""]
    blockers: dict[str, int] = {}
    for record in pgn_records:
        for warning in record.get("warnings") or []:
            blockers[str(warning)] = blockers.get(str(warning), 0) + 1
    for warning, count in sorted(blockers.items(), key=lambda item: (-item[1], item[0])):
        ocr_lines.append(f"- `{warning}`: {count}")
    (reports_dir / "ocr-issues.md").write_text("\n".join(ocr_lines) + "\n", encoding="utf-8")


def build_chess_fen_manual_review(
    out_dir: str | Path,
    *,
    html_path: str | Path | None = None,
    pdf_path: str | Path | None = None,
    review_sample_limit: int = 0,
    page_ranges: str = "",
    min_count: int = 0,
) -> dict[str, Any]:
    """Create a manual FEN labeling queue from the semantic source HTML export.

    The queue is intentionally evidence-only. Manual labels are promoted by
    `build_chess_fen_templates`, not by this review page.
    """
    out = Path(out_dir)
    if not (out / "data" / "book.json").is_file():
        if not html_path:
            return {
                "status": "failed",
                "error": "data/book.json missing; provide --html to rebuild the semantic source export first.",
                "out_dir": str(out),
            }
        rebuild_chess_source_html_export(html_path, out, pdf_path=pdf_path)
    book = _load_source_book(out)
    diagrams = _source_book_diagrams(book)
    all_diagrams = list(diagrams)
    page_filter = _parse_page_filter(page_ranges)
    auto_extended_pages: list[int] = []
    if page_filter is not None:
        diagrams = [diagram for diagram in diagrams if int(diagram.get("page") or 0) in page_filter]
        if int(min_count or 0) > 0 and len(diagrams) < int(min_count or 0):
            diagrams, auto_extended_pages = _extend_fen_review_batch(
                diagrams,
                all_diagrams,
                page_filter=page_filter,
                min_count=int(min_count or 0),
            )
    if review_sample_limit and review_sample_limit > 0:
        diagrams = diagrams[: int(review_sample_limit)]
    rows = [_fen_manual_review_row(diagram, out) for diagram in diagrams]

    review_dir = out / "review"
    reports_dir = out / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(review_dir / "fen_manual_draft.jsonl", rows)
    _write_csv(review_dir / "fen_manual_draft.csv", rows)
    (review_dir / "fen_manual_review.html").write_text(_fen_manual_review_html(rows), encoding="utf-8")
    summary = {
        "status": "ok",
        "schema": "kindlemaster.fen_manual_review.v1",
        "diagram_count": len(rows),
        "page_ranges": str(page_ranges or ""),
        "min_count": int(min_count or 0),
        "auto_extended_pages": auto_extended_pages,
        "sampled_pages": sorted({int(row.get("page") or 0) for row in rows if int(row.get("page") or 0)}),
        "source_book": str(out / "data" / "book.json"),
        "review_html": str(review_dir / "fen_manual_review.html"),
        "draft_jsonl": str(review_dir / "fen_manual_draft.jsonl"),
        "policy": "manual_fen labels are evidence until promoted and evaluated; accepted FEN still requires deterministic validation.",
    }
    _write_json(reports_dir / "fen_review_queue.json", summary)
    build_chess_quality_dashboard(out)
    return summary


def _extend_fen_review_batch(
    selected: list[dict[str, Any]],
    all_diagrams: list[dict[str, Any]],
    *,
    page_filter: set[int],
    min_count: int,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Extend a page-range sample with later diagrams without duplicating rows."""
    if len(selected) >= min_count:
        return selected, []
    selected_ids = {str(item.get("id") or item.get("diagram_id") or "") for item in selected}
    max_page = max(page_filter) if page_filter else 0
    extended = list(selected)
    added_pages: set[int] = set()
    for diagram in all_diagrams:
        page = int(diagram.get("page") or 0)
        diagram_id = str(diagram.get("id") or diagram.get("diagram_id") or "")
        if page <= max_page or page in page_filter or diagram_id in selected_ids:
            continue
        extended.append(diagram)
        selected_ids.add(diagram_id)
        if page:
            added_pages.add(page)
        if len(extended) >= min_count:
            break
    return extended, sorted(added_pages)


def build_chess_fen_templates(
    labels_path: str | Path,
    *,
    out_dir: str | Path,
    profile: str = "study_manual_verified",
    template_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Promote verified manual FEN labels and build deterministic templates."""
    from scripts.build_chess_piece_templates import build_templates_from_labels

    out = Path(out_dir)
    reports_dir = out / "reports"
    review_dir = out / "review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    labels = _promote_verified_fen_labels(Path(labels_path), out)
    promoted_path = review_dir / "fen_verified_labels.jsonl"
    _write_jsonl(promoted_path, labels)
    target = Path(template_output_dir) if template_output_dir else out / "assets" / "fen_templates" / _safe_filename(profile)
    if labels:
        template_summary = build_templates_from_labels(promoted_path, output_dir=target)
    else:
        target.mkdir(parents=True, exist_ok=True)
        template_summary = {
            "status": "failed",
            "labels_path": str(promoted_path),
            "output_dir": str(target),
            "boards_processed": 0,
            "template_count": 0,
            "reason": "no_verified_valid_fen_labels",
        }
    summary = {
        "status": "ok" if labels and template_summary.get("status") == "ok" else "failed",
        "schema": "kindlemaster.fen_template_build.v1",
        "profile": profile,
        "labels_path": str(labels_path),
        "promoted_labels_path": str(promoted_path),
        "promoted_label_count": len(labels),
        "template_output_dir": str(target),
        "template_summary": template_summary,
        "policy": "only verified manual FEN labels with valid crops are promoted into template training.",
    }
    _write_json(reports_dir / "fen_template_build.json", summary)
    build_chess_quality_dashboard(out)
    return summary


def evaluate_chess_fen_profile(
    labels_path: str | Path,
    *,
    out_dir: str | Path,
    profile: str = "study_manual_verified",
    fold_count: int = 5,
    holdout_fold: int = 0,
) -> dict[str, Any]:
    """Run holdout evaluation after promoting verified labels.

    The imported evaluator builds templates from the train split only, so the
    holdout rows cannot leak into template construction.
    """
    from scripts.evaluate_chess_fen_profile_holdout import evaluate_chess_fen_profile_holdout

    out = Path(out_dir)
    reports_dir = out / "reports"
    review_dir = out / "review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    promoted_labels = _promote_verified_fen_labels(Path(labels_path), out)
    promoted_path = review_dir / "fen_verified_labels.jsonl"
    _write_jsonl(promoted_path, promoted_labels)
    output_path = reports_dir / "fen_profile_eval.json"
    if promoted_labels:
        payload = evaluate_chess_fen_profile_holdout(
            promoted_path,
            fold_count=fold_count,
            holdout_fold=holdout_fold,
            output_path=output_path,
        )
    else:
        payload = {
            "status": "failed",
            "labels_path": str(labels_path),
            "profile": profile,
            "promoted_label_count": 0,
            "fold_count": max(2, int(fold_count)),
            "holdout_fold": int(holdout_fold),
            "reasons": ["no_verified_valid_fen_labels"],
            "policy": "templates_built_from_train_split_only",
        }
        _write_json(output_path, payload)
    payload["profile"] = profile
    payload["promoted_labels_path"] = str(promoted_path)
    payload["promoted_label_count"] = len(promoted_labels)
    _write_json(output_path, payload)
    _write_diagram_alignment_notes(out, payload)
    build_chess_quality_dashboard(out)
    return payload


def build_chess_quality_baseline(out_dir: str | Path) -> dict[str, Any]:
    """Freeze the current chess-study quality state as a machine-readable baseline."""
    out = Path(out_dir)
    reports_dir = out / "reports"
    review_dir = out / "review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    dashboard = build_chess_quality_dashboard(out)
    artifact_paths = {
        "ai_fen_candidates": review_dir / "ai_fen_candidates.jsonl",
        "fen_verified_labels": review_dir / "fen_verified_labels.jsonl",
        "pgn_lattice": review_dir / "pgn_lattice_review.jsonl",
        "glyph_clusters": review_dir / "deepseek_glyph_clusters.json",
        "fen_profile_eval": reports_dir / "fen_profile_eval.json",
        "pgn_lattice_eval": reports_dir / "pgn_lattice_eval.json",
    }
    artifacts = {
        key: {
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": _file_sha256(path) if path.is_file() else "",
        }
        for key, path in artifact_paths.items()
    }
    payload = {
        "schema": "kindlemaster.chess_quality_baseline.v1",
        "status": "ok",
        "counts": {
            "pages": int(dashboard.get("pages") or 0),
            "diagrams_total": int(dashboard.get("diagrams_total") or 0),
            "fen_accepted": int(dashboard.get("fen_accepted") or 0),
            "fen_needs_review": int(dashboard.get("fen_needs_review") or 0),
            "pgn_total": int(dashboard.get("pgn_total") or 0),
            "accepted_pgn": int(dashboard.get("accepted_pgn") or 0),
            "pgn_needs_review": int(dashboard.get("pgn_needs_review") or 0),
            "ai_fen_candidates": int(dashboard.get("ai_fen_candidates") or 0),
            "ai_pgn_candidates": int(dashboard.get("ai_pgn_candidates") or 0),
        },
        "artifacts": artifacts,
        "policy": "Baseline is evidence only; generated output remains reproducible and is not a Git source of truth.",
    }
    _write_json(reports_dir / "chess_quality_baseline.json", payload)
    return payload


def preprocess_chess_board_crops(
    out_dir: str | Path,
    *,
    limit: int = 0,
    labels_path: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize diagram crops into square board images for downstream FEN work."""
    out = Path(out_dir)
    reports_dir = out / "reports"
    review_dir = out / "review"
    normalized_dir = out / "assets" / "normalized_boards"
    reports_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    records = _board_preprocess_sources(out, labels_path=labels_path)
    if limit and limit > 0:
        records = records[: int(limit)]

    rows: list[dict[str, Any]] = []
    for record in records:
        rows.append(_preprocess_board_record(record, out, normalized_dir))

    _write_jsonl(out / "data" / "board_preprocess.jsonl", rows)
    (review_dir / "board_preprocess_review.html").write_text(
        _board_preprocess_review_html(rows),
        encoding="utf-8",
    )
    ok_count = len([row for row in rows if row.get("status") == "ok"])
    payload = {
        "schema": "kindlemaster.board_preprocess.v1",
        "status": "ok" if rows else "failed",
        "source_count": len(records),
        "normalized_count": ok_count,
        "failure_count": len(rows) - ok_count,
        "normalized_rate": round(ok_count / max(1, len(rows)), 4),
        "normalized_dir": str(normalized_dir),
        "review_html": str(review_dir / "board_preprocess_review.html"),
        "data_path": str(out / "data" / "board_preprocess.jsonl"),
        "accepted_fen_changed": 0,
        "policy": "Preprocessing creates evidence and normalized images only; it never marks FEN accepted.",
    }
    _write_json(reports_dir / "board_preprocess_eval.json", payload)
    build_chess_quality_dashboard(out)
    return payload


def build_fen_square_dataset(
    labels_path: str | Path,
    *,
    out_dir: str | Path,
    fold_count: int = 5,
    holdout_fold: int = 0,
) -> dict[str, Any]:
    """Build a 64-square supervised dataset from verified FEN labels."""
    out = Path(out_dir)
    reports_dir = out / "reports"
    data_dir = out / "data"
    squares_root = out / "assets" / "squares"
    reports_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    squares_root.mkdir(parents=True, exist_ok=True)

    labels = _promote_verified_fen_labels(Path(labels_path), out)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for label in labels:
        crop_path = Path(str(label.get("crop_path") or ""))
        diagram_id = str(label.get("diagram_id") or crop_path.stem)
        try:
            board = _normalize_board_image(crop_path)
            cells = _fen_placement_to_cells(str(label.get("fen") or "").split()[0])
        except Exception as exc:
            skipped.append({"diagram_id": diagram_id, "crop_path": str(crop_path), "reason": str(exc)})
            continue
        split = _dataset_split(diagram_id, fold_count=fold_count, holdout_fold=holdout_fold)
        for index, cell in enumerate(_split_board_into_squares(board)):
            square_name = _square_name(index)
            class_name = cells[index] or "empty"
            target_dir = squares_root / split / class_name
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{_safe_filename(diagram_id)}_{square_name}.png"
            target_path = target_dir / filename
            cell.save(target_path, format="PNG")
            rows.append(
                {
                    "schema": "kindlemaster.fen_square_sample.v1",
                    "diagram_id": diagram_id,
                    "square": square_name,
                    "square_index": index,
                    "class": class_name,
                    "split": split,
                    "image_path": str(target_path),
                    "source_crop": str(crop_path),
                    "fen": label.get("fen"),
                    "page": int(label.get("page") or 0),
                    "label_source": label.get("source") or str(labels_path),
                    "board_sha256": _image_sha256(board),
                }
            )

    dataset_path = data_dir / "fen_square_dataset.jsonl"
    _write_jsonl(dataset_path, rows)
    class_counts = _count_by(rows, "class")
    split_counts = _count_by(rows, "split")
    payload = {
        "schema": "kindlemaster.fen_square_dataset.v1",
        "status": "ok" if rows else "failed",
        "labels_path": str(labels_path),
        "verified_label_count": len(labels),
        "board_count": len({row["diagram_id"] for row in rows}),
        "sample_count": len(rows),
        "class_counts": class_counts,
        "split_counts": split_counts,
        "fold_count": max(2, int(fold_count or 5)),
        "holdout_fold": int(holdout_fold or 0),
        "dataset_path": str(dataset_path),
        "squares_root": str(squares_root),
        "skipped": skipped,
        "policy": "Holdout split is assigned by diagram id and must not be used for training.",
    }
    _write_json(reports_dir / "fen_square_dataset_summary.json", payload)
    build_chess_quality_dashboard(out)
    return payload


def train_fen_square_classifier(
    out_dir: str | Path,
    *,
    dataset_path: str | Path | None = None,
    model_name: str = "chess_fen_square_v1",
) -> dict[str, Any]:
    """Train a lightweight local square classifier profile from dataset samples.

    This v1 uses deterministic feature centroids so it has no heavyweight runtime
    dependency. A later optional trainer can replace the JSON profile with ONNX
    while keeping the same report/model-card contract.
    """
    out = Path(out_dir)
    reports_dir = out / "reports"
    models_dir = out / "models"
    reports_dir.mkdir(parents=True, exist_ok=True)
    models_dir.mkdir(parents=True, exist_ok=True)
    source = Path(dataset_path) if dataset_path else out / "data" / "fen_square_dataset.jsonl"
    rows = _read_jsonl_rows(source)
    train_rows = [row for row in rows if row.get("split") == "train"]
    centroids = _train_centroid_classifier(train_rows)
    model_path = models_dir / f"{_safe_filename(model_name)}.json"
    model = {
        "schema": "kindlemaster.fen_square_classifier.v1",
        "model_type": "feature_centroid",
        "model_name": model_name,
        "dataset_path": str(source),
        "class_centroids": centroids,
        "feature_names": ["mean", "stddev", "dark_ratio", "edge_density"],
        "onnx_available": False,
        "policy": "This model produces candidates only; ensemble validation controls accepted FEN.",
    }
    _write_json(model_path, model)
    eval_payload = _evaluate_square_classifier(rows, model)
    eval_payload.update(
        {
            "schema": "kindlemaster.fen_square_model_eval.v1",
            "status": "ok" if centroids else "failed",
            "model_path": str(model_path),
            "model_type": "feature_centroid",
            "onnx_path": "",
            "onnx_available": False,
        }
    )
    _write_json(reports_dir / "fen_square_model_eval.json", eval_payload)
    _write_square_confusion_csv(reports_dir / "fen_square_confusion_matrix.csv", eval_payload.get("confusion") or {})
    _write_json(
        models_dir / f"{_safe_filename(model_name)}.model-card.json",
        _fen_model_card(out, source, model_path, eval_payload),
    )
    build_chess_quality_dashboard(out)
    return eval_payload


def recognize_fen_local(
    out_dir: str | Path,
    *,
    model_path: str | Path | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    """Run local square-classifier inference and write review-only FEN predictions."""
    out = Path(out_dir)
    review_dir = out / "review"
    reports_dir = out / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    resolved_model = Path(model_path) if model_path else out / "models" / "chess_fen_square_v1.json"
    if not resolved_model.is_file():
        payload = {
            "schema": "kindlemaster.fen_model_runtime_eval.v1",
            "status": "needs_review",
            "reason": "model_missing",
            "model_path": str(resolved_model),
            "prediction_count": 0,
            "accepted_fen_changed": 0,
            "policy": "Missing local model is a clean fallback to review; no accepted FEN changes.",
        }
        _write_json(reports_dir / "fen_model_runtime_eval.json", payload)
        _write_jsonl(review_dir / "fen_model_predictions.jsonl", [])
        return payload

    model = json.loads(resolved_model.read_text(encoding="utf-8"))
    sources = _board_preprocess_sources(out, labels_path=None)
    if limit and limit > 0:
        sources = sources[: int(limit)]
    rows: list[dict[str, Any]] = []
    for source in sources:
        rows.append(_predict_fen_for_source(source, out, model))
    _write_jsonl(review_dir / "fen_model_predictions.jsonl", rows)
    valid_count = len([row for row in rows if row.get("deterministic_validation", {}).get("valid")])
    payload = {
        "schema": "kindlemaster.fen_model_runtime_eval.v1",
        "status": "ok",
        "model_path": str(resolved_model),
        "prediction_count": len(rows),
        "deterministic_valid": valid_count,
        "high_confidence_count": len([row for row in rows if float(row.get("global_confidence") or 0.0) >= 0.92]),
        "accepted_fen_changed": 0,
        "predictions_path": str(review_dir / "fen_model_predictions.jsonl"),
        "policy": "Local predictions are candidates; accepted FEN requires ensemble validation.",
    }
    _write_json(reports_dir / "fen_model_runtime_eval.json", payload)
    build_chess_quality_dashboard(out)
    return payload


def evaluate_fen_ensemble(
    out_dir: str | Path,
    *,
    min_confidence: float = 0.92,
) -> dict[str, Any]:
    """Evaluate deterministic FEN acceptance candidates from local/model/template evidence."""
    out = Path(out_dir)
    reports_dir = out / "reports"
    review_dir = out / "review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    predictions = _read_jsonl_rows(review_dir / "fen_model_predictions.jsonl")
    labels = _read_jsonl_rows(review_dir / "fen_verified_labels.jsonl")
    verified_by_id = {str(row.get("diagram_id") or ""): row for row in labels}
    accepted: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for prediction in predictions:
        verdict = _fen_ensemble_verdict(prediction, verified_by_id, min_confidence=min_confidence)
        if verdict["status"] == "accepted_candidate":
            accepted.append(verdict)
        else:
            conflicts.append(verdict)
    _write_jsonl(review_dir / "fen_ensemble_conflicts.jsonl", conflicts)
    (review_dir / "fen_ensemble_conflicts.html").write_text(
        _fen_ensemble_conflicts_html(conflicts),
        encoding="utf-8",
    )
    payload = {
        "schema": "kindlemaster.fen_ensemble_eval.v1",
        "status": "passed" if accepted else "needs_review",
        "prediction_count": len(predictions),
        "accepted_candidate_count": len(accepted),
        "conflict_count": len(conflicts),
        "min_confidence": float(min_confidence),
        "accepted_fen_changed": 0,
        "policy": "Ensemble produces accepted candidates only; final export updates require the existing strict FEN gate.",
        "accepted_candidates": accepted,
        "top_conflict_reasons": _top_counts(reason for row in conflicts for reason in row.get("reasons") or []),
    }
    _write_json(reports_dir / "fen_ensemble_eval.json", payload)
    build_chess_quality_dashboard(out)
    return payload


def calibrate_fen_confidence(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    predictions = _read_jsonl_rows(out / "review" / "fen_model_predictions.jsonl")
    labels = _read_jsonl_rows(out / "review" / "fen_verified_labels.jsonl")
    label_by_id = {str(row.get("diagram_id") or ""): str(row.get("fen") or "") for row in labels}
    buckets: dict[str, dict[str, int]] = {}
    for row in predictions:
        confidence = float(row.get("global_confidence") or 0.0)
        bucket = f"{int(confidence * 10) / 10:.1f}"
        stats = buckets.setdefault(bucket, {"total": 0, "exact": 0})
        stats["total"] += 1
        if label_by_id.get(str(row.get("diagram_id") or "")) == str(row.get("fen_candidate") or ""):
            stats["exact"] += 1
    reliability = {
        key: {
            **value,
            "accuracy": round(value["exact"] / max(1, value["total"]), 4),
        }
        for key, value in sorted(buckets.items())
    }
    profile = {
        "schema": "kindlemaster.fen_confidence_profile.v1",
        "auto_accept": 0.97,
        "rule_review": 0.90,
        "manual_review": 0.0,
        "false_accepted_target": 0,
        "reliability": reliability,
        "policy": "Thresholds are conservative until enough verified holdout evidence exists.",
    }
    _write_json(out / "models" / "fen_confidence_profile.json", profile)
    _write_json(reports_dir / "fen_confidence_calibration.json", profile)
    return profile


def export_fen_corpus_manifest(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    data_dir = out / "data"
    reports_dir = out / "reports"
    data_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        out / "review" / "fen_verified_labels.jsonl",
        data_dir / "fen_square_dataset.jsonl",
        reports_dir / "fen_square_dataset_summary.json",
        reports_dir / "fen_square_model_eval.json",
        reports_dir / "fen_ensemble_eval.json",
    ]
    payload = {
        "schema": "kindlemaster.fen_corpus_manifest.v1",
        "status": "ok",
        "artifacts": [
            {
                "path": str(path),
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size if path.is_file() else 0,
                "sha256": _file_sha256(path) if path.is_file() else "",
            }
            for path in paths
        ],
        "policy": "Manifest provides reproducibility without requiring generated artifacts to be committed.",
    }
    _write_json(data_dir / "fen_corpus_manifest.json", payload)
    return payload


def build_chess_pgn_review(
    out_dir: str | Path,
    *,
    glyph_mapping_file: str | Path | None = None,
) -> dict[str, Any]:
    """Build a chess-aware PGN review/lattice report from source HTML records."""
    out = Path(out_dir)
    if not (out / "data" / "book.json").is_file():
        return {
            "status": "failed",
            "error": "data/book.json missing; run chess-study run-all/rebuild first or provide --html to fen-review.",
            "out_dir": str(out),
        }
    book = _load_source_book(out)
    records = list(book.get("pgn_records") or [])
    accepted_fen_by_source = _accepted_source_fen_index(_source_book_diagrams(book))
    glyph_mapping = _load_ocr_glyph_mapping(glyph_mapping_file)
    lattice_rows = [
        _pgn_lattice_row_from_record(record, glyph_mapping, accepted_fen_by_source=accepted_fen_by_source)
        for record in records
    ]
    accepted = [row for row in lattice_rows if row.get("status") == "accepted"]
    review_rows = [row for row in lattice_rows if row.get("status") != "accepted"]
    diagnostics = _pgn_lattice_diagnostics(lattice_rows)
    candidates = _build_glyph_mapping_candidates(diagnostics)

    review_dir = out / "review"
    reports_dir = out / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_json(review_dir / "glyph_mapping_candidates.json", candidates)
    _write_json(review_dir / "glyph_mapping_manual.json", _glyph_mapping_manual_seed(candidates, glyph_mapping))
    _write_json(review_dir / "unmapped_token_blockers.json", _pgn_unmapped_token_blockers(lattice_rows, glyph_mapping))
    (review_dir / "glyph_mapping_review.html").write_text(_glyph_mapping_review_html(candidates), encoding="utf-8")
    _write_jsonl(review_dir / "pgn_lattice_review.jsonl", lattice_rows)
    _write_csv(review_dir / "pgn_lattice_review.csv", lattice_rows)
    _write_pgn_replay_blockers_top10(out, lattice_rows)

    games_pgn = "\n\n".join(str(row.get("pgn") or "").strip() for row in accepted if row.get("pgn")).strip()
    (out / "data" / "games.pgn").write_text(games_pgn + ("\n" if games_pgn else ""), encoding="utf-8")
    payload = {
        "status": "ok",
        "schema": "kindlemaster.pgn_lattice_eval.v1",
        "pgn_total": len(lattice_rows),
        "accepted_pgn": len(accepted),
        "pgn_needs_review": len(review_rows),
        "ocr_token_mappings_loaded": int(glyph_mapping.get("accepted_count") or 0),
        "ocr_token_mappings_applied": sum(int(row.get("ocr_token_mappings_applied") or 0) for row in lattice_rows),
        "fragments_blocked_by_unmapped_tokens": len([row for row in lattice_rows if row.get("unmapped_token_blockers")]),
        "top_blockers": _top_counts(warning for row in lattice_rows for warning in row.get("warnings") or []),
        "policy": "PGN accepted only when manual mappings remove token blockers and python-chess parser/replay passes.",
    }
    _write_json(reports_dir / "pgn_lattice_eval.json", payload)
    build_chess_quality_dashboard(out)
    return payload


def build_ai_fen_candidates(
    out_dir: str | Path,
    *,
    limit: int = 0,
    dry_run: bool = False,
    provider: Any | None = None,
) -> dict[str, Any]:
    """Generate review-only AI FEN candidates from extracted diagram crops."""
    out = Path(out_dir)
    review_dir = out / "review"
    reports_dir = out / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    book = _load_source_book(out)
    diagrams = _source_book_diagrams(book)
    if limit and limit > 0:
        diagrams = diagrams[:limit]

    if provider is None and not dry_run:
        try:
            from openai_chess_fen_reviewer import build_openai_chess_fen_reviewer_from_env

            provider = build_openai_chess_fen_reviewer_from_env(cwd=Path.cwd())
        except Exception:
            provider = None

    rows: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    verified_queue: list[dict[str, Any]] = []
    verified_fen_by_diagram = _load_verified_fen_labels_by_diagram(review_dir / "fen_verified_labels.jsonl")
    for diagram in diagrams:
        row = _ai_fen_candidate_row(
            out,
            diagram,
            provider=provider,
            dry_run=dry_run,
            verified_fen_by_diagram=verified_fen_by_diagram,
        )
        rows.append(row)
        if row.get("status") == "ai_cv_conflict":
            disagreements.append(row)
        if row.get("status") in {"ai_cv_agree", "verified_label_agree"}:
            verified_queue.append(_ai_verified_candidate_queue_row(row))

    _write_jsonl(review_dir / "ai_fen_candidates.jsonl", rows)
    _write_jsonl(review_dir / "ai_fen_disagreements.jsonl", disagreements)
    _write_jsonl(review_dir / "ai_verified_candidate_queue.jsonl", verified_queue)
    payload = {
        "status": "ok",
        "schema": "kindlemaster.ai_fen_candidates.v1",
        "mode": "review_only",
        "dry_run": bool(dry_run),
        "diagram_count": len(rows),
        "ai_suggested": len([row for row in rows if row.get("ai_fen_candidate")]),
        "ai_cv_agree": len([row for row in rows if row.get("status") == "ai_cv_agree"]),
        "ai_cv_conflict": len(disagreements),
        "ai_validated_candidate_count": len([row for row in rows if row.get("deterministic_validation", {}).get("valid")]),
        "deterministic_valid": len([row for row in rows if row.get("status") == "deterministic_valid"]),
        "verified_candidate_queue_count": len(verified_queue),
        "accepted_fen_changed": 0,
        "policy": "AI FEN candidates are review evidence only; accepted FEN still requires deterministic template/validation gates.",
    }
    _write_json(reports_dir / "ai_fen_candidates_eval.json", payload)
    _write_ai_cost_report(out)
    build_chess_quality_dashboard(out)
    return payload


def build_ai_pgn_candidates(
    out_dir: str | Path,
    *,
    glyph_mapping_file: str | Path | None = None,
    limit: int = 30,
    dry_run: bool = False,
    deepseek_provider: Any | None = None,
    pgn_provider: Any | None = None,
) -> dict[str, Any]:
    """Generate review-only DeepSeek/GPT PGN repair candidates."""
    out = Path(out_dir)
    review_dir = out / "review"
    reports_dir = out / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    if not (review_dir / "pgn_lattice_review.jsonl").is_file():
        build_chess_pgn_review(out, glyph_mapping_file=glyph_mapping_file)
    lattice_rows = _read_jsonl_rows(review_dir / "pgn_lattice_review.jsonl")
    candidates_payload = _read_optional_json(review_dir / "glyph_mapping_candidates.json")
    cluster_payload = _deepseek_pgn_cluster_payload(
        out,
        lattice_rows,
        candidates_payload,
        provider=deepseek_provider,
        dry_run=dry_run,
    )
    _write_json(review_dir / "deepseek_glyph_clusters.json", cluster_payload)

    if pgn_provider is None and not dry_run:
        try:
            from openai_chess_pgn_reviewer import build_openai_chess_pgn_reviewer_from_env

            pgn_provider = build_openai_chess_pgn_reviewer_from_env(cwd=Path.cwd())
        except Exception:
            pgn_provider = None

    selected = _select_ai_pgn_rows(lattice_rows, limit=max(0, int(limit or 0)))
    pgn_rows = [
        _ai_pgn_candidate_row(row, provider=pgn_provider, dry_run=dry_run)
        for row in selected
    ]
    _write_jsonl(review_dir / "ai_pgn_candidates.jsonl", pgn_rows)
    _write_jsonl(review_dir / "gpt_pgn_repair_candidates.jsonl", pgn_rows)
    payload = {
        "status": "ok",
        "schema": "kindlemaster.ai_pgn_candidates.v1",
        "mode": "review_only",
        "dry_run": bool(dry_run),
        "records_considered": len(lattice_rows),
        "records_sent_for_gpt_repair": len(pgn_rows),
        "deepseek_cluster_status": cluster_payload.get("status"),
        "ai_suggested_pgn": len([row for row in pgn_rows if row.get("candidate_pgn")]),
        "deterministic_replay_clean": len([row for row in pgn_rows if row.get("deterministic_replay_clean")]),
        "accepted_pgn_changed": 0,
        "policy": "AI PGN candidates are review evidence only; strict PGN export still requires local parser/replay gates.",
    }
    _write_json(reports_dir / "ai_pgn_candidates_eval.json", payload)
    _write_ai_cost_report(out)
    build_chess_quality_dashboard(out)
    return payload


def build_ai_assisted_quality_eval(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    reports_dir = out / "reports"
    review_dir = out / "review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    dashboard = build_chess_quality_dashboard(out)
    ai_fen = _read_optional_json(reports_dir / "ai_fen_candidates_eval.json")
    ai_pgn = _read_optional_json(reports_dir / "ai_pgn_candidates_eval.json")
    deepseek_clusters = _read_optional_json(review_dir / "deepseek_glyph_clusters.json")
    payload = {
        "status": "ok",
        "schema": "kindlemaster.ai_assisted_quality_eval.v1",
        "mode": "evidence_only",
        "dashboard": {
            "diagrams_total": dashboard.get("diagrams_total", 0),
            "fen_accepted": dashboard.get("fen_accepted", 0),
            "pgn_total": dashboard.get("pgn_total", 0),
            "accepted_pgn": dashboard.get("accepted_pgn", 0),
        },
        "ai_fen": {
            "status": ai_fen.get("status") or "not_run",
            "diagram_count": ai_fen.get("diagram_count", 0),
            "ai_suggested": ai_fen.get("ai_suggested", 0),
            "ai_cv_agree": ai_fen.get("ai_cv_agree", 0),
            "ai_cv_conflict": ai_fen.get("ai_cv_conflict", 0),
            "verified_candidate_queue_count": ai_fen.get("verified_candidate_queue_count", 0),
        },
        "ai_pgn": {
            "status": ai_pgn.get("status") or "not_run",
            "records_sent_for_gpt_repair": ai_pgn.get("records_sent_for_gpt_repair", 0),
            "ai_suggested_pgn": ai_pgn.get("ai_suggested_pgn", 0),
            "deterministic_replay_clean": ai_pgn.get("deterministic_replay_clean", 0),
        },
        "deepseek": {
            "status": deepseek_clusters.get("status") or "not_run",
            "cluster_count": deepseek_clusters.get("cluster_count", 0),
            "candidate_mapping_count": deepseek_clusters.get("candidate_mapping_count", 0),
        },
        "accepted_changed_by_ai": False,
        "policy": "AI may shorten review, but accepted FEN/PGN remains controlled by deterministic validators.",
    }
    _write_json(reports_dir / "ai_assisted_quality_eval.json", payload)
    _write_ai_cost_report(out)
    build_chess_quality_dashboard(out)
    return payload


def render_semantic_source_reader(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    book = _load_source_book(out)
    if not book:
        return {
            "status": "failed",
            "error": "data/book.json missing; run chess-study run-all/rebuild from source HTML first.",
            "out_dir": str(out),
        }
    book = _attach_engine_analysis_to_book(book, out)
    semantic_book = book.get("semantic_book") if isinstance(book.get("semantic_book"), Mapping) else {}
    if semantic_book.get("schema") != SEMANTIC_BOOK_SCHEMA:
        semantic_book = build_chess_reader_semantic_book(book)
        book["semantic_book"] = semantic_book
        _write_json(out / "data" / "book.json", book)
    _write_chess_reader_semantic_book_reports(out, semantic_book)
    (out / "styles.css").write_text(_semantic_source_styles_css(), encoding="utf-8")
    (out / "app.js").write_text(_semantic_source_app_js(), encoding="utf-8")
    (out / "index.html").write_text(_semantic_source_index_html(book), encoding="utf-8")
    page_count = len([page for page in book.get("pages") or [] if _semantic_source_page_elements(page)])
    return {
        "status": "ok",
        "schema": "kindlemaster.semantic_source_reader.v1",
        "index_html": str(out / "index.html"),
        "rendered_pages_with_content": page_count,
        "layout": "logical_study_blocks",
        "policy": "index.html is the default semantic reader; PDF preview is only an optional details/link.",
    }


def build_chess_quality_dashboard(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    book = _load_source_book(out) if (out / "data" / "book.json").is_file() else {}
    diagrams = _source_book_diagrams(book)
    pgn_records = list(book.get("pgn_records") or [])
    fen_eval = _read_optional_json(reports_dir / "fen_profile_eval.json")
    pgn_eval = _read_optional_json(reports_dir / "pgn_lattice_eval.json")
    ai_eval = _read_optional_json(reports_dir / "ai_assisted_quality_eval.json")
    baseline = _read_optional_json(reports_dir / "chess_quality_baseline.json")
    preprocess_eval = _read_optional_json(reports_dir / "board_preprocess_eval.json")
    dataset_eval = _read_optional_json(reports_dir / "fen_square_dataset_summary.json")
    model_eval = _read_optional_json(reports_dir / "fen_square_model_eval.json")
    runtime_eval = _read_optional_json(reports_dir / "fen_model_runtime_eval.json")
    ensemble_eval = _read_optional_json(reports_dir / "fen_ensemble_eval.json")
    confidence_eval = _read_optional_json(reports_dir / "fen_confidence_calibration.json")
    false_positive_audit = _read_optional_json(reports_dir / "fen_false_positive_audit.json")
    pages = _quality_dashboard_pages(book, diagrams, pgn_records)
    summary = {
        "schema": "kindlemaster.chess_quality_dashboard.v1",
        "status": "ok",
        "pages": len(list(book.get("pages") or [])),
        "diagrams_total": len(diagrams),
        "fen_accepted": len([item for item in diagrams if item.get("validation_status") == "accepted"]),
        "fen_needs_review": len([item for item in diagrams if item.get("validation_status") != "accepted"]),
        "pgn_total": len(pgn_records),
        "accepted_pgn": int(pgn_eval.get("accepted_pgn") or len([item for item in pgn_records if item.get("status") == "accepted"])),
        "pgn_needs_review": int(pgn_eval.get("pgn_needs_review") or len([item for item in pgn_records if item.get("status") != "accepted"])),
        "fen_profile_status": fen_eval.get("status") or "not_run",
        "pgn_lattice_status": pgn_eval.get("status") or "not_run",
        "ai_assisted_status": ai_eval.get("status") or "not_run",
        "baseline_status": baseline.get("status") or "not_run",
        "board_preprocess_status": preprocess_eval.get("status") or "not_run",
        "normalized_boards": int(preprocess_eval.get("normalized_count") or 0),
        "fen_square_dataset_status": dataset_eval.get("status") or "not_run",
        "fen_square_samples": int(dataset_eval.get("sample_count") or 0),
        "fen_square_boards": int(dataset_eval.get("board_count") or 0),
        "fen_model_status": model_eval.get("status") or "not_run",
        "fen_model_square_accuracy": model_eval.get("square_accuracy", 0),
        "fen_local_prediction_status": runtime_eval.get("status") or "not_run",
        "fen_local_predictions": int(runtime_eval.get("prediction_count") or 0),
        "fen_ensemble_status": ensemble_eval.get("status") or "not_run",
        "fen_ensemble_accepted_candidates": int(ensemble_eval.get("accepted_candidate_count") or 0),
        "fen_confidence_profile_status": confidence_eval.get("schema") and "ok" or "not_run",
        "fen_false_positive_audit_status": false_positive_audit.get("status") or "not_run",
        "fen_false_positive_findings": int(false_positive_audit.get("finding_count") or 0),
        "ai_fen_candidates": (ai_eval.get("ai_fen") or {}).get("ai_suggested", 0),
        "ai_pgn_candidates": (ai_eval.get("ai_pgn") or {}).get("ai_suggested_pgn", 0),
        "targets": {
            "7/10": "50-100 accepted FEN and several accepted PGN",
            "8/10": "majority of diagrams accepted",
            "9/10": ">90% FEN accepted and >70% PGN accepted without false accepted records",
        },
        "pages_detail": pages,
    }
    _write_json(reports_dir / "chess_quality_dashboard.json", summary)
    (reports_dir / "chess_quality_dashboard.html").write_text(_quality_dashboard_html(summary), encoding="utf-8")
    _write_iteration_status(out, summary)
    return summary


def _load_source_book(out_dir: Path) -> dict[str, Any]:
    book_path = out_dir / "data" / "book.json"
    if not book_path.is_file():
        return {}
    try:
        return json.loads(book_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _source_book_diagrams(book: dict[str, Any]) -> list[dict[str, Any]]:
    diagrams: list[dict[str, Any]] = []
    for page in book.get("pages") or []:
        for diagram in page.get("diagrams") or []:
            if not isinstance(diagram, dict):
                continue
            diagrams.append({**diagram, "page": int(diagram.get("page") or page.get("page") or 0)})
    diagrams.sort(key=_source_order_key)
    return diagrams


def _write_iteration_status(out_dir: Path, dashboard: dict[str, Any]) -> None:
    review_dir = out_dir / "review"
    reports_dir = out_dir / "reports"
    review_dir.mkdir(parents=True, exist_ok=True)
    fen_review = _read_optional_json(reports_dir / "fen_review_queue.json")
    fen_build = _read_optional_json(reports_dir / "fen_template_build.json")
    fen_eval = _read_optional_json(reports_dir / "fen_profile_eval.json")
    pgn_eval = _read_optional_json(reports_dir / "pgn_lattice_eval.json")
    dataset_eval = _read_optional_json(reports_dir / "fen_square_dataset_summary.json")
    model_eval = _read_optional_json(reports_dir / "fen_square_model_eval.json")
    ensemble_eval = _read_optional_json(reports_dir / "fen_ensemble_eval.json")
    verified = int(fen_eval.get("promoted_label_count") or fen_build.get("promoted_label_count") or 0)
    blocked_tokens = int(pgn_eval.get("fragments_blocked_by_unmapped_tokens") or 0)
    lines = [
        "# Chess Study Iteration Status",
        "",
        "## Current Metrics",
        "",
        f"- FEN review batch: `{fen_review.get('diagram_count', 0)}` diagram(s), ranges `{fen_review.get('page_ranges') or 'all'}`.",
        f"- Verified FEN labels promoted: `{verified}`.",
        f"- FEN accepted: `{dashboard.get('fen_accepted', 0)}` / `{dashboard.get('diagrams_total', 0)}`.",
        f"- PGN accepted: `{dashboard.get('accepted_pgn', 0)}` / `{dashboard.get('pgn_total', 0)}`.",
        f"- PGN unmapped token blockers: `{blocked_tokens}`.",
        f"- FEN profile status: `{dashboard.get('fen_profile_status')}`.",
        f"- PGN lattice status: `{dashboard.get('pgn_lattice_status')}`.",
        f"- Square dataset: `{dataset_eval.get('status') or 'not_run'}` with `{dataset_eval.get('sample_count', 0)}` samples.",
        f"- Local FEN model: `{model_eval.get('status') or 'not_run'}` square accuracy `{model_eval.get('square_accuracy', 0)}`.",
        f"- FEN ensemble: `{ensemble_eval.get('status') or 'not_run'}` accepted candidates `{ensemble_eval.get('accepted_candidate_count', 0)}`.",
        "",
        "## Gates",
        "",
        f"- Gate 1 Dataset: `{'PASS' if verified >= 30 else 'BLOCKED'}` - requires >=30 verified FEN labels.",
        f"- Gate 2 Template: `{'PASS' if fen_eval.get('status') == 'passed' else 'BLOCKED'}` - requires holdout pass.",
        f"- Gate 3 PGN: `{'PASS' if int(dashboard.get('accepted_pgn') or 0) > 0 else 'BLOCKED'}` - requires parser/replay accepted PGN.",
        "",
        "## Agent Next Actions",
        "",
        "- Agent A: fill verified FEN labels in `review/fen_manual_review.html` and export JSONL.",
        "- Agent B: run template build and holdout once verified labels exist.",
        "- Agent C: review `review/diagram_alignment_notes.jsonl` after holdout.",
        "- Agent D: confirm OCR token mappings in `review/glyph_mapping_manual.json`.",
        "- Agent E: inspect `review/pgn_replay_blockers_top10.md` after PGN review.",
        "- Agent F: inspect `reports/chess_quality_dashboard.html` after each iteration.",
        "",
        "Policy: no agent may increase accepted counts by directly editing JSON statuses.",
    ]
    (review_dir / "iteration_status.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_diagram_alignment_notes(out_dir: Path, fen_eval: dict[str, Any]) -> None:
    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    cases = list(fen_eval.get("holdout_cases") or [])
    rows: list[dict[str, Any]] = []
    if not cases:
        rows.append(
            {
                "status": "pending",
                "reason": "holdout_not_run_or_no_failure_cases",
                "recommended_action": "collect_verified_fen_labels_then_run_evaluate_fen_profile",
            }
        )
    for case in cases:
        warnings = [str(item) for item in case.get("warnings") or []]
        reason = _diagram_alignment_reason(case, warnings)
        rows.append(
            {
                "diagram_id": case.get("id") or "",
                "crop_path": case.get("crop_path") or "",
                "expected_fen": case.get("expected_fen") or "",
                "actual_fen": case.get("actual_fen") or "",
                "matched": bool(case.get("matched")),
                "false_positive": bool(case.get("false_positive")),
                "confidence": case.get("confidence", 0.0),
                "warnings": warnings,
                "failure_reason": reason,
                "recommended_alignment": _recommended_alignment_for_reason(reason),
                "manual_note": "",
            }
        )
    _write_jsonl(review_dir / "diagram_alignment_notes.jsonl", rows)


def _diagram_alignment_reason(case: dict[str, Any], warnings: list[str]) -> str:
    joined = " ".join(warnings).lower()
    if case.get("false_positive"):
        return "false_positive"
    if "coordinate" in joined or "border" in joined:
        return "coordinates_included"
    if "tight" in joined or "cropped" in joined or "missing_edge" in joined:
        return "crop_too_tight"
    if "wide" in joined or "margin" in joined:
        return "crop_too_wide"
    if "center" in joined or "off_center" in joined:
        return "board_not_centered"
    if "template" in joined or "conflict" in joined:
        return "template_conflict"
    if "low_confidence" in warnings or float(case.get("confidence") or 0.0) < 0.5:
        return "piece_glyph_low_quality"
    if not case.get("actual_fen"):
        return "board_not_detected"
    if case.get("expected_fen") and case.get("actual_fen") and case.get("expected_fen") != case.get("actual_fen"):
        return "template_conflict"
    return "uncertain_alignment_or_template_issue"


def _recommended_alignment_for_reason(reason: str) -> str:
    return {
        "false_positive": "exclude_from_strict_dataset",
        "crop_too_wide": "try_tight_or_inner_grid_crop",
        "crop_too_tight": "try_expand_or_center_square_crop",
        "board_not_centered": "try_center_square_crop",
        "coordinates_included": "try_remove_coordinates_or_inner_grid_crop",
        "template_conflict": "compare_crop_vs_render_and_add_piece_templates",
        "piece_glyph_low_quality": "review_template_coverage_or_ml_square_classifier",
        "board_not_detected": "try_expanded_crop_or_center_square",
        "uncertain_alignment_or_template_issue": "manual_crop_review",
    }.get(reason, "manual_crop_review")


def _write_pgn_replay_blockers_top10(out_dir: Path, rows: list[dict[str, Any]]) -> None:
    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    candidates = [row for row in rows if row.get("status") != "accepted"]
    candidates.sort(key=_pgn_blocker_rank)
    lines = [
        "# PGN Replay Blockers Top 10",
        "",
        "This report is evidence-only. It does not mark PGN accepted.",
        "",
    ]
    if not candidates:
        lines.append("No PGN replay blockers found.")
    for index, row in enumerate(candidates[:10], start=1):
        replay = _pgn_replay_failure_summary(str(row.get("pgn_candidate") or ""))
        warnings = ", ".join(str(item) for item in row.get("warnings") or [])
        tokens = ", ".join(str(item) for item in row.get("unmapped_token_blockers") or [])
        lines.extend(
            [
                f"## {index}. {row.get('record_id') or 'unknown'} - page {row.get('page')}",
                "",
                f"- Label: `{row.get('label') or ''}`",
                f"- Status: `{row.get('status')}`",
                f"- Warnings: `{warnings}`",
                f"- Unmapped tokens: `{tokens}`",
                f"- Replay failure: `{replay}`",
                "",
                "Raw text:",
                "",
                f"```text\n{row.get('raw_text') or ''}\n```",
                "",
                "Normalized text:",
                "",
                f"```text\n{row.get('normalized_text') or ''}\n```",
                "",
                "PGN candidate:",
                "",
                f"```pgn\n{_bounded_text(str(row.get('pgn_candidate') or ''), limit=1200)}\n```",
                "",
            ]
        )
    (review_dir / "pgn_replay_blockers_top10.md").write_text("\n".join(lines), encoding="utf-8")


def _pgn_blocker_rank(row: dict[str, Any]) -> tuple[int, int, int, int, str]:
    warnings = set(str(item) for item in row.get("warnings") or [])
    blockers = list(row.get("unmapped_token_blockers") or [])
    text = f"{row.get('normalized_text') or ''} {row.get('raw_text') or ''}"
    move_like = re.search(
        r"\b\d{1,3}\.(?:\.\.)?\s*(?:O-O(?:-O)?|0-0(?:-0)?|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8])",
        text,
    )
    no_move_penalty = 0 if move_like else 1
    hard_score = sum(
        1
        for key in [
            "unmapped_ocr_tokens",
            UNMAPPED_CHESS_GLYPH_WARNING,
            "pgn_replay_errors",
            "side_to_move_mismatch",
            "move_number_jump",
            "move_number_regression",
        ]
        if key in warnings
    )
    return (no_move_penalty, hard_score, len(blockers), len(warnings), str(row.get("record_id") or ""))


def _pgn_replay_failure_summary(pgn_text: str) -> str:
    value = str(pgn_text or "").strip()
    if not value:
        return "empty_pgn_candidate"
    try:
        import logging
        import chess.pgn  # type: ignore[import-not-found]

        logger = logging.getLogger("chess.pgn")
        was_disabled = logger.disabled
        logger.disabled = True
        try:
            game = chess.pgn.read_game(io.StringIO(value))
        finally:
            logger.disabled = was_disabled
        if game is None:
            return "parser_returned_no_game"
        errors = getattr(game, "errors", None)
        if errors:
            return _bounded_text(str(errors[0]), limit=160)
        board = game.board()
        move_count = 0
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                return f"illegal_move_at_ply_{move_count + 1}:{move}"
            board.push(move)
            move_count += 1
        if move_count <= 0:
            return "no_mainline_moves"
        return "replay_clean"
    except Exception as exc:
        return _bounded_text(type(exc).__name__ + ": " + str(exc), limit=160)


def _fen_manual_review_row(diagram: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    image_rel = str(diagram.get("image_path") or "")
    crop_path = (out_dir / image_rel).resolve() if image_rel else Path("")
    fen = str(diagram.get("fen") or "").strip()
    candidate = str(diagram.get("fen_candidate") or "").strip()
    return {
        "diagram_id": str(diagram.get("id") or diagram.get("diagram_id") or ""),
        "page": int(diagram.get("page") or 0),
        "reading_order": int(diagram.get("reading_order") or 0),
        "bbox": diagram.get("bbox") or [0, 0, 0, 0],
        "caption": str(diagram.get("caption") or ""),
        "crop_path": str(crop_path) if image_rel else "",
        "crop_rel_path": image_rel,
        "current_fen": fen,
        "fen_candidate": candidate,
        "manual_fen": "",
        "side_to_move": str(diagram.get("side_to_move") or "unknown"),
        "manual_side_to_move": "",
        "confidence": float(diagram.get("confidence") or 0.0),
        "validation_status": str(diagram.get("validation_status") or "needs-human-review"),
        "review_reason": str(diagram.get("review_reason") or "manual_fen_required"),
        "manual_label": "needs_manual_fen",
        "label_status": "draft",
        "verified_by": "",
        "verified_at": "",
        "notes": "",
    }


def _fen_manual_review_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_fen_manual_review_card(row) for row in rows) or "<p>No diagram crops found.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FEN Manual Review</title>
  <style>
    :root {{ --ink:#21170f; --paper:#fff8ec; --line:#d8c4a8; --accent:#8a4516; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Georgia,'Times New Roman',serif; color:var(--ink); background:#f0e3cf; }}
    header {{ position:sticky; top:0; z-index:2; padding:1rem 1.25rem; color:#fff8ec; background:#24170f; box-shadow:0 12px 30px rgba(0,0,0,.18); }}
    main {{ max-width:1180px; margin:0 auto; padding:1rem; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:1rem; }}
    article {{ background:var(--paper); border:1px solid var(--line); border-radius:18px; padding:1rem; box-shadow:0 12px 32px rgba(64,39,12,.08); }}
    img {{ width:100%; aspect-ratio:1; object-fit:contain; background:#fff; border:1px solid var(--line); border-radius:12px; }}
    label {{ display:block; margin:.55rem 0 .2rem; font-weight:800; }}
    input, select, textarea {{ width:100%; border:1px solid var(--line); border-radius:10px; padding:.45rem .55rem; font:inherit; background:#fffdf8; }}
    code {{ overflow-wrap:anywhere; }}
    .meta {{ color:#735f49; font-size:.9rem; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:.5rem; margin:.85rem 0; }}
    button {{ border:0; border-radius:999px; padding:.55rem .8rem; background:var(--accent); color:#fff8ec; font-weight:900; cursor:pointer; }}
    pre {{ white-space:pre-wrap; background:#fffdf8; border:1px solid var(--line); border-radius:12px; padding:.75rem; max-height:14rem; overflow:auto; }}
  </style>
</head>
<body>
  <header>
    <h1>FEN Manual Review</h1>
    <p>{len(rows)} diagram crop(s). Fill manual FEN only after human verification; export JSONL and use build-fen-templates.</p>
    <div class="actions"><button type="button" id="export-jsonl">Export filled JSONL</button></div>
  </header>
  <main><section class="grid">{cards}</section><pre id="export-preview" aria-live="polite"></pre></main>
  <script>
  const cards = [...document.querySelectorAll('[data-row]')];
  function rowFromCard(card) {{
    const seed = JSON.parse(card.dataset.row);
    seed.manual_fen = card.querySelector('[name="manual_fen"]').value.trim();
    seed.manual_side_to_move = card.querySelector('[name="manual_side_to_move"]').value.trim();
    seed.manual_label = card.querySelector('[name="manual_label"]').value;
    seed.label_status = card.querySelector('[name="label_status"]').value;
    seed.notes = card.querySelector('[name="notes"]').value.trim();
    return seed;
  }}
  document.getElementById('export-jsonl').addEventListener('click', () => {{
    const jsonl = cards.map(rowFromCard).map(row => JSON.stringify(row)).join('\\n') + '\\n';
    document.getElementById('export-preview').textContent = jsonl;
    const blob = new Blob([jsonl], {{type:'application/x-ndjson'}});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'fen_manual_draft.filled.jsonl'; a.click();
    URL.revokeObjectURL(url);
  }});
  </script>
</body>
</html>"""


def _fen_manual_review_card(row: dict[str, Any]) -> str:
    data = html.escape(json.dumps(row, ensure_ascii=False), quote=True)
    image = html.escape(str(row.get("crop_rel_path") or ""), quote=True)
    return f"""<article data-row="{data}">
  <h2>{html.escape(str(row.get('caption') or row.get('diagram_id') or 'Diagram'))}</h2>
  <p class="meta">Page {int(row.get('page') or 0)} · confidence {float(row.get('confidence') or 0.0):.3f}</p>
  <img src="../{image}" alt="{html.escape(str(row.get('caption') or row.get('diagram_id') or 'Diagram'), quote=True)}">
  <p class="meta">Current candidate: <code>{html.escape(str(row.get('fen_candidate') or ''))}</code></p>
  <label>Manual FEN</label><input name="manual_fen" placeholder="8/8/8/8/8/8/8/4K2k w - - 0 1">
  <label>Side to move</label><select name="manual_side_to_move"><option value="">unknown</option><option value="w">white</option><option value="b">black</option></select>
  <label>Label</label><select name="manual_label"><option>needs_manual_fen</option><option>correct_diagram</option><option>cropped_diagram</option><option>false_positive</option><option>uncertain</option></select>
  <label>Status</label><select name="label_status"><option>draft</option><option>verified</option><option>rejected</option></select>
  <label>Notes</label><textarea name="notes" rows="3"></textarea>
</article>"""


def _promote_verified_fen_labels(labels_path: Path, out_dir: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl_rows(labels_path)
    promoted: list[dict[str, Any]] = []
    for row in rows:
        status = str(row.get("label_status") or row.get("status") or "").strip().lower()
        manual_label = str(row.get("manual_label") or row.get("label") or "").strip().lower()
        if status not in {"verified", "accepted", "promoted"}:
            continue
        if manual_label not in {"correct_diagram", "cropped_diagram"}:
            continue
        fen = str(row.get("manual_fen") or row.get("fen") or row.get("current_fen") or "").strip()
        if not fen:
            continue
        valid, warnings = validate_fen(fen)
        if not valid or warnings:
            continue
        manual_side = str(row.get("manual_side_to_move") or row.get("side_to_move") or "").strip().lower()
        if manual_side in {"white", "w"}:
            manual_side = "w"
        elif manual_side in {"black", "b"}:
            manual_side = "b"
        else:
            manual_side = ""
        fen_side = fen.split()[1] if len(fen.split()) >= 2 else ""
        if manual_side and fen_side and manual_side != fen_side:
            continue
        crop_path = _resolve_review_crop_path(row, out_dir)
        if not crop_path.is_file():
            continue
        promoted.append(
            {
                "diagram_id": row.get("diagram_id") or row.get("id") or crop_path.stem,
                "fen": fen,
                "crop_path": str(crop_path),
                "page": _safe_int(row.get("page")),
                "source": str(labels_path),
                "label_status": "verified",
                "manual_label": manual_label or "correct_diagram",
                "manual_side_to_move": manual_side or fen_side,
                "verified_by": str(
                    row.get("verified_by")
                    or row.get("reviewer")
                    or row.get("verification_source")
                    or "verified_label_import"
                ),
                "verified_at": str(row.get("verified_at") or row.get("reviewed_at") or date.today().isoformat()),
                "notes": row.get("notes") or row.get("reviewer_note") or "",
            }
        )
    return promoted


def _resolve_review_crop_path(row: dict[str, Any], out_dir: Path) -> Path:
    crop_value = str(row.get("crop_path") or "").strip()
    if crop_value:
        crop_path = Path(crop_value)
        if crop_path.is_file():
            return crop_path
    rel_value = str(row.get("crop_rel_path") or row.get("image_path") or "").strip()
    return (out_dir / rel_value) if rel_value else Path("")


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _load_verified_fen_labels_by_diagram(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in _read_jsonl_rows(path):
        if str(row.get("label_status") or "").strip().lower() != "verified":
            continue
        diagram_id = str(row.get("diagram_id") or row.get("id") or "").strip()
        fen = str(row.get("fen") or row.get("manual_fen") or "").strip()
        if diagram_id and fen:
            labels[diagram_id] = fen
    return labels


def _accepted_source_fen_index(diagrams: list[dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    for diagram in diagrams:
        if str(diagram.get("validation_status") or diagram.get("status") or "") != "accepted":
            continue
        fen = str(diagram.get("fen") or "").strip()
        if not fen:
            continue
        valid, warnings = validate_fen(fen)
        if not valid or warnings:
            continue
        for key in [
            diagram.get("id"),
            diagram.get("diagram_id"),
            diagram.get("caption"),
            diagram.get("label"),
        ]:
            normalized = _normalize_source_label(str(key or ""))
            if normalized:
                index[normalized] = fen
    return index


def _record_source_fen(record: dict[str, Any], accepted_fen_by_source: dict[str, str]) -> str:
    for key in [
        record.get("diagram_id"),
        record.get("source_diagram"),
        record.get("label"),
        record.get("id"),
    ]:
        normalized = _normalize_source_label(str(key or ""))
        if normalized and normalized in accepted_fen_by_source:
            return accepted_fen_by_source[normalized]
    return ""


def _record_requires_source_fen(record: dict[str, Any]) -> bool:
    value = " ".join(
        str(record.get(key) or "")
        for key in ["diagram_id", "source_diagram", "label", "raw_text", "visible_review_text"]
    )
    return bool(re.search(r"\b(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\b", value, flags=re.IGNORECASE))


def _pgn_lattice_row_from_record(
    record: dict[str, Any],
    glyph_mapping: dict[str, Any],
    *,
    accepted_fen_by_source: dict[str, str] | None = None,
) -> dict[str, Any]:
    raw_text = str(record.get("visible_review_text") or record.get("raw_text") or "")
    normalized = _normalize_notation_text(raw_text)
    mapped, mapping_result = _apply_ocr_glyph_mapping(normalized, glyph_mapping)
    source_fen = _record_source_fen(record, accepted_fen_by_source or {})
    requires_source_fen = _record_requires_source_fen(record)
    candidate = str(record.get("pgn") or "").strip()
    if not candidate:
        candidate = _build_notation_pgn_candidate(
            mapped,
            page=int(record.get("logical_page") or record.get("source_page") or 0),
            source_diagram=str(record.get("label") or record.get("id") or ""),
            fen=source_fen,
            comments=[],
        )
    inherited_warnings = [str(item) for item in record.get("warnings") or []]
    blockers = list(mapping_result.get("unmapped_tokens") or [])
    warnings = [warning for warning in inherited_warnings if warning != UNMAPPED_CHESS_GLYPH_WARNING or blockers]
    if blockers:
        warnings.append("unmapped_ocr_tokens")
    if not _pgn_has_required_headers(candidate):
        warnings.append("pgn_missing_required_headers")
    if not _pgn_has_source_page(candidate):
        warnings.append("pgn_missing_source_page")
    if requires_source_fen and not source_fen:
        warnings.append("source_fen_not_accepted")
    if source_fen and not _pgn_has_setup_fen(candidate):
        warnings.append("pgn_missing_setup_fen")
    if not _pgn_replay_clean(candidate):
        warnings.append("pgn_replay_errors")
    blocking = {
        "move_number_jump",
        "move_number_regression",
        "side_to_move_mismatch",
        "pgn_missing_or_not_embedded",
        "pgn_missing_required_headers",
        "pgn_missing_source_page",
        "pgn_missing_setup_fen",
        "pgn_replay_errors",
        "source_fen_not_accepted",
        "unmapped_ocr_tokens",
        UNMAPPED_CHESS_GLYPH_WARNING,
    }
    status = "accepted" if not (set(warnings) & blocking) else "needs_review"
    return {
        "record_id": record.get("id"),
        "page": int(record.get("logical_page") or record.get("source_page") or 0),
        "label": record.get("label") or "",
        "raw_text": _bounded_text(raw_text, limit=600),
        "normalized_text": _bounded_text(mapped, limit=600),
        "pgn": candidate if status == "accepted" else "",
        "pgn_candidate": candidate,
        "status": status,
        "warnings": sorted(set(warnings or ["needs_manual_pgn_review"])),
        "ocr_token_mappings_applied": int(mapping_result.get("applied_count") or 0),
        "unmapped_token_blockers": blockers,
        "requires_source_fen": requires_source_fen,
        "source_fen": source_fen,
        "validation_status": "validated" if status == "accepted" else "not_validated",
    }


def _pgn_lattice_diagnostics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for row in rows:
        tokens = list(row.get("unmapped_token_blockers") or [])
        if not tokens:
            tokens = _glyph_mapping_tokens(str(row.get("raw_text") or ""))
        for token in tokens:
            diagnostics.append(
                {
                    "warning": UNMAPPED_CHESS_GLYPH_WARNING,
                    "source": "pgn-lattice-review",
                    "page": row.get("page"),
                    "font_name": "ocr-only",
                    "raw_text": token,
                    "context": row.get("raw_text"),
                    "codepoints": [_codepoint_label(char) for char in str(token)],
                    "bbox": [0, 0, 0, 0],
                    "reasons": ["ocr_token_blocker"],
                }
            )
    return diagnostics


def _glyph_mapping_manual_seed(candidates: dict[str, Any], glyph_mapping: dict[str, Any]) -> dict[str, Any]:
    accepted = glyph_mapping.get("accepted") or {}
    mappings: list[dict[str, Any]] = []
    for token, row in accepted.items():
        mappings.append(
            {
                "token": token,
                "replacement": row.get("replacement") or "",
                "scope": row.get("scope") or "ocr_only",
                "status": "accepted",
                "examples": row.get("examples") or [],
                "reviewer_note": row.get("reviewer_note") or "",
            }
        )
    for candidate in candidates.get("candidates") or []:
        token = str(candidate.get("token") or "")
        if not token or token in accepted:
            continue
        mappings.append(
            {
                "token": token,
                "replacement": "",
                "scope": "ocr_only",
                "status": "draft",
                "examples": candidate.get("examples") or [],
                "reviewer_note": "",
            }
        )
        if len(mappings) >= 120:
            break
    return {
        "schema": "kindlemaster.ocr_glyph_mapping.v1",
        "instructions": "Only status=accepted mappings affect PGN candidates. Parser/replay still decides accepted PGN.",
        "mappings": mappings,
    }


def _pgn_unmapped_token_blockers(rows: list[dict[str, Any]], glyph_mapping: dict[str, Any]) -> dict[str, Any]:
    blocked = [
        {
            "record_id": row.get("record_id"),
            "page": row.get("page"),
            "label": row.get("label"),
            "unmapped_token_blockers": row.get("unmapped_token_blockers") or [],
            "raw_text": row.get("raw_text"),
        }
        for row in rows
        if row.get("unmapped_token_blockers")
    ]
    return {
        "blocker_count": len(blocked),
        "raw_glyph_context_mode": "ocr_only",
        "ocr_token_mappings_loaded": int(glyph_mapping.get("accepted_count") or 0),
        "fragments": blocked[:500],
    }


def _ai_fen_candidate_row(
    out_dir: Path,
    diagram: dict[str, Any],
    *,
    provider: Any | None,
    dry_run: bool,
    verified_fen_by_diagram: dict[str, str] | None = None,
) -> dict[str, Any]:
    diagram_id = str(diagram.get("id") or diagram.get("diagram_id") or f"diagram_{len(str(diagram))}")
    crop_path = _diagram_crop_path(out_dir, diagram)
    image_bytes = crop_path.read_bytes() if crop_path.is_file() and not dry_run else b""
    image_sha = _file_sha256(crop_path) if crop_path.is_file() else ""
    context = {
        "diagram_id": diagram_id,
        "page": int(diagram.get("page") or 0),
        "caption": str(diagram.get("caption") or diagram.get("label") or ""),
        "bbox": diagram.get("bbox") or [0, 0, 0, 0],
        "side_to_move_hint": str(diagram.get("side_to_move") or "unknown"),
        "image_mime_type": _image_mime_type(crop_path),
        "image_data": image_bytes,
        "has_image": crop_path.is_file(),
    }
    result: dict[str, Any]
    if dry_run:
        result = {
            "status": "request_manifest",
            "provider": "openai-chess-fen-reviewer",
            "model": "",
            "fen": "",
            "side_to_move": "unknown",
            "confidence": 0.0,
            "needs_review": True,
            "reason": "dry_run_request_manifest",
            "estimated_cost_usd": 0.0,
        }
    elif provider is None:
        result = {
            "status": "needs_review",
            "provider": "none",
            "model": "",
            "fen": "",
            "side_to_move": "unknown",
            "confidence": 0.0,
            "needs_review": True,
            "reason": "openai_fen_provider_not_configured",
            "estimated_cost_usd": 0.0,
        }
    else:
        try:
            result = dict(provider.propose_chess_fen_from_crop(context))
        except Exception as exc:
            result = {
                "status": "needs_review",
                "provider": getattr(provider, "name", "openai-chess-fen-reviewer"),
                "model": getattr(provider, "model", ""),
                "fen": "",
                "side_to_move": "unknown",
                "confidence": 0.0,
                "needs_review": True,
                "reason": f"provider_error:{type(exc).__name__}:{exc}",
                "estimated_cost_usd": 0.0,
            }
    raw_ai_fen = str(result.get("fen") or "").strip()
    normalized_ai_fen, normalization_warnings = _normalize_ai_fen_candidate(
        raw_ai_fen,
        side_to_move=str(result.get("side_to_move") or "unknown"),
    )
    result = {**result, "fen": normalized_ai_fen}
    validation = _validate_ai_fen_candidate(
        normalized_ai_fen,
        out_dir,
        diagram_id=diagram_id,
        side_to_move=str(result.get("side_to_move") or "unknown"),
        extra_warnings=normalization_warnings,
    )
    local_fen = str(diagram.get("fen") or diagram.get("fen_candidate") or "").strip()
    local_valid = False
    if local_fen:
        local_valid, local_warnings = validate_fen(local_fen)
        local_valid = local_valid and not local_warnings
    verified_fen = str((verified_fen_by_diagram or {}).get(diagram_id) or "").strip()
    verified_valid = False
    if verified_fen:
        verified_valid, verified_warnings = validate_fen(verified_fen)
        verified_valid = verified_valid and not verified_warnings
    status = _ai_fen_status(
        result,
        validation,
        local_fen=local_fen,
        local_valid=local_valid,
        verified_fen=verified_fen,
        verified_valid=verified_valid,
    )
    return {
        "schema": "kindlemaster.ai_fen_candidate.v1",
        "diagram_id": diagram_id,
        "page": int(diagram.get("page") or 0),
        "caption": str(diagram.get("caption") or diagram.get("label") or ""),
        "bbox": diagram.get("bbox") or [0, 0, 0, 0],
        "crop_path": str(crop_path) if crop_path else "",
        "image_sha256": image_sha,
        "input_image_uploaded": bool(image_bytes),
        "provider": result.get("provider") or "none",
        "model": result.get("model") or "",
        "status": status,
        "ai_fen_candidate": normalized_ai_fen,
        "ai_fen_raw": raw_ai_fen,
        "ai_fen_normalization_warnings": normalization_warnings,
        "side_to_move": str(result.get("side_to_move") or "unknown"),
        "confidence": float(result.get("confidence") or 0.0),
        "uncertain_squares": list(result.get("uncertain_squares") or []),
        "needs_review": bool(result.get("needs_review", True)),
        "reason": str(result.get("reason") or ""),
        "local_fen_candidate": local_fen,
        "local_template_agrees": bool(local_valid and local_fen == normalized_ai_fen),
        "verified_fen_candidate": verified_fen,
        "verified_label_agrees": bool(verified_valid and verified_fen == normalized_ai_fen),
        "deterministic_validation": validation,
        "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
        "accepted_changed": False,
        "policy": "review_only_ai_candidate_never_sets_accepted",
    }


def _normalize_ai_fen_candidate(fen: str, *, side_to_move: str) -> tuple[str, list[str]]:
    value = str(fen or "").strip()
    if not value:
        return "", []
    parts = value.split()
    if len(parts) == 6:
        return value, []
    warnings: list[str] = []
    if len(parts) == 1 and "/" in parts[0]:
        side = side_to_move if side_to_move in {"w", "b"} else "w"
        if side_to_move not in {"w", "b"}:
            warnings.append("side_to_move_unknown_placeholder")
        warnings.append("ai_returned_piece_placement_only")
        return f"{parts[0]} {side} - - 0 1", warnings
    warnings.append("ai_fen_not_six_fields")
    return value, warnings


def _validate_ai_fen_candidate(
    fen: str,
    out_dir: Path,
    *,
    diagram_id: str,
    side_to_move: str,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    value = str(fen or "").strip()
    warnings: list[str] = list(extra_warnings or [])
    if not value:
        return {"valid": False, "warnings": ["fen_missing"], "rendered": {}, "king_count": 0}
    if len(value.split()) != 6:
        warnings.append("fen_must_have_6_fields")
    valid, fen_warnings = validate_fen(value)
    warnings.extend(str(item) for item in fen_warnings)
    king_count = 0
    try:
        import chess  # type: ignore[import-not-found]

        board = chess.Board(value)
        king_count = sum(1 for piece in board.piece_map().values() if piece.piece_type == chess.KING)
        if king_count != 2:
            warnings.append("fen_must_have_two_kings")
    except Exception as exc:
        valid = False
        warnings.append(f"python_chess_error:{type(exc).__name__}")
    fen_side = value.split()[1] if len(value.split()) >= 2 else ""
    if side_to_move in {"w", "b"} and fen_side and side_to_move != fen_side:
        warnings.append("side_to_move_mismatch")
    rendered = _render_valid_fen_assets(value, out_dir, diagram_id=f"{diagram_id}_ai") if valid and not warnings else {}
    if valid and not rendered:
        warnings.append("render_missing")
    return {
        "valid": bool(valid and not warnings),
        "warnings": sorted(set(warnings)),
        "king_count": king_count,
        "rendered": rendered,
    }


def _ai_fen_status(
    result: dict[str, Any],
    validation: dict[str, Any],
    *,
    local_fen: str,
    local_valid: bool,
    verified_fen: str = "",
    verified_valid: bool = False,
) -> str:
    ai_fen = str(result.get("fen") or "").strip()
    if str(result.get("status") or "") == "request_manifest":
        return "ai_suggested"
    if not ai_fen or not validation.get("valid"):
        return "needs_review"
    if verified_fen and verified_valid and verified_fen != ai_fen:
        return "ai_cv_conflict"
    if verified_fen and verified_valid and verified_fen == ai_fen:
        return "verified_label_agree"
    if local_fen and local_valid and local_fen != ai_fen:
        return "ai_cv_conflict"
    if local_fen and local_valid and local_fen == ai_fen:
        return "ai_cv_agree"
    if float(result.get("confidence") or 0.0) >= 0.80 and not bool(result.get("needs_review", True)):
        return "ai_validated_candidate"
    return "ai_suggested"


def _ai_verified_candidate_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "kindlemaster.ai_verified_candidate_queue.v1",
        "diagram_id": row.get("diagram_id"),
        "page": row.get("page"),
        "crop_path": row.get("crop_path"),
        "ai_fen_candidate": row.get("ai_fen_candidate"),
        "side_to_move": row.get("side_to_move"),
        "confidence": row.get("confidence"),
        "status": row.get("status"),
        "label_status": "draft",
        "manual_fen": "",
        "manual_label": "needs_manual_fen",
        "policy": "human verification required before template promotion",
    }


def _deepseek_pgn_cluster_payload(
    out_dir: Path,
    lattice_rows: list[dict[str, Any]],
    candidates_payload: dict[str, Any],
    *,
    provider: Any | None,
    dry_run: bool,
) -> dict[str, Any]:
    context = {
        "schema": "kindlemaster.pgn_glyph_cluster_context.v1",
        "candidate_count": candidates_payload.get("candidate_count", 0),
        "candidates": list(candidates_payload.get("candidates") or [])[:80],
        "near_accepted_rows": _select_ai_pgn_rows(lattice_rows, limit=20),
        "blocker_summary": _top_counts(warning for row in lattice_rows for warning in row.get("warnings") or []),
    }
    if provider is None and not dry_run:
        try:
            from deepseek_quality_provider import build_deepseek_audit_provider_from_env

            provider = build_deepseek_audit_provider_from_env(cwd=Path.cwd())
        except Exception:
            provider = None
    if dry_run:
        parsed = {
            "status": "request_manifest",
            "token_clusters": context["candidates"],
            "candidate_mappings": [],
            "near_accepted_records": context["near_accepted_rows"],
            "next_review_actions": ["run without --dry-run after enabling KINDLEMASTER_DEEPSEEK_AUDIT"],
        }
        provider_name = "deepseek-audit"
        model = ""
    elif provider is None:
        parsed = {
            "status": "needs_review",
            "token_clusters": context["candidates"],
            "candidate_mappings": [],
            "near_accepted_records": context["near_accepted_rows"],
            "next_review_actions": ["enable DeepSeek audit provider or review glyph_mapping_candidates.json manually"],
            "warnings": ["deepseek_provider_not_configured"],
        }
        provider_name = "none"
        model = ""
    else:
        try:
            parsed = dict(provider.review_pgn_glyph_clusters(context))
        except Exception as exc:
            parsed = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "token_clusters": context["candidates"],
                "candidate_mappings": [],
                "near_accepted_records": context["near_accepted_rows"],
            }
        provider_name = getattr(provider, "name", "deepseek-audit")
        model = getattr(getattr(provider, "config", None), "model", "")
    mappings = list(parsed.get("candidate_mappings") or parsed.get("suspected_mappings") or [])
    return {
        "schema": "kindlemaster.deepseek_glyph_clusters.v1",
        "status": parsed.get("status") or "reviewed",
        "mode": "evidence_only",
        "provider": provider_name,
        "model": model,
        "cluster_count": len(parsed.get("token_clusters") or parsed.get("glyph_clusters") or context["candidates"]),
        "candidate_mapping_count": len(mappings),
        "token_clusters": list(parsed.get("token_clusters") or parsed.get("glyph_clusters") or context["candidates"])[:120],
        "candidate_mappings": mappings[:120],
        "near_accepted_records": list(parsed.get("near_accepted_records") or context["near_accepted_rows"])[:30],
        "next_review_actions": list(parsed.get("next_review_actions") or parsed.get("next_measurements") or []),
        "warnings": list(parsed.get("warnings") or []),
        "evidence_only": True,
        "requires_human_confirmation": True,
        "mutates_output": False,
    }


def _select_ai_pgn_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row.get("status") != "accepted"]
    candidates.sort(key=_pgn_blocker_rank)
    return candidates[:limit] if limit else candidates


def _ai_pgn_candidate_row(row: dict[str, Any], *, provider: Any | None, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        result = {
            "status": "request_manifest",
            "provider": "openai-chess-pgn-reviewer",
            "model": "",
            "candidate_pgn": "",
            "confidence": 0.0,
            "reason": "dry_run_request_manifest",
            "warnings": [],
            "estimated_cost_usd": 0.0,
        }
    elif provider is None:
        result = {
            "status": "needs_review",
            "provider": "none",
            "model": "",
            "candidate_pgn": "",
            "confidence": 0.0,
            "reason": "openai_pgn_provider_not_configured",
            "warnings": ["openai_pgn_provider_not_configured"],
            "estimated_cost_usd": 0.0,
        }
    else:
        try:
            result = dict(provider.propose_pgn_repair(row))
        except Exception as exc:
            result = {
                "status": "needs_review",
                "provider": getattr(provider, "name", "openai-chess-pgn-reviewer"),
                "model": getattr(provider, "model", ""),
                "candidate_pgn": "",
                "confidence": 0.0,
                "reason": f"provider_error:{type(exc).__name__}:{exc}",
                "warnings": ["openai_pgn_provider_error"],
                "estimated_cost_usd": 0.0,
            }
    candidate = str(result.get("candidate_pgn") or "")
    replay_clean = _pgn_replay_clean(candidate)
    return {
        "schema": "kindlemaster.ai_pgn_candidate.v1",
        "record_id": row.get("record_id"),
        "page": row.get("page"),
        "label": row.get("label") or "",
        "provider": result.get("provider") or "none",
        "model": result.get("model") or "",
        "status": "deterministic_valid" if replay_clean else str(result.get("status") or "needs_review"),
        "raw_text": row.get("raw_text") or "",
        "normalized_text": row.get("normalized_text") or "",
        "source_fen": row.get("source_fen") or "",
        "requires_source_fen": bool(row.get("requires_source_fen")),
        "candidate_pgn": candidate,
        "confidence": float(result.get("confidence") or 0.0),
        "reason": str(result.get("reason") or ""),
        "warnings": sorted(set([*(row.get("warnings") or []), *(result.get("warnings") or [])])),
        "deterministic_replay_clean": bool(replay_clean),
        "accepted_changed": False,
        "estimated_cost_usd": float(result.get("estimated_cost_usd") or 0.0),
        "policy": "review_only_ai_candidate_never_updates_games_pgn",
    }


def _write_ai_cost_report(out_dir: Path) -> None:
    reports_dir = out_dir / "reports"
    review_dir = out_dir / "review"
    rows: list[dict[str, Any]] = []
    for path in [
        review_dir / "ai_fen_candidates.jsonl",
        review_dir / "ai_pgn_candidates.jsonl",
        review_dir / "gpt_pgn_repair_candidates.jsonl",
    ]:
        for row in _read_jsonl_rows(path):
            rows.append(
                {
                    "artifact": str(path.relative_to(out_dir)) if path.is_relative_to(out_dir) else str(path),
                    "provider": row.get("provider") or "",
                    "model": row.get("model") or "",
                    "estimated_cost_usd": float(row.get("estimated_cost_usd") or 0.0),
                }
            )
    payload = {
        "schema": "kindlemaster.ai_cost_report.v1",
        "status": "ok",
        "total_estimated_cost_usd": round(sum(float(row.get("estimated_cost_usd") or 0.0) for row in rows), 6),
        "calls": rows,
        "note": "Costs are zero unless providers return usage/pricing metadata; use provider dashboards for billing truth.",
    }
    _write_json(reports_dir / "ai_cost_report.json", payload)


def _diagram_crop_path(out_dir: Path, diagram: dict[str, Any]) -> Path:
    for key in ["source_crop", "image_path", "crop_rel_path", "crop_path"]:
        value = str(diagram.get(key) or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return path
        candidate = out_dir / value
        if candidate.is_file():
            return candidate
    return Path("")


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _top_counts(values: Iterable[str], *, limit: int = 12) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return [
        {"key": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _quality_dashboard_pages(
    book: dict[str, Any],
    diagrams: list[dict[str, Any]],
    pgn_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    diagrams_by_page: dict[int, list[dict[str, Any]]] = {}
    pgn_by_page: dict[int, list[dict[str, Any]]] = {}
    for diagram in diagrams:
        diagrams_by_page.setdefault(int(diagram.get("page") or 0), []).append(diagram)
    for record in pgn_records:
        pgn_by_page.setdefault(int(record.get("logical_page") or record.get("source_page") or 0), []).append(record)
    details: list[dict[str, Any]] = []
    for page in book.get("pages") or []:
        page_number = int(page.get("page") or 0)
        page_diagrams = diagrams_by_page.get(page_number, [])
        page_pgn = pgn_by_page.get(page_number, [])
        details.append(
            {
                "page": page_number,
                "diagrams": len(page_diagrams),
                "fen_accepted": len([item for item in page_diagrams if item.get("validation_status") == "accepted"]),
                "fen_review": len([item for item in page_diagrams if item.get("validation_status") != "accepted"]),
                "pgn": len(page_pgn),
                "pgn_accepted": len([item for item in page_pgn if item.get("status") == "accepted"]),
                "pgn_review": len([item for item in page_pgn if item.get("status") != "accepted"]),
                "top_blockers": _top_counts((warning for item in page_pgn for warning in item.get("warnings") or []), limit=5),
            }
        )
    return details


def _quality_dashboard_html(summary: dict[str, Any]) -> str:
    rows = "\n".join(_quality_dashboard_row(row) for row in summary.get("pages_detail") or [])
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chess Quality Dashboard</title>
  <style>
    body {{ margin:0; font-family:Georgia,'Times New Roman',serif; background:#f2e6d2; color:#21170f; }}
    header {{ padding:1rem 1.25rem; background:#24170f; color:#fff8ec; position:sticky; top:0; }}
    main {{ padding:1rem; overflow:auto; }}
    .scorebar {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:.75rem; margin:1rem 0; }}
    .score {{ background:#fff8ec; border:1px solid #d8c4a8; border-radius:16px; padding:.75rem; }}
    .score span {{ display:block; color:#735f49; font-size:.76rem; text-transform:uppercase; font-weight:900; }}
    .score strong {{ font-size:1.45rem; }}
    table {{ width:100%; border-collapse:collapse; background:#fffaf0; }}
    th, td {{ border:1px solid #d8c4a8; padding:.45rem .55rem; vertical-align:top; }}
    th {{ background:#f1dec2; text-align:left; position:sticky; top:5.5rem; }}
    code {{ overflow-wrap:anywhere; }}
  </style>
</head>
<body>
  <header>
    <h1>Chess Quality Dashboard</h1>
    <p>Evidence dashboard for FEN/PGN quality. Accepted still means deterministic validation, not AI confidence.</p>
  </header>
  <main>
    <section class="scorebar">
      {_score_tile('Diagrams', summary.get('diagrams_total'))}
      {_score_tile('FEN accepted', summary.get('fen_accepted'))}
      {_score_tile('PGN accepted', summary.get('accepted_pgn'))}
      {_score_tile('FEN eval', summary.get('fen_profile_status'))}
      {_score_tile('PGN lattice', summary.get('pgn_lattice_status'))}
      {_score_tile('AI status', summary.get('ai_assisted_status'))}
      {_score_tile('AI FEN', summary.get('ai_fen_candidates'))}
      {_score_tile('AI PGN', summary.get('ai_pgn_candidates'))}
      {_score_tile('FEN false-positive audit', summary.get('fen_false_positive_audit_status'))}
    </section>
    <table>
      <thead><tr><th>Page</th><th>Diagrams</th><th>FEN accepted/review</th><th>PGN accepted/review</th><th>Top blockers</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def _quality_dashboard_row(row: dict[str, Any]) -> str:
    blockers = ", ".join(f"{item.get('key')} ({item.get('count')})" for item in row.get("top_blockers") or [])
    return (
        "<tr>"
        f"<td>{int(row.get('page') or 0)}</td>"
        f"<td>{int(row.get('diagrams') or 0)}</td>"
        f"<td>{int(row.get('fen_accepted') or 0)} / {int(row.get('fen_review') or 0)}</td>"
        f"<td>{int(row.get('pgn_accepted') or 0)} / {int(row.get('pgn_review') or 0)}</td>"
        f"<td><code>{html.escape(blockers)}</code></td>"
        "</tr>"
    )


def build_chess_reader_semantic_book(book: Mapping[str, Any]) -> dict[str, Any]:
    """Build the versioned semantic model used by Chess Reader.

    The model is intentionally reader-facing: raw OCR diagnostics, board
    coordinates, and technical blockers are kept out of paragraph text and
    represented as component metadata/status instead.
    """

    source = dict(book or {})
    pages = _semantic_pages_with_logical_pgn(source)
    semantic_pages: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = int(page.get("page") or page.get("page_number") or 0)
        elements = _semantic_source_page_elements(page)
        study_blocks = _semantic_source_study_blocks(page, elements)
        blocks: list[dict[str, Any]] = []
        for block in study_blocks:
            blocks.extend(_semantic_book_blocks_from_study_block(page, block))
        blocks = [block for block in blocks if _semantic_book_block_has_value(block)]
        if blocks:
            semantic_pages.append({"page_number": page_number, "blocks": blocks})
    return {
        "schema": SEMANTIC_BOOK_SCHEMA,
        "book_title": str(source.get("title") or "Chess Study Reader"),
        "source_pdf": str(source.get("source_pdf") or ""),
        "source_html": str(source.get("source_html") or ""),
        "summary": _semantic_book_summary(semantic_pages),
        "pages": semantic_pages,
    }


def _write_chess_reader_semantic_book_reports(out: Path, semantic_book: Mapping[str, Any]) -> None:
    report_dir = out / "reports" / "chess_reader"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = dict(semantic_book or {})
    _write_json(report_dir / "semantic_book.json", payload)
    (report_dir / "semantic_book.md").write_text(_semantic_book_markdown(payload), encoding="utf-8")


def _semantic_book_markdown(semantic_book: Mapping[str, Any]) -> str:
    summary = dict(semantic_book.get("summary") or {})
    pages = list(semantic_book.get("pages") or [])
    first_page = pages[0] if pages else {}
    lines = [
        "# Chess Reader Semantic Book",
        "",
        f"- Schema: `{semantic_book.get('schema')}`",
        f"- Title: `{semantic_book.get('book_title')}`",
        f"- Pages: `{summary.get('page_count', 0)}`",
        f"- Blocks: `{summary.get('block_count', 0)}`",
        f"- Diagrams: `{summary.get('diagram_count', 0)}`",
        f"- Exercises: `{summary.get('exercise_count', 0)}`",
        f"- Solutions: `{summary.get('solution_count', 0)}`",
        f"- PGN blocks: `{summary.get('pgn_count', 0)}`",
        "",
        "## First Page Sample",
        "",
        "```json",
        json.dumps(first_page, ensure_ascii=False, indent=2)[:4000],
        "```",
        "",
    ]
    return "\n".join(lines)


def _semantic_book_summary(pages: list[dict[str, Any]]) -> dict[str, int]:
    blocks = [block for page in pages for block in page.get("blocks", []) if isinstance(block, dict)]
    return {
        "page_count": len(pages),
        "block_count": len(blocks),
        "paragraph_count": len([block for block in blocks if block.get("type") == "paragraph"]),
        "heading_count": len([block for block in blocks if block.get("type") == "heading"]),
        "diagram_count": len([block for block in blocks if block.get("type") == "diagram"]),
        "exercise_count": len([block for block in blocks if block.get("type") == "exercise"]),
        "solution_count": len([block for block in blocks if block.get("type") == "solution"]),
        "pgn_count": len([block for block in blocks if block.get("type") == "pgn"]),
    }


def _semantic_book_blocks_from_study_block(page: Mapping[str, Any], block: Mapping[str, Any]) -> list[dict[str, Any]]:
    if block.get("kind") == "prose":
        return _semantic_book_text_blocks(block.get("prose") or [])
    page_number = int(page.get("page") or page.get("page_number") or 0)
    output: list[dict[str, Any]] = []
    output.extend(_semantic_book_text_blocks(block.get("prose_before") or []))
    diagram = block.get("diagram") if isinstance(block.get("diagram"), Mapping) else None
    pgn = block.get("pgn") if isinstance(block.get("pgn"), Mapping) else None
    exercise_id = _semantic_exercise_id_for(diagram, pgn)
    if diagram:
        output.append(_semantic_book_diagram_block(page, diagram, pgn, exercise_id=exercise_id))
        if exercise_id:
            output.append(_semantic_book_exercise_block(page_number, diagram, exercise_id))
    if pgn:
        output.append(_semantic_book_pgn_block(pgn, exercise_id=exercise_id))
        solution = _semantic_book_solution_block(pgn, exercise_id=exercise_id, diagram_id=str((diagram or {}).get("id") or ""))
        if solution:
            output.append(solution)
    output.extend(_semantic_book_text_blocks(block.get("prose_after") or []))
    return output


def _semantic_book_text_blocks(chunks: Iterable[Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            continue
        text = _normalize_book_text(str(chunk.get("text") or ""))
        if not text or _is_reader_noise_text(text) or _is_technical_audit_text(text):
            continue
        if _semantic_text_is_coordinates_or_marker(text):
            continue
        if _source_text_kind(text) == "heading" or str(chunk.get("text_kind") or "") == "heading":
            blocks.append({"type": "heading", "level": 2, "text": text})
        else:
            blocks.append({"type": "paragraph", "text": text})
    return blocks


def _semantic_text_is_coordinates_or_marker(text: str) -> bool:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    compact = value.replace(" ", "")
    if re.fullmatch(r"[a-h]{4,8}", compact, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[1-8]{4,8}", compact):
        return True
    if value in {"△", "▼"}:
        return True
    if re.fullmatch(r"(?:bbox|raw bbox|debug id|ocr id).+", value, flags=re.IGNORECASE):
        return True
    return False


def _semantic_book_diagram_block(
    page: Mapping[str, Any],
    diagram: Mapping[str, Any],
    pgn: Mapping[str, Any] | None,
    *,
    exercise_id: str,
) -> dict[str, Any]:
    fen = str(diagram.get("fen") or diagram.get("full_fen") or "").strip() or None
    fen_candidate = str(diagram.get("fen_candidate") or "").strip()
    side = _semantic_side_to_move(diagram.get("side_to_move"))
    status = str(diagram.get("validation_status") or diagram.get("full_fen_status") or "")
    review_reason = str(
        diagram.get("review_reason")
        or diagram.get("full_fen_blocker")
        or diagram.get("fen_status")
        or ""
    ).lower()
    review_status = "verified" if status == "accepted" else "needs_review"
    pgn_text = str((pgn or {}).get("pgn") or "").strip() or None
    fen_status = "available" if fen else ("needs_review" if fen_candidate else "unavailable")
    if not fen and "fen_not_recognized" in review_reason:
        fen_status = "unavailable"
    return {
        "type": "diagram",
        "diagram_id": str(diagram.get("id") or diagram.get("diagram_id") or ""),
        "caption": str(diagram.get("caption") or diagram.get("label") or diagram.get("id") or "Diagram"),
        "source_page": int(diagram.get("page") or page.get("page") or page.get("page_number") or 0),
        "side_to_move": side,
        "fen": fen,
        "fen_status": fen_status,
        "pgn": pgn_text,
        "pgn_status": "available" if pgn_text else ("needs_review" if pgn else "unavailable"),
        "board_crop_path": str(diagram.get("board_crop_path") or diagram.get("source_crop") or diagram.get("image_path") or ""),
        "side_marker_crop_path": str(diagram.get("side_marker_crop_path") or ""),
        "side_marker_status": str(diagram.get("side_marker_status") or ""),
        "asset_missing_reason": str(diagram.get("asset_missing_reason") or ""),
        "original_page_path": str(page.get("page_preview") or ""),
        "review_status": review_status,
        "exercise_id": exercise_id,
    }


def _semantic_book_exercise_block(page_number: int, diagram: Mapping[str, Any], exercise_id: str) -> dict[str, Any]:
    caption = str(diagram.get("caption") or diagram.get("label") or diagram.get("id") or "")
    return {
        "type": "exercise",
        "exercise_id": exercise_id,
        "diagram_id": str(diagram.get("id") or diagram.get("diagram_id") or ""),
        "source_page": page_number,
        "difficulty": _semantic_difficulty_from_text(caption),
    }


def _semantic_book_pgn_block(pgn: Mapping[str, Any], *, exercise_id: str) -> dict[str, Any]:
    pgn_text = str(pgn.get("pgn") or "").strip()
    visible_text = str(pgn.get("visible_review_text") or pgn.get("raw_text") or "").strip()
    status = "available" if pgn_text else ("needs_review" if visible_text else "unavailable")
    return {
        "type": "pgn",
        "exercise_id": exercise_id,
        "label": str(pgn.get("label") or pgn.get("id") or "PGN / book line"),
        "pgn": pgn_text or None,
        "book_line": visible_text or None,
        "pgn_status": status,
    }


def _semantic_book_solution_block(
    pgn: Mapping[str, Any],
    *,
    exercise_id: str,
    diagram_id: str,
) -> dict[str, Any] | None:
    pgn_text = str(pgn.get("pgn") or "").strip()
    visible_text = str(pgn.get("visible_review_text") or pgn.get("raw_text") or "").strip()
    book_line = pgn_text or visible_text
    if not book_line:
        return None
    return {
        "type": "solution",
        "exercise_id": exercise_id,
        "diagram_id": diagram_id,
        "best_move": _semantic_best_move_from_line(book_line),
        "book_line": book_line,
        "commentary": visible_text if visible_text and visible_text != pgn_text else "",
    }


def _semantic_book_block_has_value(block: Mapping[str, Any]) -> bool:
    block_type = str(block.get("type") or "")
    if block_type in {"paragraph", "heading"}:
        return bool(str(block.get("text") or "").strip())
    if block_type == "diagram":
        return bool(str(block.get("diagram_id") or block.get("caption") or "").strip())
    return bool(block_type)


def _semantic_side_to_move(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"w", "white"}:
        return "white"
    if normalized in {"b", "black"}:
        return "black"
    return "unknown"


def _semantic_exercise_id_for(diagram: Mapping[str, Any] | None, pgn: Mapping[str, Any] | None) -> str:
    candidates = [
        str((diagram or {}).get("caption") or ""),
        str((diagram or {}).get("label") or ""),
        str((diagram or {}).get("id") or ""),
        str((pgn or {}).get("label") or ""),
        str((pgn or {}).get("visible_review_text") or ""),
    ]
    for candidate in candidates:
        exercise_id = _semantic_exercise_id_from_text(candidate)
        if exercise_id:
            return exercise_id
    diagram_id = str((diagram or {}).get("id") or "")
    return f"ex_{_safe_filename(diagram_id)}" if diagram_id else ""


def _semantic_exercise_id_from_text(text: str) -> str:
    value = str(text or "")
    match = EXERCISE_LABEL_RE.search(value)
    if match:
        return f"ex_{int(match.group('chapter'))}_{int(match.group('number'))}"
    diagram_match = re.search(r"\bDiagram\s+(?P<chapter>\d{1,2})[-.](?P<number>\d{1,2})\b", value, flags=re.IGNORECASE)
    if diagram_match:
        return f"ex_{int(diagram_match.group('chapter'))}_{int(diagram_match.group('number'))}"
    final_match = FINAL_LABEL_RE.search(value)
    if final_match:
        return f"final_{int(final_match.group('number'))}"
    return ""


def _semantic_difficulty_from_text(text: str) -> str:
    match = re.search(r"(?<!\*)\*{1,3}(?!\*)", str(text or ""))
    if not match:
        return "unknown"
    return match.group(0)


def _semantic_best_move_from_line(text: str) -> str:
    value = str(text or "")
    match = re.search(
        r"\b\d+\.(?:\.\.)?\s*(?P<move>O-O-O|O-O|0-0-0|0-0|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?)",
        value,
    )
    return str(match.group("move")).replace("0", "O") if match else ""


def _semantic_book_flow_html(semantic_book: Mapping[str, Any]) -> str:
    pages = [page for page in semantic_book.get("pages") or [] if isinstance(page, Mapping)]
    parts: list[str] = []
    for page in pages:
        page_number = int(page.get("page_number") or 0)
        page_parts = [f'<span class="page-anchor" id="page-{page_number:03d}" aria-label="Source page {page_number}"></span>']
        for block in page.get("blocks") or []:
            if isinstance(block, Mapping):
                rendered = _semantic_book_block_html(block, page_number=page_number)
                if rendered:
                    page_parts.append(rendered)
        if len(page_parts) > 1:
            parts.extend(page_parts)
    return "\n".join(parts) or '<p class="empty-page">No extractable reader content found.</p>'


def _semantic_book_block_html(block: Mapping[str, Any], *, page_number: int) -> str:
    block_type = str(block.get("type") or "")
    if block_type == "heading":
        level = min(3, max(2, int(block.get("level") or 2)))
        return f'<h{level} class="reader-text semantic-heading" data-page="{page_number}">{html.escape(str(block.get("text") or ""))}</h{level}>'
    if block_type == "paragraph":
        return f'<p class="reader-text semantic-paragraph" data-page="{page_number}">{html.escape(str(block.get("text") or ""))}</p>'
    if block_type == "diagram":
        return _semantic_source_diagram_html(_diagram_record_from_semantic_block(block, page_number=page_number))
    if block_type == "exercise":
        return f"""<section class="exercise-card" data-kind="exercise" data-exercise-id="{html.escape(str(block.get('exercise_id') or ''), quote=True)}">
  <h3>{html.escape(str(block.get('exercise_id') or 'Exercise'))}</h3>
  <p>Difficulty: <strong>{html.escape(str(block.get('difficulty') or 'unknown'))}</strong></p>
</section>"""
    if block_type == "pgn":
        return _semantic_source_pgn_html(
            {
                "id": str(block.get("exercise_id") or block.get("label") or ""),
                "label": str(block.get("label") or "PGN / book line"),
                "status": "accepted" if block.get("pgn_status") == "available" and block.get("pgn") else "needs-human-review",
                "pgn": str(block.get("pgn") or ""),
                "visible_review_text": str(block.get("book_line") or ""),
                "logical_page": page_number,
            }
        )
    if block_type == "solution":
        return f"""<section class="solution-card" data-kind="solution" data-exercise-id="{html.escape(str(block.get('exercise_id') or ''), quote=True)}">
  <h3>Solution</h3>
  {f'<p><strong>Best move</strong> {html.escape(str(block.get("best_move") or ""))}</p>' if block.get("best_move") else ''}
  {f'<pre class="pgn"><code>{html.escape(str(block.get("book_line") or ""))}</code></pre>' if block.get("book_line") else ''}
  {f'<p>{html.escape(str(block.get("commentary") or ""))}</p>' if block.get("commentary") else ''}
</section>"""
    return ""


def _diagram_record_from_semantic_block(block: Mapping[str, Any], *, page_number: int) -> dict[str, Any]:
    side = str(block.get("side_to_move") or "unknown")
    side_value = "w" if side == "white" else ("b" if side == "black" else "")
    fen = str(block.get("fen") or "")
    return {
        "id": str(block.get("diagram_id") or ""),
        "caption": str(block.get("caption") or block.get("diagram_id") or "Diagram"),
        "page": int(block.get("source_page") or page_number),
        "validation_status": "accepted" if block.get("review_status") == "verified" else "needs-human-review",
        "review_reason": "FEN unavailable" if block.get("fen_status") != "available" else "",
        "side_to_move": side_value,
        "fen": fen,
        "image_path": str(block.get("board_crop_path") or ""),
        "side_marker_crop_path": str(block.get("side_marker_crop_path") or ""),
        "side_marker_status": str(block.get("side_marker_status") or ""),
        "asset_missing_reason": str(block.get("asset_missing_reason") or "source_asset_unavailable"),
    }


def _semantic_source_index_html(book: dict[str, Any]) -> str:
    summary = dict(book.get("summary") or {})
    artifact_manifest = dict(book.get("artifact_manifest") or {})
    chapters = list(book.get("chapters") or [])
    pages = _semantic_pages_with_logical_pgn(book)
    toc = "\n".join(
        f'<li><a href="#page-{int(chapter.get("start_page") or 1):03d}">{html.escape(str(chapter.get("title") or chapter.get("id") or ""))}</a></li>'
        for chapter in chapters
        if int(chapter.get("start_page") or 0)
    )
    semantic_book = book.get("semantic_book") if isinstance(book.get("semantic_book"), Mapping) else {}
    flow_html = (
        _semantic_book_flow_html(semantic_book)
        if semantic_book.get("schema") == SEMANTIC_BOOK_SCHEMA
        else _semantic_source_book_flow_html(pages)
    )
    mode_switch = """<nav class="reader-mode-switch" aria-label="Reader view modes">
      <a href="#book" class="active" data-reader-mode-option="reader">Reader</a>
      <a href="#book" data-reader-mode-option="study">Study</a>
      <a href="reports/conversion-audit.md" data-reader-mode-option="audit">Audit</a>
    </nav>"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <link rel="stylesheet" href="styles.css">
  <title>{html.escape(str(book.get('title') or 'Chess Study Reader'))}</title>
</head>
<body class="reader-shell" {_artifact_data_attrs(artifact_manifest)}>
  <header class="app-header">
    <p class="eyebrow">MasterKindle study reader</p>
    <h1>{html.escape(str(book.get('title') or 'Chess Study Reader'))}</h1>
    <p class="lede">Logical chess-study reader rebuilt from source order: prose, diagrams, FEN/PGN review, and notation stay together without reproducing the PDF page layout 1:1.</p>
    {_artifact_provenance_banner_html(artifact_manifest)}
    {mode_switch}
    <section class="scorebar" aria-label="Conversion summary">
      {_score_tile('Pages', summary.get('html_pages'))}
      {_score_tile('Diagrams', summary.get('diagrams_total'))}
      {_score_tile('FEN accepted', summary.get('fen_accepted'))}
      {_score_tile('PGN accepted', summary.get('accepted_pgn'))}
      {_score_tile('Needs review', int(summary.get('fen_needs_review') or 0) + int(summary.get('pgn_needs_review') or 0))}
    </section>
  </header>
  <div class="layout">
    <aside class="sidebar">
      <nav aria-label="Table of contents">
        <h2>Contents</h2>
        <ol>{toc}</ol>
      </nav>
      <section class="filters" aria-label="Filters">
        <h2>Filters</h2>
        <label><input type="checkbox" data-filter="diagram" checked> Diagrams</label>
        <label><input type="checkbox" data-filter="pgn" checked> PGN</label>
        <label><input type="checkbox" data-filter="review" checked> Review</label>
        <label><input type="checkbox" data-reader-mode> Reader mode</label>
      </section>
      <p class="report-link"><a href="reports/conversion-audit.md">Conversion audit</a></p>
    </aside>
    <main class="book-flow" id="book">{flow_html}</main>
  </div>
  <script src="app.js"></script>
</body>
</html>
"""


def _engine_analysis_by_diagram_id(out: Path) -> dict[str, dict[str, Any]]:
    payload = _read_optional_json(out / "data" / "engine_analysis.json")
    rows = payload.get("items") if isinstance(payload, dict) else []
    return {
        str(row.get("diagram_id") or ""): dict(row)
        for row in rows or []
        if isinstance(row, dict) and str(row.get("diagram_id") or "")
    }


def _book_move_comparison_by_diagram_id(out: Path) -> dict[str, dict[str, Any]]:
    payload = _read_optional_json(out / "data" / "book_move_comparison.json")
    rows = payload.get("items") if isinstance(payload, dict) else []
    return {
        str(row.get("diagram_id") or ""): dict(row)
        for row in rows or []
        if isinstance(row, dict) and str(row.get("diagram_id") or "")
    }


def _engine_hints_by_diagram_id(out: Path) -> dict[str, dict[str, Any]]:
    payload = _read_optional_json(out / "data" / "engine_hints.json")
    rows = payload.get("items") if isinstance(payload, dict) else []
    return {
        str(row.get("diagram_id") or ""): dict(row)
        for row in rows or []
        if isinstance(row, dict) and str(row.get("diagram_id") or "")
    }


def _attach_engine_analysis_to_book(book: dict[str, Any], out: Path) -> dict[str, Any]:
    engine_by_id = _engine_analysis_by_diagram_id(out)
    hints_by_id = _engine_hints_by_diagram_id(out)
    comparison_by_id = _book_move_comparison_by_diagram_id(out)
    if not engine_by_id and not hints_by_id and not comparison_by_id:
        return book
    pages: list[dict[str, Any]] = []
    for page in book.get("pages") or []:
        if not isinstance(page, dict):
            continue
        next_page = dict(page)
        next_diagrams: list[dict[str, Any]] = []
        for diagram in page.get("diagrams") or []:
            if not isinstance(diagram, dict):
                continue
            next_diagram = _attach_engine_analysis_to_record(dict(diagram), engine_by_id)
            next_diagram = _attach_engine_hints_to_record(next_diagram, hints_by_id)
            next_diagram = _attach_book_move_comparison_to_record(next_diagram, comparison_by_id)
            next_diagrams.append(next_diagram)
        next_page["diagrams"] = next_diagrams
        pages.append(next_page)
    payload = _read_optional_json(out / "data" / "engine_analysis.json")
    hints_payload = _read_optional_json(out / "data" / "engine_hints.json")
    comparison_payload = _read_optional_json(out / "data" / "book_move_comparison.json")
    return {
        **book,
        "pages": pages,
        "engine_analysis_summary": payload.get("summary") or {},
        "engine_hints_summary": hints_payload.get("summary") or {},
        "book_move_comparison_summary": comparison_payload.get("summary") or {},
    }


def _attach_engine_analysis_to_record(record: dict[str, Any], engine_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in (record.get("diagram_id"), record.get("id"), record.get("source_diagram"), record.get("label")):
        value = str(key or "")
        if value and value in engine_by_id:
            return {**record, "engine_analysis": engine_by_id[value]}
    return record


def _attach_engine_hints_to_record(record: dict[str, Any], hints_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in (record.get("diagram_id"), record.get("id"), record.get("source_diagram"), record.get("label")):
        value = str(key or "")
        if value and value in hints_by_id:
            return {**record, "engine_hints": hints_by_id[value]}
    return record


def _attach_book_move_comparison_to_record(record: dict[str, Any], comparison_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in (record.get("diagram_id"), record.get("id"), record.get("source_diagram"), record.get("label")):
        value = str(key or "")
        if value and value in comparison_by_id:
            return {**record, "book_move_comparison": comparison_by_id[value]}
    return record


def _engine_analysis_panel_html(
    analysis: Mapping[str, Any] | None,
    *,
    mode: str,
    open_by_default: bool = False,
) -> str:
    row = dict(analysis or {})
    status = str(row.get("engine_status") or "missing")
    summary_label = {
        "reader": "Analiza silnika",
        "study": "Pokaż analizę silnika",
        "audit": "Dane techniczne silnika",
    }.get(mode, "Analiza silnika")
    if not row:
        body = '<p class="engine-empty">Analiza silnika niedostępna dla tej pozycji.</p>'
    elif status == "ok":
        pv = _engine_pv_text(row)
        body = f"""
        <div class="engine-kpis">
          <span><strong>Silnik</strong>{html.escape(_engine_name(row))}</span>
          <span><strong>Ocena</strong>{html.escape(_engine_score_text(row))}</span>
          <span><strong>Najlepszy ruch</strong>{html.escape(str(row.get('best_move_san') or row.get('best_move_uci') or ''))}</span>
          <span><strong>Głębokość / czas</strong>{html.escape(_engine_depth_time(row))}</span>
        </div>
        {f'<p class="engine-pv"><strong>Główna linia</strong> {html.escape(pv)}</p>' if pv else ''}
        {f'<p class="engine-cache">Cache: {html.escape("hit" if row.get("cache_hit") else "miss")}</p>' if mode == "audit" else ''}
        """
    else:
        reason = str(row.get("skip_reason") or status or "analysis_missing")
        body = f"""<p class="engine-empty">Analiza silnika niedostępna dla tej pozycji.</p>
        <p class="engine-reason">Powód: <code>{html.escape(reason)}</code></p>"""
        if mode != "audit":
            return f"""<div class="engine-panel engine-panel-{html.escape(mode, quote=True)} engine-panel-unavailable" data-engine-status="{html.escape(status, quote=True)}">
  <strong>Analiza silnika niedostępna</strong>
  <div class="engine-panel-body">{body}</div>
</div>"""
    audit_details = ""
    if mode == "audit" and row:
        audit_details = f"""<pre class="engine-technical">{html.escape(json.dumps({
            "engine_status": row.get("engine_status"),
            "skip_reason": row.get("skip_reason"),
            "fen_status": row.get("fen_status"),
            "side_marker_status": row.get("side_marker_status"),
            "engine_version": row.get("engine_version"),
            "cache_hit": row.get("cache_hit"),
            "elapsed_ms": row.get("elapsed_ms"),
            "depth": row.get("depth"),
            "multipv": row.get("multipv"),
        }, ensure_ascii=False, indent=2))}</pre>"""
    open_attr = " open" if open_by_default else ""
    return f"""<details class="engine-panel engine-panel-{html.escape(mode, quote=True)}" data-engine-status="{html.escape(status, quote=True)}"{open_attr}>
  <summary>{html.escape(summary_label)}</summary>
  <div class="engine-panel-body">{body}{audit_details}</div>
</details>"""


def _book_move_comparison_panel_html(
    comparison: Mapping[str, Any] | None,
    *,
    mode: str,
    open_by_default: bool = False,
) -> str:
    row = dict(comparison or {})
    if not row:
        return ""
    status = str(row.get("match_status") or "unknown")
    summary_label = {
        "reader": "Ruch ksiazki vs silnik",
        "study": "Porownaj ruch ksiazki",
        "audit": "Audyt ruchu ksiazki",
    }.get(mode, "Ruch ksiazki vs silnik")
    status_label = {
        "exact_match": "zgodne",
        "equivalent_move": "zgodne wariantowo",
        "book_move_legal_but_not_best": "wymaga sprawdzenia",
        "book_move_illegal": "ruch nielegalny z zaakceptowanego FEN",
        "no_book_move": "brak parsowalnego ruchu ksiazki",
        "engine_unavailable": "silnik niedostepny",
    }.get(status, status)
    body = f"""
      <div class="engine-kpis">
        <span><strong>Ruch z ksiazki</strong>{html.escape(str(row.get('book_move_san') or row.get('book_move_uci') or 'brak'))}</span>
        <span><strong>Ruch silnika</strong>{html.escape(str(row.get('engine_best_move_san') or row.get('engine_best_move_uci') or 'brak'))}</span>
        <span><strong>Status</strong>{html.escape(status_label)}</span>
      </div>
      {f'<p class="engine-reason">Powod review: <code>{html.escape(str(row.get("review_reason") or ""))}</code></p>' if row.get("review_reason") else ''}
    """
    audit_details = ""
    if mode == "audit":
        audit_details = f"""<pre class="engine-technical">{html.escape(json.dumps({
            "diagram_id": row.get("diagram_id"),
            "match_status": row.get("match_status"),
            "book_move_raw": row.get("book_move_raw"),
            "book_move_san": row.get("book_move_san"),
            "book_move_uci": row.get("book_move_uci"),
            "engine_best_move_san": row.get("engine_best_move_san"),
            "engine_best_move_uci": row.get("engine_best_move_uci"),
            "requires_review": row.get("requires_review"),
            "review_reason": row.get("review_reason"),
            "source_pgn_id": row.get("source_pgn_id"),
            "source_type": row.get("source_type"),
        }, ensure_ascii=False, indent=2))}</pre>"""
    open_attr = " open" if open_by_default else ""
    return f"""<details class="book-move-comparison-panel book-move-comparison-{html.escape(status, quote=True)}" data-book-move-status="{html.escape(status, quote=True)}"{open_attr}>
  <summary>{html.escape(summary_label)}</summary>
  <div class="engine-panel-body">{body}{audit_details}</div>
</details>"""


def _engine_study_hints_panel_html(
    hints: Mapping[str, Any] | None,
    *,
    mode: str,
    open_by_default: bool = False,
) -> str:
    if mode == "reader":
        return ""
    row = dict(hints or {})
    if not row:
        return ""
    status = str(row.get("hint_status") or "unavailable")
    source = str(row.get("source") or "engine_rule_based_v1")
    if status != "available":
        if mode != "audit":
            return ""
        reason = str(row.get("unavailable_reason") or "engine_hint_unavailable")
        return f"""<details class="engine-hints-panel engine-hints-unavailable" data-engine-hint-status="{html.escape(status, quote=True)}" data-engine-hint-source="{html.escape(source, quote=True)}">
  <summary>Engine hint unavailable</summary>
  <div class="engine-panel-body">
    <p class="engine-empty">Engine hint unavailable for this position.</p>
    <p class="engine-reason">Reason: <code>{html.escape(reason)}</code></p>
  </div>
</details>"""
    hint_1 = str(row.get("hint_level_1") or "").strip()
    hint_2 = str(row.get("hint_level_2") or "").strip()
    best_move = str(row.get("best_move_san") or row.get("best_move_uci") or "").strip()
    pv = _engine_pv_text(row)
    score = _engine_score_text(row)
    audit_details = ""
    if mode == "audit":
        audit_details = f"""<pre class="engine-technical">{html.escape(json.dumps({
            "diagram_id": row.get("diagram_id"),
            "hint_status": row.get("hint_status"),
            "source": source,
            "move_features": row.get("move_features"),
            "engine_status": row.get("engine_status"),
            "skip_reason": row.get("skip_reason"),
        }, ensure_ascii=False, indent=2))}</pre>"""
    open_attr = " open" if open_by_default else ""
    return f"""<details class="engine-hints-panel engine-hints-{html.escape(status, quote=True)}" data-engine-hint-status="{html.escape(status, quote=True)}" data-engine-hint-source="{html.escape(source, quote=True)}"{open_attr}>
  <summary>Engine hint</summary>
  <div class="engine-panel-body">
    <div class="engine-hint-steps">
      <details class="engine-hint-level" open>
        <summary>Podpowiedz 1</summary>
        <p>{html.escape(hint_1)}</p>
      </details>
      <details class="engine-hint-level">
        <summary>Podpowiedz 2</summary>
        <p>{html.escape(hint_2)}</p>
      </details>
      <details class="engine-hint-full-reveal" data-full-reveal-available="{str(bool(row.get('full_reveal_available'))).lower()}">
        <summary>Pokaz najlepszy ruch</summary>
        <p><strong>Najlepszy ruch</strong> {html.escape(best_move or 'brak')}</p>
        <p><strong>Ocena</strong> {html.escape(score)}</p>
      </details>
      <details class="engine-hint-line-reveal">
        <summary>Pokaz linie silnika</summary>
        <p>{html.escape(pv or 'Glowna linia niedostepna.')}</p>
      </details>
    </div>
    <p class="engine-hint-source">Source: <code>{html.escape(source)}</code></p>
    {audit_details}
  </div>
</details>"""


def _engine_name(row: Mapping[str, Any]) -> str:
    engine = str(row.get("engine") or "stockfish")
    version = str(row.get("engine_version") or "").strip()
    return f"{engine} {version}".strip()


def _engine_score_text(row: Mapping[str, Any]) -> str:
    if row.get("mate") is not None:
        return f"mat {row.get('mate')}"
    if row.get("score_cp") is None:
        return "brak oceny"
    try:
        return f"{int(row.get('score_cp')) / 100:+.2f}"
    except (TypeError, ValueError):
        return str(row.get("score_cp"))


def _engine_depth_time(row: Mapping[str, Any]) -> str:
    depth = str(row.get("depth") or "?")
    elapsed = str(row.get("elapsed_ms") or "0")
    return f"{depth} ply / {elapsed} ms"


def _engine_pv_text(row: Mapping[str, Any]) -> str:
    pv = row.get("pv")
    if not isinstance(pv, list) or not pv:
        return ""
    first = pv[0] if isinstance(pv[0], dict) else {}
    san = first.get("moves_san") if isinstance(first, dict) else []
    uci = first.get("moves_uci") if isinstance(first, dict) else []
    moves = san if san else uci
    return " ".join(str(move) for move in moves or [])


def _semantic_source_book_flow_html(pages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for page in pages:
        if not _semantic_source_page_elements(page):
            continue
        parts.extend(_semantic_source_flow_blocks_for_page(page))
    return "\n".join(parts) or '<p class="empty-page">No extractable reader content found.</p>'


def _semantic_source_flow_blocks_for_page(page: dict[str, Any]) -> list[str]:
    page_number = int(page.get("page") or 0)
    elements = _semantic_source_page_elements(page)
    blocks = _semantic_source_study_blocks(page, elements)
    rendered: list[str] = []
    for index, block in enumerate(blocks):
        if not rendered:
            rendered.append(f'<span class="page-anchor" id="page-{page_number:03d}" aria-label="Source page {page_number}"></span>')
        rendered.append(_semantic_source_study_block_html({**block, "block_index_on_page": index}))
    return rendered


def _semantic_pages_with_logical_pgn(book: dict[str, Any]) -> list[dict[str, Any]]:
    pages = [dict(page) for page in book.get("pages") or []]
    by_page: dict[int, list[dict[str, Any]]] = {}
    for record in book.get("pgn_records") or []:
        if not isinstance(record, dict):
            continue
        page_number = int(record.get("logical_page") or record.get("source_page") or 0)
        if page_number <= 0:
            continue
        by_page.setdefault(page_number, []).append(record)
    for page in pages:
        page_number = int(page.get("page") or 0)
        existing_ids = {str(record.get("id") or "") for record in page.get("pgn_records") or [] if isinstance(record, dict)}
        logical_records = [record for record in by_page.get(page_number, []) if str(record.get("id") or "") not in existing_ids]
        if logical_records:
            page["pgn_records"] = [*(page.get("pgn_records") or []), *logical_records]
    return pages


def _semantic_source_reader_section_html(page: dict[str, Any]) -> str:
    page_number = int(page.get("page") or 0)
    elements = _semantic_source_page_elements(page)
    blocks = _semantic_source_study_blocks(page, elements)
    block_html = "\n".join(_semantic_source_study_block_html(block) for block in blocks)
    return f"""<section class="study-page" id="page-{page_number:03d}" data-page="{page_number}">
  <header class="page-heading">
    <span>Source page {page_number}</span>
    <a href="{html.escape(str(page.get('page_preview') or ''), quote=True)}" class="page-preview-link">PDF preview</a>
  </header>
  {block_html}
</section>"""


def _semantic_source_study_blocks(page: dict[str, Any], elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    label_blocks = _semantic_label_anchored_blocks(page)
    if label_blocks:
        return label_blocks
    blocks: list[dict[str, Any]] = []
    prose_buffer: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None

    def flush_active() -> None:
        nonlocal active
        if active is not None:
            blocks.append(active)
            active = None

    def flush_prose() -> None:
        nonlocal prose_buffer
        if prose_buffer:
            blocks.append({"kind": "prose", "page": int(page.get("page") or 0), "prose": prose_buffer})
            prose_buffer = []

    for element in elements:
        kind = str(element.get("kind") or "")
        if kind == "text":
            if active is not None and len(active.get("prose_after") or []) < 3:
                active.setdefault("prose_after", []).append(element)
            else:
                prose_buffer.append(element)
            continue
        if kind == "diagram":
            flush_prose()
            flush_active()
            active = {
                "kind": "study",
                "page": int(page.get("page") or 0),
                "diagram": element,
                "pgn": None,
                "prose_before": [],
                "prose_after": [],
                "preview": page.get("page_preview") or "",
            }
            continue
        if kind == "pgn":
            flush_prose()
            if active is None:
                active = {
                    "kind": "study",
                    "page": int(page.get("page") or 0),
                    "diagram": None,
                    "pgn": element,
                    "prose_before": [],
                    "prose_after": [],
                    "preview": page.get("page_preview") or "",
                }
            elif active.get("pgn") is None:
                active["pgn"] = element
            else:
                flush_active()
                active = {
                    "kind": "study",
                    "page": int(page.get("page") or 0),
                    "diagram": None,
                    "pgn": element,
                    "prose_before": [],
                    "prose_after": [],
                    "preview": page.get("page_preview") or "",
                }
            continue
    flush_active()
    flush_prose()
    return blocks


def _semantic_label_anchored_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    text_chunks = [dict(item) for item in page.get("text_chunks", []) or [] if isinstance(item, dict)]
    diagrams = [dict(item) for item in page.get("diagrams", []) or [] if isinstance(item, dict)]
    records = [dict(item) for item in page.get("pgn_records", []) or [] if isinstance(item, dict) and _semantic_record_has_reader_value(item)]
    if not diagrams and not records:
        return []
    diagram_by_label = {
        _normalize_source_label(str(item.get("caption") or item.get("label") or item.get("id") or "")): item
        for item in diagrams
    }
    record_by_label = {
        _normalize_source_label(str(item.get("label") or item.get("visible_review_text") or item.get("raw_text") or "")): item
        for item in records
    }
    anchors: list[dict[str, Any]] = []
    used_labels: set[str] = set()
    for index, chunk in enumerate(text_chunks):
        label = _semantic_first_diagram_label(str(chunk.get("text") or ""))
        if not label:
            continue
        normalized = _normalize_source_label(label)
        if normalized in used_labels:
            continue
        used_labels.add(normalized)
        anchors.append(
            {
                "label": label,
                "normalized": normalized,
                "index": index,
                "order": int(chunk.get("reading_order") or index),
                "diagram": diagram_by_label.get(normalized),
                "pgn": record_by_label.get(normalized),
            }
        )
    if not anchors:
        return []
    blocks: list[dict[str, Any]] = []
    first_anchor = anchors[0]["index"]
    if first_anchor > 0:
        blocks.append({"kind": "prose", "page": int(page.get("page") or 0), "prose": text_chunks[:first_anchor]})
    for anchor_index, anchor in enumerate(anchors):
        start = int(anchor["index"])
        end = int(anchors[anchor_index + 1]["index"]) if anchor_index + 1 < len(anchors) else len(text_chunks)
        body = text_chunks[start:end]
        blocks.append(
            {
                "kind": "study",
                "page": int(page.get("page") or 0),
                "diagram": anchor.get("diagram"),
                "pgn": anchor.get("pgn"),
                "prose_before": body[:1],
                "prose_after": body[1:],
                "preview": page.get("page_preview") or "",
            }
        )
    for diagram in diagrams:
        normalized = _normalize_source_label(str(diagram.get("caption") or diagram.get("label") or diagram.get("id") or ""))
        if normalized and normalized in used_labels:
            continue
        blocks.append(
            {
                "kind": "study",
                "page": int(page.get("page") or 0),
                "diagram": diagram,
                "pgn": record_by_label.get(normalized),
                "prose_before": [],
                "prose_after": [],
                "preview": page.get("page_preview") or "",
            }
        )
        used_labels.add(normalized)
    for record in records:
        normalized = _normalize_source_label(str(record.get("label") or record.get("visible_review_text") or record.get("raw_text") or ""))
        if normalized and normalized in used_labels:
            continue
        blocks.append(
            {
                "kind": "study",
                "page": int(page.get("page") or 0),
                "diagram": None,
                "pgn": record,
                "prose_before": [],
                "prose_after": [],
                "preview": page.get("page_preview") or "",
            }
        )
    return blocks


def _semantic_first_diagram_label(text: str) -> str:
    match = re.search(r"\bDiagram\s+\d{1,2}[-.]\d{1,2}\b", str(text or ""), flags=re.IGNORECASE)
    if not match:
        return ""
    value = match.group(0)
    return re.sub(r"\s+", " ", value).replace(".", "-")


def _semantic_source_study_block_html(block: dict[str, Any]) -> str:
    if block.get("kind") == "prose":
        prose = "\n".join(_semantic_source_text_html(item) for item in block.get("prose") or [])
        page = int(block.get("page") or 0)
        return f"""<section class="flow-prose" data-kind="prose" data-page="{page}">
  {prose}
</section>"""
    diagram = block.get("diagram")
    pgn = block.get("pgn")
    page = int(block.get("page") or 0)
    status = _semantic_block_status(diagram, pgn)
    title = _semantic_block_title(diagram, pgn, page)
    before = "\n".join(_semantic_source_text_html(item) for item in block.get("prose_before") or [])
    after = "\n".join(_semantic_source_text_html(item) for item in block.get("prose_after") or [])
    diagram_html = _semantic_source_diagram_panel_html(diagram) if diagram else '<div class="diagram-panel missing">Diagram not matched</div>'
    notation_html = _semantic_source_pgn_panel_html(pgn) if pgn else '<p class="muted">No PGN fragment matched to this diagram yet.</p>'
    return f"""<article class="study-block" data-kind="study-block" data-status="{html.escape(status, quote=True)}" data-page="{page}">
  <header class="study-block-header">
    <div>
      <p class="eyebrow"><a href="#page-{page:03d}">Page {page}</a> · study block</p>
      <h2>{html.escape(title)}</h2>
    </div>
    <span class="review-badge {html.escape(status, quote=True)}">{html.escape(_friendly_status(status))}</span>
  </header>
  <div class="study-block-grid">
    {diagram_html}
    <div class="study-content">
      {before}
      {after}
      {notation_html}
    </div>
  </div>
</article>"""


def _semantic_source_text_html(element: dict[str, Any]) -> str:
    raw_text = str(element.get("text") or "")
    if _is_reader_noise_text(raw_text):
        return ""
    text = html.escape(raw_text)
    tag = "h3" if element.get("kind") == "heading" or element.get("text_kind") == "heading" else "p"
    return f'<{tag} class="reader-text" data-order="{int(element.get("reading_order") or 0)}">{text}</{tag}>'


def _semantic_block_title(diagram: dict[str, Any] | None, pgn: dict[str, Any] | None, page: int) -> str:
    if diagram:
        return str(diagram.get("caption") or diagram.get("id") or f"Study position page {page}")
    if pgn:
        return str(pgn.get("label") or pgn.get("id") or f"Notation page {page}")
    return f"Study block page {page}"


def _semantic_block_status(diagram: dict[str, Any] | None, pgn: dict[str, Any] | None) -> str:
    statuses = [
        str((diagram or {}).get("validation_status") or ""),
        str((pgn or {}).get("status") or ""),
    ]
    return "accepted" if statuses and all(status == "accepted" for status in statuses if status) else "needs-human-review"


def _semantic_source_page_html(page: dict[str, Any]) -> str:
    page_number = int(page.get("page") or 0)
    elements = _semantic_source_page_elements(page)
    element_html = "\n".join(_semantic_source_element_html(element) for element in elements)
    if not element_html:
        element_html = '<p class="empty-page">No extractable reader content on this page.</p>'
    return f"""<article class="chapter-page" id="page-{page_number:03d}" data-page="{page_number}">
  <header class="page-heading">
    <span>Source page {page_number}</span>
    <a href="{html.escape(str(page.get('page_preview') or ''), quote=True)}" class="page-preview-link">PDF preview</a>
  </header>
  {element_html}
</article>"""


def _semantic_source_page_elements(page: dict[str, Any]) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    diagrams = [diagram for diagram in page.get("diagrams", []) or [] if isinstance(diagram, dict)]
    for chunk in page.get("text_chunks", []) or []:
        elements.append({"kind": "text", **chunk})
    for diagram in diagrams:
        elements.append({"kind": "diagram", **diagram})
    for record in page.get("pgn_records", []) or []:
        if _semantic_record_has_reader_value(record):
            elements.append({"kind": "pgn", **record, "reading_order": _semantic_pgn_reading_order(record, diagrams)})
    elements.sort(key=_source_order_key)
    return elements


def _semantic_pgn_reading_order(record: dict[str, Any], diagrams: list[dict[str, Any]]) -> int:
    label = _normalize_source_label(str(record.get("label") or record.get("visible_review_text") or record.get("raw_text") or ""))
    for diagram in diagrams:
        diagram_label = _normalize_source_label(str(diagram.get("caption") or diagram.get("label") or diagram.get("id") or ""))
        if label and diagram_label and (label == diagram_label or label in diagram_label or diagram_label in label):
            return int(diagram.get("reading_order") or 0) + 1
    return int(record.get("reading_order") or 0)


def _semantic_record_has_reader_value(record: dict[str, Any]) -> bool:
    if str(record.get("pgn") or "").strip():
        return True
    text = str(record.get("visible_review_text") or record.get("raw_text") or "")
    if re.search(r"\b(?:Diagram|Ex\.)\s*\d{1,2}[-.]\d{1,2}\b", text, flags=re.IGNORECASE):
        return True
    if _looks_like_notation_text(text):
        return True
    return False


def _semantic_source_element_html(element: dict[str, Any]) -> str:
    kind = str(element.get("kind") or "")
    if kind == "diagram":
        return _semantic_source_diagram_html(element)
    if kind == "pgn":
        return _semantic_source_pgn_html(element)
    raw_text = str(element.get("text") or "")
    if _is_reader_noise_text(raw_text):
        return ""
    text = html.escape(raw_text)
    tag = "h2" if element.get("kind") == "heading" or element.get("text_kind") == "heading" else "p"
    return f'<{tag} class="reader-text" data-order="{int(element.get("reading_order") or 0)}">{text}</{tag}>'


def _semantic_source_diagram_panel_html(diagram: dict[str, Any]) -> str:
    return f'<div class="diagram-panel" data-kind="diagram">{_semantic_source_diagram_html(diagram)}</div>'


def _semantic_source_pgn_panel_html(record: dict[str, Any]) -> str:
    return f'<div class="notation-panel" data-kind="pgn">{_semantic_source_pgn_html(record)}</div>'


def _semantic_source_diagram_html(diagram: dict[str, Any]) -> str:
    status = str(diagram.get("validation_status") or "needs-human-review")
    fen = str(diagram.get("fen") or "")
    fen_candidate = str(diagram.get("fen_candidate") or "")
    image_path = str(diagram.get("image_path") or "")
    caption = str(diagram.get("caption") or diagram.get("id") or "Diagram")
    missing_reason = str(diagram.get("asset_missing_reason") or "source_asset_unavailable")
    marker_status = str(diagram.get("side_marker_status") or "")
    marker_attr = f' data-side-marker-status="{html.escape(marker_status, quote=True)}"' if marker_status else ""
    has_board_crop = bool(str(diagram.get("board_crop_path") or diagram.get("source_crop") or image_path).strip())
    has_side_marker_crop = bool(str(diagram.get("side_marker_crop_path") or "").strip())
    original_image_html = (
        f'<img src="{html.escape(image_path, quote=True)}" alt="{html.escape(caption, quote=True)}">'
        if image_path
        else (
            '<div class="diagram-asset-placeholder" '
            f'data-asset-missing-reason="{html.escape(missing_reason, quote=True)}">'
            f'Original diagram image unavailable: {html.escape(missing_reason.replace("_", " "))}</div>'
        )
    )
    copy_html = (
        f'<button type="button" class="copy-button" data-copy-value="{html.escape(fen, quote=True)}">Copy FEN</button>'
        if fen and status == "accepted"
        else ""
    )
    candidate_html = ""
    if not fen and fen_candidate:
        candidate_html = f"""<div class="candidate">
  <span>FEN candidate requires review</span>
  <code>{html.escape(fen_candidate)}</code>
</div>"""
    engine_html = _engine_analysis_panel_html(diagram.get("engine_analysis"), mode="reader")
    hints_html = _engine_study_hints_panel_html(diagram.get("engine_hints"), mode="reader")
    comparison_html = _book_move_comparison_panel_html(diagram.get("book_move_comparison"), mode="reader")
    side_to_move = str(diagram.get("side_to_move") or "").strip().lower()
    side_label = {"w": "white", "white": "white", "b": "black", "black": "black"}.get(side_to_move, "")
    review_reason = _friendly_reader_reason(str(diagram.get("review_reason") or "Awaiting deterministic FEN recognition."))
    side_status_html = (
        f'<p class="component-status ok">Side to move: <strong>{html.escape(side_label)}</strong></p>'
        if side_label
        else '<p class="component-status review">Side to move unavailable <a class="review-action" href="#review">Send to review</a></p>'
    )
    fen_status_html = (
        f"""<div class="fen-copy-block code-copy-block">
  <div class="code-block-header"><span>FEN</span>{copy_html}</div>
  <pre class="fen"><code>{html.escape(fen)}</code></pre>
</div>"""
        if fen
        else (
            '<div class="component-status review">'
            '<strong>FEN unavailable</strong>'
            f'<span>Reason: {html.escape(review_reason)}</span>'
            '<a class="review-action" href="#review">Send to review</a>'
            '</div>'
        )
    )
    return f"""<figure class="diagram-card" id="{html.escape(str(diagram.get('id') or ''), quote=True)}" data-kind="diagram" data-status="{html.escape(status, quote=True)}"{marker_attr} data-has-board-crop="{str(has_board_crop).lower()}" data-has-side-marker-crop="{str(has_side_marker_crop).lower()}">
  <header class="card-header">
    <h3>{html.escape(caption)}</h3>
    <span class="source-ref">Page {int(diagram.get('page') or 0)}</span>
    <span class="review-badge {html.escape(status, quote=True)}">{html.escape(_friendly_status(status))}</span>
  </header>
  <div class="diagram-grid">
    <div class="board-placeholder" data-fen="{html.escape(fen, quote=True)}">{_fen_board_placeholder(fen)}</div>
    <div class="diagram-meta">
      {side_status_html}
      <p class="review-reason">{html.escape(review_reason)}</p>
      {candidate_html}
      {fen_status_html}
      {engine_html}
      {hints_html}
      {comparison_html}
      <details class="original-diagram">
        <summary>Podgląd oryginału</summary>
        {original_image_html}
      </details>
    </div>
  </div>
</figure>"""


def _friendly_reader_reason(reason: str) -> str:
    value = str(reason or "").strip()
    if not value:
        return "needs component review"
    replacements = {
        "fen_not_recognized": "FEN was not recognized with enough confidence",
        "mass_side_to_move_unknown": "side marker needs review",
        "board_crop_quality=fail": "board crop needs review",
        "marker_crop_quality=fail": "side marker crop needs review",
        "side_to_move_unknown": "side marker needs review",
    }
    normalized = value
    for raw, friendly in replacements.items():
        normalized = normalized.replace(raw, friendly)
    return normalized.replace("_", " ")


def _semantic_source_pgn_html(record: dict[str, Any]) -> str:
    status = str(record.get("status") or "needs-human-review")
    pgn = str(record.get("pgn") or "")
    body = (
        f"""<div class="pgn-copy-block code-copy-block">
  <div class="code-block-header"><span>PGN</span><button type="button" class="copy-button" data-copy-value="{html.escape(pgn, quote=True)}">Copy PGN</button></div>
  <pre class="pgn"><code>{html.escape(pgn)}</code></pre>
</div>"""
        if status == "accepted" and pgn
        else f'<details class="review-details"><summary>Notation needs human review</summary><p>{html.escape(str(record.get("visible_review_text") or ""))}</p></details>'
    )
    warnings = ", ".join(str(item) for item in record.get("warnings") or [])
    return f"""<section class="pgn-card" id="{html.escape(str(record.get('id') or ''), quote=True)}" data-kind="pgn" data-status="{html.escape(status, quote=True)}">
  <header class="card-header">
    <h3>{html.escape(str(record.get('label') or 'PGN / solution'))}</h3>
    <span class="source-ref">Page {int(record.get('logical_page') or record.get('source_page') or 0)}</span>
    <span class="review-badge {html.escape(status, quote=True)}">{html.escape(_friendly_status(status))}</span>
  </header>
  {body}
  {f'<p class="warnings">Blocked by: {html.escape(warnings)}</p>' if warnings else ''}
</section>"""


def _semantic_source_styles_css() -> str:
    return """:root {
  --km-bg-paper:#F6F0E6;
  --km-surface:#FFFDF8;
  --km-text:#1F1A14;
  --km-muted:#6F6257;
  --km-border:#E2D5C4;
  --km-accent:#B86B2E;
  --km-accent-dark:#7A3E18;
  --km-success:#2F7D55;
  --km-warning:#B7791F;
  --km-error:#B23A30;
  --km-code-bg:#F1E7D6;
  --km-shadow:0 18px 48px rgba(63,42,20,.10);
  --km-radius-card:16px;
  --km-radius-control:999px;
  --reader-text-width:680px;
  --reader-diagram-width:340px;
  --ink:var(--km-text); --muted:var(--km-muted); --paper:var(--km-surface); --surface:#FFFAF1; --wash:#EFE3D1;
  --line:var(--km-border); --accent:var(--km-accent-dark); --ok:var(--km-success); --warn:var(--km-warning); --bad:var(--km-error);
}
* { box-sizing:border-box; }
html, body { max-width:100%; overflow-x:hidden; }
body { margin:0; font-family:Georgia, 'Times New Roman', serif; color:var(--km-text); background:var(--km-bg-paper); line-height:1.62; }
body.reader-shell { min-height:100vh; }
a { color:var(--accent); }
.app-header { max-width:1240px; margin:0 auto; padding:2rem 1.25rem 1.1rem; }
.eyebrow { margin:0 0 .35rem; color:var(--accent); font-size:.78rem; font-weight:900; letter-spacing:.08em; text-transform:uppercase; }
h1 { margin:.1rem 0 .6rem; font-size:clamp(2rem,5vw,4.4rem); line-height:1; }
.lede { max-width:70ch; color:var(--km-muted); font-size:1.08rem; overflow-wrap:anywhere; }
.scorebar { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:.75rem; margin-top:1.2rem; }
.score { background:var(--km-surface); border:1px solid var(--km-border); border-radius:var(--km-radius-card); padding:.8rem .9rem; box-shadow:0 10px 30px rgba(47,30,9,.06); }
.score span { display:block; color:var(--muted); font-size:.76rem; font-weight:900; letter-spacing:.05em; text-transform:uppercase; }
.score strong { display:block; margin-top:.15rem; font-size:1.45rem; line-height:1.1; }
.artifact-provenance { background:var(--km-surface); border:1px solid var(--km-border); border-left:6px solid var(--km-accent); border-radius:var(--km-radius-card); padding:.9rem 1rem; margin:1rem 0; box-shadow:0 10px 30px rgba(47,30,9,.06); }
.artifact-provenance h2 { margin:0 0 .35rem; font-size:1rem; }
.artifact-provenance p { margin:.2rem 0; }
.reader-mode-switch { display:flex; flex-wrap:wrap; gap:.5rem; margin:1rem 0 0; }
.reader-mode-switch a { display:inline-flex; align-items:center; justify-content:center; min-height:44px; padding:.5rem .9rem; border:1px solid var(--km-border); border-radius:var(--km-radius-control); background:var(--km-surface); color:var(--km-text); font-family:Inter, ui-sans-serif, system-ui, sans-serif; font-size:.9rem; font-weight:800; text-decoration:none; }
.reader-mode-switch a.active { background:var(--km-accent-dark); color:#FFFDF8; border-color:var(--km-accent-dark); }
.layout { max-width:1240px; margin:0 auto; display:grid; grid-template-columns:220px minmax(0,1fr); gap:1rem; padding:0 1rem 3rem; }
.sidebar { position:sticky; top:1rem; align-self:start; max-height:calc(100vh - 2rem); overflow:auto; background:#2B2118; color:#FFFDF8; border:1px solid rgba(255,253,248,.12); border-radius:var(--km-radius-card); padding:1rem; }
.sidebar a, .sidebar label { color:#fff8ed; display:block; margin:.45rem 0; text-decoration:none; }
.sidebar ol { padding-left:1.25rem; }
.filters { border-top:1px solid rgba(255,255,255,.2); margin-top:1rem; padding-top:.75rem; }
.book-flow { min-width:0; width:100%; display:grid; gap:1rem; }
.page-anchor { display:block; position:relative; top:-1rem; height:1px; overflow:hidden; }
.flow-meta { color:var(--muted); font-size:.84rem; font-weight:800; margin:0 0 .3rem; }
.flow-prose { max-width:var(--reader-text-width); margin:0 0 1rem; padding:.2rem 0 .2rem 1rem; border-left:3px solid rgba(184,107,46,.24); }
.chapter-page { background:var(--km-surface); border:1px solid var(--km-border); border-radius:var(--km-radius-card); padding:1.25rem; margin:0 0 1.2rem; box-shadow:var(--km-shadow); }
.page-heading { display:flex; justify-content:space-between; gap:1rem; align-items:center; border-bottom:1px solid var(--line); padding-bottom:.7rem; margin-bottom:.9rem; color:var(--muted); font-weight:800; }
.study-block { min-width:0; max-width:100%; background:var(--km-surface); border:1px solid var(--km-border); border-radius:var(--km-radius-card); padding:1.15rem; margin:0 0 1.15rem; box-shadow:var(--km-shadow); }
.study-block-header { display:flex; justify-content:space-between; gap:1rem; align-items:flex-start; border-bottom:1px solid var(--line); margin-bottom:1rem; padding-bottom:.75rem; }
.study-block-header h2 { margin:.1rem 0 0; font-size:clamp(1.35rem,2.5vw,2rem); line-height:1.12; }
.study-block-grid { display:grid; grid-template-columns:minmax(620px,var(--reader-text-width)) minmax(300px,var(--reader-diagram-width)); gap:1.5rem; align-items:start; }
.study-content { min-width:0; }
.study-prose-only { background:rgba(255,250,241,.72); border:1px solid var(--km-border); border-radius:14px; padding:.75rem 1rem; margin:0 0 .8rem; }
.reader-text { max-width:var(--reader-text-width); margin:.65rem 0; overflow-wrap:anywhere; font-size:17.5px; line-height:1.64; }
h2.reader-text { margin-top:1.4rem; color:#5c3215; font-size:1.45rem; }
.diagram-panel { min-width:0; }
.diagram-panel.missing { min-height:12rem; display:grid; place-items:center; border:1px dashed var(--line); border-radius:18px; color:var(--muted); background:#fffdf8; }
.notation-panel { min-width:0; }
.diagram-card, .pgn-card, .exercise-card, .solution-card { border:1px solid var(--km-border); border-radius:var(--km-radius-card); background:var(--km-surface); padding:1rem; margin:0 0 1rem; box-shadow:0 12px 30px rgba(63,42,20,.07); }
.diagram-panel .diagram-card { margin-bottom:0; }
.card-header { display:flex; flex-wrap:wrap; gap:.5rem; align-items:center; justify-content:space-between; margin-bottom:.75rem; }
.card-header h3 { margin:0; font-size:1.2rem; }
.source-ref { color:var(--muted); font-size:.92rem; overflow-wrap:anywhere; }
.component-status { display:flex; flex-wrap:wrap; gap:.45rem .65rem; align-items:center; margin:.45rem 0; padding:.55rem .65rem; border:1px solid var(--line); border-radius:14px; background:#fffdf8; font-family:Arial, sans-serif; font-size:.9rem; line-height:1.35; }
.component-status.review { border-color:#ddb26e; background:#fff8e8; color:#5a3511; }
.component-status.ok { border-color:#8ec3a0; background:#eefaf1; color:#124d2d; }
.review-action { margin-left:auto; font-weight:800; white-space:nowrap; }
.review-badge, .status-chip { border:1px solid var(--line); border-radius:999px; padding:.18rem .55rem; font-size:.82rem; font-weight:900; }
.review-badge.accepted { color:var(--ok); border-color:rgba(20,107,58,.35); }
.review-badge.needs-human-review, .review-badge.needs_review { color:var(--warn); border-color:rgba(167,97,0,.35); }
.diagram-grid { display:grid; grid-template-columns:minmax(280px,340px) minmax(0,1fr); gap:1rem; align-items:start; }
.diagram-panel .diagram-grid { grid-template-columns:1fr; }
.board-placeholder { min-height:280px; display:grid; place-items:center; border:1px dashed var(--line); border-radius:14px; background:#fffdf8; color:var(--muted); text-align:center; padding:1rem; }
.mini-board { width:min(320px,100%); aspect-ratio:1; display:grid; grid-template-columns:repeat(8,1fr); border:1px solid var(--line); color:var(--ink); }
.mini-board span { display:grid; place-items:center; font-family:Georgia,serif; font-weight:900; }
.mini-board span:nth-child(16n+1), .mini-board span:nth-child(16n+3), .mini-board span:nth-child(16n+5), .mini-board span:nth-child(16n+7),
.mini-board span:nth-child(16n+10), .mini-board span:nth-child(16n+12), .mini-board span:nth-child(16n+14), .mini-board span:nth-child(16n+16) { background:#b58863; }
.mini-board span:nth-child(16n+2), .mini-board span:nth-child(16n+4), .mini-board span:nth-child(16n+6), .mini-board span:nth-child(16n+8),
.mini-board span:nth-child(16n+9), .mini-board span:nth-child(16n+11), .mini-board span:nth-child(16n+13), .mini-board span:nth-child(16n+15) { background:#f0d9b5; }
.diagram-meta { min-width:0; }
.candidate code, pre { display:block; max-width:100%; overflow-wrap:anywhere; word-break:break-word; white-space:pre-wrap; background:var(--km-code-bg); border:1px solid #e3d1b6; border-radius:12px; padding:.7rem; font-family:ui-monospace, SFMono-Regular, Consolas, monospace; font-size:13.5px; line-height:1.45; }
.code-copy-block { border:1px solid #e3d1b6; border-radius:14px; background:#FFF8EC; padding:.65rem; margin:.65rem 0; }
.code-block-header { display:flex; align-items:center; justify-content:space-between; gap:.75rem; margin-bottom:.5rem; font-family:Inter, ui-sans-serif, system-ui, sans-serif; font-size:.8rem; font-weight:900; color:var(--km-muted); text-transform:uppercase; }
.code-copy-block pre { margin:0; }
.copy-button { min-height:44px; border:1px solid var(--km-border); border-radius:var(--km-radius-control); background:#FFFDF8; color:var(--km-accent-dark); padding:.5rem .9rem; font-family:Inter, ui-sans-serif, system-ui, sans-serif; font-weight:900; cursor:pointer; }
.copy-button:hover { background:#F8EBD7; border-color:var(--km-accent); }
.copy-button:focus-visible, a:focus-visible, summary:focus-visible, input:focus-visible, button:focus-visible { outline:3px solid #b96920; outline-offset:3px; }
.original-diagram summary, .review-details summary { cursor:pointer; min-height:44px; font-weight:900; color:var(--accent); }
.original-diagram img { max-width:100%; height:auto; border-radius:10px; border:1px solid var(--line); background:#fff; }
.engine-panel, .engine-hints-panel, .book-move-comparison-panel, .try-self-panel, .solution-panel, .original-source { border:1px solid var(--line); border-radius:14px; background:#fffaf1; margin:.65rem 0; padding:0 .75rem; }
.engine-panel summary, .engine-hints-panel summary, .book-move-comparison-panel summary, .try-self-panel summary, .solution-panel summary, .original-source summary { cursor:pointer; min-height:44px; font-weight:900; color:var(--accent); }
.engine-panel-body { padding:0 0 .75rem; }
.engine-kpis { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:.5rem; }
.engine-kpis span { border:1px solid #ead8bf; border-radius:12px; background:#fffdf8; padding:.55rem .6rem; overflow-wrap:anywhere; }
.engine-kpis strong { display:block; color:var(--muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.04em; }
.engine-empty, .engine-reason, .engine-cache, .engine-pv, .engine-hint-source { margin:.45rem 0; color:var(--muted); }
.engine-hint-steps { display:grid; gap:.45rem; }
.engine-hint-level, .engine-hint-full-reveal, .engine-hint-line-reveal { border:1px solid #ead8bf; border-radius:12px; background:#fffdf8; padding:0 .6rem; }
.engine-hint-level p, .engine-hint-full-reveal p, .engine-hint-line-reveal p { margin:.35rem 0 .6rem; }
.study-actions { min-width:0; display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:.55rem; margin:.75rem 0; }
.study-actions > * { min-width:0; }
.diagram-asset-placeholder { border:1px dashed var(--line); border-radius:10px; background:#fffdf8; color:var(--muted); padding:.8rem; overflow-wrap:anywhere; }
.pdf-context { margin-top:.85rem; border-top:1px solid var(--line); padding-top:.5rem; }
.pdf-context summary { cursor:pointer; min-height:44px; font-weight:900; color:var(--accent); }
.pdf-context img { max-width:100%; height:auto; border-radius:14px; border:1px solid var(--line); background:#fff; }
.warnings, .review-reason { color:var(--muted); }
body.hide-diagram [data-kind=\"diagram\"], body.hide-pgn [data-kind=\"pgn\"], body.hide-review [data-status=\"needs-human-review\"] { display:none; }
body.reader-mode .page-preview-link, body.reader-mode .warnings { display:none; }
.empty-page { color:var(--muted); font-style:italic; }
@media (max-width: 1180px) { .study-block-grid { grid-template-columns:1fr; } .diagram-grid { grid-template-columns:minmax(280px,340px) minmax(0,1fr); } }
@media (max-width: 940px) { .layout { grid-template-columns:1fr; } .sidebar { position:relative; top:auto; max-height:none; } .scorebar { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width: 840px) { .study-block-grid { grid-template-columns:1fr; } }
@media (max-width: 720px) { .layout,.app-header { padding-left:.85rem; padding-right:.85rem; } .scorebar,.diagram-grid,.study-actions { grid-template-columns:1fr; } .chapter-page,.study-block { border-radius:0; margin-left:-.85rem; margin-right:-.85rem; } .flow-prose { padding-left:.75rem; } .study-block-header { display:block; } .code-block-header { align-items:stretch; flex-direction:column; } .copy-button { width:100%; } }
"""


def _semantic_source_app_js() -> str:
    return """document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy-value]');
  if (!button) return;
  const value = button.getAttribute('data-copy-value') || '';
  if (!value.trim()) return;
  await navigator.clipboard.writeText(value);
  const previous = button.textContent;
  button.textContent = 'Copied';
  setTimeout(() => { button.textContent = previous; }, 1200);
});

document.querySelectorAll('[data-filter]').forEach((input) => {
  input.addEventListener('change', () => {
    document.body.classList.toggle(`hide-${input.dataset.filter}`, !input.checked);
  });
});

const readerMode = document.querySelector('[data-reader-mode]');
if (readerMode) {
  readerMode.addEventListener('change', () => {
    document.body.classList.toggle('reader-mode', readerMode.checked);
  });
}
"""


def _score_tile(label: str, value: Any) -> str:
    return f'<div class="score"><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>'


def _source_page_number(node: Any, *, fallback: int) -> int:
    if not node:
        return fallback
    value = node.get("data-page") or node.get("data-page-number") or node.get("id") or ""
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else fallback


def _source_reading_order(node: Any, *, fallback: int) -> int:
    value = node.get("data-reading-order") if node else None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return int(fallback)


def _source_style_box(style: str) -> list[float]:
    values: dict[str, float] = {}
    for key, value in re.findall(r"([a-zA-Z-]+)\s*:\s*(-?\d+(?:\.\d+)?)px", str(style or "")):
        values[key.lower()] = round(float(value), 3)
    return [
        values.get("left", 0.0),
        values.get("top", 0.0),
        values.get("width", 0.0),
        values.get("height", 0.0),
    ]


def _source_order_key(item: dict[str, Any]) -> tuple[int, int, float, float]:
    bbox = list(item.get("bbox") or [0, 0, 0, 0])
    x = float(bbox[0] if len(bbox) > 0 else 0.0)
    y = float(bbox[1] if len(bbox) > 1 else 0.0)
    return (int(item.get("page") or 0), int(item.get("reading_order") or 0), y, x)


def _save_data_uri_asset(
    src: str,
    out_dir: Path,
    *,
    filename_stem: str,
    default_ext: str,
    relative_prefix: str,
) -> str:
    value = str(src or "").strip()
    if not value:
        return ""
    if not value.startswith("data:image/"):
        return "" if "localhost" in value or "127.0.0.1" in value else value
    match = re.match(r"data:(image/[A-Za-z0-9.+-]+);base64,(.*)", value, flags=re.DOTALL)
    if not match:
        return ""
    mime = match.group(1).lower()
    ext = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime, default_ext)
    target = out_dir / f"{_safe_filename(filename_stem)}{ext}"
    try:
        target.write_bytes(base64.b64decode(match.group(2), validate=False))
    except Exception:
        return ""
    return str(Path(relative_prefix) / target.name).replace("\\", "/")


def _source_text_chunks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = _normalize_book_text(" ".join(str(item.get("text") or "") for item in current))
        current.clear()
        if not text or _is_reader_noise_text(text) or _is_technical_audit_text(text):
            return
        first = current_first
        chunks.append(
            {
                "id": f"chunk-p{int(first.get('page') or 0):03d}-{len(chunks) + 1:04d}",
                "page": int(first.get("page") or 0),
                "reading_order": int(first.get("reading_order") or 0),
                "bbox": list(first.get("bbox") or [0, 0, 0, 0]),
                "text": text,
                "text_kind": _source_text_kind(text),
            }
        )

    current_first: dict[str, Any] = {}
    for block in blocks:
        text = str(block.get("text") or "").strip()
        if not text or _is_reader_noise_text(text):
            continue
        is_heading = _source_text_kind(text) == "heading"
        if is_heading:
            flush()
            chunks.append(
                {
                    "id": f"heading-p{int(block.get('page') or 0):03d}-{len(chunks) + 1:04d}",
                    "page": int(block.get("page") or 0),
                    "reading_order": int(block.get("reading_order") or 0),
                    "bbox": list(block.get("bbox") or [0, 0, 0, 0]),
                    "text": text,
                    "text_kind": "heading",
                }
            )
            continue
        if not current:
            current_first = block
        current.append(block)
        joined = " ".join(str(item.get("text") or "") for item in current)
        if len(joined) >= 420 or re.search(r"[.!?)]$", text):
            flush()
    flush()
    return chunks


def _source_text_kind(text: str) -> str:
    value = str(text or "").strip()
    if re.match(r"^(?:Chapter\s+)?\d{1,2}\s+[A-Z][A-Za-z ,'-]{3,}$", value):
        return "heading"
    if re.match(r"^(?:Preface|Introduction|Exercises|Solutions|Final Test|Index|Recommended Books)$", value, re.IGNORECASE):
        return "heading"
    return "text"


def _nearest_source_caption(bbox: list[float], captions: list[dict[str, Any]]) -> str:
    if not captions:
        return ""
    x, y = (float(bbox[0] if len(bbox) > 0 else 0.0), float(bbox[1] if len(bbox) > 1 else 0.0))
    best = min(
        captions,
        key=lambda item: abs(float((item.get("bbox") or [0, 0])[1]) - y) + abs(float((item.get("bbox") or [0, 0])[0]) - x) * 0.25,
    )
    return str(best.get("text") or "")


def _extract_fen_candidate_from_node(node: Any) -> str:
    values = [
        node.get("data-fen") if node else "",
        node.get("data-fen-candidate") if node else "",
        node.get("fen") if node else "",
    ]
    text = node.get_text(" ", strip=True) if node else ""
    values.append(text)
    fen_re = re.compile(r"\b(?:[pnbrqkPNBRQK1-8]+/){7}[pnbrqkPNBRQK1-8]+\s+[wb]\s+(?:K?Q?k?q?|-)\s+(?:[a-h][36]|-)\s+\d+\s+\d+\b")
    for value in values:
        match = fen_re.search(str(value or ""))
        if match:
            return match.group(0)
    return ""


def _source_fen_status(fen: str, *, image_path: str) -> dict[str, Any]:
    value = str(fen or "").strip()
    if not value:
        return {
            "validation_status": "needs-human-review",
            "confidence": 0.0,
            "review_reason": "FEN recognition is not available for this extracted diagram crop yet.",
        }
    valid, warnings = validate_fen(value)
    if valid and not warnings and image_path:
        return {"validation_status": "accepted", "confidence": 1.0, "review_reason": ""}
    return {
        "validation_status": "needs-human-review",
        "confidence": 0.25 if value else 0.0,
        "review_reason": "; ".join(warnings or ["FEN candidate failed deterministic validation."]),
    }


def _infer_side_to_move(text: str) -> str:
    value = str(text or "").lower()
    if "black to move" in value or "black" in value and "move" in value:
        return "black"
    if "white to move" in value or "white" in value and "move" in value:
        return "white"
    return "unknown"


def _extract_embedded_pgn_candidate(node: Any) -> str:
    if not node:
        return ""
    for selector in ["pre code", "pre", "code.language-pgn", ".pgn"]:
        child = node.select_one(selector)
        if child:
            candidate = child.get_text("\n", strip=True)
            if "[Event" in candidate:
                return candidate
    text = node.get_text("\n", strip=True)
    return text if "[Event" in text and "[Result" in text else ""


def _source_pgn_warnings(raw_text: str, pgn: str, *, accepted: bool) -> list[str]:
    warnings: list[str] = []
    if accepted:
        return warnings
    if not pgn:
        warnings.append("pgn_missing_or_not_embedded")
    elif not _pgn_has_required_headers(pgn):
        warnings.append("pgn_missing_required_headers")
    elif not _pgn_replay_clean(pgn):
        warnings.append("pgn_replay_errors")
    value = str(raw_text or "")
    for token in ["unmapped_chess_glyphs", "move_number_jump", "move_number_regression", "side_to_move_mismatch", "pgn_replay_errors"]:
        if token in value:
            warnings.append(token)
    if _contains_unmapped_notation_glyphs(value):
        warnings.append(UNMAPPED_CHESS_GLYPH_WARNING)
    return sorted(set(warnings or ["needs_manual_pgn_review"]))


def _normalize_source_label(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower().replace(".", ""))


def _first_source_page_matching(pages: list[dict[str, Any]], pattern: str) -> int | None:
    regex = re.compile(pattern, re.IGNORECASE)
    for page in pages:
        text = " ".join(str(block.get("text") or "") for block in page.get("text_blocks", []) or [])
        if regex.search(text):
            return int(page.get("page") or 0)
    return None


def _missing_page_numbers(pdf_pages: int, pages: list[dict[str, Any]]) -> list[int]:
    if not pdf_pages:
        return []
    present = {int(page.get("page") or 0) for page in pages}
    return [page for page in range(1, int(pdf_pages) + 1) if page not in present]


def _visible_review_notation_text(raw_text: str) -> str:
    text = str(raw_text or "")
    text = re.sub(r"\bDo weryfikacji\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"PGN requires review; strict export is blocked\.?", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(?:move_number_jump|move_number_regression|pgn_replay_errors|side_to_move_mismatch|unmapped_chess_glyphs)(?:,\s*)?",
        "",
        text,
    )
    return _normalize_book_text(text).strip()


def _scrub_local_links(value: str) -> str:
    return re.sub(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?\S*", "", str(value or ""))


def _friendly_status(value: str) -> str:
    normalized = str(value or "").replace("_", "-")
    if normalized == "accepted":
        return "Validated"
    if normalized in {"needs-human-review", "needs-review"}:
        return "Needs human review"
    return normalized.replace("-", " ").title()


def _fen_board_placeholder(fen: str) -> str:
    if not fen:
        return "<span>Board render waits for a validated FEN.</span>"
    rows = str(fen).split()[0].split("/")
    pieces = {"p": "p", "n": "n", "b": "b", "r": "r", "q": "q", "k": "k"}
    cells: list[str] = []
    for row in rows:
        for char in row:
            if char.isdigit():
                cells.extend([""] * int(char))
            else:
                cells.append(pieces.get(char.lower(), char))
    if len(cells) != 64:
        return "<span>Validated FEN</span>"
    items = "".join(f'<span>{html.escape(cell)}</span>' for cell in cells)
    return f'<div class="mini-board" aria-label="FEN board">{items}</div>'



def ingest_study_pdf(config: ChessStudyConfig) -> dict[str, Any]:
    """Build the page/text source model used by renderers and QA gates."""
    pages: list[StudyPage] = []
    page_image_count = 0
    render_dpi = max(72, int(config.diagram_dpi or 160))
    page_image_dir = config.out / "assets" / "page_images"
    page_image_dir.mkdir(parents=True, exist_ok=True)
    page_image_cache_hits = 0
    page_image_cache_misses = 0
    html_pages_by_number = {
        int(page.get("page_number") or 0): page
        for page in (_html_page_texts(config.html) if config.html and config.html.is_file() else [])
    }
    pdf_mtime = config.pdf.stat().st_mtime if config.pdf.is_file() else 0.0

    with fitz.open(config.pdf) as document:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_image = ""
            if config.render_pages:
                page_image, cache_status = _render_pdf_page_image(
                    page,
                    page_number=page_number,
                    dpi=render_dpi,
                    out_dir=page_image_dir,
                    source_pdf=str(config.pdf),
                    source_mtime=pdf_mtime,
                )
                if page_image:
                    page_image_count += 1
                if cache_status == "hit":
                    page_image_cache_hits += 1
                if cache_status == "miss":
                    page_image_cache_misses += 1
            blocks = _study_text_blocks(page, page_number=page_number)
            html_page = html_pages_by_number.get(page_number) or {}
            html_blocks = _html_study_text_blocks(html_page, page_number=page_number, start_order=len(blocks))
            if html_blocks and _should_apply_html_text_assist(blocks, html_blocks):
                blocks = [*blocks, *html_blocks]
                blocks.sort(key=lambda item: (item.bbox[1] if len(item.bbox) > 1 else 0.0, item.bbox[0] if item.bbox else 0.0, item.reading_order))
            raw_text = "\n".join(block.text for block in blocks).strip()
            normalized_text = _normalize_book_text(raw_text)
            paragraphs = _paragraphs_from_blocks(blocks)
            elements = _layout_elements_from_text_blocks(blocks, page_number=page_number)
            pages.append(
                StudyPage(
                    page=page_number,
                    pdf_page_index=page_index,
                    width=float(page.rect.width or 0.0),
                    height=float(page.rect.height or 0.0),
                    page_image=page_image,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    paragraphs=paragraphs,
                    blocks=blocks,
                    elements=elements,
                )
            )

    page_dicts = [page.to_dict() for page in pages]
    summary = {
        "source_pdf": str(config.pdf),
        "profile": config.quality_profile,
        "page_count": len(page_dicts),
        "page_images": page_image_count,
        "page_image_cache_hits": page_image_cache_hits,
        "page_image_cache_misses": page_image_cache_misses,
        "pages_with_extractable_text": len([page for page in page_dicts if str(page.get("normalized_text") or "").strip()]),
        "copyable_text_characters": sum(len(str(page.get("normalized_text") or "")) for page in page_dicts),
        "ocr_fallback_requested": bool(config.ocr_fallback),
        "ocr_fallback_applied": False,
    }
    payload = {"summary": summary, "pages": page_dicts}
    _write_jsonl(config.out / "pages.jsonl", page_dicts)
    _write_jsonl(
        config.out / "book_text.jsonl",
        [
            {
                "page": page["page"],
                "text_normalized": page["normalized_text"],
                "paragraphs": page["paragraphs"],
                "block_count": len(page.get("blocks") or []),
            }
            for page in page_dicts
        ],
    )
    (config.out / "book_text.md").write_text(_book_text_markdown(page_dicts), encoding="utf-8")
    _write_json(config.out / "pages_summary.json", summary)
    return payload


def audit_current_html(config: ChessStudyConfig) -> dict[str, Any]:
    reports_dir = config.out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = audit_chess_html(
        config.pdf,
        config.html or "",
        output=reports_dir / "current_audit.json",
    )
    final_status = "ACCEPTABLE_AS_FINAL"
    if (
        int(report.get("pgn", {}).get("accepted", 0) or 0) <= 0
        or int(report.get("fen", {}).get("total", 0) or 0) <= 0
        or report.get("critical_errors")
    ):
        final_status = "NOT_ACCEPTABLE_AS_FINAL"
    report["final_html_status"] = final_status
    (reports_dir / "current_audit.md").write_text(_current_audit_markdown(report), encoding="utf-8")
    return report


def extract_study_structure(
    pdf_path: str | Path,
    out_dir: str | Path,
    *,
    html_path: str | Path | None = None,
) -> dict[str, Any]:
    pdf = Path(pdf_path)
    out = Path(out_dir)
    pages = _merged_page_texts(pdf, Path(html_path) if html_path else None)
    toc_numbers = _yusupov_toc_numbers_from_html(Path(html_path)) if html_path else []
    toc_structure = _structure_from_toc_numbers(toc_numbers)
    title_hits = toc_structure.get("chapter_starts") or _chapter_title_hits(pages)
    chapters: list[dict[str, Any]] = []
    for index, (chapter_no, title) in enumerate(YUSUPOV_CHAPTERS):
        start_page = title_hits.get(chapter_no)
        next_starts = [
            title_hits[next_no]
            for next_no, _ in YUSUPOV_CHAPTERS[index + 1 :]
            if title_hits.get(next_no) is not None
        ]
        end_page = (min(next_starts) - 1) if start_page is not None and next_starts else None
        chapters.append(
            {
                "chapter_no": chapter_no,
                "title": title,
                "start_book_page": start_page,
                "end_book_page": end_page,
                "expected_exercises": 12,
            }
        )
    final_test_page = toc_structure.get("final_test") or _first_matching_page(pages, FINAL_TEST_RE)
    appendices = toc_structure.get("appendices") or {
        key: _first_matching_page(pages, pattern)
        for key, pattern in APPENDIX_PATTERNS.items()
    }
    page_map = [{"pdf_page_index": item["index"], "book_page_number": item["page_number"]} for item in pages]
    structure = {
        "source_pdf": str(pdf),
        "structure_text_source": "html_toc_assist" if toc_structure else ("pdf+html_ocr_assist" if html_path else "pdf_text"),
        "pdf_page_count": len(pages),
        "chapters": chapters,
        "final_test": {"start_book_page": final_test_page},
        "appendices": appendices,
        "page_map": page_map,
        "validation": _validate_structure(chapters, final_test_page, appendices),
    }
    _write_json(out / "chapters.json", structure)
    return structure


def segment_study_pages(
    pdf_path: str | Path,
    structure: dict[str, Any],
    out_dir: str | Path,
    *,
    html_path: str | Path | None = None,
) -> dict[str, Any]:
    pages = _merged_page_texts(Path(pdf_path), Path(html_path) if html_path else None, include_blocks=True)
    chapters = list(structure.get("chapters") or [])
    final_start = _safe_int((structure.get("final_test") or {}).get("start_book_page"))
    appendices = [value for value in (structure.get("appendices") or {}).values() if _safe_int(value)]
    first_appendix = min([_safe_int(value) for value in appendices] or [0])
    segments: list[dict[str, Any]] = []
    for page in pages:
        book_page = int(page["page_number"])
        text = str(page.get("text") or "")
        chapter = _chapter_for_book_page(book_page, chapters)
        labels = sorted(set(_normalize_ex_label(match) for match in EXERCISE_LABEL_RE.finditer(text)))
        final_labels = sorted(set(f"F-{match.group('number')}" for match in FINAL_LABEL_RE.finditer(text)))
        page_type = _classify_page_type(text, book_page, chapter, final_start=final_start, first_appendix=first_appendix)
        blocks = _segment_blocks_from_page(page, labels=labels, final_labels=final_labels)
        segments.append(
            {
                "page": book_page,
                "pdf_page_index": page["index"],
                "book_page": book_page,
                "chapter_no": chapter.get("chapter_no") if chapter else None,
                "page_type": page_type,
                "exercise_labels": labels,
                "final_test_labels": final_labels,
                "blocks": blocks,
            }
        )
    payload = {"page_count": len(segments), "pages": segments}
    _write_json(Path(out_dir) / "page_segments.json", payload)
    return payload


def detect_study_diagrams(config: ChessStudyConfig) -> dict[str, Any]:
    manual_labels = _load_diagram_review_labels(config.diagram_review_labels)
    manifest = detect_chess_diagrams(
        config.pdf,
        output_dir=config.out,
        dpi=config.diagram_dpi,
        pages=config.diagram_pages,
        page_ranges=config.diagram_page_ranges,
        max_candidates_per_page=config.max_candidates_per_page,
        min_grid_confidence=config.min_grid_confidence,
        include_low_confidence_review_candidates=config.low_confidence_diagram_review,
        low_confidence_min_grid_confidence=config.low_confidence_min_grid_confidence,
        low_confidence_max_candidates_per_page=config.low_confidence_max_candidates_per_page,
        review_sample_limit=config.review_sample_limit,
    )
    source_dir = config.out / "diagrams" / "source"
    crop_dir = config.out / "assets" / "diagram_crops"
    source_dir.mkdir(parents=True, exist_ok=True)
    crop_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[dict[str, Any]] = []
    for item in manifest.get("diagrams", []) or []:
        record = dict(item)
        _apply_diagram_manual_label(record, manual_labels)
        image_path = Path(str(record.get("image_path") or ""))
        target_name = f"{record.get('diagram_id') or image_path.stem}.webp"
        target = source_dir / target_name
        crop_target = crop_dir / target_name
        if image_path.is_file():
            shutil.copyfile(image_path, target)
            shutil.copyfile(image_path, crop_target)
            record["legacy_source_crop"] = str(Path("diagrams") / "source" / target_name).replace("\\", "/")
            record["source_crop"] = str(Path("assets") / "diagram_crops" / target_name).replace("\\", "/")
        else:
            record["source_crop"] = ""
            record["status"] = "needs_review"
            record["reason"] = record.get("reason") or "source_crop_missing"
        rendered = _render_valid_fen_assets(
            str(record.get("fen") or ""),
            config.out,
            diagram_id=str(record.get("diagram_id") or image_path.stem or target_name),
        )
        record["rendered_svg"] = rendered.get("svg", "")
        record["rendered_png"] = rendered.get("png", "")
        record["rendered_diagram"] = record["rendered_svg"] or record["rendered_png"]
        record["visual_order_on_page"] = _visual_order_for_diagram(record, normalized)
        record["review_reason"] = record.get("reason") or ""
        normalized.append(record)
    low_confidence = []
    for item in manifest.get("low_confidence_review_candidates", []) or []:
        record = dict(item)
        _apply_diagram_manual_label(record, manual_labels)
        low_confidence.append(record)
    side_marker_summary = _attach_pdf_side_marker_evidence_to_study_diagrams(
        config.pdf,
        normalized,
        config.out,
        dpi=config.diagram_dpi,
        min_confidence=config.min_grid_confidence,
    )
    for record in normalized:
        rendered = _render_valid_fen_assets(
            str(record.get("fen") or ""),
            config.out,
            diagram_id=str(record.get("diagram_id") or record.get("id") or "diagram"),
        )
        record["rendered_svg"] = rendered.get("svg", "")
        record["rendered_png"] = rendered.get("png", "")
        record["rendered_diagram"] = record["rendered_svg"] or record["rendered_png"]
    if config.diagram_alignment_review or manual_labels:
        alignment_payload = _write_diagram_alignment_review(config.out, [*normalized, *low_confidence])
    else:
        alignment_payload = _empty_diagram_alignment_payload(config.out)
    label_counts = _diagram_manual_label_counts([*normalized, *low_confidence])
    strict_after_review = len([item for item in normalized if item.get("manual_label") != "false_positive"])
    payload = {
        **manifest,
        "diagrams": normalized,
        "low_confidence_review_candidates": low_confidence,
        "manual_label_counts": label_counts,
        "diagram_labels_imported": sum(label_counts.values()),
        "correct_diagrams": label_counts.get("correct_diagram", 0),
        "cropped_diagrams": label_counts.get("cropped_diagram", 0),
        "false_positive_diagrams": label_counts.get("false_positive", 0),
        "uncertain_diagrams": label_counts.get("uncertain", 0),
        "strict_diagram_count_after_review": strict_after_review,
        "alignment_review": alignment_payload,
        "alignment_improved_count": int(alignment_payload.get("alignment_improved_count") or 0),
        "source_crop_dir": str(source_dir),
        "diagram_crop_dir": str(crop_dir),
        "rendered_svg_dir": str(config.out / "assets" / "diagram_svg"),
        "rendered_png_dir": str(config.out / "assets" / "diagram_png"),
        "side_marker_summary": side_marker_summary,
    }
    _write_json(config.out / "chess_diagrams.json", payload)
    _write_jsonl(config.out / "diagrams.jsonl", [_study_diagram_record(record).to_dict() for record in normalized])
    _write_csv(config.out / "diagrams.csv", [_study_diagram_record(record).to_dict() for record in normalized])
    return payload


def _attach_pdf_side_marker_evidence_to_study_diagrams(
    pdf_path: str | Path,
    diagrams: list[dict[str, Any]],
    out_dir: str | Path,
    *,
    dpi: int,
    min_confidence: float,
) -> dict[str, Any]:
    out = Path(out_dir)
    if not diagrams:
        summary = _study_side_marker_summary([])
        _write_study_side_marker_report(out, [], summary)
        _write_study_two_crop_quality_metrics(out, [], summary)
        _write_study_side_marker_blocker_attribution(out, [], source_gate=None)
        return summary
    if not Path(pdf_path).is_file():
        summary = {**_study_side_marker_summary(diagrams), "status": "pdf_source_missing"}
        _write_study_side_marker_report(out, diagrams, summary)
        _write_study_two_crop_quality_metrics(out, diagrams, summary)
        _write_study_side_marker_blocker_attribution(out, diagrams, source_gate=None)
        return summary
    diagrams_by_page: dict[int, list[dict[str, Any]]] = {}
    for diagram in diagrams:
        page_number = int(diagram.get("page") or 0)
        if page_number > 0:
            diagrams_by_page.setdefault(page_number, []).append(diagram)

    with fitz.open(pdf_path) as document:
        zoom = max(72, int(dpi or 72)) / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_number, page_diagrams in diagrams_by_page.items():
            page_index = page_number - 1
            if page_index < 0 or page_index >= len(document):
                continue
            pixmap = document[page_index].get_pixmap(matrix=matrix, alpha=False)
            page_image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            page_bboxes = [
                bbox
                for bbox in (_study_pixel_bbox_xyxy(diagram) for diagram in page_diagrams)
                if bbox is not None
            ]
            for diagram in page_diagrams:
                board_bbox = _study_pixel_bbox_xyxy(diagram)
                if board_bbox is None:
                    continue
                payload = _study_side_marker_payload(diagram)
                evidence = _infer_scan_chess_side_to_move_marker_evidence(page_image, board_bbox)
                payload = _apply_scan_chess_side_to_move_context_evidence(
                    payload,
                    evidence,
                    min_confidence=min_confidence,
                )
                if bool(payload.get("requires_review")) and "side_to_move_inferred" in {
                    str(warning) for warning in list(payload.get("warnings") or [])
                }:
                    local_evidence = _scan_chess_local_side_marker_assignment_evidence(
                        page_image,
                        board_bbox,
                        payload,
                        diagram_bboxes=page_bboxes,
                    )
                    payload = _apply_scan_chess_side_to_move_context_evidence(
                        payload,
                        local_evidence,
                        min_confidence=min_confidence,
                    )
                two_crop_fields, two_crop_files = _scan_chess_two_crop_review_artifacts(
                    page_image,
                    filename=f"{diagram.get('diagram_id') or diagram.get('id') or 'diagram'}.png",
                    board_bbox=board_bbox,
                    side_marker_bbox=payload.get("side_marker_bbox"),
                )
                payload.update(two_crop_fields)
                payload = _apply_scan_chess_two_crop_quality_gate(payload, two_crop_fields)
                payload = _apply_scan_chess_two_crop_side_marker_if_trusted(
                    payload,
                    two_crop_fields,
                    min_confidence=min_confidence,
                )
                payload.update(two_crop_fields)
                _write_study_side_marker_artifact_files(out, two_crop_files)
                _apply_study_side_marker_payload(diagram, payload)

    summary = _study_side_marker_summary(diagrams)
    _write_study_side_marker_report(out, diagrams, summary)
    _write_study_two_crop_quality_metrics(out, diagrams, summary)
    _write_study_side_marker_blocker_attribution(out, diagrams, source_gate=None)
    return summary


def _study_pixel_bbox_xyxy(diagram: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    raw = diagram.get("pixel_bbox")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            x0, y0, width, height = [float(value) for value in raw]
        except (TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return (x0, y0, x0 + width, y0 + height)
    raw_xyxy = diagram.get("pixel_bbox_xyxy") or diagram.get("board_bbox")
    if isinstance(raw_xyxy, (list, tuple)) and len(raw_xyxy) == 4:
        try:
            x0, y0, x1, y1 = [float(value) for value in raw_xyxy]
        except (TypeError, ValueError):
            return None
        if x1 > x0 and y1 > y0:
            return (x0, y0, x1, y1)
    return None


def _study_side_marker_payload(diagram: Mapping[str, Any]) -> dict[str, Any]:
    warnings = [str(warning) for warning in diagram.get("warnings") or [] if str(warning)]
    full_fen = str(diagram.get("full_fen") or diagram.get("fen_candidate") or diagram.get("fen") or "").strip()
    placement = str(diagram.get("placement") or diagram.get("placement_fen") or "").strip()
    if not placement and full_fen:
        placement = full_fen.split()[0]
    side = str(diagram.get("side_to_move") or "").strip().lower()
    side_status = str(diagram.get("side_to_move_status") or "").strip().lower()
    side_evidence = str(diagram.get("side_to_move_evidence") or "").strip().lower()
    trusted_side = side in {"w", "b"} and side_status == "explicit" and side_evidence in {
        "marker",
        "caption",
        "verified_label",
        "exact_label",
    }
    if not trusted_side:
        side = side if side in {"w", "b"} else "unknown"
        side_status = "inferred" if side in {"w", "b"} else "unknown"
        side_evidence = "inferred"
        if "side_to_move_inferred" not in warnings:
            warnings.append("side_to_move_inferred")
    return {
        "fen": str(diagram.get("fen") or "").strip() if trusted_side else "",
        "full_fen": full_fen,
        "placement": placement,
        "placement_fen": placement,
        "confidence": float(diagram.get("fen_confidence") or diagram.get("confidence") or 0.0),
        "source": str(diagram.get("source") or "image-template-board"),
        "method": str(diagram.get("method") or "study-diagram-detector"),
        "side_to_move": side,
        "side_to_move_status": side_status,
        "side_to_move_evidence": side_evidence,
        "warnings": sorted(set(warnings)),
        "requires_review": not trusted_side,
        "board_detected": True,
    }


def _apply_study_side_marker_payload(diagram: dict[str, Any], payload: Mapping[str, Any]) -> None:
    marker = _scan_chess_side_marker_metadata_from_payload(payload)
    fen = str(payload.get("fen") or "").strip()
    full_fen = str(payload.get("full_fen") or "").strip()
    requires_review = bool(payload.get("requires_review", True))
    diagram.update(
        {
            "fen": fen if not requires_review else "",
            "fen_candidate": full_fen or str(diagram.get("fen_candidate") or ""),
            "full_fen": full_fen,
            "placement": str(payload.get("placement") or payload.get("placement_fen") or ""),
            "placement_fen": str(payload.get("placement") or payload.get("placement_fen") or ""),
            "placement_status": str(payload.get("placement_status") or payload.get("placement_runtime_status") or ""),
            "full_fen_status": str(payload.get("full_fen_status") or payload.get("full_fen_runtime_status") or ""),
            "fen_suppressed_reason": str(payload.get("fen_suppressed_reason") or ""),
            "side_to_move": marker.get("side_to_move") or payload.get("side_to_move") or "unknown",
            "side_to_move_status": str(payload.get("side_to_move_status") or ""),
            "side_to_move_evidence": str(payload.get("side_to_move_evidence") or ""),
            "side_marker_symbol": marker.get("side_marker_symbol") or "",
            "side_marker_status": marker.get("side_marker_status") or "",
            "side_marker_source": marker.get("side_marker_source") or "",
            "side_marker_bbox": marker.get("side_marker_bbox") or [],
            "side_marker_confidence": marker.get("side_marker_confidence") or "",
            "side_marker_assignment_trace": marker.get("side_marker_assignment_trace") or {},
            "strict_fen_side_evidence_trusted": bool(marker.get("strict_fen_side_evidence_trusted")),
            "board_crop_path": str(payload.get("board_crop_path") or diagram.get("source_crop") or ""),
            "side_marker_crop_path": str(payload.get("side_marker_crop_path") or ""),
            "side_marker_search_crop_path": str(payload.get("side_marker_search_crop_path") or ""),
            "marker_search_zone_preview_path": str(payload.get("marker_search_zone_preview_path") or ""),
            "marker_search_zone_preview_bbox": list(payload.get("marker_search_zone_preview_bbox") or []),
            "side_marker_review_crop_path": str(payload.get("side_marker_review_crop_path") or ""),
            "side_marker_review_crop_kind": str(payload.get("side_marker_review_crop_kind") or ""),
            "debug_overlay_path": str(payload.get("debug_overlay_path") or ""),
            "board_bbox": list(payload.get("board_bbox") or []),
            "board_crop_quality": str(payload.get("board_crop_quality") or ""),
            "board_crop_fail_reason": list(payload.get("board_crop_fail_reason") or []),
            "board_crop_quality_gate": dict(payload.get("board_crop_quality_gate") or {}),
            "marker_search_zones": dict(payload.get("marker_search_zones") or {}),
            "selected_marker_zone": payload.get("selected_marker_zone"),
            "marker_bbox": list(payload.get("marker_bbox") or []),
            "marker_crop_bbox": list(payload.get("marker_crop_bbox") or []),
            "marker_crop_quality": str(payload.get("marker_crop_quality") or ""),
            "marker_crop_fail_reason": list(payload.get("marker_crop_fail_reason") or []),
            "marker_crop_quality_gate": dict(payload.get("marker_crop_quality_gate") or {}),
            "side_to_move_detected": payload.get("side_to_move_detected"),
            "side_to_move_confidence": payload.get("side_to_move_confidence"),
            "manual_review_required": bool(payload.get("manual_review_required", True)),
            "manual_review_reason": str(payload.get("manual_review_reason") or ""),
            "warnings": sorted({str(warning) for warning in payload.get("warnings") or [] if str(warning)}),
        }
    )
    if fen and not requires_review:
        diagram["status"] = "accepted"
        diagram["reason"] = None
        diagram["review_reason"] = ""
    else:
        diagram["status"] = "needs_review"
        diagram["reason"] = str(payload.get("fen_suppressed_reason") or "side_marker_or_fen_requires_review")
        diagram["review_reason"] = diagram["reason"]


def _write_study_side_marker_artifact_files(out: Path, files: list[Mapping[str, Any]]) -> None:
    for item in files:
        rel_path = str(item.get("path") or "").strip()
        data = item.get("data")
        if not rel_path or not isinstance(data, (bytes, bytearray)):
            continue
        target = out / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))


def _study_side_marker_summary(diagrams: list[Mapping[str, Any]]) -> dict[str, Any]:
    diagram_count = len(diagrams)
    board_pass = len([item for item in diagrams if str(item.get("board_crop_quality") or "") == "pass"])
    marker_pass = len([item for item in diagrams if str(item.get("marker_crop_quality") or "") == "pass"])
    marker_fail = len([item for item in diagrams if str(item.get("marker_crop_quality") or "") == "fail"])
    marker_fail_reasons = [
        str(reason)
        for item in diagrams
        for reason in (item.get("marker_crop_fail_reason") or [])
        if str(reason)
    ]
    board_fail_reasons = [
        str(reason)
        for item in diagrams
        for reason in (item.get("board_crop_fail_reason") or [])
        if str(reason)
    ]
    board_fail = len([item for item in diagrams if str(item.get("board_crop_quality") or "") == "fail"])
    board_reason_breakdown = {reason: board_fail_reasons.count(reason) for reason in sorted(set(board_fail_reasons))}
    return {
        "diagram_count": diagram_count,
        "board_crop_count": len([item for item in diagrams if str(item.get("board_crop_path") or "").strip()]),
        "board_crop_pass_count": board_pass,
        "board_crop_fail_count": board_fail,
        "board_crop_fail_reason_breakdown": board_reason_breakdown,
        "side_marker_crop_count": len([item for item in diagrams if str(item.get("side_marker_crop_path") or "").strip()]),
        "side_marker_search_crop_count": len([item for item in diagrams if str(item.get("side_marker_search_crop_path") or "").strip()]),
        "marker_search_zone_count": len([item for item in diagrams if item.get("marker_search_zones")]),
        "marker_search_zone_region_count": sum(len(item.get("marker_search_zones") or {}) for item in diagrams),
        "marker_bbox_count": len([item for item in diagrams if item.get("marker_bbox")]),
        "marker_crop_pass_count": marker_pass,
        "marker_crop_fail_count": marker_fail,
        "board_crop_quality_pass_count": board_pass,
        "board_crop_quality_pass_rate": round(board_pass / diagram_count, 4) if diagram_count else 0.0,
        "board_crop_contains_coordinates_count": board_fail_reasons.count("contains_coordinates"),
        "board_crop_contains_marker_count": board_fail_reasons.count("contains_marker"),
        "marker_crop_quality_pass_count": marker_pass,
        "marker_crop_quality_pass_rate": round(marker_pass / diagram_count, 4) if diagram_count else 0.0,
        "marker_crop_missing_count": marker_fail_reasons.count("marker_missing"),
        "marker_crop_cut_off_count": marker_fail_reasons.count("marker_cut_off"),
        "marker_crop_mostly_board_edge_count": marker_fail_reasons.count("mostly_board_edge"),
        "marker_crop_mostly_coordinates_count": marker_fail_reasons.count("mostly_rank_numbers")
        + marker_fail_reasons.count("mostly_file_letters"),
        "trusted_marker_count": len([item for item in diagrams if str(item.get("side_marker_status") or "") == "trusted_marker"]),
        "marker_missing_count": len([item for item in diagrams if str(item.get("side_marker_status") or "") in {"", "marker_missing", "inferred_only"}]),
        "marker_conflict_count": len([item for item in diagrams if str(item.get("side_marker_status") or "") in {"marker_conflict", "multi_side"}]),
        "side_unknown_count": len([item for item in diagrams if str(item.get("side_to_move") or "") not in {"w", "b"}]),
        "side_to_move_auto_confident_rate": round(
            len([item for item in diagrams if str(item.get("side_marker_status") or "") == "trusted_marker"]) / diagram_count,
            4,
        )
        if diagram_count
        else 0.0,
        "side_to_move_manual_review_rate": round(
            len([item for item in diagrams if bool(item.get("manual_review_required", True))]) / diagram_count,
            4,
        )
        if diagram_count
        else 0.0,
        "side_to_move_auto_vs_manual_accuracy": None,
        "placement_accepted_count": len(
            [
                item
                for item in diagrams
                if str(item.get("placement_status") or item.get("placement_runtime_status") or "").startswith("FEN_PLACEMENT_MACHINE_ACCEPTED")
                or str(item.get("placement") or item.get("placement_fen") or "").strip()
            ]
        ),
        "full_fen_accepted_count": len([item for item in diagrams if str(item.get("status") or "") == "accepted" and str(item.get("fen") or "").strip()]),
    }


def _write_study_side_marker_report(out: Path, diagrams: list[Mapping[str, Any]], summary: Mapping[str, Any]) -> None:
    reports_dir = out / "reports" / "chess_fen"
    reports_dir.mkdir(parents=True, exist_ok=True)
    items = [
        {
            "diagram_id": item.get("diagram_id") or item.get("id") or "",
            "page": item.get("page"),
            "side_to_move": item.get("side_to_move"),
            "side_marker_symbol": item.get("side_marker_symbol"),
            "side_marker_status": item.get("side_marker_status"),
            "side_marker_confidence": item.get("side_marker_confidence"),
            "board_crop_path": item.get("board_crop_path"),
            "side_marker_crop_path": item.get("side_marker_crop_path"),
            "side_marker_search_crop_path": item.get("side_marker_search_crop_path"),
            "marker_search_zone_preview_path": item.get("marker_search_zone_preview_path"),
            "marker_search_zone_preview_bbox": item.get("marker_search_zone_preview_bbox") or [],
            "side_marker_review_crop_path": item.get("side_marker_review_crop_path"),
            "side_marker_review_crop_kind": item.get("side_marker_review_crop_kind"),
            "debug_overlay_path": item.get("debug_overlay_path"),
            "board_bbox": item.get("board_bbox"),
            "side_marker_bbox": item.get("side_marker_bbox"),
            "marker_bbox": item.get("marker_bbox"),
            "marker_crop_bbox": item.get("marker_crop_bbox"),
            "marker_search_zones": item.get("marker_search_zones") or {},
            "selected_marker_zone": item.get("selected_marker_zone"),
            "board_crop_quality": item.get("board_crop_quality"),
            "board_crop_fail_reason": item.get("board_crop_fail_reason") or [],
            "marker_crop_quality": item.get("marker_crop_quality"),
            "marker_crop_fail_reason": item.get("marker_crop_fail_reason") or [],
            "side_to_move_detected": item.get("side_to_move_detected"),
            "side_to_move_confidence": item.get("side_to_move_confidence"),
            "manual_review_required": bool(item.get("manual_review_required", True)),
            "manual_review_reason": item.get("manual_review_reason"),
            "warnings": item.get("warnings") or [],
        }
        for item in diagrams
    ]
    payload = {
        "schema": "kindlemaster.chess_study.pdf_side_marker_assignment.v1",
        "summary": dict(summary),
        "items": items,
    }
    _write_json(reports_dir / "side_marker_assignment.json", payload)
    lines = [
        "# Side Marker Assignment",
        "",
        *[f"- {key}: `{value}`" for key, value in summary.items()],
        "",
        "## Samples",
        "",
    ]
    for item in items[:40]:
        lines.append(
            f"- `{item.get('diagram_id')}` page `{item.get('page')}` "
            f"status `{item.get('side_marker_status')}` side `{item.get('side_to_move')}` "
            f"crop `{item.get('side_marker_crop_path') or ''}`"
        )
    (reports_dir / "side_marker_assignment.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    html_lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>Side Marker Assignment</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:24px;color:#111827}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d1d5db;padding:6px 8px;text-align:left}th{background:#f3f4f6}</style>",
        "</head>",
        "<body>",
        "<h1>Side Marker Assignment</h1>",
        "<dl>",
    ]
    for key, value in summary.items():
        html_lines.append(f"<dt>{html.escape(str(key))}</dt><dd>{html.escape(str(value))}</dd>")
    html_lines.extend(
        [
            "</dl>",
            "<table>",
            "<thead><tr><th>Diagram</th><th>Page</th><th>Status</th><th>Side</th><th>Board quality</th><th>Marker quality</th><th>Marker crop</th><th>Search preview</th><th>Review reason</th></tr></thead>",
            "<tbody>",
        ]
    )
    for item in items:
        html_lines.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('diagram_id') or ''))}</td>"
            f"<td>{html.escape(str(item.get('page') or ''))}</td>"
            f"<td>{html.escape(str(item.get('side_marker_status') or ''))}</td>"
            f"<td>{html.escape(str(item.get('side_to_move') or ''))}</td>"
            f"<td>{html.escape(str(item.get('board_crop_quality') or ''))}</td>"
            f"<td>{html.escape(str(item.get('marker_crop_quality') or ''))}</td>"
            f"<td>{html.escape(str(item.get('side_marker_crop_path') or ''))}</td>"
            f"<td>{html.escape(str(item.get('side_marker_search_crop_path') or ''))}</td>"
            f"<td>{html.escape(str(item.get('manual_review_reason') or ''))}</td>"
            "</tr>"
        )
    html_lines.extend(["</tbody>", "</table>", "</body>", "</html>"])
    (reports_dir / "side_marker_assignment.html").write_text("\n".join(html_lines) + "\n", encoding="utf-8")


def _study_two_crop_quality_rows(diagrams: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in diagrams:
        side_status = str(item.get("side_marker_status") or "")
        placement = str(item.get("placement") or item.get("placement_fen") or "").strip()
        placement_status = str(item.get("placement_status") or item.get("placement_runtime_status") or "")
        placement_accepted = placement_status.startswith("FEN_PLACEMENT_MACHINE_ACCEPTED") or bool(placement)
        full_fen_status = str(item.get("full_fen_status") or item.get("full_fen_runtime_status") or "")
        full_fen_accepted = str(item.get("status") or "") == "accepted" and bool(str(item.get("fen") or "").strip())
        trusted_marker = side_status == "trusted_marker"
        marker_conflict = side_status in {"marker_conflict", "multi_side"}
        marker_missing = side_status in {"", "marker_missing", "inferred_only"}
        rows.append(
            {
                "diagram_id": item.get("diagram_id") or item.get("id") or "",
                "page": item.get("page"),
                "has_board_crop": bool(str(item.get("board_crop_path") or "").strip()),
                "has_side_marker_crop": bool(str(item.get("side_marker_crop_path") or "").strip()),
                "has_side_marker_search_crop": bool(str(item.get("side_marker_search_crop_path") or "").strip()),
                "side_marker_search_crop_path": str(item.get("side_marker_search_crop_path") or ""),
                "marker_search_zone_preview_path": str(item.get("marker_search_zone_preview_path") or ""),
                "marker_search_zone_preview_bbox": list(item.get("marker_search_zone_preview_bbox") or []),
                "side_marker_review_crop_path": str(item.get("side_marker_review_crop_path") or ""),
                "side_marker_review_crop_kind": str(item.get("side_marker_review_crop_kind") or ""),
                "debug_overlay_path": str(item.get("debug_overlay_path") or ""),
                "debug_context_crop_path": str(item.get("debug_context_crop_path") or ""),
                "raw_board_candidate_bbox": list(item.get("raw_board_candidate_bbox") or []),
                "tight_board_bbox": list(item.get("tight_board_bbox") or []),
                "board_bbox": list(item.get("board_bbox") or []),
                "board_crop_quality": str(item.get("board_crop_quality") or ""),
                "board_crop_fail_reason": list(item.get("board_crop_fail_reason") or []),
                "marker_search_zones": dict(item.get("marker_search_zones") or {}),
                "selected_marker_zone": item.get("selected_marker_zone"),
                "marker_bbox": list(item.get("marker_bbox") or []),
                "marker_crop_bbox": list(item.get("marker_crop_bbox") or []),
                "marker_crop_quality": str(item.get("marker_crop_quality") or ""),
                "marker_crop_fail_reason": list(item.get("marker_crop_fail_reason") or []),
                "side_to_move_detected": item.get("side_to_move_detected"),
                "side_to_move_confidence": item.get("side_to_move_confidence"),
                "manual_review_required": bool(item.get("manual_review_required", True)),
                "manual_review_reason": str(item.get("manual_review_reason") or ""),
                "side_marker_status": side_status or "marker_missing",
                "side_marker_symbol": str(item.get("side_marker_symbol") or ""),
                "side_to_move": str(item.get("side_to_move") or "unknown"),
                "trusted_marker": trusted_marker,
                "marker_missing": marker_missing,
                "marker_conflict": marker_conflict,
                "placement_status": placement_status or ("FEN_PLACEMENT_MACHINE_ACCEPTED" if placement_accepted else "FEN_PLACEMENT_REVIEW_REQUIRED"),
                "full_fen_status": full_fen_status or ("FEN_MACHINE_ACCEPTED" if full_fen_accepted else "FEN_REVIEW_REQUIRED"),
                "blocked_by_marker": not trusted_marker,
                "blocked_by_placement": not placement_accepted,
            }
        )
    return rows


def _study_two_crop_accuracy_gap(diagrams: list[Mapping[str, Any]]) -> dict[str, Any]:
    verified = [
        item
        for item in diagrams
        if str(item.get("label_source") or item.get("verification_status") or "").lower() in {"human_verified", "verified"}
    ]
    marker_label_count = len([item for item in verified if str(item.get("expected_side_to_move") or "").lower() in {"w", "b", "white", "black"}])
    placement_label_count = len([item for item in verified if str(item.get("expected_placement") or "").strip()])
    both_label_count = len(
        [
            item
            for item in verified
            if str(item.get("expected_side_to_move") or "").lower() in {"w", "b", "white", "black"}
            and str(item.get("expected_placement") or "").strip()
        ]
    )
    missing_data = []
    if marker_label_count < 30:
        missing_data.append({"field": "expected_side_to_move", "needed": "human_verified side-to-move labels", "available": marker_label_count})
    if placement_label_count < 30:
        missing_data.append({"field": "expected_placement", "needed": "human_verified board placement labels", "available": placement_label_count})
    status = "TRAINING_DATA_GAP" if missing_data else "READY"
    return {
        "status": status,
        "message": "TRAINING_DATA_GAP: accuracy requires human-verified side-to-move and placement labels." if missing_data else "Human-verified labels are available for accuracy measurement.",
        "human_verified_record_count": len(verified),
        "marker_label_count": marker_label_count,
        "placement_label_count": placement_label_count,
        "both_label_count": both_label_count,
        "missing_data": missing_data,
    }


def _study_side_marker_probe_before_after(summary: Mapping[str, Any]) -> dict[str, Any]:
    after = {
        "marker_missing_count": int(summary.get("marker_missing_count") or 0),
        "marker_conflict_count": int(summary.get("marker_conflict_count") or 0),
        "trusted_marker_count": int(summary.get("trusted_marker_count") or 0),
        "full_fen_accepted_count": int(summary.get("full_fen_accepted_count") or 0),
    }
    before = {
        "marker_missing_count": summary.get("baseline_marker_missing_count"),
        "marker_conflict_count": summary.get("baseline_marker_conflict_count"),
        "trusted_marker_count": summary.get("baseline_trusted_marker_count"),
        "full_fen_accepted_count": summary.get("baseline_full_fen_accepted_count"),
    }
    has_baseline = all(value is not None for value in before.values())
    if not has_baseline:
        return {
            "status": "TRAINING_DATA_GAP",
            "message": "TRAINING_DATA_GAP: side-marker probe before/after requires matched baseline fixture counts.",
            "before": before,
            "after": after,
            "improvement": {
                "trusted_marker_count_delta": None,
                "marker_missing_count_delta": None,
                "marker_conflict_count_delta": None,
                "full_fen_accepted_count_delta": None,
            },
        }
    before_int = {key: int(value or 0) for key, value in before.items()}
    improvement = {
        "trusted_marker_count_delta": after["trusted_marker_count"] - before_int["trusted_marker_count"],
        "marker_missing_count_delta": after["marker_missing_count"] - before_int["marker_missing_count"],
        "marker_conflict_count_delta": after["marker_conflict_count"] - before_int["marker_conflict_count"],
        "full_fen_accepted_count_delta": after["full_fen_accepted_count"] - before_int["full_fen_accepted_count"],
    }
    status = (
        "improved"
        if improvement["trusted_marker_count_delta"] > 0
        or improvement["marker_missing_count_delta"] < 0
        or improvement["marker_conflict_count_delta"] < 0
        else "unchanged"
    )
    return {
        "status": status,
        "message": "Side-marker probe before/after counts are available.",
        "before": before_int,
        "after": after,
        "improvement": improvement,
    }


def _write_study_two_crop_quality_metrics(out: Path, diagrams: list[Mapping[str, Any]], summary: Mapping[str, Any]) -> None:
    reports_dir = out / "reports" / "chess_fen"
    reports_dir.mkdir(parents=True, exist_ok=True)
    rows = _study_two_crop_quality_rows(diagrams)
    payload = {
        "schema": "kindlemaster.chess_fen.two_crop_quality_metrics.v1",
        "summary": {
            "diagram_count": len(rows),
            "board_crop_count": int(summary.get("board_crop_count") or 0),
            "board_crop_pass_count": int(summary.get("board_crop_pass_count") or summary.get("board_crop_quality_pass_count") or 0),
            "board_crop_fail_count": int(summary.get("board_crop_fail_count") or 0),
            "board_crop_fail_reason_breakdown": dict(summary.get("board_crop_fail_reason_breakdown") or {}),
            "side_marker_crop_count": int(summary.get("side_marker_crop_count") or 0),
            "side_marker_search_crop_count": int(summary.get("side_marker_search_crop_count") or 0),
            "marker_search_zone_count": int(summary.get("marker_search_zone_count") or 0),
            "marker_search_zone_region_count": int(summary.get("marker_search_zone_region_count") or 0),
            "marker_bbox_count": int(summary.get("marker_bbox_count") or 0),
            "marker_crop_pass_count": int(summary.get("marker_crop_pass_count") or summary.get("marker_crop_quality_pass_count") or 0),
            "marker_crop_fail_count": int(summary.get("marker_crop_fail_count") or 0),
            "board_crop_quality_pass_count": int(summary.get("board_crop_quality_pass_count") or 0),
            "board_crop_quality_pass_rate": summary.get("board_crop_quality_pass_rate", 0.0),
            "board_crop_contains_coordinates_count": int(summary.get("board_crop_contains_coordinates_count") or 0),
            "board_crop_contains_marker_count": int(summary.get("board_crop_contains_marker_count") or 0),
            "marker_crop_quality_pass_count": int(summary.get("marker_crop_quality_pass_count") or 0),
            "marker_crop_quality_pass_rate": summary.get("marker_crop_quality_pass_rate", 0.0),
            "marker_crop_missing_count": int(summary.get("marker_crop_missing_count") or 0),
            "marker_crop_cut_off_count": int(summary.get("marker_crop_cut_off_count") or 0),
            "marker_crop_mostly_board_edge_count": int(summary.get("marker_crop_mostly_board_edge_count") or 0),
            "marker_crop_mostly_coordinates_count": int(summary.get("marker_crop_mostly_coordinates_count") or 0),
            "side_to_move_auto_confident_rate": summary.get("side_to_move_auto_confident_rate", 0.0),
            "side_to_move_manual_review_rate": summary.get("side_to_move_manual_review_rate", 0.0),
            "side_to_move_auto_vs_manual_accuracy": summary.get("side_to_move_auto_vs_manual_accuracy"),
            "trusted_marker_count": int(summary.get("trusted_marker_count") or 0),
            "marker_missing_count": int(summary.get("marker_missing_count") or 0),
            "marker_conflict_count": int(summary.get("marker_conflict_count") or 0),
            "side_unknown_count": int(summary.get("side_unknown_count") or 0),
            "placement_accepted_count": int(summary.get("placement_accepted_count") or 0),
            "full_fen_accepted_count": int(summary.get("full_fen_accepted_count") or 0),
            "blocked_by_marker_count": len([row for row in rows if row.get("blocked_by_marker")]),
            "blocked_by_placement_count": len([row for row in rows if row.get("blocked_by_placement")]),
        },
        "probe_quality_before_after": _study_side_marker_probe_before_after(summary),
        "accuracy": _study_two_crop_accuracy_gap(diagrams),
        "items": rows,
    }
    _write_json(reports_dir / "two_crop_quality_metrics.json", payload)
    accuracy = payload["accuracy"]
    lines = [
        "# Chess FEN Two-Crop Quality Metrics",
        "",
        *[f"- {key}: `{value}`" for key, value in payload["summary"].items()],
        "",
        "## Accuracy",
        "",
        f"- status: `{accuracy.get('status')}`",
        f"- human verified records: `{accuracy.get('human_verified_record_count')}`",
        f"- side-to-move labels: `{accuracy.get('marker_label_count')}`",
        f"- placement labels: `{accuracy.get('placement_label_count')}`",
    ]
    probe_quality = payload["probe_quality_before_after"]
    lines.extend(
        [
            "",
            "## Probe Before/After",
            "",
            f"- status: `{probe_quality.get('status')}`",
            f"- message: {probe_quality.get('message')}",
            f"- after marker missing: `{(probe_quality.get('after') or {}).get('marker_missing_count')}`",
            f"- after marker conflict: `{(probe_quality.get('after') or {}).get('marker_conflict_count')}`",
            f"- after trusted marker: `{(probe_quality.get('after') or {}).get('trusted_marker_count')}`",
            f"- after full FEN accepted: `{(probe_quality.get('after') or {}).get('full_fen_accepted_count')}`",
        ]
    )
    if accuracy.get("status") == "TRAINING_DATA_GAP":
        lines.extend(["", f"TRAINING_DATA_GAP: {accuracy.get('message')}", ""])
        lines.extend(["| Missing field | Needed | Available |", "| --- | --- | ---: |"])
        for item in accuracy.get("missing_data") or []:
            lines.append(f"| {item.get('field')} | {item.get('needed')} | {item.get('available')} |")
    lines.extend(
        [
            "",
            "| Diagram | Page | Board crop | Marker crop | Search crop | Board quality | Marker quality | Review crop | Marker status | Placement | Full FEN | Marker block | Placement block |",
            "| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows[:100]:
        lines.append(
            "| {diagram} | {page} | {board} | {marker} | {search} | {board_quality} | {marker_quality} | {review} | {status} | {placement} | {full} | {marker_block} | {placement_block} |".format(
                diagram=row.get("diagram_id") or "",
                page=row.get("page") or "",
                board="yes" if row.get("has_board_crop") else "no",
                marker="yes" if row.get("has_side_marker_crop") else "no",
                search="yes" if row.get("has_side_marker_search_crop") else "no",
                board_quality=row.get("board_crop_quality") or "",
                marker_quality=row.get("marker_crop_quality") or "",
                review=row.get("side_marker_review_crop_kind") or "",
                status=row.get("side_marker_status") or "",
                placement=row.get("placement_status") or "",
                full=row.get("full_fen_status") or "",
                marker_block="yes" if row.get("blocked_by_marker") else "no",
                placement_block="yes" if row.get("blocked_by_placement") else "no",
            )
        )
    (reports_dir / "two_crop_quality_metrics.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_study_side_marker_blocker_attribution(
    out: Path,
    diagrams: list[Mapping[str, Any]],
    *,
    source_gate: Mapping[str, Any] | None,
) -> None:
    reports_dir = out / "reports" / "chess_fen"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = build_side_marker_blocker_attribution(diagrams, source_gate=source_gate)
    _write_json(reports_dir / "side_marker_blocker_attribution.json", report)
    (reports_dir / "side_marker_blocker_attribution.md").write_text(
        side_marker_blocker_attribution_markdown(report),
        encoding="utf-8",
    )


def _study_side_to_move_label(value: Any) -> str:
    side = str(value or "").strip().lower()
    if side in {"w", "white"}:
        return "white"
    if side in {"b", "black"}:
        return "black"
    return "unknown"


def build_study_positions(diagrams: dict[str, Any], segments: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    pages_by_number = {int(page.get("page") or 0): page for page in segments.get("pages", []) or []}
    positions: list[dict[str, Any]] = []
    for index, diagram in enumerate(diagrams.get("diagrams", []) or [], start=1):
        if diagram.get("manual_label") == "false_positive":
            continue
        page_number = int(diagram.get("page") or 0)
        page = pages_by_number.get(page_number) or {}
        label = _best_label_for_diagram(diagram, page, fallback_index=index)
        chapter_no = page.get("chapter_no")
        item_type = "final_test" if str(label).startswith("F-") or page.get("page_type") == "final_test" else "exercise"
        fen = str(diagram.get("fen") or "").strip()
        status, warnings = _position_status_from_diagram(diagram, fen=fen)
        position_id = _position_id(item_type=item_type, chapter_no=chapter_no, label=label, fallback_index=index)
        positions.append(
            {
                "id": position_id,
                "type": item_type,
                "chapter_no": chapter_no,
                "chapter_title": _chapter_title(chapter_no),
                "label": label,
                "diagram_page": page_number,
                "solution_page": None,
                "side_to_move": _study_side_to_move_label(diagram.get("side_to_move")),
                "side_to_move_code": str(diagram.get("side_to_move") or "unknown"),
                "side_to_move_status": str(diagram.get("side_to_move_status") or ""),
                "side_to_move_evidence": str(diagram.get("side_to_move_evidence") or ""),
                "side_marker_symbol": str(diagram.get("side_marker_symbol") or ""),
                "side_marker_status": str(diagram.get("side_marker_status") or ""),
                "side_marker_source": str(diagram.get("side_marker_source") or ""),
                "side_marker_bbox": _bbox4(diagram.get("side_marker_bbox") or []),
                "side_marker_confidence": diagram.get("side_marker_confidence", ""),
                "side_marker_crop_path": str(diagram.get("side_marker_crop_path") or ""),
                "side_marker_assignment_trace": dict(diagram.get("side_marker_assignment_trace") or {}),
                "strict_fen_side_evidence_trusted": bool(diagram.get("strict_fen_side_evidence_trusted")),
                "bbox": _bbox4(diagram.get("bbox") or []),
                "board_bbox": _bbox4(diagram.get("board_bbox") or diagram.get("bbox_xyxy") or []),
                "board_crop_path": str(diagram.get("board_crop_path") or diagram.get("source_crop") or ""),
                "debug_overlay_path": str(diagram.get("debug_overlay_path") or ""),
                "visual_order_on_page": diagram.get("visual_order_on_page"),
                "stars": None,
                "fen": fen,
                "fen_candidate": str(diagram.get("fen_candidate") or ""),
                "placement": str(diagram.get("placement") or diagram.get("placement_fen") or ""),
                "placement_status": str(diagram.get("placement_status") or ""),
                "full_fen": str(diagram.get("full_fen") or ""),
                "full_fen_status": str(diagram.get("full_fen_status") or ""),
                "fen_suppressed_reason": str(diagram.get("fen_suppressed_reason") or ""),
                "solution_pgn": "",
                "points": None,
                "theme": "",
                "status": status,
                "warnings": warnings,
                "critical_warnings": _critical_warnings(status, warnings),
                "source_crop": diagram.get("source_crop") or "",
                "rendered_diagram": diagram.get("rendered_diagram") or "",
                "rendered_svg": diagram.get("rendered_svg") or "",
                "rendered_png": diagram.get("rendered_png") or "",
                "diagram_confidence": diagram.get("confidence"),
                "fen_confidence": diagram.get("fen_confidence"),
                "validation_status": "validated" if status == "accepted" else "not_validated",
                "ai_candidate": None,
                "ai_confidence": None,
                "ai_reason": "",
            }
        )
    payload = {"positions": positions, "status_counts": _count_by_status(positions)}
    _write_json(Path(out_dir) / "positions.json", payload)
    return payload


def extract_study_notation_fragments(
    page_model: dict[str, Any],
    positions: dict[str, Any],
    out_dir: str | Path,
    *,
    glyph_context_pages: str = "",
    glyph_mapping_file: str | Path | None = None,
) -> dict[str, Any]:
    positions_by_page = _positions_by_page(positions)
    glyph_context_page_set = _parse_page_filter(glyph_context_pages)
    glyph_mapping = _load_ocr_glyph_mapping(glyph_mapping_file)
    fragments: list[dict[str, Any]] = []
    mapping_application_count = 0
    blocked_fragment_count = 0
    for page in page_model.get("pages", []) or []:
        page_number = int(page.get("page") or 0)
        page_positions = positions_by_page.get(page_number, [])
        blocks = [block for block in page.get("blocks", []) or [] if _looks_like_notation_text(str(block.get("normalized_text") or block.get("text") or ""))]
        if not blocks:
            continue
        raw_text = " ".join(str(block.get("text") or "") for block in blocks)
        normalized_text = _normalize_notation_text(raw_text)
        bbox = _union_bboxes([block.get("bbox") or [] for block in blocks])
        glyph_diagnostics = _notation_glyph_diagnostics_from_blocks(
            blocks,
            page_number=page_number,
            fallback_text=raw_text,
        )
        if _needs_raw_glyph_context(glyph_diagnostics) and _page_filter_allows(glyph_context_page_set, page_number):
            glyph_diagnostics = _merge_glyph_diagnostics(
                glyph_diagnostics,
                _rawdict_glyph_context_for_page(
                    page,
                    page_number=page_number,
                    fallback_text=raw_text,
                ),
            )
        mapped_text, mapping_result = _apply_ocr_glyph_mapping(normalized_text, glyph_mapping)
        mapping_application_count += int(mapping_result.get("applied_count") or 0)
        blockers = list(mapping_result.get("unmapped_tokens") or [])
        if blockers:
            blocked_fragment_count += 1
        linked_position = page_positions[0] if page_positions else {}
        source_diagram = str(linked_position.get("id") or "")
        fragment_id = f"n_p{page_number:03d}_{len(fragments) + 1:03d}"
        comments = _comments_from_notation_context(page, blocks)
        pgn = _build_notation_pgn_candidate(
            mapped_text,
            page=page_number,
            source_diagram=source_diagram,
            fen=str(linked_position.get("fen") or ""),
            comments=comments,
        )
        warnings: list[str] = []
        if not _pgn_has_required_headers(pgn):
            warnings.append("pgn_missing_required_headers")
        if not _pgn_replay_clean(pgn):
            warnings.append("pgn_replay_failed")
        if any(warning in str(linked_position.get("critical_warnings") or "") for warning in ["unmapped_chess_glyphs"]):
            warnings.append("linked_position_has_critical_warning")
        if blockers:
            warnings.append("unmapped_ocr_tokens")
        if glyph_diagnostics or _contains_unmapped_notation_glyphs(mapped_text):
            warnings.append(UNMAPPED_CHESS_GLYPH_WARNING)
        if not blockers and mapping_result.get("applied_count") and not _contains_unmapped_notation_glyphs(mapped_text):
            warnings = [warning for warning in warnings if warning != UNMAPPED_CHESS_GLYPH_WARNING]
            glyph_diagnostics = _mark_glyph_diagnostics_mapped(glyph_diagnostics, mapping_result)
        status = "accepted" if not warnings and source_diagram else "needs_review"
        fragments.append(
            StudyNotationFragment(
                id=fragment_id,
                page=page_number,
                diagram_id=source_diagram,
                source_page=page_number,
                source_diagram=source_diagram,
                raw_text=raw_text,
                normalized_text=mapped_text,
                comments=comments,
                pgn=pgn,
                status=status,
                warnings=sorted(set(warnings)),
                bbox=bbox,
                glyph_diagnostics=glyph_diagnostics[:NOTATION_GLYPH_SAMPLE_LIMIT],
            ).to_dict()
        )
        fragments[-1]["ocr_token_mappings_applied"] = int(mapping_result.get("applied_count") or 0)
        fragments[-1]["unmapped_token_blockers"] = blockers
        fragments[-1]["raw_glyph_context_mode"] = _raw_glyph_context_mode(fragments[-1])
    payload = {
        "fragment_count": len(fragments),
        "accepted_count": len([item for item in fragments if item.get("status") == "accepted"]),
        "needs_review_count": len([item for item in fragments if item.get("status") != "accepted"]),
        "ocr_token_mappings_loaded": int(glyph_mapping.get("accepted_count") or 0),
        "ocr_token_mappings_applied": mapping_application_count,
        "fragments_blocked_by_unmapped_tokens": blocked_fragment_count,
        "raw_glyph_context_mode": _raw_glyph_context_mode_for_fragments(fragments),
        "fragments": fragments,
    }
    _write_jsonl(Path(out_dir) / "notation_fragments.jsonl", fragments)
    _write_csv(Path(out_dir) / "notation_fragments.csv", fragments)
    _write_notation_glyph_diagnostics(Path(out_dir), fragments)
    _write_glyph_mapping_template_and_blockers(Path(out_dir), fragments, glyph_mapping)
    return payload


def build_study_pgn(
    positions: dict[str, Any],
    out_dir: str | Path,
    *,
    notation_fragments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    accepted_fragments = [
        item
        for item in (notation_fragments or {}).get("fragments", []) or []
        if item.get("status") == "accepted"
        and _pgn_has_required_headers(str(item.get("pgn") or ""))
        and _pgn_has_source_page(str(item.get("pgn") or ""))
        and _pgn_replay_clean(str(item.get("pgn") or ""))
        and UNMAPPED_CHESS_GLYPH_WARNING not in (item.get("warnings") or [])
        and not _fragment_has_raw_context_gap(item)
    ]
    accepted_positions = [
        item
        for item in positions.get("positions", []) or []
        if item.get("status") == "accepted"
        and _pgn_has_required_headers(str(item.get("solution_pgn") or ""))
        and _pgn_has_source_page(str(item.get("solution_pgn") or ""))
        and _pgn_replay_clean(str(item.get("solution_pgn") or ""))
    ]
    pgn_values = [str(item["pgn"]).strip() for item in accepted_fragments]
    pgn_values.extend(str(item["solution_pgn"]).strip() for item in accepted_positions)
    pgn_text = "\n\n".join(value for value in pgn_values if value).strip()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "book.pgn").write_text(pgn_text + ("\n" if pgn_text else ""), encoding="utf-8")
    (Path(out_dir) / "games_with_comments.pgn").write_text(pgn_text + ("\n" if pgn_text else ""), encoding="utf-8")
    return {
        "accepted_pgn_count": len(accepted_fragments) + len(accepted_positions),
        "pgn_records": [*accepted_fragments, *accepted_positions],
    }


def build_study_exercises(positions: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    exercises = [item for item in positions.get("positions", []) or [] if item.get("type") == "exercise"]
    payload = {"exercise_count": len(exercises), "exercises": exercises}
    _write_json(Path(out_dir) / "exercises.json", payload)
    return payload


def build_study_final_test(positions: dict[str, Any], out_dir: str | Path) -> dict[str, Any]:
    items = [item for item in positions.get("positions", []) or [] if item.get("type") == "final_test"]
    payload = {"type": "final_test", "position_count": len(items), "positions": items}
    _write_json(Path(out_dir) / "final_test.json", payload)
    return payload


def validate_study_export(
    config: ChessStudyConfig,
    *,
    current_audit: dict[str, Any],
    structure: dict[str, Any],
    segments: dict[str, Any],
    diagrams: dict[str, Any],
    positions: dict[str, Any],
    page_model: dict[str, Any] | None = None,
    notation_fragments: dict[str, Any] | None = None,
    pgn_payload: dict[str, Any],
    exercises: dict[str, Any],
    final_test: dict[str, Any],
) -> dict[str, Any]:
    position_list = list(positions.get("positions") or [])
    page_summary = (page_model or {}).get("summary") or {}
    notation_payload = notation_fragments or {"fragments": []}
    problems: list[dict[str, Any]] = []
    structure_validation = structure.get("validation") or {}
    for error in structure_validation.get("errors", []) or []:
        problems.append({"severity": "critical", "code": error, "area": "structure"})
    for item in position_list:
        if item.get("status") == "accepted":
            fen = str(item.get("fen") or "")
            valid, warnings = validate_fen(fen)
            if not valid or warnings:
                problems.append({"severity": "critical", "code": "accepted_fen_invalid", "position_id": item.get("id")})
            if not item.get("source_crop"):
                problems.append({"severity": "critical", "code": "accepted_fen_missing_crop", "position_id": item.get("id")})
            if not item.get("rendered_diagram"):
                problems.append({"severity": "critical", "code": "accepted_fen_missing_render", "position_id": item.get("id")})
            if str(item.get("solution_pgn") or "").strip() and not _pgn_replay_clean(str(item.get("solution_pgn") or "")):
                problems.append({"severity": "critical", "code": "accepted_pgn_invalid", "position_id": item.get("id")})
            if str(item.get("solution_pgn") or "").strip() and not _pgn_has_source_page(str(item.get("solution_pgn") or "")):
                problems.append({"severity": "critical", "code": "accepted_pgn_missing_source_page", "position_id": item.get("id")})
            if item.get("critical_warnings"):
                problems.append({"severity": "critical", "code": "accepted_has_critical_warning", "position_id": item.get("id")})
        if item.get("type") == "exercise" and not item.get("solution_page"):
            problems.append({"severity": "review", "code": "unlinked_solution", "position_id": item.get("id")})
    for fragment in notation_payload.get("fragments") or []:
        if not isinstance(fragment, dict) or fragment.get("status") != "accepted":
            continue
        fragment_id = fragment.get("id")
        pgn = str(fragment.get("pgn") or "")
        warnings = [str(warning) for warning in (fragment.get("warnings") or [])]
        if UNMAPPED_CHESS_GLYPH_WARNING in warnings:
            problems.append({"severity": "critical", "code": "accepted_notation_has_unmapped_glyphs", "fragment_id": fragment_id})
        if _fragment_has_raw_context_gap(fragment):
            problems.append({"severity": "critical", "code": "accepted_notation_missing_raw_glyph_context", "fragment_id": fragment_id})
        if not _pgn_has_required_headers(pgn):
            problems.append({"severity": "critical", "code": "accepted_notation_missing_required_headers", "fragment_id": fragment_id})
        if not _pgn_has_source_page(pgn):
            problems.append({"severity": "critical", "code": "accepted_notation_missing_source_page", "fragment_id": fragment_id})
        if not _pgn_replay_clean(pgn):
            problems.append({"severity": "critical", "code": "accepted_notation_pgn_invalid", "fragment_id": fragment_id})
    critical = [problem for problem in problems if problem.get("severity") == "critical"]
    review = [problem for problem in problems if problem.get("severity") == "review"]
    status = "FAIL" if critical else ("PASS_WITH_REVIEW_ITEMS" if review or _count_review_positions(position_list) else "PASS")
    notation_status_counts = _count_by_status(
        [item for item in notation_payload.get("fragments") or [] if isinstance(item, dict)]
    )
    notation_diagnostics = [
        diagnostic
        for item in notation_payload.get("fragments") or []
        if isinstance(item, dict)
        for diagnostic in item.get("glyph_diagnostics") or []
        if isinstance(diagnostic, dict)
    ][:NOTATION_GLYPH_DIAGNOSTIC_LIMIT]
    glyph_mapping_candidate_payload = _build_glyph_mapping_candidates(notation_diagnostics)
    glyph_mapping_candidate_count = int(glyph_mapping_candidate_payload.get("candidate_count") or 0)
    strict_diagram_count = int(
        diagrams.get("strict_diagram_count_after_review")
        if diagrams.get("strict_diagram_count_after_review") is not None
        else diagrams.get("diagram_count") or len(diagrams.get("diagrams") or [])
    )
    summary = {
        "pages": structure.get("pdf_page_count", 0),
        "chapters": len([chapter for chapter in structure.get("chapters", []) if chapter.get("start_book_page")]),
        "expected_chapters": len(YUSUPOV_CHAPTERS),
        "final_test_detected": bool((structure.get("final_test") or {}).get("start_book_page")),
        "diagrams_detected": strict_diagram_count,
        "fens_accepted": len([item for item in position_list if item.get("status") == "accepted" and item.get("fen")]),
        "fens_needs_review": len([item for item in position_list if item.get("status") in {"needs_review", "low_confidence"}]),
        "fens_missing": len([item for item in position_list if item.get("status") == "missing_fen"]),
        "pgn_accepted": int(pgn_payload.get("accepted_pgn_count") or 0),
        "pgn_needs_review": len([item for item in position_list if not item.get("solution_pgn")]),
        "pgn_illegal": 0,
        "missing_exercise_links": len([problem for problem in problems if problem.get("code") == "unlinked_solution"]),
        "validation_status": status,
        "quality_profile": config.quality_profile,
        "glyph_context_pages": config.glyph_context_pages,
        "review_sample_limit": config.review_sample_limit,
        "page_images": int(page_summary.get("page_images") or 0),
        "pages_with_extractable_text": int(page_summary.get("pages_with_extractable_text") or 0),
        "copyable_text_characters": int(page_summary.get("copyable_text_characters") or 0),
        "diagrams_total": strict_diagram_count,
        "strict_diagrams_total": strict_diagram_count,
        "low_confidence_review_candidates": int(
            diagrams.get("low_confidence_review_count")
            or len(diagrams.get("low_confidence_review_candidates") or [])
        ),
        "diagram_labels_imported": int(diagrams.get("diagram_labels_imported") or 0),
        "correct_diagrams": int(diagrams.get("correct_diagrams") or 0),
        "cropped_diagrams": int(diagrams.get("cropped_diagrams") or 0),
        "false_positive_diagrams": int(diagrams.get("false_positive_diagrams") or 0),
        "uncertain_diagrams": int(diagrams.get("uncertain_diagrams") or 0),
        "alignment_improved_count": int(diagrams.get("alignment_improved_count") or 0),
        "sampled_diagram_pages": list(diagrams.get("sampled_pages") or []),
        "strict_diagrams_sampled": strict_diagram_count,
        "low_confidence_candidates_sampled": int(
            diagrams.get("low_confidence_review_count")
            or len(diagrams.get("low_confidence_review_candidates") or [])
        ),
        "fen_accepted": len([item for item in position_list if item.get("status") == "accepted" and item.get("fen")]),
        "fen_status_counts": _count_by_status(position_list),
        "notation_fragments_total": int(notation_payload.get("fragment_count") or len(notation_payload.get("fragments") or [])),
        "notation_status_counts": notation_status_counts,
        "notation_glyph_diagnostics": sum(
            len(item.get("glyph_diagnostics") or [])
            for item in notation_payload.get("fragments") or []
            if isinstance(item, dict)
        ),
        "glyph_mapping_candidate_count": glyph_mapping_candidate_count,
        "ocr_token_mappings_loaded": int(notation_payload.get("ocr_token_mappings_loaded") or 0),
        "ocr_token_mappings_applied": int(notation_payload.get("ocr_token_mappings_applied") or 0),
        "fragments_blocked_by_unmapped_tokens": int(notation_payload.get("fragments_blocked_by_unmapped_tokens") or 0),
        "raw_glyph_context_mode": notation_payload.get("raw_glyph_context_mode") or "unknown",
        "raw_glyph_context_available": any(
            str(diagnostic.get("source") or "").startswith("pymupdf-rawdict")
            for diagnostic in notation_diagnostics
        ),
        "raw_glyph_context_gap_fragments": len(
            [
                item
                for item in notation_payload.get("fragments") or []
                if isinstance(item, dict) and _fragment_has_raw_context_gap(item)
            ]
        ),
        "unmapped_notation_fragments": len(
            [
                item
                for item in notation_payload.get("fragments") or []
                if isinstance(item, dict) and UNMAPPED_CHESS_GLYPH_WARNING in (item.get("warnings") or [])
            ]
        ),
        "accepted_pgn": int(pgn_payload.get("accepted_pgn_count") or 0),
        "ordering": "page-y-x-reading_order",
        "localhost_links": 0,
        "build_status": status,
    }
    if int(summary.get("unmapped_notation_fragments") or 0):
        problems.append(
            {
                "severity": "review",
                "code": UNMAPPED_CHESS_GLYPH_WARNING,
                "area": "notation",
                "count": int(summary.get("unmapped_notation_fragments") or 0),
            }
        )
    threshold_problems = _quality_threshold_problems(config, summary)
    problems.extend(threshold_problems)
    critical = [problem for problem in problems if problem.get("severity") == "critical"]
    review = [problem for problem in problems if problem.get("severity") == "review"]
    status = "FAIL" if critical else ("PASS_WITH_REVIEW_ITEMS" if review or _count_review_positions(position_list) else "PASS")
    summary["validation_status"] = status
    summary["build_status"] = status
    report = {
        "status": status,
        "summary": summary,
        "problems": problems,
        "current_audit_status": current_audit.get("final_html_status") or current_audit.get("status"),
        "status_policy": "accepted_requires_deterministic_validation",
        "quality_profile": config.quality_profile,
    }
    _write_json(config.out / "qa_report.json", report)
    render_audit_artifacts(config.out, report)
    return report


def render_study_html(
    out_dir: str | Path,
    *,
    structure: dict[str, Any],
    positions: dict[str, Any],
    qa_report: dict[str, Any],
    page_model: dict[str, Any] | None = None,
    notation_fragments: dict[str, Any] | None = None,
    source_pdf: str | Path | None = None,
    source_html: str | Path | None = None,
    source_gate: Mapping[str, Any] | None = None,
) -> Path:
    out = Path(out_dir)
    engine_by_id = _engine_analysis_by_diagram_id(out)
    hints_by_id = _engine_hints_by_diagram_id(out)
    comparison_by_id = _book_move_comparison_by_diagram_id(out)
    position_list = [
        _attach_book_move_comparison_to_record(
            _attach_engine_hints_to_record(_attach_engine_analysis_to_record(dict(item), engine_by_id), hints_by_id),
            comparison_by_id,
        )
        for item in positions.get("positions") or []
    ]
    positions = {**positions, "positions": position_list}
    artifact_manifest = _build_artifact_manifest(
        artifact_type=FINAL_READER_ARTIFACT_TYPE,
        pipeline_mode="pdf_two_crop_reader",
        source_pdf=source_pdf,
        source_html=source_html,
        source_gate=source_gate,
        summary=qa_report.get("summary") or {},
        positions=position_list,
    )
    _write_artifact_manifest(out / "data" / "artifact_manifest.json", artifact_manifest)
    status_options = sorted(STUDY_STATUSES)
    chapters = list(structure.get("chapters") or [])
    body_cards = "\n".join(_position_card_html(item) for item in position_list) or "<p>No positions detected yet.</p>"
    chapter_links = "\n".join(
        f'<a href="#chapter-{chapter["chapter_no"]}">{chapter["chapter_no"]}. {html.escape(chapter["title"])}</a>'
        for chapter in chapters
    )
    filters = "\n".join(
        f'<label><input type="checkbox" data-status-filter value="{status}" checked> {status}</label>'
        for status in status_options
    )
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Build Up Your Chess - Study Export</title>
  <style>
    :root {{ --ink:#1d1711; --paper:#fbf5ea; --line:#d8c8b1; --accent:#9a4f19; --ok:#147a3d; --warn:#a15c00; }}
    body {{ margin:0; font-family: Georgia, 'Times New Roman', serif; background:#efe4d3; color:var(--ink); }}
    .shell {{ display:grid; grid-template-columns:260px minmax(0,1fr) 280px; min-height:100vh; }}
    aside {{ position:sticky; top:0; height:100vh; overflow:auto; padding:1rem; background:#201810; color:#fff8ed; }}
    aside a, aside label {{ display:block; color:#fff8ed; margin:.45rem 0; text-decoration:none; }}
    main {{ padding:1.25rem; }}
    .summary, .card {{ background:var(--paper); border:1px solid var(--line); border-radius:18px; box-shadow:0 16px 40px rgba(58,38,17,.10); }}
    .summary {{ padding:1rem; margin-bottom:1rem; display:flex; flex-wrap:wrap; gap:.5rem; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:.32rem .65rem; background:#fffaf2; font-weight:700; }}
    .card {{ display:grid; grid-template-columns:280px minmax(0,1fr); gap:1rem; padding:1rem; margin:1rem 0; }}
    .diagram {{ background:#fffaf2; border-radius:14px; padding:.75rem; text-align:center; }}
    .diagram img {{ max-width:100%; height:auto; border-radius:6px; }}
    .status {{ font-weight:900; color:var(--warn); }}
    .status.accepted {{ color:var(--ok); }}
    code, pre {{ font-family: 'Courier New', monospace; }}
    pre {{ white-space:pre-wrap; background:#f6eddd; padding:.75rem; border-radius:12px; }}
    button {{ border:1px solid var(--line); border-radius:999px; background:#fff8ed; padding:.42rem .75rem; font-weight:800; cursor:pointer; }}
    button:focus-visible, summary:focus-visible, a:focus-visible, input:focus-visible {{ outline:3px solid #b96920; outline-offset:3px; }}
.engine-panel, .engine-hints-panel, .book-move-comparison-panel, .try-self-panel, .solution-panel, .original-source {{ border:1px solid var(--line); border-radius:14px; background:#fffaf2; margin:.65rem 0; padding:0 .75rem; }}
.engine-panel summary, .engine-hints-panel summary, .book-move-comparison-panel summary, .try-self-panel summary, .solution-panel summary, .original-source summary {{ cursor:pointer; min-height:2.75rem; font-weight:900; color:var(--accent); }}
.engine-panel-body {{ padding:0 0 .75rem; }}
.engine-kpis {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:.5rem; }}
.engine-kpis span {{ border:1px solid #e5d5bd; border-radius:12px; background:#fffdf8; padding:.55rem .6rem; overflow-wrap:anywhere; }}
.engine-kpis strong {{ display:block; color:#76634e; font-size:.76rem; text-transform:uppercase; letter-spacing:.04em; }}
.engine-empty, .engine-reason, .engine-cache, .engine-pv, .engine-hint-source {{ margin:.45rem 0; color:#76634e; }}
.engine-hint-steps {{ display:grid; gap:.45rem; }}
.engine-hint-level, .engine-hint-full-reveal, .engine-hint-line-reveal {{ border:1px solid #e5d5bd; border-radius:12px; background:#fffdf8; padding:0 .6rem; }}
.engine-hint-level p, .engine-hint-full-reveal p, .engine-hint-line-reveal p {{ margin:.35rem 0 .6rem; }}
    .study-actions {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:.55rem; margin:.75rem 0; }}
    .artifact-provenance {{ background:#fff8ed; border:1px solid var(--line); border-left:6px solid var(--accent); border-radius:14px; padding:.85rem 1rem; margin:0 0 1rem; }}
    .artifact-provenance h2 {{ margin:0 0 .35rem; font-size:1rem; }}
    .artifact-provenance p {{ margin:.2rem 0; }}
    .debug {{ display:none; }}
    body.show-debug .debug {{ display:block; }}
    @media(max-width:960px) {{ .shell {{ grid-template-columns:1fr; }} aside {{ position:relative; height:auto; }} .card {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body {_artifact_data_attrs(artifact_manifest)}>
<div class="shell">
  <aside>
    <h2>Chapters</h2>
    {chapter_links}
    <h2>Filters</h2>
    {filters}
    <button type="button" id="debugToggle">Debug view</button>
    <p><a href="qa_report.html">QA report</a></p>
  </aside>
  <main>
    <h1>Build Up Your Chess - Study Export</h1>
    {_artifact_provenance_banner_html(artifact_manifest)}
    {_summary_html(qa_report)}
    <section id="positions">{body_cards}</section>
  </main>
  <aside>
    <h2>QA</h2>
    <pre>{html.escape(json.dumps(_html_safe_summary(qa_report), ensure_ascii=False, indent=2))}</pre>
  </aside>
</div>
<script>
document.addEventListener('click', function(event) {{
  var button = event.target.closest('[data-copy-target]');
  if (!button) return;
  var target = document.getElementById(button.getAttribute('data-copy-target'));
  if (!target) return;
  navigator.clipboard.writeText(target.innerText || target.textContent || '');
}});
document.getElementById('debugToggle').addEventListener('click', function() {{ document.body.classList.toggle('show-debug'); }});
document.querySelectorAll('[data-status-filter]').forEach(function(input) {{
  input.addEventListener('change', function() {{
    var active = Array.from(document.querySelectorAll('[data-status-filter]:checked')).map(function(node) {{ return node.value; }});
    document.querySelectorAll('[data-position-status]').forEach(function(card) {{
      card.style.display = active.indexOf(card.getAttribute('data-position-status')) >= 0 ? '' : 'none';
    }});
  }});
}});
</script>
</body>
</html>
"""
    path = out / "index.html"
    path.write_text(html_text, encoding="utf-8")
    _write_final_reader_health_gate(
        out,
        artifact_manifest=artifact_manifest,
        summary=qa_report.get("summary") or {},
        positions=position_list,
    )
    _render_standalone_html(
        out,
        structure=structure,
        positions=positions,
        qa_report=qa_report,
        page_model=page_model or {"pages": []},
        notation_fragments=notation_fragments or {"fragments": []},
    )
    _render_kindle_html(
        out,
        structure=structure,
        positions=positions,
        qa_report=qa_report,
        page_model=page_model or {"pages": []},
        notation_fragments=notation_fragments or {"fragments": []},
    )
    return path


def render_qa_html(out_dir: str | Path, qa_report: dict[str, Any]) -> Path:
    out = Path(out_dir)
    rows = "\n".join(
        f"<tr><td>{html.escape(str(problem.get('severity')))}</td><td>{html.escape(str(problem.get('code')))}</td><td>{html.escape(str(problem.get('position_id', '')))}</td></tr>"
        for problem in qa_report.get("problems", [])
    )
    text = f"""<!doctype html><html><head><meta charset="utf-8"><title>Chess Study QA</title>
<style>body{{font-family:Georgia,serif;margin:2rem;background:#fbf5ea;color:#1d1711}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #d8c8b1;padding:.5rem}}pre{{background:#f6eddd;padding:1rem;border-radius:12px}}</style>
</head><body><h1>Chess Study QA</h1><h2>Status: {html.escape(str(qa_report.get('status')))}</h2>
<pre>{html.escape(json.dumps(qa_report.get('summary', {}), ensure_ascii=False, indent=2))}</pre>
<table><thead><tr><th>Severity</th><th>Code</th><th>Position</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
    path = out / "qa_report.html"
    path.write_text(text, encoding="utf-8")
    return path


def render_audit_artifacts(out_dir: str | Path, qa_report: dict[str, Any]) -> None:
    out = Path(out_dir)
    summary = qa_report.get("summary") or {}
    _write_json(out / "audit_summary.json", {"status": qa_report.get("status"), **summary})
    lines = [
        "# Chess Study Audit Report",
        "",
        f"- Status: `{qa_report.get('status')}`",
        f"- Quality profile: `{qa_report.get('quality_profile') or summary.get('quality_profile')}`",
        f"- Glyph context pages: `{summary.get('glyph_context_pages')}`",
        f"- Review sample limit: `{summary.get('review_sample_limit')}`",
        f"- Pages: `{summary.get('pages')}`",
        f"- Page images: `{summary.get('page_images')}`",
        f"- Pages with text: `{summary.get('pages_with_extractable_text')}`",
        f"- Copyable text characters: `{summary.get('copyable_text_characters')}`",
        f"- Diagrams: `{summary.get('diagrams_total')}`",
        f"- Strict diagrams: `{summary.get('strict_diagrams_total')}`",
        f"- Low-confidence review candidates: `{summary.get('low_confidence_review_candidates')}`",
        f"- Diagram labels imported: `{summary.get('diagram_labels_imported')}`",
        f"- Correct diagrams: `{summary.get('correct_diagrams')}`",
        f"- Cropped diagrams: `{summary.get('cropped_diagrams')}`",
        f"- False-positive diagrams: `{summary.get('false_positive_diagrams')}`",
        f"- Uncertain diagrams: `{summary.get('uncertain_diagrams')}`",
        f"- Alignment improved count: `{summary.get('alignment_improved_count')}`",
        f"- Sampled diagram pages: `{summary.get('sampled_diagram_pages')}`",
        f"- Strict diagrams sampled: `{summary.get('strict_diagrams_sampled')}`",
        f"- Low-confidence candidates sampled: `{summary.get('low_confidence_candidates_sampled')}`",
        f"- Accepted FEN: `{summary.get('fen_accepted')}`",
        f"- FEN status counts: `{summary.get('fen_status_counts')}`",
        f"- Notation fragments: `{summary.get('notation_fragments_total')}`",
        f"- Notation status counts: `{summary.get('notation_status_counts')}`",
        f"- Notation glyph diagnostics: `{summary.get('notation_glyph_diagnostics')}`",
        f"- Glyph mapping candidate count: `{summary.get('glyph_mapping_candidate_count')}`",
        f"- OCR token mappings loaded: `{summary.get('ocr_token_mappings_loaded')}`",
        f"- OCR token mappings applied: `{summary.get('ocr_token_mappings_applied')}`",
        f"- Fragments blocked by unmapped tokens: `{summary.get('fragments_blocked_by_unmapped_tokens')}`",
        f"- Raw glyph context mode: `{summary.get('raw_glyph_context_mode')}`",
        f"- Raw glyph context available: `{summary.get('raw_glyph_context_available')}`",
        f"- Raw glyph context gap fragments: `{summary.get('raw_glyph_context_gap_fragments')}`",
        f"- Unmapped notation fragments: `{summary.get('unmapped_notation_fragments')}`",
        f"- Accepted PGN: `{summary.get('accepted_pgn')}`",
        f"- Ordering: `{summary.get('ordering')}`",
        f"- Build status: `{summary.get('build_status')}`",
        "",
        "## Problems",
        "",
    ]
    problems = qa_report.get("problems") or []
    if not problems:
        lines.append("- None")
    else:
        for problem in problems:
            lines.append(
                f"- `{problem.get('severity')}` `{problem.get('code')}` "
                f"{problem.get('position_id') or problem.get('metric') or problem.get('area') or ''}"
            )
    audit_text = "\n".join(lines) + "\n"
    (out / "audit_report.md").write_text(audit_text, encoding="utf-8")
    reports_dir = out / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "conversion-audit.md").write_text(audit_text, encoding="utf-8")


def _render_standalone_html(
    out: Path,
    *,
    structure: dict[str, Any],
    positions: dict[str, Any],
    qa_report: dict[str, Any],
    page_model: dict[str, Any],
    notation_fragments: dict[str, Any],
) -> Path:
    page_cards = "\n".join(
        _study_page_html(page, positions=positions, notation_fragments=notation_fragments, include_page_image=True)
        for page in page_model.get("pages", []) or []
    )
    if not page_cards:
        page_cards = "<p>No page model available.</p>"
    text = _study_html_document(
        title="Build Up Your Chess - Standalone Audit",
        body=f"""
<header class="hero">
  <p class="eyebrow">MasterKindle audit view</p>
  <h1>Build Up Your Chess - Standalone Audit</h1>
  {_summary_html(qa_report)}
</header>
<main class="book-flow">{page_cards}</main>
""",
        qa_report=qa_report,
    )
    path = out / "standalone.html"
    path.write_text(text, encoding="utf-8")
    audit_path = out / "standalone_audit.html"
    audit_path.write_text(text, encoding="utf-8")
    return path


def _render_kindle_html(
    out: Path,
    *,
    structure: dict[str, Any],
    positions: dict[str, Any],
    qa_report: dict[str, Any],
    page_model: dict[str, Any],
    notation_fragments: dict[str, Any],
) -> Path:
    chapters = list(structure.get("chapters") or [])
    toc = "\n".join(
        f'<li><a href="#chapter-{int(chapter.get("chapter_no") or 0):02d}">{html.escape(str(chapter.get("title") or ""))}</a></li>'
        for chapter in chapters
    )
    page_cards = "\n".join(
        _study_page_html(page, positions=positions, notation_fragments=notation_fragments, include_page_image=False)
        for page in page_model.get("pages", []) or []
    )
    text = _study_html_document(
        title="Build Up Your Chess - Kindle Study",
        body=f"""
<header class="hero">
  <p class="eyebrow">Kindle study edition</p>
  <h1>Build Up Your Chess - Study Reader</h1>
  <p class="hero-copy">Semantic reading order with diagrams, notation, and review status kept together for study.</p>
  {_study_reader_scorebar_html(qa_report)}
  {_study_reader_audit_summary_html(qa_report)}
</header>
<nav class="toc" aria-label="Chapters"><ol>{toc}</ol></nav>
<main class="book-flow">{page_cards or '<p>No page text available.</p>'}</main>
""",
        qa_report=qa_report,
    )
    path = out / "kindle.html"
    path.write_text(text, encoding="utf-8")
    return path


def _study_html_document(*, title: str, body: str, qa_report: dict[str, Any]) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --ink:#201713; --paper:#fffaf0; --paper-soft:#fff7e8; --wash:#efe2cd; --line:#d8c5aa; --muted:#76634e; --ok:#176b3a; --warn:#985b00; --bad:#9b1c1c; --accent:#7a3e13; }}
    * {{ box-sizing:border-box; }}
    html, body {{ max-width:100%; overflow-x:hidden; }}
    body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; line-height:1.55; color:var(--ink); background:var(--wash); }}
    a {{ color:#7a3e13; }}
    .hero {{ max-width:1120px; margin:0 auto; padding:2rem 1rem 1rem; }}
    .hero-copy {{ max-width:62ch; margin:.25rem 0 1.1rem; color:var(--muted); font-size:1.05rem; overflow-wrap:anywhere; }}
    .eyebrow {{ margin:0 0 .35rem; color:#8a4a18; font-weight:800; letter-spacing:.06em; text-transform:uppercase; font-size:.8rem; }}
    h1 {{ margin:.1rem 0 1rem; font-size:clamp(2rem, 5vw, 4rem); line-height:1.02; }}
    h2, h3 {{ line-height:1.18; }}
    .summary {{ display:flex; flex-wrap:wrap; gap:.45rem; margin:1rem 0; }}
    .pill {{ display:inline-flex; align-items:center; min-height:2rem; border:1px solid var(--line); border-radius:999px; padding:.25rem .62rem; background:#fffdf7; font-weight:700; font-size:.9rem; }}
    .scorebar {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:.75rem; margin:1rem 0; }}
    .score {{ background:var(--paper); border:1px solid var(--line); border-radius:18px; padding:.85rem .95rem; }}
    .score-label {{ display:block; color:var(--muted); font-size:.78rem; font-weight:800; letter-spacing:.04em; text-transform:uppercase; }}
    .score-value {{ display:block; margin-top:.15rem; font-size:1.35rem; font-weight:900; line-height:1.1; }}
    .audit-summary {{ margin:.75rem 0 0; border:1px solid var(--line); border-radius:16px; background:rgba(255,250,240,.72); padding:0 .9rem; }}
    .audit-summary summary {{ color:var(--accent); }}
    .audit-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:.45rem; padding:0 0 .9rem; }}
    .audit-grid span {{ color:var(--muted); font-size:.88rem; overflow-wrap:anywhere; }}
    .toc, .book-flow {{ max-width:1120px; width:100%; min-width:0; margin:0 auto; padding:0 1rem 2rem; }}
    .toc {{ background:#fff6e8; border:1px solid var(--line); border-radius:20px; padding:1rem 1.25rem; margin-bottom:1rem; }}
    .page {{ min-width:0; max-width:100%; background:var(--paper); border:1px solid var(--line); border-radius:22px; padding:1.25rem; margin:1.25rem 0; box-shadow:0 18px 42px rgba(61, 38, 12, .10); }}
    .study-page {{ background:linear-gradient(180deg, #fffaf0 0%, #fff4e2 100%); box-shadow:0 10px 30px rgba(61,38,12,.07); }}
    .page-header {{ display:flex; justify-content:space-between; gap:1rem; align-items:baseline; border-bottom:1px solid var(--line); margin-bottom:1rem; }}
    .page-header span {{ color:var(--muted); font-size:.92rem; }}
    .page-image img, .diagram-card img {{ max-width:100%; height:auto; border-radius:10px; border:1px solid var(--line); background:#fff; }}
    .book-elements {{ max-width:82ch; }}
    .book-text-block, .study-prose {{ margin:.65rem 0; max-width:68ch; }}
    .book-heading {{ margin:1rem 0 .45rem; color:#5d3418; }}
    .diagram-card, .notation-fragment {{ border:1px solid var(--line); border-radius:16px; padding:.9rem; background:#fffdf7; margin:1rem 0; }}
    .study-block {{ min-width:0; max-width:100%; background:var(--paper-soft); }}
    .study-block-grid {{ display:grid; grid-template-columns:minmax(220px, 280px) minmax(0, 1fr); gap:1rem; align-items:start; }}
    .study-diagram {{ min-width:0; }}
    .study-content {{ min-width:0; }}
    .study-meta {{ display:flex; flex-wrap:wrap; gap:.45rem; align-items:center; margin:.25rem 0 .75rem; color:var(--muted); font-size:.9rem; }}
    .study-meta > * {{ min-width:0; overflow-wrap:anywhere; }}
    .study-notation, .study-review {{ min-width:0; }}
    .diagram-compare {{ display:grid; grid-template-columns:1fr; gap:.75rem; align-items:start; }}
    .diagram-compare figure {{ margin:0; }}
    .diagram-compare figcaption {{ font-size:.86rem; color:var(--muted); margin:.25rem 0; }}
    code.fen {{ display:block; overflow-wrap:anywhere; background:#f5ead8; border:1px solid #e5d5bd; border-radius:10px; padding:.55rem; }}
    pre.pgn {{ background:#f1e2c9; }}
    .status {{ display:inline-flex; max-width:100%; border-radius:999px; padding:.2rem .55rem; border:1px solid var(--line); font-weight:800; white-space:normal; overflow-wrap:anywhere; }}
    .status.accepted {{ color:var(--ok); }}
    .status.needs_review, .status.missing_fen, .status.missing_pgn, .status.low_confidence, .status.unlinked_solution {{ color:var(--warn); }}
    .status.illegal_pgn {{ color:var(--bad); }}
    pre, code {{ font-family:'Courier New', monospace; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; max-width:100%; background:#f5ead8; border-radius:12px; padding:.75rem; border:1px solid #e5d5bd; }}
    summary {{ cursor:pointer; font-weight:800; min-height:2.75rem; display:flex; align-items:center; }}
    summary:focus-visible, a:focus-visible, button:focus-visible {{ outline:3px solid #b96920; outline-offset:3px; }}
.engine-panel, .engine-hints-panel, .book-move-comparison-panel, .try-self-panel, .solution-panel, .original-source {{ border:1px solid var(--line); border-radius:14px; background:#fffaf2; margin:.65rem 0; padding:0 .75rem; }}
.engine-panel summary, .engine-hints-panel summary, .book-move-comparison-panel summary, .try-self-panel summary, .solution-panel summary, .original-source summary {{ color:var(--accent); font-weight:900; }}
.engine-panel-body {{ padding:0 0 .75rem; }}
.engine-kpis {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:.5rem; }}
.engine-kpis span {{ border:1px solid #e5d5bd; border-radius:12px; background:#fffdf8; padding:.55rem .6rem; overflow-wrap:anywhere; }}
.engine-kpis strong {{ display:block; color:var(--muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.04em; }}
.engine-empty, .engine-reason, .engine-cache, .engine-pv, .engine-hint-source {{ margin:.45rem 0; color:var(--muted); }}
.engine-hint-steps {{ display:grid; gap:.45rem; }}
.engine-hint-level, .engine-hint-full-reveal, .engine-hint-line-reveal {{ border:1px solid #e5d5bd; border-radius:12px; background:#fffdf8; padding:0 .6rem; }}
.engine-hint-level p, .engine-hint-full-reveal p, .engine-hint-line-reveal p {{ margin:.35rem 0 .6rem; }}
    .study-actions {{ min-width:0; display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:.55rem; margin:.75rem 0; }}
    .study-actions > * {{ min-width:0; }}
    @media(max-width:840px) {{ .scorebar {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} .study-block-grid {{ grid-template-columns:1fr; }} }}
    @media(max-width:720px) {{ .page {{ border-radius:0; margin:0 0 1rem; }} .page-header {{ display:block; }} .scorebar, .study-actions {{ grid-template-columns:1fr; }} .toc, .book-flow, .hero {{ padding-left:.85rem; padding-right:.85rem; }} }}
  </style>
</head>
<body data-audit-status="{html.escape(str(qa_report.get('status') or ''), quote=True)}">
{body}
</body>
</html>"""


def _study_page_html(
    page: dict[str, Any],
    *,
    positions: dict[str, Any],
    notation_fragments: dict[str, Any],
    include_page_image: bool,
) -> str:
    page_number = int(page.get("page") or 0)
    page_positions = [item for item in positions.get("positions", []) or [] if int(item.get("diagram_page") or 0) == page_number]
    page_fragments = [item for item in notation_fragments.get("fragments", []) or [] if int(item.get("page") or 0) == page_number]
    image_html = ""
    if include_page_image and page.get("page_image"):
        image_html = f"""<details class="page-image"><summary>Source PDF page image</summary><img src="{html.escape(str(page.get('page_image')), quote=True)}" alt="PDF page {page_number}"></details>"""
    position_by_id = {str(item.get("id") or ""): item for item in page_positions}
    fragment_by_id = {str(item.get("id") or ""): item for item in page_fragments}
    layout_elements = _page_layout_elements_for_render(page, page_positions=page_positions, page_fragments=page_fragments)
    element_html = "\n".join(
        _study_layout_element_html(element, position_by_id=position_by_id, fragment_by_id=fragment_by_id)
        for element in layout_elements
    )
    if not element_html:
        element_html = "<p>No extractable text on this page.</p>"
    chapter = _chapter_anchor_for_page(page_number, positions)
    return f"""<section class="page study-page" data-page="{page_number}" {chapter}>
  <div class="page-header"><h2>Page {page_number}</h2><span>{len(page_positions)} diagrams / {len(page_fragments)} notation fragments</span></div>
  {image_html}
  <section class="book-elements">{element_html}</section>
</section>"""


def _page_layout_elements_for_render(
    page: dict[str, Any],
    *,
    page_positions: list[dict[str, Any]],
    page_fragments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    page_number = int(page.get("page") or 0)
    elements: list[dict[str, Any]] = []
    for element in page.get("elements") or []:
        item = _coerce_layout_element(element)
        if item.get("type") == "notation":
            continue
        elements.append(item)
    base_order = len(elements) + 1
    for index, position in enumerate(page_positions):
        elements.append(
            {
                "type": "diagram",
                "page": page_number,
                "bbox": _bbox4(position.get("bbox") or []),
                "reading_order": base_order + index,
                "source_kind": "diagram-detector",
                "ref_id": str(position.get("id") or ""),
                "status": str(position.get("status") or "needs_review"),
                "text": str(position.get("label") or position.get("id") or "Diagram"),
            }
        )
    base_order += len(page_positions)
    for index, fragment in enumerate(page_fragments):
        elements.append(
            {
                "type": "notation",
                "page": page_number,
                "bbox": _bbox4(fragment.get("bbox") or []),
                "reading_order": base_order + index,
                "source_kind": "pgn-normalizer",
                "ref_id": str(fragment.get("id") or ""),
                "status": str(fragment.get("status") or "needs_review"),
                "text": str(fragment.get("normalized_text") or fragment.get("raw_text") or ""),
            }
        )
    return sort_study_layout_elements(elements)


def _study_layout_element_html(
    element: dict[str, Any],
    *,
    position_by_id: dict[str, dict[str, Any]],
    fragment_by_id: dict[str, dict[str, Any]],
) -> str:
    element_type = str(element.get("type") or "text")
    source_kind = html.escape(str(element.get("source_kind") or "unknown"), quote=True)
    reading_order = html.escape(str(element.get("reading_order") or "0"), quote=True)
    if element_type == "diagram":
        return _study_position_article(position_by_id.get(str(element.get("ref_id") or ""), element))
    if element_type == "notation":
        return _study_notation_article(fragment_by_id.get(str(element.get("ref_id") or ""), element))
    text = html.escape(str(element.get("text") or ""))
    if not text:
        return ""
    if element_type == "heading":
        return f'<h3 class="book-heading" data-source-kind="{source_kind}" data-reading-order="{reading_order}">{text}</h3>'
    if _is_reader_noise_text(str(element.get("text") or "")):
        return ""
    return f'<p class="book-text-block study-prose" data-source-kind="{source_kind}" data-reading-order="{reading_order}">{text}</p>'


def _study_position_article(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "needs_review")
    fen = str(item.get("fen") or "")
    pgn = str(item.get("solution_pgn") or "")
    crop = str(item.get("source_crop") or item.get("board_crop_path") or "")
    marker_crop = str(item.get("side_marker_crop_path") or "")
    debug_overlay = str(item.get("debug_overlay_path") or "")
    rendered = str(item.get("rendered_diagram") or item.get("rendered_svg") or item.get("rendered_png") or "")
    label = html.escape(str(item.get("label") or item.get("id") or "Diagram"))
    source_page = html.escape(str(item.get("diagram_page") or ""))
    side_to_move = html.escape(str(item.get("side_to_move") or "unknown"))
    crop_html = (
        f'<figure><figcaption>Diagram crop</figcaption><img src="{html.escape(crop, quote=True)}" alt="{html.escape(str(item.get("id") or "diagram"))} crop"></figure>'
        if crop
        else "<figure><figcaption>Diagram crop</figcaption><p>No crop available.</p></figure>"
    )
    marker_crop_html = (
        f'<figure><figcaption>Side marker crop</figcaption><img src="{html.escape(marker_crop, quote=True)}" alt="{html.escape(str(item.get("id") or "diagram"))} side marker crop"></figure>'
        if marker_crop
        else ""
    )
    debug_overlay_html = (
        f'<figure><figcaption>Debug overlay</figcaption><img src="{html.escape(debug_overlay, quote=True)}" alt="{html.escape(str(item.get("id") or "diagram"))} debug overlay"></figure>'
        if debug_overlay
        else ""
    )
    rendered_html = (
        f'<figure><figcaption>Rendered FEN</figcaption><img src="{html.escape(rendered, quote=True)}" alt="{html.escape(str(item.get("id") or "diagram"))} rendered FEN"></figure>'
        if rendered
        else ""
    )
    fen_html = (
        f'<p><strong>FEN</strong></p><code class="fen book-fen">{html.escape(fen)}</code>'
        if fen and status == "accepted"
        else '<p class="study-review">FEN needs review or is missing.</p>'
    )
    pgn_html = (
        f'<p><strong>PGN</strong></p><pre class="pgn book-pgn-record">{html.escape(pgn)}</pre>'
        if pgn and status == "accepted" and _pgn_replay_clean(pgn)
        else '<p class="study-review">PGN needs review or is missing.</p>'
    )
    rendered_block = rendered_html if rendered_html else ""
    hints_html = _engine_study_hints_panel_html(item.get("engine_hints"), mode="study")
    engine_html = _engine_analysis_panel_html(item.get("engine_analysis"), mode="study")
    comparison_html = _book_move_comparison_panel_html(item.get("book_move_comparison"), mode="study")
    solution_html = f"""<details class="solution-panel">
        <summary>Pokaż rozwiązanie książki</summary>
        {pgn_html}
      </details>"""
    return f"""<article class="study-block diagram-card" data-status="{html.escape(status, quote=True)}" data-diagram-id="{html.escape(str(item.get("id") or ""), quote=True)}">
  <div class="study-block-grid">
    <aside class="study-diagram">
      <details class="original-source" open>
        <summary>Podgląd oryginału</summary>
        <div class="diagram-compare">{crop_html}{marker_crop_html}{debug_overlay_html}{rendered_block}</div>
      </details>
    </aside>
    <div class="study-content">
      <h3>{label}</h3>
      <p class="study-meta"><span class="status {html.escape(status, quote=True)}">{html.escape(status)}</span><span>Page {source_page}</span><span>{side_to_move} to move</span><span>{html.escape(str(item.get('side_marker_status') or 'marker_missing'))}</span></p>
      <div class="study-actions">
        <details class="try-self-panel">
          <summary>Spróbuj sam</summary>
          <p>Zatrzymaj się przy diagramie i wybierz własny kandydacki ruch przed odkryciem analizy.</p>
        </details>
        {hints_html}
        {engine_html}
        {comparison_html}
        {solution_html}
      </div>
      {fen_html}
    </div>
  </div>
</article>"""


def _study_notation_article(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "needs_review")
    pgn = str(item.get("pgn") or "")
    raw = str(item.get("raw_text") or "")
    body = (
        f'<pre class="pgn book-pgn-record">{html.escape(pgn)}</pre>'
        if status == "accepted" and pgn and _pgn_replay_clean(pgn)
        else f'<pre class="notation-source">{html.escape(raw)}</pre>'
    )
    review_class = "study-review" if status != "accepted" else ""
    return f"""<details class="study-block study-notation notation-fragment {review_class}" data-status="{html.escape(status, quote=True)}">
  <summary>Notation fragment {html.escape(str(item.get("id") or ""))} - {html.escape(status)}</summary>
  <p><strong>Source page:</strong> {html.escape(str(item.get("source_page") or ""))}</p>
  {body}
</details>"""

def _position_card_html(item: dict[str, Any]) -> str:
    safe_id = html.escape(str(item.get("id") or "position"), quote=True)
    status = str(item.get("status") or "needs_review")
    crop = str(item.get("source_crop") or item.get("board_crop_path") or "")
    marker_crop = str(item.get("side_marker_crop_path") or "")
    debug_overlay = str(item.get("debug_overlay_path") or "")
    rendered = str(item.get("rendered_diagram") or item.get("rendered_svg") or item.get("rendered_png") or "")
    fen = str(item.get("fen") or "")
    pgn = str(item.get("solution_pgn") or "")
    marker_status = str(item.get("side_marker_status") or "")
    marker_attr = f' data-side-marker-status="{html.escape(marker_status, quote=True)}"' if marker_status else ""
    has_board_crop = bool(crop.strip())
    has_side_marker_crop = bool(marker_crop.strip())
    crop_html = f'<img src="{html.escape(crop, quote=True)}" alt="{safe_id} source crop">' if crop else "<p>No source crop</p>"
    marker_html = f'<hr><img src="{html.escape(marker_crop, quote=True)}" alt="{safe_id} side marker crop">' if marker_crop else ""
    overlay_html = f'<hr><img src="{html.escape(debug_overlay, quote=True)}" alt="{safe_id} debug overlay">' if debug_overlay else ""
    rendered_html = f'<img src="{html.escape(rendered, quote=True)}" alt="{safe_id} rendered FEN">' if rendered else "<p>No rendered FEN diagram</p>"
    fen_html = (
        f'<p><button data-copy-target="fen-{safe_id}">Copy FEN</button></p><pre id="fen-{safe_id}">{html.escape(fen)}</pre>'
        if fen and status == "accepted"
        else "<p>FEN: needs review or missing.</p>"
    )
    pgn_html = (
        f'<p><button data-copy-target="pgn-{safe_id}">Copy PGN</button></p><pre id="pgn-{safe_id}">{html.escape(pgn)}</pre>'
        if pgn and status == "accepted" and _pgn_replay_clean(pgn)
        else "<p>PGN: needs review or missing.</p>"
    )
    hints_html = _engine_study_hints_panel_html(item.get("engine_hints"), mode="audit", open_by_default=False)
    engine_html = _engine_analysis_panel_html(item.get("engine_analysis"), mode="audit", open_by_default=False)
    comparison_html = _book_move_comparison_panel_html(item.get("book_move_comparison"), mode="audit", open_by_default=False)
    return f"""<article class="card" data-position-status="{html.escape(status, quote=True)}"{marker_attr} data-has-board-crop="{str(has_board_crop).lower()}" data-has-side-marker-crop="{str(has_side_marker_crop).lower()}">
  <div class="diagram">{crop_html}{marker_html}{overlay_html}<hr>{rendered_html}</div>
  <div>
    <h2>{html.escape(str(item.get("chapter_title") or "Unassigned"))} - {html.escape(str(item.get("label") or item.get("id")))}</h2>
    <p>Source page: {html.escape(str(item.get("diagram_page") or ""))}</p>
    <p>Solution page: {html.escape(str(item.get("solution_page") or "unlinked"))}</p>
    <p>Side to move: {html.escape(str(item.get("side_to_move") or "unknown"))}</p>
    <p>Status: <span class="status {html.escape(status, quote=True)}">{html.escape(status)}</span></p>
    {fen_html}
    {pgn_html}
    {hints_html}
    {engine_html}
    {comparison_html}
    <div class="debug"><h3>Marker</h3><pre>{html.escape(json.dumps({'status': item.get('side_marker_status'), 'symbol': item.get('side_marker_symbol'), 'bbox': item.get('side_marker_bbox'), 'trace': item.get('side_marker_assignment_trace')}, ensure_ascii=False, indent=2))}</pre><h3>Warnings</h3><pre>{html.escape(json.dumps(item.get("warnings", []), ensure_ascii=False, indent=2))}</pre></div>
  </div>
</article>"""


def _study_reader_scorebar_html(qa_report: dict[str, Any]) -> str:
    summary = _html_safe_summary(qa_report)
    values = [
        ("Pages", summary.get("pages")),
        ("Diagrams", f"{summary.get('diagrams_total', 0)} / review {summary.get('low_confidence_review_candidates', 0)}"),
        ("FEN", f"{summary.get('fen_accepted', summary.get('fens_accepted', 0))} accepted"),
        ("PGN", f"{summary.get('accepted_pgn', summary.get('pgn_accepted', 0))} accepted"),
    ]
    items = "".join(
        f'<div class="score"><span class="score-label">{html.escape(str(label))}</span><span class="score-value">{html.escape(str(value))}</span></div>'
        for label, value in values
    )
    return f'<section class="scorebar" aria-label="Study export summary">{items}</section>'


def _study_reader_audit_summary_html(qa_report: dict[str, Any]) -> str:
    summary = _html_safe_summary(qa_report)
    rows = "".join(
        f"<span><strong>{html.escape(str(key))}:</strong> {html.escape(str(value))}</span>"
        for key, value in summary.items()
    )
    return f"""<details class="audit-summary">
  <summary>Audit metrics</summary>
  <div class="audit-grid">{rows}</div>
</details>"""


def _summary_html(qa_report: dict[str, Any]) -> str:
    summary = _html_safe_summary(qa_report)
    return '<section class="summary">' + "".join(
        f'<span class="pill">{html.escape(str(key))}: {html.escape(str(value))}</span>'
        for key, value in summary.items()
    ) + "</section>"


def _html_safe_summary(qa_report: dict[str, Any]) -> dict[str, Any]:
    summary = dict(qa_report.get("summary") or {})
    if "localhost_links" in summary:
        summary["local_link_count"] = summary.pop("localhost_links")
    return summary


def _ensure_output_dirs(out: Path) -> None:
    for child in [
        out,
        out / "reports",
        out / "assets" / "page_images",
        out / "assets" / "diagram_crops",
        out / "assets" / "diagram_svg",
        out / "assets" / "diagram_png",
        out / "diagrams" / "source",
        out / "diagrams" / "rendered",
        out / "logs",
        out / "review",
    ]:
        child.mkdir(parents=True, exist_ok=True)


def _normalize_quality_profile(value: str) -> str:
    normalized = str(value or "default").strip().lower()
    return normalized if normalized in QUALITY_PROFILES else "default"


def _render_pdf_page_image(
    page: fitz.Page,
    *,
    page_number: int,
    dpi: int,
    out_dir: Path,
    source_pdf: str,
    source_mtime: float,
) -> tuple[str, str]:
    filename = f"page_{page_number:03d}.webp"
    target = out_dir / filename
    metadata_path = out_dir / f"page_{page_number:03d}.json"
    expected_metadata = {
        "version": 2,
        "source_pdf": source_pdf,
        "source_mtime": round(float(source_mtime or 0.0), 6),
        "page_number": int(page_number),
        "dpi": int(dpi),
        "width": round(float(page.rect.width or 0.0), 3),
        "height": round(float(page.rect.height or 0.0), 3),
        "format": "webp",
        "quality": 72,
        "method": 2,
    }
    if target.is_file() and metadata_path.is_file():
        try:
            current_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current_metadata = {}
        if all(current_metadata.get(key) == value for key, value in expected_metadata.items()):
            return str(Path("assets") / "page_images" / filename).replace("\\", "/"), "hit"
    try:
        zoom = max(72, int(dpi)) / 72.0
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        mode = "RGB" if pixmap.n < 4 else "RGBA"
        image = Image.frombytes(mode, (pixmap.width, pixmap.height), pixmap.samples)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(target, format="WEBP", quality=72, method=2)
        metadata_path.write_text(json.dumps(expected_metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(Path("assets") / "page_images" / filename).replace("\\", "/"), "miss"
    except Exception:
        return "", "error"


def _study_text_blocks(page: fitz.Page, *, page_number: int) -> list[StudyTextBlock]:
    rawdict_blocks = _study_text_blocks_from_rawdict(page, page_number=page_number)
    if rawdict_blocks:
        return rawdict_blocks
    return _study_text_blocks_from_dict(page, page_number=page_number)


def _study_text_blocks_from_rawdict(page: fitz.Page, *, page_number: int) -> list[StudyTextBlock]:
    try:
        raw = page.get_text("rawdict") or {}
    except Exception:
        return []
    blocks: list[StudyTextBlock] = []
    reading_order = 0
    for block_index, block in enumerate(raw.get("blocks", []) or []):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", []) or []):
            for span_index, span in enumerate(line.get("spans", []) or []):
                chars = _rawdict_span_chars(span)
                text = "".join(str(char.get("char") or "") for char in chars)
                normalized = _normalize_book_text(text)
                if not normalized:
                    continue
                bbox = [round(float(value), 3) for value in (span.get("bbox") or line.get("bbox") or block.get("bbox") or [0, 0, 0, 0])[:4]]
                block_type = "notation" if _looks_like_notation_text(normalized) else "text"
                diagnostics = _study_span_glyph_diagnostics(
                    page_number=page_number,
                    block_index=block_index,
                    line_index=line_index,
                    span_index=span_index,
                    text=text,
                    normalized_text=normalized,
                    font=str(span.get("font") or ""),
                    size=float(span.get("size") or 0.0),
                    bbox=bbox,
                    chars=chars,
                    block_type=block_type,
                )
                blocks.append(
                    StudyTextBlock(
                        page=page_number,
                        block_index=block_index,
                        line_index=line_index,
                        span_index=span_index,
                        reading_order=reading_order,
                        text=text,
                        normalized_text=normalized,
                        bbox=bbox,
                        font=str(span.get("font") or ""),
                        size=float(span.get("size") or 0.0),
                        type=block_type,
                        glyph_diagnostics=diagnostics,
                    )
                )
                reading_order += 1
    return _sorted_study_text_blocks(blocks)


def _study_text_blocks_from_dict(page: fitz.Page, *, page_number: int) -> list[StudyTextBlock]:
    raw = page.get_text("dict") or {}
    blocks: list[StudyTextBlock] = []
    reading_order = 0
    for block_index, block in enumerate(raw.get("blocks", []) or []):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", []) or []):
            for span_index, span in enumerate(line.get("spans", []) or []):
                text = str(span.get("text") or "")
                normalized = _normalize_book_text(text)
                if not normalized:
                    continue
                bbox = [round(float(value), 3) for value in (span.get("bbox") or line.get("bbox") or block.get("bbox") or [0, 0, 0, 0])[:4]]
                blocks.append(
                    StudyTextBlock(
                        page=page_number,
                        block_index=block_index,
                        line_index=line_index,
                        span_index=span_index,
                        reading_order=reading_order,
                        text=text,
                        normalized_text=normalized,
                        bbox=bbox,
                        font=str(span.get("font") or ""),
                        size=float(span.get("size") or 0.0),
                        type="notation" if _looks_like_notation_text(normalized) else "text",
                        glyph_diagnostics=_synthetic_span_glyph_diagnostics(
                            page_number=page_number,
                            block_index=block_index,
                            line_index=line_index,
                            span_index=span_index,
                            text=text,
                            normalized_text=normalized,
                            font=str(span.get("font") or ""),
                            bbox=bbox,
                            source="dict-no-raw-chars",
                        ),
                    )
                )
                reading_order += 1
    return _sorted_study_text_blocks(blocks)


def _sorted_study_text_blocks(blocks: list[StudyTextBlock]) -> list[StudyTextBlock]:
    blocks.sort(key=lambda item: (item.bbox[1] if len(item.bbox) > 1 else 0.0, item.bbox[0] if item.bbox else 0.0, item.reading_order))
    return [
        StudyTextBlock(**{**block.to_dict(), "reading_order": index})
        for index, block in enumerate(blocks)
    ]


def _rawdict_span_chars(span: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for char_index, char in enumerate(span.get("chars", []) or []):
        value = str(char.get("c") or "")
        bbox = [round(float(part), 3) for part in (char.get("bbox") or [0, 0, 0, 0])[:4]]
        origin = [round(float(part), 3) for part in (char.get("origin") or [])[:2]]
        result.append(
            {
                "char_index": char_index,
                "char": value,
                "codepoint": _codepoint_label(value),
                "bbox": bbox,
                "origin": origin,
            }
        )
    return result


def _study_span_glyph_diagnostics(
    *,
    page_number: int,
    block_index: int,
    line_index: int,
    span_index: int,
    text: str,
    normalized_text: str,
    font: str,
    size: float,
    bbox: list[float],
    chars: list[dict[str, Any]],
    block_type: str,
) -> list[dict[str, Any]]:
    reasons = _unmapped_notation_glyph_reasons(text)
    if not reasons:
        return []
    if block_type != "notation" and not _looks_like_notation_text(normalized_text):
        return []
    return [
        {
            "warning": UNMAPPED_CHESS_GLYPH_WARNING,
            "source": "pymupdf-rawdict",
            "page": page_number,
            "block_index": block_index,
            "line_index": line_index,
            "span_index": span_index,
            "font_name": font,
            "font_size": round(float(size or 0.0), 3),
            "bbox": list(bbox),
            "raw_text": _bounded_text(text),
            "normalized_text": _bounded_text(normalized_text),
            "context": _bounded_text(text, limit=120),
            "codepoints": [_codepoint_label(char.get("char")) for char in chars if str(char.get("char") or "")],
            "chars": chars[:80],
            "reasons": reasons,
            "mapping_status": "unmapped",
        }
    ]


def _synthetic_span_glyph_diagnostics(
    *,
    page_number: int,
    block_index: int,
    line_index: int,
    span_index: int,
    text: str,
    normalized_text: str,
    font: str,
    bbox: list[float],
    source: str,
) -> list[dict[str, Any]]:
    reasons = _unmapped_notation_glyph_reasons(text)
    if not reasons:
        return []
    return [
        {
            "warning": UNMAPPED_CHESS_GLYPH_WARNING,
            "source": source,
            "page": page_number,
            "block_index": block_index,
            "line_index": line_index,
            "span_index": span_index,
            "font_name": font,
            "bbox": list(bbox),
            "raw_text": _bounded_text(text),
            "normalized_text": _bounded_text(normalized_text),
            "context": _bounded_text(text, limit=120),
            "codepoints": [_codepoint_label(char) for char in text],
            "chars": [],
            "reasons": [*reasons, "raw_char_context_unavailable"],
            "mapping_status": "unmapped",
        }
    ]


def _html_study_text_blocks(html_page: dict[str, Any], *, page_number: int, start_order: int = 0) -> list[StudyTextBlock]:
    result: list[StudyTextBlock] = []
    blocks = list(html_page.get("blocks") or [])
    if not blocks and html_page.get("text"):
        blocks = [{"text": html_page.get("text"), "bbox": [0, 0, 0, 0], "block_index": 0, "line_index": 0}]
    for index, block in enumerate(blocks):
        text = str(block.get("text") or "")
        normalized = _normalize_book_text(text)
        if not normalized or _is_technical_audit_text(normalized):
            continue
        bbox = [round(float(value), 3) for value in (block.get("bbox") or [0, 0, 0, 0])[:4]]
        result.append(
            StudyTextBlock(
                page=page_number,
                block_index=int(block.get("block_index") or index),
                line_index=int(block.get("line_index") or 0),
                span_index=0,
                reading_order=start_order + index,
                text=text,
                normalized_text=normalized,
                bbox=bbox,
                font="html-assist",
                size=0.0,
                type="notation" if _looks_like_notation_text(normalized) else "text",
                glyph_diagnostics=_synthetic_span_glyph_diagnostics(
                    page_number=page_number,
                    block_index=int(block.get("block_index") or index),
                    line_index=int(block.get("line_index") or 0),
                    span_index=0,
                    text=text,
                    normalized_text=normalized,
                    font="html-assist",
                    bbox=bbox,
                    source="html-assist-no-raw-glyph-context",
                ),
            )
        )
    return result


def _should_apply_html_text_assist(pdf_blocks: list[StudyTextBlock], html_blocks: list[StudyTextBlock]) -> bool:
    if not html_blocks:
        return False
    pdf_chars = sum(len(block.normalized_text) for block in pdf_blocks)
    html_chars = sum(len(block.normalized_text) for block in html_blocks)
    if pdf_chars == 0:
        return True
    return html_chars > pdf_chars * 2


def _normalize_book_text(text: str) -> str:
    value = str(text or "").replace("\u00ad", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _is_technical_audit_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    lower = value.lower()
    technical_tokens = [
        "unmapped_chess_glyphs",
        "pgn_replay_errors",
        "move_number_jump",
        "move_number_regression",
        "side_to_move_mismatch",
        "quality_threshold_not_met",
        "status_policy",
    ]
    token_hits = sum(1 for token in technical_tokens if token in lower)
    snake_case_hits = len(re.findall(r"\b[a-z]+(?:_[a-z0-9]+){2,}\b", lower))
    if token_hits >= 2:
        return True
    if token_hits >= 1 and snake_case_hits >= 3:
        return True
    if snake_case_hits >= 8 and not re.search(
        r"\b(?:diagram|chapter|ex\.|white|black|king|queen|rook|bishop|knight|pawn)\b",
        lower,
    ):
        return True
    return False


def _paragraphs_from_blocks(blocks: list[StudyTextBlock]) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    previous_y: float | None = None
    for block in blocks:
        y = float(block.bbox[1] if len(block.bbox) > 1 else 0.0)
        if previous_y is not None and y - previous_y > max(10.0, float(block.size or 0.0) * 1.8) and current:
            paragraphs.append(_normalize_book_text(" ".join(current)))
            current = []
        current.append(block.normalized_text)
        previous_y = y
    if current:
        paragraphs.append(_normalize_book_text(" ".join(current)))
    return [paragraph for paragraph in paragraphs if paragraph]


def _layout_elements_from_text_blocks(blocks: list[StudyTextBlock], *, page_number: int) -> list[StudyLayoutElement]:
    elements: list[StudyLayoutElement] = []
    for index, block in enumerate(blocks):
        text = str(block.normalized_text or block.text or "").strip()
        if not text:
            continue
        element_type = _text_block_layout_type(block)
        source_kind = "html-assist" if block.font == "html-assist" else "pdf-text-layer"
        ref_id = f"p{page_number:03d}_b{block.block_index}_l{block.line_index}_s{block.span_index}"
        elements.append(
            StudyLayoutElement(
                type=element_type,
                page=page_number,
                bbox=_bbox4(block.bbox),
                reading_order=index,
                source_kind=source_kind,
                text=text,
                ref_id=ref_id,
                status="source",
            )
        )
    return [
        StudyLayoutElement(
            type=str(item.get("type") or "text"),
            page=int(item.get("page") or page_number),
            bbox=_bbox4(item.get("bbox") or []),
            reading_order=index,
            source_kind=str(item.get("source_kind") or "pdf-text-layer"),
            text=str(item.get("text") or ""),
            ref_id=str(item.get("ref_id") or ""),
            status=str(item.get("status") or "source"),
        )
        for index, item in enumerate(sort_study_layout_elements(elements))
    ]


def sort_study_layout_elements(
    elements: Iterable[dict[str, Any] | StudyLayoutElement],
    *,
    line_tolerance: float = 3.0,
) -> list[dict[str, Any]]:
    """Sort mixed text/diagram/notation elements in stable PDF reading order."""
    normalized = [_coerce_layout_element(element) for element in elements]
    tolerance = max(1.0, float(line_tolerance or 3.0))

    def sort_key(item: dict[str, Any]) -> tuple[int, int, float, int]:
        bbox = _bbox4(item.get("bbox") or [])
        y_bucket = int(round(float(bbox[1] or 0.0) / tolerance))
        return (
            int(item.get("page") or 0),
            y_bucket,
            float(bbox[0] or 0.0),
            int(item.get("reading_order") or 0),
        )

    return sorted(normalized, key=sort_key)


def _coerce_layout_element(element: dict[str, Any] | StudyLayoutElement) -> dict[str, Any]:
    if isinstance(element, StudyLayoutElement):
        return element.to_dict()
    item = dict(element)
    item["bbox"] = _bbox4(item.get("bbox") or [])
    item["page"] = int(item.get("page") or 0)
    item["reading_order"] = int(item.get("reading_order") or 0)
    item["type"] = str(item.get("type") or "text")
    item["source_kind"] = str(item.get("source_kind") or "unknown")
    return item


def _text_block_layout_type(block: StudyTextBlock) -> str:
    text = str(block.normalized_text or block.text or "").strip()
    if block.type == "notation" or _looks_like_notation_text(text):
        return "notation"
    if FINAL_TEST_RE.search(text) or any(pattern.search(text) for pattern in APPENDIX_PATTERNS.values()):
        return "heading"
    if EXERCISE_LABEL_RE.search(text) or FINAL_LABEL_RE.search(text):
        return "heading"
    if len(text) <= 90 and re.search(r"\b(?:chapter|diagram|exercises?)\b", text, re.IGNORECASE):
        return "heading"
    return "text"


def _is_reader_noise_text(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    technical_tokens = (
        "fen_not_recognized",
        "mass_side_to_move_unknown",
        "board_crop_quality=fail",
        "marker_crop_quality=fail",
        "side_to_move_unknown",
    )
    lowered = value.lower()
    if any(token in lowered for token in technical_tokens):
        return True
    if re.search(r"\b(?:board|side_marker|marker|raw|debug)?_?bbox\s*[:=]", lowered):
        return True
    if len(value) <= 3 and re.fullmatch(r"[a-h1-8*.,:;!?\-]+", value, flags=re.IGNORECASE):
        return True
    if len(value) <= 6 and re.fullmatch(r"[a-z]{0,2}\W+[a-z0-9]{0,2}", value, flags=re.IGNORECASE):
        return True
    compact = re.sub(r"\s+", "", value)
    alpha_count = len(re.findall(r"[A-Za-z]", compact))
    if len(compact) <= 2 and alpha_count == len(compact):
        return True
    if len(compact) <= 6 and alpha_count <= 2 and re.search(r"[^A-Za-z0-9]", compact):
        return True
    return False


def _bbox4(value: Any) -> list[float]:
    values: list[float] = []
    if isinstance(value, (list, tuple)):
        for item in value[:4]:
            try:
                values.append(float(item))
            except (TypeError, ValueError):
                values.append(0.0)
    while len(values) < 4:
        values.append(0.0)
    return values[:4]


def _union_bboxes(values: Iterable[Any]) -> list[float]:
    boxes = [_bbox4(value) for value in values if value]
    boxes = [box for box in boxes if any(box)]
    if not boxes:
        return [0.0, 0.0, 0.0, 0.0]
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    return [x0, y0, x1, y1]


def _book_text_markdown(pages: list[dict[str, Any]]) -> str:
    lines = ["# Book Text", ""]
    for page in pages:
        lines.append(f"## Page {page.get('page')}")
        lines.append("")
        paragraphs = page.get("paragraphs") or []
        if not paragraphs:
            lines.append("_No extractable text._")
        else:
            for paragraph in paragraphs:
                lines.append(str(paragraph))
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _study_diagram_record(record: dict[str, Any]) -> StudyDiagram:
    return StudyDiagram(
        id=str(record.get("diagram_id") or record.get("id") or ""),
        page=int(record.get("page") or 0),
        visual_order_on_page=int(record.get("visual_order_on_page") or 0),
        bbox=[float(value) for value in (record.get("bbox") or [0, 0, 0, 0])[:4]],
        label=str(record.get("label") or record.get("diagram_id") or ""),
        side_to_move=str(record.get("side_to_move") or "w"),
        fen=str(record.get("fen") or ""),
        fen_candidate=str(record.get("fen_candidate") or ""),
        status=str(record.get("status") or "needs_review"),
        confidence=float(record.get("confidence") or 0.0),
        source_crop=str(record.get("source_crop") or ""),
        board_crop_path=str(record.get("board_crop_path") or record.get("source_crop") or ""),
        side_marker_crop_path=str(record.get("side_marker_crop_path") or ""),
        side_marker_search_crop_path=str(record.get("side_marker_search_crop_path") or ""),
        marker_search_zone_preview_path=str(record.get("marker_search_zone_preview_path") or record.get("side_marker_search_crop_path") or ""),
        marker_search_zone_preview_bbox=_bbox4(record.get("marker_search_zone_preview_bbox") or record.get("side_marker_search_bbox") or []),
        side_marker_review_crop_path=str(
            record.get("side_marker_review_crop_path")
            or record.get("side_marker_crop_path")
            or record.get("side_marker_search_crop_path")
            or ""
        ),
        side_marker_review_crop_kind=str(record.get("side_marker_review_crop_kind") or ""),
        debug_overlay_path=str(record.get("debug_overlay_path") or ""),
        board_bbox=_bbox4(record.get("board_bbox") or record.get("bbox_xyxy") or []),
        side_marker_bbox=_bbox4(record.get("side_marker_bbox") or []),
        marker_search_zones=dict(record.get("marker_search_zones") or {}),
        selected_marker_zone=str(record.get("selected_marker_zone") or ""),
        marker_bbox=_bbox4(record.get("marker_bbox") or []),
        marker_crop_bbox=_bbox4(record.get("marker_crop_bbox") or record.get("marker_bbox") or []),
        board_crop_quality=str(record.get("board_crop_quality") or ""),
        board_crop_fail_reason=[str(reason) for reason in record.get("board_crop_fail_reason") or []],
        marker_crop_quality=str(record.get("marker_crop_quality") or ""),
        marker_crop_fail_reason=[str(reason) for reason in record.get("marker_crop_fail_reason") or []],
        side_to_move_detected=str(record.get("side_to_move_detected") or ""),
        side_to_move_confidence=record.get("side_to_move_confidence", ""),
        manual_review_required=bool(record.get("manual_review_required", True)),
        manual_review_reason=str(record.get("manual_review_reason") or ""),
        side_to_move_status=str(record.get("side_to_move_status") or ""),
        side_to_move_evidence=str(record.get("side_to_move_evidence") or ""),
        side_marker_symbol=str(record.get("side_marker_symbol") or ""),
        side_marker_status=str(record.get("side_marker_status") or ""),
        side_marker_source=str(record.get("side_marker_source") or ""),
        side_marker_confidence=record.get("side_marker_confidence", ""),
        side_marker_assignment_trace=dict(record.get("side_marker_assignment_trace") or {}),
        strict_fen_side_evidence_trusted=bool(record.get("strict_fen_side_evidence_trusted")),
        placement=str(record.get("placement") or record.get("placement_fen") or ""),
        placement_status=str(record.get("placement_status") or record.get("placement_runtime_status") or ""),
        full_fen=str(record.get("full_fen") or ""),
        full_fen_status=str(record.get("full_fen_status") or record.get("full_fen_runtime_status") or ""),
        fen_suppressed_reason=str(record.get("fen_suppressed_reason") or ""),
        rendered_svg=str(record.get("rendered_svg") or ""),
        rendered_png=str(record.get("rendered_png") or ""),
        review_reason=str(record.get("review_reason") or record.get("reason") or ""),
        warnings=[str(warning) for warning in record.get("warnings") or []],
    )


def _visual_order_for_diagram(record: dict[str, Any], previous: list[dict[str, Any]]) -> int:
    page = int(record.get("page") or 0)
    return len([item for item in previous if int(item.get("page") or 0) == page]) + 1


def _render_valid_fen_assets(fen: str, out_dir: Path, *, diagram_id: str) -> dict[str, str]:
    valid, warnings = validate_fen(fen)
    if not valid or warnings:
        return {}
    try:
        import chess

        chess.Board(fen)
    except Exception:
        return {}
    svg_rel = _render_fen_svg(fen, out_dir, diagram_id=diagram_id)
    png_rel = _render_fen_png(fen, out_dir, diagram_id=diagram_id)
    return {"svg": svg_rel, "png": png_rel}


def _render_fen_svg(fen: str, out_dir: Path, *, diagram_id: str) -> str:
    placement = fen.split()[0]
    board = _placement_to_board(placement)
    size = 256
    cell = size // 8
    piece_map = {
        "K": "K", "Q": "Q", "R": "R", "B": "B", "N": "N", "P": "P",
        "k": "k", "q": "q", "r": "r", "b": "b", "n": "n", "p": "p",
    }
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img">',
        '<rect width="256" height="256" fill="#f1d9b5"/>',
    ]
    for row in range(8):
        for col in range(8):
            fill = "#f5e6c8" if (row + col) % 2 == 0 else "#8b5a34"
            x = col * cell
            y = row * cell
            parts.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" fill="{fill}"/>')
            piece = board[row][col]
            if piece:
                color = "#15110d" if piece.islower() else "#fffaf0"
                stroke = "#15110d" if piece.isupper() else "#fffaf0"
                parts.append(
                    f'<text x="{x + cell / 2}" y="{y + cell * .68}" text-anchor="middle" '
                    f'font-family="Georgia,serif" font-size="22" font-weight="700" '
                    f'fill="{color}" stroke="{stroke}" stroke-width=".45">{piece_map[piece]}</text>'
                )
    parts.append("</svg>")
    target_dir = out_dir / "assets" / "diagram_svg"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(diagram_id)}.svg"
    (target_dir / filename).write_text("\n".join(parts), encoding="utf-8")
    return str(Path("assets") / "diagram_svg" / filename).replace("\\", "/")


def _render_fen_png(fen: str, out_dir: Path, *, diagram_id: str) -> str:
    board = _placement_to_board(fen.split()[0])
    size = 256
    cell = size // 8
    image = Image.new("RGB", (size, size), "#f1d9b5")
    draw = ImageDraw.Draw(image)
    for row in range(8):
        for col in range(8):
            fill = "#f5e6c8" if (row + col) % 2 == 0 else "#8b5a34"
            x0 = col * cell
            y0 = row * cell
            draw.rectangle([x0, y0, x0 + cell, y0 + cell], fill=fill)
            piece = board[row][col]
            if piece:
                draw.text((x0 + 11, y0 + 8), piece, fill="#15110d" if piece.islower() else "#fffaf0")
    target_dir = out_dir / "assets" / "diagram_png"
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_safe_filename(diagram_id)}.png"
    image.save(target_dir / filename, format="PNG")
    return str(Path("assets") / "diagram_png" / filename).replace("\\", "/")


def _placement_to_board(placement: str) -> list[list[str]]:
    board: list[list[str]] = []
    for rank in placement.split("/"):
        row: list[str] = []
        for char in rank:
            if char.isdigit():
                row.extend([""] * int(char))
            else:
                row.append(char)
        row = row[:8] + [""] * max(0, 8 - len(row))
        board.append(row)
    while len(board) < 8:
        board.append([""] * 8)
    return board[:8]


def _positions_by_page(positions: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {}
    for item in positions.get("positions", []) or []:
        page = int(item.get("diagram_page") or 0)
        result.setdefault(page, []).append(item)
    return result


def _looks_like_notation_text(text: str) -> bool:
    value = str(text or "")
    return bool(re.search(r"\b\d{1,3}\.(?:\.\.)?\s*[@\ufffdA-Za-zKQRBN0O][@A-Za-z0-9=+#xO\-]*", value))


def _normalize_notation_text(text: str) -> str:
    value = _normalize_book_text(text)
    replacements = {
        "0-0-0": "O-O-O",
        "0-0": "O-O",
        "0-O": "O-O",
        "O-0": "O-O",
        "\u2654": "K",
        "\u2655": "Q",
        "\u2656": "R",
        "\u2657": "B",
        "\u2658": "N",
        "\u2659": "",
        "\u265a": "K",
        "\u265b": "Q",
        "\u265c": "R",
        "\u265d": "B",
        "\u265e": "N",
        "\u265f": "",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value


def _contains_unmapped_notation_glyphs(text: str) -> bool:
    return bool(_unmapped_notation_glyph_reasons(text))


def _unmapped_notation_glyph_reasons(text: str) -> list[str]:
    value = str(text or "")
    reasons: list[str] = []
    if "\ufffd" in value:
        reasons.append("replacement_character")
    if any(0xE000 <= ord(char) <= 0xF8FF for char in value):
        reasons.append("private_use_area")
    if "@" in value and _looks_like_notation_text(value):
        reasons.append("at_sign_in_notation")
    if SUSPECT_NOTATION_GLYPH_RE.search(value) and _looks_like_notation_text(value):
        reasons.append("suspicious_mojibake_notation_token")
    return sorted(set(reasons))


def _notation_glyph_diagnostics_from_blocks(
    blocks: list[dict[str, Any]],
    *,
    page_number: int,
    fallback_text: str,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for block in blocks:
        for item in block.get("glyph_diagnostics") or []:
            if not isinstance(item, dict):
                continue
            diagnostics.append({**item, "page": int(item.get("page") or page_number)})
            if len(diagnostics) >= NOTATION_GLYPH_DIAGNOSTIC_LIMIT:
                return diagnostics
    if not diagnostics and _contains_unmapped_notation_glyphs(fallback_text):
        diagnostics.append(
            {
                "warning": UNMAPPED_CHESS_GLYPH_WARNING,
                "source": "notation-fragment-fallback",
                "page": page_number,
                "font_name": "unknown",
                "bbox": [0, 0, 0, 0],
                "raw_text": _bounded_text(fallback_text),
                "normalized_text": _bounded_text(_normalize_notation_text(fallback_text)),
                "context": _bounded_text(fallback_text, limit=120),
                "codepoints": [_codepoint_label(char) for char in fallback_text[:120]],
                "chars": [],
                "reasons": [*_unmapped_notation_glyph_reasons(fallback_text), "source_block_context_unavailable"],
                "mapping_status": "unmapped",
            }
        )
    return diagnostics[:NOTATION_GLYPH_DIAGNOSTIC_LIMIT]


def _needs_raw_glyph_context(diagnostics: list[dict[str, Any]]) -> bool:
    if not diagnostics:
        return False
    has_rawdict = any(str(item.get("source") or "") == "pymupdf-rawdict" for item in diagnostics)
    has_unavailable = any("raw_char_context_unavailable" in (item.get("reasons") or []) for item in diagnostics)
    return has_unavailable and not has_rawdict


def _rawdict_glyph_context_for_page(
    page: dict[str, Any],
    *,
    page_number: int,
    fallback_text: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    fallback_tokens = set(_glyph_mapping_tokens(fallback_text))
    for block in page.get("blocks") or []:
        if str(block.get("font") or "") == "html-assist":
            continue
        diagnostics = [item for item in block.get("glyph_diagnostics") or [] if isinstance(item, dict)]
        if diagnostics:
            for item in diagnostics:
                result.append({**item, "page": int(item.get("page") or page_number), "linked_from_html_assist": True})
                if len(result) >= NOTATION_GLYPH_SAMPLE_LIMIT:
                    return result
            continue
        text = str(block.get("text") or block.get("normalized_text") or "")
        if not text or not _looks_like_notation_text(text):
            continue
        block_tokens = set(_glyph_mapping_tokens(text))
        if fallback_tokens and block_tokens and not (fallback_tokens & block_tokens):
            continue
        chars = [
            {
                "char_index": index,
                "char": char,
                "codepoint": _codepoint_label(char),
                "bbox": list(block.get("bbox") or [0, 0, 0, 0]),
                "origin": [],
            }
            for index, char in enumerate(text[:80])
        ]
        result.append(
            {
                "warning": UNMAPPED_CHESS_GLYPH_WARNING,
                "source": "pymupdf-rawdict-page-context",
                "page": page_number,
                "block_index": int(block.get("block_index") or 0),
                "line_index": int(block.get("line_index") or 0),
                "span_index": int(block.get("span_index") or 0),
                "font_name": str(block.get("font") or "unknown"),
                "font_size": round(float(block.get("size") or 0.0), 3),
                "bbox": list(block.get("bbox") or [0, 0, 0, 0]),
                "raw_text": _bounded_text(text),
                "normalized_text": _bounded_text(str(block.get("normalized_text") or text)),
                "context": _bounded_text(text, limit=120),
                "codepoints": [_codepoint_label(char) for char in text[:120]],
                "chars": chars,
                "reasons": [*_unmapped_notation_glyph_reasons(text), "rawdict_page_context"],
                "mapping_status": "unmapped",
                "linked_from_html_assist": True,
            }
        )
        if len(result) >= NOTATION_GLYPH_SAMPLE_LIMIT:
            break
    return result


def _merge_glyph_diagnostics(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in [*first, *second]:
        key = (
            item.get("source"),
            item.get("page"),
            item.get("block_index"),
            item.get("line_index"),
            item.get("span_index"),
            item.get("raw_text"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= NOTATION_GLYPH_DIAGNOSTIC_LIMIT:
            break
    return merged


def _parse_page_filter(value: str) -> set[int] | None:
    text = str(value or "").strip()
    if not text:
        return None
    selected: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = _safe_int(start_text)
            end = _safe_int(end_text)
            if not start or not end:
                continue
            if end < start:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            page = _safe_int(token)
            if page:
                selected.add(page)
    return selected


def _page_filter_allows(page_filter: set[int] | None, page_number: int) -> bool:
    return page_filter is None or int(page_number) in page_filter


def _write_notation_glyph_diagnostics(out_dir: Path, fragments: list[dict[str, Any]]) -> None:
    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    diagnostics: list[dict[str, Any]] = []
    for fragment in fragments:
        for item in fragment.get("glyph_diagnostics") or []:
            if not isinstance(item, dict):
                continue
            diagnostics.append(
                {
                    **item,
                    "fragment_id": fragment.get("id"),
                    "fragment_status": fragment.get("status"),
                    "fragment_warnings": fragment.get("warnings") or [],
                }
            )
            if len(diagnostics) >= NOTATION_GLYPH_DIAGNOSTIC_LIMIT:
                break
        if len(diagnostics) >= NOTATION_GLYPH_DIAGNOSTIC_LIMIT:
            break
    font_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    page_counts: dict[str, int] = {}
    rawdict_context_count = 0
    raw_context_unavailable_count = 0
    for item in diagnostics:
        font = str(item.get("font_name") or "unknown")
        font_counts[font] = font_counts.get(font, 0) + 1
        page = str(item.get("page") or "unknown")
        page_counts[page] = page_counts.get(page, 0) + 1
        if str(item.get("source") or "").startswith("pymupdf-rawdict"):
            rawdict_context_count += 1
        for reason in item.get("reasons") or []:
            reason_key = str(reason)
            reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1
            if reason_key == "raw_char_context_unavailable":
                raw_context_unavailable_count += 1
    glyph_mapping_candidates = _build_glyph_mapping_candidates(diagnostics)
    payload = {
        "diagnostic_count": len(diagnostics),
        "evidence_only": True,
        "requires_human_confirmation": True,
        "policy": "Build deterministic font->glyph->SAN maps only from verified glyph context; AI suggestions cannot mark PGN accepted.",
        "font_counts": font_counts,
        "page_counts": page_counts,
        "reason_counts": reason_counts,
        "rawdict_context_count": rawdict_context_count,
        "raw_context_unavailable_count": raw_context_unavailable_count,
        "raw_glyph_context_available": rawdict_context_count > 0,
        "glyph_mapping_candidate_count": int(glyph_mapping_candidates.get("candidate_count") or 0),
        "samples": diagnostics[:NOTATION_GLYPH_SAMPLE_LIMIT],
    }
    _write_json(out_dir / "notation_glyph_diagnostics.json", payload)
    _write_jsonl(out_dir / "notation_glyph_diagnostics.jsonl", diagnostics)
    _write_json(review_dir / "glyph_mapping_candidates.json", glyph_mapping_candidates)
    (review_dir / "glyph_mapping_review.html").write_text(
        _glyph_mapping_review_html(glyph_mapping_candidates),
        encoding="utf-8",
    )
    _write_optional_deepseek_glyph_audit(out_dir, payload, glyph_mapping_candidates)


def _build_glyph_mapping_candidates(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for item in diagnostics:
        font = str(item.get("font_name") or "unknown")
        tokens = _glyph_mapping_tokens(str(item.get("raw_text") or item.get("context") or ""))
        if not tokens:
            tokens = ["<whole-span>"]
        for token in tokens:
            key = (font, token)
            cluster = clusters.setdefault(
                key,
                {
                    "font_name": font,
                    "token": token,
                    "count": 0,
                    "pages": set(),
                    "reasons": set(),
                    "codepoints": set(),
                    "examples": [],
                    "mapping_status": "candidate_requires_human_confirmation",
                    "ai_candidate": None,
                    "ai_confidence": None,
                    "ai_reason": "",
                },
            )
            cluster["count"] += 1
            if item.get("page"):
                cluster["pages"].add(int(item.get("page") or 0))
            for reason in item.get("reasons") or []:
                cluster["reasons"].add(str(reason))
            for codepoint in item.get("codepoints") or []:
                if codepoint:
                    cluster["codepoints"].add(str(codepoint))
            if len(cluster["examples"]) < 5:
                cluster["examples"].append(
                    {
                        "page": item.get("page"),
                        "source": item.get("source"),
                        "context": item.get("context") or item.get("raw_text"),
                        "bbox": item.get("bbox") or [0, 0, 0, 0],
                    }
                )
    candidates = []
    for cluster in clusters.values():
        candidates.append(
            {
                **cluster,
                "pages": sorted(page for page in cluster["pages"] if page),
                "reasons": sorted(cluster["reasons"]),
                "codepoints": sorted(cluster["codepoints"]),
            }
        )
    candidates.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get("font_name") or ""), str(item.get("token") or "")))
    return {
        "evidence_only": True,
        "requires_human_confirmation": True,
        "mutates_output": False,
        "policy": "Use these clusters only to design deterministic font/token/glyph mappings with regression tests.",
        "candidate_count": len(candidates),
        "candidates": candidates[:200],
    }


def _glyph_mapping_tokens(text: str) -> list[str]:
    value = str(text or "")
    tokens: list[str] = []
    for match in re.finditer(r"[^\s{}()\[\],;:]+", value):
        token = match.group(0).strip()
        if not token:
            continue
        if (
            "\ufffd" in token
            or "@" in token
            or ">" in token
            or "%" in token
            or SUSPECT_NOTATION_GLYPH_RE.search(token)
        ):
            tokens.append(token[:80])
    return tokens[:20]


def _glyph_mapping_review_html(payload: dict[str, Any]) -> str:
    candidates = list(payload.get("candidates") or [])
    rows = "\n".join(_glyph_mapping_candidate_row(candidate) for candidate in candidates) or "<tr><td colspan=\"7\">No glyph mapping candidates.</td></tr>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Chess Glyph Mapping Review</title>
  <style>
    body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; background:#f0e4d0; color:#21170f; }}
    header {{ padding:1rem 1.25rem; background:#24180f; color:#fff7ea; position:sticky; top:0; }}
    main {{ padding:1rem; overflow:auto; }}
    table {{ border-collapse:collapse; width:100%; background:#fffaf0; }}
    th, td {{ border:1px solid #d7c4aa; padding:.45rem .55rem; vertical-align:top; }}
    th {{ background:#f2dec1; text-align:left; }}
    code {{ font-family:'Courier New', monospace; }}
    pre {{ white-space:pre-wrap; max-width:46rem; margin:0; }}
  </style>
</head>
<body>
  <header>
    <h1>Chess Glyph Mapping Review</h1>
    <p>{len(candidates)} candidate cluster(s). Evidence only; do not use as accepted PGN without deterministic mapping and regression tests.</p>
  </header>
  <main>
    <table>
      <thead><tr><th>Font</th><th>Token</th><th>Count</th><th>Pages</th><th>Reasons</th><th>Codepoints</th><th>Examples</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </main>
</body>
</html>"""


def _glyph_mapping_candidate_row(candidate: dict[str, Any]) -> str:
    examples = "\n\n".join(str(example.get("context") or "") for example in (candidate.get("examples") or [])[:3])
    return (
        "<tr>"
        f"<td>{html.escape(str(candidate.get('font_name') or ''))}</td>"
        f"<td><code>{html.escape(str(candidate.get('token') or ''))}</code></td>"
        f"<td>{html.escape(str(candidate.get('count') or 0))}</td>"
        f"<td>{html.escape(', '.join(str(page) for page in candidate.get('pages') or []))}</td>"
        f"<td>{html.escape(', '.join(str(reason) for reason in candidate.get('reasons') or []))}</td>"
        f"<td><code>{html.escape(', '.join(str(codepoint) for codepoint in candidate.get('codepoints') or []))}</code></td>"
        f"<td><pre>{html.escape(examples)}</pre></td>"
        "</tr>"
    )


def _write_optional_deepseek_glyph_audit(out_dir: Path, glyph_payload: dict[str, Any], mapping_candidates: dict[str, Any]) -> None:
    try:
        from deepseek_quality_provider import build_deepseek_audit_payload, build_deepseek_audit_provider_from_env

        provider = build_deepseek_audit_provider_from_env(cwd=Path.cwd())
        if provider is None:
            return
        audit = build_deepseek_audit_payload(
            provider=provider,
            source_title="chess-study glyph mapping review",
            glyph_payload={**glyph_payload, "glyph_mapping_candidates": mapping_candidates},
            records=[],
            diagrams=[],
            conversion_quality={},
        )
        if audit:
            _write_json(out_dir / "deepseek_audit.json", audit)
    except Exception as exc:
        _write_json(
            out_dir / "deepseek_audit.json",
            {
                "provider": "deepseek-audit",
                "mode": "evidence_only",
                "status": "failed",
                "error": str(exc),
                "mutates_output": False,
            },
        )


def _load_ocr_glyph_mapping(path: str | Path | None) -> dict[str, Any]:
    mapping_path = Path(path) if path else None
    if mapping_path is None or not mapping_path.is_file():
        return {
            "source": str(mapping_path or ""),
            "accepted_count": 0,
            "draft_count": 0,
            "rejected_count": 0,
            "accepted": {},
        }
    try:
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "source": str(mapping_path),
            "accepted_count": 0,
            "draft_count": 0,
            "rejected_count": 0,
            "accepted": {},
            "load_error": "invalid_json",
        }
    rows = payload.get("mappings") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        rows = []
    accepted: dict[str, dict[str, Any]] = {}
    counts = {"accepted": 0, "draft": 0, "rejected": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = str(row.get("token") or "").strip()
        replacement = str(row.get("replacement") or "").strip()
        status = str(row.get("status") or "draft").strip().lower()
        scope = str(row.get("scope") or "ocr_only").strip().lower()
        if status not in counts:
            status = "draft"
        counts[status] += 1
        if status != "accepted" or not token or not replacement:
            continue
        if scope not in {"ocr_only", "html-assist", "all", "global"}:
            continue
        accepted[token] = {
            "token": token,
            "replacement": replacement,
            "scope": scope,
            "status": status,
            "examples": row.get("examples") or [],
            "reviewer_note": row.get("reviewer_note") or row.get("reviewer_notes") or "",
        }
    return {
        "source": str(mapping_path),
        "accepted_count": len(accepted),
        "draft_count": counts["draft"],
        "rejected_count": counts["rejected"],
        "accepted": accepted,
    }


def _apply_ocr_glyph_mapping(text: str, mapping: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    mapped = str(text or "")
    applied: list[dict[str, Any]] = []
    accepted = mapping.get("accepted") or {}
    for token, row in sorted(accepted.items(), key=lambda item: (-len(str(item[0])), str(item[0]))):
        token_text = str(token or "")
        replacement = str((row or {}).get("replacement") or "")
        if not token_text or not replacement or token_text not in mapped:
            continue
        count = mapped.count(token_text)
        mapped = mapped.replace(token_text, replacement)
        applied.append(
            {
                "token": token_text,
                "replacement": replacement,
                "count": count,
                "scope": (row or {}).get("scope") or "ocr_only",
            }
        )
    blockers = sorted(set(_glyph_mapping_tokens(mapped)))
    return (
        mapped,
        {
            "applied_count": sum(int(item.get("count") or 0) for item in applied),
            "applied": applied,
            "unmapped_tokens": blockers,
        },
    )


def _mark_glyph_diagnostics_mapped(
    diagnostics: list[dict[str, Any]],
    mapping_result: dict[str, Any],
) -> list[dict[str, Any]]:
    applied = list(mapping_result.get("applied") or [])
    marked: list[dict[str, Any]] = []
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        marked.append(
            {
                **item,
                "mapping_status": "manual_ocr_mapped",
                "ocr_mapping_applied": applied,
                "validation_status": "mapped_candidate_requires_pgn_replay",
            }
        )
    return marked


def _raw_glyph_context_mode(fragment: dict[str, Any]) -> str:
    diagnostics = [item for item in fragment.get("glyph_diagnostics") or [] if isinstance(item, dict)]
    if not diagnostics:
        return "unknown"
    has_rawdict = any(str(item.get("source") or "").startswith("pymupdf-rawdict") for item in diagnostics)
    has_unavailable = any("raw_char_context_unavailable" in (item.get("reasons") or []) for item in diagnostics)
    has_fallback = any(
        str(item.get("source") or "") in {"notation-fragment-fallback", "html-assist"}
        or "source_block_context_unavailable" in (item.get("reasons") or [])
        for item in diagnostics
    )
    if has_rawdict and (has_unavailable or has_fallback):
        return "mixed"
    if has_rawdict:
        return "rawdict"
    if has_unavailable or has_fallback:
        return "ocr_only"
    return "unknown"


def _raw_glyph_context_mode_for_fragments(fragments: list[dict[str, Any]]) -> str:
    modes = {str(item.get("raw_glyph_context_mode") or _raw_glyph_context_mode(item)) for item in fragments if isinstance(item, dict)}
    modes.discard("unknown")
    if not modes:
        return "unknown"
    if len(modes) > 1:
        return "mixed"
    return next(iter(modes))


def _write_glyph_mapping_template_and_blockers(
    out_dir: Path,
    fragments: list[dict[str, Any]],
    glyph_mapping: dict[str, Any],
) -> None:
    review_dir = out_dir / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    diagnostics: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        for item in fragment.get("glyph_diagnostics") or []:
            if isinstance(item, dict):
                diagnostics.append({**item, "fragment_id": fragment.get("id")})
        token_blockers = list(fragment.get("unmapped_token_blockers") or [])
        if token_blockers or UNMAPPED_CHESS_GLYPH_WARNING in (fragment.get("warnings") or []):
            blockers.append(
                {
                    "fragment_id": fragment.get("id"),
                    "page": fragment.get("page"),
                    "status": fragment.get("status"),
                    "warnings": fragment.get("warnings") or [],
                    "unmapped_token_blockers": token_blockers,
                    "raw_glyph_context_mode": fragment.get("raw_glyph_context_mode") or "unknown",
                    "raw_text": _bounded_text(str(fragment.get("raw_text") or ""), limit=400),
                    "normalized_text": _bounded_text(str(fragment.get("normalized_text") or ""), limit=400),
                }
            )
    candidate_payload = _build_glyph_mapping_candidates(diagnostics[:NOTATION_GLYPH_DIAGNOSTIC_LIMIT])
    accepted_tokens = set((glyph_mapping.get("accepted") or {}).keys())
    mappings: list[dict[str, Any]] = []
    for candidate in candidate_payload.get("candidates") or []:
        token = str(candidate.get("token") or "")
        if not token or token in accepted_tokens:
            continue
        mappings.append(
            {
                "token": token,
                "replacement": "",
                "scope": "ocr_only",
                "status": "draft",
                "examples": candidate.get("examples") or [],
                "reviewer_note": "",
            }
        )
        if len(mappings) >= 100:
            break
    template = {
        "schema": "kindlemaster.ocr_glyph_mapping.v1",
        "evidence_only": True,
        "instructions": "Fill replacement and set status=accepted only after manual confirmation; draft/rejected mappings never affect PGN.",
        "mappings": mappings,
    }
    _write_json(review_dir / "glyph_mapping_manual.template.json", template)
    _write_json(
        review_dir / "unmapped_token_blockers.json",
        {
            "blocker_count": len(blockers),
            "raw_glyph_context_mode": _raw_glyph_context_mode_for_fragments(fragments),
            "ocr_token_mappings_loaded": int(glyph_mapping.get("accepted_count") or 0),
            "fragments": blockers[:500],
        },
    )


def _load_diagram_review_labels(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    labels: dict[str, dict[str, Any]] = {}
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                diagram_id = str(row.get("diagram_id") or "").strip()
                label = str(row.get("manual_label") or "").strip()
                if diagram_id and label:
                    labels[diagram_id] = {**row, "manual_label": label, "label_source": str(path)}
        return labels
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        diagram_id = str(row.get("diagram_id") or "").strip()
        label = str(row.get("manual_label") or "").strip()
        if diagram_id and label:
            labels[diagram_id] = {**row, "manual_label": label, "label_source": str(path)}
    return labels


def _apply_diagram_manual_label(record: dict[str, Any], labels: dict[str, dict[str, Any]]) -> None:
    diagram_id = str(record.get("diagram_id") or record.get("id") or "")
    row = labels.get(diagram_id)
    if not row:
        return
    label = str(row.get("manual_label") or "").strip()
    allowed = {"correct_diagram", "false_positive", "cropped_diagram", "uncertain"}
    if label not in allowed:
        return
    record["manual_label"] = label
    record["manual_label_source"] = row.get("label_source") or ""
    record["manual_reviewer_notes"] = row.get("reviewer_notes") or row.get("notes") or ""
    if label == "false_positive":
        record["fen"] = ""
        record["fen_candidate"] = ""
        record["status"] = "needs_review"
        record["reason"] = "manual_false_positive"
        record["review_only"] = True
        record["warnings"] = sorted(set([*list(record.get("warnings") or []), "manual_false_positive"]))
    elif label == "cropped_diagram":
        record["status"] = "needs_review"
        record["reason"] = record.get("reason") or "manual_cropped_diagram"
        record["warnings"] = sorted(set([*list(record.get("warnings") or []), "manual_cropped_diagram"]))
    elif label == "uncertain":
        record["status"] = "needs_review"
        record["reason"] = record.get("reason") or "manual_uncertain_diagram"
        record["warnings"] = sorted(set([*list(record.get("warnings") or []), "manual_uncertain_diagram"]))


def _diagram_manual_label_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        label = str(record.get("manual_label") or "")
        if label:
            counts[label] = counts.get(label, 0) + 1
    return counts


def _empty_diagram_alignment_payload(out_dir: Path) -> dict[str, Any]:
    return {
        "enabled": False,
        "html": str(out_dir / "review" / "diagram_alignment_review.html"),
        "candidate_count": 0,
        "alignment_improved_count": 0,
    }


def _write_diagram_alignment_review(out_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    review_dir = out_dir / "review"
    asset_dir = out_dir / "assets" / "diagram_alignment"
    review_dir.mkdir(parents=True, exist_ok=True)
    asset_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for record in records:
        if record.get("manual_label") not in {"correct_diagram", "cropped_diagram"}:
            continue
        source_path = Path(str(record.get("image_path") or ""))
        if not source_path.is_file():
            continue
        variants = _diagram_alignment_variants(source_path, asset_dir, str(record.get("diagram_id") or source_path.stem))
        rows.append(
            {
                "diagram_id": record.get("diagram_id"),
                "manual_label": record.get("manual_label"),
                "page": record.get("page"),
                "bbox": record.get("bbox") or [0, 0, 0, 0],
                "source": record.get("image_href") or record.get("source_crop") or "",
                "variants": variants,
            }
        )
    html_path = review_dir / "diagram_alignment_review.html"
    html_path.write_text(_diagram_alignment_review_html(rows), encoding="utf-8")
    return {
        "enabled": True,
        "html": str(html_path),
        "candidate_count": len(rows),
        "alignment_improved_count": 0,
    }


def _diagram_alignment_variants(source_path: Path, asset_dir: Path, diagram_id: str) -> list[dict[str, str]]:
    variants: list[dict[str, str]] = []
    try:
        image = Image.open(source_path).convert("RGB")
    except Exception:
        return variants
    width, height = image.size
    boxes = {
        "tight": _image_crop_box(width, height, 0.03),
        "inner-grid": _image_crop_box(width, height, 0.08),
        "remove-coordinates": _image_crop_box(width, height, 0.12),
        "center-square": _center_square_box(width, height),
    }
    for variant, box in boxes.items():
        crop = image.crop(box)
        filename = f"{_safe_filename(diagram_id)}-{variant}.webp"
        target = asset_dir / filename
        crop.save(target, format="WEBP", quality=88, method=6)
        variants.append({"variant": variant, "href": str(Path("assets") / "diagram_alignment" / filename).replace("\\", "/")})
    expanded = Image.new("RGB", (max(1, int(width * 1.1)), max(1, int(height * 1.1))), "white")
    expanded.paste(image, ((expanded.width - width) // 2, (expanded.height - height) // 2))
    filename = f"{_safe_filename(diagram_id)}-expand.webp"
    target = asset_dir / filename
    expanded.save(target, format="WEBP", quality=88, method=6)
    variants.append({"variant": "expand", "href": str(Path("assets") / "diagram_alignment" / filename).replace("\\", "/")})
    return variants


def _image_crop_box(width: int, height: int, margin_ratio: float) -> tuple[int, int, int, int]:
    dx = int(width * margin_ratio)
    dy = int(height * margin_ratio)
    return (min(dx, width - 1), min(dy, height - 1), max(width - dx, 1), max(height - dy, 1))


def _center_square_box(width: int, height: int) -> tuple[int, int, int, int]:
    side = min(width, height)
    left = max(0, (width - side) // 2)
    top = max(0, (height - side) // 2)
    return (left, top, left + side, top + side)


def _diagram_alignment_review_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(_diagram_alignment_card(row) for row in rows) or "<p>No manually labeled diagrams available for alignment review.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Diagram Alignment Review</title>
  <style>
    body {{ margin:0; font-family:Georgia, 'Times New Roman', serif; background:#efe3d0; color:#21170f; }}
    header {{ padding:1rem 1.25rem; background:#24180f; color:#fff7ea; }}
    main {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(320px, 1fr)); gap:1rem; padding:1rem; }}
    article {{ background:#fffaf0; border:1px solid #d7c4aa; border-radius:16px; padding:1rem; }}
    img {{ max-width:100%; border:1px solid #d7c4aa; border-radius:10px; background:white; }}
    figure {{ margin:.5rem 0; }}
    figcaption {{ font-weight:700; }}
  </style>
</head>
<body>
  <header><h1>Diagram Alignment Review</h1><p>{len(rows)} manually labeled diagram(s). Evidence only; accepted FEN still requires deterministic validation.</p></header>
  <main>{cards}</main>
</body>
</html>"""


def _diagram_alignment_card(row: dict[str, Any]) -> str:
    variants = "\n".join(
        f'<figure><figcaption>{html.escape(str(item.get("variant") or ""))}</figcaption><img src="../{html.escape(str(item.get("href") or ""), quote=True)}" alt="{html.escape(str(row.get("diagram_id") or ""), quote=True)} {html.escape(str(item.get("variant") or ""), quote=True)}"></figure>'
        for item in row.get("variants") or []
    )
    source = str(row.get("source") or "")
    source_html = f'<figure><figcaption>source</figcaption><img src="../{html.escape(source, quote=True)}" alt="{html.escape(str(row.get("diagram_id") or ""), quote=True)} source"></figure>' if source else ""
    return f"""<article>
  <h2>{html.escape(str(row.get("diagram_id") or "diagram"))}</h2>
  <p>Page {html.escape(str(row.get("page") or ""))}, label {html.escape(str(row.get("manual_label") or ""))}</p>
  {source_html}
  {variants}
</article>"""


def _codepoint_label(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return " ".join(f"U+{ord(char):04X}" for char in text)


def _bounded_text(value: str, *, limit: int = 240) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _comments_from_notation_context(page: dict[str, Any], notation_blocks: list[dict[str, Any]]) -> list[str]:
    notation_orders = {int(block.get("reading_order") or -1) for block in notation_blocks}
    comments: list[str] = []
    for block in page.get("blocks", []) or []:
        order = int(block.get("reading_order") or -1)
        text = str(block.get("normalized_text") or block.get("text") or "")
        if order in notation_orders or _looks_like_notation_text(text):
            continue
        if len(text) >= 20 and re.search(r"[A-Za-z]", text):
            comments.append(text)
        if len(comments) >= 3:
            break
    return comments


def _build_notation_pgn_candidate(
    notation: str,
    *,
    page: int,
    source_diagram: str,
    fen: str,
    comments: list[str],
) -> str:
    move_text = _extract_move_text(notation)
    headers = [
        f'[Event "Source page {page}"]',
        '[Site "?"]',
        '[Date "????.??.??"]',
        '[Round "?"]',
        '[White "?"]',
        '[Black "?"]',
        '[Result "*"]',
        f'[SourcePage "{page}"]',
        f'[SourceDiagram "{source_diagram or "?"}"]',
    ]
    if fen:
        headers.extend(['[SetUp "1"]', f'[FEN "{fen}"]'])
    comment_text = " ".join(_pgn_comment(comment) for comment in comments[:2])
    body_parts = [part for part in [comment_text, move_text, "*"] if part]
    return "\n".join(headers) + "\n\n" + " ".join(body_parts).strip()


def _extract_move_text(value: str) -> str:
    text = str(value or "")
    match = re.search(r"\b\d{1,3}\.(?:\.\.)?.*", text)
    if not match:
        return ""
    move_text = match.group(0)
    move_text = re.sub(r"\s+", " ", move_text).strip()
    move_text = re.sub(r"\[[^\]]+\]", "", move_text)
    return move_text


def _pgn_comment(value: str) -> str:
    clean = str(value or "").replace("{", "(").replace("}", ")").strip()
    return f"{{{clean}}}" if clean else ""


def _pgn_has_required_headers(pgn_text: str) -> bool:
    value = str(pgn_text or "")
    required = ["Event", "Site", "Date", "White", "Black", "Result", "SourcePage"]
    return all(re.search(rf'^\[{name}\s+"[^"]*"\]', value, flags=re.MULTILINE) for name in required)


def _pgn_has_source_page(pgn_text: str) -> bool:
    return bool(re.search(r'^\[SourcePage\s+"[^"]+"\]', str(pgn_text or ""), flags=re.MULTILINE))


def _pgn_has_setup_fen(pgn_text: str) -> bool:
    value = str(pgn_text or "")
    return bool(
        re.search(r'^\[SetUp\s+"1"\]', value, flags=re.MULTILINE)
        and re.search(r'^\[FEN\s+"[^"]+"\]', value, flags=re.MULTILINE)
    )


def _fragment_has_raw_context_gap(fragment: dict[str, Any]) -> bool:
    ocr_only_manual_mapping_covers_gap = (
        str(fragment.get("raw_glyph_context_mode") or "") == "ocr_only"
        and int(fragment.get("ocr_token_mappings_applied") or 0) > 0
        and not list(fragment.get("unmapped_token_blockers") or [])
        and UNMAPPED_CHESS_GLYPH_WARNING not in (fragment.get("warnings") or [])
    )
    for diagnostic in fragment.get("glyph_diagnostics") or []:
        if not isinstance(diagnostic, dict):
            continue
        if "raw_char_context_unavailable" in (diagnostic.get("reasons") or []):
            if ocr_only_manual_mapping_covers_gap:
                continue
            return True
    return False


def _quality_threshold_problems(config: ChessStudyConfig, summary: dict[str, Any]) -> list[dict[str, Any]]:
    profile = _normalize_quality_profile(config.quality_profile)
    thresholds = QUALITY_THRESHOLDS[profile]
    severity = "critical" if profile == "masterkindle" or config.strict_thresholds else "review"
    metric_map = {
        "pages": "pages",
        "page_images": "page_images",
        "pages_with_extractable_text": "pages_with_extractable_text",
        "copyable_text_characters": "copyable_text_characters",
        "diagrams_total": "diagrams_total",
        "fen_accepted": "fen_accepted",
        "notation_fragments_total": "notation_fragments_total",
        "accepted_pgn": "accepted_pgn",
    }
    problems: list[dict[str, Any]] = []
    for threshold_key, summary_key in metric_map.items():
        expected = int(thresholds.get(threshold_key) or 0)
        actual = int(summary.get(summary_key) or 0)
        if expected and actual < expected:
            problems.append(
                {
                    "severity": severity,
                    "code": "quality_threshold_not_met",
                    "metric": summary_key,
                    "actual": actual,
                    "expected_min": expected,
                    "profile": profile,
                }
            )
    return problems


def _chapter_anchor_for_page(page_number: int, positions: dict[str, Any]) -> str:
    chapter_numbers = [
        int(item.get("chapter_no") or 0)
        for item in positions.get("positions", []) or []
        if int(item.get("diagram_page") or 0) == page_number and int(item.get("chapter_no") or 0)
    ]
    if not chapter_numbers:
        return ""
    return f'id="chapter-{chapter_numbers[0]:02d}"'


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "diagram")).strip("._")
    return safe or "diagram"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _board_preprocess_sources(out_dir: Path, *, labels_path: str | Path | None) -> list[dict[str, Any]]:
    if labels_path:
        labels = _promote_verified_fen_labels(Path(labels_path), out_dir)
        return [
            {
                "diagram_id": row.get("diagram_id") or Path(str(row.get("crop_path") or "")).stem,
                "page": int(row.get("page") or 0),
                "crop_path": str(row.get("crop_path") or ""),
                "crop_rel_path": _relative_to_out(out_dir, Path(str(row.get("crop_path") or ""))),
                "caption": row.get("diagram_id") or "",
                "fen": row.get("fen") or "",
                "source": "verified_label",
            }
            for row in labels
        ]
    book = _load_source_book(out_dir)
    diagrams = _source_book_diagrams(book)
    if diagrams:
        return [
            {
                "diagram_id": diagram.get("id") or diagram.get("diagram_id") or f"diagram_{index:04d}",
                "page": int(diagram.get("page") or 0),
                "crop_path": str(out_dir / str(diagram.get("image_path") or "")),
                "crop_rel_path": str(diagram.get("image_path") or ""),
                "caption": diagram.get("caption") or diagram.get("label") or "",
                "fen": diagram.get("fen") or diagram.get("fen_candidate") or "",
                "confidence": diagram.get("confidence") or 0.0,
                "source": "book_diagram",
            }
            for index, diagram in enumerate(diagrams)
            if str(diagram.get("image_path") or "")
        ]
    diagram_dir = out_dir / "assets" / "diagrams"
    return [
        {
            "diagram_id": path.stem,
            "page": _safe_int(re.search(r"p(\d+)", path.stem).group(1)) if re.search(r"p(\d+)", path.stem) else 0,
            "crop_path": str(path),
            "crop_rel_path": _relative_to_out(out_dir, path),
            "caption": path.stem,
            "fen": "",
            "confidence": 0.0,
            "source": "diagram_asset",
        }
        for path in sorted(diagram_dir.glob("*.*"))
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]


def _preprocess_board_record(record: dict[str, Any], out_dir: Path, normalized_dir: Path) -> dict[str, Any]:
    diagram_id = _safe_filename(str(record.get("diagram_id") or "diagram"))
    crop_path = Path(str(record.get("crop_path") or ""))
    row: dict[str, Any] = {
        "schema": "kindlemaster.board_preprocess_row.v1",
        "diagram_id": diagram_id,
        "page": int(record.get("page") or 0),
        "caption": str(record.get("caption") or ""),
        "source_crop": str(crop_path),
        "source_crop_rel": str(record.get("crop_rel_path") or _relative_to_out(out_dir, crop_path)),
        "status": "failed",
        "confidence": 0.0,
        "failure_reason": "",
        "normalized_board": "",
        "normalized_board_rel": "",
        "transform_matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "variants": [],
        "accepted_fen_changed": 0,
    }
    if not crop_path.is_file():
        row["failure_reason"] = "source_crop_missing"
        return row
    try:
        variants = _board_normalization_variants(crop_path)
        normalized = variants["normalized_8x8"]
        target = normalized_dir / f"{diagram_id}.png"
        normalized.save(target, format="PNG")
        with Image.open(crop_path) as source_image:
            image_size = list(source_image.size)
        row.update(
            {
                "status": "ok",
                "confidence": _board_normalization_confidence(normalized),
                "failure_reason": "",
                "normalized_board": str(target),
                "normalized_board_rel": _relative_to_out(out_dir, target),
                "variants": sorted(variants.keys()),
                "image_size": image_size,
            }
        )
        return row
    except Exception as exc:
        row["failure_reason"] = type(exc).__name__ + ": " + str(exc)
        return row


def _board_normalization_variants(crop_path: Path) -> dict[str, Image.Image]:
    image = Image.open(crop_path).convert("RGB")
    square = _center_square(image)
    tight = ImageOps.autocontrast(square.convert("L")).convert("RGB")
    inner = _inner_grid_crop(square)
    normalized = ImageOps.autocontrast(inner.convert("L")).resize((256, 256), Image.Resampling.LANCZOS).convert("RGB")
    return {
        "original": image.copy(),
        "tight": tight.resize((256, 256), Image.Resampling.LANCZOS),
        "inner-grid": inner.resize((256, 256), Image.Resampling.LANCZOS),
        "remove-coordinates": inner.resize((256, 256), Image.Resampling.LANCZOS),
        "center-square": square.resize((256, 256), Image.Resampling.LANCZOS),
        "normalized_8x8": normalized,
    }


def _normalize_board_image(crop_path: Path) -> Image.Image:
    return _board_normalization_variants(crop_path)["normalized_8x8"]


def _center_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = min(width, height)
    left = max(0, (width - side) // 2)
    top = max(0, (height - side) // 2)
    return image.crop((left, top, left + side, top + side))


def _inner_grid_crop(image: Image.Image) -> Image.Image:
    width, height = image.size
    margin = max(0, int(min(width, height) * 0.035))
    if width - margin * 2 < 32 or height - margin * 2 < 32:
        return image.copy()
    return image.crop((margin, margin, width - margin, height - margin))


def _board_normalization_confidence(image: Image.Image) -> float:
    gray = image.convert("L")
    pixels = list(gray.resize((32, 32)).tobytes())
    if not pixels:
        return 0.0
    mean = sum(pixels) / len(pixels)
    variance = sum((pixel - mean) ** 2 for pixel in pixels) / len(pixels)
    contrast = min(1.0, math.sqrt(variance) / 80.0)
    dark_ratio = len([pixel for pixel in pixels if pixel < 128]) / len(pixels)
    balance = max(0.0, 1.0 - abs(dark_ratio - 0.5) * 1.6)
    return round(max(0.05, min(0.99, 0.55 * contrast + 0.45 * balance)), 4)


def _split_board_into_squares(board: Image.Image, *, size: int = 64) -> list[Image.Image]:
    normalized = board.resize((size * 8, size * 8), Image.Resampling.LANCZOS).convert("RGB")
    cells: list[Image.Image] = []
    for rank in range(8):
        for file_index in range(8):
            x0 = file_index * size
            y0 = rank * size
            cells.append(normalized.crop((x0, y0, x0 + size, y0 + size)))
    return cells


def _fen_placement_to_cells(placement: str) -> list[str]:
    cells: list[str] = []
    rows = str(placement or "").split("/")
    if len(rows) != 8:
        raise ValueError("FEN placement must contain 8 ranks")
    for row in rows:
        width = 0
        for char in row:
            if char.isdigit():
                value = int(char)
                cells.extend([""] * value)
                width += value
            elif char in "KQRBNPkqrbnp":
                cells.append(char)
                width += 1
            else:
                raise ValueError(f"invalid FEN placement char: {char}")
        if width != 8:
            raise ValueError("FEN rank must contain 8 files")
    if len(cells) != 64:
        raise ValueError("FEN placement must contain 64 cells")
    return cells


def _cells_to_placement(cells: list[str]) -> str:
    ranks: list[str] = []
    for rank in range(8):
        empty = 0
        parts: list[str] = []
        for file_index in range(8):
            piece = cells[rank * 8 + file_index]
            if not piece:
                empty += 1
                continue
            if empty:
                parts.append(str(empty))
                empty = 0
            parts.append(piece)
        if empty:
            parts.append(str(empty))
        ranks.append("".join(parts) or "8")
    return "/".join(ranks)


def _square_name(index: int) -> str:
    file_index = index % 8
    rank_index = index // 8
    return f"{chr(ord('a') + file_index)}{8 - rank_index}"


def _dataset_split(diagram_id: str, *, fold_count: int, holdout_fold: int) -> str:
    folds = max(2, int(fold_count or 5))
    fold = int(hashlib.sha256(str(diagram_id).encode("utf-8")).hexdigest()[:8], 16) % folds
    if fold == int(holdout_fold or 0) % folds:
        return "holdout"
    if fold == (int(holdout_fold or 0) + 1) % folds:
        return "val"
    return "train"


def _square_features(image: Image.Image) -> list[float]:
    gray = ImageOps.autocontrast(image.convert("L")).resize((32, 32), Image.Resampling.LANCZOS)
    pixels = [float(pixel) for pixel in gray.tobytes()]
    mean = sum(pixels) / max(1, len(pixels)) / 255.0
    variance = sum((pixel / 255.0 - mean) ** 2 for pixel in pixels) / max(1, len(pixels))
    dark_ratio = len([pixel for pixel in pixels if pixel < 120]) / max(1, len(pixels))
    edge = 0.0
    width, height = gray.size
    for y in range(height - 1):
        for x in range(width - 1):
            edge += abs(gray.getpixel((x, y)) - gray.getpixel((x + 1, y)))
            edge += abs(gray.getpixel((x, y)) - gray.getpixel((x, y + 1)))
    edge_density = edge / max(1, (width - 1) * (height - 1) * 2 * 255)
    return [round(mean, 6), round(math.sqrt(variance), 6), round(dark_ratio, 6), round(edge_density, 6)]


def _train_centroid_classifier(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    grouped: dict[str, list[list[float]]] = {}
    for row in rows:
        image_path = Path(str(row.get("image_path") or ""))
        if not image_path.is_file():
            continue
        label = str(row.get("class") or "empty")
        grouped.setdefault(label, []).append(_square_features(Image.open(image_path)))
    centroids: dict[str, list[float]] = {}
    for label, vectors in grouped.items():
        if not vectors:
            continue
        width = len(vectors[0])
        centroids[label] = [round(sum(vector[index] for vector in vectors) / len(vectors), 6) for index in range(width)]
    return centroids


def _predict_square_class(image: Image.Image, model: dict[str, Any]) -> dict[str, Any]:
    centroids = model.get("class_centroids") or {}
    if not centroids:
        return {"class": "", "confidence": 0.0, "probabilities": {}, "entropy": 1.0}
    features = _square_features(image)
    distances = {
        label: math.sqrt(sum((features[index] - float(values[index])) ** 2 for index in range(min(len(features), len(values)))))
        for label, values in centroids.items()
    }
    scores = {label: math.exp(-distance * 8.0) for label, distance in distances.items()}
    total = sum(scores.values()) or 1.0
    probabilities = {label: score / total for label, score in scores.items()}
    best = max(probabilities.items(), key=lambda item: item[1])
    entropy = -sum(prob * math.log(max(prob, 1e-9)) for prob in probabilities.values()) / max(1e-9, math.log(max(2, len(probabilities))))
    return {
        "class": "" if best[0] == "empty" else best[0],
        "label": best[0],
        "confidence": round(float(best[1]), 4),
        "probabilities": {key: round(value, 4) for key, value in sorted(probabilities.items())},
        "entropy": round(float(entropy), 4),
    }


def _square_prediction_alternatives(result: dict[str, Any], *, top_n: int = 3) -> list[dict[str, Any]]:
    probabilities = result.get("probabilities") if isinstance(result.get("probabilities"), dict) else {}
    rows: list[dict[str, Any]] = []
    for label, probability in sorted(probabilities.items(), key=lambda item: float(item[1] or 0.0), reverse=True)[: max(1, top_n)]:
        label_text = str(label or "empty")
        piece = "" if label_text == "empty" else label_text
        rows.append(
            {
                "class": label_text,
                "piece": piece if piece in "KQRBNPkqrbnp" else "",
                "confidence": round(float(probability or 0.0), 4),
                "source": "model_centroid",
            }
        )
    if not rows:
        label_text = str(result.get("label") or "empty")
        piece = "" if label_text == "empty" else str(result.get("class") or "")
        rows.append(
            {
                "class": label_text,
                "piece": piece if piece in "KQRBNPkqrbnp" else "",
                "confidence": round(float(result.get("confidence") or 0.0), 4),
                "source": "model_centroid",
            }
        )
    return rows


def _evaluate_square_classifier(rows: list[dict[str, Any]], model: dict[str, Any]) -> dict[str, Any]:
    eval_rows = [row for row in rows if row.get("split") in {"val", "holdout"}]
    if not eval_rows:
        eval_rows = rows
    confusion: dict[str, dict[str, int]] = {}
    correct = 0
    total = 0
    for row in eval_rows:
        path = Path(str(row.get("image_path") or ""))
        if not path.is_file():
            continue
        expected = str(row.get("class") or "empty")
        predicted = str(_predict_square_class(Image.open(path), model).get("label") or "empty")
        confusion.setdefault(expected, {})
        confusion[expected][predicted] = confusion[expected].get(predicted, 0) + 1
        correct += int(expected == predicted)
        total += 1
    return {
        "sample_count": total,
        "exact_square_count": correct,
        "square_accuracy": round(correct / max(1, total), 4),
        "confusion": confusion,
        "per_class_accuracy": {
            label: round(values.get(label, 0) / max(1, sum(values.values())), 4)
            for label, values in sorted(confusion.items())
        },
    }


def _predict_fen_for_source(source: dict[str, Any], out_dir: Path, model: dict[str, Any]) -> dict[str, Any]:
    diagram_id = str(source.get("diagram_id") or "diagram")
    crop_path = Path(str(source.get("crop_path") or ""))
    row: dict[str, Any] = {
        "schema": "kindlemaster.fen_model_prediction.v1",
        "diagram_id": diagram_id,
        "page": int(source.get("page") or 0),
        "source_crop": str(crop_path),
        "status": "needs_review",
        "fen_candidate": "",
        "placement": "",
        "global_confidence": 0.0,
        "mean_entropy": 1.0,
        "squares": [],
        "deterministic_validation": {"valid": False, "warnings": ["not_run"]},
    }
    if not crop_path.is_file():
        row["deterministic_validation"] = {"valid": False, "warnings": ["source_crop_missing"]}
        return row
    try:
        board = _normalize_board_image(crop_path)
        square_results = [_predict_square_class(square, model) for square in _split_board_into_squares(board)]
        cells = [str(result.get("class") or "") for result in square_results]
        placement = _cells_to_placement(cells)
        side = _infer_side_to_move(str(source.get("caption") or "")) or "w"
        fen = f"{placement} {side if side in {'w', 'b'} else 'w'} - - 0 1"
        valid, warnings = validate_fen(fen)
        confidences = [float(result.get("confidence") or 0.0) for result in square_results]
        entropies = [float(result.get("entropy") or 1.0) for result in square_results]
        row.update(
            {
                "status": "deterministic_valid" if valid and not warnings else "needs_review",
                "fen_candidate": fen,
                "placement": placement,
                "global_confidence": round(sum(confidences) / max(1, len(confidences)), 4),
                "mean_entropy": round(sum(entropies) / max(1, len(entropies)), 4),
                "squares": [
                    {
                        "square": _square_name(index),
                        "class": result.get("label") or "empty",
                        "piece": result.get("class") or "",
                        "confidence": result.get("confidence"),
                        "entropy": result.get("entropy"),
                        "alternatives": _square_prediction_alternatives(result, top_n=3),
                        "source": "model_centroid",
                    }
                    for index, result in enumerate(square_results)
                ],
                "deterministic_validation": {"valid": bool(valid and not warnings), "warnings": warnings},
            }
        )
        if any(not square.get("alternatives") for square in row.get("squares") or []):
            row.setdefault("warnings", []).append("no_square_alternatives")
    except Exception as exc:
        row["deterministic_validation"] = {"valid": False, "warnings": [type(exc).__name__ + ": " + str(exc)]}
    return row


def _fen_ensemble_verdict(
    prediction: dict[str, Any],
    verified_by_id: dict[str, dict[str, Any]],
    *,
    min_confidence: float,
) -> dict[str, Any]:
    diagram_id = str(prediction.get("diagram_id") or "")
    fen = str(prediction.get("fen_candidate") or "")
    reasons: list[str] = []
    validation = prediction.get("deterministic_validation") or {}
    if not validation.get("valid"):
        reasons.append("deterministic_validation_failed")
    if float(prediction.get("global_confidence") or 0.0) < float(min_confidence):
        reasons.append("confidence_below_threshold")
    label = verified_by_id.get(diagram_id)
    if label and str(label.get("fen") or "") != fen:
        reasons.append("verified_label_disagrees")
    if not label:
        reasons.append("no_verified_label_for_ensemble_acceptance")
    return {
        "diagram_id": diagram_id,
        "fen_candidate": fen,
        "global_confidence": prediction.get("global_confidence"),
        "status": "accepted_candidate" if not reasons else "needs_review",
        "reasons": reasons,
        "source": "local_model_ensemble",
        "accepted_fen_changed": 0,
    }


def _board_preprocess_review_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(
        f"""<article>
  <h2>{html.escape(str(row.get('diagram_id') or 'diagram'))}</h2>
  <p>Page {html.escape(str(row.get('page') or ''))} · status <b>{html.escape(str(row.get('status') or ''))}</b> · confidence {float(row.get('confidence') or 0.0):.3f}</p>
  <img src="../{html.escape(str(row.get('source_crop_rel') or ''), quote=True)}" alt="source">
  {f'<img src="../{html.escape(str(row.get("normalized_board_rel") or ""), quote=True)}" alt="normalized">' if row.get('normalized_board_rel') else ''}
  <p>{html.escape(str(row.get('failure_reason') or ''))}</p>
</article>"""
        for row in rows
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Board Preprocess Review</title>
<style>body{{font-family:Georgia,serif;background:#efe4d3;color:#21170f}}main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:1rem;padding:1rem}}article{{background:#fff8ec;border:1px solid #d8c4a8;border-radius:16px;padding:1rem}}img{{max-width:100%;background:white;border:1px solid #d8c4a8;margin:.35rem 0}}</style></head>
<body><header><h1>Board Preprocess Review</h1><p>{len(rows)} crop(s), evidence only.</p></header><main>{cards}</main></body></html>"""


def _fen_ensemble_conflicts_html(rows: list[dict[str, Any]]) -> str:
    cards = "\n".join(
        f"<article><h2>{html.escape(str(row.get('diagram_id') or 'diagram'))}</h2><p>{html.escape(', '.join(row.get('reasons') or []))}</p><code>{html.escape(str(row.get('fen_candidate') or ''))}</code></article>"
        for row in rows
    )
    return f"<!doctype html><html><head><meta charset='utf-8'><title>FEN Ensemble Conflicts</title></head><body><h1>FEN Ensemble Conflicts</h1>{cards or '<p>No conflicts.</p>'}</body></html>"


def _write_square_confusion_csv(path: Path, confusion: dict[str, dict[str, int]]) -> None:
    rows = [
        {"expected": expected, "predicted": predicted, "count": count}
        for expected, values in confusion.items()
        for predicted, count in values.items()
    ]
    _write_csv(path, rows)


def _fen_model_card(out_dir: Path, dataset_path: Path, model_path: Path, eval_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "kindlemaster.fen_model_card.v1",
        "model_path": str(model_path),
        "dataset_path": str(dataset_path),
        "dataset_sha256": _file_sha256(dataset_path) if dataset_path.is_file() else "",
        "model_sha256": _file_sha256(model_path) if model_path.is_file() else "",
        "eval": {
            "status": eval_payload.get("status"),
            "sample_count": eval_payload.get("sample_count"),
            "square_accuracy": eval_payload.get("square_accuracy"),
        },
        "git_commit": _current_git_commit(),
        "policy": "Model card links the local model to dataset evidence and validation status.",
    }


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _relative_to_out(out_dir: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(out_dir.resolve())).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _image_sha256(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _current_git_commit() -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_cell(row.get(key)) for key in fieldnames})


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _resolve_html_input(value: str | Path | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.suffix.lower() != ".zip":
        return path
    extract_dir = path.with_suffix("")
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        html_names = [name for name in archive.namelist() if name.lower().endswith(".html")]
        if not html_names:
            raise ValueError(f"ZIP does not contain an HTML file: {path}")
        archive.extract(html_names[0], extract_dir)
        return extract_dir / html_names[0]


def _empty_current_audit() -> dict[str, Any]:
    return {"status": "not_provided", "final_html_status": "NOT_ACCEPTABLE_AS_FINAL"}


def _pdf_page_texts(pdf: Path, *, include_blocks: bool = False) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    with fitz.open(pdf) as document:
        for index, page in enumerate(document):
            text = page.get_text("text") or ""
            row: dict[str, Any] = {"index": index, "page_number": index + 1, "text": text}
            if include_blocks:
                row["blocks"] = _text_blocks(page)
            pages.append(row)
    return pages


def _merged_page_texts(
    pdf: Path,
    html_path: Path | None,
    *,
    include_blocks: bool = False,
) -> list[dict[str, Any]]:
    pdf_pages = _pdf_page_texts(pdf, include_blocks=include_blocks)
    if not html_path or not html_path.is_file():
        return pdf_pages
    html_pages = _html_page_texts(html_path)
    html_by_number = {int(page.get("page_number") or 0): page for page in html_pages}
    merged: list[dict[str, Any]] = []
    for page in pdf_pages:
        page_number = int(page["page_number"])
        html_page = html_by_number.get(page_number) or {}
        pdf_text = str(page.get("text") or "")
        html_text = str(html_page.get("text") or "")
        combined_text = "\n".join(part for part in [pdf_text.strip(), html_text.strip()] if part)
        row = {**page, "text": combined_text or pdf_text}
        if include_blocks and html_text:
            row["blocks"] = [
                *list(page.get("blocks") or []),
                *list(html_page.get("blocks") or []),
            ]
        merged.append(row)
    return merged


def _html_page_texts(html_path: Path) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    page_nodes = soup.select(".chess-book-page, .pdf-page, section[data-page]")
    if not page_nodes:
        return [{"index": 0, "page_number": 1, "text": soup.get_text("\n", strip=True), "blocks": []}]
    pages: list[dict[str, Any]] = []
    for index, node in enumerate(page_nodes):
        raw_page = node.get("data-page") or node.get("data-page-number") or str(index + 1)
        try:
            page_number = int(str(raw_page).strip())
        except ValueError:
            page_number = index + 1
        text_blocks: list[dict[str, Any]] = []
        for block_index, block in enumerate(node.select(".book-text, .pdf-text-span, pre, p, h1, h2, h3")):
            text = block.get_text(" ", strip=True)
            if not text or _is_technical_audit_text(text):
                continue
            text_blocks.append({"type": "text", "text": text, "bbox": [0, 0, 0, 0], "block_index": block_index, "line_index": 0})
        text = "\n".join(block["text"] for block in text_blocks) or node.get_text("\n", strip=True)
        if _is_technical_audit_text(text):
            text = ""
        pages.append({"index": page_number - 1, "page_number": page_number, "text": text, "blocks": text_blocks})
    return pages


def _text_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block_index, block in enumerate((page.get_text("dict") or {}).get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            text = "".join(str(span.get("text") or "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            bbox = line.get("bbox") or block.get("bbox") or [0, 0, 0, 0]
            blocks.append({"type": "text", "text": text, "bbox": [float(value) for value in bbox[:4]], "block_index": block_index, "line_index": line_index})
    return blocks


def _chapter_title_hits(pages: list[dict[str, Any]]) -> dict[int, int | None]:
    hits: dict[int, int | None] = {}
    for chapter_no, title in YUSUPOV_CHAPTERS:
        pattern = re.compile(rf"\b{chapter_no}\s+{re.escape(title)}\b|Chapter\s+{chapter_no}\b.*{re.escape(title)}", re.IGNORECASE)
        hits[chapter_no] = _first_matching_page(pages, pattern)
    return hits


def _yusupov_toc_numbers_from_html(html_path: Path) -> list[int]:
    if not html_path.is_file():
        return []
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for node in soup.select(".chess-book-page, .pdf-page, section[data-page]"):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
        if "CONTENTS" not in text.upper() or "Mating motifs" not in text:
            continue
        tail = text.split("CONTENTS", 1)[-1]
        numbers = [int(value) for value in re.findall(r"\b\d{1,3}\b", tail)]
        if len(numbers) >= 31:
            return numbers
    return []


def _structure_from_toc_numbers(numbers: list[int]) -> dict[str, Any]:
    # Expected sequence in this book: Key, Preface, Introduction, 24 chapters,
    # Final test, and three appendix entries.
    if len(numbers) < 31:
        return {}
    chapter_numbers = numbers[3:27]
    if len(chapter_numbers) != len(YUSUPOV_CHAPTERS):
        return {}
    return {
        "chapter_starts": {
            chapter_no: chapter_numbers[index]
            for index, (chapter_no, _title) in enumerate(YUSUPOV_CHAPTERS)
        },
        "final_test": numbers[27],
        "appendices": {
            "index_of_composers_and_analysts": numbers[28],
            "index_of_games": numbers[29],
            "recommended_books": numbers[30],
        },
    }


def _first_matching_page(pages: list[dict[str, Any]], pattern: re.Pattern[str]) -> int | None:
    for page in pages:
        if pattern.search(str(page.get("text") or "")):
            return int(page["page_number"])
    return None


def _validate_structure(chapters: list[dict[str, Any]], final_test_page: int | None, appendices: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    detected = [chapter for chapter in chapters if chapter.get("start_book_page")]
    if len(detected) != len(YUSUPOV_CHAPTERS):
        errors.append("chapter_count_incomplete")
    if not final_test_page:
        errors.append("final_test_missing")
    if not any(appendices.values()):
        errors.append("appendices_missing")
    ranges = [
        (int(chapter["start_book_page"]), int(chapter["end_book_page"]))
        for chapter in chapters
        if chapter.get("start_book_page") and chapter.get("end_book_page")
    ]
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] <= previous[1]:
            errors.append("chapter_ranges_overlap")
            break
    return {"status": "failed" if errors else "passed", "errors": errors}


def _chapter_for_book_page(book_page: int, chapters: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [chapter for chapter in chapters if _safe_int(chapter.get("start_book_page")) and _safe_int(chapter.get("start_book_page")) <= book_page]
    candidates.sort(key=lambda item: _safe_int(item.get("start_book_page")), reverse=True)
    return candidates[0] if candidates else None


def _classify_page_type(
    text: str,
    book_page: int,
    chapter: dict[str, Any] | None,
    *,
    final_start: int,
    first_appendix: int,
) -> str:
    value = re.sub(r"\s+", " ", text or "").lower()
    if final_start and book_page >= final_start and (not first_appendix or book_page < first_appendix):
        return "final_test"
    if first_appendix and book_page >= first_appendix:
        return "appendix"
    if not chapter:
        return "front_matter"
    if "solution" in value or "solutions" in value:
        return "solutions"
    if "scoring" in value or "points" in value:
        return "scoring"
    if EXERCISE_LABEL_RE.search(text):
        return "exercises"
    if "example" in value or "ex." in value:
        return "examples"
    return "chapter_lesson"


def _segment_blocks_from_page(page: dict[str, Any], *, labels: list[str], final_labels: list[str]) -> list[dict[str, Any]]:
    blocks = list(page.get("blocks") or [])
    result: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block.get("text") or "")
        block_type = "notation" if re.search(r"\b\d{1,3}\.(?:\.\.)?\s*\S+", text) else "text"
        if EXERCISE_LABEL_RE.search(text):
            block_type = "exercise_label"
        if FINAL_LABEL_RE.search(text):
            block_type = "final_test_label"
        result.append({**block, "type": block_type})
    for label in labels:
        if not any(item.get("type") == "exercise_label" and label in str(item.get("text")) for item in result):
            result.append({"type": "exercise_label", "text": label, "bbox": [0, 0, 0, 0]})
    for label in final_labels:
        if not any(item.get("type") == "final_test_label" and label in str(item.get("text")) for item in result):
            result.append({"type": "final_test_label", "text": label, "bbox": [0, 0, 0, 0]})
    return result


def _normalize_ex_label(match: re.Match[str]) -> str:
    return f"Ex. {int(match.group('chapter'))}-{int(match.group('number'))}"


def _best_label_for_diagram(diagram: dict[str, Any], page: dict[str, Any], *, fallback_index: int) -> str:
    labels = list(page.get("exercise_labels") or [])
    final_labels = list(page.get("final_test_labels") or [])
    if labels:
        return labels[min(fallback_index - 1, len(labels) - 1)]
    if final_labels:
        return final_labels[min(fallback_index - 1, len(final_labels) - 1)]
    return str(diagram.get("diagram_id") or f"diagram-{fallback_index:03d}")


def _position_status_from_diagram(diagram: dict[str, Any], *, fen: str) -> tuple[str, list[str]]:
    warnings = [str(warning) for warning in (diagram.get("warnings") or [])]
    if not fen:
        return "missing_fen", sorted(set([*warnings, str(diagram.get("reason") or "fen_missing")]))
    valid, fen_warnings = validate_fen(fen)
    if not valid or fen_warnings:
        return "needs_review", sorted(set([*warnings, *fen_warnings]))
    if not diagram.get("source_crop"):
        return "needs_review", sorted(set([*warnings, "source_crop_missing"]))
    if not diagram.get("rendered_diagram"):
        return "needs_review", sorted(set([*warnings, "rendered_diagram_missing"]))
    if warnings:
        return "needs_review", sorted(set(warnings))
    if str(diagram.get("status") or "") != "accepted":
        return "needs_review", sorted(set([*warnings, str(diagram.get("reason") or "fen_requires_review")]))
    return "accepted", warnings


def _position_id(*, item_type: str, chapter_no: Any, label: str, fallback_index: int) -> str:
    if item_type == "final_test":
        number = re.sub(r"\D+", "", label or "") or str(fallback_index)
        return f"final_f_{int(number):03d}"
    label_match = EXERCISE_LABEL_RE.search(label or "")
    if label_match:
        return f"ch{int(label_match.group('chapter')):02d}_ex_{int(label_match.group('number')):03d}"
    if chapter_no:
        return f"ch{int(chapter_no):02d}_diag_{fallback_index:03d}"
    return f"diag_{fallback_index:03d}"


def _chapter_title(chapter_no: Any) -> str:
    try:
        number = int(chapter_no)
    except (TypeError, ValueError):
        return ""
    for candidate_no, title in YUSUPOV_CHAPTERS:
        if candidate_no == number:
            return title
    return ""


def _critical_warnings(status: str, warnings: list[str]) -> list[str]:
    if status != "accepted":
        return []
    return [warning for warning in warnings if warning]


def _count_by_status(items: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "needs_review")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _count_review_positions(items: list[dict[str, Any]]) -> int:
    return len([item for item in items if item.get("status") != "accepted"])


def _pgn_replay_clean(pgn_text: str) -> bool:
    value = str(pgn_text or "").strip()
    if not value:
        return False
    try:
        import io
        import logging
        import chess.pgn  # type: ignore[import-not-found]

        logger = logging.getLogger("chess.pgn")
        was_disabled = logger.disabled
        logger.disabled = True
        try:
            game = chess.pgn.read_game(io.StringIO(value))
        finally:
            logger.disabled = was_disabled
        if game is None or getattr(game, "errors", None):
            return False
        board = game.board()
        move_count = 0
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                return False
            board.push(move)
            move_count += 1
        return move_count > 0
    except Exception:
        return False


def _current_audit_markdown(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Current Chess HTML Audit",
            "",
            f"- Status: `{report.get('final_html_status')}`",
            f"- PDF pages: `{report.get('pdf_pages')}`",
            f"- HTML pages: `{report.get('html_pages')}`",
            f"- Diagrams: `{(report.get('diagrams') or {}).get('detected')}`",
            f"- FEN records: `{(report.get('fen') or {}).get('total')}`",
            f"- Accepted PGN: `{(report.get('pgn') or {}).get('accepted')}`",
            f"- Critical errors: `{', '.join(report.get('critical_errors') or [])}`",
            "",
        ]
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
