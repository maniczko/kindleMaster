from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from chess_exercise_navigation import NavigationReport, build_navigation_report
from chess_exercise_reconciliation import reconcile_exercise_solution_pairs
from chess_solution_integrity import SolutionIntegrityReport, analyze_solution_integrity


CHESS_EXERCISE_MODEL_SCHEMA = "kindlemaster.chess_exercises.v1"


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _safe_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    path = PurePosixPath(raw)
    if not Path(raw).is_absolute() and not re.match(r"^[A-Za-z]:/", raw):
        return str(path)
    for marker in ("assets", "diagrams", "review", "reports", "data"):
        if marker in path.parts:
            return str(PurePosixPath(*path.parts[path.parts.index(marker) :]))
    return path.name


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ValidationWarning:
    code: str
    message: str
    severity: str = "warning"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": self.severity}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ValidationWarning":
        return cls(
            code=str(value.get("code") or "UNKNOWN"),
            message=str(value.get("message") or ""),
            severity=str(value.get("severity") or "warning"),
        )


@dataclass(frozen=True)
class CorrectionTrace:
    field: str
    raw_value: str
    normalized_value: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CorrectionTrace":
        return cls(**{key: str(value.get(key) or "") for key in ("field", "raw_value", "normalized_value", "reason")})


@dataclass(frozen=True)
class ExerciseSource:
    page_number: int
    column: int | None = None
    bounding_box: tuple[float, float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "column": self.column,
            "bounding_box": list(self.bounding_box) if self.bounding_box else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ExerciseSource":
        column = value.get("column")
        return cls(
            page_number=int(value.get("page_number") or 0),
            column=int(column) if column not in {None, ""} else None,
            bounding_box=_bbox(value.get("bounding_box")),
        )


@dataclass(frozen=True)
class GameIdentity:
    raw_title: str = ""
    normalized_title: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"raw_title": self.raw_title, "normalized_title": self.normalized_title}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GameIdentity":
        return cls(raw_title=str(value.get("raw_title") or ""), normalized_title=str(value.get("normalized_title") or ""))


@dataclass(frozen=True)
class DiagramEvidence:
    diagram_id: str
    image_path: str
    original_image_path: str = ""
    asset_missing_reason: str = ""
    raw_caption: str = ""
    normalized_caption: str = ""
    fen: str = ""
    fen_confidence: float | None = None
    side_to_move: str = "unknown"
    side_to_move_confidence: float | None = None
    review_status: str = "needs_review"

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagram_id": self.diagram_id,
            "image_path": self.image_path,
            "original_image_path": self.original_image_path,
            "asset_missing_reason": self.asset_missing_reason,
            "raw_caption": self.raw_caption,
            "normalized_caption": self.normalized_caption,
            "fen": self.fen,
            "fen_confidence": self.fen_confidence,
            "side_to_move": self.side_to_move,
            "side_to_move_confidence": self.side_to_move_confidence,
            "review_status": self.review_status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DiagramEvidence":
        return cls(
            diagram_id=str(value.get("diagram_id") or ""),
            image_path=_safe_relative_path(value.get("image_path")),
            original_image_path=_safe_relative_path(value.get("original_image_path")),
            asset_missing_reason=str(value.get("asset_missing_reason") or ""),
            raw_caption=str(value.get("raw_caption") or ""),
            normalized_caption=str(value.get("normalized_caption") or ""),
            fen=str(value.get("fen") or ""),
            fen_confidence=_optional_float(value.get("fen_confidence")),
            side_to_move=str(value.get("side_to_move") or "unknown"),
            side_to_move_confidence=_optional_float(value.get("side_to_move_confidence")),
            review_status=str(value.get("review_status") or "needs_review"),
        )


@dataclass(frozen=True)
class SolutionEvidence:
    raw_text: str
    normalized_notation: str
    source_page_number: int = 0
    best_move: str = ""
    variations: tuple[str, ...] = ()
    commentary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "normalized_notation": self.normalized_notation,
            "source_page_number": self.source_page_number,
            "best_move": self.best_move,
            "variations": list(self.variations),
            "commentary": self.commentary,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SolutionEvidence":
        return cls(
            raw_text=str(value.get("raw_text") or ""),
            normalized_notation=str(value.get("normalized_notation") or ""),
            source_page_number=int(value.get("source_page_number") or 0),
            best_move=str(value.get("best_move") or ""),
            variations=tuple(str(item) for item in value.get("variations") or []),
            commentary=str(value.get("commentary") or ""),
        )


