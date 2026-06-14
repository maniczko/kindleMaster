from __future__ import annotations

import html
import io
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping


RESULT_VALUES = {"1-0", "0-1", "1/2-1/2", "0.5-0.5", "*"}
UNMAPPED_CHESS_GLYPH_WARNING = "unmapped_chess_glyphs"
MOVE_NUMBER_RE = re.compile(r"^(?P<num>\d{1,3})\.(?P<black>\.\.)?$")
RESULT_RE = re.compile(r"^(?:1-0|0-1|1/2-1/2|0\.5-0\.5|\*)$")
SAN_RE = re.compile(
    r"^(?:"
    r"O-O(?:-O)?"
    r"|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h][1-8](?:=[QRBN])?[+#]?"
    r")(?:[!?]{0,2})$"
)
TOKEN_SCAN_RE = re.compile(
    r"1-0|0-1|1/2-1/2|0\.5-0\.5|\*"
    r"|\d{1,3}\.(?:\.\.)?"
    r"|O-O(?:-O)?|0-0(?:-0)?"
    r"|[KQRBNPDAW@&£8]?[a-h]?[1-8]?x?[a-h][1-8](?:\s*=\s*[QRBN])?[+#†‡¢t]?[!?]{0,2}"
    r"|[a-h]x[a-h][1-8](?:\s*=\s*[QRBN])?[+#†‡¢t]?[!?]{0,2}"
    r"|[a-h][1-8](?:\s*=\s*[QRBN])?[+#†‡¢t]?[!?]{0,2}",
    re.IGNORECASE,
)
GLYPH_DIAGNOSTIC_RECORD_LIMIT = 20
GLYPH_DIAGNOSTIC_DOCUMENT_LIMIT = 500
DIAGRAM_LINE_RE = re.compile(r"^\s*(?:diagram|dia\.?)\s+\d+(?:[-.]\d+)?", re.IGNORECASE)
NOTATION_HEAVY_RE = re.compile(r"\b\d{1,3}\.(?:\.\.)?\s*\S+")
PLAYER_CAPTION_RE = re.compile(
    r"(?P<white>[A-Z][A-Za-z.'\u2019\- ]{1,40})\s+[\u2013-]\s+(?P<black>[A-Z][A-Za-z.'\u2019\- ]{1,40})"
)
YEAR_RE = re.compile(r"\b(?P<year>18|19|20)\d{2}\b")
ECO_CODE_RE = re.compile(r"\b(?P<eco>[A-Ea-e][0-9Oo]{2})\b")
COLLECTION_GAME_START_RE = re.compile(r"^\s*(?:\d{1,5}\s+)?[A-Ea-e][0-9Oo]{2}\b")
COLLECTION_NUMBERED_ECO_RE = re.compile(r"^\s*\d{1,5}\s+[A-Ea-e][0-9Oo]{2}\b")
OPENING_DESCRIPTOR_RE = re.compile(
    r"(?i)\b(?:sidelines?|including|unusual\s+lines?|variation|attack|defen[cs]e|opening|system|lines?)\b"
)
BOARD_FILES_INLINE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])a\s+b\s+c\s+d\s+e\s+f\s+g\s+h(?![A-Za-z0-9])"
)
BOARD_FILES_PREFIX_ECO_RE = re.compile(
    r"(?i)^\s*a\s+b\s+c\s+d\s+e\s+f\s+g\s+h(?=\s*\d{1,5}\s+[A-E][0-9]{2}\b)"
)
BOARD_RANK_GRID_RE = re.compile(r"(?<![\d.])(?:[1-8]\s+){3,}[1-8](?![\d.])")
ENGINE_EVAL_RE = re.compile(r"(?:[=+\-]\s*)?\d+\s*\.\s*\d+\s*/\s*\d+(?=\b|[A-Za-z])")
BARE_ENGINE_EVAL_RE = re.compile(r"(?<![\w.-])(?:[=+\-]\s*)?\d+\s*\.\s*\d{1,3}(?=(?:\b(?![-./])|[A-Za-z]))")
TACTICAL_ANALYSIS_RE = re.compile(
    r"(?i)\bTactical Analysis\s+\d+\s*\.\s*\d+\s*\([^)]*\):?(?:\s*'[^']*')?"
)
BROKEN_CASTLE_NEGATIVE_EVAL_RE = re.compile(r"\b(?P<castle>O-O|0-0)-[O0]\.\s*(?P<eval>\d+\s*/\s*\d+)\b")
MIXED_QUEENSIDE_CASTLE_RE = re.compile(r"\b[O0]-[O0]-[O0](?=[N!?+#\s,.;:\]\)}]|$)", re.IGNORECASE)
MIXED_KINGSIDE_CASTLE_RE = re.compile(r"\b[O0]-[O0](?=[N!?+#\s,.;:\]\)}]|$)", re.IGNORECASE)
WEIGHTED_ERROR_VALUE_RE = re.compile(r"(?im)^\s*Weighted Error Value:.*$")
WEIGHTED_ERROR_SIDE_RE = re.compile(r"(?i)\b(?:White|Black)\s*=\s*\d+\s*\.\s*\d+\s*/?")
OCR_HASH_ARTIFACT_RE = re.compile(r"(?i)\b0x[0-9a-f]+(?:\.\s*[0-9a-f]+)?(?:p[-+]?\d+)?[a-z]*\b")
SENSOR_BOARD_ERROR_RE = re.compile(r"(?i)\bSensor\s+Board\s+Error\s*\([^)]*\)\??")
MOJIBAKE_PGN_TOKEN_RE = re.compile(r"(?<!\w)[\"'`´’]?[A-Za-z]?[!;:<>]{2,}[A-Za-z0-9!?+\-=#]*")
ANGLE_GLYPH_TOKEN_RE = re.compile(r"<[^>\s]{1,16}>")
COMPACT_PROMOTION_RE = re.compile(
    r"\b(?P<move>[a-h](?:x[a-h])?[18])(?P<piece>[QRBN])(?=(?:[+#?!\s,.;:\]\)}]|$|=|[KQRBNP]{2,3}-[KQRBNP]{2,3}\b))"
)
PROMOTION_ENDGAME_LABEL_RE = re.compile(
    r"(?P<promo>\b[a-h](?:x[a-h])?[18]=[QRBN])(?P<label>[KQRBNP]{2,3}-[KQRBNP]{2,3}\b)"
)
PROMOTION_EQUAL_COMMENT_RE = re.compile(r"(?P<promo>\b[a-h](?:x[a-h])?[18]=[QRBN])=(?=[A-Za-z])")
CHESSBASE_PIECE_FIGURINE_MAP = {
    "\ue024": "K",
    "\ue025": "Q",
    "\ue026": "R",
    "\ue027": "B",
    "\ue028": "N",
    "\ue029": "P",
}
CHESSBASE_ANNOTATION_SYMBOL_MAP = {
    "\ue000": "\u221e=",
    "\ue005": "\u25a3",
    "\ue008": "\u2642",
    "\ue009": "\u221e",
    "\ue00a": "\u00b1",
    "\ue00c": "\u2265",
    "\ue00d": "\u25b3",
    "\ue010": "\u00d7",
    "\ue012": "\u2191",
    "\ue013": "\u2192",
    "\ue017": "\u21c4",
    "\ue018": "\u2194",
    "\ue019": "\u2197",
    "\ue01a": "\u2213",
    "\ue020": "\u2312",
    "\ue021": "\u25a1",
    "\ue024": "K",
    "\ue025": "Q",
    "\ue026": "R",
    "\ue027": "B",
    "\ue028": "N",
    "\ue029": "P",
    "\ue02e": "\u2a71",
    "\ue02f": "\u2a72",
}


@dataclass(frozen=True)
class ChessPgnRecord:
    id: str
    source_pages: list[int]
    title: str
    headers: dict[str, str]
    movetext: str
    pgn: str
    annotated_pgn: str = ""
    result: str = "*"
    confidence: float = 0.0
    status: str = "requires_review"
    warnings: list[str] = field(default_factory=list)
    fen: str = ""
    final_fen: str = ""
    fen_snapshots: list[dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""
    glyph_diagnostics: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_pages": list(self.source_pages),
            "title": self.title,
            "headers": dict(self.headers),
            "movetext": self.movetext,
            "pgn": self.pgn,
            "annotated_pgn": self.annotated_pgn,
            "result": self.result,
            "confidence": round(float(self.confidence or 0.0), 3),
            "status": self.status,
            "warnings": list(self.warnings),
            "fen": self.fen,
            "final_fen": self.final_fen,
            "fen_snapshots": [dict(snapshot) for snapshot in self.fen_snapshots],
            "raw_text": self.raw_text,
            "glyph_diagnostics": _bounded_glyph_diagnostics(self.glyph_diagnostics),
        }


@dataclass(frozen=True)
class ChessDiagramRecord:
    id: str
    page_index: int
    page_number: int
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    caption: str = ""
    image_data_uri: str = ""
    fen_candidate: str = ""
    nearby_text: str = ""
    matched_record_id: str = ""
    match_confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "page_index": self.page_index,
            "page_number": self.page_number,
            "bbox": list(self.bbox),
            "caption": self.caption,
            "image_data_uri": self.image_data_uri,
            "fen_candidate": self.fen_candidate,
            "nearby_text": self.nearby_text,
            "matched_record_id": self.matched_record_id,
            "match_confidence": round(float(self.match_confidence or 0.0), 3),
        }


@dataclass(frozen=True)
class ChessBookLayoutElement:
    type: str
    bbox: tuple[float, float, float, float]
    reading_order: int = 0
    text: str = ""
    image_data_uri: str = ""
    record_id: str = ""
    title: str = ""
    status: str = ""
    fen: str = ""
    pgn: str = ""
    warnings: list[str] = field(default_factory=list)
    font_size: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "bbox": list(self.bbox),
            "reading_order": int(self.reading_order or 0),
            "text": self.text,
            "image_data_uri": self.image_data_uri,
            "record_id": self.record_id,
            "title": self.title,
            "status": self.status,
            "fen": self.fen,
            "pgn": self.pgn,
            "warnings": list(self.warnings or []),
            "font_size": round(float(self.font_size or 0.0), 2),
        }


