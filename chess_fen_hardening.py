from __future__ import annotations

import hashlib
import html
from pathlib import Path
from typing import Any

PIECE_CHARS = set("KQRBNPkqrbnp")
SQUARE_NAMES = [f"{file}{rank}" for rank in range(8, 0, -1) for file in "abcdefgh"]
FEN_SQUARES = SQUARE_NAMES
PIECE_NAMES = {
    "P": "white pawn",
    "N": "white knight",
    "B": "white bishop",
    "R": "white rook",
    "Q": "white queen",
    "K": "white king",
    "p": "black pawn",
    "n": "black knight",
    "b": "black bishop",
    "r": "black rook",
    "q": "black queen",
    "k": "black king",
    "": "empty square",
    "empty": "empty square",
}
MAJOR_PIECES = set("KQRkqr")
MINOR_AND_PAWN_PIECES = set("BNPbnp")
AI_ONLY_VERIFICATION_SOURCES = {
    "ai",
    "ai_assist",
    "ai_candidate",
    "ai_review",
    "ai_review_only",
    "openai",
    "openai_review",
    "gpt",
}
HUMAN_VERIFICATION_SOURCES = {
    "human",
    "human_import",
    "human_manual",
    "human_visual",
    "legacy_human_visual",
}
KNOWN_BAD_EXPECTED_FENS = {
    "p010_d002": "6k1/p4p1p/3p1p2/2p1r3/2PnrqN1/P6P/1P1Q1PP1/3R1RK1 b - - 0 1",
}


def crop_sha256(path: str | Path) -> str:
    crop_path = Path(path)
    digest = hashlib.sha256()
    with crop_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fen_to_cells(fen_or_placement: str) -> list[str]:
    placement = str(fen_or_placement or "").strip().split()[0] if str(fen_or_placement or "").strip() else ""
    ranks = placement.split("/")
    if len(ranks) != 8:
        raise ValueError("FEN placement must have 8 ranks")
    cells: list[str] = []
    for rank in ranks:
        width = 0
        last_was_digit = False
        for char in rank:
            if char.isdigit():
                value = int(char)
                if value < 1 or value > 8 or last_was_digit:
                    raise ValueError("Invalid FEN rank digit")
                cells.extend([""] * value)
                width += value
                last_was_digit = True
            elif char in PIECE_CHARS:
                cells.append(char)
                width += 1
                last_was_digit = False
            else:
                raise ValueError(f"Invalid FEN piece marker: {char!r}")
        if width != 8:
            raise ValueError("FEN rank width must be 8")
    if len(cells) != 64:
        raise ValueError("FEN placement must contain 64 cells")
    return cells


def fen_placement_to_square_map(fen_or_placement: str) -> dict[str, str]:
    return dict(zip(SQUARE_NAMES, fen_to_cells(fen_or_placement)))


def piece_name(piece: str) -> str:
    return PIECE_NAMES.get(str(piece or ""), str(piece or ""))


def compare_fen_placements(candidate_fen: str, expected_fen: str) -> list[dict[str, str]]:
    candidate = fen_placement_to_square_map(candidate_fen)
    expected = fen_placement_to_square_map(expected_fen)
    diffs: list[dict[str, str]] = []
    for square in SQUARE_NAMES:
        candidate_piece = candidate[square]
        manual_piece = expected[square]
        if candidate_piece == manual_piece:
            continue
        diffs.append(
            {
                "square": square,
                "candidate_piece": piece_name(candidate_piece),
                "manual_piece": piece_name(manual_piece),
                "candidate_fen_char": candidate_piece or "empty",
                "manual_fen_char": manual_piece or "empty",
                "severity": _square_diff_severity(candidate_piece, manual_piece),
                "reason": _square_diff_reason(candidate_piece, manual_piece),
                "expected_piece": manual_piece or "empty",
                "actual_piece": candidate_piece or "empty",
            }
        )
    return diffs


def square_level_fen_diff(expected_fen: str, actual_fen: str) -> list[dict[str, str]]:
    return compare_fen_placements(actual_fen, expected_fen)


def compare_fen(candidate_fen: str, expected_fen: str) -> dict[str, Any]:
    placement_diffs: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    try:
        placement_diffs = compare_fen_placements(candidate_fen, expected_fen)
    except ValueError as exc:
        field = "candidate_fen"
        try:
            fen_placement_to_square_map(expected_fen)
        except ValueError:
            field = "expected_fen"
        errors.append({"field": field, "code": "invalid_fen_placement", "message": str(exc)})

    candidate_parts = str(candidate_fen or "").strip().split()
    expected_parts = str(expected_fen or "").strip().split()
    side_to_move_diff = None
    if len(candidate_parts) >= 2 and len(expected_parts) >= 2 and candidate_parts[1] != expected_parts[1]:
        side_to_move_diff = {
            "candidate": candidate_parts[1],
            "manual": expected_parts[1],
            "severity": "low",
            "reason": "side_to_move_mismatch",
        }

    metadata_diffs: list[dict[str, str]] = []
    for index, name in enumerate(("castling", "en_passant", "halfmove_clock", "fullmove_number"), start=2):
        if len(candidate_parts) > index and len(expected_parts) > index and candidate_parts[index] != expected_parts[index]:
            metadata_diffs.append(
                {
                    "field": name,
                    "candidate": candidate_parts[index],
                    "manual": expected_parts[index],
                    "severity": "low",
                    "reason": f"{name}_mismatch",
                }
            )
    return {
        "placement_diffs": placement_diffs,
        "side_to_move_diff": side_to_move_diff,
        "metadata_diffs": metadata_diffs,
        "errors": errors,
    }


