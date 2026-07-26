from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Iterable, Mapping


EXERCISE_INDEX_SCHEMA = "kindlemaster.chess.exercise_index.v1"
EXERCISE_ID_RE = re.compile(
    r"(?i)(?:\bEx(?:ercise)?\.?\s*)?"
    r"(?P<chapter>\d{1,3})\s*[-.\u2013\u2014]\s*(?P<number>\d{1,3})\b"
)
RESOLVED_ASSIGNMENT_STATUSES = {"exact", "consensus"}


def canonical_exercise_id(value: Any) -> str:
    match = EXERCISE_ID_RE.search(str(value or "").strip())
    if not match:
        return ""
    return f"{int(match.group('chapter'))}-{int(match.group('number'))}"


def build_source_exercise_index(
    source_payload: Mapping[str, Any],
    diagram_records: Iterable[Mapping[str, Any]],
    assignments: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the canonical solution-to-diagram index without guessing IDs."""

    source_pdf_sha256 = str(source_payload.get("source_pdf_sha256") or "")
    diagrams = [_diagram_evidence(record) for record in diagram_records]
    diagrams_by_id = {
        row["diagram_id"]: row for row in diagrams if row["diagram_id"]
    }
    solutions_by_exercise: dict[str, list[dict[str, Any]]] = {}
    for page in (source_payload.get("pages") or {}).values():
        if not isinstance(page, Mapping):
            continue
        for block in page.get("solution_blocks") or []:
            if not isinstance(block, Mapping):
                continue
            evidence = _solution_evidence(
                block,
                source_pdf_sha256=source_pdf_sha256,
            )
            if evidence["exercise_id"]:
                solutions_by_exercise.setdefault(
                    evidence["exercise_id"], []
                ).append(evidence)

    assignments_by_exercise: dict[str, list[dict[str, Any]]] = {}
    assignments_by_diagram: dict[str, list[dict[str, Any]]] = {}
    normalized_assignments: list[dict[str, Any]] = []
    for raw_assignment in assignments:
        assignment = _assignment_evidence(raw_assignment)
        normalized_assignments.append(assignment)
        if assignment["exercise_id"]:
            assignments_by_exercise.setdefault(
                assignment["exercise_id"], []
            ).append(assignment)
        if assignment["diagram_id"]:
            assignments_by_diagram.setdefault(
                assignment["diagram_id"], []
            ).append(assignment)

    exercise_ids = sorted(
        set(solutions_by_exercise) | set(assignments_by_exercise),
        key=_exercise_sort_key,
    )
    records: list[dict[str, Any]] = []
    review_queue: list[dict[str, Any]] = []
    selected_diagram_ids: set[str] = set()
    for exercise_id in exercise_ids:
        solutions = solutions_by_exercise.get(exercise_id, [])
        evidence = assignments_by_exercise.get(exercise_id, [])
        resolved = [
            row
            for row in evidence
            if row["status"] in RESOLVED_ASSIGNMENT_STATUSES
            and row["auto_accepted"]
            and row["diagram_id"]
        ]
        candidates = [
            row for row in evidence if row["status"] == "candidate"
        ]
        blockers: list[str] = []
        if len(solutions) > 1:
            blockers.append("duplicate_solution_blocks")
        if len(resolved) > 1:
            blockers.append("duplicate_resolved_diagrams")
        if len({row["diagram_id"] for row in resolved}) != len(resolved):
            blockers.append("duplicate_diagram_assignment")
        if not solutions:
            blockers.append("orphan_diagram_assignment")
        if solutions and not resolved:
            blockers.append(
                "candidate_diagram_binding"
                if candidates
                else "orphan_solution"
            )

        selected = (
            resolved[0]
            if len(solutions) == 1
            and len(resolved) == 1
            and not blockers
            else None
        )
        diagram = (
            diagrams_by_id.get(selected["diagram_id"], {})
            if selected is not None
            else {}
        )
        if selected is not None:
            selected_diagram_ids.add(selected["diagram_id"])
        resolution_status = _resolution_status(
            selected=selected,
            candidates=candidates,
            blockers=blockers,
        )
        record = {
            "schema": EXERCISE_INDEX_SCHEMA,
            "exercise_id": exercise_id,
            "resolution_status": resolution_status,
            "selected_diagram_id": (
                selected["diagram_id"] if selected is not None else ""
            ),
            "selected_diagram_fingerprint": str(
                diagram.get("diagram_fingerprint") or ""
            ),
            "selected_full_fen": str(diagram.get("full_fen") or ""),
            "selected_full_fen_trusted": bool(
                diagram.get("full_fen_trusted")
            ),
            "assignment_source": (
                selected["source"] if selected is not None else ""
            ),
            "assignment_confidence": (
                selected["confidence"] if selected is not None else 0.0
            ),
            "solutions": solutions,
            "diagram_evidence": evidence,
            "blockers": sorted(set(blockers)),
        }
        records.append(record)
        if resolution_status not in {"exact", "consensus"}:
            review_queue.append(
                _review_item(
                    exercise_id=exercise_id,
                    status=resolution_status,
                    blockers=record["blockers"],
                    solutions=solutions,
                    evidence=evidence,
                )
            )

    exercise_pages = {
        int(assignment["source_page"])
        for assignment in normalized_assignments
        if assignment["exercise_id"] and assignment["source_page"]
    }
    for diagram in diagrams:
        diagram_id = diagram["diagram_id"]
        if (
            not diagram_id
            or diagram_id in selected_diagram_ids
            or diagram_id in assignments_by_diagram
            or not diagram["confirmed_diagram"]
        ):
            continue
        review_type = (
            "orphan_diagram"
            if diagram["source_page"] in exercise_pages
            else "unclassified_diagram"
        )
        review_queue.append(
            {
                "schema": EXERCISE_INDEX_SCHEMA,
                "review_type": review_type,
                "exercise_id": "",
                "diagram_id": diagram_id,
                "diagram_fingerprint": diagram["diagram_fingerprint"],
                "source_page": diagram["source_page"],
                "bbox": diagram["bbox"],
                "blockers": [review_type],
                "model_route": {
                    "route": "diagram_label_vision_candidate",
                    "model_tier": "high_vision",
                    "purpose": "read_printed_exercise_label_from_crop",
                    "auto_accept": False,
                },
            }
        )

    status_counts = Counter(record["resolution_status"] for record in records)
    review_type_counts = Counter(
        item["review_type"] for item in review_queue
    )
    return {
        "schema": EXERCISE_INDEX_SCHEMA,
        "source_pdf_sha256": source_pdf_sha256,
        "records": records,
        "assignments": normalized_assignments,
        "review_queue": review_queue,
        "summary": {
            "exercise_count": len(records),
            "solution_block_count": sum(
                len(items) for items in solutions_by_exercise.values()
            ),
            "diagram_count": len(diagrams),
            "exact_count": status_counts["exact"],
            "consensus_count": status_counts["consensus"],
            "candidate_count": status_counts["candidate"],
            "conflict_count": status_counts["conflict"],
            "orphan_solution_count": status_counts["orphan_solution"],
            "orphan_diagram_count": review_type_counts["orphan_diagram"],
            "vision_candidate_diagram_count": (
                review_type_counts["orphan_diagram"]
                + review_type_counts["unclassified_diagram"]
            ),
            "review_queue_count": len(review_queue),
            "auto_accept_policy": (
                "unique_source_or_independent_evidence_consensus_only"
            ),
            "vision_policy": "candidate_generation_only",
        },
    }


def resolved_diagrams_by_exercise(
    exercise_index: Mapping[str, Any],
    diagram_records: Iterable[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    diagrams_by_id = {
        _diagram_id(record): dict(record)
        for record in diagram_records
        if _diagram_id(record)
    }
    result: dict[str, list[dict[str, Any]]] = {}
    for raw_record in exercise_index.get("records") or []:
        if not isinstance(raw_record, Mapping):
            continue
        if str(raw_record.get("resolution_status") or "") not in {
            "exact",
            "consensus",
        }:
            continue
        exercise_id = canonical_exercise_id(
            raw_record.get("exercise_id")
        )
        diagram_id = str(
            raw_record.get("selected_diagram_id") or ""
        ).strip()
        diagram = diagrams_by_id.get(diagram_id)
        if exercise_id and diagram is not None:
            result[exercise_id] = [diagram]
    return result


def _solution_evidence(
    block: Mapping[str, Any],
    *,
    source_pdf_sha256: str,
) -> dict[str, Any]:
    exercise_id = canonical_exercise_id(block.get("exercise_id"))
    page_number = int(block.get("page_number") or 0)
    bbox = _bbox(block.get("bbox"))
    source_label = str(block.get("source_label") or "").strip()
    identity = json.dumps(
        {
            "source_pdf_sha256": source_pdf_sha256,
            "exercise_id": exercise_id,
            "page_number": page_number,
            "bbox": bbox,
            "source_label": source_label,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "solution_block_id": hashlib.sha256(identity).hexdigest(),
        "exercise_id": exercise_id,
        "source_label": source_label,
        "source_page": page_number,
        "bbox": bbox,
        "status": str(block.get("status") or ""),
        "blockers": [
            str(item)
            for item in block.get("blockers") or []
            if str(item)
        ],
    }


def _diagram_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    full_fen = str(
        record.get("full_fen") or record.get("fen") or ""
    ).strip()
    return {
        "diagram_id": _diagram_id(record),
        "diagram_fingerprint": str(
            record.get("diagram_fingerprint")
            or record.get("verified_diagram_fingerprint")
            or ""
        ).strip(),
        "source_page": int(
            record.get("page_number") or record.get("page") or 0
        ),
        "bbox": _bbox(
            record.get("bbox")
            or record.get("board_bbox")
            or record.get("bbox_xyxy")
        ),
        "full_fen": full_fen,
        "full_fen_trusted": bool(
            full_fen
            and (
                record.get("fen_human_verified") is True
                or record.get("human_verified") is True
                or record.get("full_fen_allowed") is True
                or "HUMAN_VERIFIED"
                in str(record.get("full_fen_status") or "")
            )
        ),
        "confirmed_diagram": bool(
            record.get("confirmed_diagram", True)
            and record.get("publication_included", True)
        ),
    }


def _assignment_evidence(
    assignment: Mapping[str, Any],
) -> dict[str, Any]:
    status = str(assignment.get("status") or "candidate").strip()
    auto_accepted = bool(
        assignment.get("auto_accepted")
        if "auto_accepted" in assignment
        else status == "exact"
    )
    return {
        "exercise_id": canonical_exercise_id(
            assignment.get("exercise_id")
        ),
        "raw_label": str(
            assignment.get("raw_label")
            or assignment.get("source_label")
            or ""
        ),
        "diagram_id": str(assignment.get("diagram_id") or "").strip(),
        "diagram_fingerprint": str(
            assignment.get("diagram_fingerprint") or ""
        ).strip(),
        "source_page": int(
            assignment.get("source_page")
            or assignment.get("page_number")
            or 0
        ),
        "label_bbox": _bbox(assignment.get("label_bbox")),
        "diagram_bbox": _bbox(assignment.get("diagram_bbox")),
        "source": str(
            assignment.get("source")
            or assignment.get("exercise_label_source")
            or ""
        ),
        "confidence": _confidence(assignment.get("confidence")),
        "status": status,
        "auto_accepted": auto_accepted,
        "decision_evidence": [
            str(item)
            for item in assignment.get("decision_evidence") or []
            if str(item)
        ],
        "blockers": [
            str(item)
            for item in assignment.get("blockers") or []
            if str(item)
        ],
    }


def _review_item(
    *,
    exercise_id: str,
    status: str,
    blockers: list[str],
    solutions: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    route = (
        "diagram_label_vision_candidate"
        if status in {"candidate", "orphan_solution"}
        else "exercise_binding_conflict_review"
    )
    return {
        "schema": EXERCISE_INDEX_SCHEMA,
        "review_type": status,
        "exercise_id": exercise_id,
        "diagram_id": "",
        "diagram_fingerprint": "",
        "source_page": (
            int(solutions[0]["source_page"]) if solutions else 0
        ),
        "bbox": solutions[0]["bbox"] if solutions else [],
        "blockers": blockers,
        "candidate_assignments": evidence,
        "model_route": {
            "route": route,
            "model_tier": (
                "high_vision"
                if route == "diagram_label_vision_candidate"
                else "none"
            ),
            "purpose": (
                "read_printed_exercise_label_from_crop"
                if route == "diagram_label_vision_candidate"
                else "resolve_duplicate_or_conflicting_source_evidence"
            ),
            "auto_accept": False,
        },
    }


def _resolution_status(
    *,
    selected: Mapping[str, Any] | None,
    candidates: list[dict[str, Any]],
    blockers: list[str],
) -> str:
    if selected is not None:
        return (
            "exact"
            if selected.get("status") == "exact"
            else "consensus"
        )
    if any(
        blocker.startswith("duplicate_")
        or blocker == "orphan_diagram_assignment"
        for blocker in blockers
    ):
        return "conflict"
    if candidates:
        return "candidate"
    return "orphan_solution"


def _diagram_id(record: Mapping[str, Any]) -> str:
    return str(
        record.get("diagram_id") or record.get("id") or ""
    ).strip()


def _bbox(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return []
    try:
        return [round(float(part), 3) for part in value[:4]]
    except (TypeError, ValueError):
        return []


def _confidence(value: Any) -> float:
    try:
        return round(max(0.0, min(1.0, float(value or 0.0))), 6)
    except (TypeError, ValueError):
        return 0.0


def _exercise_sort_key(value: str) -> tuple[int, int, str]:
    exercise_id = canonical_exercise_id(value)
    if not exercise_id:
        return (10**9, 10**9, str(value))
    chapter, number = exercise_id.split("-", 1)
    return (int(chapter), int(number), exercise_id)
