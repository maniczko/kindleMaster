from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
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

MACHINE_ACCEPTED_FEN_SOURCES = {
    "deterministic",
    "deterministic_candidate",
    "deterministic_ensemble",
    "font_board",
    "font-board",
    "image_template",
    "image-template",
    "image-template-board",
    "verified_exact_crop_label",
}
MACHINE_REVIEW_ONLY_FEN_SOURCES = {
    "ai",
    "ai_candidate",
    "ai_review",
    "ai_review_only",
    "openai",
    "openai_review",
    "gpt",
}
MACHINE_BLOCKING_FEN_WARNINGS = {
    "black_king_count_invalid",
    "board_grid_not_detected",
    "board_visual_pattern_not_detected",
    "confidence_below_threshold",
    "image_board_requires_review",
    "partial_board_crop_without_dense_board_evidence",
    "piece_template_confidence_below_threshold",
    "piece_template_set_incomplete",
    "queen_color_ambiguous_suppressed",
    "review_crop_candidate_mismatch",
    "score_margin_below_threshold",
    "no_square_alternatives",
    "side_to_move_inferred",
    "side_to_move_marker_ambiguous",
    "side_to_move_marker_local_ambiguous",
    "side_to_move_marker_local_conflict",
    "side_to_move_marker_multi_region_conflict",
    "sparse_position_confidence_below_threshold",
    "template_profile_not_ready",
    "white_king_count_invalid",
}
MACHINE_BLOCKING_SIDE_MARKER_STATUSES = {
    "ambiguous_marker",
    "inferred_only",
    "marker_conflict",
    "marker_missing",
    "multi_side",
    "multiple_candidates",
    "side_to_move_marker_local_ambiguous",
    "side_to_move_marker_local_conflict",
    "unclear_symbol",
}
MACHINE_TRUSTED_SIDE_MARKER_STATUSES = {
    "trusted_marker",
    "trusted_caption",
    "trusted_exact_label",
    "trusted_verified_label",
}
MACHINE_BLOCKING_MARKER_CROP_REASONS = {
    "multiple_candidates",
    "unclear_symbol",
    "marker_cut_off",
    "mostly_board_edge",
    "wrong_marker_candidate",
}
MACHINE_BLOCKING_PLACEMENT_WARNINGS = {
    "black_king_count_invalid",
    "board_grid_not_detected",
    "board_visual_pattern_not_detected",
    "image_board_requires_review",
    "partial_board_crop_without_dense_board_evidence",
    "piece_template_confidence_below_threshold",
    "piece_template_set_incomplete",
    "queen_color_ambiguous_suppressed",
    "review_crop_candidate_mismatch",
    "sparse_position_confidence_below_threshold",
    "template_profile_not_ready",
    "white_king_count_invalid",
}


@dataclass(frozen=True)
class FenValidationIssue:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class FenValidationResult:
    input: str
    normalized_fen: str | None
    is_syntax_valid: bool
    is_legal_position: bool
    errors: list[FenValidationIssue]
    warnings: list[FenValidationIssue]


@dataclass(frozen=True)
class FenPlacementValidationResult:
    input: str
    normalized_placement: str | None
    is_structure_valid: bool
    is_plausible_position: bool
    errors: list[FenValidationIssue]
    warnings: list[FenValidationIssue]