@dataclass(frozen=True)
class ChessBookLayoutPage:
    page_index: int
    page_number: int
    width: float
    height: float
    background_image_data_uri: str = ""
    elements: list[ChessBookLayoutElement | Mapping[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "page_number": self.page_number,
            "width": round(float(self.width or 0.0), 2),
            "height": round(float(self.height or 0.0), 2),
            "background_image_data_uri": self.background_image_data_uri,
            "elements": [
                element.to_dict() if isinstance(element, ChessBookLayoutElement) else dict(element)
                for element in self.elements
            ],
        }


def extract_chess_pgn_records_from_ocr_pages(
    pages: Mapping[Any, Mapping[str, Any]],
    *,
    source_title: str = "",
    fen_by_page: Mapping[int, list[str]] | None = None,
) -> list[ChessPgnRecord]:
    records: list[ChessPgnRecord] = []
    fen_lookup = fen_by_page or {}
    for raw_page, record in sorted(pages.items(), key=lambda item: _safe_int(item[0])):
        page_num = int(record.get("page_num", _safe_int(raw_page)) or 0)
        page_records = extract_chess_pgn_records_from_text(
            str(record.get("text") or ""),
            page_num=page_num,
            source_title=source_title,
            ocr_confidence=float(record.get("confidence", 0.0) or 0.0),
            fen_candidates=fen_lookup.get(page_num) or [],
        )
        records.extend(page_records)
    return merge_chess_pgn_continuation_records(records)


def extract_chess_pgn_records_from_text(
    text: str,
    *,
    page_num: int = 0,
    source_title: str = "",
    ocr_confidence: float = 0.0,
    fen_candidates: Iterable[str] | None = None,
    glyph_diagnostics: Iterable[Mapping[str, Any]] | None = None,
) -> list[ChessPgnRecord]:
    normalized = normalize_ocr_text_for_pgn(text)
    if not normalized.strip():
        return []

    candidates = _split_candidate_game_blocks(normalized)
    fen_list = [str(fen or "").strip() for fen in (fen_candidates or []) if str(fen or "").strip()]
    glyph_diagnostic_rows = _bounded_glyph_diagnostics(glyph_diagnostics or [], limit=GLYPH_DIAGNOSTIC_DOCUMENT_LIMIT)
    records: list[ChessPgnRecord] = []
    for index, candidate in enumerate(candidates, start=1):
        body = _strip_opening_descriptor_prefix(candidate["body"], context=candidate["caption"])
        raw = _strip_opening_descriptor_prefix(candidate["raw"], context=candidate["caption"])
        tokens = _extract_pgn_tokens(body)
        movetext, result, halfmove_count, warnings = _tokens_to_movetext(tokens)
        if halfmove_count < 2:
            continue
        if _looks_like_opening_descriptor_only(
            caption=candidate["caption"],
            body=body,
            movetext=movetext,
            result=result,
            halfmove_count=halfmove_count,
        ):
            continue
        fen = fen_list[index - 1] if index - 1 < len(fen_list) else ""
        title = _candidate_title(candidate["caption"], fallback=f"Strona {page_num + 1}, partia {index}")
        headers = _build_headers(
            title=title,
            caption=candidate["caption"],
            source_title=source_title,
            result=result,
            fen=fen,
        )
        title = _title_from_headers(headers, fallback=title)
        pgn = _format_pgn(headers, movetext, result)
        annotated_pgn = _format_annotated_pgn(
            headers,
            raw,
            fallback_movetext=movetext,
            result=result,
        )
        warnings = _with_unmapped_glyph_warning(warnings, raw, movetext, pgn, annotated_pgn)
        record_glyph_diagnostics = (
            _select_record_glyph_diagnostics(
                glyph_diagnostic_rows,
                texts=(raw, body, movetext, pgn, annotated_pgn),
            )
            if UNMAPPED_CHESS_GLYPH_WARNING in set(warnings)
            else []
        )
        confidence = _pgn_confidence(
            halfmove_count=halfmove_count,
            ocr_confidence=ocr_confidence,
            has_players=bool(headers.get("White", "?") != "?" or headers.get("Black", "?") != "?"),
            warnings=warnings,
        )
        status = "accepted" if confidence >= 0.72 and not _blocking_pgn_warnings(warnings) else "requires_review"
        records.append(
            ChessPgnRecord(
                id=f"scan-chess-p{page_num + 1:03d}-g{index:02d}",
                source_pages=[page_num + 1],
                title=title,
                headers=headers,
                movetext=movetext,
                pgn=pgn,
                annotated_pgn=annotated_pgn,
                result=result,
                confidence=confidence,
                status=status,
                warnings=warnings,
                fen=fen,
                raw_text=raw,
                glyph_diagnostics=record_glyph_diagnostics,
            )
        )
    return records


def attach_fen_candidates_to_pgn_records(
    records: Iterable[ChessPgnRecord],
    fen_candidates: Iterable[str],
) -> list[ChessPgnRecord]:
    fen_list = [str(fen or "").strip() for fen in fen_candidates if str(fen or "").strip()]
    updated: list[ChessPgnRecord] = []
    for index, record in enumerate(records):
        if record.fen or index >= len(fen_list):
            updated.append(record)
            continue
        headers = dict(record.headers)
        headers["SetUp"] = "1"
        headers["FEN"] = fen_list[index]
        pgn = _format_pgn(headers, record.movetext, record.result)
        annotated_pgn = _format_annotated_pgn(
            headers,
            record.raw_text,
            fallback_movetext=record.movetext,
            result=record.result,
        )
        warnings = _with_unmapped_glyph_warning(record.warnings, record.raw_text, record.movetext, pgn, annotated_pgn)
        updated.append(
            replace(
                record,
                headers=headers,
                pgn=pgn,
                annotated_pgn=annotated_pgn,
                fen=fen_list[index],
                warnings=warnings,
            )
        )
    return updated


def annotate_records_with_replayed_fens(records: Iterable[ChessPgnRecord]) -> list[ChessPgnRecord]:
    """Attach deterministic final FEN values by legally replaying PGN movetext.

    `record.fen` is reserved for an initial position/FEN header. The final
    position is stored separately so PGN headers are not corrupted with a
    position that belongs after the moves.
    """
    updated: list[ChessPgnRecord] = []
    for record in records:
        replay = _replay_record_to_final_fen(record)
        warnings = sorted(set([*record.warnings, *replay["warnings"]]))
        warnings = _with_unmapped_glyph_warning(
            warnings,
            record.raw_text,
            record.movetext,
            record.pgn,
            record.annotated_pgn,
        )
        status = record.status
        confidence = float(record.confidence or 0.0)
        blocking_warnings = _blocking_pgn_warnings(warnings)
        final_fen = replay["final_fen"]
        fen_snapshots = replay["fen_snapshots"]
        if blocking_warnings:
            status = "requires_review"
            confidence = min(confidence, 0.64)
            final_fen = ""
            fen_snapshots = []
        if replay["final_fen"]:
            confidence = min(1.0, max(confidence, confidence + 0.08))
            if confidence >= 0.72 and not blocking_warnings:
                status = "accepted"
        else:
            status = "requires_review"
            confidence = min(confidence, 0.64)
        updated.append(
            replace(
                record,
                final_fen=final_fen,
                fen_snapshots=fen_snapshots,
                warnings=warnings,
                status=status,
                confidence=confidence,
            )
        )
    return updated


def merge_chess_pgn_continuation_records(records: Iterable[ChessPgnRecord]) -> list[ChessPgnRecord]:
    """Merge adjacent page/chapter continuations when the combined mainline is legal."""
    merged: list[ChessPgnRecord] = []
    for record in records:
        if merged:
            stitched = _try_merge_continuation_pair(merged[-1], record)
            if stitched is not None:
                merged[-1] = stitched
                continue
        merged.append(record)
    return merged


def _try_merge_continuation_pair(
    previous: ChessPgnRecord,
    continuation: ChessPgnRecord,
) -> ChessPgnRecord | None:
    first_move = _first_movetext_number(continuation.movetext)
    previous_last_move = _last_movetext_number(previous.movetext)
    if first_move is None or previous_last_move is None:
        return None
    if first_move <= 1 or previous.result != "*":
        return None
    if first_move < previous_last_move or first_move > previous_last_move + 2:
        return None

    result = continuation.result if continuation.result != "*" else previous.result
    combined_movetext = " ".join(
        value
        for value in (
            _movetext_without_terminal_result(previous.movetext),
            continuation.movetext.strip(),
        )
        if value
    ).strip()
    if not combined_movetext:
        return None

    headers = dict(previous.headers)
    headers["Result"] = result or "*"
    raw_text = "\n".join(value for value in (previous.raw_text, continuation.raw_text) if value)
    glyph_diagnostics = _bounded_glyph_diagnostics(
        [
            *(previous.glyph_diagnostics or []),
            *(continuation.glyph_diagnostics or []),
        ],
        limit=GLYPH_DIAGNOSTIC_RECORD_LIMIT,
    )
    combined_pgn = _format_pgn(headers, combined_movetext, result)
    combined_annotated_pgn = _format_annotated_pgn(
        headers,
        raw_text,
        fallback_movetext=combined_movetext,
        result=result,
    )
    glyph_warnings = [
        warning
        for warning in [*previous.warnings, *continuation.warnings]
        if warning == UNMAPPED_CHESS_GLYPH_WARNING
    ]
    combined_warnings = _with_unmapped_glyph_warning(
        glyph_warnings,
        raw_text,
        combined_movetext,
        combined_pgn,
        combined_annotated_pgn,
    )
    candidate = replace(
        previous,
        source_pages=sorted(set([*previous.source_pages, *continuation.source_pages])),
        headers=headers,
        movetext=combined_movetext,
        pgn=combined_pgn,
        annotated_pgn=combined_annotated_pgn,
        result=result,
        raw_text=raw_text,
        glyph_diagnostics=glyph_diagnostics,
        warnings=combined_warnings,
    )
    replayed = annotate_records_with_replayed_fens([candidate])[0]
    if _blocking_pgn_warnings(replayed.warnings) or not replayed.final_fen:
        return None
    return replace(
        replayed,
        warnings=sorted(set([*replayed.warnings, "continuation_record_merged"])),
        status="accepted",
        confidence=max(float(replayed.confidence or 0.0), float(previous.confidence or 0.0), 0.80),
    )


def _last_movetext_number(movetext: str) -> int | None:
    matches = list(re.finditer(r"(?<![\d.])(?P<num>\d{1,3})\.(?:\.\.)?(?!\d)", str(movetext or "")))
    if not matches:
        return None
    try:
        return int(matches[-1].group("num"))
    except ValueError:
        return None


def _movetext_without_terminal_result(movetext: str) -> str:
    return re.sub(
        r"\s*(?:1-0|0-1|1/2-1/2|0\.5-0\.5|\*)\s*$",
        "",
        str(movetext or "").strip(),
    ).strip()


def render_chess_pgn_html_parts(
    records: Iterable[ChessPgnRecord],
    *,
    download_href: str = "chess_games.pgn",
) -> list[str]:
    parts: list[str] = []
    for record in records:
        if not _is_exportable_pgn_record(record):
            parts.append(_record_review_html(record))
            continue
        safe_title = html.escape(record.title or record.id)
        safe_pgn = html.escape(_record_export_pgn(record))
        fen_markup = _record_fen_html(record)
        safe_href = html.escape(download_href, quote=True) if download_href else ""
        download_markup = (
            '<p class="chess-pgn-download">'
            f'<a href="{safe_href}">Pobierz PGN dla partii szachowych</a>'
            "</p>"
            if safe_href
            else ""
        )
        status = html.escape(record.status or "requires_review", quote=True)
        confidence = float(record.confidence or 0.0)
        parts.append(
            '<section class="chess-pgn" '
            f'id="{html.escape(record.id, quote=True)}" '
            f'data-pgn-status="{status}" data-pgn-confidence="{confidence:.3f}">'
            f"<h2>PGN: {safe_title}</h2>"
            f"{download_markup}"
            f"{fen_markup}"
            f'<pre class="chess-pgn-text"><code>{safe_pgn}</code></pre>'
            "</section>"
        )
    return parts


def build_combined_pgn(records: Iterable[ChessPgnRecord]) -> str:
    pgn_values = [_record_export_pgn(record).strip() for record in records if _is_exportable_pgn_record(record)]
    return "\n\n".join(pgn_values).strip() + ("\n" if pgn_values else "")


def build_pgn_download_html(
    records: Iterable[ChessPgnRecord],
    *,
    title: str = "Chess PGN",
    diagrams: Iterable[ChessDiagramRecord | Mapping[str, Any]] | None = None,
    book_layout_pages: Iterable[ChessBookLayoutPage | Mapping[str, Any]] | None = None,
    pdf_preview_href: str = "pdf_layout_preview",
) -> str:
    record_list = list(records)
    diagram_list = [_coerce_chess_diagram_record(diagram) for diagram in (diagrams or [])]
    diagram_list = [diagram for diagram in diagram_list if diagram is not None]
    book_page_list = [_coerce_chess_book_layout_page(page) for page in (book_layout_pages or [])]
    book_page_list = [page for page in book_page_list if page is not None]
    accepted_records = [record for record in record_list if _is_exportable_pgn_record(record)]
    review_records = [record for record in record_list if not _is_exportable_pgn_record(record)]
    full_notation_records = [record for record in record_list if (record.raw_text or record.movetext or "").strip()]
    if book_page_list:
        body = _book_layout_pgn_html_body(
            record_list,
            book_page_list,
            accepted_count=len(accepted_records),
            review_count=len(review_records),
            full_notation_count=len(full_notation_records),
            diagram_count=len(diagram_list),
            pdf_preview_href=pdf_preview_href,
        )
    elif diagram_list:
        body = _layout_aware_pgn_html_body(
            record_list,
            diagram_list,
            accepted_count=len(accepted_records),
            review_count=len(review_records),
            full_notation_count=len(full_notation_records),
            pdf_preview_href=pdf_preview_href,
        )
    else:
        body = [
            f"<p>Accepted PGN records: {len(accepted_records)}</p>",
            f"<p>Manual review records: {len(review_records)}</p>",
            f"<p>Full notation records: {len(full_notation_records)}</p>",
            "<p>HTML order: source PDF order preserved.</p>",
            "<h2>Games in source order</h2>",
        ]
        if not record_list:
            body.append("<p>No PGN-like records were detected.</p>")
        for record in record_list:
            body.append(_record_download_html(record))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:Georgia,serif;line-height:1.5;margin:2rem;color:#141414;}"
        "section{margin:1.4rem 0 2rem}pre{white-space:pre-wrap;background:#f6f1e8;color:#141414;padding:1rem;border-radius:12px;}"
        ".chess-pgn-game{border-top:1px solid #d8c8b1;padding-top:1rem}.chess-pgn-status{display:inline-block;"
        "border-radius:999px;padding:.25rem .6rem;font-weight:800;font-size:.82rem;margin:.2rem 0 .6rem}"
        ".chess-pgn-legal .chess-pgn-status{background:#e3f7ea;color:#096b31}.chess-pgn-needs-review .chess-pgn-status{background:#fff0d8;color:#8a4b00}"
        ".diagram-fen{margin:.6rem 0;color:#141414}.diagram-fen code{font-family:monospace}"
        ".copy-pgn-button,.copy-fen-button{border:1px solid #c9b89f;border-radius:999px;background:#fff8ed;color:#141414;"
        "font-weight:700;padding:.45rem .8rem;cursor:pointer;margin:.4rem 0 .65rem}"
        ".chess-full-notation h3{margin-bottom:.2rem}.chess-review-reason{color:#725b3f;font-weight:700}"
        ".chess-glyph-audit-note{color:#725b3f;font-size:.92rem;font-weight:700}"
        ".chess-review-summary{display:flex;flex-wrap:wrap;gap:.55rem;margin:1rem 0 1.4rem}"
        ".chess-review-summary span{border:1px solid #d8c8b1;border-radius:999px;padding:.35rem .7rem;background:#fff8ed;font-weight:700}"
        ".chess-page-group{border-top:2px solid #cdb790;padding-top:1rem}.chess-page-group h2{margin:.1rem 0 1rem}"
        ".chess-review-card{display:grid;grid-template-columns:minmax(190px,300px) minmax(0,1fr);gap:1.2rem;align-items:start;"
        "border:1px solid #d8c8b1;border-radius:18px;padding:1rem;background:#fffdf8;box-shadow:0 8px 24px rgba(89,63,30,.08)}"
        ".chess-diagram-panel{background:#f7efe2;border-radius:14px;padding:.8rem;text-align:center}.chess-diagram-panel figure{margin:0}"
        ".chess-review-diagram{max-width:100%;height:auto;display:block;margin:0 auto;border-radius:4px}"
        ".chess-diagram-caption{font-weight:800;margin:.55rem 0 .25rem}.chess-diagram-meta{font-size:.86rem;color:#725b3f;margin:.25rem 0}"
        ".chess-diagram-placeholder{border:1px dashed #c9b89f;border-radius:12px;padding:1rem;color:#725b3f;background:#fff8ed;font-weight:700}"
        ".chess-notation-panel .chess-pgn-game{border-top:0;margin:0;padding-top:0}.chess-notation-panel h2{margin-top:0}"
        ".chess-pdf-preview-link{display:inline-block;margin:.25rem 0 1rem;color:#7a3d12;font-weight:800}.chess-unmatched-diagrams{background:#fff8ed;border:1px dashed #c9b89f;border-radius:16px;padding:1rem}"
        ".chess-book-review{margin:0;background:#e7ded1;color:#1d1711}.chess-book-review>h1{max-width:1180px;margin:1.5rem auto .75rem;padding:0 1rem}"
        ".chess-book-toolbar{position:sticky;top:0;z-index:30;display:flex;flex-wrap:wrap;gap:.6rem;align-items:center;padding:.75rem 1rem;background:rgba(29,23,17,.94);color:#fff8ed;box-shadow:0 10px 30px rgba(0,0,0,.22)}"
        ".chess-book-toolbar span{border:1px solid rgba(255,248,237,.24);border-radius:999px;padding:.28rem .65rem;font-size:.82rem;font-weight:800}.chess-book-toolbar button{border:1px solid rgba(255,248,237,.35);border-radius:999px;background:#fff8ed;color:#2b2118;font-weight:800;padding:.38rem .7rem;cursor:pointer}"
        ".chess-book-stack{display:flex;flex-direction:column;align-items:center;gap:1.5rem;padding:1rem 1rem 2.5rem}.chess-book-page{position:relative;background:#fff;overflow:hidden;box-shadow:0 24px 58px rgba(48,35,21,.30);transform-origin:top center;max-width:calc(100vw - 2rem)}"
        ".book-page-bg{position:absolute;inset:0;width:100%;height:100%;object-fit:fill;user-select:none;pointer-events:none}.book-element{position:absolute;box-sizing:border-box}.book-text{white-space:pre-wrap;line-height:1.1;color:rgba(22,18,14,.84);background:rgba(255,252,246,.55);border-radius:3px;padding:1px 2px}.book-diagram{margin:0;display:flex;align-items:center;justify-content:center;background:rgba(255,252,246,.78);border:1px solid rgba(90,70,44,.18)}"
        ".book-diagram img{display:block;max-width:100%;max-height:100%;object-fit:contain}.book-fen{font-family:'Courier New',monospace;font-size:10px;line-height:1.25;background:rgba(255,248,224,.92);border:1px solid rgba(145,96,22,.35);border-radius:6px;padding:3px 5px;color:#322315;overflow:hidden}.book-pgn-record,.book-review-warning{background:rgba(255,252,246,.95);border:1px solid #d1b58b;border-left:5px solid #a55d1d;border-radius:10px;padding:.45rem .55rem;box-shadow:0 10px 22px rgba(55,36,15,.16);overflow:hidden}.book-pgn-record.accepted{border-left-color:#1f8f4d}.book-pgn-record.review{border-left-color:#c47211}.book-pgn-status{display:inline-block;border-radius:999px;padding:.1rem .45rem;font-size:.72rem;font-weight:900;background:#fff0d8;color:#7b4300}.book-pgn-record.accepted .book-pgn-status{background:#e3f7ea;color:#0d6b36}.book-pgn-title{font-weight:900;font-size:.86rem;margin:.25rem 0}.book-pgn-record pre{font-size:10px;line-height:1.25;margin:.3rem 0 0;padding:.35rem;border-radius:6px;max-height:95px;overflow:auto}.book-review-warning{border-left-color:#b91c1c;color:#4b1a13}.book-page-label{position:absolute;left:.5rem;top:.5rem;z-index:4;border-radius:999px;padding:.16rem .5rem;background:rgba(255,255,255,.86);color:#5b4227;font-size:10px;font-weight:900}"
        ".hide-pdf-bg .book-page-bg{opacity:0}.hide-recognized-text .book-text{display:none}.hide-pgn-fen .book-pgn-record,.hide-pgn-fen .book-fen{display:none}.hide-review-warnings .book-review-warning{display:none}@media(max-width:780px){.chess-book-page{transform:scale(.72);margin-bottom:-28%}.chess-book-toolbar{font-size:.9rem}}"
        ".copy-pgn-button:focus,.copy-fen-button:focus{outline:2px solid #b85c24;outline-offset:2px}@media(max-width:780px){.chess-review-card{grid-template-columns:1fr}}"
        "</style>"
        "</head><body"
        + (" class=\"chess-book-review\"" if book_page_list else "")
        + ">"
        f"<h1>{html.escape(title)}</h1>"
        + "\n".join(body)
        + _copy_pgn_script()
        + (_book_layout_script() if book_page_list else "")
        + "</body></html>\n"
    )


