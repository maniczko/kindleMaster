from __future__ import annotations

from collections.abc import Mapping
from typing import Any


CANONICAL_VERIFICATION_SOURCE = "human_visual"
LEGACY_REVIEW_VERIFICATION_SOURCES = {
    CANONICAL_VERIFICATION_SOURCE,
    "human_visual_piece_grid_and_marker",
}


def normalize_review_row_for_gold_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map a terminal piece-grid review row to canonical human evidence fields."""
    normalized = dict(row)
    status = str(normalized.get("label_status") or "").strip().lower()
    terminal = status in {"verified", "placement_verified", "rejected", "unreadable"}
    if terminal:
        normalized["human_verified"] = True
        normalized["verification_source"] = CANONICAL_VERIFICATION_SOURCE
        normalized["label_provenance"] = "human_visual_source_bound_piece_grid_review"
    if status in {"verified", "placement_verified"} and normalized.get("piece_labels_verified") is True:
        manual_fen = str(normalized.get("manual_fen") or "").strip()
        normalized["id"] = str(
            normalized.get("id")
            or normalized.get("diagram_id")
            or normalized.get("diagram_fingerprint")
            or ""
        )
        normalized["placement_human_verified"] = True
        normalized["square_diff_ack"] = True
        if status == "verified":
            normalized["fen"] = manual_fen
            normalized["fen_human_verified"] = True
        else:
            normalized["fen"] = ""
            normalized["fen_human_verified"] = False
    return normalized
