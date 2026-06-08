from __future__ import annotations

import base64
import html
import io
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping


RESULT_VALUES = {"1-0", "0-1", "1/2-1/2", "0.5-0.5", "*"}
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
        }


@dataclass(frozen=True)
class ChessExerciseRecord:
    id: str
    source_pages: list[int]
    diagram_number: str
    caption: str
    diagram_records: list[dict[str, Any]]
    fen_candidate: str
    fen_confidence: float
    raw_ocr_text: str
    candidate_lines: list[dict[str, Any]] = field(default_factory=list)
    legal_line_candidates: list[dict[str, Any]] = field(default_factory=list)
    status: str = "requires_review"
    warnings: list[str] = field(default_factory=list)
    quality_flags: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_pages": list(self.source_pages),
            "diagram_number": self.diagram_number,
            "caption": self.caption,
            "diagram_records": [dict(record) for record in self.diagram_records],
            "fen_candidate": self.fen_candidate,
            "fen_confidence": round(float(self.fen_confidence or 0.0), 3),
            "raw_ocr_text": self.raw_ocr_text,
            "candidate_lines": [dict(line) for line in self.candidate_lines],
            "legal_line_candidates": [dict(line) for line in self.legal_line_candidates],
            "status": self.status,
            "warnings": list(self.warnings),
            "quality_flags": dict(self.quality_flags),
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
) -> list[ChessPgnRecord]:
    normalized = normalize_ocr_text_for_pgn(text)
    if not normalized.strip():
        return []

    candidates = _split_candidate_game_blocks(normalized)
    fen_list = [str(fen or "").strip() for fen in (fen_candidates or []) if str(fen or "").strip()]
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
        updated.append(replace(record, headers=headers, pgn=pgn, annotated_pgn=annotated_pgn, fen=fen_list[index]))
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
        status = record.status
        confidence = float(record.confidence or 0.0)
        if _blocking_pgn_warnings(warnings):
            status = "requires_review"
            confidence = min(confidence, 0.64)
        if replay["final_fen"]:
            confidence = min(1.0, max(confidence, confidence + 0.08))
            if confidence >= 0.72 and not _blocking_pgn_warnings(warnings):
                status = "accepted"
        else:
            status = "requires_review"
            confidence = min(confidence, 0.64)
        updated.append(
            replace(
                record,
                final_fen=replay["final_fen"],
                fen_snapshots=replay["fen_snapshots"],
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
    candidate = replace(
        previous,
        source_pages=sorted(set([*previous.source_pages, *continuation.source_pages])),
        headers=headers,
        movetext=combined_movetext,
        pgn=_format_pgn(headers, combined_movetext, result),
        annotated_pgn=_format_annotated_pgn(
            headers,
            raw_text,
            fallback_movetext=combined_movetext,
            result=result,
        ),
        result=result,
        raw_text=raw_text,
        warnings=[],
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


def _diagram_image_data_uri(record: Mapping[str, Any]) -> str:
    raw_data = record.get("image_data")
    if raw_data is None:
        raw_data = record.get("data")
    if isinstance(raw_data, str):
        raw_data = raw_data.encode("latin1", errors="ignore")
    if not isinstance(raw_data, (bytes, bytearray)) or not raw_data:
        return ""
    extension = str(record.get("extension") or "png").strip().lower()
    content_type = "image/jpeg" if extension in {"jpg", "jpeg"} else "image/png"
    encoded = base64.b64encode(bytes(raw_data)).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _diagram_record_html(record: Mapping[str, Any], index: int) -> str:
    fen = str(record.get("fen") or "").strip()
    confidence = float(record.get("confidence", record.get("fen_confidence", 0.0)) or 0.0)
    requires_review = bool(record.get("requires_review", not fen))
    status_label = "FEN do weryfikacji" if requires_review or not fen else "FEN zaakceptowany"
    status_class = "fen-review" if requires_review or not fen else "fen-accepted"
    page = str(record.get("page") or "").strip()
    method = str(record.get("method") or record.get("fen_method") or "").strip()
    selected_variant = str(record.get("selected_preprocess_variant") or "").strip()
    display_variant = str(record.get("display_variant_used") or "").strip()
    filename = str(record.get("filename") or f"diagram-{index}").strip()
    safe_img_src = html.escape(_diagram_image_data_uri(record), quote=True)
    image_markup = (
        f'<img class="chess-diagram-thumb" src="{safe_img_src}" alt="Diagram szachowy {index}"/>'
        if safe_img_src
        else '<p class="chess-diagram-missing-image">Brak miniatury diagramu.</p>'
    )
    fen_id = html.escape(f"diagram-fen-{index}", quote=True)
    if fen:
        fen_markup = (
            '<p class="diagram-fen">'
            '<span class="diagram-fen-label">FEN:</span> '
            f'<code id="{fen_id}" class="diagram-fen-code">{html.escape(fen)}</code>'
            f' <button type="button" class="copy-pgn-button" data-copy-target="{fen_id}">Kopiuj FEN</button>'
            "</p>"
        )
    else:
        fen_markup = (
            '<p class="diagram-fen diagram-review" data-fen-status="requires-review">'
            "FEN do weryfikacji - brak deterministycznej pewnosci figur."
            "</p>"
        )
    warnings = [str(warning) for warning in (record.get("warnings") or []) if str(warning).strip()]
    warning_markup = ""
    if warnings:
        items = "".join(f"<li>{html.escape(warning)}</li>" for warning in warnings[:10])
        warning_markup = f'<ul class="diagram-fen-warnings">{items}</ul>'
    meta_bits = []
    if page:
        meta_bits.append(f"strona {html.escape(page)}")
    if method:
        meta_bits.append(html.escape(method))
    if selected_variant:
        meta_bits.append(f"recognition {html.escape(selected_variant)}")
    if display_variant:
        meta_bits.append(f"display {html.escape(display_variant)}")
    meta_bits.append(f"confidence {confidence:.3f}")
    meta = " | ".join(meta_bits)
    return (
        f'<section class="chess-diagram-fen-record {status_class}" '
        f'data-fen-status="{html.escape(status_label, quote=True)}" data-fen-confidence="{confidence:.3f}">'
        f"<h3>{html.escape(filename)}</h3>"
        f'<p class="chess-diagram-fen-status">{status_label}</p>'
        f'<p class="chess-diagram-fen-meta">{meta}</p>'
        f"{image_markup}"
        f"{fen_markup}"
        f"{warning_markup}"
        "</section>"
    )


def _diagram_records_html(diagram_records: Iterable[Mapping[str, Any]]) -> tuple[list[str], dict[str, int]]:
    records = [dict(record) for record in diagram_records]
    fen_count = len([record for record in records if str(record.get("fen") or "").strip()])
    review_count = len([record for record in records if bool(record.get("requires_review", not record.get("fen")))])
    parts = [
        "<h2>Detected chess diagrams / FEN</h2>",
        f"<p>Detected diagram records: {len(records)}</p>",
        f"<p>Diagram FEN records: {fen_count}</p>",
        f"<p>Diagram FEN review records: {review_count}</p>",
    ]
    if not records:
        parts.append("<p>No scanned chess diagrams were attached to this HTML artifact.</p>")
    for index, record in enumerate(records, start=1):
        parts.append(_diagram_record_html(record, index))
    return parts, {"diagram_count": len(records), "diagram_fen_count": fen_count, "diagram_review_count": review_count}


def _diagram_number_from_text(text: str) -> str:
    match = re.search(r"(?i)\bdiagram\s+(\d+(?:[-.]\d+)?)", str(text or ""))
    return match.group(1).strip() if match else ""


def _record_diagram_number(record: ChessPgnRecord) -> str:
    return (
        _diagram_number_from_text(record.title)
        or _diagram_number_from_text(record.raw_text)
        or _diagram_number_from_text(record.movetext)
    )


def _candidate_solution_segments(text: str) -> list[str]:
    prepared = str(text or "").strip()
    if not prepared:
        return []
    prepared = re.sub(r"(?i)\b(?:if|or|due to|in view of|on account of|threatening|black resigned|white resigned)\b", "\n", prepared)
    prepared = re.sub(r"[\[\](){}]", "\n", prepared)
    prepared = re.sub(r"(?m)(?<!^)(?=\s*\d{1,3}\.\.?\s*)", "\n", prepared)
    segments = []
    for raw_segment in prepared.splitlines():
        segment = raw_segment.strip()
        if len(segment) < 4:
            continue
        if not re.search(r"\d{1,3}\.\.?\s*|[KQRBNP]?[a-h]?[1-8]?x?[a-h][1-8]|O-O|0-0", segment, re.IGNORECASE):
            continue
        segments.append(segment)
    return segments[:12]


def _validate_candidate_line_from_fen(fen: str, movetext: str) -> dict[str, Any]:
    if not fen or not movetext:
        return {"legal": False, "final_fen": "", "warnings": ["missing_fen_or_movetext"]}
    try:
        import chess.pgn  # type: ignore[import-not-found]
    except Exception:
        return {"legal": False, "final_fen": "", "warnings": ["python_chess_unavailable"]}
    pgn_text = "\n".join(
        [
            '[Event "Exercise candidate"]',
            '[SetUp "1"]',
            f'[FEN "{fen}"]',
            "",
            movetext,
        ]
    )
    try:
        pgn_logger = logging.getLogger("chess.pgn")
        was_disabled = pgn_logger.disabled
        pgn_logger.disabled = True
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
        finally:
            pgn_logger.disabled = was_disabled
    except Exception:
        return {"legal": False, "final_fen": "", "warnings": ["candidate_pgn_parse_failed"]}
    if game is None or getattr(game, "errors", None):
        return {"legal": False, "final_fen": "", "warnings": ["candidate_pgn_replay_errors"]}
    try:
        board = game.board()
        ply = 0
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                return {"legal": False, "final_fen": "", "warnings": ["candidate_illegal_move"]}
            board.push(move)
            ply += 1
    except Exception:
        return {"legal": False, "final_fen": "", "warnings": ["candidate_pgn_replay_failed"]}
    if ply <= 0:
        return {"legal": False, "final_fen": "", "warnings": ["candidate_no_legal_moves"]}
    return {"legal": True, "final_fen": board.fen(), "warnings": []}


def _exercise_candidate_lines(raw_text: str, fen: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for segment in _candidate_solution_segments(raw_text):
        tokens = _extract_pgn_tokens(segment)
        movetext, result, halfmove_count, warnings = _tokens_to_movetext(tokens)
        if halfmove_count <= 0:
            continue
        validation = _validate_candidate_line_from_fen(fen, movetext)
        line_warnings = sorted(set([*warnings, *validation.get("warnings", [])]))
        lines.append(
            {
                "source_text": segment,
                "movetext": movetext,
                "result": result,
                "halfmove_count": halfmove_count,
                "legal_from_fen": bool(validation.get("legal")),
                "final_fen": str(validation.get("final_fen") or ""),
                "warnings": line_warnings,
            }
        )
    return lines[:8]


def _ocr_noise_score(text: str) -> float:
    sample = str(text or "")
    if not sample:
        return 0.0
    noisy_chars = sum(1 for char in sample if char in {"�", "@", "¢", "†", "‡", "\\", "|", "¬"})
    suspicious_tokens = len(re.findall(r"(?i)\b(?:liJ|LJ|gg\d|[a-z]\d+t|[0-9][a-z]{2,})\b", sample))
    score = (noisy_chars * 2 + suspicious_tokens) / max(1, len(sample.split()))
    return round(min(score, 1.0), 3)


def _build_exercise_record(
    record: ChessPgnRecord,
    diagram_records: Iterable[Mapping[str, Any]],
    *,
    record_source_order: int | None = None,
) -> ChessExerciseRecord:
    diagram_record_list = [dict(diagram) for diagram in diagram_records]
    matched_diagrams = [
        dict(diagram)
        for diagram in _matching_diagram_records_for_record(
            record,
            diagram_record_list,
            record_source_order=record_source_order,
        )
    ]
    fen = str(record.fen or "").strip()
    if not fen:
        fen = next((str(diagram.get("fen") or "").strip() for diagram in matched_diagrams if str(diagram.get("fen") or "").strip()), "")
    fen_confidence = 0.0
    for diagram in matched_diagrams:
        if str(diagram.get("fen") or "").strip() == fen:
            fen_confidence = float(diagram.get("confidence", diagram.get("fen_confidence", 0.0)) or 0.0)
            break
    match_scores = [
        _diagram_match_score(record, diagram, record_source_order=record_source_order)
        for diagram in matched_diagrams
    ]
    diagram_match_score = max((score for score, _fen_score in match_scores), default=0)
    fen_match_score = max((fen_score for _score, fen_score in match_scores), default=0)
    missing_diagram_reason = ""
    if not matched_diagrams:
        missing_diagram_reason = "no_diagram_candidates" if not diagram_record_list else "no_number_page_fen_or_order_match"
    raw_text = _record_full_notation_text(record)
    candidate_lines = _exercise_candidate_lines(raw_text, fen)
    legal_lines = [line for line in candidate_lines if line.get("legal_from_fen")]
    quality_flags = {
        "diagram_visible": bool(matched_diagrams),
        "fen_available": bool(fen),
        "missing_diagram_reason": missing_diagram_reason,
        "diagram_candidate_count": len(diagram_record_list),
        "diagram_match_score": diagram_match_score,
        "fen_match_score": fen_match_score,
        "solution_line_count": len(candidate_lines),
        "legal_solution_line_count": len(legal_lines),
        "ocr_noise_score": _ocr_noise_score(raw_text),
        "exportable_pgn": _is_exportable_pgn_record(record),
    }
    warnings = sorted(
        set(
            [
                *record.warnings,
                *[warning for line in candidate_lines for warning in (line.get("warnings") or [])],
            ]
        )
    )
    status = "legal_pgn_verified" if quality_flags["exportable_pgn"] else "requires_review"
    if legal_lines and not quality_flags["exportable_pgn"]:
        status = "candidate_solution_verified"
    return ChessExerciseRecord(
        id=record.id,
        source_pages=list(record.source_pages or []),
        diagram_number=_record_diagram_number(record),
        caption=record.title,
        diagram_records=matched_diagrams,
        fen_candidate=fen,
        fen_confidence=fen_confidence,
        raw_ocr_text=raw_text,
        candidate_lines=candidate_lines,
        legal_line_candidates=legal_lines,
        status=status,
        warnings=warnings,
        quality_flags=quality_flags,
    )


def build_pgn_download_html(
    records: Iterable[ChessPgnRecord],
    *,
    title: str = "Chess PGN",
    diagram_records: Iterable[Mapping[str, Any]] | None = None,
) -> str:
    record_list = list(records)
    diagram_list = list(diagram_records or [])
    diagram_parts, diagram_summary = _diagram_records_html(diagram_list)
    accepted_records = [record for record in record_list if _is_exportable_pgn_record(record)]
    review_records = [record for record in record_list if not _is_exportable_pgn_record(record)]
    full_notation_records = [record for record in record_list if (record.raw_text or record.movetext or "").strip()]
    body = [
        f"<p>Accepted PGN records: {len(accepted_records)}</p>",
        f"<p>Manual review records: {len(review_records)}</p>",
        f"<p>Full notation records: {len(full_notation_records)}</p>",
        f"<p>Detected chess diagrams: {diagram_summary['diagram_count']}</p>",
        f"<p>Detected diagram FEN: {diagram_summary['diagram_fen_count']}</p>",
        f"<p>Diagram FEN review records: {diagram_summary['diagram_review_count']}</p>",
        "<p>HTML order: source PDF order preserved.</p>",
        *diagram_parts,
        "<h2>Games in source order</h2>",
    ]
    if not record_list:
        body.append("<p>No PGN-like records were detected.</p>")
    for record_source_order, record in enumerate(record_list):
        body.append(
            _record_download_html(
                record,
                diagram_records=diagram_list,
                record_source_order=record_source_order,
            )
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:Georgia,serif;line-height:1.5;margin:2rem;color:#141414;}"
        "section{margin:1.4rem 0 2rem}pre{white-space:pre-wrap;background:#f6f1e8;color:#141414;padding:1rem;border-radius:12px;}"
        ".chess-pgn-game{border-top:1px solid #d8c8b1;padding-top:1rem}.chess-pgn-status{display:inline-block;"
        "border-radius:999px;padding:.25rem .6rem;font-weight:800;font-size:.82rem;margin:.2rem 0 .6rem}"
        ".chess-pgn-legal .chess-pgn-status{background:#e3f7ea;color:#096b31}.chess-pgn-needs-review .chess-pgn-status{background:#fff0d8;color:#8a4b00}"
        ".chess-diagram-fen-record{border:1px solid #d8c8b1;border-radius:16px;padding:1rem;background:#fffaf2}"
        ".chess-diagram-thumb{display:block;max-width:260px;width:100%;height:auto;border-radius:10px;border:1px solid #c9b89f;background:#fff;margin:.7rem 0}"
        ".chess-diagram-fen-status{display:inline-block;border-radius:999px;padding:.25rem .6rem;font-weight:800;font-size:.82rem}"
        ".fen-accepted .chess-diagram-fen-status{background:#e3f7ea;color:#096b31}.fen-review .chess-diagram-fen-status{background:#fff0d8;color:#8a4b00}"
        ".chess-diagram-fen-meta{color:#725b3f}.diagram-fen-warnings{color:#725b3f}"
        ".exercise-quality-flags{display:flex;flex-wrap:wrap;gap:.45rem;list-style:none;padding:0;margin:.5rem 0 1rem}"
        ".exercise-quality-flags li{border:1px solid #d8c8b1;border-radius:999px;padding:.25rem .55rem;background:#fffaf2;font-size:.82rem}"
        ".exercise-quality-key{font-weight:800;color:#725b3f}.exercise-candidate-lines{margin:1rem 0;padding:1rem;border-left:4px solid #c9b89f;background:#fffaf2;border-radius:12px}"
        ".exercise-candidate-line{margin:.8rem 0;padding:.75rem;border-radius:10px;border:1px solid #d8c8b1}.candidate-line-legal{background:#f1fbf4}.candidate-line-review{background:#fff7e8}"
        ".candidate-line-warnings{color:#8a4b00;font-weight:700}.candidate-line-text{margin:.4rem 0}"
        ".diagram-fen{margin:.6rem 0;color:#141414}.diagram-fen code{font-family:monospace}"
        ".copy-pgn-button{border:1px solid #c9b89f;border-radius:999px;background:#fff8ed;color:#141414;"
        "font-weight:700;padding:.45rem .8rem;cursor:pointer;margin:.4rem 0 .65rem}"
        ".chess-full-notation h3{margin-bottom:.2rem}.chess-review-reason{color:#725b3f;font-weight:700}"
        ".copy-pgn-button:focus{outline:2px solid #b85c24;outline-offset:2px}</style>"
        "</head><body>"
        f"<h1>{html.escape(title)}</h1>"
        + "\n".join(body)
        + _copy_pgn_script()
        + "</body></html>\n"
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
        }
    )


def _is_exportable_pgn_record(record: ChessPgnRecord) -> bool:
    return bool(
        record.pgn.strip()
        and record.status == "accepted"
        and record.final_fen
        and not _blocking_pgn_warnings(record.warnings)
    )


def _record_export_pgn(record: ChessPgnRecord) -> str:
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


def _matching_diagram_records_for_record(
    record: ChessPgnRecord,
    diagram_records: Iterable[Mapping[str, Any]],
    *,
    record_source_order: int | None = None,
) -> list[Mapping[str, Any]]:
    ranked: list[tuple[int, int, Mapping[str, Any]]] = []
    for source_order, diagram in enumerate(diagram_records):
        score, _fen_score = _diagram_match_score(
            record,
            diagram,
            record_source_order=record_source_order,
            fallback_source_order=source_order,
        )
        if score:
            ranked.append((score, -source_order, diagram))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [diagram for _, _, diagram in ranked[:3]]


def _diagram_match_score(
    record: ChessPgnRecord,
    diagram: Mapping[str, Any],
    *,
    record_source_order: int | None = None,
    fallback_source_order: int | None = None,
) -> tuple[int, int]:
    record_fen = str(record.fen or "").strip()
    record_diagram_number = _record_diagram_number(record)
    record_pages = {_safe_int(page) for page in (record.source_pages or [])}
    diagram_fen = str(diagram.get("fen") or "").strip()
    diagram_number = str(diagram.get("diagram_number") or "").strip()
    diagram_page = _safe_int(diagram.get("page"))
    score = 0
    fen_score = 0
    if record_diagram_number and diagram_number and record_diagram_number == diagram_number:
        score += 100
    if diagram_page in record_pages:
        score += 40
    if record_fen and diagram_fen == record_fen:
        score += 30
        fen_score += 30
    if record_source_order is not None:
        raw_source_order = diagram.get("source_order")
        if raw_source_order is not None and str(raw_source_order).strip() != "":
            diagram_source_order = _safe_int(raw_source_order)
        elif fallback_source_order is not None:
            diagram_source_order = int(fallback_source_order)
        else:
            diagram_source_order = -1
        if diagram_source_order >= 0:
            distance = abs(int(record_source_order) - diagram_source_order)
            if distance == 0:
                score += 55
            elif distance == 1:
                score += 18
    return score, fen_score


def _exercise_quality_flags_html(exercise: ChessExerciseRecord) -> str:
    flags = exercise.quality_flags
    rows = [
        ("diagram_visible", "yes" if flags.get("diagram_visible") else "no"),
        ("fen_available", "yes" if flags.get("fen_available") else "no"),
        ("missing_diagram_reason", str(flags.get("missing_diagram_reason") or "")),
        ("diagram_candidate_count", str(flags.get("diagram_candidate_count", 0))),
        ("diagram_match_score", str(flags.get("diagram_match_score", 0))),
        ("fen_match_score", str(flags.get("fen_match_score", 0))),
        ("solution_line_count", str(flags.get("solution_line_count", 0))),
        ("legal_solution_line_count", str(flags.get("legal_solution_line_count", 0))),
        ("ocr_noise_score", f"{float(flags.get('ocr_noise_score', 0.0) or 0.0):.3f}"),
        ("exportable_pgn", "yes" if flags.get("exportable_pgn") else "no"),
    ]
    items = "".join(
        f'<li><span class="exercise-quality-key">{html.escape(key)}:</span> '
        f'<span class="exercise-quality-value">{html.escape(value)}</span></li>'
        for key, value in rows
    )
    return f'<ul class="exercise-quality-flags">{items}</ul>'


def _exercise_candidate_lines_html(exercise: ChessExerciseRecord) -> str:
    if not exercise.candidate_lines:
        return (
            '<div class="exercise-candidate-lines">'
            "<h3>Candidate solution lines</h3>"
            "<p>Nie znaleziono kandydackiej linii rozwiązania do legalnej walidacji.</p>"
            "</div>"
        )
    rows = []
    for index, line in enumerate(exercise.candidate_lines, start=1):
        legal = bool(line.get("legal_from_fen"))
        status = "legal from diagram FEN" if legal else "requires review"
        status_class = "candidate-line-legal" if legal else "candidate-line-review"
        warnings = [str(warning) for warning in (line.get("warnings") or []) if str(warning).strip()]
        warning_markup = ""
        if warnings:
            warning_markup = '<p class="candidate-line-warnings">' + html.escape(", ".join(warnings[:8])) + "</p>"
        final_fen = str(line.get("final_fen") or "").strip()
        final_fen_markup = (
            f'<p class="diagram-fen"><span class="diagram-fen-label">Final FEN:</span> '
            f'<code class="diagram-fen-code">{html.escape(final_fen)}</code></p>'
            if final_fen
            else ""
        )
        rows.append(
            f'<section class="exercise-candidate-line {status_class}">'
            f"<h4>Candidate line {index}: {html.escape(status)}</h4>"
            f'<pre class="candidate-line-text"><code>{html.escape(str(line.get("movetext") or ""))}</code></pre>'
            f"{final_fen_markup}"
            f"{warning_markup}"
            "</section>"
        )
    return (
        '<div class="exercise-candidate-lines">'
        "<h3>Candidate solution lines</h3>"
        + "".join(rows)
        + "</div>"
    )


def _record_download_html(
    record: ChessPgnRecord,
    *,
    diagram_records: Iterable[Mapping[str, Any]] = (),
    record_source_order: int | None = None,
) -> str:
    exportable = _is_exportable_pgn_record(record)
    exercise = _build_exercise_record(record, diagram_records, record_source_order=record_source_order)
    safe_title = html.escape(record.title or record.id)
    safe_section_id = html.escape(record.id, quote=True)
    safe_pgn_id = html.escape(f"{record.id}-pgn", quote=True)
    safe_full_id = html.escape(f"{record.id}-full-notation", quote=True)
    status_label = "Legalny PGN/FEN" if exportable else "Do weryfikacji"
    status_markup = f'<p class="chess-pgn-status">{status_label}</p>'
    fen_markup = _record_fen_html(record) if exportable else ""
    matched_diagram_markup = "".join(
        _diagram_record_html(diagram, index)
        for index, diagram in enumerate(exercise.diagram_records, start=1)
    )
    quality_markup = _exercise_quality_flags_html(exercise)
    candidate_markup = _exercise_candidate_lines_html(exercise)
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
        pgn_markup = (
            f'<p class="chess-review-reason">Nie eksportuję strict PGN/FEN automatycznie: '
            f"{html.escape(_record_review_reason(record.warnings))}</p>"
        )
    full_notation = _record_full_notation_text(record)
    raw_heading = "Pełna notacja z książki" if exportable else "Tekst OCR z książki, wymaga korekty"
    full_markup = (
        '<div class="chess-full-notation">'        f"<h3>{html.escape(raw_heading)}</h3>"
        f'<button type="button" class="copy-pgn-button" data-copy-target="{safe_full_id}">Kopiuj pełną notację</button>'
        f'<pre id="{safe_full_id}" class="chess-full-notation-text"><code>{html.escape(full_notation)}</code></pre>'
        "</div>"
    )
    section_class = "chess-pgn-game chess-pgn-legal" if exportable else "chess-pgn-game chess-pgn-needs-review"
    return (
        f'<section class="{section_class}" id="{safe_section_id}">'
        f"<h2>{safe_title}</h2>"
        f"{status_markup}"
        f"{quality_markup}"
        f"{matched_diagram_markup}"
        f"{full_markup}"
        f"{candidate_markup}"
        f"{fen_markup}"
        f"{pgn_markup}"
        "</section>"
    )


def _record_full_notation_text(record: ChessPgnRecord) -> str:
    if record.status != "accepted":
        candidates = (record.raw_text, record.movetext, record.annotated_pgn, record.pgn)
    else:
        candidates = (record.annotated_pgn, record.pgn, record.movetext, record.raw_text)
    for candidate in candidates:
        cleaned = str(candidate or "").strip()
        if cleaned:
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
    raw = (record.raw_text or record.movetext or "").strip()
    if not raw:
        raw = "Brak surowego fragmentu notacji."
    return (
        '<section class="chess-pgn-review" '
        f'id="{safe_id}" data-pgn-status="{status}" data-pgn-confidence="{confidence:.3f}">'
        f'<p class="chess-pgn-review-title"><strong>PGN do weryfikacji: {safe_title}</strong></p>'
        f'<p class="chess-pgn-review-note">Nie publikuje PGN/FEN automatycznie: {reason}</p>'
        f"{warning_markup}"
        f'<pre class="chess-pgn-review-text"><code>{html.escape(raw)}</code></pre>'
        "</section>"
    )


def _record_review_reason(warnings: Iterable[str]) -> str:
    warning_set = set(warnings or [])
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


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