def crop_sha256(path: str | Path) -> str:
    crop_path = Path(path)
    digest = hashlib.sha256()
    with crop_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_fen_whitespace(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def placement_from_fen_or_placement(value: str) -> str:
    normalized = normalize_fen_whitespace(value)
    return normalized.split()[0].strip() if normalized else ""


def normalize_placement(value: str) -> str:
    result = validate_placement_detailed(value)
    if not result.is_structure_valid or result.errors:
        message = result.errors[0].message if result.errors else "Invalid FEN placement."
        raise ValueError(message)
    return result.normalized_placement or ""


def validate_placement_detailed(
    value: str,
    *,
    require_kings: bool = True,
    reject_pawns_on_back_rank: bool = True,
) -> FenPlacementValidationResult:
    raw = str(value or "")
    placement = placement_from_fen_or_placement(raw)
    errors: list[FenValidationIssue] = []
    warnings: list[FenValidationIssue] = []

    def add(code: str, message: str, *, severity: str = "error") -> None:
        issue = FenValidationIssue(code=code, severity=severity, message=message)
        if severity == "warning":
            warnings.append(issue)
        else:
            errors.append(issue)

    if not placement:
        add("placement_candidate_missing", "No board placement candidate was supplied.")
        return FenPlacementValidationResult(
            input=raw,
            normalized_placement=None,
            is_structure_valid=False,
            is_plausible_position=False,
            errors=errors,
            warnings=warnings,
        )

    ranks = placement.split("/")
    if len(ranks) != 8:
        add("invalid_rank_count", "Board placement must contain exactly eight ranks.")

    white_kings = 0
    black_kings = 0
    for rank_index, rank in enumerate(ranks):
        width = 0
        last_was_digit = False
        for char in rank:
            if char.isdigit():
                digit = int(char)
                if digit < 1 or digit > 8 or last_was_digit:
                    add("invalid_rank_digit", "Empty square digits must be between 1 and 8 and not repeated.")
                width += digit
                last_was_digit = True
                continue
            if char not in PIECE_CHARS:
                add("invalid_piece", f"Unsupported FEN piece character: {char!r}.")
                width += 1
                last_was_digit = False
                continue
            if char == "K":
                white_kings += 1
            elif char == "k":
                black_kings += 1
            if reject_pawns_on_back_rank and rank_index in {0, 7} and char in {"P", "p"}:
                add("pawn_on_back_rank", "Pawns cannot appear on the first or eighth rank.")
            width += 1
            last_was_digit = False
        if width != 8:
            add("invalid_rank_width", "Each FEN rank must describe exactly eight squares.")

    if require_kings:
        if white_kings == 0:
            add("missing_white_king", "Placement must contain exactly one white king.")
        elif white_kings > 1:
            add("too_many_white_kings", "Placement cannot contain more than one white king.")
        if black_kings == 0:
            add("missing_black_king", "Placement must contain exactly one black king.")
        elif black_kings > 1:
            add("too_many_black_kings", "Placement cannot contain more than one black king.")

    structure_error_codes = {"placement_candidate_missing", "invalid_rank_count", "invalid_rank_width", "invalid_rank_digit", "invalid_piece"}
    structure_errors = [issue for issue in errors if issue.code in structure_error_codes]
    return FenPlacementValidationResult(
        input=raw,
        normalized_placement=placement if not structure_errors else None,
        is_structure_valid=not structure_errors,
        is_plausible_position=not errors,
        errors=errors,
        warnings=warnings,
    )


def placement_to_default_fen(placement: str, *, side_to_move: str = "w") -> str:
    normalized_placement = normalize_placement(placement)
    side = str(side_to_move or "w").strip().lower()
    if side not in {"w", "b"}:
        raise ValueError("side_to_move must be 'w' or 'b'")
    return f"{normalized_placement} {side} - - 0 1"


def validate_fen_detailed(fen: str) -> FenValidationResult:
    normalized = normalize_fen_whitespace(fen)
    errors: list[FenValidationIssue] = []
    parts = normalized.split() if normalized else []
    syntax_codes = {
        "fen_must_have_six_fields",
        "placement_must_have_eight_ranks",
        "rank_digit_invalid",
        "rank_width_invalid",
        "invalid_rank_count",
        "invalid_rank_width",
        "invalid_rank_digit",
        "invalid_piece",
        "placement_contains_invalid_piece",
        "side_to_move_invalid",
        "castling_invalid",
        "castling_order_invalid",
        "en_passant_invalid",
        "move_counters_invalid",
        "fullmove_number_invalid",
    }

    def add(code: str, message: str) -> None:
        errors.append(FenValidationIssue(code=code, severity="error", message=message))

    if len(parts) != 6:
        add("fen_must_have_six_fields", "FEN must contain exactly six fields.")

    placement = parts[0] if parts else ""
    ranks = placement.split("/") if placement else []
    if len(ranks) != 8:
        add("invalid_rank_count", "Board placement must contain exactly eight ranks.")

    white_kings = 0
    black_kings = 0
    for rank_index, rank in enumerate(ranks):
        width = 0
        last_was_digit = False
        for char in rank:
            if char.isdigit():
                value = int(char)
                if value < 1 or value > 8 or last_was_digit:
                    add("invalid_rank_digit", "Empty square digits must be between 1 and 8 and not repeated.")
                width += value
                last_was_digit = True
                continue
            if char not in PIECE_CHARS:
                add("invalid_piece", f"Unsupported FEN piece character: {char!r}.")
                width += 1
                last_was_digit = False
                continue
            if char == "K":
                white_kings += 1
            elif char == "k":
                black_kings += 1
            if rank_index in {0, 7} and char in {"P", "p"}:
                add("pawn_on_back_rank", "Pawns cannot appear on the first or eighth rank.")
            width += 1
            last_was_digit = False
        if width != 8:
            add("invalid_rank_width", "Each FEN rank must describe exactly eight squares.")

    if white_kings == 0:
        add("missing_white_king", "Position must contain exactly one white king.")
    elif white_kings > 1:
        add("too_many_white_kings", "Position cannot contain more than one white king.")
    if black_kings == 0:
        add("missing_black_king", "Position must contain exactly one black king.")
    elif black_kings > 1:
        add("too_many_black_kings", "Position cannot contain more than one black king.")

    if len(parts) >= 2 and parts[1] not in {"w", "b"}:
        add("side_to_move_invalid", "Active color must be w or b.")

    if len(parts) >= 3:
        castling = parts[2]
        if castling != "-":
            if not castling or any(char not in "KQkq" for char in castling):
                add("castling_invalid", "Castling rights must contain only KQkq or '-'.")
            else:
                ordered = "".join(sorted(castling, key="KQkq".index))
                if ordered != castling or len(set(castling)) != len(castling):
                    add("castling_order_invalid", "Castling rights must be unique and ordered as KQkq.")

    if len(parts) >= 4 and parts[3] != "-":
        en_passant = parts[3]
        if len(en_passant) != 2 or en_passant[0] not in "abcdefgh" or en_passant[1] not in "36":
            add("en_passant_invalid", "En passant target must be '-' or a square on rank 3 or 6.")

    if len(parts) >= 5 and (not parts[4].isdigit()):
        add("move_counters_invalid", "Halfmove clock must be an integer >= 0.")

    if len(parts) >= 6:
        if not parts[5].isdigit():
            add("move_counters_invalid", "Fullmove number must be an integer >= 1.")
        elif int(parts[5]) < 1:
            add("fullmove_number_invalid", "Fullmove number must be an integer >= 1.")

    syntax_errors = [issue for issue in errors if issue.code in syntax_codes]
    return FenValidationResult(
        input=fen,
        normalized_fen=normalized if not errors else None,
        is_syntax_valid=not syntax_errors,
        is_legal_position=not errors,
        errors=errors,
        warnings=[],
    )


def evaluate_diagram_acceptance(candidate: dict[str, Any]) -> dict[str, Any]:
    raw_fen = str(candidate.get("raw_fen") or candidate.get("rawFen") or candidate.get("fen") or "")
    confidence = candidate.get("confidence") if isinstance(candidate.get("confidence"), dict) else {}
    mean = float(confidence.get("mean") or 0.0)
    min_occupied = float(confidence.get("min_occupied", confidence.get("minOccupied", 0.0)) or 0.0)
    orientation = float(confidence.get("orientation") or 0.0)
    context_match = candidate.get("context_match", candidate.get("contextMatch"))
    validation = validate_fen_detailed(raw_fen)
    reasons: list[str] = []

    if mean < 0.75:
        reasons.append("mean_confidence_below_reject_threshold")
    if not validation.is_syntax_valid or not validation.is_legal_position or validation.errors:
        reasons.append("invalid_fen")
    if "invalid_fen" in reasons or "mean_confidence_below_reject_threshold" in reasons:
        return {
            "status": "rejected",
            "normalized_fen": None,
            "reasons": reasons,
            "validation": validation,
        }

    if mean < 0.97:
        reasons.append("mean_confidence_below_auto_threshold")
    if min_occupied < 0.90:
        reasons.append("occupied_square_confidence_below_auto_threshold")
    if orientation < 0.98:
        reasons.append("orientation_confidence_below_auto_threshold")
    if context_match is False:
        reasons.append("context_mismatch")

    if reasons:
        return {
            "status": "manual_review_required",
            "normalized_fen": validation.normalized_fen,
            "reasons": reasons,
            "validation": validation,
        }
    return {
        "status": "auto_verified",
        "normalized_fen": validation.normalized_fen,
        "reasons": ["all_auto_verify_gates_passed"],
        "validation": validation,
    }


def machine_accept_placement(candidate: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate whether a visual board placement may be machine accepted.

    This gate intentionally does not accept a full six-field FEN. It proves only
    the board occupancy/piece placement layer and leaves active color and other
    FEN metadata to the stricter full-FEN gate.
    """
    ctx = dict(context or {})
    source = str(candidate.get("source") or candidate.get("method") or "").strip()
    normalized_source = source.lower().replace("_", "-")
    placement_value = _candidate_placement_value(candidate)
    min_confidence = float(ctx.get("min_confidence", 0.835) or 0.835)
    confidence = _candidate_confidence(candidate, default=ctx.get("confidence"))
    warnings = sorted({str(item) for item in candidate.get("warnings") or [] if str(item)})
    board_crop_quality = str(candidate.get("board_crop_quality") or "").strip().lower()
    board_crop_fail_reason = _string_list(candidate.get("board_crop_fail_reason"))
    blockers: list[dict[str, Any]] = []
    trace: dict[str, Any] = {
        "source": source or "unknown",
        "confidence": confidence,
        "min_confidence": min_confidence,
        "warnings": warnings,
        "board_crop_quality": board_crop_quality or None,
        "policy": "runtime_placement_acceptance_v1",
    }

    if not placement_value:
        blockers.append({"code": "placement_candidate_missing", "message": "No placement candidate was supplied."})
    if any(normalized_source == item.replace("_", "-") for item in MACHINE_REVIEW_ONLY_FEN_SOURCES):
        blockers.append({"code": "ai_review_only_source", "message": "AI placement candidates cannot be machine accepted directly."})
    if not source:
        blockers.append({"code": "fen_source_missing", "message": "Candidate source is required for placement machine acceptance."})
    elif normalized_source == "local-model-candidate" and not bool(ctx.get("allow_local_model_candidate")):
        blockers.append({"code": "non_deterministic_source", "message": "Local model placement candidates are review-only unless explicitly enabled."})
    elif not _is_machine_accepted_source(source) and normalized_source != "local-model-candidate":
        blockers.append({"code": "non_deterministic_source", "message": f"Source {source!r} is review-only for placement acceptance."})
    if confidence < min_confidence:
        blockers.append(
            {
                "code": "confidence_below_runtime_threshold",
                "message": "Candidate confidence is below the runtime placement acceptance threshold.",
                "confidence": confidence,
                "min_confidence": min_confidence,
            }
        )
    if _has_field(candidate, "board_crop_quality") and board_crop_quality != "pass":
        blockers.append(
            {
                "code": "placement_blocked_by_board_crop_quality",
                "message": "Placement cannot be machine accepted when the tight board crop quality gate fails.",
                "board_crop_quality": board_crop_quality or "missing",
                "board_crop_fail_reason": board_crop_fail_reason,
            }
        )

    if normalized_source == "deterministic-ensemble":
        blockers.extend(_deterministic_ensemble_placement_blockers(candidate, ctx, trace))

    validation = validate_placement_detailed(placement_value)
    trace["placement_validation"] = {
        "is_structure_valid": validation.is_structure_valid,
        "is_plausible_position": validation.is_plausible_position,
        "normalized_placement": validation.normalized_placement,
        "errors": [issue.__dict__ for issue in validation.errors],
        "warnings": [issue.__dict__ for issue in validation.warnings],
    }
    for issue in validation.errors:
        blockers.append({"code": issue.code, "message": issue.message})

    warning_blockers = sorted(set(warnings) & MACHINE_BLOCKING_PLACEMENT_WARNINGS)
    for warning in warning_blockers:
        blockers.append({"code": warning, "message": "Recognizer warning blocks runtime placement acceptance."})

    accepted = not blockers
    return {
        "status": "accepted" if accepted else "review_required",
        "runtime_status": "FEN_PLACEMENT_MACHINE_ACCEPTED" if accepted else "FEN_PLACEMENT_REVIEW_REQUIRED",
        "selected_placement": validation.normalized_placement if accepted else None,
        "normalized_placement": validation.normalized_placement,
        "acceptance_blockers": blockers,
        "acceptance_trace": trace,
        "acceptance_policy": "runtime_placement_acceptance_v1",
    }


def machine_accept_fen(candidate: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate whether a FEN candidate may be accepted by the runtime machine gate.

    This is deliberately separate from corpus verification. It never sets
    human/corpus fields and treats AI as review-only evidence.
    """
    ctx = dict(context or {})
    source = str(candidate.get("source") or candidate.get("method") or "").strip()
    normalized_source = source.lower().replace("_", "-")
    fen = str(candidate.get("fen") or candidate.get("value") or candidate.get("candidate_fen") or "").strip()
    min_confidence = float(ctx.get("min_confidence", 0.835) or 0.835)
    confidence = _candidate_confidence(candidate, default=ctx.get("confidence"))
    warnings = sorted({str(item) for item in candidate.get("warnings") or [] if str(item)})
    board_crop_quality = str(candidate.get("board_crop_quality") or "").strip().lower()
    board_crop_fail_reason = _string_list(candidate.get("board_crop_fail_reason"))
    board_crop_quality_gate = candidate.get("board_crop_quality_gate") if isinstance(candidate.get("board_crop_quality_gate"), dict) else {}
    board_crop_gate_reasons = _string_list(board_crop_quality_gate.get("reasons"))
    blockers: list[dict[str, Any]] = []
    trace: dict[str, Any] = {
        "source": source or "unknown",
        "confidence": confidence,
        "min_confidence": min_confidence,
        "warnings": warnings,
        "crop_quality": {
            "board_crop_quality": board_crop_quality or "missing",
            "board_crop_fail_reason": board_crop_fail_reason,
            "board_crop_quality_gate_reasons": board_crop_gate_reasons,
        },
        "policy": "runtime_machine_acceptance_only",
    }

    if not fen:
        blockers.append({"code": "fen_candidate_missing", "message": "No FEN candidate was supplied."})
    if any(normalized_source == item.replace("_", "-") for item in MACHINE_REVIEW_ONLY_FEN_SOURCES):
        blockers.append({"code": "ai_review_only_source", "message": "AI FEN candidates cannot be machine accepted directly."})
    if not source:
        blockers.append({"code": "fen_source_missing", "message": "Candidate source is required for machine acceptance."})
    elif not _is_machine_accepted_source(source):
        blockers.append({"code": "non_deterministic_source", "message": f"Source {source!r} is review-only for runtime acceptance."})
    if confidence < min_confidence:
        blockers.append(
            {
                "code": "confidence_below_runtime_threshold",
                "message": "Candidate confidence is below the runtime acceptance threshold.",
                "confidence": confidence,
                "min_confidence": min_confidence,
            }
        )

    if normalized_source == "deterministic-ensemble":
        blockers.extend(_deterministic_ensemble_contract_blockers(candidate, ctx, trace))

    placement_gate = machine_accept_placement(candidate, ctx)
    trace["placement_gate"] = {
        "runtime_status": placement_gate.get("runtime_status"),
        "status": placement_gate.get("status"),
        "selected_placement": placement_gate.get("selected_placement"),
        "blocker_codes": [blocker.get("code") for blocker in placement_gate.get("acceptance_blockers") or []],
    }
    if placement_gate.get("runtime_status") != "FEN_PLACEMENT_MACHINE_ACCEPTED":
        blockers.append(
            {
                "code": "full_fen_blocked_by_placement",
                "message": "Full FEN cannot be machine accepted until board placement is machine accepted.",
                "placement_runtime_status": placement_gate.get("runtime_status"),
                "placement_blockers": placement_gate.get("acceptance_blockers") or [],
            }
        )

    side_status = str(candidate.get("side_to_move_status") or "").strip().lower()
    side_evidence = str(candidate.get("side_to_move_evidence") or "").strip().lower()
    side_marker_status = str(candidate.get("side_marker_status") or "").strip().lower()
    marker_crop_quality = str(candidate.get("marker_crop_quality") or "").strip().lower()
    marker_crop_fail_reason = [
        str(reason)
        for reason in candidate.get("marker_crop_fail_reason") or []
        if str(reason)
    ]
    marker_crop_quality_gate = candidate.get("marker_crop_quality_gate") if isinstance(candidate.get("marker_crop_quality_gate"), dict) else {}
    marker_crop_gate_reasons = [
        str(reason)
        for reason in marker_crop_quality_gate.get("reasons") or []
        if str(reason)
    ]
    try:
        marker_crop_component_count = int(marker_crop_quality_gate.get("component_count") or 0)
    except (TypeError, ValueError):
        marker_crop_component_count = 0
    marker_bbox = candidate.get("marker_bbox") or candidate.get("side_marker_bbox")
    marker_bbox_valid = False
    if isinstance(marker_bbox, (list, tuple)) and len(marker_bbox) == 4:
        try:
            mx0, my0, mx1, my1 = [float(value) for value in marker_bbox]
        except (TypeError, ValueError):
            marker_bbox_valid = False
        else:
            marker_bbox_valid = mx1 > mx0 and my1 > my0
    selected_marker_zone = str(candidate.get("selected_marker_zone") or "").strip()
    trace["side_to_move"] = {
        "status": side_status,
        "evidence": side_evidence,
        "marker_status": side_marker_status,
        "trusted_marker": side_marker_status in MACHINE_TRUSTED_SIDE_MARKER_STATUSES,
        "marker_crop_quality": marker_crop_quality or None,
        "marker_crop_fail_reason": marker_crop_fail_reason,
        "selected_marker_zone": selected_marker_zone or None,
        "marker_bbox_present": marker_bbox_valid,
        "marker_crop_component_count": marker_crop_component_count,
    }
    trace["crop_quality"]["marker_crop_quality"] = marker_crop_quality or "missing"
    trace["crop_quality"]["marker_crop_fail_reason"] = marker_crop_fail_reason
    trace["crop_quality"]["marker_crop_quality_gate_reasons"] = marker_crop_gate_reasons
    if board_crop_quality != "pass":
        blockers.append(
            {
                "code": "full_fen_blocked_by_board_crop_quality",
                "message": "Full FEN requires a passing tight 8x8 board crop.",
                "board_crop_quality": board_crop_quality or "missing",
                "board_crop_fail_reason": sorted(set(board_crop_fail_reason + board_crop_gate_reasons)),
            }
        )
    if side_status == "inferred" or side_evidence == "inferred":
        blockers.append(
            {
                "code": "side_to_move_inferred",
                "message": "Full FEN cannot be machine accepted when side to move is inferred.",
            }
        )
    manual_review_required = _truthy_value(candidate.get("manual_review_required"))
    manual_review_reason = str(candidate.get("manual_review_reason") or "").strip()
    manual_review_codes = set(_string_list(candidate.get("manual_review_reason"))) | set(marker_crop_fail_reason) | set(marker_crop_gate_reasons) | set(warnings)
    if manual_review_required or manual_review_reason or "system_suggestion_mismatch" in manual_review_codes:
        blockers.append(
            {
                "code": "full_fen_blocked_by_marker_manual_review",
                "message": "Full FEN cannot be machine accepted while marker evidence is flagged for manual review.",
                "manual_review_required": manual_review_required,
                "manual_review_reason": manual_review_reason,
            }
        )
    if side_marker_status in MACHINE_BLOCKING_SIDE_MARKER_STATUSES:
        blockers.append(
            {
                "code": side_marker_status,
                "message": "Full FEN cannot be machine accepted without trusted diagram-scoped side-marker evidence.",
            }
        )
        if "conflict" in side_marker_status or "multi" in side_marker_status:
            blockers.append(
                {
                    "code": "full_fen_blocked_by_marker_conflict",
                    "message": "Full FEN cannot be machine accepted when side-marker evidence conflicts.",
                    "side_marker_status": side_marker_status,
                }
            )
    if side_marker_status not in MACHINE_TRUSTED_SIDE_MARKER_STATUSES:
        blockers.append(
            {
                "code": "full_fen_blocked_by_marker",
                "message": "Full FEN requires trusted diagram-scoped side-marker evidence.",
                "side_marker_status": side_marker_status or "marker_missing",
            }
        )
    if marker_crop_quality != "pass":
        blockers.append(
            {
                "code": "full_fen_blocked_by_marker_crop_quality",
                "message": "Full FEN requires a passing tight side-marker crop.",
                "marker_crop_quality": marker_crop_quality or "missing",
                "marker_crop_fail_reason": sorted(set(marker_crop_fail_reason + marker_crop_gate_reasons)),
            }
        )
    if side_marker_status == "trusted_marker" and marker_crop_quality != "pass":
        blockers.append(
            {
                "code": "marker_crop_quality_failed",
                "message": "Full FEN requires a passing tight side-marker crop.",
                "marker_crop_quality": marker_crop_quality or "missing",
                "marker_crop_fail_reason": marker_crop_fail_reason,
            }
        )
    if side_marker_status == "trusted_marker":
        if not marker_bbox_valid:
            blockers.append(
                {
                    "code": "marker_bbox_missing",
                    "message": "Full FEN requires a non-empty tight side-marker bbox.",
                }
            )
        if not selected_marker_zone:
            blockers.append(
                {
                    "code": "selected_marker_zone_missing",
                    "message": "Full FEN requires an unambiguous selected marker zone.",
                }
            )
        if marker_crop_component_count != 1:
            blockers.append(
                {
                    "code": "marker_component_count_invalid",
                    "message": "Full FEN requires exactly one marker candidate component.",
                    "component_count": marker_crop_component_count,
                }
            )
        blocked_crop_reasons = sorted(
            (set(marker_crop_fail_reason) | set(marker_crop_gate_reasons)) & MACHINE_BLOCKING_MARKER_CROP_REASONS
        )
        if blocked_crop_reasons:
            blockers.append(
                {
                    "code": "marker_crop_blocking_reason",
                    "message": "Full FEN cannot use a side marker crop with blocking review reasons.",
                    "reasons": blocked_crop_reasons,
                }
            )

    validation = validate_fen_detailed(fen)
    trace["fen_validation"] = {
        "is_syntax_valid": validation.is_syntax_valid,
        "is_legal_position": validation.is_legal_position,
        "normalized_fen": validation.normalized_fen,
        "errors": [issue.__dict__ for issue in validation.errors],
        "warnings": [issue.__dict__ for issue in validation.warnings],
    }
    for issue in validation.errors:
        blockers.append({"code": issue.code, "message": issue.message})

    python_chess_result = _python_chess_validate(fen)
    trace["python_chess"] = python_chess_result
    if not python_chess_result.get("valid"):
        blockers.append(
            {
                "code": "python_chess_invalid_position",
                "message": python_chess_result.get("message") or "python-chess rejected the position.",
                "details": python_chess_result,
            }
        )

    warning_blockers = sorted(set(warnings) & MACHINE_BLOCKING_FEN_WARNINGS)
    for warning in warning_blockers:
        blockers.append({"code": warning, "message": "Recognizer warning blocks runtime machine acceptance."})

    expected_fen = str(ctx.get("expected_fen") or "").strip()
    if expected_fen and fen:
        diff = compare_fen(fen, expected_fen)
        trace["expected_fen_diff"] = diff
        if diff.get("placement_diffs"):
            blockers.append(
                {
                    "code": "expected_fen_square_mismatch",
                    "message": "Candidate differs from expected/manual FEN evidence.",
                    "square_diffs": diff.get("placement_diffs"),
                }
            )

    accepted = not blockers
    return {
        "status": "accepted" if accepted else "review_required",
        "runtime_status": "FEN_MACHINE_ACCEPTED" if accepted else "FEN_REVIEW_REQUIRED",
        "selected_value": validation.normalized_fen if accepted else None,
        "normalized_fen": validation.normalized_fen,
        "acceptance_blockers": blockers,
        "acceptance_trace": trace,
        "acceptance_policy": "runtime_machine_acceptance_v1",
    }


def _candidate_placement_value(candidate: dict[str, Any]) -> str:
    for key in ("placement", "placement_fen", "fen", "value", "candidate_fen"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return placement_from_fen_or_placement(value)
    return ""


def _candidate_confidence(candidate: dict[str, Any], *, default: Any = None) -> float:
    value = candidate.get("confidence", default)
    if isinstance(value, dict):
        value = value.get("mean", value.get("global", value.get("score", 0.0)))
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _has_field(candidate: dict[str, Any], key: str) -> bool:
    return key in candidate and candidate.get(key) not in (None, "")


def _truthy_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _string_list(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _is_machine_accepted_source(source: str) -> bool:
    normalized = str(source or "").strip().lower().replace("_", "-")
    return any(normalized == item.replace("_", "-") for item in MACHINE_ACCEPTED_FEN_SOURCES)


def _deterministic_ensemble_contract_blockers(
    candidate: dict[str, Any],
    context: dict[str, Any],
    trace: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    min_score_margin = float(context.get("min_score_margin", 0.025) or 0.025)
    score_margin = _candidate_score_margin(candidate, evidence)
    source_crop_hash = str(candidate.get("source_crop_hash") or evidence.get("source_crop_hash") or "").strip()
    local_model_evidence = bool(evidence.get("local_model_candidate"))
    template_evidence = bool(evidence.get("template_candidate"))
    square_alternatives_checked = bool(evidence.get("square_alternatives_checked"))
    python_chess_valid = bool(evidence.get("python_chess_valid"))
    validate_fen_passed = bool(
        evidence.get("validate_fen_detailed_passed")
        or (isinstance(candidate.get("deterministic_validation"), dict) and candidate["deterministic_validation"].get("valid"))
    )
    blockers: list[dict[str, Any]] = []
    trace["deterministic_ensemble_evidence"] = {
        "python_chess_valid": python_chess_valid,
        "validate_fen_detailed_passed": validate_fen_passed,
        "score_margin_to_second_candidate": score_margin,
        "min_score_margin": min_score_margin,
        "source_crop_hash_present": bool(source_crop_hash),
        "local_model_candidate": local_model_evidence,
        "template_candidate": template_evidence,
        "square_alternatives_checked": square_alternatives_checked,
    }
    if not python_chess_valid:
        blockers.append(
            {
                "code": "python_chess_evidence_missing",
                "message": "Deterministic ensemble candidates must carry positive python-chess validation evidence.",
            }
        )
    if not validate_fen_passed:
        blockers.append(
            {
                "code": "validate_fen_evidence_missing",
                "message": "Deterministic ensemble candidates must carry validate_fen_detailed evidence.",
            }
        )
    if not source_crop_hash:
        blockers.append(
            {
                "code": "source_crop_hash_missing",
                "message": "Deterministic ensemble candidates require crop-backed hash evidence.",
            }
        )
    if not square_alternatives_checked:
        blockers.append(
            {
                "code": "square_alternatives_not_checked",
                "message": "Deterministic ensemble candidates require checked per-square alternatives.",
            }
        )
    if not (local_model_evidence or template_evidence):
        blockers.append(
            {
                "code": "no_template_or_model_agreement",
                "message": "Deterministic ensemble candidates require local model or template evidence.",
            }
        )
    if score_margin < min_score_margin:
        blockers.append(
            {
                "code": "score_margin_too_low",
                "message": "Best deterministic ensemble candidate is too close to the runner-up.",
                "score_margin_to_second_candidate": score_margin,
                "min_score_margin": min_score_margin,
            }
        )
    return blockers


def _deterministic_ensemble_placement_blockers(
    candidate: dict[str, Any],
    context: dict[str, Any],
    trace: dict[str, Any],
) -> list[dict[str, Any]]:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    min_score_margin = float(context.get("min_score_margin", 0.025) or 0.025)
    score_margin = _candidate_score_margin(candidate, evidence)
    source_crop_hash = str(candidate.get("source_crop_hash") or evidence.get("source_crop_hash") or "").strip()
    local_model_evidence = bool(evidence.get("local_model_candidate"))
    template_evidence = bool(evidence.get("template_candidate"))
    square_alternatives_checked = bool(evidence.get("square_alternatives_checked"))
    blockers: list[dict[str, Any]] = []
    trace["deterministic_ensemble_placement_evidence"] = {
        "score_margin_to_second_candidate": score_margin,
        "min_score_margin": min_score_margin,
        "source_crop_hash_present": bool(source_crop_hash),
        "local_model_candidate": local_model_evidence,
        "template_candidate": template_evidence,
        "square_alternatives_checked": square_alternatives_checked,
    }
    if not source_crop_hash:
        blockers.append(
            {
                "code": "source_crop_hash_missing",
                "message": "Deterministic ensemble placement candidates require crop-backed hash evidence.",
            }
        )
    if not square_alternatives_checked:
        blockers.append(
            {
                "code": "square_alternatives_not_checked",
                "message": "Deterministic ensemble placement candidates require checked per-square alternatives.",
            }
        )
    if not (local_model_evidence or template_evidence):
        blockers.append(
            {
                "code": "no_template_or_model_agreement",
                "message": "Deterministic ensemble placement candidates require local model or template evidence.",
            }
        )
    if score_margin < min_score_margin:
        blockers.append(
            {
                "code": "score_margin_too_low",
                "message": "Best deterministic ensemble placement candidate is too close to the runner-up.",
                "score_margin_to_second_candidate": score_margin,
                "min_score_margin": min_score_margin,
            }
        )
    return blockers


def _candidate_score_margin(candidate: dict[str, Any], evidence: dict[str, Any]) -> float:
    for value in [candidate.get("score_margin_to_second_candidate"), evidence.get("score_margin_to_second_candidate")]:
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _python_chess_validate(fen: str) -> dict[str, Any]:
    try:
        import chess  # type: ignore

        board = chess.Board(fen)
        status = int(board.status())
        return {
            "valid": bool(board.is_valid()),
            "status": status,
            "message": "" if board.is_valid() else f"python-chess status={status}",
        }
    except Exception as exc:
        return {
            "valid": False,
            "status": "exception",
            "message": f"{exc.__class__.__name__}: {exc}",
        }


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