def _coerce_chess_book_layout_page(page: ChessBookLayoutPage | Mapping[str, Any]) -> ChessBookLayoutPage | None:
    if isinstance(page, ChessBookLayoutPage):
        return page
    if not isinstance(page, Mapping):
        return None
    raw_size = page.get("page_size") if isinstance(page.get("page_size"), Mapping) else {}
    width = _safe_float(page.get("width", raw_size.get("width", 0.0)))
    height = _safe_float(page.get("height", raw_size.get("height", 0.0)))
    if width <= 0 or height <= 0:
        return None
    page_index = _safe_int(page.get("page_index", _safe_int(page.get("page_number", 1)) - 1))
    page_number = _safe_int(page.get("page_number", page_index + 1)) or page_index + 1
    elements = [_coerce_chess_book_layout_element(element) for element in (page.get("elements") or [])]
    return ChessBookLayoutPage(
        page_index=page_index,
        page_number=page_number,
        width=width,
        height=height,
        background_image_data_uri=str(page.get("background_image_data_uri") or ""),
        elements=[element for element in elements if element is not None],
    )


def _coerce_chess_book_layout_element(element: ChessBookLayoutElement | Mapping[str, Any]) -> ChessBookLayoutElement | None:
    if isinstance(element, ChessBookLayoutElement):
        return element
    if not isinstance(element, Mapping):
        return None
    raw_bbox = element.get("bbox") or (0.0, 0.0, 0.0, 0.0)
    bbox_values: list[float] = []
    try:
        bbox_values = [float(value or 0.0) for value in list(raw_bbox)[:4]]
    except (TypeError, ValueError):
        bbox_values = []
    while len(bbox_values) < 4:
        bbox_values.append(0.0)
    return ChessBookLayoutElement(
        type=str(element.get("type") or "text"),
        bbox=tuple(bbox_values[:4]),
        reading_order=_safe_int(element.get("reading_order")),
        text=str(element.get("text") or ""),
        image_data_uri=str(element.get("image_data_uri") or element.get("data_uri") or ""),
        record_id=str(element.get("record_id") or ""),
        title=str(element.get("title") or ""),
        status=str(element.get("status") or ""),
        fen=str(element.get("fen") or element.get("fen_candidate") or ""),
        pgn=str(element.get("pgn") or ""),
        warnings=[str(warning) for warning in (element.get("warnings") or [])],
        font_size=_safe_float(element.get("font_size")),
    )


def _book_layout_pgn_html_body(
    records: list[ChessPgnRecord],
    pages: list[ChessBookLayoutPage],
    *,
    accepted_count: int,
    review_count: int,
    full_notation_count: int,
    diagram_count: int,
    pdf_preview_href: str,
) -> list[str]:
    record_count = len(records)
    element_count = sum(len(page.elements) for page in pages)
    safe_preview_href = html.escape(pdf_preview_href, quote=True)
    body = [
        '<div class="chess-book-toolbar" aria-label="Chess book review controls">',
        f"<span>Pages: {len(pages)}</span>",
        f"<span>Elements: {element_count}</span>",
        f"<span>Accepted PGN: {accepted_count}</span>",
        f"<span>Review: {review_count}</span>",
        f"<span>Full notation: {full_notation_count}</span>",
        f"<span>Diagrams: {diagram_count}</span>",
        '<button type="button" data-book-toggle="hide-pdf-bg">PDF background</button>',
        '<button type="button" data-book-toggle="hide-recognized-text">Recognized text</button>',
        '<button type="button" data-book-toggle="hide-pgn-fen">PGN/FEN</button>',
        '<button type="button" data-book-toggle="hide-review-warnings">Review warnings</button>',
        f'<a class="chess-pdf-preview-link" href="{safe_preview_href}">PDF layout preview</a>' if safe_preview_href else "",
        "</div>",
        '<main class="chess-book-stack" data-km-view="chess-book-review">',
    ]
    if not pages:
        body.append("<p>No book layout pages were detected.</p>")
    for page in sorted(pages, key=lambda item: (item.page_number, item.page_index)):
        body.append(_book_layout_page_html(page))
    body.append("</main>")
    if record_count and not accepted_count:
        body.append(
            '<p class="chess-glyph-audit-note">All detected PGN records require review; strict PGN export remains empty.</p>'
        )
    return [part for part in body if part]


def _book_layout_page_html(page: ChessBookLayoutPage) -> str:
    width = max(1.0, float(page.width or 1.0))
    height = max(1.0, float(page.height or 1.0))
    page_parts = [
        f'<section class="chess-book-page" data-page="{page.page_number}" style="width:{width:.2f}px;height:{height:.2f}px">',
    ]
    if page.background_image_data_uri:
        page_parts.append(
            f'<img class="book-page-bg" alt="" src="{html.escape(page.background_image_data_uri, quote=True)}"/>'
        )
    page_parts.append(f'<span class="book-page-label">Page {page.page_number}</span>')
    elements = sorted(page.elements, key=lambda item: (int(item.reading_order or 0), item.bbox[1], item.bbox[0]))
    for element in elements:
        page_parts.append(_book_layout_element_html(element, page_width=width, page_height=height))
    page_parts.append("</section>")
    return "".join(page_parts)


def _book_layout_element_html(element: ChessBookLayoutElement, *, page_width: float, page_height: float) -> str:
    element_type = (element.type or "text").strip().lower()
    style = _book_layout_bbox_style(element.bbox, page_width=page_width, page_height=page_height)
    order_attr = f' data-reading-order="{int(element.reading_order or 0)}"'
    if element_type == "diagram":
        image = (
            f'<img src="{html.escape(element.image_data_uri, quote=True)}" alt="{html.escape(element.title or "Chess diagram", quote=True)}"/>'
            if element.image_data_uri
            else '<span>Diagram image unavailable</span>'
        )
        return f'<figure class="book-element book-diagram" style="{style}"{order_attr}>{image}</figure>'
    if element_type == "fen":
        fen_value = (element.fen or element.text).strip()
        if not _fen_parse_clean(fen_value):
            return (
                f'<div class="book-element book-review-warning" style="{style}"{order_attr}>'
                "<strong>FEN do weryfikacji</strong><br>Pozycja nie przeszla walidacji parserem.</div>"
            )
        target_id = f"book-fen-{html.escape(_html_id_fragment(element.record_id or fen_value), quote=True)}"
        return (
            f'<div class="book-element book-fen" style="{style}"{order_attr}>'
            f'<button class="copy-fen-button" type="button" data-copy-target="{target_id}">Kopiuj FEN</button> '
            f'FEN: <code id="{target_id}">{html.escape(fen_value)}</code></div>'
        )
    if element_type == "pgn_record":
        return _book_layout_pgn_record_html(element, style=style, order_attr=order_attr)
    if element_type == "review_warning":
        warnings = ", ".join(element.warnings or [])
        text = element.text or warnings or "Record requires review"
        return (
            f'<div class="book-element book-review-warning" style="{style}"{order_attr}>'
            f'<strong>Do weryfikacji</strong><br>{html.escape(text)}</div>'
        )
    font_size = max(6.0, float(element.font_size or 10.0))
    style = f"{style}font-size:{font_size:.2f}px;"
    return (
        f'<div class="book-element book-text" style="{style}"{order_attr}>'
        f"{html.escape(element.text)}</div>"
    )


def _book_layout_pgn_record_html(element: ChessBookLayoutElement, *, style: str, order_attr: str) -> str:
    status = (element.status or "requires_review").strip().lower()
    accepted = status == "accepted" and bool(element.pgn.strip())
    status_label = "Legalny PGN/FEN" if accepted else "Do weryfikacji"
    class_name = "accepted" if accepted else "review"
    warning_text = ", ".join(element.warnings or [])
    pgn_markup = (
        f'<button class="copy-pgn-button" type="button" data-copy-target="book-pgn-{html.escape(element.record_id, quote=True)}">Kopiuj PGN</button>'
        f'<pre id="book-pgn-{html.escape(element.record_id, quote=True)}" class="chess-pgn-mainline"><code>{html.escape(element.pgn)}</code></pre>'
        if accepted
        else f'<p class="chess-review-reason">{html.escape(warning_text or "Record requires manual review.")}</p>'
    )
    fen_markup = ""
    if element.fen and _fen_parse_clean(element.fen):
        fen_id = f"book-record-fen-{html.escape(_html_id_fragment(element.record_id or element.fen), quote=True)}"
        fen_markup = (
            f'<p><button class="copy-fen-button" type="button" data-copy-target="{fen_id}">Kopiuj FEN</button> '
            f'FEN: <code id="{fen_id}">{html.escape(element.fen)}</code></p>'
        )
    return (
        f'<aside class="book-element book-pgn-record {class_name}" style="{style}"{order_attr}>'
        f'<span class="book-pgn-status">{status_label}</span>'
        f'<div class="book-pgn-title">{html.escape(element.title or element.record_id or "PGN record")}</div>'
        f"{fen_markup}{pgn_markup}</aside>"
    )


def _book_layout_bbox_style(
    bbox: tuple[float, float, float, float],
    *,
    page_width: float,
    page_height: float,
) -> str:
    x0, y0, x1, y1 = [float(value or 0.0) for value in bbox]
    x0 = max(0.0, min(x0, page_width))
    y0 = max(0.0, min(y0, page_height))
    x1 = max(x0 + 1.0, min(x1, page_width))
    y1 = max(y0 + 1.0, min(y1, page_height))
    return (
        f"left:{x0:.2f}px;top:{y0:.2f}px;"
        f"width:{max(1.0, x1 - x0):.2f}px;height:{max(1.0, y1 - y0):.2f}px;"
    )


def _book_layout_script() -> str:
    return (
        "<script>(function(){document.querySelectorAll('[data-book-toggle]').forEach(function(button){"
        "button.addEventListener('click',function(){document.body.classList.toggle(button.getAttribute('data-book-toggle'));});"
        "});})();</script>"
    )


def _fen_parse_clean(fen: str) -> bool:
    value = str(fen or "").strip()
    if not value:
        return False
    parts = value.split()
    if len(parts) != 6:
        return False
    try:
        import chess

        chess.Board(value)
    except Exception:
        return False
    return True


def _html_id_fragment(value: str) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-")
    return fragment[:80] or "value"


def _coerce_chess_diagram_record(diagram: ChessDiagramRecord | Mapping[str, Any]) -> ChessDiagramRecord | None:
    if isinstance(diagram, ChessDiagramRecord):
        return diagram
    if not isinstance(diagram, Mapping):
        return None
    page_index = _safe_int(diagram.get("page_index", diagram.get("page", 0)))
    page_number = _safe_int(diagram.get("page_number", page_index + 1))
    raw_bbox = diagram.get("bbox") or (0.0, 0.0, 0.0, 0.0)
    bbox_values: list[float] = []
    try:
        bbox_values = [float(value or 0.0) for value in list(raw_bbox)[:4]]
    except (TypeError, ValueError):
        bbox_values = []
    while len(bbox_values) < 4:
        bbox_values.append(0.0)
    return ChessDiagramRecord(
        id=str(diagram.get("id") or f"diagram-p{page_number:03d}"),
        page_index=page_index,
        page_number=page_number or page_index + 1,
        bbox=tuple(bbox_values[:4]),
        caption=str(diagram.get("caption") or ""),
        image_data_uri=str(diagram.get("image_data_uri") or diagram.get("data_uri") or ""),
        fen_candidate=str(diagram.get("fen_candidate") or diagram.get("fen") or ""),
        nearby_text=str(diagram.get("nearby_text") or ""),
        matched_record_id=str(diagram.get("matched_record_id") or ""),
        match_confidence=float(diagram.get("match_confidence", 0.0) or 0.0),
    )


def _layout_aware_pgn_html_body(
    records: list[ChessPgnRecord],
    diagrams: list[ChessDiagramRecord],
    *,
    accepted_count: int,
    review_count: int,
    full_notation_count: int,
    pdf_preview_href: str,
) -> list[str]:
    record_diagrams, unmatched = _match_diagrams_to_records(records, diagrams)
    matched_count = sum(len(items) for items in record_diagrams.values())
    safe_preview_href = html.escape(pdf_preview_href, quote=True)
    body = [
        '<div class="chess-review-summary">',
        f"<span>Accepted PGN: {accepted_count}</span>",
        f"<span>Manual review: {review_count}</span>",
        f"<span>Full notation: {full_notation_count}</span>",
        f"<span>Diagrams: {len(diagrams)}</span>",
        f"<span>Matched diagrams: {matched_count}</span>",
        f"<span>Unmatched diagrams: {len(unmatched)}</span>",
        "</div>",
        '<p>HTML order: source PDF order preserved; diagrams are matched by caption, page, and reading order.</p>',
    ]
    if safe_preview_href:
        body.append(f'<p><a class="chess-pdf-preview-link" href="{safe_preview_href}">PDF layout preview</a></p>')
    if not records and not diagrams:
        body.append("<p>No PGN-like records or diagrams were detected.</p>")
        return body

    page_numbers = sorted(
        set(
            [
                page
                for record in records
                for page in (record.source_pages or [1])
            ]
            + [diagram.page_number for diagram in diagrams]
        )
    )
    for page_number in page_numbers:
        page_records = [record for record in records if page_number in set(record.source_pages or [page_number])]
        page_unmatched = [diagram for diagram in unmatched if diagram.page_number == page_number]
        if not page_records and not page_unmatched:
            continue
        body.append(f'<section class="chess-page-group" data-page="{page_number}"><h2>Page {page_number}</h2>')
        for record in page_records:
            body.append(_layout_record_card_html(record, record_diagrams.get(record.id) or []))
        if page_unmatched:
            body.append('<section class="chess-unmatched-diagrams"><h3>Unmatched diagrams</h3>')
            for diagram in page_unmatched:
                body.append(_diagram_panel_html(diagram, placeholder=False))
            body.append("</section>")
        body.append("</section>")
    return body