@dataclass(frozen=True)
class ChessExercise:
    exercise_id: str
    difficulty: str
    source: ExerciseSource
    game: GameIdentity
    diagram: DiagramEvidence | None
    solution: SolutionEvidence | None
    confidence: float
    solution_match: Mapping[str, Any] | None = None
    solution_integrity: Mapping[str, Any] | None = None
    navigation: Mapping[str, Any] | None = None
    warnings: tuple[ValidationWarning, ...] = ()
    correction_trace: tuple[CorrectionTrace, ...] = ()

    def __post_init__(self) -> None:
        if not self.exercise_id.strip():
            raise ValueError("ChessExercise requires an explicit exercise_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "difficulty": self.difficulty,
            "source": self.source.to_dict(),
            "game": self.game.to_dict(),
            "diagram": self.diagram.to_dict() if self.diagram else None,
            "solution": self.solution.to_dict() if self.solution else None,
            "solution_match": dict(self.solution_match) if self.solution_match else None,
            "solution_integrity": dict(self.solution_integrity) if self.solution_integrity else None,
            "navigation": dict(self.navigation) if self.navigation else None,
            "validation": {
                "confidence": self.confidence,
                "warnings": [warning.to_dict() for warning in self.warnings],
            },
            "correction_trace": [trace.to_dict() for trace in self.correction_trace],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChessExercise":
        validation = value.get("validation") if isinstance(value.get("validation"), Mapping) else {}
        diagram = value.get("diagram")
        solution = value.get("solution")
        return cls(
            exercise_id=str(value.get("exercise_id") or ""),
            difficulty=str(value.get("difficulty") or "unknown"),
            source=ExerciseSource.from_dict(value.get("source") or {}),
            game=GameIdentity.from_dict(value.get("game") or {}),
            diagram=DiagramEvidence.from_dict(diagram) if isinstance(diagram, Mapping) else None,
            solution=SolutionEvidence.from_dict(solution) if isinstance(solution, Mapping) else None,
            confidence=float(validation.get("confidence") or 0.0),
            solution_match=dict(value.get("solution_match")) if isinstance(value.get("solution_match"), Mapping) else None,
            solution_integrity=(
                dict(value.get("solution_integrity"))
                if isinstance(value.get("solution_integrity"), Mapping)
                else None
            ),
            navigation=(
                dict(value.get("navigation"))
                if isinstance(value.get("navigation"), Mapping)
                else None
            ),
            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get("warnings") or []),
            correction_trace=tuple(CorrectionTrace.from_dict(item) for item in value.get("correction_trace") or []),
        )