def render_square_diff_text(record_id: str, diffs: list[dict[str, str]]) -> list[str]:
    prefix = str(record_id or "record")
    return [
        f"{prefix}: {diff.get('square', '')} "
        f"{diff.get('manual_piece') or piece_name(str(diff.get('expected_piece') or ''))}, "
        f"not {diff.get('candidate_piece') or piece_name(str(diff.get('actual_piece') or ''))}"
        for diff in diffs
    ]


def render_square_diff_json(record_id: str, diffs: list[dict[str, str]]) -> dict[str, Any]:
    return {"id": str(record_id or ""), "diffs": diffs}


def render_square_diff_html(record_id: str, diffs: list[dict[str, str]]) -> str:
    rows: list[str] = []
    for diff in diffs:
        severity = html.escape(str(diff.get("severity") or ""))
        rows.append(
            '<tr class="severity-{severity}">'
            "<td>{square}</td>"
            "<td>{manual_piece} <code>{manual_char}</code></td>"
            "<td>{candidate_piece} <code>{candidate_char}</code></td>"
            "<td>{severity}</td>"
            "<td>{reason}</td>"
            "</tr>".format(
                severity=severity,
                square=html.escape(str(diff.get("square") or "")),
                manual_piece=html.escape(str(diff.get("manual_piece") or piece_name(str(diff.get("expected_piece") or "")))),
                manual_char=html.escape(str(diff.get("manual_fen_char") or diff.get("expected_piece") or "")),
                candidate_piece=html.escape(str(diff.get("candidate_piece") or piece_name(str(diff.get("actual_piece") or "")))),
                candidate_char=html.escape(str(diff.get("candidate_fen_char") or diff.get("actual_piece") or "")),
                reason=html.escape(str(diff.get("reason") or "")),
            )
        )
    if not rows:
        rows.append('<tr class="severity-none"><td colspan="5">No square differences</td></tr>')
    safe_record_id = html.escape(str(record_id or ""), quote=True)
    return (
        f'<section class="fen-square-diff-card" data-record-id="{safe_record_id}">'
        '<table class="fen-square-diff">'
        "<thead><tr><th>Square</th><th>Manual / expected</th><th>Candidate</th><th>Severity</th><th>Reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></section>"
    )


def _square_diff_reason(candidate_piece: str, manual_piece: str) -> str:
    if candidate_piece and manual_piece:
        return "piece_mismatch"
    if manual_piece:
        return "missing_piece"
    return "extra_piece"


def _square_diff_severity(candidate_piece: str, manual_piece: str) -> str:
    if candidate_piece and manual_piece:
        if candidate_piece in MAJOR_PIECES or manual_piece in MAJOR_PIECES:
            return "critical"
        if candidate_piece in MINOR_AND_PAWN_PIECES or manual_piece in MINOR_AND_PAWN_PIECES:
            return "high"
    if candidate_piece or manual_piece:
        return "medium"
    return "low"


def infer_verification_source(record: dict[str, Any]) -> str:
    explicit = str(record.get("verification_source") or "").strip().lower()
    if explicit:
        return explicit
    verified_by = str(record.get("verified_by") or "").strip().lower()
    notes = str(record.get("notes") or "").strip().lower()
    if (
        verified_by == "unit-test"
        or "manual" in verified_by
        or "visual" in verified_by
        or "visual" in notes
        or "spot-checked" in notes
        or "manually" in notes
    ):
        return "legacy_human_visual"
    if record.get("human_verified") is True:
        return "human_visual"
    if record.get("ai_assisted") or record.get("ai_suggested_fen") or record.get("ai_approved"):
        return "ai_review_only"
    return ""


def is_human_verification_source(source: str) -> bool:
    return str(source or "").strip().lower() in HUMAN_VERIFICATION_SOURCES


def is_ai_only_verification_source(source: str) -> bool:
    value = str(source or "").strip().lower()
    return value in AI_ONLY_VERIFICATION_SOURCES or value.startswith("ai_")


def has_square_diff_ack(record: dict[str, Any]) -> bool:
    if record.get("square_diff_ack") is True:
        return True
    if record.get("square_diff_reviewed") is True:
        return True
    if isinstance(record.get("square_diff"), list):
        return True
    return False