def _match_diagrams_to_records(
    records: list[ChessPgnRecord],
    diagrams: list[ChessDiagramRecord],
) -> tuple[dict[str, list[ChessDiagramRecord]], list[ChessDiagramRecord]]:
    by_record: dict[str, list[ChessDiagramRecord]] = {record.id: [] for record in records}
    unmatched: list[ChessDiagramRecord] = []
    record_by_id = {record.id: record for record in records}
    assigned_diagram_ids: set[str] = set()

    def assign(diagram: ChessDiagramRecord, record: ChessPgnRecord, confidence: float) -> None:
        updated = replace(diagram, matched_record_id=record.id, match_confidence=max(diagram.match_confidence, confidence))
        by_record.setdefault(record.id, []).append(updated)
        assigned_diagram_ids.add(diagram.id)

    for diagram in diagrams:
        if diagram.matched_record_id and diagram.matched_record_id in record_by_id:
            assign(diagram, record_by_id[diagram.matched_record_id], 1.0)

    for diagram in diagrams:
        if diagram.id in assigned_diagram_ids:
            continue
        diagram_ref = _diagram_reference_key(diagram.caption)
        if not diagram_ref:
            unmatched.append(diagram)
            continue
        matched = None
        for record in records:
            haystack = " ".join([record.title, record.raw_text, record.movetext])
            if diagram_ref and diagram_ref in _diagram_reference_key(haystack):
                matched = record
                break
        if matched is not None:
            assign(diagram, matched, 0.92)
        else:
            unmatched.append(diagram)

    still_unmatched = [diagram for diagram in unmatched if diagram.id not in assigned_diagram_ids]
    unmatched = []
    for page_number in sorted(set(diagram.page_number for diagram in still_unmatched)):
        page_diagrams = sorted(
            [diagram for diagram in still_unmatched if diagram.page_number == page_number],
            key=lambda item: (item.bbox[1], item.bbox[0], item.id),
        )
        page_records = [record for record in records if page_number in set(record.source_pages or []) and not by_record.get(record.id)]
        for diagram, record in zip(page_diagrams, page_records):
            assign(diagram, record, 0.55)
        unmatched.extend(page_diagrams[len(page_records):])
    return by_record, unmatched


def _diagram_reference_key(text: str) -> str:
    value = str(text or "").lower()
    matches = re.findall(r"\b(?:diagram|dia\.?|ex\.?|exercise)\s*[\-:]?\s*(\d{1,3}(?:[-.]\d{1,3})?)", value)
    if not matches:
        return ""
    return " ".join(f"diagram {match.replace('.', '-')}" for match in matches)


def _layout_record_card_html(record: ChessPgnRecord, diagrams: list[ChessDiagramRecord]) -> str:
    diagram_markup = _diagram_panel_html(diagrams[0], placeholder=False) if diagrams else _diagram_panel_html(None, placeholder=True)
    return (
        '<article class="chess-review-card">'
        f"{diagram_markup}"
        '<div class="chess-notation-panel">'
        f"{_record_download_html(record)}"
        "</div>"
        "</article>"
    )


def _diagram_panel_html(diagram: ChessDiagramRecord | None, *, placeholder: bool) -> str:
    if placeholder or diagram is None:
        return (
            '<aside class="chess-diagram-panel">'
            '<div class="chess-diagram-placeholder">Diagram not matched</div>'
            "</aside>"
        )
    caption = html.escape(diagram.caption or f"Diagram page {diagram.page_number}")
    image_markup = (
        f'<img class="chess-review-diagram" src="{html.escape(diagram.image_data_uri, quote=True)}" alt="{caption}"/>'
        if diagram.image_data_uri
        else '<div class="chess-diagram-placeholder">Diagram image unavailable</div>'
    )
    fen_markup = (
        f'<p class="chess-diagram-meta">FEN: <code>{html.escape(diagram.fen_candidate)}</code></p>'
        if diagram.fen_candidate
        else ""
    )
    bbox = ", ".join(f"{value:.1f}" for value in diagram.bbox)
    confidence_markup = (
        f'<p class="chess-diagram-meta">Match confidence: {diagram.match_confidence:.2f}</p>'
        if diagram.match_confidence
        else ""
    )
    return (
        '<aside class="chess-diagram-panel">'
        "<figure>"
        f"{image_markup}"
        f'<figcaption class="chess-diagram-caption">{caption}</figcaption>'
        "</figure>"
        f"{fen_markup}"
        f'<p class="chess-diagram-meta">Page {diagram.page_number}; bbox {html.escape(bbox)}</p>'
        f"{confidence_markup}"
        "</aside>"
    )


def summarize_chess_pgn_records(records: Iterable[ChessPgnRecord | Mapping[str, Any]]) -> dict[str, Any]:
    rows = [record.to_dict() if isinstance(record, ChessPgnRecord) else dict(record) for record in records]
    candidate_count = len(rows)
    accepted = [row for row in rows if row.get("status") == "accepted" and row.get("pgn")]
    review = [row for row in rows if row.get("status") != "accepted"]
    fen_rows = [row for row in rows if row.get("fen") or row.get("final_fen") or row.get("fen_snapshots")]
    coverage = (len(accepted) / candidate_count) if candidate_count else 0.0
    warning_counts: dict[str, int] = {}
    for row in rows:
        for warning in row.get("warnings") or []:
            warning_key = str(warning or "").strip()
            if warning_key:
                warning_counts[warning_key] = warning_counts.get(warning_key, 0) + 1
    unmapped_glyphs = _summarize_unmapped_glyph_records(rows)
    continuation_fragment_count = len(
        [
            row
            for row in review
            if (_first_movetext_number(str(row.get("movetext") or "")) or 0) > 1
        ]
    )
    return {
        "status": "passed" if coverage >= 0.50 and accepted else ("requires_review" if candidate_count else "not_detected"),
        "candidate_game_count": candidate_count,
        "valid_pgn_count": len(accepted),
        "legal_pgn_count": len(accepted),
        "fen_count": len(fen_rows),
        "derived_final_fen_count": len([row for row in rows if row.get("final_fen")]),
        "manual_review_count": len(review),
        "continuation_fragment_count": continuation_fragment_count,
        "warning_counts": dict(sorted(warning_counts.items())),
        "unmapped_glyphs": unmapped_glyphs,
        "full_notation_count": len([row for row in rows if str(row.get("raw_text") or row.get("movetext") or "").strip()]),
        "html_source_order_preserved": True,
        "coverage": round(coverage, 4),
        "acceptance_min": 0.50,
        "records": rows[:100],
    }


def _first_movetext_number(movetext: str) -> int | None:
    match = re.search(r"(?<![\d.])(?P<num>\d{1,3})\.(?!\d)", str(movetext or ""))
    if not match:
        return None
    try:
        return int(match.group("num"))
    except ValueError:
        return None