@dataclass(frozen=True)
class ChessExerciseModel:
    exercises: tuple[ChessExercise, ...]
    warnings: tuple[ValidationWarning, ...] = ()
    reconciliation: Mapping[str, Any] | None = None
    integrity: Mapping[str, Any] | None = None
    navigation: Mapping[str, Any] | None = None
    schema: str = field(default=CHESS_EXERCISE_MODEL_SCHEMA, init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "summary": {
                "exercise_count": len(self.exercises),
                "warning_count": len(self.warnings) + sum(len(item.warnings) for item in self.exercises),
            },
            "warnings": [warning.to_dict() for warning in self.warnings],
            "solution_reconciliation": dict(self.reconciliation) if self.reconciliation else None,
            "solution_integrity": dict(self.integrity) if self.integrity else None,
            "exercise_navigation": dict(self.navigation) if self.navigation else None,
            "exercises": [exercise.to_dict() for exercise in self.exercises],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChessExerciseModel":
        if value.get("schema") != CHESS_EXERCISE_MODEL_SCHEMA:
            raise ValueError(f"Unsupported chess exercise model schema: {value.get('schema')}")
        return cls(
            exercises=tuple(ChessExercise.from_dict(item) for item in value.get("exercises") or []),
            warnings=tuple(ValidationWarning.from_dict(item) for item in value.get("warnings") or []),
            reconciliation=(
                dict(value.get("solution_reconciliation"))
                if isinstance(value.get("solution_reconciliation"), Mapping)
                else None
            ),
            integrity=(
                dict(value.get("solution_integrity"))
                if isinstance(value.get("solution_integrity"), Mapping)
                else None
            ),
            navigation=(
                dict(value.get("exercise_navigation"))
                if isinstance(value.get("exercise_navigation"), Mapping)
                else None
            ),
        )


def build_chess_exercise_model(pages: Iterable[Mapping[str, Any]]) -> ChessExerciseModel:
    components: dict[str, dict[str, Mapping[str, Any]]] = {}
    solution_candidates: list[dict[str, Any]] = []
    order: list[str] = []
    model_warnings: list[ValidationWarning] = []

    for page in pages:
        page_number = int(page.get("page_number") or 0)
        for block in page.get("blocks") or []:
            if not isinstance(block, Mapping) or block.get("type") not in {"diagram", "exercise", "pgn", "solution"}:
                continue
            exercise_id = str(block.get("exercise_id") or "").strip()
            if not exercise_id:
                model_warnings.append(
                    ValidationWarning(
                        code="MISSING_EXERCISE_ID",
                        message=f"Skipped {block.get('type')} block on source page {page_number}: no explicit exercise_id.",
                        severity="error",
                    )
                )
                continue
            kind = str(block.get("type"))
            if kind == "solution":
                solution_candidates.append({**dict(block), "_page_number": page_number})
                continue
            if exercise_id not in components:
                components[exercise_id] = {}
                order.append(exercise_id)
            if kind in components[exercise_id]:
                model_warnings.append(
                    ValidationWarning(
                        code="DUPLICATE_EXERCISE_COMPONENT",
                        message=f"Exercise {exercise_id} has more than one {kind} block; the first block is authoritative.",
                        severity="error",
                    )
                )
                continue
            components[exercise_id][kind] = {**dict(block), "_page_number": page_number}

    reconciliation_inputs: list[dict[str, Any]] = []
    for exercise_id in order:
        component = components[exercise_id]
        exercise = component.get("exercise", {})
        diagram = component.get("diagram", {})
        reconciliation_inputs.append(
            {
                **dict(exercise),
                "exercise_id": exercise_id,
                "printed_number": (
                    exercise.get("printed_number")
                    or exercise.get("exercise_number")
                    or diagram.get("printed_number")
                    or diagram.get("exercise_number")
                ),
                "raw_title": (
                    diagram.get("caption")
                    or exercise.get("raw_title")
                    or exercise.get("title")
                    or exercise.get("game_title")
                ),
                "players": exercise.get("players") or diagram.get("players"),
                "location": exercise.get("location") or diagram.get("location"),
                "year": exercise.get("year") or diagram.get("year"),
                "source_page": (
                    exercise.get("source_page")
                    or diagram.get("source_page")
                    or exercise.get("_page_number")
                    or diagram.get("_page_number")
                ),
            }
        )
    reconciliation_report = reconcile_exercise_solution_pairs(reconciliation_inputs, solution_candidates)
    reconciliation_decisions = {
        decision.exercise_id: decision for decision in reconciliation_report.decisions
    }

    exercises: list[ChessExercise] = []
    integrity_records = []
    for exercise_id in order:
        component = components[exercise_id]
        exercise = component.get("exercise", {})
        diagram = component.get("diagram")
        pgn = component.get("pgn", {})
        decision = reconciliation_decisions.get(exercise_id)
        solution = None
        if (
            decision
            and decision.usable_with_review
            and decision.selected_solution_index is not None
            and 0 <= decision.selected_solution_index < len(solution_candidates)
        ):
            solution = solution_candidates[decision.selected_solution_index]
        source_page = int(
            exercise.get("source_page")
            or (diagram or {}).get("source_page")
            or exercise.get("_page_number")
            or (diagram or {}).get("_page_number")
            or 0
        )
        if source_page <= 0:
            model_warnings.append(
                ValidationWarning(
                    code="MISSING_SOURCE_LOCATION",
                    message=f"Skipped exercise {exercise_id}: no source page is available.",
                    severity="error",
                )
            )
            continue
        warnings: list[ValidationWarning] = []
        if not diagram:
            warnings.append(ValidationWarning("MISSING_DIAGRAM", f"Exercise {exercise_id} has no diagram."))
        if not solution and not str(pgn.get("pgn") or pgn.get("book_line") or "").strip():
            warnings.append(ValidationWarning("MISSING_SOLUTION", f"Exercise {exercise_id} has no solution."))
        if decision and solution_candidates:
            if decision.status == "mismatch":
                warnings.append(
                    ValidationWarning(
                        "SOLUTION_TITLE_MISMATCH",
                        f"Exercise {exercise_id} has no solution with the same canonical number and normalized title.",
                        severity="error",
                    )
                )
            elif decision.status == "ambiguous":
                warnings.append(
                    ValidationWarning(
                        "AMBIGUOUS_SOLUTION_IDENTITY",
                        f"Exercise {exercise_id} has more than one plausible canonical solution.",
                        severity="error",
                    )
                )
            elif decision.status == "unmatched":
                warnings.append(
                    ValidationWarning(
                        "UNMATCHED_SOLUTION_IDENTITY",
                        f"Exercise {exercise_id} has no canonical solution candidate.",
                        severity="error",
                    )
                )
            elif decision.status == "legacy_id":
                warnings.append(
                    ValidationWarning(
                        "CANONICAL_SOLUTION_IDENTITY_INCOMPLETE",
                        f"Exercise {exercise_id} uses same-ID fallback because number or title evidence is incomplete.",
                        severity="error",
                    )
                )
            elif decision.reassigned:
                warnings.append(
                    ValidationWarning(
                        "SOLUTION_IDENTITY_REASSIGNED",
                        f"Exercise {exercise_id} was paired to {decision.selected_solution_id} by canonical identity.",
                    )
                )

        raw_title = str((diagram or {}).get("caption") or exercise.get("raw_title") or "").strip()
        normalized_title = _text(raw_title)
        traces: list[CorrectionTrace] = []
        if raw_title and raw_title != normalized_title:
            traces.append(CorrectionTrace("game.normalized_title", raw_title, normalized_title, "whitespace_normalization"))
        if decision and decision.reassigned:
            traces.append(
                CorrectionTrace(
                    "solution.exercise_id",
                    decision.selected_solution_id,
                    exercise_id,
                    "canonical_number_and_normalized_title_match",
                )
            )

        diagram_evidence = None
        if diagram:
            raw_caption = str(diagram.get("caption") or "").strip()
            normalized_caption = _text(raw_caption)
            if raw_caption and raw_caption != normalized_caption:
                traces.append(CorrectionTrace("diagram.normalized_caption", raw_caption, normalized_caption, "whitespace_normalization"))
            diagram_evidence = DiagramEvidence(
                diagram_id=str(diagram.get("diagram_id") or ""),
                image_path=_safe_relative_path(diagram.get("board_crop_path") or diagram.get("original_crop_path")),
                original_image_path=_safe_relative_path(diagram.get("original_crop_path") or diagram.get("board_crop_path")),
                asset_missing_reason=str(diagram.get("asset_missing_reason") or ""),
                raw_caption=raw_caption,
                normalized_caption=normalized_caption,
                fen=str(diagram.get("fen") or ""),
                fen_confidence=_optional_float(diagram.get("fen_confidence")),
                side_to_move=str(diagram.get("side_to_move") or "unknown"),
                side_to_move_confidence=_optional_float(diagram.get("side_to_move_confidence")),
                review_status=str(diagram.get("review_status") or "needs_review"),
            )

        solution_evidence = None
        if solution or pgn:
            raw_solution = str((solution or {}).get("book_line") or pgn.get("book_line") or pgn.get("pgn") or "").strip()
            normalized_notation = str((solution or {}).get("pgn") or pgn.get("pgn") or _text(raw_solution)).strip()
            if raw_solution and raw_solution != normalized_notation:
                traces.append(
                    CorrectionTrace("solution.normalized_notation", raw_solution, normalized_notation, "validated_pgn_preferred")
                )
            if raw_solution or normalized_notation:
                solution_evidence = SolutionEvidence(
                    raw_text=raw_solution,
                    normalized_notation=normalized_notation,
                    source_page_number=int((solution or {}).get("solution_page") or (solution or {}).get("_page_number") or source_page),
                    best_move=str((solution or {}).get("best_move") or ""),
                    variations=tuple(str(item) for item in (solution or {}).get("variations") or []),
                    commentary=str((solution or {}).get("commentary") or ""),
                )

        exercise_number = (
            exercise.get("printed_number")
            or exercise.get("exercise_number")
            or (diagram or {}).get("printed_number")
            or (diagram or {}).get("exercise_number")
        )
        integrity_record = analyze_solution_integrity(
            exercise_id=exercise_id,
            exercise_number=exercise_number,
            source_page=source_page,
            solution_page=(solution_evidence.source_page_number if solution_evidence else source_page),
            text=(
                solution_evidence.normalized_notation or solution_evidence.raw_text
                if solution_evidence
                else ""
            ),
            expected_side_to_move=(diagram_evidence.side_to_move if diagram_evidence else "unknown"),
            expected_first_move_number=(
                (solution or {}).get("expected_first_move_number")
                or (solution or {}).get("first_move_number")
                or (diagram or {}).get("expected_first_move_number")
                or (diagram or {}).get("first_move_number")
            ),
        )
        integrity_records.append(integrity_record)
        for finding in integrity_record.findings:
            if finding.code == "MISSING_SOLUTION_TEXT" and not solution_evidence:
                continue
            warnings.append(
                ValidationWarning(
                    finding.code,
                    finding.message,
                    severity=finding.severity,
                )
            )

        confidence_values = [value for value in ((diagram_evidence.fen_confidence if diagram_evidence else None),) if value is not None]
        confidence = min(confidence_values) if confidence_values else (1.0 if not warnings else 0.0)
        exercises.append(
            ChessExercise(
                exercise_id=exercise_id,
                difficulty=str(exercise.get("difficulty") or "unknown"),
                source=ExerciseSource(
                    page_number=source_page,
                    column=int(exercise.get("source_column")) if exercise.get("source_column") not in {None, ""} else None,
                    bounding_box=_bbox(exercise.get("bounding_box")),
                ),
                game=GameIdentity(raw_title=raw_title, normalized_title=normalized_title),
                diagram=diagram_evidence,
                solution=solution_evidence,
                confidence=confidence,
                solution_match=decision.to_dict() if decision else None,
                solution_integrity=integrity_record.to_dict(),
                warnings=tuple(warnings),
                correction_trace=tuple(traces),
            )
        )

    navigation_report = build_navigation_report(
        [exercise.to_dict() for exercise in exercises],
        default_document="reader.xhtml",
    )
    navigation_by_id = {record.exercise_id: record for record in navigation_report.records}
    exercises = [
        replace(
            exercise,
            navigation=(
                navigation_by_id[exercise.exercise_id].to_dict()
                if exercise.exercise_id in navigation_by_id
                else None
            ),
        )
        for exercise in exercises
    ]

    return ChessExerciseModel(
        exercises=tuple(exercises),
        warnings=tuple(model_warnings),
        reconciliation=reconciliation_report.to_dict(),
        integrity=SolutionIntegrityReport(records=tuple(integrity_records)).to_dict(),
        navigation=navigation_report.to_dict(),
    )


def exercise_to_reader_item(exercise: Mapping[str, Any]) -> dict[str, Any]:
    source = exercise.get("source") if isinstance(exercise.get("source"), Mapping) else {}
    diagram = exercise.get("diagram") if isinstance(exercise.get("diagram"), Mapping) else {}
    solution = exercise.get("solution") if isinstance(exercise.get("solution"), Mapping) else {}
    integrity = exercise.get("solution_integrity") if isinstance(exercise.get("solution_integrity"), Mapping) else {}
    navigation = exercise.get("navigation") if isinstance(exercise.get("navigation"), Mapping) else {}
    exercise_id = str(exercise.get("exercise_id") or "")
    return {
        "exercise_id": exercise_id,
        "diagram_id": str(diagram.get("diagram_id") or ""),
        "source_page": int(source.get("page_number") or 0),
        "fen": str(diagram.get("fen") or ""),
        "fen_status": "available" if diagram.get("fen") else "unavailable",
        "side_to_move": str(diagram.get("side_to_move") or "unknown"),
        "difficulty": str(exercise.get("difficulty") or "unknown"),
        "review_status": str(diagram.get("review_status") or "needs_review"),
        "board_crop_path": str(diagram.get("image_path") or ""),
        "original_crop_path": str(diagram.get("original_image_path") or diagram.get("image_path") or ""),
        "asset_missing_reason": str(diagram.get("asset_missing_reason") or ("" if diagram.get("image_path") else "source_asset_unavailable")),
        "pgn": str(solution.get("normalized_notation") or ""),
        "book_line": str(solution.get("raw_text") or solution.get("normalized_notation") or ""),
        "best_move": str(solution.get("best_move") or ""),
        "variations": list(solution.get("variations") or []),
        "commentary": str(solution.get("commentary") or ""),
        "solution_page": int(solution.get("source_page_number") or 0),
        "solution_integrity_status": str(integrity.get("status") or "unknown"),
        "solution_integrity_findings": [
            str(item.get("code") or "")
            for item in integrity.get("findings") or []
            if isinstance(item, Mapping) and item.get("code")
        ],
        "navigation_status": str(navigation.get("status") or "blocked"),
        "exercise_anchor": str(navigation.get("exercise_anchor") or ""),
        "solution_anchor": str(navigation.get("solution_anchor") or ""),
        "solution_href": str(navigation.get("forward_href") or ""),
        "exercise_href": str(navigation.get("backlink_href") or ""),
        "solution_link_text": str(navigation.get("forward_text") or ""),
        "backlink_text": str(navigation.get("backlink_text") or ""),
        "navigation_findings": [
            str(item.get("code") or "")
            for item in navigation.get("findings") or []
            if isinstance(item, Mapping) and item.get("code")
        ],
    }
