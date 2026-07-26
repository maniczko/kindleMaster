from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import fitz


SOURCE_NOTATION_SCHEMA = "kindlemaster.source_bound_chess_notation.v1"
UNKNOWN_GLYPH_PREFIX = "[[gid:"
EXERCISE_LABEL_RE = re.compile(
    r"(?i)\bEx(?:ercise)?\.?\s*(?P<chapter>\d{1,3})\s*[-.\u2013\u2014]\s*"
    r"(?P<number>\d{1,3})\b"
)
PRINTED_EXERCISE_LABEL_RE = re.compile(
    r"(?i)^[^A-Za-z0-9]*Ex(?:ercise)?\.?\s*"
    r"(?P<chapter>\d{1,3})\s*[-.\u2013\u2014]\s*"
    r"(?P<number>\d{1,3})\b"
)


@dataclass(frozen=True)
class SourceGlyph:
    font_name: str
    font_fingerprint: str
    glyph_id: int
    raw_unicode: str
    decoded_text: str
    origin: tuple[float, float]
    bbox: tuple[float, float, float, float]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "font_name": self.font_name,
            "font_fingerprint": self.font_fingerprint,
            "glyph_id": self.glyph_id,
            "raw_unicode": self.raw_unicode,
            "decoded_text": self.decoded_text,
            "origin": [round(value, 3) for value in self.origin],
            "bbox": [round(value, 3) for value in self.bbox],
            "status": self.status,
        }


@dataclass(frozen=True)
class SourceNotationLine:
    page_number: int
    baseline: float
    bbox: tuple[float, float, float, float]
    raw_text: str
    decoded_text: str
    glyphs: tuple[SourceGlyph, ...]
    blockers: tuple[str, ...]

    @property
    def status(self) -> str:
        return "decoded" if not self.blockers else "needs_review"

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "baseline": round(self.baseline, 3),
            "bbox": [round(value, 3) for value in self.bbox],
            "raw_text": self.raw_text,
            "decoded_text": self.decoded_text,
            "normalized_text": normalize_decoded_notation(self.decoded_text),
            "status": self.status,
            "blockers": list(self.blockers),
            "glyphs": [glyph.to_dict() for glyph in self.glyphs],
        }


@dataclass(frozen=True)
class SourceNotationBlock:
    exercise_id: str
    source_label: str
    page_number: int
    bbox: tuple[float, float, float, float]
    raw_text: str
    decoded_text: str
    notation_text: str
    lines: tuple[SourceNotationLine, ...]
    blockers: tuple[str, ...]

    @property
    def status(self) -> str:
        if self.blockers:
            return "needs_review"
        return "decoded" if self.notation_text else "no_notation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "source_label": self.source_label,
            "page_number": self.page_number,
            "bbox": [round(value, 3) for value in self.bbox],
            "raw_text": self.raw_text,
            "decoded_text": self.decoded_text,
            "notation_text": self.notation_text,
            "status": self.status,
            "blockers": list(self.blockers),
            "line_bboxes": [
                [round(value, 3) for value in line.bbox]
                for line in self.lines
            ],
            "lines": [line.to_dict() for line in self.lines],
        }


def load_source_glyph_maps(
    paths: Iterable[str | Path] | None = None,
) -> dict[str, dict[str, Any]]:
    mapping_paths = list(paths or _default_mapping_paths())
    mappings: dict[str, dict[str, Any]] = {}
    for raw_path in mapping_paths:
        path = Path(raw_path)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema") != SOURCE_NOTATION_SCHEMA:
            continue
        fingerprint = str(payload.get("font_sha256") or "").strip().lower()
        glyphs = payload.get("glyphs")
        if len(fingerprint) != 64 or not isinstance(glyphs, Mapping):
            continue
        mappings[fingerprint] = {
            "source": str(path),
            "font_name": str(payload.get("font_name") or ""),
            "strict": bool(payload.get("strict")),
            "glyphs": {
                int(glyph_id): str(replacement)
                for glyph_id, replacement in glyphs.items()
                if str(glyph_id).isdigit()
            },
            "sequences": _load_glyph_sequences(payload.get("sequences")),
            "suspicious_decoded_patterns": tuple(
                str(pattern)
                for pattern in payload.get("suspicious_decoded_patterns") or ()
                if str(pattern)
            ),
        }
    return mappings


