from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


BLOCKER_CATEGORIES = (
    "crop_grid",
    "recognition",
    "placement",
    "full_fen_validation",
    "source_policy",
    "confidence",
    "metadata",
    "ai_review_only",
    "pgn",
    "runtime_dependency",
    "unknown",
)


_DIRECT_CODE_CATEGORIES: dict[str, str] = {
    "board_grid_not_detected": "crop_grid",
    "board_visual_pattern_not_detected": "crop_grid",
    "partial_board_crop_without_dense_board_evidence": "crop_grid",
    "image_board_requires_review": "crop_grid",
    "review_crop_candidate_mismatch": "crop_grid",
    "crop_path_missing": "crop_grid",
    "crop_missing": "crop_grid",
    "crop_invalid": "crop_grid",
    "reader_visible_crop_missing": "crop_grid",
    "reader_expanded_crop_missing": "crop_grid",
    "final_rendered_crop_missing": "crop_grid",
    "low_resolution": "crop_grid",
    "piece_template_confidence_below_threshold": "recognition",
    "piece_template_set_incomplete": "recognition",
    "queen_color_ambiguous_suppressed": "recognition",
    "sparse_position_confidence_below_threshold": "recognition",
    "no_square_alternatives": "recognition",
    "square_alternatives_not_checked": "recognition",
    "no_template_or_model_agreement": "recognition",
    "score_margin_too_low": "recognition",
    "score_margin_below_threshold": "recognition",
    "ambiguous_piece": "recognition",
    "placement_candidate_missing": "placement",
    "invalid_rank_count": "placement",
    "invalid_rank_width": "placement",
    "invalid_rank_digit": "placement",
    "invalid_piece": "placement",
    "missing_white_king": "placement",
    "missing_black_king": "placement",
    "too_many_white_kings": "placement",
    "too_many_black_kings": "placement",
    "pawn_on_back_rank": "placement",
    "white_king_count_invalid": "placement",
    "black_king_count_invalid": "placement",
    "candidate_conflicts_missing": "placement",
    "fen_must_have_six_fields": "full_fen_validation",
    "python_chess_invalid_position": "full_fen_validation",
    "python_chess_evidence_missing": "full_fen_validation",
    "validate_fen_evidence_missing": "full_fen_validation",
    "side_to_move_invalid": "full_fen_validation",
    "castling_invalid": "full_fen_validation",
    "castling_order_invalid": "full_fen_validation",
    "en_passant_invalid": "full_fen_validation",
    "move_counters_invalid": "full_fen_validation",
    "fullmove_number_invalid": "full_fen_validation",
    "full_fen_metadata_not_accepted": "full_fen_validation",
    "fen_validation_failed": "full_fen_validation",
    "fen_not_recognized": "full_fen_validation",
    "ai_review_only_source": "source_policy",
    "non_deterministic_source": "source_policy",
    "fen_source_missing": "source_policy",
    "source_fen_not_machine_accepted": "source_policy",
    "crop_sha256_missing": "source_policy",
    "source_crop_hash_missing": "metadata",
    "template_profile_not_ready": "metadata",
    "candidate_fens_missing": "metadata",
    "candidate_fens_invalid": "metadata",
    "confidence_below_runtime_threshold": "confidence",
    "confidence_below_threshold": "confidence",
    "ai_selection_not_in_candidates": "ai_review_only",
    "ai_consensus": "ai_review_only",
    "ai_tie_break_resolved": "ai_review_only",
    "ai_best_effort": "ai_review_only",
    "ai_unreadable": "ai_review_only",
    "pgn_requires_review": "pgn",
    "pgn_items_require_review": "pgn",
    "pgn_parse_failed": "pgn",
    "illegal_pgn": "pgn",
    "unmapped_chess_glyphs": "pgn",
    "pgn_replay_failed": "pgn",
    "python_chess_unavailable": "runtime_dependency",
    "python_chess_missing": "runtime_dependency",
    "python_chess_pgn_missing": "runtime_dependency",
}


