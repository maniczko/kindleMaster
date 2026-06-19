from __future__ import annotations

from typing import Any, Mapping


TRUSTED_SIDE_TO_MOVE_EVIDENCE = {"marker", "caption", "verified_label", "exact_label"}
TRUSTED_SIDE_TO_MOVE_WARNINGS = {"side_to_move_marker_detected", "verified_exact_crop_label_used"}


def machine_accept_fen(candidate: Mapping[str, Any], *, trusted_side_to_move_evidence: bool = False) -> bool:
    """Return whether a full six-field FEN is safe for runtime publication."""
    fen = str(candidate.get("fen") or candidate.get("full_fen") or "").strip()
    if not fen:
        return False
    if bool(candidate.get("requires_review", False)):
        return False
    warnings = {str(warning) for warning in (candidate.get("warnings") or [])}
    if "side_to_move_inferred" not in warnings:
        return True
    if trusted_side_to_move_evidence:
        return True
    status = str(candidate.get("side_to_move_status") or "").strip()
    evidence = str(candidate.get("side_to_move_evidence") or "").strip()
    if status == "explicit" and evidence in TRUSTED_SIDE_TO_MOVE_EVIDENCE:
        return True
    return bool(warnings & TRUSTED_SIDE_TO_MOVE_WARNINGS)
