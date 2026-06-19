from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Iterable

from chess_fen_hardening import crop_sha256, machine_accept_fen, validate_fen_detailed

FILES = "abcdefgh"
RANKS = "87654321"
PIECE_ORDER = {"": 0, "K": 6, "Q": 5, "R": 4, "B": 3, "N": 3, "P": 1, "k": 6, "q": 5, "r": 4, "b": 3, "n": 3, "p": 1}
BLOCKING_VALIDATION_CODES = {
    "missing_white_king",
    "missing_black_king",
    "too_many_white_kings",
    "too_many_black_kings",
    "pawn_on_back_rank",
    "invalid_rank_count",
    "invalid_rank_width",
    "invalid_rank_digit",
    "invalid_piece",
    "fen_must_have_six_fields",
}
FEN_RUNTIME_ACCEPTED_STATUSES = {"FEN_MACHINE_ACCEPTED", "FEN_MACHINE_REPAIRED", "FEN_CORPUS_VERIFIED"}
DEFAULT_MIN_TEMPLATE_VERIFIED_LABELS = 50
TEMPLATE_PROFILE_NOT_READY_WARNING = "template_profile_not_ready"


def load_model_predictions(out_dir: str | Path) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(Path(out_dir) / "review" / "fen_model_predictions.jsonl")
    return {str(row.get("diagram_id") or ""): row for row in rows if row.get("diagram_id")}


def load_template_candidates(out_dir: str | Path) -> dict[str, dict[str, Any]]:
    out = Path(out_dir)
    candidates: dict[str, dict[str, Any]] = {}
    for path in [
        out / "review" / "fen_template_candidates.jsonl",
        out / "reports" / "fen_template_candidates.jsonl",
    ]:
        for row in _read_jsonl(path):
            diagram_id = str(row.get("diagram_id") or row.get("id") or "")
            fen = str(row.get("fen") or row.get("manual_fen") or row.get("fen_candidate") or "").strip()
            if diagram_id and fen:
                candidates.setdefault(diagram_id, {**row, "fen": fen})
    return candidates


def build_runtime_template_candidates(
    out_dir: str | Path,
    *,
    template_dir: str | Path | None = None,
    min_verified_labels: int = DEFAULT_MIN_TEMPLATE_VERIFIED_LABELS,
    recognizer: Callable[[Path, dict[str, Any]], Any] | None = None,
    force_undertrained_profile: bool = False,
) -> dict[str, Any]:
    """Run existing piece templates against diagram crops and write runtime candidates.

    Verified labels and AI suggestions remain training/review evidence. This function
    only emits deterministic template candidates that still have to pass
    ``machine_accept_fen`` through the ensemble gate.
    """

    out = Path(out_dir)
    review = out / "review"
    reports = out / "reports"
    review.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)

    build_report = _read_optional_json(reports / "fen_template_build.json")
    resolved_template_dir = _resolve_template_dir(out, template_dir, build_report)
    promoted_label_count = _template_label_count(out, build_report)
    profile_ready = promoted_label_count >= int(min_verified_labels or DEFAULT_MIN_TEMPLATE_VERIFIED_LABELS)
    if recognizer is None and not profile_ready and not force_undertrained_profile:
        _write_jsonl(review / "fen_template_candidates.jsonl", [])
        payload = {
            "schema": "kindlemaster.fen_template_candidates_eval.v1",
            "status": "needs_review",
            "template_candidate_count": 0,
            "row_count": 0,
            "profile_ready": False,
            "promoted_label_count": promoted_label_count,
            "min_verified_labels": int(min_verified_labels or DEFAULT_MIN_TEMPLATE_VERIFIED_LABELS),
            "template_dir": str(resolved_template_dir or ""),
            "failure_reasons": [{"key": TEMPLATE_PROFILE_NOT_READY_WARNING, "count": 1}],
            "next_action": "add_verified_fen_labels",
            "policy": "Template profile is below the verified-label gate; runtime template recognition is skipped to avoid expensive review-only work.",
        }
        _write_json(reports / "fen_template_candidates_eval.json", payload)
        return payload
    template_loader_error = ""
    piece_templates: Any = None
    if recognizer is None:
        if not resolved_template_dir or not resolved_template_dir.exists():
            template_loader_error = "template_missing"
        else:
            try:
                from chess_position_recognizer import load_piece_templates

                piece_templates = load_piece_templates(resolved_template_dir)
            except Exception as exc:
                template_loader_error = f"{exc.__class__.__name__}: {exc}"

    rows: list[dict[str, Any]] = []
    failure_reasons: dict[str, int] = {}
    if recognizer is not None or piece_templates:
        for diagram in _load_diagrams(out):
            row = _template_candidate_for_diagram(
                out,
                diagram,
                recognizer=recognizer,
                piece_templates=piece_templates,
                profile_ready=profile_ready,
                promoted_label_count=promoted_label_count,
                min_verified_labels=int(min_verified_labels or DEFAULT_MIN_TEMPLATE_VERIFIED_LABELS),
                template_dir=resolved_template_dir,
            )
            rows.append(row)
            for warning in row.get("warnings") or []:
                failure_reasons[str(warning)] = failure_reasons.get(str(warning), 0) + 1
    elif template_loader_error:
        failure_reasons[template_loader_error] = 1

    _write_jsonl(review / "fen_template_candidates.jsonl", rows)
    payload = {
        "schema": "kindlemaster.fen_template_candidates_eval.v1",
        "status": "ok" if rows else "needs_review",
        "template_candidate_count": len([row for row in rows if row.get("fen")]),
        "row_count": len(rows),
        "profile_ready": profile_ready,
        "promoted_label_count": promoted_label_count,
        "min_verified_labels": int(min_verified_labels or DEFAULT_MIN_TEMPLATE_VERIFIED_LABELS),
        "template_dir": str(resolved_template_dir or ""),
        "failure_reasons": [
            {"key": key, "count": count}
            for key, count in sorted(failure_reasons.items(), key=lambda item: (-item[1], item[0]))
        ],
        "next_action": "run_fen_beam_candidates" if rows else "build_fen_templates_from_verified_labels",
        "policy": "Template candidates are deterministic evidence only; profile readiness and machine_accept_fen still gate runtime acceptance.",
    }
    _write_json(reports / "fen_template_candidates_eval.json", payload)
    return payload


