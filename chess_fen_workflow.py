from __future__ import annotations

from typing import Any


CHESS_FEN_WORKFLOW_SCHEMA_VERSION = "kindlemaster.chess_fen_workflow.v1"

CANDIDATE_DETECTED = "candidate_detected"
DETERMINISTIC_CANDIDATE = "deterministic_candidate"
MANUAL_DRAFT = "manual_draft"
AI_REVIEWED = "ai_reviewed"
HUMAN_VERIFIED = "human_verified"
VALIDATION_PASSED = "validation_passed"
PROFILE_READY = "profile_ready"

REVIEW_ONLY_WORKFLOW_STATES = {
    CANDIDATE_DETECTED,
    DETERMINISTIC_CANDIDATE,
    MANUAL_DRAFT,
    AI_REVIEWED,
}


def with_workflow_state(row: dict[str, Any], workflow_state: str) -> dict[str, Any]:
    updated = dict(row)
    updated["schema_version"] = CHESS_FEN_WORKFLOW_SCHEMA_VERSION
    updated["workflow_state"] = str(workflow_state or "").strip()
    return updated


def candidate_workflow_state(candidate_fen: Any) -> str:
    return DETERMINISTIC_CANDIDATE if str(candidate_fen or "").strip() else CANDIDATE_DETECTED


def promoted_label_workflow_state(validation_passed: bool) -> str:
    return VALIDATION_PASSED if validation_passed else HUMAN_VERIFIED


def profile_workflow_state(status: Any) -> str:
    return PROFILE_READY if str(status or "").strip() == "ready" else ""
