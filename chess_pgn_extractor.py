from __future__ import annotations

import html
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
    r"\d{1,3}\.(?:\.\.)?"
    r"|O-O(?:-O)?|0-0(?:-0)?"
    r"|1-0|0-1|1/2-1/2|0\.5-0\.5|\*"
    r"|[KQRBNDAW@&£8]?[a-h]?[1-8]?x?[a-h][1-8](?:\s*=\s*[QRBN])?[+#†‡¢t]?[!?]{0,2}"
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


@dataclass(frozen=True)
class ChessPgnRecord:
    id: str
    source_pages: list[int]
    title: str
    headers: dict[str, str]
    movetext: str
    pgn: str
    result: str = "*"
    confidence: float = 0.0
    status: str = "requires_review"
    warnings: list[str] = field(default_factory=list)
    fen: str = ""
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_pages": list(self.source_pages),
            "title": self.title,
            "headers": dict(self.headers),
            "movetext": self.movetext,
            "pgn": self.pgn,
            "result": self.result,
            "confidence": round(float(self.confidence or 0.0), 3),
            "status": self.status,
            "warnings": list(self.warnings),
            "fen": self.fen,
            "raw_text": self.raw_text,
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
    return records


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
        tokens = _extract_pgn_tokens(candidate["body"])
        movetext, result, halfmove_count, warnings = _tokens_to_movetext(tokens)
        if halfmove_count < 2:
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
        pgn = _format_pgn(headers, movetext, result)
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
                result=result,
                confidence=confidence,
                status=status,
                warnings=warnings,
                fen=fen,
                raw_text=candidate["raw"],
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
        updated.append(replace(record, headers=headers, pgn=pgn, fen=fen_list[index]))
    return updated


def render_chess_pgn_html_parts(
    records: Iterable[ChessPgnRecord],
    *,
    download_href: str = "chess_games.pgn",
) -> list[str]:
    parts: list[str] = []
    for record in records:
        safe_title = html.escape(record.title or record.id)
        safe_pgn = html.escape(record.pgn)
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
            f'<pre class="chess-pgn-text"><code>{safe_pgn}</code></pre>'
            "</section>"
        )
    return parts


def build_combined_pgn(records: Iterable[ChessPgnRecord]) -> str:
    pgn_values = [record.pgn.strip() for record in records if record.pgn.strip()]
    return "\n\n".join(pgn_values).strip() + ("\n" if pgn_values else "")


def build_pgn_download_html(records: Iterable[ChessPgnRecord], *, title: str = "Chess PGN") -> str:
    record_list = list(records)
    body = []
    for record in record_list:
        body.append(
            f"<section><h2>{html.escape(record.title or record.id)}</h2>"
            f"<pre><code>{html.escape(record.pgn)}</code></pre></section>"
        )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:Georgia,serif;line-height:1.5;margin:2rem;}"
        "pre{white-space:pre-wrap;background:#f6f1e8;padding:1rem;border-radius:12px;}</style>"
        "</head><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<p>PGN records: {len(record_list)}</p>"
        + "\n".join(body)
        + "</body></html>\n"
    )


def summarize_chess_pgn_records(records: Iterable[ChessPgnRecord | Mapping[str, Any]]) -> dict[str, Any]:
    rows = [record.to_dict() if isinstance(record, ChessPgnRecord) else dict(record) for record in records]
    candidate_count = len(rows)
    accepted = [row for row in rows if row.get("status") == "accepted" and row.get("pgn")]
    review = [row for row in rows if row.get("status") != "accepted"]
    coverage = (len(accepted) / candidate_count) if candidate_count else 0.0
    return {
        "status": "passed" if coverage >= 0.50 and accepted else ("requires_review" if candidate_count else "not_detected"),
        "candidate_game_count": candidate_count,
        "valid_pgn_count": len(accepted),
        "manual_review_count": len(review),
        "coverage": round(coverage, 4),
        "acceptance_min": 0.50,
        "records": rows[:100],
    }


def normalize_ocr_text_for_pgn(text: str) -> str:
    normalized = str(text or "")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u2013", "-").replace("\u2014", "-")
    normalized = normalized.replace("\u2020", "+").replace("\u2021", "#")
    normalized = normalized.replace("Â˝", "1/2").replace("\u00bd", "1/2")
    normalized = re.sub(r"\b0-0-0\b", "O-O-O", normalized)
    normalized = re.sub(r"\b0-0\b", "O-O", normalized)
    normalized = re.sub(r"=\s+([QRBN])\b", r"=\1", normalized)
    normalized = re.sub(r"\s+([,.;:!?])", r"\1", normalized)
    normalized = re.sub(r"(?<=\d)\s+\.\s+\.\s+\.", "...", normalized)
    normalized = re.sub(r"(?<=\d)\s+\.", ".", normalized)
    return normalized


def _split_candidate_game_blocks(text: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in text.splitlines()]
    starts = [index for index, line in enumerate(lines) if DIAGRAM_LINE_RE.match(line)]
    if not starts:
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


def _line_has_pgn_tokens(line: str) -> bool:
    return bool(NOTATION_HEAVY_RE.search(line) or len(_extract_pgn_tokens(line)) >= 4)


def _extract_pgn_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for line in normalize_ocr_text_for_pgn(text).splitlines() or [normalize_ocr_text_for_pgn(text)]:
        if not _line_has_explicit_movetext(line):
            continue
        for match in TOKEN_SCAN_RE.finditer(line):
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


def _line_has_explicit_movetext(line: str) -> bool:
    sample = str(line or "")
    if RESULT_RE.search(sample.strip()):
        return True
    return bool(re.search(r"\b\d{1,3}\.(?:\.\.)?\s*\S+", sample))


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
    token = re.sub(r"([+#])[+#]+$", r"\1", token)
    if SAN_RE.match(token):
        return token
    return ""


def _tokens_to_movetext(tokens: Iterable[str]) -> tuple[str, str, int, list[str]]:
    fragments: list[str] = []
    warnings: list[str] = []
    current_move: int | None = None
    side = "w"
    result = "*"
    halfmove_count = 0
    saw_move_number = False

    for token in tokens:
        move_match = MOVE_NUMBER_RE.match(token)
        if move_match:
            saw_move_number = True
            current_move = int(move_match.group("num"))
            side = "b" if move_match.group("black") else "w"
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


def _build_headers(*, title: str, caption: str, source_title: str, result: str, fen: str = "") -> dict[str, str]:
    white = "?"
    black = "?"
    site = "?"
    date = "????.??.??"
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
    headers = {
        "Event": _clean_header_value(source_title or title or "KindleMaster OCR"),
        "Site": site,
        "Date": date,
        "Round": "?",
        "White": white,
        "Black": black,
        "Result": result or "*",
    }
    if fen:
        headers["SetUp"] = "1"
        headers["FEN"] = fen
    return headers


def _format_pgn(headers: Mapping[str, str], movetext: str, result: str) -> str:
    ordered = ["Event", "Site", "Date", "Round", "White", "Black", "Result", "SetUp", "FEN"]
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


def _candidate_title(caption: str, *, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", caption or "").strip(" -")
    if not cleaned:
        return fallback
    return cleaned[:120]


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
    return "no_explicit_move_number" in warning_set


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
