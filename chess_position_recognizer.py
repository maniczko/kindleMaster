from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import numpy as np
from PIL import Image, ImageFilter, ImageOps


PIECE_CHARS = set("pnbrqkPNBRQK")
UNICODE_PIECES = {
    "\u2654": "K",
    "\u2655": "Q",
    "\u2656": "R",
    "\u2657": "B",
    "\u2658": "N",
    "\u2659": "P",
    "\u265a": "k",
    "\u265b": "q",
    "\u265c": "r",
    "\u265d": "b",
    "\u265e": "n",
    "\u265f": "p",
}
EMPTY_CELL_CHARS = {"", ".", "-", "_", " ", "\u00b7", "\u25a1", "\u25a2", "\u25a3", "\u25a6", "\u25a7", "\u25a8", "\u25a9", "0", "Z"}

# Figurine fonts already normalized elsewhere in the chess extractor. Keeping
# this map here lets unit-level font-board tests use the same source glyphs.
FIGURINE_FONT_PIECES = {
    "\xa2": "K",
    "\xa3": "Q",
    "\xa4": "N",
    "\xa5": "B",
    "\xa6": "R",
}

# SkakNew-Diagram encodes pieces with two glyph variants per piece, depending
# on square color. CTAN's SkakNew documentation prints the initial position as
# `rmblkans/opopopop/.../POPOPOPO/SNAQJBMR`, which defines this map.
SKAKNEW_DIAGRAM_PIECES = {
    "J": "K",
    "K": "K",
    "L": "Q",
    "Q": "Q",
    "R": "R",
    "S": "R",
    "A": "B",
    "B": "B",
    "M": "N",
    "N": "N",
    "O": "P",
    "P": "P",
    "j": "k",
    "k": "k",
    "l": "q",
    "q": "q",
    "r": "r",
    "s": "r",
    "a": "b",
    "b": "b",
    "m": "n",
    "n": "n",
    "o": "p",
    "p": "p",
}

TEMPLATE_CELL_SIZE = 32
# Keep runtime matching bounded as the verified corpus grows. Empty-square
# templates dominate the dataset, so a deterministic diverse cap avoids a
# quadratic slowdown without changing the template file format.
# Keep enough empty-square diversity that adding a verified board label does
# not evict sparse/endgame backgrounds from the deterministic matcher.
MAX_EMPTY_TEMPLATE_VARIANTS = 1200
MAX_PIECE_TEMPLATE_VARIANTS = 10_000
MIN_BOARD_ALTERNATION_SIGNAL = 0.40
MIN_BOARD_CELL_TONE_STD = 24.0
MIN_BOARD_CELL_TONE_RANGE = 85.0
MIN_DENSE_CROP_ACCEPTANCE_CONFIDENCE = 0.90
MIN_DENSE_CROP_CONFIDENCE_GAIN = 0.08
MIN_DENSE_CROP_GRID_GAIN = 0.08
MIN_ACCEPTED_TEMPLATE_GRID_WITHOUT_DENSE_CROP = 0.55
SPARSE_POSITION_PIECE_LIMIT = 8
# Sparse/endgame diagrams are easy to make syntactically valid by accident.
# Keep a higher bar than the general FEN threshold. A narrow 0.832 margin is
# calibrated against sparse/endgame review crops: it recovers near-threshold
# legal positions while still blocking lower-confidence hallucinated boards.
MIN_SPARSE_POSITION_CONFIDENCE = 0.832
MAX_CROSS_MARKER_TEMPLATE_CONFIDENCE = 0.35
MAX_DARK_QUEEN_AMBIGUITY_CONFIDENCE = 0.70
MIN_DARK_QUEEN_AMBIGUITY_FOREGROUND_MEAN = 0.30
MIN_DARK_QUEEN_AMBIGUITY_DENSITY = 0.38
MIN_INNER_CHECKERBOARD_CROP_SIGNAL = 0.62
MIN_INNER_CHECKERBOARD_CROP_GAIN = 0.16
MIN_DOMINANT_CONTENT_CROP_SIGNAL = 0.46
MIN_DOMINANT_CONTENT_CROP_GRID = 0.34
MIN_EMPTY_VS_PIECE_ERROR_MARGIN = 0.003
MIN_RECOGNITION_TRIM_GRID_CONFIDENCE = 0.34
MIN_RECOGNITION_TRIM_SCORE_GAIN = 0.04
_PREPARED_TEMPLATE_CACHE_LIMIT = 8
_PREPARED_TEMPLATE_CACHE: dict[tuple[tuple[str, tuple[int, ...]], ...], dict[str, np.ndarray]] = {}


@dataclass(frozen=True)
class ChessFenResult:
    fen: str = ""
    placement: str = ""
    full_fen: str = ""
    confidence: float = 0.0
    side_to_move: str = "w"
    side_to_move_status: str = "unknown"
    side_to_move_evidence: str = "none"
    bbox: tuple[float, float, float, float] | None = None
    method: str = "unavailable"
    warnings: list[str] = field(default_factory=list)
    requires_review: bool = True
    board_detected: bool = False
    squares: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        full_fen = self.full_fen or self.fen
        warnings = list(self.warnings)
        trusted_side_to_move = (
            self.side_to_move_status == "explicit"
            and self.side_to_move_evidence in {"marker", "caption", "verified_label", "exact_label"}
        ) or "verified_exact_crop_label_used" in set(warnings)
        runtime_fen = self.fen
        requires_review = bool(self.requires_review)
        fen_suppressed_reason = ""
        if "side_to_move_inferred" in warnings and not trusted_side_to_move:
            runtime_fen = ""
            requires_review = True
            fen_suppressed_reason = "side_to_move_inferred"
        return {
            "fen": runtime_fen,
            "full_fen": full_fen,
            "placement": self.placement,
            "placement_fen": self.placement,
            "fen_suppressed_reason": fen_suppressed_reason,
            "confidence": round(float(self.confidence or 0.0), 3),
            "side_to_move": self.side_to_move,
            "side_to_move_status": self.side_to_move_status,
            "side_to_move_evidence": self.side_to_move_evidence,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "method": self.method,
            "warnings": warnings,
            "requires_review": requires_review,
            "board_detected": bool(self.board_detected),
            "squares": [dict(square) for square in self.squares],
        }