def load_square_alternatives(out_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    return {
        diagram_id: list(row.get("squares") or [])
        for diagram_id, row in load_model_predictions(out_dir).items()
        if row.get("squares")
    }


def build_deterministic_ensemble_candidates(
    *,
    diagrams: list[dict[str, Any]],
    model_predictions: dict[str, dict[str, Any]],
    template_candidates: dict[str, dict[str, Any]] | None = None,
    beam_width: int = 256,
    max_uncertain_squares: int = 12,
    top_n_per_square: int = 3,
    min_confidence: float = 0.835,
    min_score_margin: float = 0.025,
) -> list[dict[str, Any]]:
    templates = template_candidates or {}
    rows: list[dict[str, Any]] = []
    for diagram in diagrams:
        diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
        model_prediction = model_predictions.get(diagram_id, {})
        template_prediction = templates.get(diagram_id, {})
        if not model_prediction and not template_prediction:
            continue
        rows.append(
            build_deterministic_ensemble_fen(
                diagram,
                model_prediction or None,
                template_prediction or None,
                {
                    "beam_width": beam_width,
                    "max_uncertain_squares": max_uncertain_squares,
                    "top_n_per_square": top_n_per_square,
                    "min_confidence": min_confidence,
                    "min_score_margin": min_score_margin,
                },
            )
        )
    return rows


def validate_ensemble_candidate(candidate: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    return machine_accept_fen(candidate, context or {})


def select_best_ensemble_fen(candidates: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    ctx = dict(context or {})
    min_margin = float(ctx.get("min_score_margin", 0.025) or 0.025)
    finalist_limit = int(ctx.get("finalist_limit", 24) or 24)
    finalists = sorted(candidates, key=_fast_fen_candidate_score, reverse=True)[: max(2, finalist_limit)]
    ranked = sorted(finalists, key=score_fen_candidate, reverse=True)
    if not ranked:
        return None
    best = dict(ranked[0])
    best_score = score_fen_candidate(best)
    second_score = score_fen_candidate(ranked[1]) if len(ranked) > 1 else best_score - 1.0
    margin = round(best_score - second_score, 6)
    best["score"] = best_score
    best["score_margin_to_second_candidate"] = margin
    best.setdefault("evidence", {})["score_margin_to_second_candidate"] = margin
    if margin < min_margin:
        best.setdefault("warnings", []).append("score_margin_below_threshold")
    best["machine_acceptance"] = validate_ensemble_candidate(
        best,
        {
            "min_confidence": ctx.get("min_confidence", 0.835),
            "min_score_margin": min_margin,
        },
    )
    return best


def build_deterministic_ensemble_fen(
    diagram: dict[str, Any],
    model_prediction: dict[str, Any] | None,
    template_prediction: dict[str, Any] | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = dict(context or {})
    candidates: list[dict[str, Any]] = []
    source_crop_hash = str(ctx.get("source_crop_hash") or "").strip() or _source_crop_hash(diagram, model_prediction, template_prediction)
    if template_prediction:
        direct = _direct_fen(template_prediction, source_crop_hash=source_crop_hash, source_kind="template")
        if direct:
            candidates.append(direct)
    if model_prediction:
        direct = _direct_fen(model_prediction, source_crop_hash=source_crop_hash, source_kind="model")
        if direct:
            candidates.append(direct)
        if model_prediction.get("squares"):
            candidates.extend(
                generate_fen_candidates_from_square_alternatives(
                    {**diagram, **model_prediction, "source_crop_hash": source_crop_hash},
                    max_uncertain_squares=int(ctx.get("max_uncertain_squares", 12) or 12),
                    beam_width=int(ctx.get("beam_width", 256) or 256),
                    top_n_per_square=int(ctx.get("top_n_per_square", 3) or 3),
                )
            )
    candidates = _dedupe_fen_candidates(candidates)
    selected = select_best_ensemble_fen(candidates, ctx) or {}
    fen = str(selected.get("fen") or "")
    evidence = {
        "template_candidate": bool(template_prediction and _direct_fen(template_prediction, source_crop_hash=source_crop_hash, source_kind="template")),
        "local_model_candidate": bool(model_prediction and (_direct_fen(model_prediction, source_crop_hash=source_crop_hash, source_kind="model") or model_prediction.get("squares"))),
        "square_alternatives_checked": bool(selected.get("evidence", {}).get("square_alternatives_checked")),
        "square_alternatives_used": int(selected.get("evidence", {}).get("square_alternatives_used") or 0),
        "orientation_checked": selected.get("evidence", {}).get("orientation_checked") or ["normal"],
        "python_chess_valid": _python_chess_valid(fen),
        "validate_fen_detailed_passed": _validate_fen_passed(fen),
        "source_crop_hash": source_crop_hash,
        "score_margin_to_second_candidate": selected.get("score_margin_to_second_candidate", 0.0),
    }
    warnings = sorted(set(list(selected.get("warnings") or []) + _validation_warning_codes(fen)))
    row = {
        "schema": "kindlemaster.fen_beam_candidate.v1",
        "diagram_id": str(diagram.get("diagram_id") or diagram.get("id") or (model_prediction or {}).get("diagram_id") or ""),
        "page": int(diagram.get("page") or (model_prediction or {}).get("page") or 0),
        "source": "deterministic_ensemble",
        "method": "deterministic_ensemble",
        "fen": fen,
        "confidence": float(selected.get("confidence") or 0.0),
        "score": score_fen_candidate(selected) if selected else 0.0,
        "score_margin_to_second_candidate": selected.get("score_margin_to_second_candidate", 0.0),
        "source_crop_hash": source_crop_hash,
        "warnings": warnings,
        "changed_squares": selected.get("changed_squares") or [],
        "evidence": evidence,
        "deterministic_validation": _deterministic_validation(fen),
    }
    row["machine_acceptance"] = validate_ensemble_candidate(
        row,
        {
            "min_confidence": ctx.get("min_confidence", 0.835),
            "min_score_margin": ctx.get("min_score_margin", 0.025),
        },
    )
    row["next_action"] = _next_action_for_blockers(row["machine_acceptance"].get("acceptance_blockers") or [])
    return row


def generate_fen_candidates_from_square_alternatives(
    diagram: dict[str, Any],
    max_uncertain_squares: int = 12,
    beam_width: int = 256,
    top_n_per_square: int = 3,
) -> list[dict[str, Any]]:
    squares = _coerce_squares(diagram)
    if len(squares) != 64:
        direct = _direct_fen(diagram, source_crop_hash=str(diagram.get("source_crop_hash") or ""), source_kind="direct")
        return [direct] if direct else []

    uncertain = _uncertain_square_indices(squares, max_uncertain_squares=max_uncertain_squares)
    base_pieces: list[str] = []
    base_scores: list[float] = []
    uncertain_options: list[tuple[int, list[tuple[str, float]]]] = []
    for index, square in enumerate(squares):
        alternatives = _square_alternatives(square, top_n=top_n_per_square)
        top = (alternatives or [("", 0.0)])[0]
        base_pieces.append(top[0])
        base_scores.append(top[1])
        if index in uncertain:
            uncertain_options.append((index, alternatives or [("", 0.0)]))

    candidates: list[dict[str, Any]] = []
    for orientation in ["normal", "flipped"]:
        for pieces, scores in _beam_uncertain_square_options(base_pieces, base_scores, uncertain_options, beam_width=beam_width):
            oriented = list(reversed(pieces)) if orientation == "flipped" else list(pieces)
            fen = _pieces_to_fen(oriented, side_to_move=_side_to_move(diagram))
            candidates.append(
                _candidate_payload(
                    diagram,
                    fen,
                    confidence=_mean(scores),
                    orientation=orientation,
                    square_alternatives_checked=True,
                    square_alternatives_used=len(uncertain),
                    changed_squares=_changed_squares(squares, oriented),
                    source_crop_hash=str(diagram.get("source_crop_hash") or ""),
                )
            )
    return _dedupe_fen_candidates(sorted(candidates, key=_fast_fen_candidate_score, reverse=True))[:beam_width]


def build_fen_beam_candidates(out_dir: str | Path, *, beam_width: int = 256, max_uncertain_squares: int = 12) -> dict[str, Any]:
    out = Path(out_dir)
    review = out / "review"
    reports = out / "reports"
    review.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    rows = build_deterministic_ensemble_candidates(
        diagrams=_load_diagrams(out),
        model_predictions=load_model_predictions(out),
        template_candidates=load_template_candidates(out),
        beam_width=beam_width,
        max_uncertain_squares=max_uncertain_squares,
    )
    _write_jsonl(review / "fen_beam_candidates.jsonl", rows)
    accepted = [row for row in rows if row.get("machine_acceptance", {}).get("runtime_status") == "FEN_MACHINE_ACCEPTED"]
    top_blockers = _top_blockers(rows)
    payload = {
        "schema": "kindlemaster.fen_beam_eval.v1",
        "status": "ok" if rows else "needs_review",
        "candidate_count": len(rows),
        "machine_accepted_candidate_count": len(accepted),
        "beam_width": beam_width,
        "max_uncertain_squares": max_uncertain_squares,
        "top_blockers": top_blockers,
        "next_actions": _top_next_actions(rows),
        "next_action": _next_action_for_blocker_keys([row.get("key") for row in top_blockers]),
        "policy": "Beam candidates are deterministic ensemble evidence; machine_accept_fen remains the acceptance gate.",
    }
    _write_json(reports / "fen_beam_eval.json", payload)
    return payload


def apply_runtime_accepted_fen(out_dir: str | Path) -> dict[str, Any]:
    out = Path(out_dir)
    fen_payload = _read_optional_json(out / "fen" / "fen_candidates.json")
    accepted = {
        str(item.get("id") or ""): item
        for item in fen_payload.get("items") or []
        if item.get("runtime_status") in FEN_RUNTIME_ACCEPTED_STATUSES and item.get("selected_value")
    }
    book_path = out / "data" / "book.json"
    diagrams_path = out / "data" / "diagrams.json"
    book = _read_optional_json(book_path)
    diagrams_payload = _read_optional_json(diagrams_path)
    applied_ids: set[str] = set()

    for page in book.get("pages") or []:
        for diagram in page.get("diagrams") or []:
            if _apply_fen_to_diagram(diagram, accepted):
                applied_ids.add(str(diagram.get("diagram_id") or diagram.get("id") or ""))
    if isinstance(diagrams_payload.get("diagrams"), list):
        for diagram in diagrams_payload.get("diagrams") or []:
            if _apply_fen_to_diagram(diagram, accepted):
                applied_ids.add(str(diagram.get("diagram_id") or diagram.get("id") or ""))

    if book:
        _write_json(book_path, book)
    if diagrams_payload:
        _write_json(diagrams_path, diagrams_payload)

    try:
        from chess_study_export import build_chess_quality_dashboard, render_semantic_source_reader

        render_semantic_source_reader(out)
        build_chess_quality_dashboard(out)
    except Exception:
        pass

    payload = {
        "schema": "kindlemaster.fen_apply_runtime_acceptance.v1",
        "status": "ok",
        "accepted_input_count": len(accepted),
        "applied_count": len(applied_ids),
        "applied_ids": sorted(applied_ids),
    }
    _write_json(out / "reports" / "fen_apply_runtime_acceptance.json", payload)
    return payload


def score_fen_candidate(candidate: dict[str, Any]) -> float:
    validation = validate_fen_detailed(str(candidate.get("fen") or ""))
    score = float(candidate.get("confidence") or 0.0)
    if validation.errors:
        score -= 2.0
    if validation.normalized_fen:
        score += 0.25
    score -= 0.02 * len(candidate.get("changed_squares") or [])
    if _python_chess_valid(str(candidate.get("fen") or "")):
        score += 0.5
    else:
        score -= 0.75
    return round(score, 6)


def _direct_fen(source: dict[str, Any], *, source_crop_hash: str, source_kind: str) -> dict[str, Any]:
    fen = str(source.get("fen") or source.get("fen_candidate") or source.get("predicted_fen") or source.get("manual_fen") or "").strip()
    if not fen:
        return {}
    confidence = _float(source.get("confidence"), source.get("global_confidence"), source.get("score"))
    return _candidate_payload(
        source,
        fen,
        confidence=confidence,
        orientation=str(source.get("orientation") or "normal"),
        square_alternatives_checked=bool(_squares_have_alternatives(source.get("squares") or [])),
        square_alternatives_used=0,
        changed_squares=[],
        source_crop_hash=source_crop_hash,
        source_kind=source_kind,
    )


def _candidate_payload(
    source: dict[str, Any],
    fen: str,
    *,
    confidence: float,
    orientation: str,
    square_alternatives_checked: bool,
    square_alternatives_used: int,
    changed_squares: list[dict[str, Any]],
    source_crop_hash: str,
    source_kind: str = "beam",
) -> dict[str, Any]:
    warnings = list(source.get("warnings") or [])
    if not square_alternatives_checked:
        warnings.append("no_square_alternatives")
    return {
        "diagram_id": str(source.get("diagram_id") or source.get("id") or ""),
        "page": int(source.get("page") or 0),
        "source": "deterministic_ensemble",
        "fen": fen,
        "confidence": float(confidence or 0.0),
        "source_crop_hash": source_crop_hash,
        "warnings": sorted(set(warnings)),
        "changed_squares": changed_squares,
        "evidence": {
            "square_alternatives_checked": square_alternatives_checked,
            "square_alternatives_used": square_alternatives_used,
            "orientation_checked": [orientation],
            "source_kind": source_kind,
        },
    }


def _fast_fen_candidate_score(candidate: dict[str, Any]) -> float:
    fen = str(candidate.get("fen") or "")
    placement = fen.split()[0] if fen else ""
    score = float(candidate.get("confidence") or 0.0)
    score -= 0.02 * len(candidate.get("changed_squares") or [])
    if placement.count("K") == 1:
        score += 0.20
    else:
        score -= 0.50
    if placement.count("k") == 1:
        score += 0.20
    else:
        score -= 0.50
    ranks = placement.split("/")
    if len(ranks) == 8:
        score += 0.10
        if any(piece in ranks[0] + ranks[-1] for piece in "Pp"):
            score -= 0.50
    else:
        score -= 0.75
    return round(score, 6)


def _coerce_squares(source: dict[str, Any]) -> list[dict[str, Any]]:
    squares = list(source.get("squares") or [])
    return squares if len(squares) == 64 else []


def _square_alternatives(square: dict[str, Any], *, top_n: int) -> list[tuple[str, float]]:
    raw = square.get("alternatives") or []
    alternatives: list[tuple[str, float]] = []
    for item in raw:
        if isinstance(item, dict):
            alternatives.append((_piece_from_square(item), _float(item.get("confidence"), item.get("score"), 0.0)))
    alternatives.append((_piece_from_square(square), _float(square.get("confidence"), 0.0)))
    dedup: dict[str, float] = {}
    for piece, score in alternatives:
        dedup[piece] = max(dedup.get(piece, 0.0), score)
    return sorted(dedup.items(), key=lambda item: (item[1], PIECE_ORDER.get(item[0], 0)), reverse=True)[: max(1, top_n)]


def _piece_from_square(square: dict[str, Any]) -> str:
    value = str(square.get("piece") or square.get("class") or square.get("label") or "").strip()
    if value in {"empty", "blank", "none", "."}:
        return ""
    return value if value in "KQRBNPkqrbnp" else ""


def _uncertain_square_indices(squares: list[dict[str, Any]], *, max_uncertain_squares: int) -> set[int]:
    scored: list[tuple[float, int]] = []
    for index, square in enumerate(squares):
        alternatives = _square_alternatives(square, top_n=3)
        top = alternatives[0][1] if alternatives else 0.0
        second = alternatives[1][1] if len(alternatives) > 1 else 0.0
        uncertainty = (1.0 - top) + max(0.0, 0.08 - (top - second))
        if top < 0.90 or (top - second) < 0.08:
            scored.append((uncertainty, index))
    return {index for _score, index in sorted(scored, reverse=True)[:max(0, max_uncertain_squares)]}


def _beam_uncertain_square_options(
    base_pieces: list[str],
    base_scores: list[float],
    uncertain_options: list[tuple[int, list[tuple[str, float]]]],
    *,
    beam_width: int,
) -> Iterable[tuple[list[str], list[float]]]:
    beams: list[tuple[dict[int, tuple[str, float]], float]] = [({}, 0.0)]
    for square_index, square_options in uncertain_options:
        expanded_replacements: list[tuple[dict[int, tuple[str, float]], float]] = []
        for replacements, total in beams:
            for piece, score in square_options:
                next_replacements = dict(replacements)
                next_replacements[square_index] = (piece, float(score or 0.0))
                expanded_replacements.append((next_replacements, total + float(score or 0.0)))
        beams = sorted(expanded_replacements, key=lambda item: item[1], reverse=True)[: max(1, beam_width)]
    for replacements, _total in beams:
        pieces = list(base_pieces)
        scores = list(base_scores)
        for square_index, (piece, score) in replacements.items():
            pieces[square_index] = piece
            scores[square_index] = score
        yield pieces, scores


def _pieces_to_fen(pieces: list[str], *, side_to_move: str) -> str:
    ranks: list[str] = []
    for offset in range(0, 64, 8):
        empty = 0
        row = ""
        for piece in pieces[offset : offset + 8]:
            if not piece:
                empty += 1
                continue
            if empty:
                row += str(empty)
                empty = 0
            row += piece
        if empty:
            row += str(empty)
        ranks.append(row or "8")
    return f"{'/'.join(ranks)} {side_to_move if side_to_move in {'w', 'b'} else 'w'} - - 0 1"


def _changed_squares(squares: list[dict[str, Any]], pieces: list[str]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for index, piece in enumerate(pieces):
        original = _piece_from_square(squares[index])
        if original != piece:
            changes.append({"square": f"{FILES[index % 8]}{RANKS[index // 8]}", "from": original, "to": piece})
    return changes


def _side_to_move(source: dict[str, Any]) -> str:
    value = str(source.get("side_to_move") or source.get("active_color") or "").lower()
    if value in {"w", "white"}:
        return "w"
    if value in {"b", "black"}:
        return "b"
    return "w"


def _source_crop_hash(*sources: dict[str, Any] | None) -> str:
    for source in sources:
        if not source:
            continue
        for key in ["source_crop_hash", "crop_sha256", "crop_hash"]:
            value = str(source.get(key) or "").strip()
            if value:
                return value
        path_value = str(source.get("source_crop") or source.get("crop_path") or source.get("image_path") or "").strip()
        if path_value:
            path = Path(path_value)
            if path.is_file():
                try:
                    return crop_sha256(path)
                except OSError:
                    pass
    return ""


def _deterministic_validation(fen: str) -> dict[str, Any]:
    validation = validate_fen_detailed(fen)
    return {
        "valid": bool(validation.normalized_fen and not validation.errors),
        "normalized_fen": validation.normalized_fen,
        "errors": [asdict(issue) for issue in validation.errors],
    }


def _validation_warning_codes(fen: str) -> list[str]:
    return [issue.code for issue in validate_fen_detailed(fen).errors if issue.code in BLOCKING_VALIDATION_CODES]


def _validate_fen_passed(fen: str) -> bool:
    validation = validate_fen_detailed(fen)
    return bool(validation.normalized_fen and not validation.errors)


def _python_chess_valid(fen: str) -> bool:
    try:
        import chess  # type: ignore

        return bool(chess.Board(fen).is_valid())
    except Exception:
        return False


def _squares_have_alternatives(squares: list[dict[str, Any]]) -> bool:
    return bool(squares) and all(bool(square.get("alternatives")) for square in squares)


def _dedupe_fen_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        fen = str(row.get("fen") or "")
        if fen in seen:
            continue
        seen.add(fen)
        output.append(row)
    return output


def _load_diagrams(out: Path) -> list[dict[str, Any]]:
    diagrams_path = out / "data" / "diagrams.json"
    if diagrams_path.is_file():
        payload = json.loads(diagrams_path.read_text(encoding="utf-8"))
        return list(payload.get("diagrams") or [])
    book_path = out / "data" / "book.json"
    if not book_path.is_file():
        return []
    book = json.loads(book_path.read_text(encoding="utf-8"))
    return [diagram for page in book.get("pages") or [] for diagram in page.get("diagrams") or []]


def _template_candidate_for_diagram(
    out: Path,
    diagram: dict[str, Any],
    *,
    recognizer: Callable[[Path, dict[str, Any]], Any] | None,
    piece_templates: Any,
    profile_ready: bool,
    promoted_label_count: int,
    min_verified_labels: int,
    template_dir: Path | None,
) -> dict[str, Any]:
    diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
    crop_path = _resolve_crop_path(out, diagram)
    base: dict[str, Any] = {
        "schema": "kindlemaster.fen_template_candidate.v1",
        "diagram_id": diagram_id,
        "page": int(diagram.get("page") or 0),
        "source": "template_candidate",
        "method": "image-template-board",
        "fen": "",
        "confidence": 0.0,
        "source_crop": str(crop_path) if crop_path else "",
        "source_crop_hash": "",
        "squares": [],
        "warnings": [],
        "profile_ready": profile_ready,
        "profile_status": "ready" if profile_ready else TEMPLATE_PROFILE_NOT_READY_WARNING,
        "promoted_label_count": promoted_label_count,
        "min_verified_labels": min_verified_labels,
        "template_dir": str(template_dir or ""),
    }
    if not crop_path or not crop_path.is_file():
        base["warnings"] = ["source_crop_missing"]
        return base
    try:
        base["source_crop_hash"] = crop_sha256(crop_path)
        if recognizer is not None:
            raw_result = recognizer(crop_path, diagram)
        else:
            raw_result = _default_template_recognizer(crop_path, piece_templates)
        result = _coerce_template_result(raw_result)
        fen = str(result.get("fen") or "").strip()
        if not fen and result.get("placement"):
            side = str(result.get("side_to_move") or "w")
            fen = f"{result.get('placement')} {side if side in {'w', 'b'} else 'w'} - - 0 1"
        warnings = list(result.get("warnings") or [])
        if not profile_ready:
            warnings.append(TEMPLATE_PROFILE_NOT_READY_WARNING)
        base.update(
            {
                "fen": fen,
                "placement": result.get("placement") or (fen.split()[0] if fen else ""),
                "confidence": _float(result.get("confidence"), 0.0),
                "side_to_move": result.get("side_to_move") or "w",
                "requires_review": bool(result.get("requires_review", True)),
                "board_detected": bool(result.get("board_detected", False)),
                "squares": list(result.get("squares") or []),
                "warnings": sorted(set(warnings)),
                "deterministic_validation": _deterministic_validation(fen),
            }
        )
        return base
    except Exception as exc:
        base["warnings"] = [f"{exc.__class__.__name__}: {exc}"]
        return base


def _default_template_recognizer(crop_path: Path, piece_templates: Any) -> dict[str, Any]:
    from chess_position_recognizer import recognize_chess_position_from_image

    result = recognize_chess_position_from_image(
        crop_path.read_bytes(),
        piece_templates=piece_templates,
        min_confidence=0.92,
    )
    return result.to_dict() if hasattr(result, "to_dict") else dict(result)


def _coerce_template_result(raw_result: Any) -> dict[str, Any]:
    if hasattr(raw_result, "to_dict"):
        return dict(raw_result.to_dict())
    if isinstance(raw_result, dict):
        return dict(raw_result)
    return {}


def _resolve_template_dir(out: Path, template_dir: str | Path | None, build_report: dict[str, Any]) -> Path | None:
    if template_dir:
        return Path(template_dir)
    for key in ["template_output_dir", "template_dir", "output_dir"]:
        value = str(build_report.get(key) or "").strip()
        if value:
            return Path(value)
    default = out / "assets" / "fen_templates" / "study_manual_verified"
    return default if default.exists() else None


def _template_label_count(out: Path, build_report: dict[str, Any]) -> int:
    for key in ["promoted_label_count", "verified_label_count", "label_count", "template_count"]:
        try:
            if build_report.get(key) is not None:
                return int(build_report.get(key) or 0)
        except (TypeError, ValueError):
            pass
    labels_path = out / "review" / "fen_verified_labels.jsonl"
    return len(_read_jsonl(labels_path))


def _resolve_crop_path(out: Path, diagram: dict[str, Any]) -> Path | None:
    for key in ["source_crop", "crop_path", "image_path", "crop_rel_path"]:
        value = str(diagram.get(key) or "").strip()
        if not value:
            continue
        path = Path(value)
        if path.is_file():
            return path
        candidate = out / value
        if candidate.is_file():
            return candidate
    return None


def _apply_fen_to_diagram(diagram: dict[str, Any], accepted: dict[str, dict[str, Any]]) -> bool:
    diagram_id = str(diagram.get("diagram_id") or diagram.get("id") or "")
    item = accepted.get(diagram_id)
    if not item:
        return False
    selected = str(item.get("selected_value") or "").strip()
    if not selected:
        return False
    diagram["fen"] = selected
    diagram["fen_candidate"] = selected
    diagram["validation_status"] = "accepted"
    diagram["status"] = "accepted"
    diagram["runtime_status"] = item.get("runtime_status") or "FEN_MACHINE_ACCEPTED"
    diagram["acceptance_policy"] = item.get("acceptance_policy") or "runtime_machine_acceptance_v1"
    diagram["review_reason"] = ""
    diagram["acceptance_trace"] = item.get("acceptance_trace") or {}
    return True


def _top_blockers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        for blocker in row.get("machine_acceptance", {}).get("acceptance_blockers") or []:
            code = str(blocker.get("code") or "")
            if code:
                counts[code] = counts.get(code, 0) + 1
    return [{"key": key, "count": count} for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _top_next_actions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in rows:
        action = str(row.get("next_action") or _next_action_for_blockers(row.get("machine_acceptance", {}).get("acceptance_blockers") or []))
        if action:
            counts[action] = counts.get(action, 0) + 1
    return [{"key": key, "count": count} for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _next_action_for_blockers(blockers: Iterable[dict[str, Any]]) -> str:
    return _next_action_for_blocker_keys([str(blocker.get("code") or "") for blocker in blockers])


def _next_action_for_blocker_keys(codes: Iterable[Any]) -> str:
    blocker_codes = {str(code or "") for code in codes if str(code or "")}
    if not blocker_codes:
        return "export_allowed"
    if "template_profile_not_ready" in blocker_codes:
        return "add_verified_fen_labels"
    if "source_crop_hash_missing" in blocker_codes:
        return "regenerate_crops_with_hash"
    if "square_alternatives_not_checked" in blocker_codes or "no_square_alternatives" in blocker_codes:
        return "rerun_local_fen_recognition_with_square_alternatives"
    if "confidence_below_runtime_threshold" in blocker_codes:
        return "improve_model_or_template_confidence"
    if "score_margin_too_low" in blocker_codes or "score_margin_below_threshold" in blocker_codes:
        return "calibrate_or_improve_candidate_margin"
    if "no_template_or_model_agreement" in blocker_codes:
        return "add_template_or_model_evidence"
    if "python_chess_invalid_position" in blocker_codes or "python_chess_evidence_missing" in blocker_codes:
        return "fix_piece_recognition_or_board_alignment"
    if blocker_codes & BLOCKING_VALIDATION_CODES:
        return "fix_piece_recognition_or_board_alignment"
    return "manual_review"


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _mean(values: Iterable[float]) -> float:
    data = [float(value or 0.0) for value in values]
    return round(sum(data) / max(1, len(data)), 4)


def _float(*values: Any) -> float:
    for value in values:
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0