def normalize_ocr_text_for_pgn(text: str) -> str:
    normalized = str(text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _normalize_chessbase_private_symbols(normalized)
    normalized = _normalize_german_piece_notation(normalized)
    normalized = _normalize_move_clock_artifacts(normalized)
    normalized = normalized.replace("\u2013", "-").replace("\u2014", "-")
    normalized = normalized.replace("\u2020", "+").replace("\u2021", "#")
    normalized = normalized.replace("Â˝", "1/2").replace("\u00bd", "1/2")
    normalized = MIXED_QUEENSIDE_CASTLE_RE.sub("O-O-O", normalized)
    normalized = MIXED_KINGSIDE_CASTLE_RE.sub("O-O", normalized)
    normalized = re.sub(r"\b0-0-0\b", "O-O-O", normalized)
    normalized = re.sub(r"\b0-0\b", "O-O", normalized)
    normalized = BROKEN_CASTLE_NEGATIVE_EVAL_RE.sub(r"\g<castle> -0.\g<eval>", normalized)
    normalized = re.sub(r"=\s+([QRBN])\b", r"=\1", normalized)
    normalized = COMPACT_PROMOTION_RE.sub(r"\g<move>=\g<piece>", normalized)
    normalized = PROMOTION_ENDGAME_LABEL_RE.sub(r"\g<promo> \g<label>", normalized)
    normalized = PROMOTION_EQUAL_COMMENT_RE.sub(r"\g<promo> = ", normalized)
    normalized = re.sub(r"(?<=\d)\.\s+\.\.", "...", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"(?<=\d)\s+\.\s+\.\s+\.", "...", normalized)
    normalized = re.sub(r"(?<=\d)\s+\.", ".", normalized)
    return normalized


def _detect_unmapped_pgn_glyphs(text: str) -> bool:
    return bool(_detect_unmapped_pgn_glyph_details(text))


def _detect_unmapped_pgn_glyph_details(text: str, *, field: str = "") -> list[dict[str, str]]:
    value = str(text or "")
    if not value:
        return []
    details: list[dict[str, str]] = []
    seen_reasons: set[str] = set()

    def add_detail(reason: str, sample: str) -> None:
        if reason in seen_reasons:
            return
        seen_reasons.add(reason)
        details.append(
            {
                "reason": reason,
                "field": field,
                "sample": _audit_text_sample(sample),
            }
        )

    for index, char in enumerate(value):
        codepoint = ord(char)
        if char == "\ufffd":
            add_detail("replacement_char", _context_sample(value, index, index + 1))
            continue
        if 0xE000 <= codepoint <= 0xF8FF:
            add_detail("pua", _context_sample(value, index, index + 1))
            continue
        if 0xF0000 <= codepoint <= 0xFFFFD or 0x100000 <= codepoint <= 0x10FFFD:
            add_detail("supplemental_pua", _context_sample(value, index, index + 1))
            continue
        if (codepoint < 32 and char not in "\n\r\t") or 0x7F <= codepoint <= 0x9F:
            add_detail("control_char", _context_sample(value, index, index + 1))
    mojibake = MOJIBAKE_PGN_TOKEN_RE.search(value)
    if mojibake:
        add_detail("mojibake_token", _context_sample(value, mojibake.start(), mojibake.end()))
    angle = ANGLE_GLYPH_TOKEN_RE.search(value)
    if angle:
        add_detail("angle_token", _context_sample(value, angle.start(), angle.end()))
    return details


def _summarize_unmapped_glyph_records(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    diagnostic_by_font: dict[str, int] = {}
    diagnostic_by_page: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    diagnostic_samples: list[dict[str, Any]] = []
    diagnostic_count = 0
    record_count = 0
    for row in rows:
        record_details: list[dict[str, str]] = []
        for field in ("raw_text", "movetext", "pgn", "annotated_pgn"):
            record_details.extend(_detect_unmapped_pgn_glyph_details(str(row.get(field) or ""), field=field))
        glyph_diagnostics = _bounded_glyph_diagnostics(row.get("glyph_diagnostics") or [])
        diagnostic_count += len(glyph_diagnostics)
        for diagnostic in glyph_diagnostics:
            font = str(diagnostic.get("font_name") or "Unknown")
            diagnostic_by_font[font] = diagnostic_by_font.get(font, 0) + 1
            page = str(diagnostic.get("page") or diagnostic.get("page_index") or "")
            if page:
                diagnostic_by_page[page] = diagnostic_by_page.get(page, 0) + 1
            if len(diagnostic_samples) < 12:
                diagnostic_samples.append(
                    {
                        "record_id": str(row.get("id") or ""),
                        "title": str(row.get("title") or "")[:120],
                        "source_pages": list(row.get("source_pages") or []),
                        "page": diagnostic.get("page"),
                        "font_name": font,
                        "span_index": diagnostic.get("span_index"),
                        "reasons": list(diagnostic.get("reasons") or []),
                        "sample": str(
                            diagnostic.get("raw_text")
                            or diagnostic.get("glyph_context")
                            or diagnostic.get("normalized_text")
                            or ""
                        )[:160],
                    }
                )
        if (
            not record_details
            and not glyph_diagnostics
            and UNMAPPED_CHESS_GLYPH_WARNING not in set(row.get("warnings") or [])
        ):
            continue
        record_count += 1
        seen_record_reasons: set[str] = set()
        for detail in record_details:
            reason = detail["reason"]
            by_reason[reason] = by_reason.get(reason, 0) + 1
            if reason in seen_record_reasons or len(samples) >= 12:
                continue
            seen_record_reasons.add(reason)
            samples.append(
                {
                    "record_id": str(row.get("id") or ""),
                    "title": str(row.get("title") or "")[:120],
                    "source_pages": list(row.get("source_pages") or []),
                    "field": detail["field"],
                    "reason": reason,
                    "sample": detail["sample"],
                }
            )
    return {
        "record_count": record_count,
        "by_reason": dict(sorted(by_reason.items())),
        "samples": samples,
        "diagnostic_count": diagnostic_count,
        "diagnostic_by_font": dict(sorted(diagnostic_by_font.items())),
        "diagnostic_by_page": dict(sorted(diagnostic_by_page.items())),
        "diagnostic_samples": diagnostic_samples,
    }


def _context_sample(text: str, start: int, end: int, *, radius: int = 24) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right]


def _audit_text_sample(text: str, *, limit: int = 120) -> str:
    sample = re.sub(r"\s+", " ", str(text or "")).strip()
    escaped: list[str] = []
    for char in sample[:limit]:
        codepoint = ord(char)
        if char == "\ufffd":
            escaped.append("\\ufffd")
        elif 0xE000 <= codepoint <= 0xF8FF:
            escaped.append(f"\\u{codepoint:04x}")
        elif 0xF0000 <= codepoint <= 0xFFFFD or 0x100000 <= codepoint <= 0x10FFFD:
            escaped.append(f"\\U{codepoint:08x}")
        elif codepoint < 32 or 0x7F <= codepoint <= 0x9F:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(char)
    return "".join(escaped)


def _with_unmapped_glyph_warning(warnings: Iterable[str], *texts: str) -> list[str]:
    warning_set = set(warnings or [])
    if any(_detect_unmapped_pgn_glyphs(text) for text in texts):
        warning_set.add(UNMAPPED_CHESS_GLYPH_WARNING)
    return sorted(warning_set)


def _bounded_glyph_diagnostics(
    diagnostics: Iterable[Mapping[str, Any]],
    *,
    limit: int = GLYPH_DIAGNOSTIC_RECORD_LIMIT,
) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for diagnostic in diagnostics or []:
        if not isinstance(diagnostic, Mapping):
            continue
        sanitized = _sanitize_glyph_diagnostic(diagnostic)
        if not sanitized:
            continue
        bounded.append(sanitized)
        if len(bounded) >= limit:
            break
    return bounded


def _sanitize_glyph_diagnostic(diagnostic: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in diagnostic.items():
        key_text = str(key or "").strip()
        if not key_text:
            continue
        if len(key_text) > 64:
            key_text = key_text[:64]
        safe_value = _safe_glyph_diagnostic_value(value)
        if safe_value is not None:
            sanitized[key_text] = safe_value
    return sanitized


def _safe_glyph_diagnostic_value(value: Any, *, depth: int = 0) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if depth >= 3:
        return str(value)[:160]
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 60:
                break
            safe_key = str(key or "").strip()[:64]
            if not safe_key:
                continue
            safe_item = _safe_glyph_diagnostic_value(item, depth=depth + 1)
            if safe_item is not None:
                safe[safe_key] = safe_item
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_glyph_diagnostic_value(item, depth=depth + 1) for item in list(value)[:120]]
    return str(value)[:160]


def _select_record_glyph_diagnostics(
    diagnostics: Iterable[Mapping[str, Any]],
    *,
    texts: Iterable[str],
    limit: int = GLYPH_DIAGNOSTIC_RECORD_LIMIT,
) -> list[dict[str, Any]]:
    rows = _bounded_glyph_diagnostics(diagnostics, limit=GLYPH_DIAGNOSTIC_DOCUMENT_LIMIT)
    matched = [row for row in rows if _diagnostic_matches_record(row, texts)]
    if matched:
        return matched[:limit]
    return rows[:limit]


def _diagnostic_matches_record(diagnostic: Mapping[str, Any], texts: Iterable[str]) -> bool:
    haystack = "\n".join(str(text or "") for text in texts)
    if not haystack:
        return False
    for value in _diagnostic_text_values(diagnostic):
        if not value:
            continue
        if value in haystack:
            return True
        if len(value) > 4 and haystack in value:
            return True
    return False


def _diagnostic_text_values(diagnostic: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("raw_text", "glyph_context", "normalized_text", "context", "sample"):
        value = str(diagnostic.get(key) or "").strip()
        if value:
            values.append(value)
    for char_info in diagnostic.get("codepoints") or diagnostic.get("chars") or []:
        if not isinstance(char_info, Mapping):
            continue
        for key in ("char", "c", "sample"):
            value = str(char_info.get(key) or "")
            if value:
                values.append(value)
    return values


def build_chess_glyph_diagnostics_payload(
    records: Iterable[ChessPgnRecord | Mapping[str, Any]],
    *,
    source_title: str = "",
) -> dict[str, Any]:
    rows = [record.to_dict() if isinstance(record, ChessPgnRecord) else dict(record) for record in records]
    by_font: dict[str, int] = {}
    by_page: dict[str, int] = {}
    payload_records: list[dict[str, Any]] = []
    diagnostic_count = 0
    for row in rows:
        diagnostics = _bounded_glyph_diagnostics(
            row.get("glyph_diagnostics") or [],
            limit=max(0, GLYPH_DIAGNOSTIC_DOCUMENT_LIMIT - diagnostic_count),
        )
        if not diagnostics:
            continue
        diagnostic_count += len(diagnostics)
        for diagnostic in diagnostics:
            font = str(diagnostic.get("font_name") or "Unknown")
            by_font[font] = by_font.get(font, 0) + 1
            page = str(diagnostic.get("page") or diagnostic.get("page_index") or "")
            if page:
                by_page[page] = by_page.get(page, 0) + 1
        payload_records.append(
            {
                "record_id": str(row.get("id") or ""),
                "title": str(row.get("title") or "")[:160],
                "source_pages": list(row.get("source_pages") or []),
                "warnings": list(row.get("warnings") or []),
                "diagnostics": diagnostics,
            }
        )
        if diagnostic_count >= GLYPH_DIAGNOSTIC_DOCUMENT_LIMIT:
            break
    return {
        "source_title": source_title,
        "warning": UNMAPPED_CHESS_GLYPH_WARNING,
        "diagnostic_count": diagnostic_count,
        "record_count": len(payload_records),
        "by_font": dict(sorted(by_font.items())),
        "by_page": dict(sorted(by_page.items())),
        "records": payload_records,
    }


def _normalize_chessbase_private_symbols(text: str) -> str:
    normalized = str(text or "")
    for private_char, piece_letter in CHESSBASE_PIECE_FIGURINE_MAP.items():
        normalized = re.sub(f"{re.escape(private_char)}(?=[a-h](?:[a-h])?[1-8])", piece_letter, normalized)
    for private_char, symbol in CHESSBASE_ANNOTATION_SYMBOL_MAP.items():
        normalized = normalized.replace(private_char, symbol)
    return normalized


def _normalize_german_piece_notation(text: str) -> str:
    """Normalize German figurine letters in SAN-like tokens only.

    Translated comments often contain "Te1"/"Le2"/"Sf3". Without this guard
    the scanner can see the pawn move "e1"/"e2" inside the token. Keep the
    rule token-shaped so ordinary prose words are left alone.
    """
    piece_map = {"D": "Q", "T": "R", "L": "B", "S": "N"}

    def replace_piece(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{piece_map.get(match.group('piece'), match.group('piece'))}"

    return re.sub(
        r"(?P<prefix>(?<![A-Za-z0-9])(?:\d{1,3}\.(?:\.\.)?\s*)?)"
        r"(?P<piece>[DTLS])"
        r"(?=(?:[a-h]?[1-8]?x?[a-h][1-8]|[a-h][1-8]))",
        replace_piece,
        text or "",
    )


def _normalize_move_clock_artifacts(text: str) -> str:
    """Remove chess-clock/OCR counters that are interleaved with SAN tokens.

    Some chess PDFs expose side clocks as separate text runs between moves, for
    example ``1.Nc3 2 Nf6 3:14 2.d4 21d5``. Those numbers are not PGN; leaving
    them in makes black moves disappear or creates false move-number jumps.
    Keep the rule line-scoped and evidence-gated so ordinary move numbers stay
    intact.
    """
    san = _annotated_san_pattern()
    time_re = re.compile(r"\b\d{1,2}\s*:\s*\d{1,2}\b")
    attached_san_re = re.compile(rf"(?<![\w.])\d{{1,2}}(?=(?:{san})(?![A-Za-z0-9]))")
    standalone_noise_re = re.compile(
        rf"(?<![\w.])\d{{1,2}}\s+"
        rf"(?=(?:{san})(?![A-Za-z0-9])|\d{{1,3}}\.(?:\.\.)?|(?:1-0|0-1|1/2-1/2|0\.5-0\.5|\*)\b)"
    )

    def has_clock_artifacts(line: str) -> bool:
        if time_re.search(line):
            return True
        attached_count = len(attached_san_re.findall(line))
        standalone_count = len(standalone_noise_re.findall(line))
        return attached_count >= 2 or (attached_count >= 1 and standalone_count >= 2)

    def normalize_line(line: str) -> str:
        if not has_clock_artifacts(line):
            return line
        line = time_re.sub(" ", line)
        line = attached_san_re.sub("", line)
        line = standalone_noise_re.sub("", line)
        return re.sub(r"\s{2,}", " ", line)

    return "\n".join(normalize_line(line) for line in str(text or "").splitlines())


def _split_candidate_game_blocks(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines()]
    starts = [index for index, line in enumerate(lines) if DIAGRAM_LINE_RE.match(line)]
    if not starts:
        collection_blocks = _split_notation_collection_game_blocks(text)
        if collection_blocks:
            return collection_blocks
        return [{"caption": "", "body": text, "raw": text}] if NOTATION_HEAVY_RE.search(text) else []

    candidates: list[dict[str, str]] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        segment = [line for line in lines[start:end] if line]
        if not segment:
            continue
        notation_index = next((idx for idx, line in enumerate(segment) if _line_has_pgn_tokens(line)), -1)
        if notation_index < 0:
            continue
        caption_lines = segment[:notation_index]
        body_lines = segment[notation_index:]
        candidates.append(
            {
                "caption": " ".join(caption_lines).strip(),
                "body": "\n".join(body_lines).strip(),
                "raw": "\n".join(segment).strip(),
            }
        )
    return candidates


def _split_notation_collection_game_blocks(text: str) -> list[dict[str, str]]:
    lines = [_clean_collection_line_for_pgn(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not any(_line_has_pgn_tokens(line) for line in lines):
        return []

    blocks: list[list[str]] = []
    current: list[str] = []
    current_has_moves = False
    current_has_result = False

    for line in lines:
        starts_new_game = _looks_like_collection_game_start(line)
        if (
            current
            and starts_new_game
            and (current_has_moves or current_has_result)
            and len(current) >= 3
        ):
            blocks.append(current)
            current = []
            current_has_moves = False
            current_has_result = False
        elif (
            current
            and current_has_result
            and re.match(r"^\s*1\.(?:\.\.)?\s*\S+", line)
            and len(current) >= 3
        ):
            blocks.append(current)
            current = []
            current_has_moves = False
            current_has_result = False

        current.append(line)
        current_has_moves = current_has_moves or _line_has_pgn_tokens(line)
        current_has_result = current_has_result or bool(RESULT_RE.search(line))

    if current:
        blocks.append(current)

    candidates: list[dict[str, str]] = []
    for block in blocks:
        notation_index = next((idx for idx, line in enumerate(block) if _line_has_pgn_tokens(line)), -1)
        if notation_index < 0:
            continue
        body = "\n".join(block[notation_index:]).strip()
        if len(_extract_pgn_tokens(body)) < 4:
            continue
        candidates.append(
            {
                "caption": "\n".join(block[:notation_index]).strip(),
                "body": body,
                "raw": "\n".join(block).strip(),
            }
        )
    return candidates


def _looks_like_collection_game_start(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", line or "").strip()
    if not normalized:
        return False
    if COLLECTION_NUMBERED_ECO_RE.match(normalized):
        return True
    return bool(COLLECTION_GAME_START_RE.match(normalized) and not _line_has_pgn_tokens(normalized))


def _clean_collection_line_for_pgn(line: str) -> str:
    cleaned = normalize_ocr_text_for_pgn(line)
    cleaned = BOARD_FILES_PREFIX_ECO_RE.sub("", cleaned)
    cleaned = BOARD_FILES_INLINE_RE.sub(" ", cleaned)
    cleaned = BOARD_RANK_GRID_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" |")
    if not cleaned:
        return ""
    tokens = re.findall(r"[A-Za-z0-9]+", cleaned)
    if tokens and all(_is_board_coordinate_token(token) for token in tokens):
        return ""
    return cleaned


def _is_board_coordinate_token(token: str) -> bool:
    lowered = str(token or "").lower()
    if lowered in set("abcdefgh"):
        return True
    if lowered in set("12345678"):
        return True
    return bool(lowered and set(lowered) <= set("12345678") and len(lowered) <= 2)


def _line_has_pgn_tokens(line: str) -> bool:
    return bool(NOTATION_HEAVY_RE.search(line) or len(_extract_pgn_tokens(line)) >= 4)


def _strip_opening_descriptor_prefix(text: str, *, context: str = "") -> str:
    """Drop descriptive opening-line fragments before the real move one.

    Chess books often print a taxonomy line such as "D02: ... including
    2...Nf6 3 g3 and 2...Nf6 3 Bf4" immediately before the actual game.
    Those fragments are examples, not game movetext. If the same block later
    contains a proper white move one, start there and let strict replay decide.
    """
    sample = str(text or "")
    if not sample.strip():
        return sample
    first_match = _find_explicit_movetext_match(sample)
    if not first_match:
        return sample
    first_marker = re.match(r"(?P<num>\d{1,3})\.(?P<black>\.\.)?", first_match.group(0).strip())
    if not first_marker:
        return sample
    if int(first_marker.group("num")) <= 1 and not first_marker.group("black"):
        return sample

    for match in _iter_explicit_movetext_matches(sample[first_match.end() :]):
        marker = re.match(r"(?P<num>\d{1,3})\.(?P<black>\.\.)?", match.group(0).strip())
        if not marker or int(marker.group("num")) != 1 or marker.group("black"):
            continue
        start = first_match.end() + match.start()
        prefix = sample[:start]
        if _looks_like_opening_descriptor_text(f"{context}\n{prefix}"):
            return sample[start:].lstrip()
    return sample


def _looks_like_opening_descriptor_only(
    *,
    caption: str,
    body: str,
    movetext: str,
    result: str,
    halfmove_count: int,
) -> bool:
    first_move = _first_movetext_number(movetext)
    if first_move is None or first_move <= 1:
        return False
    if result != "*" or halfmove_count > 4:
        return False
    return _looks_like_opening_descriptor_text(f"{caption}\n{body}")


def _looks_like_opening_descriptor_text(text: str) -> bool:
    sample = re.sub(r"\s+", " ", str(text or "")).strip()
    if not sample:
        return False
    if RESULT_RE.search(sample):
        return False
    if OPENING_DESCRIPTOR_RE.search(sample):
        return True
    if ECO_CODE_RE.search(sample) and re.search(r"(?i)\band\b", sample):
        return True
    return False


def _extract_pgn_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    normalized_text = _prepare_mainline_token_source(text)
    seen_movetext = False
    for line in normalized_text.splitlines() or [normalized_text]:
        explicit_movetext = _line_has_explicit_movetext(line)
        if not explicit_movetext and not (seen_movetext and _line_looks_like_pgn_continuation(line)):
            continue
        already_seen_movetext = seen_movetext
        seen_movetext = True
        scan_line = (
            _movetext_scan_slice(line, allow_prefix_continuation=already_seen_movetext)
            if explicit_movetext
            else line
        )
        for match in TOKEN_SCAN_RE.finditer(scan_line):
            raw = match.group(0).strip()
            if not raw:
                continue
            if MOVE_NUMBER_RE.match(raw) or RESULT_RE.match(raw):
                tokens.append(raw)
                continue
            san = _sanitize_san_token(raw)
            if san:
                tokens.append(san)
    return tokens


def _prepare_mainline_token_source(text: str) -> str:
    prepared = normalize_ocr_text_for_pgn(text)
    prepared = re.sub(r"(?i)\(\s*diagram\s*\)", " ", prepared)
    prepared = _strip_prose_move_references(prepared)
    prepared = _strip_analysis_variations(prepared)
    prepared = _strip_engine_eval_annotations(prepared)
    san = _annotated_san_pattern()
    prepared = re.sub(
        rf"(?P<san>{san})\s*(?:\u00b1|\u2213|\u2a71|\u2a72|\+\-|-\+|=)(?=\s+(?:{san}|\d{{1,3}}\.|$))",
        r"\g<san> ",
        prepared,
    )
    prepared = re.sub(
        rf"(?P<san>{san})\.\.\.\s*(?P<comment>.*?)(?=(?:\s+\d{{1,3}}\.)|$)",
        r"\g<san> ",
        prepared,
    )
    prepared = re.sub(
        rf"(?P<san>{san})=\s*(?![QRBN](?:[+#]?[!?]{{0,2}})?(?=\s|$|[,.;:\]\)}}]))(?=[A-Z])(?P<comment>.*?)(?=(?:\s+\d{{1,3}}\.)|$)",
        r"\g<san> ",
        prepared,
    )
    return prepared


def _strip_prose_move_references(text: str) -> str:
    stripped = _leading_prose_move_reference_pattern().sub(" ", text or "")
    return _replace_duplicate_trailing_prose_move_references(stripped, as_comment=False)


def _comment_prose_move_references(text: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        lead = re.sub(r"\s+", " ", match.group("lead")).strip()
        move = re.sub(r"\s+", "", match.group("move")).strip()
        if re.search(r"(?i)\b(?:should\s+(?:play|try)|better\s+is|worse\s+is|stronger\s+than|and\s+not)\b", lead):
            return f"{_comment_token(lead)} {move}"
        return _comment_token(match.group(0))

    commented = _leading_prose_move_reference_pattern().sub(replacement, text or "")
    return _replace_duplicate_trailing_prose_move_references(commented, as_comment=True)


def _leading_prose_move_reference_pattern() -> re.Pattern[str]:
    san = _annotated_san_pattern()
    return re.compile(
        rf"(?i)\b(?P<lead>(?:instead\s+of|anstelle\s+von|rather\s+than|called\s+for|was\s+called\s+for|"
        rf"should\s+(?:play|try)|better\s+is|worse\s+is|and\s+not|don't\s+play|do\s+not\s+play|"
        rf"more\s+successful\s+than|erfolgreicher\s+als|stronger\s+than)"
        rf")\s+(?P<move>\d{{1,3}}\.(?:\.\.)?\s*{san})"
    )


def _replace_duplicate_trailing_prose_move_references(text: str, *, as_comment: bool) -> str:
    source = text or ""
    san = _annotated_san_pattern()
    split_modern = r"m\s*o\s*d\s*e\s*r\s*n"
    split_continuation = r"c\s*o\s*n\s*t\s*i\s*n\s*u\s*a\s*t\s*i\s*o\s*n"
    split_fortsetzung = r"F\s*o\s*r\s*t\s*s\s*e\s*t\s*z\s*u\s*n\s*g"
    pattern = re.compile(
        rf"(?is)(?P<move>(?P<num>\d{{1,3}})\.\s*{san})\s+"
        rf"(?P<comment>(?:is\s+the\s+(?:modern\s+continuation|{split_modern}\s+{split_continuation})|"
        rf"ist\s+die\s+(?:moderne\s+Fortsetzung|{split_modern}\s*e\s+{split_fortsetzung})|"
        rf"got\s+a\s+lot\s+of\s+attention|erh[aä]lt\s+[^.]*Aufmerksamkeit|"
        rf"(?:is\s+)?(?:setting|sets)\s+a\s+new\s+trend|setzt\s+[^.]*Trend|"
        rf"is\s+the\s+fancy\s+move|ist\s+der\s+angesagte\s+Zug)[^.]*\.?)"
    )
    output: list[str] = []
    last = 0
    for match in pattern.finditer(source):
        try:
            move_num = int(match.group("num"))
        except ValueError:
            continue
        if not _same_white_move_number_seen_before(source[: match.start()], move_num):
            continue
        output.append(source[last : match.start()])
        replacement = _comment_token(match.group(0)) if as_comment else " "
        output.append(replacement)
        last = match.end()
    if not output:
        return source
    output.append(source[last:])
    return "".join(output)


def _same_white_move_number_seen_before(prefix: str, move_num: int) -> bool:
    pattern = re.compile(rf"(?<![\d.]){move_num}\.\s*{_annotated_san_pattern()}")
    return len(pattern.findall(prefix[-220:])) > 0


def _strip_analysis_variations(text: str) -> str:
    stripped = _strip_balanced_square_bracket_segments(text or "")
    stripped = re.sub(r"\([^)]*\d{1,3}\.(?:\.\.)?[^)]*\)", " ", stripped, flags=re.DOTALL)
    return stripped


def _strip_balanced_square_bracket_segments(text: str) -> str:
    output: list[str] = []
    depth = 0
    for char in str(text or ""):
        if char == "[":
            depth += 1
            if depth == 1:
                output.append(" ")
            continue
        if char == "]" and depth:
            depth -= 1
            if depth == 0:
                output.append(" ")
            continue
        if depth:
            continue
        output.append(char)
    return "".join(output)


def _strip_engine_eval_annotations(text: str) -> str:
    stripped = WEIGHTED_ERROR_VALUE_RE.sub(" ", text or "")
    stripped = WEIGHTED_ERROR_SIDE_RE.sub(" ", stripped)
    stripped = OCR_HASH_ARTIFACT_RE.sub(" ", stripped)
    stripped = SENSOR_BOARD_ERROR_RE.sub(" ", stripped)
    stripped = TACTICAL_ANALYSIS_RE.sub(" ", stripped)
    stripped = ENGINE_EVAL_RE.sub(" ", stripped)
    stripped = BARE_ENGINE_EVAL_RE.sub(" ", stripped)
    return stripped


def _line_has_explicit_movetext(line: str) -> bool:
    sample = str(line or "")
    if RESULT_RE.search(sample.strip()):
        return True
    return _find_explicit_movetext_match(sample) is not None


def _movetext_scan_slice(line: str, *, allow_prefix_continuation: bool = True) -> str:
    match = _find_explicit_movetext_match(line or "")
    if not match:
        return line or ""
    prefix = (line or "")[: match.start()].strip()
    if allow_prefix_continuation and prefix and _line_looks_like_pgn_continuation(prefix):
        return line or ""
    return (line or "")[match.start():]


def _find_explicit_movetext_match(line: str) -> re.Match[str] | None:
    return next(_iter_explicit_movetext_matches(line), None)


def _iter_explicit_movetext_matches(line: str) -> Iterable[re.Match[str]]:
    for match in re.finditer(r"\b\d{1,3}\.(?:\.\.)?\s*", line or ""):
        rest = (line or "")[match.end():].lstrip()
        if not rest:
            continue
        token_match = TOKEN_SCAN_RE.search(rest)
        if not token_match:
            continue
        token = token_match.group(0).strip()
        if MOVE_NUMBER_RE.match(token):
            continue
        if RESULT_RE.match(token) or _sanitize_san_token(token):
            yield match


def _line_looks_like_pgn_continuation(line: str) -> bool:
    sample = re.sub(r"\s+", " ", str(line or "")).strip()
    if not sample or len(sample) > 80:
        return False
    if re.search(r"(?i)\b(?:position|threat|threatening|better|worse|superior|called|considered|diagram)\b", sample):
        return False
    tokens = [match.group(0).strip() for match in TOKEN_SCAN_RE.finditer(sample)]
    if not tokens:
        return False
    token_chars = sum(len(token) for token in tokens)
    sample_chars = len(re.sub(r"[\s,;:!?(){}\[\]\-+.=/0-9]", "", sample))
    if sample_chars == 0:
        return False
    return token_chars >= max(2, sample_chars * 0.55)


def _sanitize_san_token(raw: str) -> str:
    token = str(raw or "").strip(" \t\n\r,;:()[]{}")
    if not token:
        return ""
    token = token.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
    token = token.replace("\u2020", "+").replace("\u2021", "#").replace("†", "+").replace("‡", "#")
    token = re.sub(r"[¢t]$", "+", token)
    token = re.sub(r"\s+", "", token)
    token = re.sub(r"=\s*([QRBN])", r"=\1", token)
    if token[:1] in {"D", "£"} and re.match(r"^[D£]x?[a-h][1-8]", token):
        token = "Q" + token[1:]
    elif token[:1] == "W" and re.match(r"^Wx?[a-h][1-8]", token):
        token = "R" + token[1:]
    elif token[:1] == "A" and re.match(r"^Ax?[a-h][1-8]", token):
        token = "N" + token[1:]
    elif token[:1] == "&" and re.match(r"^&x?[a-h][1-8]", token):
        token = "B" + token[1:]
    elif token[:1] == "@" and re.match(r"^@x?[a-h][1-8]", token):
        token = "K" + token[1:]
    elif token[:1] == "8" and re.match(r"^8x?[a-h][1-8]", token):
        token = "B" + token[1:]
    elif token[:1] == "P" and re.match(r"^Px?[a-h][1-8]", token):
        token = token[1:]
    token = re.sub(r"([+#])[+#]+$", r"\1", token)
    if SAN_RE.match(token):
        return token
    return ""


def _tokens_to_movetext(tokens: Iterable[str]) -> tuple[str, str, int, list[str]]:
    token_list = list(tokens)
    fragments: list[str] = []
    warnings: list[str] = []
    current_move: int | None = None
    side = "w"
    result = "*"
    halfmove_count = 0
    saw_move_number = False

    index = 0
    while index < len(token_list):
        token = token_list[index]
        index += 1
        move_match = MOVE_NUMBER_RE.match(token)
        if move_match:
            saw_move_number = True
            move_num = int(move_match.group("num"))
            marker_side = "b" if move_match.group("black") else "w"
            if move_num <= 0:
                warnings.append("invalid_move_number_zero")
                continue
            if current_move is not None:
                if halfmove_count > 0 and move_num < current_move:
                    warnings.append("move_number_regression")
                elif halfmove_count > 0 and move_num > current_move:
                    warnings.append("move_number_jump")
                if halfmove_count > 0 and move_num == current_move and marker_side != side:
                    warnings.append("side_to_move_mismatch")
                elif halfmove_count > 0 and move_num != current_move:
                    warnings.append("side_to_move_mismatch")
            current_move = move_num
            side = marker_side
            continue
        if token in RESULT_VALUES:
            result = "1/2-1/2" if token == "0.5-0.5" else token
            continue
        if not SAN_RE.match(token):
            warnings.append("invalid_san_token_skipped")
            continue
        if current_move is None:
            current_move = 1
            warnings.append("move_number_inferred")
        if _san_is_shadowed_by_explicit_same_ply_marker(token_list, index, current_move, side):
            continue
        if side == "w":
            fragments.append(f"{current_move}. {token}")
            side = "b"
        else:
            if fragments and fragments[-1].startswith(f"{current_move}. "):
                fragments[-1] = f"{fragments[-1]} {token}"
            else:
                fragments.append(f"{current_move}... {token}")
            current_move += 1
            side = "w"
        halfmove_count += 1

    if not saw_move_number:
        warnings.append("no_explicit_move_number")
    if result != "*" and (not fragments or fragments[-1] != result):
        fragments.append(result)
    elif result == "*" and fragments:
        fragments.append("*")
    return " ".join(fragments).strip(), result, halfmove_count, sorted(set(warnings))


def _san_is_shadowed_by_explicit_same_ply_marker(
    tokens: list[str],
    start_index: int,
    current_move: int,
    side: str,
) -> bool:
    expected_marker_side = side
    for token in tokens[start_index : start_index + 5]:
        if token in RESULT_VALUES:
            return False
        move_match = MOVE_NUMBER_RE.match(token)
        if not move_match:
            continue
        marker_move = int(move_match.group("num"))
        marker_side = "b" if move_match.group("black") else "w"
        return marker_move == current_move and marker_side == expected_marker_side
    return False


def _build_headers(*, title: str, caption: str, source_title: str, result: str, fen: str = "") -> dict[str, str]:
    white = "?"
    black = "?"
    site = "?"
    date = "????.??.??"
    event = _clean_header_value(source_title or title or "KindleMaster OCR")
    round_value = "?"
    caption_text = re.sub(r"\s+", " ", caption or "").strip()
    player_match = PLAYER_CAPTION_RE.search(caption_text)
    if player_match:
        white = _clean_header_value(player_match.group("white"))
        black = _clean_header_value(player_match.group("black"))
    year_match = YEAR_RE.search(caption_text)
    if year_match:
        year = year_match.group(0)
        date = f"{year}.??.??"
        before_year = caption_text[: year_match.start()].strip(" -,_")
        if before_year:
            site_candidate = before_year.split()[-3:]
            site = _clean_header_value(" ".join(site_candidate)) or "?"
    collection_metadata = _parse_collection_caption_metadata(caption)
    if collection_metadata:
        event = collection_metadata.get("Event") or event
        site = collection_metadata.get("Site") or site
        date = collection_metadata.get("Date") or date
        round_value = collection_metadata.get("Round") or round_value
        white = collection_metadata.get("White") or white
        black = collection_metadata.get("Black") or black
    headers = {
        "Event": event,
        "Site": site,
        "Date": date,
        "Round": round_value,
        "White": white,
        "Black": black,
        "Result": result or "*",
    }
    for key in ("WhiteElo", "BlackElo", "ECO", "Opening", "Annotator", "Source"):
        if collection_metadata.get(key):
            headers[key] = collection_metadata[key]
    if fen:
        headers["SetUp"] = "1"
        headers["FEN"] = fen
    return headers


def _format_pgn(headers: Mapping[str, str], movetext: str, result: str) -> str:
    ordered = [
        "Event",
        "Site",
        "Date",
        "Round",
        "White",
        "Black",
        "Result",
        "WhiteElo",
        "BlackElo",
        "ECO",
        "Opening",
        "Annotator",
        "Source",
        "SetUp",
        "FEN",
    ]
    lines: list[str] = []
    for key in ordered:
        if key not in headers:
            continue
        value = str(headers.get(key) or "?").replace('"', "'")
        lines.append(f'[{key} "{value}"]')
    for key in sorted(set(headers) - set(ordered)):
        value = str(headers.get(key) or "?").replace('"', "'")
        lines.append(f'[{key} "{value}"]')
    body = (movetext or "").strip()
    if body and result and result != "*" and not body.endswith(result):
        body = f"{body} {result}"
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def _format_annotated_pgn(
    headers: Mapping[str, str],
    raw_text: str,
    *,
    fallback_movetext: str,
    result: str,
) -> str:
    annotated_movetext = _annotated_movetext_from_raw(raw_text, fallback_movetext=fallback_movetext)
    if not annotated_movetext:
        annotated_movetext = fallback_movetext
    return _format_pgn(headers, annotated_movetext, result)


def _annotated_movetext_from_raw(raw_text: str, *, fallback_movetext: str = "") -> str:
    lines = [normalize_ocr_text_for_pgn(line) for line in str(raw_text or "").splitlines()]
    body_lines: list[str] = []
    started = False
    for line in lines:
        cleaned = re.sub(r"\s+", " ", line or "").strip(" |")
        if not cleaned:
            continue
        if not started:
            if not _line_has_explicit_movetext(cleaned):
                continue
            cleaned = _movetext_scan_slice(cleaned, allow_prefix_continuation=False)
            started = True
        if _is_annotated_noise_line(cleaned):
            continue
        body_lines.append(cleaned)
    if not body_lines:
        return fallback_movetext
    body_text = "\n".join(body_lines)
    body_text = _merge_weighted_error_value_lines(body_text)
    annotated = _annotated_segment_to_pgn(body_text)
    return annotated or fallback_movetext


def _is_annotated_noise_line(line: str) -> bool:
    sample = re.sub(r"\s+", " ", str(line or "")).strip()
    if not sample:
        return True
    if re.fullmatch(r"(?i)\(?\s*diagram\s*\)?", sample):
        return True
    if _looks_like_pgn_board_coordinate_noise(sample):
        return True
    return False


def _looks_like_pgn_board_coordinate_noise(text: str) -> bool:
    if _sanitize_san_token(text):
        return False
    tokens = re.findall(r"[A-Za-z0-9]+", text or "")
    compact = re.sub(r"\s+", "", text or "")
    if compact and set(compact.lower()) <= set("abcdefgh12345678") and len(compact) >= 2:
        return True
    if len(tokens) < 4:
        return False
    lowered = [token.lower() for token in tokens]
    if all(token in set("abcdefgh") for token in lowered):
        return True
    if all(token in set("12345678") for token in lowered):
        return True
    return all(token and set(token) <= set("12345678") and len(token) <= 2 for token in lowered)


def _merge_weighted_error_value_lines(text: str) -> str:
    merged = re.sub(
        r"(?i)Weighted Error Value:\s*White\s*=\s*(?P<white>[+-]?\d+\.\s*\d+)\s*/\s*\n\s*Black\s*=\s*(?P<black>[+-]?\d+\.\s*\d+)",
        lambda match: (
            "Weighted Error Value: "
            f"White={_compact_decimal(match.group('white'))}/ "
            f"Black={_compact_decimal(match.group('black'))}"
        ),
        text or "",
    )
    return merged


def _compact_decimal(value: str) -> str:
    return re.sub(r"(?<=\d)\.\s+(?=\d)", ".", str(value or "").strip())


def _annotated_segment_to_pgn(text: str) -> str:
    prepared = _prepare_annotated_source_text(text)
    prepared = _convert_square_bracket_variations(prepared)
    return _scan_annotated_tokens(prepared)


def _prepare_annotated_source_text(text: str) -> str:
    prepared = normalize_ocr_text_for_pgn(text)
    prepared = re.sub(r"(?i)\(\s*diagram\s*\)", " ", prepared)
    prepared = SENSOR_BOARD_ERROR_RE.sub(lambda match: _comment_token(match.group(0)), prepared)
    prepared = _normalize_chessbase_pgn_extensions(prepared)
    prepared = prepared.replace("&Bianco", "Bianco")
    prepared = prepared.replace("\u2312", " ")
    prepared = _comment_prose_move_references(prepared)
    prepared = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", prepared)
    prepared = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", prepared)
    prepared = re.sub(r"(?P<eval>=?[+\-]?\d+\.\d+/\d+)(?=[A-Za-z])", r"\g<eval> ", prepared)
    prepared = re.sub(
        rf"(?P<san>{_annotated_san_pattern()})(?P<eval>=?[+\-]\d+\.\d+/\d+)",
        r"\g<san> \g<eval>",
        prepared,
    )
    prepared = re.sub(r"(?<=\d)\.\s+\.\.\s*", "...", prepared)
    prepared = re.sub(r"(?<=\d)\s+\.\s+\.\s+\.", "...", prepared)
    prepared = re.sub(r"(?<=\d)\s+\.", ".", prepared)
    prepared = _wrap_weighted_error_value_comments(prepared)
    prepared = _wrap_move_eval_symbols(prepared)
    prepared = _normalize_inline_move_comments(prepared)
    prepared = _wrap_engine_eval_comments(prepared)
    return prepared


def _normalize_chessbase_pgn_extensions(text: str) -> str:
    normalized = re.sub(
        r"(?i)\[\s*%eval\s+(?P<eval>[^\]]+)\]",
        lambda match: _comment_token(_compact_engine_eval(match.group("eval").strip())),
        text or "",
    )
    normalized = re.sub(r"(?i)\[\s*%(?:emt|clk)[^\]]*\]", " ", normalized)
    normalized = re.sub(r"(?i)\[\s*%[a-z][^\]]*\]", " ", normalized)
    return normalized


def _wrap_weighted_error_value_comments(text: str) -> str:
    return re.sub(
        r"(?is)Weighted Error Value:\s*.*?(?=(?:\s+(?:1-0|0-1|1/2-1/2|0\.5-0\.5|\*)\b)|$)",
        lambda match: _comment_token(re.sub(r"\s+", " ", match.group(0)).strip()),
        text or "",
    )


def _normalize_inline_move_comments(text: str) -> str:
    san = _annotated_san_pattern()
    normalized = re.sub(
        rf"(?P<san>{san})\.\.\.\s*(?P<comment>.*?)(?=(?:\s+\d{{1,3}}\.)|$)",
        lambda match: f"{match.group('san')} {_comment_token(match.group('comment'))}",
        text or "",
    )
    normalized = re.sub(
        rf"(?P<san>{san})=\s*(?![QRBN](?:[+#]?[!?]{{0,2}})?(?=\s|$|[,.;:\]\)}}]))(?=[A-Z])(?P<comment>.*?)(?=(?:\s+\d{{1,3}}\.)|$)",
        lambda match: f"{match.group('san')} {_comment_token(match.group('comment'))}",
        normalized,
    )
    return normalized


def _wrap_engine_eval_comments(text: str) -> str:
    return re.sub(
        r"(?<![\w{}])(?P<eval>=?[+\-]?\s*\d+\.\s*\d+\s*/\s*\d+)(?![\w{}])",
        lambda match: _comment_token(_compact_engine_eval(match.group("eval"))),
        text or "",
    )


def _compact_engine_eval(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or ""))
    compact = re.sub(r"^=", "", compact)
    return compact


def _wrap_move_eval_symbols(text: str) -> str:
    san = _annotated_san_pattern()
    eval_symbols = r"(?:\u00b1|\u2213|\u2a71|\u2a72|\+\-|-\+|=)"
    return re.sub(
        rf"(?P<san>{san})(?P<eval>{eval_symbols})(?=\s+(?:\d{{1,3}}\.|[KQRBNOa-h]|\{{|\(|\[))",
        lambda match: f"{match.group('san')} {_comment_token(match.group('eval'))}",
        text or "",
    )


def _convert_square_bracket_variations(text: str) -> str:
    converted = str(text or "")
    bracket_re = re.compile(r"\[(?P<inner>[^\[\]]+)\]", re.DOTALL)
    while True:
        match = bracket_re.search(converted)
        if not match:
            return converted
        inner = match.group("inner").strip()
        inner_pgn = _annotated_segment_to_pgn(inner)
        replacement = f"({inner_pgn})" if inner_pgn else ""
        converted = converted[: match.start()] + replacement + converted[match.end() :]


def _scan_annotated_tokens(text: str) -> str:
    source = re.sub(r"\s+", " ", str(text or "")).strip()
    tokens: list[str] = []
    index = 0
    while index < len(source):
        if source[index].isspace():
            index += 1
            continue
        if source[index] == "{":
            end = source.find("}", index + 1)
            if end < 0:
                comment = source[index + 1 :].strip()
                index = len(source)
            else:
                comment = source[index + 1 : end].strip()
                index = end + 1
            _append_comment(tokens, comment)
            continue
        if source[index] == "(":
            inner, end = _extract_balanced_parentheses(source, index)
            inner_pgn = _annotated_segment_to_pgn(inner)
            if inner_pgn:
                tokens.append(f"({inner_pgn})")
            index = end
            continue
        move_match = re.match(_annotated_move_number_pattern(), source[index:])
        if move_match:
            tokens.append(_normalize_move_number_for_pgn(move_match.group(0)))
            index += move_match.end()
            continue
        result_match = re.match(r"(?:(?:1-0|0-1|1/2-1/2|0\.5-0\.5)(?![\w/.-])|\*)", source[index:])
        if result_match:
            tokens.append("1/2-1/2" if result_match.group(0) == "0.5-0.5" else result_match.group(0))
            index += result_match.end()
            continue
        san_match = re.match(_annotated_san_pattern(), source[index:])
        if san_match:
            san = _sanitize_san_token(san_match.group(0))
            if san:
                tokens.append(san)
                index += san_match.end()
                continue
        comment_end = _next_annotated_token_index(source, index + 1)
        comment = source[index:comment_end].strip(" ,;:")
        _append_comment_or_trailing_san(tokens, comment)
        index = comment_end
    return _join_annotated_tokens(tokens)


def _extract_balanced_parentheses(text: str, start: int) -> tuple[str, int]:
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    return text[start + 1 :], len(text)


def _next_annotated_token_index(text: str, start: int) -> int:
    candidates = [len(text)]
    for pattern in (
        r"\{",
        r"\(",
        _annotated_move_number_pattern(),
        r"(?:(?:1-0|0-1|1/2-1/2|0\.5-0\.5)(?![\w/.-])|\*)",
    ):
        match = re.search(pattern, text[start:])
        if match:
            candidates.append(start + match.start())
    return min(candidates)


def _annotated_san_pattern() -> str:
    return (
        r"(?:O-O(?:-O)?"
        r"|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
        r"|[a-h]x[a-h][1-8](?:=[QRBN])?[+#]?"
        r"|[a-h][1-8](?:=[QRBN])?[+#]?"
        r")(?:[!?]{0,2})"
    )


def _annotated_move_number_pattern() -> str:
    return rf"(?<![A-Za-z])\b\d{{1,3}}\.(?:\.\.)?(?=\s*{_annotated_san_pattern()})"


def _normalize_move_number_for_pgn(token: str) -> str:
    match = re.match(r"(?P<num>\d{1,3})\.(?P<black>\.\.)?", token or "")
    if not match:
        return token
    return f"{match.group('num')}{'...' if match.group('black') else '.'}"


def _comment_token(comment: str) -> str:
    cleaned = _clean_pgn_comment(comment)
    return f"{{{cleaned}}}" if cleaned else ""


def _append_comment(tokens: list[str], comment: str) -> None:
    cleaned = _clean_pgn_comment(comment)
    if cleaned:
        tokens.append(f"{{{cleaned}}}")


def _append_comment_or_trailing_san(tokens: list[str], comment: str) -> None:
    cleaned = _clean_pgn_comment(comment)
    if not cleaned:
        return
    # OCR often emits prose plus the next variation move as one fragment:
    # "might work better. Rxb4 38.hxg6+". Keep the prose as a comment and
    # expose the trailing SAN move so the RAV remains parseable.
    match = re.match(rf"(?P<comment>.+[.!?])\s+(?P<san>{_annotated_san_pattern()})$", cleaned)
    if match:
        san = _sanitize_san_token(match.group("san"))
        comment_text = match.group("comment").strip()
        if san and comment_text:
            _append_comment(tokens, comment_text)
            tokens.append(san)
            return
    tokens.append(f"{{{cleaned}}}")


def _clean_pgn_comment(comment: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(comment or "")).strip(" {}[]();,")
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    cleaned = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", cleaned)
    cleaned = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", cleaned)
    cleaned = _normalize_spaced_ocr_comment(cleaned)
    return cleaned


def _normalize_spaced_ocr_comment(comment: str) -> str:
    cleaned = str(comment or "")

    def collapse_lowercase_letters(match: re.Match[str]) -> str:
        compact = match.group(0).replace(" ", "")
        return _segment_spaced_ocr_words(compact)

    # OCR sometimes splits ordinary words in comments: "a i m i n g f o r".
    cleaned = re.sub(r"\b(?:[a-z]\s+){2,}[a-z]\b", collapse_lowercase_letters, cleaned)
    cleaned = re.sub(r"\b([KQRBN])\s+([a-h])\s*([1-8])\b", r"\1\2\3", cleaned)
    cleaned = re.sub(r"\b([a-h])\s*([1-8])\b", r"\1\2", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _segment_spaced_ocr_words(compact: str) -> str:
    word = str(compact or "")
    if len(word) < 6:
        return word
    lexicon = {
        "and",
        "be",
        "better",
        "called",
        "clearly",
        "does",
        "endgame",
        "equal",
        "for",
        "from",
        "intending",
        "is",
        "mate",
        "much",
        "now",
        "play",
        "position",
        "recover",
        "should",
        "strongly",
        "than",
        "the",
        "this",
        "threatening",
        "threatens",
        "to",
        "try",
        "white",
        "winning",
        "with",
        "worse",
        "would",
        "aiming",
    }
    best: list[str] | None = None

    def visit(index: int, parts: list[str]) -> None:
        nonlocal best
        if best is not None:
            return
        if index == len(word):
            best = parts[:]
            return
        for end in range(len(word), index + 1, -1):
            candidate = word[index:end]
            if candidate in lexicon:
                visit(end, parts + [candidate])

    visit(0, [])
    if best and len(best) > 1:
        return " ".join(best)
    return word


def _join_annotated_tokens(tokens: Iterable[str]) -> str:
    output: list[str] = []
    for token in tokens:
        if not token:
            continue
        if not output:
            output.append(token)
            continue
        output.append(token)
    return " ".join(output).strip()


def _candidate_title(caption: str, *, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", caption or "").strip(" -")
    if not cleaned:
        return fallback
    return cleaned[:120]


def _parse_collection_caption_metadata(caption: str) -> dict[str, str]:
    lines = [re.sub(r"\s+", " ", line or "").strip(" -") for line in str(caption or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return {}

    metadata: dict[str, str] = {}
    player_rows: list[tuple[str, str]] = []
    collection_signal = False
    for line in lines:
        normalized_line = _strip_collection_prefix(line)
        eco = _extract_eco_code(normalized_line)
        if eco and "ECO" not in metadata:
            metadata["ECO"] = eco
            collection_signal = True
        opening = _extract_opening(normalized_line)
        if opening and "Opening" not in metadata:
            metadata["Opening"] = opening
            collection_signal = True
            continue
        bracket = _extract_bracket_value(normalized_line)
        if bracket:
            collection_signal = True
            metadata.setdefault("Source", bracket)
            metadata.setdefault("Annotator", bracket)
            metadata.setdefault("Site", bracket)
            continue
        event = _extract_event_line(normalized_line)
        if event:
            collection_signal = True
            metadata["Event"] = event["event"]
            if event.get("round"):
                metadata["Round"] = event["round"]
            if event.get("date"):
                metadata["Date"] = event["date"]
            continue
        player_rows.extend(_extract_player_rows(normalized_line))

    if not collection_signal and len(player_rows) < 2:
        return {}
    if player_rows:
        metadata["White"] = player_rows[0][0]
        if player_rows[0][1]:
            metadata["WhiteElo"] = player_rows[0][1]
    if len(player_rows) > 1:
        metadata["Black"] = player_rows[1][0]
        if player_rows[1][1]:
            metadata["BlackElo"] = player_rows[1][1]
    return {key: _clean_header_value(value) for key, value in metadata.items() if _clean_header_value(value) != "?"}


def _strip_collection_prefix(line: str) -> str:
    cleaned = re.sub(r"^\s*\d{1,5}\s+(?=[A-Ea-e][0-9Oo]{2}\b)", "", line or "").strip()
    return cleaned


def _extract_eco_code(line: str) -> str:
    match = ECO_CODE_RE.search(line or "")
    if not match:
        return ""
    return match.group("eco").upper().replace("O", "0")


def _extract_opening(line: str) -> str:
    match = re.search(r"\b[A-Ea-e][0-9Oo]{2}\s*:\s*(?P<opening>.+)$", line or "")
    if not match:
        return ""
    return _clean_header_value(match.group("opening").strip(" '\""))


def _extract_bracket_value(line: str) -> str:
    match = re.match(r"^\[(?P<value>[^\]]{2,80})\]$", line or "")
    return _clean_header_value(match.group("value")) if match else ""


def _extract_event_line(line: str) -> dict[str, str]:
    sample = line or ""
    if not re.search(
        r"(?i)\b(?:titled|blitz|rapid|classical|championship|tournament|arena|bullet|early|late|fide|world)\b",
        sample,
    ):
        return {}
    round_value = ""
    round_match = re.search(r"\((?P<round>\d{1,3})\)\s*$", sample)
    if round_match:
        round_value = round_match.group("round")
        sample = sample[: round_match.start()].strip()
    parsed_date = _extract_partial_event_date(sample)
    return {
        "event": _clean_header_value(sample),
        "round": round_value,
        "date": parsed_date,
    }


def _extract_partial_event_date(line: str) -> str:
    month_lookup = {
        "jan": "01",
        "feb": "02",
        "mar": "03",
        "apr": "04",
        "may": "05",
        "jun": "06",
        "jul": "07",
        "aug": "08",
        "sep": "09",
        "oct": "10",
        "nov": "11",
        "dec": "12",
    }
    year_match = YEAR_RE.search(line or "")
    year = year_match.group(0) if year_match else "????"
    match = re.search(r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]{3,9})\b", line or "", re.IGNORECASE)
    if not match:
        return f"{year}.??.??" if year != "????" else ""
    month = month_lookup.get(match.group("month")[:3].lower(), "??")
    day = int(match.group("day"))
    return f"{year}.{month}.{day:02d}"


def _extract_player_rows(line: str) -> list[tuple[str, str]]:
    sample = _strip_collection_prefix(line)
    eco = _extract_eco_code(sample)
    if eco:
        sample = re.sub(r"^\s*[A-Ea-e][0-9Oo]{2}\b", "", sample).strip()
    if not sample or _extract_opening(sample) or _extract_bracket_value(sample) or _extract_event_line(sample):
        return []
    two_players = _extract_two_comma_players_with_optional_white_elo(sample)
    if two_players:
        return two_players
    with_elos = [
        (_clean_player_name(match.group("name")), match.group("elo"))
        for match in re.finditer(r"(?P<name>[A-Z][^0-9]{1,80}?)\s+(?P<elo>[12]\d{3})(?=\s+[A-Z]|\s*$)", sample)
    ]
    with_elos = [(name, elo) for name, elo in with_elos if name]
    if with_elos:
        return with_elos[:2]
    name = _clean_player_name(sample)
    return [(name, "")] if _looks_like_player_name(name) else []


def _extract_two_comma_players_with_optional_white_elo(line: str) -> list[tuple[str, str]]:
    sample = re.sub(r"\s+", " ", line or "").strip()
    match = re.match(
        r"^(?P<white>[A-Z][^,]{1,60},\s*[A-Z][A-Za-z.'-]{0,24})"
        r"(?:\s+(?P<white_elo>[12]\d{3}))?\s+"
        r"(?P<black>[A-Z][^,]{1,60},\s*[^0-9]+?)"
        r"(?:\s+(?P<black_elo>[12]\d{3}))?$",
        sample,
    )
    if not match:
        return []
    white = _clean_player_name(match.group("white"))
    black = _clean_player_name(match.group("black"))
    if not (_looks_like_player_name(white) and _looks_like_player_name(black)):
        return []
    return [(white, match.group("white_elo") or ""), (black, match.group("black_elo") or "")]


def _clean_player_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" ,;-")
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r",(?=\S)", ", ", cleaned)
    return _clean_header_value(cleaned)


def _looks_like_player_name(value: str) -> bool:
    sample = value or ""
    if len(sample) < 3 or len(sample) > 90:
        return False
    if re.search(r"\b(?:variation|attack|opening|defen[cs]e|gambit|system|lines)\b", sample, re.IGNORECASE):
        return False
    return bool("," in sample or re.search(r"\b[A-Z][a-z]{2,}\b", sample))


def _title_from_headers(headers: Mapping[str, str], *, fallback: str) -> str:
    white = str(headers.get("White") or "").strip()
    black = str(headers.get("Black") or "").strip()
    event = str(headers.get("Event") or "").strip()
    if white and black and white != "?" and black != "?":
        base = f"{white} - {black}"
        if event and event != "?":
            return f"{base}, {event}"[:120]
        return base[:120]
    return fallback


def _clean_header_value(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" -,_")
    return cleaned or "?"


def _pgn_confidence(*, halfmove_count: int, ocr_confidence: float, has_players: bool, warnings: list[str]) -> float:
    score = 0.45 + min(0.30, halfmove_count * 0.035)
    score += max(0.0, min(float(ocr_confidence or 0.0), 1.0)) * 0.18
    if has_players:
        score += 0.08
    if "move_number_inferred" in warnings:
        score -= 0.15
    if "no_explicit_move_number" in warnings:
        score -= 0.20
    return max(0.0, min(score, 1.0))


def _blocking_pgn_warnings(warnings: Iterable[str]) -> bool:
    warning_set = set(warnings)
    return bool(
        warning_set
        & {
            "invalid_move_number_zero",
            "move_number_regression",
            "move_number_jump",
            "side_to_move_mismatch",
            "no_explicit_move_number",
            "python_chess_unavailable",
            "pgn_parse_failed",
            "pgn_replay_failed",
            "pgn_replay_errors",
            "pgn_no_legal_moves",
            UNMAPPED_CHESS_GLYPH_WARNING,
        }
    )


def _is_exportable_pgn_record(record: ChessPgnRecord) -> bool:
    if _detect_unmapped_pgn_glyphs(record.raw_text):
        return False
    if _detect_unmapped_pgn_glyphs(record.movetext):
        return False
    if _detect_unmapped_pgn_glyphs(record.pgn):
        return False
    if _detect_unmapped_pgn_glyphs(record.annotated_pgn):
        return False
    return bool(
        record.pgn.strip()
        and record.status == "accepted"
        and record.final_fen
        and not _blocking_pgn_warnings(record.warnings)
    )


def _record_export_pgn(record: ChessPgnRecord) -> str:
    if any(
        _detect_unmapped_pgn_glyphs(text)
        for text in (record.raw_text, record.movetext, record.pgn, record.annotated_pgn)
    ):
        return ""
    annotated = str(getattr(record, "annotated_pgn", "") or "").strip()
    if annotated and _pgn_parse_clean(annotated):
        return annotated
    return record.pgn


def _pgn_parse_clean(pgn_text: str) -> bool:
    try:
        import chess.pgn  # type: ignore[import-not-found]
    except Exception:
        return False
    try:
        pgn_logger = logging.getLogger("chess.pgn")
        was_disabled = pgn_logger.disabled
        pgn_logger.disabled = True
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text or ""))
        finally:
            pgn_logger.disabled = was_disabled
    except Exception:
        return False
    return bool(game is not None and not getattr(game, "errors", None))


def _replay_record_to_final_fen(record: ChessPgnRecord) -> dict[str, Any]:
    try:
        import chess.pgn  # type: ignore[import-not-found]
    except Exception:
        return {"final_fen": "", "fen_snapshots": [], "warnings": ["python_chess_unavailable"]}

    try:
        pgn_logger = logging.getLogger("chess.pgn")
        was_disabled = pgn_logger.disabled
        pgn_logger.disabled = True
        try:
            game = chess.pgn.read_game(io.StringIO(record.pgn or ""))
        finally:
            pgn_logger.disabled = was_disabled
    except Exception:
        return {"final_fen": "", "fen_snapshots": [], "warnings": ["pgn_parse_failed"]}
    if game is None:
        return {"final_fen": "", "fen_snapshots": [], "warnings": ["pgn_parse_failed"]}

    warnings: list[str] = []
    if getattr(game, "errors", None):
        return {"final_fen": "", "fen_snapshots": [], "warnings": ["pgn_replay_errors"]}
    try:
        board = game.board()
        halfmove_index = 0
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                warnings.append("pgn_replay_failed")
                return {"final_fen": "", "fen_snapshots": [], "warnings": sorted(set(warnings))}
            board.push(move)
            halfmove_index += 1
    except Exception:
        return {"final_fen": "", "fen_snapshots": [], "warnings": ["pgn_replay_failed"]}

    if halfmove_index <= 0:
        warnings.append("pgn_no_legal_moves")
        return {"final_fen": "", "fen_snapshots": [], "warnings": sorted(set(warnings))}

    final_fen = board.fen()
    return {
        "final_fen": final_fen,
        "fen_snapshots": [{"label": "final", "ply": halfmove_index, "fen": final_fen}],
        "warnings": sorted(set(warnings)),
    }


def _record_fen_html(record: ChessPgnRecord) -> str:
    rows: list[str] = []
    if record.fen:
        rows.append(
            '<p class="diagram-fen">'
            '<span class="diagram-fen-label">Initial FEN:</span> '
            f'<code class="diagram-fen-code">{html.escape(record.fen)}</code>'
            "</p>"
        )
    if record.final_fen:
        rows.append(
            '<p class="diagram-fen">'
            '<span class="diagram-fen-label">Final FEN:</span> '
            f'<code class="diagram-fen-code">{html.escape(record.final_fen)}</code>'
            "</p>"
        )
    for snapshot in record.fen_snapshots:
        label = str(snapshot.get("label") or "").strip()
        fen = str(snapshot.get("fen") or "").strip()
        if not fen or label == "final":
            continue
        rows.append(
            '<p class="diagram-fen">'
            f'<span class="diagram-fen-label">{html.escape(label)} FEN:</span> '
            f'<code class="diagram-fen-code">{html.escape(fen)}</code>'
            "</p>"
        )
    return "".join(rows)


def _record_download_html(record: ChessPgnRecord) -> str:
    exportable = _is_exportable_pgn_record(record)
    safe_title = html.escape(record.title or record.id)
    safe_section_id = html.escape(record.id, quote=True)
    safe_pgn_id = html.escape(f"{record.id}-pgn", quote=True)
    safe_full_id = html.escape(f"{record.id}-full-notation", quote=True)
    status_label = "Legalny PGN/FEN" if exportable else "Do weryfikacji"
    status_markup = f'<p class="chess-pgn-status">{status_label}</p>'
    fen_markup = _record_fen_html(record) if exportable else ""
    pgn_markup = ""
    if exportable:
        export_pgn = _record_export_pgn(record)
        pgn_markup = (
            '<div class="chess-pgn-mainline">'
            f'<button type="button" class="copy-pgn-button" data-copy-target="{safe_pgn_id}">Kopiuj PGN</button>'
            f'<pre id="{safe_pgn_id}" class="chess-pgn-text"><code>{html.escape(export_pgn)}</code></pre>'
            "</div>"
        )
    else:
        diagnostic_count = len(_bounded_glyph_diagnostics(record.glyph_diagnostics or []))
        diagnostic_markup = (
            f'<p class="chess-glyph-audit-note">Glyph diagnostics available in audit: {diagnostic_count} span(s).</p>'
            if diagnostic_count
            else ""
        )
        pgn_markup = (
            f'<p class="chess-review-reason">Nie eksportuję strict PGN/FEN automatycznie: '
            f"{html.escape(_record_review_reason(record.warnings))}</p>"
            f"{diagnostic_markup}"
        )
    full_notation = _record_full_notation_text(record)
    full_markup = (
        '<div class="chess-full-notation">'
        "<h3>Pełna notacja z książki</h3>"
        f'<button type="button" class="copy-pgn-button" data-copy-target="{safe_full_id}">Kopiuj pełną notację</button>'
        f'<pre id="{safe_full_id}" class="chess-full-notation-text"><code>{html.escape(full_notation)}</code></pre>'
        "</div>"
    )
    section_class = "chess-pgn-game chess-pgn-legal" if exportable else "chess-pgn-game chess-pgn-needs-review"
    return (
        f'<section class="{section_class}" id="{safe_section_id}">'
        f"<h2>{safe_title}</h2>"
        f"{status_markup}"
        f"{full_markup}"
        f"{fen_markup}"
        f"{pgn_markup}"
        "</section>"
    )


def _record_full_notation_text(record: ChessPgnRecord) -> str:
    for candidate in (record.annotated_pgn, record.pgn, record.movetext):
        cleaned = str(candidate or "").strip()
        if cleaned:
            if _detect_unmapped_pgn_glyphs(cleaned) or _detect_unmapped_pgn_glyphs(record.raw_text):
                return "Notacja wymaga weryfikacji: wykryto nierozpoznany glyph."
            return cleaned
    return "Brak rozpoznanej notacji."


def _copy_pgn_script() -> str:
    return (
        "<script>"
        "(function(){"
        "async function copyText(text){"
        "if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);return;}"
        "var area=document.createElement('textarea');area.value=text;area.setAttribute('readonly','');"
        "area.style.position='fixed';area.style.left='-9999px';document.body.appendChild(area);"
        "area.select();document.execCommand('copy');document.body.removeChild(area);"
        "}"
        "document.addEventListener('click',function(event){"
        "var button=event.target.closest&&event.target.closest('[data-copy-target]');"
        "if(!button)return;"
        "var target=document.getElementById(button.getAttribute('data-copy-target'));"
        "if(!target)return;"
        "var original=button.textContent;"
        "copyText(target.innerText||target.textContent||'').then(function(){"
        "button.textContent='Skopiowano';setTimeout(function(){button.textContent=original;},1400);"
        "}).catch(function(){button.textContent='Nie skopiowano';setTimeout(function(){button.textContent=original;},1800);});"
        "});"
        "})();"
        "</script>"
    )


def _record_review_html(record: ChessPgnRecord) -> str:
    safe_title = html.escape(record.title or record.id)
    safe_id = html.escape(record.id, quote=True)
    status = html.escape(record.status or "requires_review", quote=True)
    confidence = float(record.confidence or 0.0)
    warnings = list(record.warnings or [])
    reason = html.escape(_record_review_reason(warnings))
    warning_markup = ""
    if warnings:
        items = "".join(f"<li>{html.escape(warning)}</li>" for warning in warnings[:12])
        warning_markup = f'<ul class="chess-pgn-review-warnings">{items}</ul>'
    diagnostic_count = len(_bounded_glyph_diagnostics(record.glyph_diagnostics or []))
    diagnostic_markup = (
        f'<p class="chess-glyph-audit-note">Glyph diagnostics available in audit: {diagnostic_count} span(s).</p>'
        if diagnostic_count
        else ""
    )
    raw = _record_review_display_text(record)
    return (
        '<section class="chess-pgn-review" '
        f'id="{safe_id}" data-pgn-status="{status}" data-pgn-confidence="{confidence:.3f}">'
        f'<p class="chess-pgn-review-title"><strong>PGN do weryfikacji: {safe_title}</strong></p>'
        f'<p class="chess-pgn-review-note">Nie publikuje PGN/FEN automatycznie: {reason}</p>'
        f"{warning_markup}"
        f"{diagnostic_markup}"
        f'<pre class="chess-pgn-review-text"><code>{html.escape(raw)}</code></pre>'
        "</section>"
    )


def _record_review_reason(warnings: Iterable[str]) -> str:
    warning_set = set(warnings or [])
    if UNMAPPED_CHESS_GLYPH_WARNING in warning_set:
        return "wykryto nierozpoznany glyph albo token mojibake w zrodle PGN."
    if "invalid_move_number_zero" in warning_set:
        return "wystepuje niedozwolony numer ruchu 0."
    if "move_number_regression" in warning_set:
        return "numeracja ruchow cofa sie, prawdopodobnie wklejono wariant bez nawiasow PGN."
    if "move_number_jump" in warning_set:
        return "numeracja ruchow przeskakuje bez kompletnej sekwencji."
    if "side_to_move_mismatch" in warning_set:
        return "marker ruchu wskazuje niewlasciwa strone do ruchu."
    if "pgn_replay_errors" in warning_set:
        return "legalny replay PGN zglosil blad."
    if "pgn_replay_failed" in warning_set:
        return "nie udalo sie legalnie odtworzyc partii."
    if "python_chess_unavailable" in warning_set:
        return "brak lokalnej biblioteki python-chess do walidacji."
    return "confidence jest ponizej progu albo rekord wymaga recznej kontroli."


def _record_review_display_text(record: ChessPgnRecord) -> str:
    raw = (record.raw_text or record.movetext or "").strip()
    if not raw:
        return "Brak surowego fragmentu notacji."
    if _detect_unmapped_pgn_glyphs(raw):
        return "Notacja wymaga weryfikacji: wykryto nierozpoznany glyph. Surowy tekst pozostaje w raporcie audytowym."
    return raw


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
