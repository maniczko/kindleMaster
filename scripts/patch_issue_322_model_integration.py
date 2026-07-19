from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "chess_exercise_model.py"
RECONCILIATION_PATH = ROOT / "chess_exercise_reconciliation.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_reconciliation_report() -> None:
    text = RECONCILIATION_PATH.read_text(encoding="utf-8")
    old = '''            "decisions": [decision.to_dict() for decision in self.decisions],
        }
'''
    new = '''            "exercise_identities": [identity.to_dict() for identity in self.exercise_identities],
            "solution_identities": [identity.to_dict() for identity in self.solution_identities],
            "decisions": [decision.to_dict() for decision in self.decisions],
        }
'''
    text = replace_once(text, old, new, "reconciliation identity evidence")
    RECONCILIATION_PATH.write_text(text, encoding="utf-8")


def patch_model() -> None:
    text = MODEL_PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from typing import Any, Iterable, Mapping\n",
        "from typing import Any, Iterable, Mapping\n\nfrom chess_exercise_reconciliation import reconcile_exercise_solution_pairs\n",
        "reconciliation import",
    )
    text = replace_once(
        text,
        '''    solution: SolutionEvidence | None
    confidence: float
    warnings: tuple[ValidationWarning, ...] = ()
''',
        '''    solution: SolutionEvidence | None
    confidence: float
    solution_match: Mapping[str, Any] | None = None
    warnings: tuple[ValidationWarning, ...] = ()
''',
        "ChessExercise solution_match field",
    )
    text = replace_once(
        text,
        '''            "solution": self.solution.to_dict() if self.solution else None,
            "validation": {
''',
        '''            "solution": self.solution.to_dict() if self.solution else None,
            "solution_match": dict(self.solution_match) if self.solution_match else None,
            "validation": {
''',
        "ChessExercise solution_match serialization",
    )
    text = replace_once(
        text,
        '''            solution=SolutionEvidence.from_dict(solution) if isinstance(solution, Mapping) else None,
            confidence=float(validation.get("confidence") or 0.0),
            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get("warnings") or []),
''',
        '''            solution=SolutionEvidence.from_dict(solution) if isinstance(solution, Mapping) else None,
            confidence=float(validation.get("confidence") or 0.0),
            solution_match=dict(value.get("solution_match")) if isinstance(value.get("solution_match"), Mapping) else None,
            warnings=tuple(ValidationWarning.from_dict(item) for item in validation.get("warnings") or []),
''',
        "ChessExercise solution_match parsing",
    )
    text = replace_once(
        text,
        '''class ChessExerciseModel:
    exercises: tuple[ChessExercise, ...]
    warnings: tuple[ValidationWarning, ...] = ()
''',
        '''class ChessExerciseModel:
    exercises: tuple[ChessExercise, ...]
    warnings: tuple[ValidationWarning, ...] = ()
    reconciliation: Mapping[str, Any] | None = None
''',
        "ChessExerciseModel reconciliation field",
    )
    text = replace_once(
        text,
        '''            "warnings": [warning.to_dict() for warning in self.warnings],
            "exercises": [exercise.to_dict() for exercise in self.exercises],
''',
        '''            "warnings": [warning.to_dict() for warning in self.warnings],
            "solution_reconciliation": dict(self.reconciliation) if self.reconciliation else None,
            "exercises": [exercise.to_dict() for exercise in self.exercises],
''',
        "ChessExerciseModel reconciliation serialization",
    )
    text = replace_once(
        text,
        '''            exercises=tuple(ChessExercise.from_dict(item) for item in value.get("exercises") or []),
            warnings=tuple(ValidationWarning.from_dict(item) for item in value.get("warnings") or []),
        )
''',
        '''            exercises=tuple(ChessExercise.from_dict(item) for item in value.get("exercises") or []),
            warnings=tuple(ValidationWarning.from_dict(item) for item in value.get("warnings") or []),
            reconciliation=(
                dict(value.get("solution_reconciliation"))
                if isinstance(value.get("solution_reconciliation"), Mapping)
                else None
            ),
        )
''',
        "ChessExerciseModel reconciliation parsing",
    )
    text = replace_once(
        text,
        '''    components: dict[str, dict[str, Mapping[str, Any]]] = {}
    order: list[str] = []
    model_warnings: list[ValidationWarning] = []
''',
        '''    components: dict[str, dict[str, Mapping[str, Any]]] = {}
    solution_candidates: list[dict[str, Any]] = []
    order: list[str] = []
    model_warnings: list[ValidationWarning] = []
''',
        "solution candidate collection",
    )
    text = replace_once(
        text,
        '''            kind = str(block.get("type"))
            if kind in components[exercise_id]:
''',
        '''            kind = str(block.get("type"))
            if kind == "solution":
                solution_candidates.append({**dict(block), "_page_number": page_number})
                continue
            if kind in components[exercise_id]:
''',
        "solution candidate branch",
    )
    text = replace_once(
        text,
        '''    exercises: list[ChessExercise] = []
    for exercise_id in order:
''',
        '''    reconciliation_inputs: list[dict[str, Any]] = []
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
    for exercise_id in order:
''',
        "reconciliation execution",
    )
    text = replace_once(
        text,
        '''        pgn = component.get("pgn", {})
        solution = component.get("solution")
        source_page = int(
''',
        '''        pgn = component.get("pgn", {})
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
''',
        "matched solution selection",
    )
    text = replace_once(
        text,
        '''        if not solution and not str(pgn.get("pgn") or pgn.get("book_line") or "").strip():
            warnings.append(ValidationWarning("MISSING_SOLUTION", f"Exercise {exercise_id} has no solution."))

        raw_title = str((diagram or {}).get("caption") or exercise.get("raw_title") or "").strip()
''',
        '''        if not solution and not str(pgn.get("pgn") or pgn.get("book_line") or "").strip():
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
''',
        "reconciliation warnings",
    )
    text = replace_once(
        text,
        '''        if raw_title and raw_title != normalized_title:
            traces.append(CorrectionTrace("game.normalized_title", raw_title, normalized_title, "whitespace_normalization"))

        diagram_evidence = None
''',
        '''        if raw_title and raw_title != normalized_title:
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
''',
        "reassignment correction trace",
    )
    text = replace_once(
        text,
        '''                solution=solution_evidence,
                confidence=confidence,
                warnings=tuple(warnings),
''',
        '''                solution=solution_evidence,
                confidence=confidence,
                solution_match=decision.to_dict() if decision else None,
                warnings=tuple(warnings),
''',
        "solution match attachment",
    )
    text = replace_once(
        text,
        '''    return ChessExerciseModel(exercises=tuple(exercises), warnings=tuple(model_warnings))
''',
        '''    return ChessExerciseModel(
        exercises=tuple(exercises),
        warnings=tuple(model_warnings),
        reconciliation=reconciliation_report.to_dict(),
    )
''',
        "model reconciliation report",
    )
    MODEL_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    patch_reconciliation_report()
    patch_model()


if __name__ == "__main__":
    main()