def extract_source_notation_lines(
    pdf_path: str | Path,
    *,
    page_number: int,
    clip: Sequence[float] | None = None,
    mapping_paths: Iterable[str | Path] | None = None,
    baseline_tolerance: float = 3.2,
) -> list[SourceNotationLine]:
    source = Path(pdf_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    with fitz.open(source) as document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(f"page_number_out_of_range:{page_number}")
        page = document[page_number - 1]
        font_fingerprints = _page_font_fingerprints(document, page)
        mappings = load_source_glyph_maps(mapping_paths)
        glyphs = _source_glyphs(
            page,
            font_fingerprints=font_fingerprints,
            mappings=mappings,
            clip=clip,
        )
    return _group_glyphs_into_lines(
        glyphs,
        page_number=page_number,
        baseline_tolerance=baseline_tolerance,
    )


def extract_source_notation_pages(
    pdf_path: str | Path,
    *,
    page_numbers: Iterable[int],
    mapping_paths: Iterable[str | Path] | None = None,
    baseline_tolerance: float = 3.2,
) -> dict[str, Any]:
    source = Path(pdf_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    requested_pages = sorted(
        {
            int(page_number)
            for page_number in page_numbers
            if int(page_number) > 0
        }
    )
    mappings = load_source_glyph_maps(mapping_paths)
    pages: dict[str, dict[str, Any]] = {}
    with fitz.open(source) as document:
        for page_number in requested_pages:
            if page_number > document.page_count:
                pages[str(page_number)] = {
                    "page_number": page_number,
                    "status": "needs_review",
                    "decoded_text": "",
                    "blockers": ["page_number_out_of_range"],
                    "lines": [],
                }
                continue
            page = document[page_number - 1]
            glyphs = _source_glyphs(
                page,
                font_fingerprints=_page_font_fingerprints(document, page),
                mappings=mappings,
                clip=None,
            )
            source_lines = _group_glyphs_into_lines(
                glyphs,
                page_number=page_number,
                baseline_tolerance=baseline_tolerance,
            )
            solution_blocks = segment_source_notation_blocks(
                source_lines,
                page_width=float(page.rect.width),
            )
            solution_blocks = [
                block
                for block in solution_blocks
                if block.notation_text or block.blockers
            ]
            notation_lines = [
                line
                for line in source_lines
                if looks_like_decoded_notation_line(line.decoded_text)
            ]
            notation_lines = _order_notation_lines_by_columns(
                notation_lines,
                page_width=float(page.rect.width),
            )
            blockers = sorted(
                {
                    blocker
                    for line in notation_lines
                    for blocker in line.blockers
                }
            )
            decoded_text = "\n".join(
                normalize_decoded_notation(line.decoded_text)
                for line in notation_lines
            ).strip()
            pages[str(page_number)] = {
                "page_number": page_number,
                "status": (
                    "decoded"
                    if decoded_text and not blockers
                    else ("needs_review" if blockers else "no_notation")
                ),
                "decoded_text": decoded_text,
                "blockers": blockers,
                "lines": [line.to_dict() for line in notation_lines],
                "solution_blocks": [
                    block.to_dict()
                    for block in solution_blocks
                ],
            }
    return {
        "schema": SOURCE_NOTATION_SCHEMA,
        "source_pdf": str(source.resolve()),
        "source_pdf_sha256": _file_sha256(source),
        "pages": pages,
    }


def segment_source_notation_blocks(
    lines: Sequence[SourceNotationLine],
    *,
    page_width: float,
) -> list[SourceNotationBlock]:
    """Split a multi-column solution page at printed exercise labels."""

    source_lines = list(lines)
    labels = [
        (
            line,
            _printed_exercise_label(
                line.decoded_text or line.raw_text
            ),
        )
        for line in source_lines
    ]
    labels = [(line, label) for line, label in labels if label]
    if not labels:
        return []

    midpoint = page_width / 2.0 if page_width > 0 else 0.0
    has_left_labels = any(
        line.bbox[2] <= midpoint for line, _ in labels
    )
    has_right_labels = any(
        line.bbox[0] >= midpoint for line, _ in labels
    )
    column_count = 2 if midpoint and has_left_labels and has_right_labels else 1

    columns: dict[int, list[SourceNotationLine]] = {
        column: [] for column in range(column_count)
    }
    for line in source_lines:
        width = max(0.0, line.bbox[2] - line.bbox[0])
        if page_width > 0 and width >= page_width * 0.72:
            continue
        center = (line.bbox[0] + line.bbox[2]) / 2.0
        column = 0 if column_count == 1 or center < midpoint else 1
        columns[column].append(line)

    pending: list[dict[str, Any]] = []
    for column in range(column_count):
        ordered = sorted(
            columns[column],
            key=lambda line: (line.baseline, line.bbox[0]),
        )
        label_indexes = [
            index
            for index, line in enumerate(ordered)
            if _printed_exercise_label(
                line.decoded_text or line.raw_text
            )
        ]
        if not label_indexes:
            continue

        prefix = ordered[: label_indexes[0]]
        if pending:
            pending[-1]["lines"].extend(
                line
                for line in prefix
                if looks_like_decoded_notation_line(line.decoded_text)
            )

        for label_position, start_index in enumerate(label_indexes):
            end_index = (
                label_indexes[label_position + 1]
                if label_position + 1 < len(label_indexes)
                else len(ordered)
            )
            label_line = ordered[start_index]
            label = _printed_exercise_label(
                label_line.decoded_text or label_line.raw_text
            )
            if not label:
                continue
            pending.append(
                {
                    "exercise_id": label,
                    "source_label": (
                        label_line.decoded_text or label_line.raw_text
                    ).strip(),
                    "label_line": label_line,
                    "lines": ordered[start_index + 1 : end_index],
                }
            )

    blocks: list[SourceNotationBlock] = []
    for item in pending:
        content_lines = [
            line
            for line in item["lines"]
            if _meaningful_solution_line(line.decoded_text)
        ]
        notation_lines = [
            line
            for line in content_lines
            if looks_like_decoded_notation_line(line.decoded_text)
        ]
        all_lines = [item["label_line"], *content_lines]
        blockers = sorted(
            {
                blocker
                for line in notation_lines
                for blocker in line.blockers
            }
        )
        blocks.append(
            SourceNotationBlock(
                exercise_id=str(item["exercise_id"]),
                source_label=str(item["source_label"]),
                page_number=int(item["label_line"].page_number),
                bbox=_union_bbox(line.bbox for line in all_lines),
                raw_text="\n".join(
                    line.raw_text for line in content_lines
                ).strip(),
                decoded_text="\n".join(
                    normalize_decoded_notation(line.decoded_text)
                    for line in content_lines
                ).strip(),
                notation_text="\n".join(
                    normalize_decoded_notation(line.decoded_text)
                    for line in notation_lines
                ).strip(),
                lines=tuple(content_lines),
                blockers=tuple(blockers),
            )
        )
    return blocks


def build_source_notation_audit(
    pdf_path: str | Path,
    *,
    page_number: int,
    regions: Sequence[Mapping[str, Any]],
    output_path: str | Path | None = None,
    mapping_paths: Iterable[str | Path] | None = None,
) -> dict[str, Any]:
    region_rows: list[dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        bbox = region.get("bbox")
        if not isinstance(bbox, Sequence) or len(bbox) < 4:
            raise ValueError(f"region_bbox_invalid:{index}")
        lines = extract_source_notation_lines(
            pdf_path,
            page_number=page_number,
            clip=[float(value) for value in bbox[:4]],
            mapping_paths=mapping_paths,
        )
        blockers = sorted(
            {
                blocker
                for line in lines
                for blocker in line.blockers
            }
        )
        region_rows.append(
            {
                "region_id": str(region.get("region_id") or f"region-{index:03d}"),
                "label": str(region.get("label") or ""),
                "bbox": [float(value) for value in bbox[:4]],
                "status": "decoded" if not blockers else "needs_review",
                "raw_text": "\n".join(line.raw_text for line in lines).strip(),
                "source_decoded_text": "\n".join(
                    line.decoded_text for line in lines
                ).strip(),
                "decoded_text": "\n".join(
                    normalize_decoded_notation(line.decoded_text)
                    for line in lines
                ).strip(),
                "blockers": blockers,
                "lines": [line.to_dict() for line in lines],
            }
        )
    payload = {
        "schema": SOURCE_NOTATION_SCHEMA,
        "source_pdf": str(Path(pdf_path).resolve()),
        "source_pdf_sha256": _file_sha256(Path(pdf_path)),
        "page_number": page_number,
        "status": (
            "decoded"
            if all(row["status"] == "decoded" for row in region_rows)
            else "needs_review"
        ),
        "regions": region_rows,
    }
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return payload


def assign_source_exercise_labels_to_diagrams(
    pdf_path: str | Path,
    diagram_records: Iterable[Mapping[str, Any]],
    *,
    mapping_paths: Iterable[str | Path] | None = None,
    enable_vision_ocr: bool = True,
    scan_all_diagram_pages: bool = False,
    known_exercise_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Attach printed labels and keep OCR/vision evidence review-safe."""

    source = Path(pdf_path)
    records = [dict(record) for record in diagram_records]
    by_page: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        page_number = int(
            record.get("page_number") or record.get("page") or 0
        )
        if page_number > 0:
            by_page.setdefault(page_number, []).append(record)

    assignments: list[dict[str, Any]] = []
    used_diagram_ids: set[str] = set()
    mappings = load_source_glyph_maps(mapping_paths)
    page_widths: dict[int, float] = {}
    with fitz.open(source) as document:
        for page_number, page_records in sorted(by_page.items()):
            if page_number > document.page_count:
                continue
            page = document[page_number - 1]
            page_widths[page_number] = float(page.rect.width)
            glyphs = _source_glyphs(
                page,
                font_fingerprints=_page_font_fingerprints(document, page),
                mappings=mappings,
                clip=None,
            )
            source_lines = _group_glyphs_into_lines(
                glyphs,
                page_number=page_number,
                baseline_tolerance=3.2,
            )
            label_lines = [
                line
                for line in source_lines
                if _printed_exercise_label(
                    line.decoded_text or line.raw_text
                )
            ]
            is_exercise_page = bool(label_lines) or any(
                "exercises"
                in str(line.decoded_text or line.raw_text).strip().lower()
                for line in source_lines
            )
            for label_line in label_lines:
                exercise_id = _printed_exercise_label(
                    label_line.decoded_text or label_line.raw_text
                )
                ranked: list[tuple[float, str, dict[str, Any]]] = []
                label_center = (
                    label_line.bbox[0] + label_line.bbox[2]
                ) / 2.0
                for record in page_records:
                    diagram_id = str(
                        record.get("diagram_id") or record.get("id") or ""
                    )
                    if not diagram_id or diagram_id in used_diagram_ids:
                        continue
                    bbox = _bbox4(record.get("bbox"))
                    if bbox is None:
                        continue
                    vertical_gap = bbox[1] - label_line.bbox[3]
                    if vertical_gap < -4.0 or vertical_gap > 48.0:
                        continue
                    diagram_center = (bbox[0] + bbox[2]) / 2.0
                    if abs(label_center - diagram_center) > max(
                        72.0, (bbox[2] - bbox[0]) * 0.7
                    ):
                        continue
                    score = vertical_gap + abs(
                        label_center - diagram_center
                    ) * 0.04
                    ranked.append((score, diagram_id, record))
                ranked.sort(key=lambda item: (item[0], item[1]))
                selected = ranked[0] if ranked else None
                ambiguous = bool(
                    len(ranked) > 1
                    and abs(ranked[1][0] - ranked[0][0]) < 3.0
                )
                if selected is None or ambiguous:
                    assignments.append(
                        {
                            "exercise_id": exercise_id,
                            "source_page": page_number,
                            "label_bbox": list(label_line.bbox),
                            "status": (
                                "ambiguous" if ambiguous else "orphan_label"
                            ),
                            "diagram_id": "",
                            "raw_label": (
                                label_line.decoded_text
                                or label_line.raw_text
                            ).strip(),
                            "source": "source_text_geometry",
                            "confidence": 1.0,
                            "auto_accepted": False,
                            "decision_evidence": [
                                "printed_source_label",
                                (
                                    "geometry_ambiguous"
                                    if ambiguous
                                    else "diagram_not_detected"
                                ),
                            ],
                        }
                    )
                    continue
                _, diagram_id, record = selected
                used_diagram_ids.add(diagram_id)
                record["exercise_id"] = exercise_id
                record["diagram_number"] = exercise_id
                record["exercise_label_source"] = "source_text_geometry"
                record["exercise_label_bbox"] = list(label_line.bbox)
                assignments.append(
                    {
                        "exercise_id": exercise_id,
                        "source_page": page_number,
                        "label_bbox": list(label_line.bbox),
                        "status": "exact",
                        "diagram_id": diagram_id,
                        "diagram_fingerprint": _record_fingerprint(record),
                        "diagram_bbox": list(_bbox4(record.get("bbox")) or ()),
                        "raw_label": (
                            label_line.decoded_text
                            or label_line.raw_text
                        ).strip(),
                        "source": "source_text_geometry",
                        "confidence": 1.0,
                        "auto_accepted": True,
                        "decision_evidence": [
                            "printed_source_label",
                            "unique_geometry_match",
                        ],
                    }
                )
            if enable_vision_ocr and (
                is_exercise_page or scan_all_diagram_pages
            ):
                assignments.extend(
                    _ocr_exercise_label_candidates(
                        page,
                        page_records,
                        used_diagram_ids=used_diagram_ids,
                    )
                )

    _resolve_unambiguous_label_candidates(
        assignments,
        records,
        page_widths=page_widths,
        known_exercise_ids=known_exercise_ids,
    )
    resolved_by_diagram = {
        str(item.get("diagram_id") or ""): item
        for item in assignments
        if item.get("status") in {"exact", "consensus"}
        and item.get("auto_accepted")
        and item.get("diagram_id")
    }
    for record in records:
        diagram_id = _record_id(record)
        assignment = resolved_by_diagram.get(diagram_id)
        if assignment is None:
            continue
        exercise_id = str(assignment.get("exercise_id") or "")
        record["exercise_id"] = exercise_id
        record["diagram_number"] = exercise_id
        record["exercise_label_source"] = str(
            assignment.get("source") or ""
        )
        record["exercise_label_bbox"] = list(
            assignment.get("label_bbox") or []
        )
        record["exercise_label_confidence"] = float(
            assignment.get("confidence") or 0.0
        )
        record["exercise_label_status"] = str(
            assignment.get("status") or ""
        )
    exact_count = len(
        [item for item in assignments if item["status"] == "exact"]
    )
    consensus_count = len(
        [item for item in assignments if item["status"] == "consensus"]
    )
    candidate_count = len(
        [item for item in assignments if item["status"] == "candidate"]
    )
    conflict_count = len(
        [
            item
            for item in assignments
            if item["status"] in {"ambiguous", "conflict"}
        ]
    )
    resolved_count = exact_count + consensus_count
    assignment_sources = Counter(
        str(item.get("source") or "unknown") for item in assignments
    )
    return {
        "schema": "kindlemaster.source_exercise_diagram_binding.v2",
        "records": records,
        "assignments": assignments,
        "summary": {
            "diagram_count": len(records),
            "label_count": len(assignments),
            "exact_assignment_count": exact_count,
            "consensus_assignment_count": consensus_count,
            "candidate_assignment_count": candidate_count,
            "conflict_assignment_count": conflict_count,
            "review_assignment_count": len(assignments) - resolved_count,
            "unassigned_diagram_count": len(records) - resolved_count,
            "assignment_source_counts": dict(
                sorted(assignment_sources.items())
            ),
            "vision_policy": "candidate_generation_only",
            "vision_scan_scope": (
                "all_diagram_pages"
                if scan_all_diagram_pages
                else "source_labeled_exercise_pages"
            ),
            "auto_accept_policy": (
                "source_exact_or_independent_deterministic_consensus"
            ),
        },
    }


def replay_source_notation_blocks(
    source_payload: Mapping[str, Any],
    diagram_records: Iterable[Mapping[str, Any]],
    *,
    exercise_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay exact exercise/diagram bindings and keep uncertainty review-only."""

    from chess_pgn_extractor import (
        annotate_records_with_replayed_fens,
        extract_chess_pgn_records_from_text,
    )

    diagram_rows = [dict(record) for record in diagram_records]
    exercise_resolution: dict[str, str] = {}
    if exercise_index is not None:
        from chess_exercise_index import resolved_diagrams_by_exercise

        diagrams_by_exercise = resolved_diagrams_by_exercise(
            exercise_index,
            diagram_rows,
        )
        exercise_resolution = {
            _exercise_label(str(record.get("exercise_id") or "")): str(
                record.get("resolution_status") or ""
            )
            for record in exercise_index.get("records") or []
            if isinstance(record, Mapping)
            and _exercise_label(str(record.get("exercise_id") or ""))
        }
    else:
        diagrams_by_exercise: dict[str, list[dict[str, Any]]] = {}
        for row in diagram_rows:
            exercise_id = _exercise_label(
                str(
                    row.get("exercise_id")
                    or row.get("diagram_number")
                    or row.get("printed_exercise_number")
                    or ""
                )
            )
            if exercise_id:
                diagrams_by_exercise.setdefault(exercise_id, []).append(row)

    accepted = 0
    review = 0
    model_route_counts: dict[str, int] = {}
    pages: dict[str, Any] = {}
    for page_key, page_value in (
        source_payload.get("pages") or {}
    ).items():
        page = dict(page_value)
        replayed_blocks: list[dict[str, Any]] = []
        for raw_block in page.get("solution_blocks") or []:
            block = dict(raw_block)
            exercise_id = _exercise_label(
                str(block.get("exercise_id") or "")
            )
            matches = diagrams_by_exercise.get(exercise_id, [])
            blockers = list(block.get("blockers") or [])
            accepted_pgn = ""
            final_fen = ""
            replay_warnings: list[str] = []
            diagram_id = ""
            diagram_page = 0
            fen = ""
            if len(matches) != 1:
                resolution_status = exercise_resolution.get(exercise_id, "")
                if exercise_index is not None and resolution_status:
                    blockers.append(
                        f"exercise_index_{resolution_status}"
                    )
                else:
                    blockers.append(
                        "ambiguous_diagram_binding"
                        if matches
                        else "missing_diagram_binding"
                    )
            else:
                diagram = matches[0]
                diagram_id = str(
                    diagram.get("diagram_id") or diagram.get("id") or ""
                )
                diagram_page = int(
                    diagram.get("page_number") or diagram.get("page") or 0
                )
                fen = _trusted_full_fen(diagram)
                if not fen:
                    blockers.append("missing_trusted_full_fen")
            notation_text = str(
                block.get("notation_text")
                or block.get("decoded_text")
                or ""
            ).strip()
            if not notation_text:
                blockers.append("missing_notation_text")
            if not blockers:
                records = extract_chess_pgn_records_from_text(
                    notation_text,
                    page_num=max(
                        0, int(block.get("page_number") or 1) - 1
                    ),
                    source_title=f"Exercise {exercise_id}",
                    ocr_confidence=1.0,
                    fen_candidates=[fen],
                )
                records = annotate_records_with_replayed_fens(records)
                if len(records) != 1:
                    blockers.append(
                        "multiple_pgn_records"
                        if len(records) > 1
                        else "pgn_record_not_found"
                    )
                else:
                    record = records[0]
                    replay_warnings = list(record.warnings)
                    if (
                        record.status == "accepted"
                        and record.final_fen
                        and not replay_warnings
                    ):
                        accepted_pgn = record.pgn
                        final_fen = record.final_fen
                    else:
                        blockers.extend(
                            replay_warnings or ["pgn_replay_failed"]
                        )
            status = "accepted" if accepted_pgn else "review"
            model_route = _notation_model_route(blockers)
            route_name = str(model_route["route"])
            model_route_counts[route_name] = (
                model_route_counts.get(route_name, 0) + 1
            )
            accepted += int(status == "accepted")
            review += int(status != "accepted")
            block.update(
                {
                    "exercise_id": exercise_id,
                    "diagram_id": diagram_id,
                    "diagram_page": diagram_page,
                    "fen": fen,
                    "replay_status": status,
                    "accepted_pgn": accepted_pgn,
                    "final_fen": final_fen,
                    "replay_warnings": sorted(set(replay_warnings)),
                    "blockers": sorted(set(blockers)),
                    "model_route": model_route,
                }
            )
            replayed_blocks.append(block)
        page["solution_blocks"] = replayed_blocks
        pages[str(page_key)] = page
    return {
        **dict(source_payload),
        "pages": pages,
        "replay_summary": {
            "solution_block_count": accepted + review,
            "accepted_count": accepted,
            "review_count": review,
            "model_route_counts": dict(sorted(model_route_counts.items())),
            "acceptance_policy": "deterministic_parser_and_full_replay",
            "model_policy": {
                "deterministic_layers": [
                    "source_geometry",
                    "font_sha_gid_decode",
                    "python_chess_replay",
                ],
                "ai_role": "candidate_generation_only",
                "ai_may_auto_accept": False,
            },
            "exercise_index_summary": (
                dict(exercise_index.get("summary") or {})
                if exercise_index is not None
                else {}
            ),
        },
    }


def validate_san_line(fen: str, san_line: str) -> dict[str, Any]:
    import chess

    board = chess.Board(fen)
    moves: list[str] = []
    for token in san_line.split():
        if token[0].isdigit() and "." in token:
            _, _, suffix = token.partition(".")
            token = suffix.lstrip(".")
            if not token:
                continue
        try:
            move = board.parse_san(token)
        except ValueError as error:
            return {
                "status": "invalid",
                "moves_replayed": len(moves),
                "failed_token": token,
                "error": str(error),
                "final_fen": board.fen(),
            }
        moves.append(token)
        board.push(move)
    return {
        "status": "valid" if moves else "invalid",
        "moves_replayed": len(moves),
        "failed_token": "",
        "error": "" if moves else "no_moves",
        "final_fen": board.fen(),
        "outcome": str(board.outcome() or ""),
    }


def normalize_decoded_notation(value: str) -> str:
    text = str(value or "")
    text = re.sub(
        r"\b(\d+)\s*\.\s*\.\s*\.\s*(?=[KQRBN])",
        r"\1...",
        text,
    )
    text = re.sub(r"\b(\d+)\s+\.", r"\1.", text)
    text = re.sub(r"\b(\d+\.\.\.)\s+(?=[KQRBN])", r"\1", text)
    text = re.sub(r"\b([KQRBN])\s+([a-h][1-8])", r"\1\2", text)
    text = re.sub(r"(?<=[a-h][1-8])(?=\d{1,3}\.)", " ", text)
    text = re.sub(r"(?<=[+#?!])(?=\d{1,3}\.)", " ", text)
    text = re.sub(r"(?<=[a-h1-8])\s+([+#])", r"\1", text)
    text = re.sub(r"\s+([,.)])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def looks_like_decoded_notation_line(value: str) -> bool:
    text = normalize_decoded_notation(value)
    san_move = (
        r"(?:O-O(?:-O)?|"
        r"[KQRBN]?[a-h1-8]{0,2}x?[a-h][1-8](?:=[QRBN])?[+#]?)"
    )
    return bool(
        re.search(
            r"(?<![\d.])\d{1,3}\.(?:\.\.)?\s*"
            rf"(?:\[\[gid:\d+\]\]|{san_move})",
            text,
        )
        or re.search(
            r"(?<![\d.])\d{1,3}\.(?:\.\.)?[^\n]{0,24}\[\[gid:\d+\]\]",
            text,
        )
    )


def _exercise_label(value: str) -> str:
    text = str(value or "").strip()
    match = EXERCISE_LABEL_RE.search(text)
    if match:
        return f"{int(match.group('chapter'))}-{int(match.group('number'))}"
    compact = re.fullmatch(
        r"\s*(?P<chapter>\d{1,3})\s*[-.]\s*(?P<number>\d{1,3})\s*",
        text,
    )
    if compact:
        return f"{int(compact.group('chapter'))}-{int(compact.group('number'))}"
    return ""


def _printed_exercise_label(value: str) -> str:
    text = re.sub(
        rf"{re.escape(UNKNOWN_GLYPH_PREFIX)}\d+\]\]",
        "",
        str(value or "").strip(),
    )
    match = PRINTED_EXERCISE_LABEL_RE.search(text)
    if not match:
        return ""
    return f"{int(match.group('chapter'))}-{int(match.group('number'))}"


def _meaningful_solution_line(value: str) -> bool:
    text = normalize_decoded_notation(value)
    if not text:
        return False
    if re.fullmatch(r"\d{1,3}", text):
        return False
    if re.fullmatch(r"[a-h](?:\s+[a-h]){3,7}", text, flags=re.I):
        return False
    return True


def _bbox4(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    try:
        x0, y0, x1, y1 = (float(part) for part in value[:4])
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _ocr_exercise_label_candidates(
    page: fitz.Page,
    page_records: Sequence[Mapping[str, Any]],
    *,
    used_diagram_ids: set[str],
) -> list[dict[str, Any]]:
    candidate_records = [
        dict(record)
        for record in page_records
        if _record_id(record)
        and _record_id(record) not in used_diagram_ids
        and _record_is_confirmed(record)
        and _bbox4(record.get("bbox")) is not None
    ]
    if not candidate_records:
        return []
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageOps

        from ocr_module import configure_tesseract

        configure_tesseract(pytesseract)
    except Exception:
        return []

    scale = 4.0
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
    )
    mode = "RGB" if pixmap.n >= 3 else "L"
    page_image = Image.frombytes(
        mode,
        (pixmap.width, pixmap.height),
        pixmap.samples,
    )
    bands: list[dict[str, Any]] = []
    for record in sorted(
        candidate_records,
        key=lambda item: _record_id(item),
    ):
        bbox = _bbox4(record.get("bbox"))
        if bbox is None:
            continue
        label_bbox = (
            max(0.0, bbox[0] - 16.0),
            max(0.0, bbox[1] - 28.0),
            min(float(page.rect.width), bbox[2] + 8.0),
            min(float(page.rect.height), bbox[1] + 2.0),
        )
        crop_box = tuple(int(round(value * scale)) for value in label_bbox)
        crop = page_image.crop(crop_box)
        prepared = ImageEnhance.Contrast(
            ImageOps.grayscale(crop)
        ).enhance(2.0)
        bands.append(
            {
                "record": record,
                "label_bbox": label_bbox,
                "image": prepared,
            }
        )
    if not bands:
        return []

    width = max(band["image"].width for band in bands)
    gap = 24
    height = sum(band["image"].height + gap for band in bands)
    montage = Image.new("L", (width, height), 255)
    cursor = 0
    for band in bands:
        image = band["image"]
        montage.paste(image, (0, cursor))
        band["montage_y0"] = cursor
        band["montage_y1"] = cursor + image.height
        cursor += image.height + gap

    try:
        data = pytesseract.image_to_data(
            montage,
            lang="eng",
            config="--psm 6",
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        return []

    assignments: list[dict[str, Any]] = []
    for band in bands:
        raw_label, confidence = _ocr_band_text(
            data,
            y0=int(band["montage_y0"]),
            y1=int(band["montage_y1"]),
        )
        exercise_id = _exercise_label(raw_label)
        if not exercise_id or confidence < 0.72:
            try:
                individual_data = pytesseract.image_to_data(
                    band["image"],
                    lang="eng",
                    config="--psm 7",
                    output_type=pytesseract.Output.DICT,
                )
                individual_label, individual_confidence = _ocr_band_text(
                    individual_data,
                    y0=0,
                    y1=band["image"].height,
                )
                individual_id = _exercise_label(individual_label)
                if individual_id:
                    raw_label = individual_label
                    exercise_id = individual_id
                    confidence = individual_confidence
            except Exception:
                pass
        if not exercise_id:
            continue
        record = band["record"]
        diagram_bbox = _bbox4(record.get("bbox")) or ()
        assignments.append(
            {
                "exercise_id": exercise_id,
                "source_page": int(page.number) + 1,
                "label_bbox": list(band["label_bbox"]),
                "diagram_bbox": list(diagram_bbox),
                "status": "candidate",
                "diagram_id": _record_id(record),
                "diagram_fingerprint": _record_fingerprint(record),
                "raw_label": raw_label,
                "source": "tesseract_label_crop",
                "confidence": round(confidence, 6),
                "auto_accepted": False,
                "decision_evidence": [
                    "source_bound_label_crop",
                    "vision_ocr_candidate",
                ],
                "blockers": [
                    "vision_candidate_requires_deterministic_consensus"
                ],
                "model_route": {
                    "route": "exercise_label_vision_candidate",
                    "model_tier": "medium_vision",
                    "fallback_model_tier": "high_vision",
                    "purpose": "read_printed_exercise_label_from_crop",
                    "auto_accept": False,
                },
            }
        )
    return assignments


def _ocr_band_text(
    data: Mapping[str, Sequence[Any]],
    *,
    y0: int,
    y1: int,
) -> tuple[str, float]:
    words: list[str] = []
    confidences: list[float] = []
    texts = list(data.get("text") or [])
    tops = list(data.get("top") or [])
    raw_confidences = list(data.get("conf") or [])
    for index, raw_text in enumerate(texts):
        text = str(raw_text or "").strip()
        if not text:
            continue
        try:
            top = int(tops[index])
        except (IndexError, TypeError, ValueError):
            continue
        if top < y0 - 3 or top >= y1 + 3:
            continue
        words.append(text)
        try:
            confidence = float(raw_confidences[index])
        except (IndexError, TypeError, ValueError):
            confidence = -1.0
        if confidence >= 0.0:
            confidences.append(confidence)
    score = (
        sum(confidences) / len(confidences) / 100.0
        if confidences
        else 0.0
    )
    return " ".join(words).strip(), max(0.0, min(1.0, score))


def _resolve_unambiguous_label_candidates(
    assignments: list[dict[str, Any]],
    records: Sequence[Mapping[str, Any]],
    *,
    page_widths: Mapping[int, float],
    known_exercise_ids: Iterable[str] | None,
) -> None:
    known = {
        _exercise_label(str(value or ""))
        for value in (known_exercise_ids or ())
    }
    known.discard("")
    if not known:
        return
    diagrams_by_id = {
        _record_id(record): dict(record)
        for record in records
        if _record_id(record)
    }
    resolved_ids = {
        str(item.get("exercise_id") or "")
        for item in assignments
        if item.get("status") == "exact"
        and item.get("auto_accepted")
    }
    candidate_id_counts = Counter(
        str(item.get("exercise_id") or "")
        for item in assignments
        if item.get("status") == "candidate"
    )
    candidate_diagram_counts = Counter(
        str(item.get("diagram_id") or "")
        for item in assignments
        if item.get("status") == "candidate"
    )
    assignments_by_page: dict[int, list[dict[str, Any]]] = {}
    for assignment in assignments:
        assignments_by_page.setdefault(
            int(assignment.get("source_page") or 0), []
        ).append(assignment)

    for page_number, page_assignments in assignments_by_page.items():
        anchored_chapters = {
            exercise_id.split("-", 1)[0]
            for item in page_assignments
            if item.get("source") == "source_text_geometry"
            for exercise_id in [
                _exercise_label(str(item.get("exercise_id") or ""))
            ]
            if exercise_id
        }
        if len(anchored_chapters) != 1:
            continue
        page_width = float(page_widths.get(page_number) or 0.0)
        ordered = [
            item
            for item in page_assignments
            if item.get("diagram_id")
            and item.get("status") in {"exact", "candidate"}
        ]
        ordered.sort(
            key=lambda item: _diagram_visual_sort_key(
                diagrams_by_id.get(str(item.get("diagram_id") or ""), {}),
                page_width=page_width,
            )
        )
        ordered_ids = [
            _exercise_label(str(item.get("exercise_id") or ""))
            for item in ordered
        ]
        numeric_order = [
            tuple(int(part) for part in exercise_id.split("-", 1))
            for exercise_id in ordered_ids
            if exercise_id
        ]
        if (
            len(numeric_order) != len(ordered)
            or len({chapter for chapter, _ in numeric_order}) != 1
            or any(
                right <= left
                for (_, left), (_, right) in zip(
                    numeric_order,
                    numeric_order[1:],
                )
            )
        ):
            for item in ordered:
                if item.get("status") == "candidate":
                    item["blockers"] = sorted(
                        {
                            *list(item.get("blockers") or []),
                            "visual_order_conflict",
                        }
                    )
            continue

        for item in ordered:
            if item.get("status") != "candidate":
                continue
            exercise_id = _exercise_label(
                str(item.get("exercise_id") or "")
            )
            diagram_id = str(item.get("diagram_id") or "")
            chapter = (
                exercise_id.split("-", 1)[0] if exercise_id else ""
            )
            decision_evidence = list(
                item.get("decision_evidence") or []
            )
            blockers = set(item.get("blockers") or [])
            checks = {
                "known_solution_id": exercise_id in known,
                "chapter_anchor_match": chapter in anchored_chapters,
                "unique_exercise_candidate": (
                    candidate_id_counts[exercise_id] == 1
                    and exercise_id not in resolved_ids
                ),
                "unique_diagram_candidate": (
                    candidate_diagram_counts[diagram_id] == 1
                ),
                "vision_confidence": float(
                    item.get("confidence") or 0.0
                )
                >= 0.72,
                "visual_order_monotonic": True,
            }
            for check, passed in checks.items():
                if passed:
                    decision_evidence.append(check)
                else:
                    blockers.add(f"{check}_failed")
            if all(checks.values()):
                item["status"] = "consensus"
                item["auto_accepted"] = True
                item["source"] = "deterministic_vision_consensus"
                blockers.discard(
                    "vision_candidate_requires_deterministic_consensus"
                )
                resolved_ids.add(exercise_id)
            item["decision_evidence"] = sorted(set(decision_evidence))
            item["blockers"] = sorted(blockers)


def _diagram_visual_sort_key(
    record: Mapping[str, Any],
    *,
    page_width: float,
) -> tuple[int, float, float, str]:
    bbox = _bbox4(
        record.get("bbox")
        or record.get("board_bbox")
        or record.get("bbox_xyxy")
    )
    if bbox is None:
        return (2, float("inf"), float("inf"), _record_id(record))
    center = (bbox[0] + bbox[2]) / 2.0
    column = 0 if not page_width or center < page_width / 2.0 else 1
    return (column, bbox[1], bbox[0], _record_id(record))


def _record_id(record: Mapping[str, Any]) -> str:
    return str(
        record.get("diagram_id") or record.get("id") or ""
    ).strip()


def _record_fingerprint(record: Mapping[str, Any]) -> str:
    return str(
        record.get("diagram_fingerprint")
        or record.get("verified_diagram_fingerprint")
        or ""
    ).strip()


def _record_is_confirmed(record: Mapping[str, Any]) -> bool:
    return bool(
        record.get("confirmed_diagram", True)
        and record.get("publication_included", True)
    )


def _trusted_full_fen(record: Mapping[str, Any]) -> str:
    fen = str(record.get("full_fen") or record.get("fen") or "").strip()
    if not fen:
        return ""
    trusted = bool(
        record.get("fen_human_verified") is True
        or record.get("human_verified") is True
        or record.get("full_fen_allowed") is True
        or "HUMAN_VERIFIED" in str(record.get("full_fen_status") or "")
    )
    if not trusted:
        return ""
    try:
        import chess

        chess.Board(fen)
    except Exception:
        return ""
    return fen


def _notation_model_route(blockers: Iterable[str]) -> dict[str, Any]:
    codes = {str(code) for code in blockers if str(code)}
    if not codes:
        return {
            "route": "deterministic_only",
            "model_tier": "none",
            "purpose": "accepted_by_parser_and_replay",
            "auto_accept": False,
        }
    if (
        "missing_diagram_binding" in codes
        or "exercise_index_candidate" in codes
        or "exercise_index_orphan_solution" in codes
    ):
        return {
            "route": "diagram_label_vision_candidate",
            "model_tier": "high_vision",
            "purpose": "read_printed_exercise_label_from_source_crop",
            "auto_accept": False,
        }
    if any(code.startswith("exercise_index_conflict") for code in codes):
        return {
            "route": "exercise_binding_conflict_review",
            "model_tier": "none",
            "purpose": "resolve_conflicting_source_evidence",
            "auto_accept": False,
        }
    if any(
        code.startswith("unmapped_source_glyph")
        or code.startswith("suspicious_decoded")
        for code in codes
    ):
        return {
            "route": "source_glyph_vision_candidate",
            "model_tier": "high_vision",
            "purpose": "propose_font_bound_glyph_mapping",
            "auto_accept": False,
        }
    if "missing_trusted_full_fen" in codes:
        return {
            "route": "fen_review",
            "model_tier": "none",
            "purpose": "obtain_verified_position_and_side_to_move",
            "auto_accept": False,
        }
    return {
        "route": "notation_repair_candidate",
        "model_tier": "high_reasoning",
        "purpose": "propose_candidates_for_deterministic_replay",
        "auto_accept": False,
    }


def _default_mapping_paths() -> list[Path]:
    return sorted(
        (Path(__file__).resolve().parent / "chess_glyph_maps").glob("*.json")
    )


def _page_font_fingerprints(
    document: fitz.Document,
    page: fitz.Page,
) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for font in page.get_fonts(full=True):
        xref = int(font[0])
        base_name = _normalize_font_name(str(font[3] or ""))
        try:
            _, _, _, data = document.extract_font(xref)
        except Exception:
            continue
        if not data:
            continue
        fingerprints[base_name] = hashlib.sha256(data).hexdigest()
    return fingerprints


def _source_glyphs(
    page: fitz.Page,
    *,
    font_fingerprints: Mapping[str, str],
    mappings: Mapping[str, Mapping[str, Any]],
    clip: Sequence[float] | None,
) -> list[SourceGlyph]:
    result: list[SourceGlyph] = []
    clip_rect = fitz.Rect(clip) if clip is not None else None
    for span in page.get_texttrace():
        font_name = _normalize_font_name(str(span.get("font") or ""))
        fingerprint = str(font_fingerprints.get(font_name) or "")
        mapping = mappings.get(fingerprint)
        strict = bool((mapping or {}).get("strict"))
        replacements = (mapping or {}).get("glyphs") or {}
        span_start = len(result)
        previous_origin: tuple[float, float] | None = None
        previous_status = ""
        source_bound_origin: tuple[float, float] | None = None
        for raw_char in span.get("chars") or []:
            unicode_value = int(raw_char[0])
            glyph_id = int(raw_char[1])
            origin = tuple(float(value) for value in raw_char[2][:2])
            bbox = tuple(float(value) for value in raw_char[3][:4])
            if clip_rect is not None:
                glyph_rect = fitz.Rect(bbox)
                if glyph_rect.is_empty:
                    if not clip_rect.contains(fitz.Point(origin)):
                        continue
                elif not glyph_rect.intersects(clip_rect):
                    continue
            raw_unicode = chr(unicode_value)
            duplicate_origin = (
                previous_origin is not None
                and all(
                    abs(previous_origin[index] - origin[index]) <= 0.01
                    for index in range(2)
                )
            )
            source_bound_continuation = (
                glyph_id < 0
                and source_bound_origin is not None
                and all(
                    abs(source_bound_origin[index] - origin[index]) <= 0.01
                    for index in range(2)
                )
            )
            if (
                source_bound_continuation
                or (
                    glyph_id < 0
                    and duplicate_origin
                    and previous_status == "unmapped_source_glyph"
                )
            ):
                decoded = ""
                status = "synthetic_to_unicode_continuation"
            elif glyph_id < 0 and raw_unicode != "\ufffd":
                decoded = raw_unicode
                status = "unicode_fallback"
            elif glyph_id in replacements:
                decoded = str(replacements[glyph_id])
                status = "source_bound_mapping"
            elif strict:
                decoded = f"{UNKNOWN_GLYPH_PREFIX}{glyph_id}]]"
                status = "unmapped_source_glyph"
            elif raw_unicode == "\ufffd":
                decoded = f"{UNKNOWN_GLYPH_PREFIX}{glyph_id}]]"
                status = "unmapped_source_glyph"
            else:
                decoded = raw_unicode
                status = "unicode_fallback"
            result.append(
                SourceGlyph(
                    font_name=font_name,
                    font_fingerprint=fingerprint,
                    glyph_id=glyph_id,
                    raw_unicode=raw_unicode,
                    decoded_text=decoded,
                    origin=origin,
                    bbox=bbox,
                    status=status,
                )
            )
            previous_origin = origin
            previous_status = status
            if status in {"source_bound_mapping", "unmapped_source_glyph"}:
                source_bound_origin = origin
            elif glyph_id >= 0:
                source_bound_origin = None
        if mapping:
            result[span_start:] = _apply_source_sequence_mappings(
                result[span_start:],
                mapping.get("sequences") or (),
            )
            result[span_start:] = _mark_suspicious_decoded_patterns(
                result[span_start:],
                mapping.get("suspicious_decoded_patterns") or (),
            )
    return result


def _load_glyph_sequences(value: Any) -> tuple[tuple[tuple[int, ...], str], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    sequences: list[tuple[tuple[int, ...], str]] = []
    for row in value:
        if not isinstance(row, Mapping):
            continue
        raw_ids = row.get("glyph_ids")
        if (
            not isinstance(raw_ids, Sequence)
            or isinstance(raw_ids, (str, bytes))
            or not raw_ids
        ):
            continue
        try:
            glyph_ids = tuple(int(glyph_id) for glyph_id in raw_ids)
        except (TypeError, ValueError):
            continue
        if any(glyph_id < 0 for glyph_id in glyph_ids):
            continue
        sequences.append((glyph_ids, str(row.get("replacement") or "")))
    return tuple(
        sorted(sequences, key=lambda item: len(item[0]), reverse=True)
    )


def _apply_source_sequence_mappings(
    glyphs: Sequence[SourceGlyph],
    sequences: Sequence[tuple[tuple[int, ...], str]],
) -> list[SourceGlyph]:
    result = list(glyphs)
    primary_indices = [
        index for index, glyph in enumerate(result) if glyph.glyph_id >= 0
    ]
    claimed: set[int] = set()
    for primary_offset in range(len(primary_indices)):
        if primary_offset in claimed:
            continue
        for glyph_ids, replacement in sequences:
            end_offset = primary_offset + len(glyph_ids)
            if end_offset > len(primary_indices):
                continue
            actual = tuple(
                result[primary_indices[offset]].glyph_id
                for offset in range(primary_offset, end_offset)
            )
            if actual != glyph_ids:
                continue
            start_index = primary_indices[primary_offset]
            end_index = (
                primary_indices[end_offset]
                if end_offset < len(primary_indices)
                else len(result)
            )
            for index in range(start_index, end_index):
                result[index] = replace(
                    result[index],
                    decoded_text=replacement if index == start_index else "",
                    status=(
                        "source_bound_mapping"
                        if index == start_index
                        else "synthetic_to_unicode_continuation"
                    ),
                )
            claimed.update(range(primary_offset, end_offset))
            break
    return result


def _mark_suspicious_decoded_patterns(
    glyphs: Sequence[SourceGlyph],
    patterns: Sequence[str],
) -> list[SourceGlyph]:
    result = list(glyphs)
    decoded = "".join(glyph.decoded_text for glyph in result)
    if not result or not decoded:
        return result
    for pattern in patterns:
        if pattern not in decoded:
            continue
        index = next(
            (
                glyph_index
                for glyph_index, glyph in enumerate(result)
                if glyph.decoded_text
            ),
            0,
        )
        result[index] = replace(
            result[index],
            status=f"suspicious_decoded_ligature:{pattern}",
        )
    return result


def _group_glyphs_into_lines(
    glyphs: Sequence[SourceGlyph],
    *,
    page_number: int,
    baseline_tolerance: float,
) -> list[SourceNotationLine]:
    rows: list[list[SourceGlyph]] = []
    row_baselines: list[float] = []
    for glyph in sorted(glyphs, key=lambda item: (item.origin[1], item.origin[0])):
        baseline = glyph.origin[1]
        match_index = next(
            (
                index
                for index, value in enumerate(row_baselines)
                if abs(value - baseline) <= baseline_tolerance
            ),
            None,
        )
        if match_index is None:
            rows.append([glyph])
            row_baselines.append(baseline)
        else:
            rows[match_index].append(glyph)
            count = len(rows[match_index])
            row_baselines[match_index] = (
                row_baselines[match_index] * (count - 1) + baseline
            ) / count
    lines: list[SourceNotationLine] = []
    for baseline, row in sorted(
        zip(row_baselines, rows),
        key=lambda item: item[0],
    ):
        ordered = sorted(row, key=lambda item: (item.origin[0], item.bbox[0]))
        for segment in _split_glyph_row(ordered):
            blockers = sorted(
                {
                    "unmapped_source_glyph:"
                    f"{glyph.font_fingerprint}:{glyph.glyph_id}"
                    for glyph in segment
                    if glyph.status == "unmapped_source_glyph"
                }
                | {
                    glyph.status
                    for glyph in segment
                    if glyph.status.startswith(
                        "suspicious_decoded_ligature:"
                    )
                }
            )
            lines.append(
                SourceNotationLine(
                    page_number=page_number,
                    baseline=baseline,
                    bbox=_union_bbox(glyph.bbox for glyph in segment),
                    raw_text="".join(
                        glyph.raw_unicode for glyph in segment
                    ),
                    decoded_text="".join(
                        glyph.decoded_text for glyph in segment
                    ),
                    glyphs=tuple(segment),
                    blockers=tuple(blockers),
                )
            )
    return sorted(lines, key=lambda line: (line.baseline, line.bbox[0]))


def _split_glyph_row(
    glyphs: Sequence[SourceGlyph],
    *,
    maximum_horizontal_gap: float = 12.0,
) -> list[list[SourceGlyph]]:
    segments: list[list[SourceGlyph]] = []
    for glyph in glyphs:
        if (
            segments
            and glyph.bbox[0] - max(
                previous.bbox[2] for previous in segments[-1]
            ) > maximum_horizontal_gap
        ):
            segments.append([])
        if not segments:
            segments.append([])
        segments[-1].append(glyph)
    return [segment for segment in segments if segment]


def _order_notation_lines_by_columns(
    lines: Sequence[SourceNotationLine],
    *,
    page_width: float,
) -> list[SourceNotationLine]:
    ordered = sorted(lines, key=lambda line: (line.baseline, line.bbox[0]))
    if page_width <= 0 or len(ordered) < 4:
        return ordered
    midpoint = page_width / 2.0
    gutter = max(4.0, page_width * 0.015)
    left = [
        line for line in ordered if line.bbox[2] <= midpoint + gutter
    ]
    right = [
        line for line in ordered if line.bbox[0] >= midpoint - gutter
    ]
    spanning = [
        line for line in ordered if line not in left and line not in right
    ]
    if len(left) < 2 or len(right) < 2 or spanning:
        return ordered
    return sorted(left, key=lambda line: (line.baseline, line.bbox[0])) + sorted(
        right,
        key=lambda line: (line.baseline, line.bbox[0]),
    )


def _normalize_font_name(value: str) -> str:
    name = value.split("+", 1)[-1].strip("/")
    for suffix in ("-Identity-H", "-Identity-V"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _union_bbox(
    values: Iterable[Sequence[float]],
) -> tuple[float, float, float, float]:
    boxes = [tuple(float(value) for value in bbox[:4]) for bbox in values]
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(bbox[0] for bbox in boxes),
        min(bbox[1] for bbox in boxes),
        max(bbox[2] for bbox in boxes),
        max(bbox[3] for bbox in boxes),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
