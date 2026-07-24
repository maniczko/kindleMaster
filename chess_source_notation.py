from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import fitz


SOURCE_NOTATION_SCHEMA = "kindlemaster.source_bound_chess_notation.v1"
UNKNOWN_GLYPH_PREFIX = "[[gid:"


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
            }
    return {
        "schema": SOURCE_NOTATION_SCHEMA,
        "source_pdf": str(source.resolve()),
        "source_pdf_sha256": _file_sha256(source),
        "pages": pages,
    }


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