_KEYWORD_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "ai_review_only",
        (
            "ai_review_only",
            "ai_only",
            "ai_consensus",
            "ai_tie_break",
            "ai_best_effort",
            "ai_unreadable",
            "ai-autoread",
        ),
    ),
    (
        "runtime_dependency",
        (
            "python_chess_unavailable",
            "python_chess_missing",
            "dependency",
            "runtime_import",
        ),
    ),
    (
        "source_policy",
        (
            "source",
            "external",
            "verified_exact",
            "label_lookup",
            "non_deterministic",
            "release_safe",
        ),
    ),
    (
        "crop_grid",
        (
            "grid",
            "bbox",
            "board_not_detected",
            "partial_board",
            "crop_invalid",
            "crop_missing",
            "crop_path_missing",
            "reader_visible_crop",
            "reader_expanded_crop",
            "final_rendered_crop",
            "low_resolution",
            "blur",
            "pixel",
            "resolution",
        ),
    ),
    (
        "recognition",
        (
            "template",
            "piece",
            "king_count",
            "queen_color",
            "recognition",
            "ambiguous",
            "square_alternatives",
            "score_margin",
        ),
    ),
    ("placement", ("placement", "rank_count", "rank_width", "invalid_rank", "invalid_piece")),
    (
        "full_fen_validation",
        (
            "python_chess",
            "fen_parse",
            "fen_position",
            "fen_missing",
            "side_to_move_inferred",
            "fen_must",
            "castling",
            "en_passant",
            "fullmove",
            "move_counters",
        ),
    ),
    ("confidence", ("confidence", "threshold")),
    ("metadata", ("metadata", "caption", "marker", "side_to_move", "crop_sha")),
    ("pgn", ("pgn", "chess_glyph", "movetext", "replay")),
)


def empty_blocker_category_counts() -> dict[str, int]:
    return {category: 0 for category in BLOCKER_CATEGORIES}


def classify_blocker_category(
    code: Any,
    *,
    kind: str = "fen",
    context: Iterable[Any] = (),
) -> str:
    normalized = _normalize_code(code)
    if normalized in _DIRECT_CODE_CATEGORIES:
        return _DIRECT_CODE_CATEGORIES[normalized]
    text = " ".join(part for part in [normalized, *(_normalize_code(item) for item in context)] if part)
    for category, keywords in _KEYWORD_CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return category
    if kind == "pgn" and not normalized.startswith("unknown"):
        return "pgn"
    return "unknown"


def categorize_blocker(
    blocker: Any,
    *,
    kind: str = "fen",
    context: Iterable[Any] = (),
) -> dict[str, Any]:
    if isinstance(blocker, Mapping):
        result = dict(blocker)
        code = str(result.get("code") or result.get("id") or result.get("blocker") or "unknown_blocker")
    else:
        code = str(blocker or "unknown_blocker")
        result = {"code": code}
    result["category"] = classify_blocker_category(code, kind=kind, context=context)
    return result


def count_blocker_categories(
    blockers: Iterable[Any],
    *,
    kind: str = "fen",
    context: Iterable[Any] = (),
    include_zero: bool = True,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for blocker in blockers:
        counts[categorize_blocker(blocker, kind=kind, context=context)["category"]] += 1
    return sorted_category_counts(counts, include_zero=include_zero)


def sorted_category_counts(counts: Mapping[str, int] | Counter[str], *, include_zero: bool = True) -> dict[str, int]:
    normalized = empty_blocker_category_counts() if include_zero else {}
    for category, count in counts.items():
        key = category if category in BLOCKER_CATEGORIES else "unknown"
        normalized[key] = normalized.get(key, 0) + int(count)
    return dict(sorted(normalized.items(), key=lambda pair: (-pair[1], BLOCKER_CATEGORIES.index(pair[0]))))


def recommendation_for_category(category: str) -> str:
    return {
        "crop_grid": "inspect crop/grid geometry before recognizer changes",
        "recognition": "compare placement against crop and template confidence",
        "placement": "separate placement recovery from full FEN metadata",
        "full_fen_validation": "inspect side-to-move/full FEN validation evidence",
        "source_policy": "audit exact-label/external source authority",
        "confidence": "inspect confidence drop without lowering strict gates",
        "metadata": "inspect side-to-move marker/caption metadata",
        "ai_review_only": "keep AI as evidence; require deterministic proof before strict accept",
        "pgn": "repair PGN parse/replay blockers before export",
        "runtime_dependency": "restore required runtime dependency evidence",
        "unknown": "inspect raw record and add missing blocker data",
    }.get(category, "inspect raw record and add missing blocker data")


def _normalize_code(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