class ChessFenReviewProvider(Protocol):
    name: str

    def review_chess_fen(self, context: dict[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError


def empty_chess_fen_result(
    *,
    method: str,
    warning: str,
    bbox: tuple[float, float, float, float] | None = None,
    confidence: float = 0.0,
    board_detected: bool = False,
) -> ChessFenResult:
    return ChessFenResult(
        method=method,
        warnings=[warning],
        bbox=bbox,
        confidence=confidence,
        requires_review=True,
        board_detected=board_detected,
    )


def build_fen_from_board(
    board: list[list[str]],
    *,
    side_to_move: str = "w",
    castling: str = "-",
    en_passant: str = "-",
    halfmove_clock: int = 0,
    fullmove_number: int = 1,
) -> str:
    placement = board_to_placement(board)
    side = "b" if str(side_to_move).lower().startswith("b") else "w"
    castling_value = castling if castling and castling != "" else "-"
    en_passant_value = en_passant if en_passant and en_passant != "" else "-"
    return f"{placement} {side} {castling_value} {en_passant_value} {max(0, int(halfmove_clock))} {max(1, int(fullmove_number))}"


def board_to_placement(board: list[list[str]]) -> str:
    if len(board) != 8 or any(len(row) != 8 for row in board):
        raise ValueError("A chess board must contain exactly 8 ranks and 8 files.")
    ranks: list[str] = []
    for row in board:
        empty_run = 0
        rank = []
        for piece in row:
            value = str(piece or "")
            if not value:
                empty_run += 1
                continue
            if value not in PIECE_CHARS:
                raise ValueError(f"Unsupported chess piece marker: {value!r}")
            if empty_run:
                rank.append(str(empty_run))
                empty_run = 0
            rank.append(value)
        if empty_run:
            rank.append(str(empty_run))
        ranks.append("".join(rank) or "8")
    return "/".join(ranks)


def validate_fen(fen: str) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    parts = str(fen or "").strip().split()
    if len(parts) != 6:
        return False, ["fen_must_have_six_fields"]
    placement, side, castling, en_passant, halfmove, fullmove = parts
    ranks = placement.split("/")
    if len(ranks) != 8:
        warnings.append("placement_must_have_eight_ranks")
    white_kings = placement.count("K")
    black_kings = placement.count("k")
    if white_kings != 1:
        warnings.append("white_king_count_invalid")
    if black_kings != 1:
        warnings.append("black_king_count_invalid")
    for rank in ranks:
        width = 0
        last_was_digit = False
        for char in rank:
            if char.isdigit():
                value = int(char)
                if value < 1 or value > 8 or last_was_digit:
                    warnings.append("rank_digit_invalid")
                width += value
                last_was_digit = True
            elif char in PIECE_CHARS:
                width += 1
                last_was_digit = False
            else:
                warnings.append("placement_contains_invalid_piece")
        if width != 8:
            warnings.append("rank_width_invalid")
    if len(ranks) == 8:
        if any(char in {"P", "p"} for char in ranks[0]):
            warnings.append("pawn_on_back_rank")
        if any(char in {"P", "p"} for char in ranks[7]):
            warnings.append("pawn_on_back_rank")
    if side not in {"w", "b"}:
        warnings.append("side_to_move_invalid")
    if castling != "-" and not re.fullmatch(r"K?Q?k?q?", castling):
        warnings.append("castling_invalid")
    if en_passant != "-" and not re.fullmatch(r"[a-h][36]", en_passant):
        warnings.append("en_passant_invalid")
    if not halfmove.isdigit() or not fullmove.isdigit() or int(fullmove or "0") < 1:
        warnings.append("move_counters_invalid")
    return not warnings, sorted(set(warnings))


def recognize_font_board_from_lines(
    lines: Iterable[str],
    *,
    side_to_move: str = "w",
    bbox: tuple[float, float, float, float] | None = None,
    min_confidence: float = 0.85,
) -> ChessFenResult:
    board_rows: list[list[str]] = []
    unknown_rows = 0
    for line in lines:
        row, warning = _decode_board_row(line)
        if row is None:
            if warning == "unknown_chess_font_row":
                unknown_rows += 1
            continue
        board_rows.append(row)

    if len(board_rows) != 8:
        warning = "font_board_not_decoded"
        if unknown_rows:
            warning = "font_board_contains_unknown_glyphs"
        return empty_chess_fen_result(method="font-board", warning=warning, bbox=bbox)

    try:
        fen = build_fen_from_board(board_rows, side_to_move=side_to_move)
    except ValueError as exc:
        return empty_chess_fen_result(method="font-board", warning=str(exc), bbox=bbox)

    is_valid, warnings = validate_fen(fen)
    confidence = 0.96 if is_valid else 0.68
    if "side_to_move_inferred" not in warnings and side_to_move not in {"w", "b"}:
        warnings.append("side_to_move_inferred")
    return ChessFenResult(
        fen=fen if confidence >= min_confidence and is_valid else "",
        placement=fen.split()[0],
        full_fen=fen,
        confidence=confidence,
        side_to_move="b" if side_to_move == "b" else "w",
        side_to_move_status="explicit" if side_to_move in {"w", "b"} else "inferred",
        side_to_move_evidence="caption" if side_to_move in {"w", "b"} else "inferred",
        bbox=bbox,
        method="font-board",
        warnings=warnings,
        requires_review=not (confidence >= min_confidence and is_valid),
        board_detected=True,
    )


def recognize_font_board_from_spans(
    spans: Iterable[Any],
    *,
    bbox: tuple[float, float, float, float] | None = None,
    side_to_move: str = "w",
    min_confidence: float = 0.85,
) -> ChessFenResult:
    ordered = sorted(
        spans,
        key=lambda item: (
            round(float(getattr(item, "y", 0.0) or 0.0), 1),
            float(getattr(item, "x", 0.0) or 0.0),
            int(getattr(item, "index", 0) or 0),
        ),
    )
    rows: list[list[Any]] = []
    for span in ordered:
        y = float(getattr(span, "y", 0.0) or 0.0)
        if rows and abs(float(getattr(rows[-1][0], "y", 0.0) or 0.0) - y) <= 1.5:
            rows[-1].append(span)
        else:
            rows.append([span])
    lines = [
        "".join(str(getattr(span, "text", "") or "") for span in sorted(row, key=lambda item: float(getattr(item, "x", 0.0) or 0.0)))
        for row in rows
    ]
    return recognize_font_board_from_lines(lines, bbox=bbox, side_to_move=side_to_move, min_confidence=min_confidence)


def recognize_chess_position_from_image(
    image_data: bytes,
    *,
    bbox: tuple[float, float, float, float] | None = None,
    min_confidence: float = 0.92,
    piece_templates: Mapping[str, Iterable[Any]] | None = None,
    allow_recognition_recovery: bool = True,
) -> ChessFenResult:
    try:
        image = Image.open(io.BytesIO(image_data)).convert("L")
    except Exception:
        return empty_chess_fen_result(method="image-board", warning="image_unreadable", bbox=bbox)

    board_image = _normalize_board_square(image)
    grid_confidence = _estimate_board_grid_confidence(board_image)
    board_detected, board_signal = _has_board_visual_pattern(board_image)
    if not board_detected:
        return empty_chess_fen_result(
            method="image-board",
            warning="board_visual_pattern_not_detected",
            bbox=bbox,
            confidence=min(grid_confidence, board_signal),
            board_detected=False,
        )
    if grid_confidence < 0.55 and piece_templates:
        template_result = _recognize_board_with_templates(
            board_image,
            piece_templates,
            grid_confidence=grid_confidence,
            bbox=bbox,
            min_confidence=min_confidence,
            allow_recognition_recovery=allow_recognition_recovery,
        )
        if template_result is not None:
            return template_result

    if grid_confidence < 0.55:
        return empty_chess_fen_result(
            method="image-board",
            warning="board_grid_not_detected",
            bbox=bbox,
            confidence=grid_confidence,
        )

    if piece_templates:
        template_result = _recognize_board_with_templates(
            board_image,
            piece_templates,
            grid_confidence=grid_confidence,
            bbox=bbox,
            min_confidence=min_confidence,
            allow_recognition_recovery=allow_recognition_recovery,
        )
        if template_result is not None:
            return template_result

    warnings = ["piece_templates_unavailable", "image_board_requires_review", "side_to_move_inferred"]
    return ChessFenResult(
        confidence=min(grid_confidence, min_confidence - 0.01),
        side_to_move="w",
        bbox=bbox,
        method="image-board",
        warnings=warnings,
        requires_review=True,
        board_detected=True,
    )


def detect_board_candidates_in_page_image(
    image_data: bytes,
    *,
    max_candidates: int = 3,
    min_grid_confidence: float = 0.58,
    enable_sliding_probe: bool = False,
) -> list[ChessFenResult]:
    candidate_limit = max(0, int(max_candidates))
    if candidate_limit == 0:
        return []
    try:
        image = Image.open(io.BytesIO(image_data)).convert("L")
    except Exception:
        return []

    width, height = image.size
    long_edge = max(width, height)
    if long_edge <= 0:
        return []
    scale = min(1.0, 760.0 / float(long_edge))
    probe = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.Resampling.BILINEAR)
    probe_width, probe_height = probe.size
    min_side = max(64, int(min(probe_width, probe_height) * 0.12))
    max_side = max(min_side, int(min(probe_width, probe_height) * 0.56))
    step_sizes = sorted({min_side, int(min_side * 1.25), int(min_side * 1.55), int(min_side * 1.9), int(min_side * 2.8), max_side})
    candidates: list[ChessFenResult] = []
    seen: list[tuple[float, float, float, float]] = []

    for grid_bbox in _regular_grid_square_candidates(probe, scale=scale):
        gx0, gy0, gx1, gy1 = grid_bbox
        crop = image.crop((gx0, gy0, gx1, gy1))
        if not _has_board_visual_pattern(crop)[0]:
            continue
        confidence = _estimate_board_grid_confidence(crop)
        if confidence < min_grid_confidence:
            continue
        if any(_bbox_overlap_ratio(grid_bbox, existing) > 0.55 for existing in seen):
            continue
        seen.append(grid_bbox)
        candidates.append(
            ChessFenResult(
                confidence=confidence,
                bbox=grid_bbox,
                method="image-page-board-candidate",
                warnings=["piece_templates_unavailable", "image_board_requires_review", "side_to_move_inferred"],
                requires_review=True,
                board_detected=True,
            )
        )
        if len(candidates) >= candidate_limit:
            break

    for grid_bbox in _edge_component_square_candidates(probe, scale=scale):
        gx0, gy0, gx1, gy1 = grid_bbox
        crop = image.crop((gx0, gy0, gx1, gy1))
        if not _has_board_visual_pattern(crop)[0]:
            continue
        confidence = _estimate_board_grid_confidence(crop)
        # Component candidates often include the printed "Diagram N" caption or
        # coordinates around the board. Keep a slightly lower floor here so the
        # extractor can crop the board for manual/FEN review without inventing FEN.
        if confidence < max(0.42, min_grid_confidence - 0.1):
            continue
        if any(_bbox_overlap_ratio(grid_bbox, existing) > 0.55 for existing in seen):
            continue
        seen.append(grid_bbox)
        candidates.append(
            ChessFenResult(
                confidence=confidence,
                bbox=grid_bbox,
                method="image-page-board-component",
                warnings=["piece_templates_unavailable", "image_board_requires_review", "side_to_move_inferred"],
                requires_review=True,
                board_detected=True,
            )
        )
        if len(candidates) >= candidate_limit:
            break

    global_candidate = _dark_pixel_square_candidate(probe, scale=scale)
    if global_candidate is not None:
        gx0, gy0, gx1, gy1 = global_candidate
        crop = image.crop((gx0, gy0, gx1, gy1))
        if not _has_board_visual_pattern(crop)[0]:
            global_candidate = None
        else:
            confidence = _estimate_board_grid_confidence(crop)
        if global_candidate is not None:
            if confidence >= min_grid_confidence:
                seen.append(global_candidate)
                candidates.append(
                    ChessFenResult(
                        confidence=confidence,
                        bbox=global_candidate,
                        method="image-page-board-candidate",
                        warnings=["piece_templates_unavailable", "image_board_requires_review", "side_to_move_inferred"],
                        requires_review=True,
                        board_detected=True,
                    )
                )
                if confidence >= 0.9:
                    return candidates[:candidate_limit]

    if not enable_sliding_probe:
        candidates = _refine_page_candidates_with_border_lines(image, probe, scale=scale, candidates=candidates)
        candidates.sort(key=lambda item: item.confidence, reverse=True)
        return candidates[:candidate_limit]

    for side in step_sizes:
        stride = max(24, side // 3)
        for top in range(0, max(1, probe_height - side + 1), stride):
            for left in range(0, max(1, probe_width - side + 1), stride):
                crop = probe.crop((left, top, left + side, top + side))
                if not _has_board_visual_pattern(crop)[0]:
                    continue
                confidence = _estimate_board_grid_confidence(crop)
                if confidence < min_grid_confidence:
                    continue
                original_bbox = (
                    left / scale,
                    top / scale,
                    (left + side) / scale,
                    (top + side) / scale,
                )
                if any(_bbox_overlap_ratio(original_bbox, existing) > 0.55 for existing in seen):
                    continue
                seen.append(original_bbox)
                candidates.append(
                    ChessFenResult(
                        confidence=confidence,
                        bbox=original_bbox,
                        method="image-page-board-candidate",
                        warnings=["piece_templates_unavailable", "image_board_requires_review", "side_to_move_inferred"],
                        requires_review=True,
                        board_detected=True,
                    )
                )

    candidates = _refine_page_candidates_with_border_lines(image, probe, scale=scale, candidates=candidates)
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    return candidates[:candidate_limit]


def load_piece_templates(template_dir: str | Path) -> dict[str, list[Image.Image]]:
    """Load piece-cell templates from a directory.

    Filenames should start with a FEN piece marker (`K_*.png`, `p-dark.png`) or
    with `empty`, `blank`, or `none` for empty-square templates.
    """
    root = Path(template_dir)
    if not root.exists() or not root.is_dir():
        return {}
    templates: dict[str, list[Image.Image]] = {}
    for path in sorted(root.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        label = _template_label_from_path(path)
        if label is None:
            continue
        try:
            image = Image.open(path).convert("L").copy()
        except Exception:
            continue
        templates.setdefault(label, []).append(image)
    return templates


def normalize_board_crop_for_templates(image: Image.Image) -> Image.Image:
    """Normalize a labeled board crop exactly like image-template recognition."""
    return _normalize_board_square(image)


def review_chess_fen_candidate(
    result: ChessFenResult,
    *,
    provider: ChessFenReviewProvider | None = None,
    image_data: bytes | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "skipped",
        "provider": getattr(provider, "name", "none") if provider is not None else "none",
        "fen": result.fen,
        "confidence": result.confidence,
        "warnings": list(result.warnings),
        "changed_output": False,
    }
    if provider is None:
        payload["fallback_reason"] = "provider-not-configured"
        return payload
    try:
        review_context = {**(context or {}), "candidate": result.to_dict(), "has_image": image_data is not None}
        if image_data is not None:
            review_context["image_data"] = image_data
        review = dict(provider.review_chess_fen(review_context))
    except Exception as exc:
        return {**payload, "status": "failed", "fallback_reason": f"provider-error:{exc.__class__.__name__}"}
    return {**payload, **review, "changed_output": False}


def summarize_chess_fen_results(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(records)
    with_fen = [item for item in records if str(item.get("fen") or "").strip()]
    review = [item for item in records if item.get("requires_review")]
    with_board_crop = [item for item in records if str(item.get("board_crop_path") or "").strip()]
    with_side_marker_crop = [item for item in records if str(item.get("side_marker_crop_path") or "").strip()]
    with_debug_overlay = [item for item in records if str(item.get("debug_overlay_path") or "").strip()]
    return {
        "status": "not_applicable" if total == 0 else ("passed" if len(with_fen) == total else "requires_review"),
        "diagram_count": total,
        "fen_count": len(with_fen),
        "manual_review_count": len(review),
        "board_crop_count": len(with_board_crop),
        "side_marker_crop_count": len(with_side_marker_crop),
        "debug_overlay_count": len(with_debug_overlay),
        "records": [dict(item) for item in records],
    }


def _recognize_board_with_templates(
    image: Image.Image,
    piece_templates: Mapping[str, Iterable[Any]],
    *,
    grid_confidence: float,
    bbox: tuple[float, float, float, float] | None,
    min_confidence: float,
    allow_recognition_recovery: bool = True,
) -> ChessFenResult | None:
    normalized_templates = _prepare_cached_piece_templates(piece_templates)
    if not normalized_templates:
        return None

    board, template_confidence, squares = _classify_board_cells(image, normalized_templates)
    result = _template_result_from_board(
        board,
        template_confidence,
        squares,
        normalized_templates,
        grid_confidence=grid_confidence,
        bbox=bbox,
        min_confidence=min_confidence,
    )
    result = _downgrade_low_grid_partial_board_result(result, image, grid_confidence)

    if allow_recognition_recovery:
        repaired_result = _recover_trimmed_review_board_result(
            image,
            result,
            normalized_templates,
            grid_confidence=grid_confidence,
            bbox=bbox,
            min_confidence=min_confidence,
        )
        if repaired_result is not None:
            return repaired_result

        if result.requires_review and any(warning.endswith("king_count_invalid") for warning in result.warnings):
            dense_crop = _dense_board_area_crop(ImageOps.autocontrast(image.convert("L")))
            if dense_crop is not None:
                dense_board_detected, _dense_board_signal = _has_board_visual_pattern(dense_crop)
                dense_grid_confidence = _estimate_board_grid_confidence(dense_crop)
                if not dense_board_detected:
                    return result
                dense_board, dense_confidence, dense_squares = _classify_board_cells(dense_crop, normalized_templates)
                dense_result = _template_result_from_board(
                    dense_board,
                    dense_confidence,
                    dense_squares,
                    normalized_templates,
                    grid_confidence=dense_grid_confidence,
                    bbox=bbox,
                    min_confidence=min_confidence,
                    extra_warnings=["dense_board_area_crop_used"],
                )
                dense_improved_enough = (
                    dense_result.confidence >= min_confidence
                    and dense_result.confidence >= result.confidence + MIN_DENSE_CROP_CONFIDENCE_GAIN
                    and dense_grid_confidence >= grid_confidence + MIN_DENSE_CROP_GRID_GAIN
                )
                if (
                    not dense_result.requires_review
                    and (
                        dense_result.confidence >= max(min_confidence, MIN_DENSE_CROP_ACCEPTANCE_CONFIDENCE)
                        or dense_improved_enough
                    )
                ):
                    return dense_result

    return result


def _recover_trimmed_review_board_result(
    image: Image.Image,
    base_result: ChessFenResult,
    normalized_templates: Mapping[str, np.ndarray],
    *,
    grid_confidence: float,
    bbox: tuple[float, float, float, float] | None,
    min_confidence: float,
) -> ChessFenResult | None:
    if not base_result.requires_review or not base_result.board_detected:
        return None
    base_warnings = {str(warning) for warning in base_result.warnings}
    repairs_king_count = any(warning.endswith("king_count_invalid") for warning in base_warnings)
    repairs_cross_marker = "annotation_cross_marker_suppressed" in base_warnings
    if not repairs_king_count and not repairs_cross_marker:
        return None

    best: tuple[float, float, ChessFenResult] | None = None
    for crop, trim_warning in _recognition_trim_variant_crops(image):
        detected, signal = _has_board_visual_pattern(crop)
        if not detected:
            continue
        variant_grid = _estimate_board_grid_confidence(crop)
        if variant_grid < MIN_RECOGNITION_TRIM_GRID_CONFIDENCE:
            continue
        baseline_score = grid_confidence + (0.10 if repairs_king_count else 0.0)
        variant_score = variant_grid + signal * 0.18
        if variant_score < baseline_score + MIN_RECOGNITION_TRIM_SCORE_GAIN:
            continue
        board, template_confidence, squares = _classify_board_cells(crop, normalized_templates)
        variant_result = _template_result_from_board(
            board,
            template_confidence,
            squares,
            normalized_templates,
            grid_confidence=variant_grid,
            bbox=bbox,
            min_confidence=min_confidence,
            extra_warnings=[trim_warning],
        )
        if not _trimmed_result_is_safe_upgrade(
            base_result,
            variant_result,
            repairs_king_count=repairs_king_count,
            repairs_cross_marker=repairs_cross_marker,
            min_confidence=min_confidence,
        ):
            continue
        score = float(variant_result.confidence or 0.0)
        candidate = (score, variant_grid, variant_result)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None
    return best[2]


def _trimmed_result_is_safe_upgrade(
    base_result: ChessFenResult,
    variant_result: ChessFenResult,
    *,
    repairs_king_count: bool,
    repairs_cross_marker: bool,
    min_confidence: float,
) -> bool:
    if not variant_result.fen or variant_result.requires_review:
        return False
    if float(variant_result.confidence or 0.0) < float(min_confidence or 0.0):
        return False
    base_warnings = {str(warning) for warning in base_result.warnings}
    variant_warnings = {str(warning) for warning in variant_result.warnings}
    repaired_king_count = repairs_king_count and not any(
        warning.endswith("king_count_invalid") for warning in variant_warnings
    )
    repaired_cross_marker = repairs_cross_marker and "annotation_cross_marker_suppressed" not in variant_warnings
    if not repaired_king_count and not repaired_cross_marker:
        return False
    if "sparse_position_confidence_below_threshold" in base_warnings and not repaired_king_count:
        return False
    return True


def _prepare_cached_piece_templates(piece_templates: Mapping[str, Iterable[Any]]) -> dict[str, np.ndarray]:
    cache_key = _piece_template_cache_key(piece_templates)
    if cache_key is None:
        return _prepare_piece_templates(piece_templates)
    cached = _PREPARED_TEMPLATE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    prepared = _prepare_piece_templates(piece_templates)
    if len(_PREPARED_TEMPLATE_CACHE) >= _PREPARED_TEMPLATE_CACHE_LIMIT:
        _PREPARED_TEMPLATE_CACHE.pop(next(iter(_PREPARED_TEMPLATE_CACHE)))
    _PREPARED_TEMPLATE_CACHE[cache_key] = prepared
    return prepared


def _piece_template_cache_key(piece_templates: Mapping[str, Iterable[Any]]) -> tuple[tuple[str, tuple[int, ...]], ...] | None:
    parts: list[tuple[str, tuple[int, ...]]] = []
    try:
        items = list(piece_templates.items())
    except AttributeError:
        return None
    for raw_label, sources in items:
        if isinstance(sources, (str, bytes)) or sources is None:
            return None
        try:
            source_ids = tuple(id(source) for source in sources)
        except TypeError:
            return None
        parts.append((str(raw_label or ""), source_ids))
    return tuple(sorted(parts, key=lambda item: item[0]))


def _clear_piece_template_cache() -> None:
    _PREPARED_TEMPLATE_CACHE.clear()


def _downgrade_low_grid_partial_board_result(
    result: ChessFenResult,
    image: Image.Image,
    grid_confidence: float,
) -> ChessFenResult:
    if not result.fen or result.requires_review:
        return result
    if grid_confidence >= MIN_ACCEPTED_TEMPLATE_GRID_WITHOUT_DENSE_CROP:
        return result
    dense_crop = _dense_board_area_crop(ImageOps.autocontrast(image.convert("L")))
    if dense_crop is not None:
        return result
    warnings = sorted(set([*result.warnings, "partial_board_crop_without_dense_board_evidence"]))
    return ChessFenResult(
        fen="",
        placement=result.placement,
        full_fen=result.full_fen or result.fen,
        confidence=result.confidence,
        side_to_move=result.side_to_move,
        side_to_move_status=result.side_to_move_status,
        side_to_move_evidence=result.side_to_move_evidence,
        bbox=result.bbox,
        method=result.method,
        warnings=warnings,
        requires_review=True,
        board_detected=result.board_detected,
        squares=result.squares,
    )


def _template_result_from_board(
    board: list[list[str]],
    template_confidence: float,
    squares: list[dict[str, Any]],
    normalized_templates: Mapping[str, np.ndarray],
    *,
    grid_confidence: float,
    bbox: tuple[float, float, float, float] | None,
    min_confidence: float,
    extra_warnings: Iterable[str] | None = None,
) -> ChessFenResult:
    try:
        fen = build_fen_from_board(board, side_to_move="w")
    except ValueError as exc:
        return empty_chess_fen_result(
            method="image-template-board",
            warning=str(exc),
            bbox=bbox,
            confidence=template_confidence,
            board_detected=True,
        )

    is_valid, warnings = validate_fen(fen)
    warnings = list(warnings)
    if "side_to_move_inferred" not in warnings:
        warnings.append("side_to_move_inferred")
    warnings.extend(extra_warnings or [])
    square_warnings = {
        str(warning)
        for square in squares
        for warning in square.get("warnings", [])
    }
    warnings.extend(square_warnings)
    template_set_complete = "" in normalized_templates and len({piece for piece in normalized_templates if piece in PIECE_CHARS}) == 12
    if not template_set_complete:
        warnings.append("piece_template_set_incomplete")

    # Once a board candidate is found, piece matching is the stronger signal
    # for FEN publication. Scanned crops can have modest grid confidence due
    # to print texture and coordinate baselines, so keep grid confidence as a
    # small stabilizer rather than letting it suppress exact template matches.
    confidence = max(0.0, min(1.0, template_confidence * 0.96 + grid_confidence * 0.04))
    piece_count = sum(1 for row in board for piece in row if piece)
    sparse_position_low_confidence = piece_count <= SPARSE_POSITION_PIECE_LIMIT and confidence < MIN_SPARSE_POSITION_CONFIDENCE
    if sparse_position_low_confidence:
        warnings.append("sparse_position_confidence_below_threshold")
    square_ambiguity_requires_review = "queen_color_ambiguous_suppressed" in square_warnings
    accepted = bool(
        template_set_complete
        and is_valid
        and confidence >= min_confidence
        and not sparse_position_low_confidence
        and not square_ambiguity_requires_review
    )
    if not accepted and confidence < min_confidence:
        warnings.append("piece_template_confidence_below_threshold")

    return ChessFenResult(
        fen=fen if accepted else "",
        placement=fen.split()[0],
        full_fen=fen,
        confidence=confidence,
        side_to_move="w",
        side_to_move_status="inferred",
        side_to_move_evidence="inferred",
        bbox=bbox,
        method="image-template-board",
        warnings=sorted(set(warnings)),
        requires_review=not accepted,
        board_detected=True,
        squares=squares,
    )


def _prepare_piece_templates(piece_templates: Mapping[str, Iterable[Any]]) -> dict[str, np.ndarray]:
    normalized: dict[str, list[np.ndarray]] = {}
    for raw_label, sources in piece_templates.items():
        label = _normalize_template_label(str(raw_label or ""))
        if label is None:
            continue
        for source in sources or []:
            image = _coerce_template_image(source)
            if image is None:
                continue
            normalized.setdefault(label, []).append(_normalize_piece_cell(image))
    return {
        label: np.stack(_select_template_variants(label, values), axis=0)
        for label, values in normalized.items()
        if values
    }


def _select_template_variants(label: str, values: list[np.ndarray]) -> list[np.ndarray]:
    limit = MAX_EMPTY_TEMPLATE_VARIANTS if label == "" else MAX_PIECE_TEMPLATE_VARIANTS
    if len(values) <= limit:
        return values
    if limit <= 1:
        return values[:1]
    last = len(values) - 1
    indexes = sorted({int(round(index * last / float(limit - 1))) for index in range(limit)})
    return [values[index] for index in indexes]


def _classify_board_cells(
    image: Image.Image,
    templates: Mapping[str, np.ndarray],
) -> tuple[list[list[str]], float, list[dict[str, Any]]]:
    board_image = _normalize_board_square(image).resize((TEMPLATE_CELL_SIZE * 8, TEMPLATE_CELL_SIZE * 8), Image.Resampling.BILINEAR)
    board: list[list[str]] = []
    confidences: list[float] = []
    squares: list[dict[str, Any]] = []
    for row in range(8):
        board_row: list[str] = []
        for col in range(8):
            cell = board_image.crop(
                (
                    col * TEMPLATE_CELL_SIZE,
                    row * TEMPLATE_CELL_SIZE,
                    (col + 1) * TEMPLATE_CELL_SIZE,
                    (row + 1) * TEMPLATE_CELL_SIZE,
                )
            )
            normalized_cell = _normalize_piece_cell(cell)
            label, confidence, alternatives = _match_piece_template_with_alternatives(normalized_cell, templates)
            square_warnings: list[str] = []
            if (
                label
                and label not in {"K", "k"}
                and confidence < MAX_CROSS_MARKER_TEMPLATE_CONFIDENCE
                and _looks_like_non_piece_cross_marker(normalized_cell)
            ):
                label = ""
                confidence = max(confidence, 0.86)
                square_warnings.append("annotation_cross_marker_suppressed")
            if _looks_like_low_confidence_dark_queen_ambiguity(label, confidence, normalized_cell, templates):
                label = ""
                confidence = min(confidence, MAX_DARK_QUEEN_AMBIGUITY_CONFIDENCE)
                square_warnings.append("queen_color_ambiguous_suppressed")
            board_row.append(label)
            confidences.append(confidence)
            square_record = {
                "square": f"{chr(ord('a') + col)}{8 - row}",
                "piece": label,
                "confidence": round(float(confidence), 3),
                "alternatives": alternatives,
            }
            if square_warnings:
                square_record["warnings"] = square_warnings
            squares.append(square_record)
        board.append(board_row)
    if not confidences:
        return board, 0.0, squares
    scores = np.array(confidences, dtype=np.float32)
    lower_decile = float(np.percentile(scores, 10))
    return board, float(scores.mean() * 0.88 + lower_decile * 0.12), squares


def _normalize_board_square(image: Image.Image) -> Image.Image:
    """Return a square board crop using the same conservative crop as template building.

    Board crops produced from scanned pages are often a few pixels taller than
    wide because they include an outer border or coordinate baseline. Using the
    full rectangle for recognition while templates use a square crop shifts the
    8x8 cell grid and causes edge-rank pieces to disappear. Center-cropping the
    shortest side keeps recognition and template extraction aligned without
    inventing any board content.
    """
    grayscale = ImageOps.autocontrast(image.convert("L"))
    border_crop = _strong_border_square_crop(grayscale)
    if border_crop is not None:
        return border_crop
    inner_checkerboard_crop = _checkerboard_inner_square_crop(grayscale)
    if inner_checkerboard_crop is not None:
        return inner_checkerboard_crop

    side = max(1, min(grayscale.size))
    baseline_left = max(0, (grayscale.width - side) // 2)
    baseline_top = max(0, (grayscale.height - side) // 2)
    return grayscale.crop((baseline_left, baseline_top, baseline_left + side, baseline_top + side))


def _recognition_trim_variant_crops(image: Image.Image) -> list[tuple[Image.Image, str]]:
    grayscale = ImageOps.autocontrast(image.convert("L"))
    variants: list[tuple[Image.Image, str]] = []
    for crop, warning in (
        (_recognition_inner_border_trim_crop(grayscale), "recognition_inner_border_trim_used"),
        (_recognition_caption_bottom_trim_crop(grayscale), "recognition_caption_bottom_trim_used"),
        (_recognition_side_marker_trim_crop(grayscale), "recognition_side_marker_trim_used"),
        (_recognition_corner_marker_trim_crop(grayscale), "recognition_corner_marker_trim_used"),
    ):
        if crop is None:
            continue
        if crop.size == grayscale.size:
            continue
        variants.append((crop, warning))
    return variants


def _recognition_inner_border_trim_crop(image: Image.Image) -> Image.Image | None:
    pixels = np.array(image, dtype=np.uint8)
    if pixels.size == 0:
        return None
    dark = pixels < 118
    col_groups = _dense_projection_groups(dark.mean(axis=0), threshold=0.24)
    row_groups = _dense_projection_groups(dark.mean(axis=1), threshold=0.24)
    if len(col_groups) < 2 or len(row_groups) < 2:
        return None
    width, height = image.size
    left_group = col_groups[0]
    right_group = col_groups[-1]
    top_group = row_groups[0]
    bottom_group = row_groups[-1]
    if left_group[0] > max(4, int(round(width * 0.04))):
        return None
    if top_group[0] > max(4, int(round(height * 0.04))):
        return None
    if right_group[1] < width - max(5, int(round(width * 0.04))):
        return None
    if bottom_group[1] < height - max(5, int(round(height * 0.04))):
        return None
    x0 = left_group[1] + 1
    y0 = top_group[1] + 1
    x1 = right_group[0]
    y1 = bottom_group[0]
    if x1 - x0 < max(64, int(round(width * 0.72))) or y1 - y0 < max(64, int(round(height * 0.72))):
        return None
    crop = _square_crop_around_box(image, x0, y0, x1, y1)
    if min(crop.size) >= min(image.size) * 0.98:
        return None
    return crop


def _recognition_caption_bottom_trim_crop(image: Image.Image) -> Image.Image | None:
    width, height = image.size
    min_axis = min(width, height)
    if min_axis < 96:
        return None
    baseline_detected, baseline_signal = _has_board_visual_pattern(image)
    baseline_grid = _estimate_board_grid_confidence(image) if baseline_detected else 0.0
    baseline_score = baseline_signal * 2.0 + baseline_grid
    best: tuple[float, Image.Image] | None = None
    for ratio in (0.06, 0.08, 0.10, 0.12):
        cut = int(round(height * ratio))
        if cut <= 0:
            continue
        bottom_band = np.array(image.crop((0, height - cut, width, height)), dtype=np.uint8)
        if bottom_band.size == 0 or float((bottom_band < 176).mean()) < 0.04:
            continue
        side = min(width, height - cut)
        if side < min_axis * 0.82:
            continue
        left = max(0, (width - side) // 2)
        crop = image.crop((left, 0, left + side, side))
        detected, signal = _has_board_visual_pattern(crop)
        if not detected:
            continue
        grid = _estimate_board_grid_confidence(crop)
        score = signal * 2.0 + grid - ratio * 0.12
        if score < baseline_score + MIN_RECOGNITION_TRIM_SCORE_GAIN:
            continue
        candidate = (score, crop)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    return best[1]


def _recognition_corner_marker_trim_crop(image: Image.Image) -> Image.Image | None:
    width, height = image.size
    min_axis = min(width, height)
    if min_axis < 96:
        return None
    probes = (
        image.crop((0, 0, int(round(width * 0.24)), int(round(height * 0.24)))),
        image.crop((int(round(width * 0.76)), 0, width, int(round(height * 0.24)))),
    )
    if not any(
        probe.size and float((np.array(probe, dtype=np.uint8) < 164).mean()) >= 0.05
        for probe in probes
    ):
        return None
    baseline_detected, baseline_signal = _has_board_visual_pattern(image)
    baseline_grid = _estimate_board_grid_confidence(image) if baseline_detected else 0.0
    baseline_score = baseline_signal * 2.0 + baseline_grid
    best: tuple[float, Image.Image] | None = None
    for crop, ratio in _recognition_marker_trim_candidates(image, anchor_modes=("top_left", "top_right")):
        if min(crop.size) < min_axis * 0.84:
            continue
        detected, signal = _has_board_visual_pattern(crop)
        if not detected:
            continue
        grid = _estimate_board_grid_confidence(crop)
        score = signal * 2.0 + grid - ratio * 0.10
        if score < baseline_score + MIN_RECOGNITION_TRIM_SCORE_GAIN:
            continue
        candidate = (score, crop)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    return best[1]


def _recognition_side_marker_trim_crop(image: Image.Image) -> Image.Image | None:
    width, height = image.size
    min_axis = min(width, height)
    if min_axis < 96:
        return None
    left_probe = np.array(image.crop((0, 0, int(round(width * 0.16)), height)), dtype=np.uint8)
    right_probe = np.array(image.crop((int(round(width * 0.84)), 0, width, height)), dtype=np.uint8)
    if not any(
        probe.size and float((probe < 164).mean()) >= 0.05
        for probe in (left_probe, right_probe)
    ):
        return None
    baseline_detected, baseline_signal = _has_board_visual_pattern(image)
    baseline_grid = _estimate_board_grid_confidence(image) if baseline_detected else 0.0
    baseline_score = baseline_signal * 2.0 + baseline_grid
    best: tuple[float, Image.Image] | None = None
    for crop, ratio in _recognition_marker_trim_candidates(image, anchor_modes=("left_side", "right_side")):
        if min(crop.size) < min_axis * 0.84:
            continue
        detected, signal = _has_board_visual_pattern(crop)
        if not detected:
            continue
        grid = _estimate_board_grid_confidence(crop)
        score = signal * 2.0 + grid - ratio * 0.08
        if score < baseline_score + MIN_RECOGNITION_TRIM_SCORE_GAIN:
            continue
        candidate = (score, crop)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        return None
    return best[1]


def _recognition_marker_trim_candidates(
    image: Image.Image,
    *,
    anchor_modes: Iterable[str],
) -> list[tuple[Image.Image, float]]:
    width, height = image.size
    candidates: list[tuple[Image.Image, float]] = []
    seen_boxes: set[tuple[int, int, int, int]] = set()
    for ratio in (0.04, 0.06, 0.08):
        cut_x = int(round(width * ratio))
        cut_y = int(round(height * ratio))
        for anchor in anchor_modes:
            box: tuple[int, int, int, int] | None = None
            if anchor == "right_side":
                side = min(width - cut_x, height)
                if side > 0:
                    box = (0, 0, side, side)
            elif anchor == "left_side":
                side = min(width - cut_x, height)
                if side > 0:
                    box = (cut_x, 0, cut_x + side, side)
            elif anchor == "top_right":
                side = min(width - cut_x, height - cut_y)
                if side > 0:
                    box = (0, cut_y, side, cut_y + side)
            elif anchor == "top_left":
                side = min(width - cut_x, height - cut_y)
                if side > 0:
                    box = (cut_x, cut_y, cut_x + side, cut_y + side)
            if box is None:
                continue
            if box in seen_boxes:
                continue
            seen_boxes.add(box)
            candidates.append((image.crop(box), ratio))
    return candidates


def _checkerboard_inner_square_crop(image: Image.Image) -> Image.Image | None:
    """Find the actual board square inside crops that include coordinates/captions.

    Many scanned chess books export a board plus file/rank labels and a caption
    as one image. A simple center square then slides the 8x8 grid into the
    labels, especially on the a/h files and first/eighth ranks. This bounded
    scan keeps the crop conservative: only accept an inner square when the
    checkerboard signal is materially stronger than the baseline center crop.
    """
    width, height = image.size
    min_axis = min(width, height)
    if min_axis < 96:
        return None

    scale = min(1.0, 220.0 / float(max(width, height)))
    if scale < 1.0:
        probe = image.resize((max(1, int(round(width * scale))), max(1, int(round(height * scale)))), Image.Resampling.BILINEAR)
    else:
        probe = image
    probe_width, probe_height = probe.size
    probe_min_axis = min(probe_width, probe_height)

    baseline_side = probe_min_axis
    baseline_left = max(0, (probe_width - baseline_side) // 2)
    baseline_top = max(0, (probe_height - baseline_side) // 2)
    baseline_crop = probe.crop((baseline_left, baseline_top, baseline_left + baseline_side, baseline_top + baseline_side))
    baseline_detected, baseline_signal = _has_board_visual_pattern(baseline_crop)
    baseline_grid = _estimate_board_grid_confidence(baseline_crop) if baseline_detected else 0.0
    baseline_score = baseline_signal * 2.0 + baseline_grid
    if baseline_grid >= MIN_ACCEPTED_TEMPLATE_GRID_WITHOUT_DENSE_CROP:
        return None

    side_values = sorted(
        {
            int(round(probe_min_axis * ratio))
            for ratio in (0.70, 0.80, 0.88, 0.94)
            if int(round(probe_min_axis * ratio)) >= 64
        }
    )
    best: tuple[float, int, int, int, float, float] | None = None
    for side in side_values:
        x_stride = max(8, side // 8)
        y_stride = max(8, side // 8)
        for top in range(0, max(1, probe_height - side + 1), y_stride):
            for left in range(0, max(1, probe_width - side + 1), x_stride):
                crop = probe.crop((left, top, left + side, top + side))
                detected, signal = _has_board_visual_pattern(crop)
                if not detected or signal < MIN_INNER_CHECKERBOARD_CROP_SIGNAL:
                    continue
                grid = _estimate_board_grid_confidence(crop)
                if grid < 0.40:
                    continue
                center_penalty = abs((left + side / 2.0) - probe_width / 2.0) / max(1.0, probe_width) * 0.15
                # Captions usually sit below the board, so slightly prefer a
                # high crop when scores are otherwise comparable.
                top_penalty = top / max(1.0, probe_height) * 0.05
                score = signal * 2.0 + grid - center_penalty - top_penalty
                if best is None or score > best[0]:
                    best = (score, left, top, side, signal, grid)

    if best is None:
        return None
    score, left, top, side, signal, _grid = best
    if signal < MIN_INNER_CHECKERBOARD_CROP_SIGNAL:
        return None
    if score < baseline_score + MIN_INNER_CHECKERBOARD_CROP_GAIN:
        return None
    if scale <= 0:
        return None
    original_left = int(round(left / scale))
    original_top = int(round(top / scale))
    original_side = int(round(side / scale))
    original_left = min(max(0, original_left), max(0, width - original_side))
    original_top = min(max(0, original_top), max(0, height - original_side))
    if original_side >= min_axis * 0.90:
        return None
    return image.crop((original_left, original_top, original_left + original_side, original_top + original_side))


def _dominant_board_content_square_crop(image: Image.Image) -> Image.Image | None:
    """Crop the dominant board-like content area from captioned diagram images.

    Some scan extractors hand us a square image that contains the board in the
    upper part plus coordinate labels and a caption below it. Center-cropping
    that square keeps the caption in the bottom rank and can turn caption text
    into high-confidence false pieces. This projection crop is deliberately
    conservative: it only accepts a large near-square content component that
    still has a chessboard visual pattern.
    """
    width, height = image.size
    min_axis = min(width, height)
    if min_axis < 96:
        return None

    pixels = np.array(image, dtype=np.uint8)
    if pixels.size == 0:
        return None
    dark = pixels < 180
    row_density = dark.mean(axis=1)
    col_density = dark.mean(axis=0)
    min_group_len = max(32, int(round(min_axis * 0.35)))
    row_group = _dominant_projection_group(row_density, threshold=0.03, min_length=min_group_len)
    col_group = _dominant_projection_group(col_density, threshold=0.05, min_length=min_group_len)
    if row_group is None or col_group is None:
        return None

    y0, y1 = row_group
    x0, x1 = col_group
    group_width = x1 - x0
    group_height = y1 - y0
    if group_width <= 0 or group_height <= 0:
        return None
    ratio = group_width / max(1.0, float(group_height))
    if ratio < 0.78 or ratio > 1.28:
        return None

    side = int(round(max(group_width, group_height)))
    if side < min_group_len:
        return None
    # Keep the crop idempotent. Recognition normalizes once before template
    # matching and cell classification normalizes again; a second pass must not
    # shave a nearly full board down to only its densest piece area.
    if side >= min_axis * 0.90:
        return None

    center_x = (x0 + x1) / 2.0
    center_y = (y0 + y1) / 2.0
    left = int(round(center_x - side / 2.0))
    top = int(round(center_y - side / 2.0))
    left = min(max(0, left), max(0, width - side))
    top = min(max(0, top), max(0, height - side))
    crop = image.crop((left, top, left + side, top + side))

    detected, signal = _has_board_visual_pattern(crop)
    if not detected or signal < MIN_DOMINANT_CONTENT_CROP_SIGNAL:
        return None
    grid = _estimate_board_grid_confidence(crop)
    if grid < MIN_DOMINANT_CONTENT_CROP_GRID:
        return None

    baseline_side = min_axis
    baseline_left = max(0, (width - baseline_side) // 2)
    baseline_top = max(0, (height - baseline_side) // 2)
    baseline_crop = image.crop((baseline_left, baseline_top, baseline_left + baseline_side, baseline_top + baseline_side))
    baseline_detected, baseline_signal = _has_board_visual_pattern(baseline_crop)
    baseline_grid = _estimate_board_grid_confidence(baseline_crop) if baseline_detected else 0.0
    if signal * 2.0 + grid < baseline_signal * 2.0 + baseline_grid - 0.08:
        return None
    return crop


def _dominant_projection_group(values: np.ndarray, *, threshold: float, min_length: int) -> tuple[int, int] | None:
    groups = _dense_projection_groups(values, threshold=threshold)
    candidates = [(start, end) for start, end in groups if end - start >= min_length]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1] - item[0])


def _strong_border_square_crop(image: Image.Image) -> Image.Image | None:
    pixels = np.array(image, dtype=np.uint8)
    if pixels.size == 0:
        return None
    dark = pixels < 80
    col_density = dark.mean(axis=0)
    row_density = dark.mean(axis=1)
    col_groups = _dense_projection_groups(col_density, threshold=0.42)
    row_groups = _dense_projection_groups(row_density, threshold=0.42)
    if len(col_groups) < 2 or len(row_groups) < 2:
        return None

    left_group = col_groups[0]
    right_group = col_groups[-1]
    top_group = row_groups[0]
    bottom_group = row_groups[-1]
    # Use the inside of the border lines for piece recognition. Border lines
    # are useful for locating the board but hurt template matching if they
    # become part of the a/h files or first/eighth ranks.
    x0, x1 = left_group[1] + 1, right_group[0]
    y0, y1 = top_group[1] + 1, bottom_group[0]
    width = x1 - x0
    height = y1 - y0
    if width < 64 or height < 64:
        return None
    min_axis = min(image.size)
    if width < min_axis * 0.70 or height < min_axis * 0.70:
        return None
    ratio = width / float(max(height, 1))
    if not (0.82 <= ratio <= 1.18):
        return None
    return _square_crop_around_box(image, x0, y0, x1, y1)


def _dense_projection_groups(values: np.ndarray, *, threshold: float) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(values):
        if float(value) >= threshold:
            if start is None:
                start = index
        elif start is not None:
            if index - start >= 2:
                groups.append((start, index - 1))
            start = None
    if start is not None and len(values) - start >= 2:
        groups.append((start, len(values) - 1))
    return groups


def _dense_board_area_crop(image: Image.Image) -> Image.Image | None:
    """Crop the actual board area when coordinates/captions are included.

    Some scan crops include `a-h`, rank labels, or a diagram caption below/above
    the board. Center-cropping those rectangles shifts the 8x8 grid into the
    labels and produces plausible but invalid king counts. The shaded board
    itself has a large contiguous dark-pixel projection; text captions do not.
    """
    pixels = np.array(image, dtype=np.uint8)
    if pixels.size == 0:
        return None
    dark = pixels < 80
    row_groups = _merge_close_projection_groups(_dense_projection_groups(dark.mean(axis=1), threshold=0.10), max_gap=10)
    col_groups = _merge_close_projection_groups(_dense_projection_groups(dark.mean(axis=0), threshold=0.10), max_gap=10)
    if not row_groups or not col_groups:
        return None

    min_axis = min(image.size)
    best: tuple[float, int, int, int, int] | None = None
    for x0, x1 in sorted(col_groups, key=lambda item: item[1] - item[0], reverse=True)[:3]:
        width = x1 - x0 + 1
        if width < max(64, int(min_axis * 0.55)):
            continue
        for y0, y1 in row_groups:
            height = y1 - y0 + 1
            if height < width * 0.55:
                continue
            ratio = width / float(max(height, 1))
            if not (0.75 <= ratio <= 1.40):
                continue
            score = min(width, height) - abs(width - height) * 0.3
            if best is None or score > best[0]:
                best = (score, x0, y0, x1, y1)

    if best is None:
        return None
    _, x0, y0, x1, y1 = best
    width = x1 - x0 + 1
    height = y1 - y0 + 1
    if width < 64 or height < 64:
        return None
    side = max(width, height)
    return ImageOps.autocontrast(image.crop((x0, y0, x1 + 1, y1 + 1)).resize((side, side), Image.Resampling.BILINEAR))


def _merge_close_projection_groups(groups: list[tuple[int, int]], *, max_gap: int) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in groups:
        if merged and start - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _square_crop_around_box(image: Image.Image, x0: int, y0: int, x1: int, y1: int) -> Image.Image:
    width = max(1, x1 - x0)
    height = max(1, y1 - y0)
    side = max(width, height)
    center_x = (x0 + x1) / 2.0
    center_y = (y0 + y1) / 2.0
    left = int(round(center_x - side / 2.0))
    top = int(round(center_y - side / 2.0))
    left = min(max(0, left), max(0, image.width - side))
    top = min(max(0, top), max(0, image.height - side))
    return image.crop((left, top, left + side, top + side))


def _match_piece_template(
    cell: np.ndarray,
    templates: Mapping[str, np.ndarray],
) -> tuple[str, float]:
    label, confidence, _alternatives = _match_piece_template_with_alternatives(cell, templates)
    return label, confidence


def _match_piece_template_with_alternatives(
    cell: np.ndarray,
    templates: Mapping[str, np.ndarray],
    *,
    top_n: int = 3,
) -> tuple[str, float, list[dict[str, Any]]]:
    best_label = ""
    best_error = float("inf")
    second_error = float("inf")
    empty_error = float("inf")
    label_errors: list[tuple[str, float]] = []
    for label, variants in templates.items():
        if variants.size == 0:
            continue
        errors = np.mean((variants - cell) ** 2, axis=(1, 2))
        label_best = float(errors.min())
        label_errors.append((label, label_best))
        if label == "":
            empty_error = min(empty_error, label_best)
        if errors.size >= 2:
            label_second = float(np.partition(errors, 1)[1])
        else:
            label_second = float("inf")
        if label_best < best_error:
            second_error = min(best_error, label_second)
            best_error = label_best
            best_label = label
        elif label_best < second_error:
            second_error = label_best
    if best_label and empty_error < float("inf") and empty_error - best_error < MIN_EMPTY_VS_PIECE_ERROR_MARGIN:
        best_label = ""
        best_error = empty_error
    confidence = max(0.0, min(1.0, 1.0 - best_error * 4.0))
    if second_error < float("inf") and second_error - best_error < 0.006:
        confidence *= 0.92
    alternatives = []
    for label, error in sorted(label_errors, key=lambda item: (item[1], item[0]))[: max(1, int(top_n or 1))]:
        alternatives.append(
            {
                "piece": label,
                "confidence": round(max(0.0, min(1.0, 1.0 - error * 4.0)), 3),
            }
        )
    return best_label, confidence, alternatives


def _looks_like_non_piece_cross_marker(cell: np.ndarray) -> bool:
    """Detect instructional X/cross annotations that are not chess pieces.

    Scanned puzzle books sometimes draw a large X over a target square. With a
    sparse template set, that mark can look like a low-confidence rook/queen.
    Only suppress low-confidence non-king matches; real pieces with a strong
    template score still win.
    """
    if cell.size == 0:
        return False
    mask = np.asarray(cell) > 0.28
    density = float(mask.mean())
    if density < 0.16 or density > 0.48:
        return False
    height, width = mask.shape
    if height < 12 or width < 12:
        return False
    diag_a = _diagonal_band_density(mask, slope=1)
    diag_b = _diagonal_band_density(mask, slope=-1)
    return min(diag_a, diag_b) >= 0.45 and max(diag_a, diag_b) >= 0.50


def _looks_like_low_confidence_dark_queen_ambiguity(
    label: str,
    confidence: float,
    cell: np.ndarray,
    templates: Mapping[str, np.ndarray],
) -> bool:
    """Suppress risky Q/q color ambiguity rather than publishing a false FEN.

    The Merida-like scan style can make a clipped black queen resemble a white
    queen by outline shape. Rewriting Q->q would be too aggressive, so this
    only abstains for low-confidence white-queen matches whose foreground mass
    looks more like a filled dark queen.
    """
    if label != "Q" or "q" not in templates or confidence >= MAX_DARK_QUEEN_AMBIGUITY_CONFIDENCE:
        return False
    foreground_mean = float(np.asarray(cell).mean())
    density = float((np.asarray(cell) > 0.35).mean())
    return (
        foreground_mean >= MIN_DARK_QUEEN_AMBIGUITY_FOREGROUND_MEAN
        and density >= MIN_DARK_QUEEN_AMBIGUITY_DENSITY
    )


def _diagonal_band_density(mask: np.ndarray, *, slope: int) -> float:
    height, width = mask.shape
    total = 0
    hits = 0
    for y in range(height):
        base_x = int(round(y * (width - 1) / max(1, height - 1)))
        if slope < 0:
            base_x = width - 1 - base_x
        for offset in (-1, 0, 1):
            x = base_x + offset
            if 0 <= x < width:
                total += 1
                hits += int(bool(mask[y, x]))
    return hits / float(max(1, total))


def _normalize_piece_cell(image: Image.Image) -> np.ndarray:
    cell = image.convert("L").resize((TEMPLATE_CELL_SIZE, TEMPLATE_CELL_SIZE), Image.Resampling.BILINEAR)
    values = np.array(cell, dtype=np.float32) / 255.0
    background = float(np.median(values))
    foreground = np.clip(background - values, 0.0, 1.0)
    max_value = float(foreground.max())
    if max_value < 0.04:
        return np.zeros((TEMPLATE_CELL_SIZE, TEMPLATE_CELL_SIZE), dtype=np.float32)
    return foreground / max_value


def _coerce_template_image(source: Any) -> Image.Image | None:
    if isinstance(source, Image.Image):
        return source.convert("L")
    if isinstance(source, (bytes, bytearray)):
        try:
            return Image.open(io.BytesIO(source)).convert("L")
        except Exception:
            return None
    if isinstance(source, (str, Path)):
        try:
            return Image.open(source).convert("L")
        except Exception:
            return None
    return None


def _template_label_from_path(path: Path) -> str | None:
    stem = path.stem.strip()
    if not stem:
        return None
    first = re.split(r"[_\-. ]+", stem, maxsplit=1)[0]
    return _normalize_template_label(first)


def _normalize_template_label(label: str) -> str | None:
    normalized = str(label or "").strip()
    if normalized.lower() in {"", "empty", "blank", "none", "vacant"}:
        return ""
    if normalized in PIECE_CHARS:
        return normalized
    return None


def _decode_board_row(text: str) -> tuple[list[str] | None, str]:
    cleaned = _strip_board_border(text)
    if not cleaned:
        return None, "empty_or_border_row"
    if "/" in cleaned:
        return None, "compound_board_row"

    expanded = _expand_fen_rank(cleaned)
    if expanded is not None:
        return expanded, ""

    tokens = [token for token in cleaned if not token.isspace()]
    cells: list[str] = []
    unknown_chess_glyph = False
    for token in tokens:
        piece = _piece_from_token(token)
        if piece is None:
            if _looks_like_unknown_chess_glyph(token):
                unknown_chess_glyph = True
            continue
        cells.append(piece)
        if len(cells) == 8:
            break
    if len(cells) == 8:
        return cells, ""
    return None, "unknown_chess_font_row" if unknown_chess_glyph else "row_not_decodable"


def _strip_board_border(text: str) -> str:
    cleaned = str(text or "").strip().replace("\n", "")
    cleaned = re.sub(r"^[1-8]\s+", "", cleaned)
    cleaned = re.sub(r"\s+[1-8]$", "", cleaned)
    cleaned = re.sub(r"^[a-h](?:\s+[a-h])+$", "", cleaned, flags=re.IGNORECASE)
    compact = re.sub(r"\s+", "", cleaned)
    if len(compact) == 9 and compact[0] in "12345678":
        cleaned = compact[1:]
    return cleaned.strip()


def _expand_fen_rank(value: str) -> list[str] | None:
    compact = re.sub(r"\s+", "", value)
    if not compact or not re.fullmatch(r"[pnbrqkPNBRQK1-8]+", compact):
        return None
    row: list[str] = []
    for char in compact:
        if char.isdigit():
            row.extend([""] * int(char))
        else:
            row.append(char)
    return row if len(row) == 8 else None


def _piece_from_token(token: str) -> str | None:
    if token in SKAKNEW_DIAGRAM_PIECES:
        return SKAKNEW_DIAGRAM_PIECES[token]
    if token in PIECE_CHARS:
        return token
    if token in UNICODE_PIECES:
        return UNICODE_PIECES[token]
    if token in FIGURINE_FONT_PIECES:
        return FIGURINE_FONT_PIECES[token]
    if token in EMPTY_CELL_CHARS:
        return ""
    return None


def _looks_like_unknown_chess_glyph(token: str) -> bool:
    if not token:
        return False
    code = ord(token[0])
    return 0xE000 <= code <= 0xF8FF or 0xF000 <= code <= 0xF0FF


def _estimate_board_grid_confidence(image: Image.Image) -> float:
    normalized = _normalize_board_signal_image(image)
    pixels = np.array(normalized, dtype=np.float32)
    if pixels.shape != (160, 160):
        return 0.0
    cell_values = []
    for row in range(8):
        for col in range(8):
            y0, y1 = row * 20 + 5, row * 20 + 15
            x0, x1 = col * 20 + 5, col * 20 + 15
            cell_values.append(float(np.mean(pixels[y0:y1, x0:x1])))
    cells = np.array(cell_values, dtype=np.float32).reshape(8, 8)
    light = cells[(np.indices((8, 8)).sum(axis=0) % 2) == 0]
    dark = cells[(np.indices((8, 8)).sum(axis=0) % 2) == 1]
    contrast = abs(float(light.mean()) - float(dark.mean())) / 255.0
    consistency = 1.0 - min((float(light.std()) + float(dark.std())) / 180.0, 1.0)
    edges = normalized.filter(ImageFilter.FIND_EDGES)
    edge_density = float(np.mean(np.array(edges, dtype=np.float32) > 18))
    grid_bonus = min(edge_density * 1.5, 0.25)
    return max(0.0, min(1.0, contrast * 0.65 + consistency * 0.25 + grid_bonus))


def _estimate_board_alternation_signal(image: Image.Image) -> float:
    """Estimate whether a crop has local 8x8 checkerboard structure.

    Text blocks can be square and edge-dense enough to pass the coarse board
    locator. A real chess board still has many neighboring 2x2 cells with an
    alternating light/dark pattern, even when pieces and coordinates obscure
    the global checkerboard contrast.
    """
    normalized = _normalize_board_signal_image(image)
    pixels = np.array(normalized, dtype=np.float32)
    if pixels.shape != (160, 160):
        return 0.0

    values: list[float] = []
    for row in range(8):
        for col in range(8):
            y0, y1 = row * 20 + 5, row * 20 + 15
            x0, x1 = col * 20 + 5, col * 20 + 15
            values.append(float(np.mean(pixels[y0:y1, x0:x1])))
    cells = np.array(values, dtype=np.float32).reshape(8, 8)
    signs = np.where((np.indices((8, 8)).sum(axis=0) % 2) == 0, 1.0, -1.0).astype(np.float32)

    local_scores: list[float] = []
    for row in range(7):
        for col in range(7):
            block = cells[row : row + 2, col : col + 2]
            block_signs = signs[row : row + 2, col : col + 2]
            centered = block - float(block.mean())
            denominator = float(np.sqrt(np.sum(centered**2)) * np.sqrt(np.sum(block_signs**2)))
            if denominator:
                local_scores.append(abs(float(np.sum(centered * block_signs)) / denominator))
    if not local_scores:
        return 0.0
    return max(0.0, min(1.0, float(np.mean(local_scores))))


def _has_board_visual_pattern(image: Image.Image) -> tuple[bool, float]:
    """Return whether a crop looks like a real chessboard, not only a square text block."""
    alternation = _estimate_board_alternation_signal(image)
    cell_std, cell_range = _estimate_board_cell_tone_stats(image)
    tone_score = min(
        cell_std / MIN_BOARD_CELL_TONE_STD if MIN_BOARD_CELL_TONE_STD else 0.0,
        cell_range / MIN_BOARD_CELL_TONE_RANGE if MIN_BOARD_CELL_TONE_RANGE else 0.0,
        1.0,
    )
    score = min(alternation, tone_score)
    return (
        alternation >= MIN_BOARD_ALTERNATION_SIGNAL
        and cell_std >= MIN_BOARD_CELL_TONE_STD
        and cell_range >= MIN_BOARD_CELL_TONE_RANGE,
        max(0.0, min(1.0, score)),
    )


def _estimate_board_cell_tone_stats(image: Image.Image) -> tuple[float, float]:
    """Measure 8x8 cell-tone dispersion.

    Text false positives often have enough edge density and local rhythm to
    mimic a board, but their 8x8 cell means stay clustered because most cells
    are white page background. Real checkerboards have much larger tone spread.
    """
    normalized = _normalize_board_signal_image(image)
    pixels = np.array(normalized, dtype=np.float32)
    if pixels.shape != (160, 160):
        return 0.0, 0.0

    values: list[float] = []
    for row in range(8):
        for col in range(8):
            y0, y1 = row * 20 + 5, row * 20 + 15
            x0, x1 = col * 20 + 5, col * 20 + 15
            values.append(float(np.mean(pixels[y0:y1, x0:x1])))
    if not values:
        return 0.0, 0.0
    return float(np.std(values)), float(max(values) - min(values))


def _normalize_board_signal_image(image: Image.Image) -> Image.Image:
    """Return a stable grayscale image for board-signal heuristics.

    Review/export crops are intentionally saved as 1-bit PNGs to keep EPUBs
    compact. Pillow's autocontrast LUT does not support mode "1", so convert
    before resizing to keep runtime and offline analysis on the same path.
    """
    return ImageOps.autocontrast(image.convert("L").resize((160, 160), Image.Resampling.BILINEAR)).filter(
        ImageFilter.MedianFilter(size=3)
    )


def _regular_grid_square_candidates(image: Image.Image, *, scale: float) -> list[tuple[float, float, float, float]]:
    normalized = ImageOps.autocontrast(image.convert("L"))
    edge_pixels = np.array(normalized.filter(ImageFilter.FIND_EDGES), dtype=np.uint8)
    if edge_pixels.size == 0:
        return []

    mask = edge_pixels > 25
    if not np.any(mask):
        return []

    min_axis = min(image.size)
    min_distance = max(4, int(min_axis * 0.01))
    x_peaks = _projection_peaks(mask.mean(axis=0), min_distance=min_distance, threshold_ratio=0.35)
    y_peaks = _projection_peaks(mask.mean(axis=1), min_distance=min_distance, threshold_ratio=0.35)
    if len(x_peaks) < 7 or len(y_peaks) < 7:
        return []

    min_step = max(8, int(min_axis * 0.015))
    max_step = max(min_step + 1, int(min_axis * 0.14))
    x_intervals = _regular_grid_intervals(x_peaks, min_step=min_step, max_step=max_step)
    y_intervals = _regular_grid_intervals(y_peaks, min_step=min_step, max_step=max_step)

    candidates: list[tuple[float, tuple[float, float, float, float]]] = []
    page_area = float(max(image.width * image.height, 1))
    for x_hits, x0, x8, x_step in x_intervals:
        for y_hits, y0, y8, y_step in y_intervals:
            side_x = float(x8 - x0)
            side_y = float(y8 - y0)
            if side_x <= 0 or side_y <= 0:
                continue
            ratio = side_x / side_y
            area_ratio = (side_x * side_y) / page_area
            if not (0.78 <= ratio <= 1.22 and 0.004 <= area_ratio <= 0.35):
                continue
            pad = max(1.0, min(side_x, side_y) * 0.006)
            bbox = (
                max(0.0, x0 - pad) / scale,
                max(0.0, y0 - pad) / scale,
                min(float(image.width), x8 + pad) / scale,
                min(float(image.height), y8 + pad) / scale,
            )
            score = float(x_hits + y_hits) + min(x_step, y_step) * 0.01
            candidates.append((score, bbox))

    candidates.sort(key=lambda item: item[0], reverse=True)
    deduped: list[tuple[float, float, float, float]] = []
    for _, bbox in candidates:
        if any(_bbox_overlap_ratio(bbox, existing) > 0.55 for existing in deduped):
            continue
        deduped.append(bbox)
        if len(deduped) >= 12:
            break
    return deduped


def _border_line_square_candidates(image: Image.Image, *, scale: float) -> list[tuple[float, float, float, float]]:
    """Find full board boxes from visible outer border lines.

    Projection/grid candidates can lock onto only part of a board when nearby
    coordinates and captions complete a square-looking component. Outer border
    lines, when visible, are a stronger signal for the full 8x8 board extent.
    """
    normalized = ImageOps.autocontrast(image.convert("L"))
    pixels = np.array(normalized, dtype=np.uint8)
    if pixels.size == 0:
        return []

    dark = pixels < 96
    row_groups = _projection_line_groups(dark.mean(axis=1), threshold=0.18)
    col_groups = _projection_line_groups(dark.mean(axis=0), threshold=0.18)
    if len(row_groups) < 2 or len(col_groups) < 2:
        return []

    min_axis = min(image.size)
    min_side = max(40.0, min_axis * 0.10)
    max_side = max(min_side, min_axis * 0.62)
    page_area = float(max(image.width * image.height, 1))
    x_pairs = _border_axis_pairs(col_groups, min_side=min_side, max_side=max_side)
    y_pairs = _border_axis_pairs(row_groups, min_side=min_side, max_side=max_side)
    candidates: list[tuple[float, tuple[float, float, float, float]]] = []
    for x_score, x0, x1, side_x in x_pairs:
        for y_score, y0, y1, side_y in y_pairs:
            ratio = side_x / float(max(side_y, 1.0))
            area_ratio = (side_x * side_y) / page_area
            if not (0.86 <= ratio <= 1.14 and 0.004 <= area_ratio <= 0.28):
                continue
            square_bbox = _square_bbox_around_coordinates(
                x0,
                y0,
                x1,
                y1,
                image_width=float(image.width),
                image_height=float(image.height),
            )
            score = float(x_score + y_score) + min(side_x, side_y) * 0.003 - abs(side_x - side_y) * 0.01
            candidates.append(
                (
                    score,
                    tuple(value / scale for value in square_bbox),
                )
            )

    candidates.sort(key=lambda item: item[0], reverse=True)
    deduped: list[tuple[float, float, float, float]] = []
    for _, bbox in candidates:
        if any(_bbox_overlap_ratio(bbox, existing) > 0.55 for existing in deduped):
            continue
        deduped.append(bbox)
        if len(deduped) >= 12:
            break
    return deduped


def _refine_page_candidates_with_border_lines(
    image: Image.Image,
    probe: Image.Image,
    *,
    scale: float,
    candidates: list[ChessFenResult],
) -> list[ChessFenResult]:
    if not candidates:
        return candidates

    border_bboxes = _border_line_square_candidates(probe, scale=scale)
    refined: list[ChessFenResult] = []
    for candidate in candidates:
        if not candidate.bbox:
            refined.append(candidate)
            continue
        best: tuple[float, tuple[float, float, float, float], float] | None = None
        for border_bbox in border_bboxes:
            if not _border_refinement_is_local(candidate.bbox, border_bbox):
                continue
            overlap = _bbox_overlap_ratio(candidate.bbox, border_bbox)
            if overlap < 0.45:
                continue
            crop = image.crop(border_bbox)
            if not _has_board_visual_pattern(crop)[0]:
                continue
            confidence = _estimate_board_grid_confidence(crop)
            if confidence < 0.42:
                continue
            score = overlap + confidence * 0.25
            if best is None or score > best[0]:
                best = (score, border_bbox, confidence)
        if best is None:
            refined.append(candidate)
            continue
        _, bbox, confidence = best
        refined.append(
            ChessFenResult(
                confidence=max(candidate.confidence, confidence),
                bbox=bbox,
                method="image-page-board-border-refined",
                warnings=list(candidate.warnings),
                requires_review=candidate.requires_review,
                board_detected=candidate.board_detected,
            )
        )

    for border_bbox in border_bboxes:
        if any(existing.bbox and _bbox_overlap_ratio(border_bbox, existing.bbox) > 0.90 for existing in refined):
            continue
        crop = image.crop(border_bbox)
        if not _has_board_visual_pattern(crop)[0]:
            continue
        confidence = _estimate_board_grid_confidence(crop)
        if confidence < 0.42:
            continue
        refined.append(
            ChessFenResult(
                confidence=confidence,
                bbox=border_bbox,
                method="image-page-board-border",
                warnings=["piece_templates_unavailable", "image_board_requires_review", "side_to_move_inferred"],
                requires_review=True,
                board_detected=True,
            )
        )

    refined.sort(key=lambda item: item.confidence, reverse=True)
    deduped: list[ChessFenResult] = []
    for candidate in refined:
        if candidate.bbox and any(existing.bbox and _bbox_overlap_ratio(candidate.bbox, existing.bbox) > 0.90 for existing in deduped):
            continue
        deduped.append(candidate)
    return deduped


def _border_refinement_is_local(
    original: tuple[float, float, float, float],
    refined: tuple[float, float, float, float],
) -> bool:
    """Only allow border lines to make a small local crop correction.

    Border-line projections are useful when coordinates/captions make the board
    crop a few pixels too small. They are unsafe when the candidate is shifted to
    a neighboring partial board or when the border box cuts away much of the
    original candidate.
    """
    ox0, oy0, ox1, oy1 = original
    rx0, ry0, rx1, ry1 = refined
    ow = max(1.0, ox1 - ox0)
    oh = max(1.0, oy1 - oy0)
    rw = max(1.0, rx1 - rx0)
    rh = max(1.0, ry1 - ry0)
    original_side = max(ow, oh)
    refined_side = max(rw, rh)
    side_ratio = refined_side / original_side
    if not (0.90 <= side_ratio <= 1.18):
        return False
    original_area = ow * oh
    refined_area = rw * rh
    area_ratio = refined_area / max(1.0, original_area)
    if not (0.92 <= area_ratio <= 1.25):
        return False
    original_center = ((ox0 + ox1) * 0.5, (oy0 + oy1) * 0.5)
    refined_center = ((rx0 + rx1) * 0.5, (ry0 + ry1) * 0.5)
    max_center_shift = original_side * 0.12
    if abs(original_center[0] - refined_center[0]) > max_center_shift:
        return False
    if abs(original_center[1] - refined_center[1]) > max_center_shift:
        return False
    return True


def _square_bbox_around_coordinates(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    image_width: float,
    image_height: float,
) -> tuple[float, float, float, float]:
    """Expand a near-square border box without center-cropping edge files/ranks."""
    width = max(1.0, x1 - x0)
    height = max(1.0, y1 - y0)
    side = max(width, height)
    center_x = (x0 + x1) * 0.5
    center_y = (y0 + y1) * 0.5
    left = center_x - side * 0.5
    top = center_y - side * 0.5
    left = min(max(0.0, left), max(0.0, image_width - side))
    top = min(max(0.0, top), max(0.0, image_height - side))
    return (left, top, left + side, top + side)


def _border_axis_pairs(
    groups: list[tuple[int, int, float]],
    *,
    min_side: float,
    max_side: float,
    limit: int = 36,
) -> list[tuple[float, float, float, float]]:
    pairs: list[tuple[float, float, float, float]] = []
    for first in groups:
        for second in groups:
            if second[0] <= first[1]:
                continue
            start = float(first[1] + 1)
            end = float(second[0])
            side = end - start
            if not (min_side <= side <= max_side):
                continue
            score = float(first[2] + second[2]) + side * 0.001
            pairs.append((score, start, end, side))
    pairs.sort(key=lambda item: item[0], reverse=True)
    return pairs[:limit]


def _projection_line_groups(values: np.ndarray, *, threshold: float) -> list[tuple[int, int, float]]:
    groups: list[tuple[int, int, float]] = []
    start: int | None = None
    max_value = 0.0
    for index, value in enumerate(values):
        numeric = float(value)
        if numeric >= threshold:
            if start is None:
                start = index
                max_value = numeric
            else:
                max_value = max(max_value, numeric)
        elif start is not None:
            groups.append((start, index - 1, max_value))
            start = None
            max_value = 0.0
    if start is not None:
        groups.append((start, len(values) - 1, max_value))
    return groups


def _edge_component_square_candidates(image: Image.Image, *, scale: float) -> list[tuple[float, float, float, float]]:
    """Find board-like dense square components without an expensive sliding scan.

    Scanned chess books often contain raster boards where the regular 8x8 line
    projections are broken by print noise, coordinates, and captions. Edge-block
    components are a fast, conservative pre-filter: they return likely square
    regions for review/cropping, while FEN still requires later deterministic
    piece recognition.
    """
    grayscale = ImageOps.autocontrast(image.convert("L"))
    edge_pixels = np.array(grayscale.filter(ImageFilter.FIND_EDGES), dtype=np.uint8)
    if edge_pixels.size == 0:
        return []

    edge_mask = edge_pixels > 28
    block_size = max(6, min(12, int(min(image.size) / 80) or 8))
    block_rows = edge_mask.shape[0] // block_size
    block_cols = edge_mask.shape[1] // block_size
    if block_rows <= 2 or block_cols <= 2:
        return []

    density = edge_mask[: block_rows * block_size, : block_cols * block_size].reshape(
        block_rows,
        block_size,
        block_cols,
        block_size,
    ).mean(axis=(1, 3))
    active = density > 0.12
    if not np.any(active):
        return []

    visited = np.zeros(active.shape, dtype=bool)
    components: list[tuple[int, tuple[float, float, float, float]]] = []
    for row in range(block_rows):
        for col in range(block_cols):
            if visited[row, col] or not active[row, col]:
                continue
            stack = [(row, col)]
            visited[row, col] = True
            rows: list[int] = []
            cols: list[int] = []
            while stack:
                current_row, current_col = stack.pop()
                rows.append(current_row)
                cols.append(current_col)
                for next_row in range(current_row - 1, current_row + 2):
                    for next_col in range(current_col - 1, current_col + 2):
                        if (
                            next_row < 0
                            or next_col < 0
                            or next_row >= block_rows
                            or next_col >= block_cols
                            or visited[next_row, next_col]
                            or not active[next_row, next_col]
                        ):
                            continue
                        visited[next_row, next_col] = True
                        stack.append((next_row, next_col))

            block_count = len(rows)
            if block_count < 12:
                continue
            x0 = min(cols) * block_size
            x1 = (max(cols) + 1) * block_size
            y0 = min(rows) * block_size
            y1 = (max(rows) + 1) * block_size
            width = x1 - x0
            height = y1 - y0
            if width <= 0 or height <= 0:
                continue
            ratio = width / float(height)
            area_ratio = (width * height) / float(max(image.width * image.height, 1))
            min_side = min(image.size) * 0.10
            max_side = min(image.size) * 0.58
            if not (0.72 <= ratio <= 1.18 and min_side <= width <= max_side and min_side <= height <= max_side):
                continue
            if not (0.006 <= area_ratio <= 0.24):
                continue
            pad = max(2.0, min(width, height) * 0.015)
            components.append(
                (
                    block_count,
                    (
                        max(0.0, x0 - pad) / scale,
                        max(0.0, y0 - pad) / scale,
                        min(float(image.width), x1 + pad) / scale,
                        min(float(image.height), y1 + pad) / scale,
                    ),
                )
            )

    components.sort(key=lambda item: item[0], reverse=True)
    deduped: list[tuple[float, float, float, float]] = []
    for _, bbox in components:
        if any(_bbox_overlap_ratio(bbox, existing) > 0.55 for existing in deduped):
            continue
        deduped.append(bbox)
        if len(deduped) >= 12:
            break
    return deduped


def _projection_peaks(
    projection: np.ndarray,
    *,
    min_distance: int,
    threshold_ratio: float,
) -> list[int]:
    values = np.asarray(projection, dtype=np.float32)
    if values.size < 3 or float(values.max()) <= 0.0:
        return []

    kernel_size = max(3, int(min_distance // 3) | 1)
    pad = kernel_size // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    smoothed = np.convolve(padded, np.ones(kernel_size, dtype=np.float32) / float(kernel_size), mode="valid")
    threshold = max(float(smoothed.mean() + smoothed.std() * 0.6), float(smoothed.max() * threshold_ratio))
    raw_peaks: list[tuple[float, int]] = []
    for idx in range(1, len(smoothed) - 1):
        value = float(smoothed[idx])
        if value >= threshold and value >= float(smoothed[idx - 1]) and value >= float(smoothed[idx + 1]):
            raw_peaks.append((value, idx))

    raw_peaks.sort(reverse=True)
    selected: list[int] = []
    for _, idx in raw_peaks:
        if all(abs(idx - existing) >= min_distance for existing in selected):
            selected.append(idx)
        if len(selected) >= 96:
            break
    return sorted(selected)


def _regular_grid_intervals(
    peaks: list[int],
    *,
    min_step: int,
    max_step: int,
) -> list[tuple[int, int, int, float]]:
    intervals: list[tuple[int, int, int, float]] = []
    for start in peaks:
        for end in peaks:
            if end <= start:
                continue
            step = (end - start) / 8.0
            if not (min_step <= step <= max_step):
                continue
            tolerance = max(3.0, step * 0.08)
            hits = 0
            for index in range(9):
                target = start + index * step
                if any(abs(peak - target) <= tolerance for peak in peaks):
                    hits += 1
            if hits >= 7:
                intervals.append((hits, start, end, step))

    intervals.sort(key=lambda item: (item[0], item[3]), reverse=True)
    return intervals[:16]


def _bbox_overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = first
    bx0, by0, bx1, by1 = second
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    first_area = max((ax1 - ax0) * (ay1 - ay0), 1.0)
    second_area = max((bx1 - bx0) * (by1 - by0), 1.0)
    return intersection / min(first_area, second_area)


def _dark_pixel_square_candidate(image: Image.Image, *, scale: float) -> tuple[float, float, float, float] | None:
    pixels = np.array(image, dtype=np.uint8)
    mask = pixels < 210
    if not np.any(mask):
        return None
    ys, xs = np.where(mask)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    width = x1 - x0
    height = y1 - y0
    if width < 64 or height < 64:
        return None
    ratio = width / float(max(height, 1))
    area_ratio = (width * height) / float(max(image.width * image.height, 1))
    if not (0.72 <= ratio <= 1.28 and area_ratio <= 0.42):
        return None
    pad = max(2, int(min(width, height) * 0.02))
    return (
        max(0, x0 - pad) / scale,
        max(0, y0 - pad) / scale,
        min(image.width, x1 + pad) / scale,
        min(image.height, y1 + pad) / scale,
    )
