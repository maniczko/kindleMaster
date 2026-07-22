from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence


RECONCILIATION_SCHEMA = "kindlemaster.chess.solution_reconciliation.v1"
_AUTO_MATCH_MIN_SCORE = 0.82
_AMBIGUITY_MARGIN = 0.02
_NUMBER_PATTERN = re.compile(r"^\D*(\d{1,4})\D*$")
_YEAR_PATTERN = re.compile(r"\b(18\d{2}|19\d{2}|20\d{2})\b")
_PLAYER_SPLIT_PATTERN = re.compile(r"\s+(?:-|–|—|vs\.?|v\.?)\s+", re.IGNORECASE)
_IDENTITY_TRANSLITERATION = str.maketrans(
    {
        "ł": "l",
        "đ": "d",
        "ð": "d",
        "þ": "th",
        "æ": "ae",
        "œ": "oe",
        "ø": "o",
    }
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_identity_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKD", _text(value).casefold().translate(_IDENTITY_TRANSLITERATION))
    without_marks = "".join(character for character in raw if not unicodedata.combining(character))
    normalized = re.sub(r"[^a-z0-9]+", " ", without_marks)
    return re.sub(r"\s+", " ", normalized).strip()


def _first_value(record: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict, set)) and not value:
            continue
        return value
    return None


def _canonical_number(record: Mapping[str, Any]) -> int | None:
    value = _first_value(record, ("printed_number", "exercise_number", "solution_number", "number"))
    if value not in {None, ""}:
        try:
            return int(value)
        except (TypeError, ValueError):
            match = _NUMBER_PATTERN.fullmatch(str(value).strip())
            if match:
                return int(match.group(1))
    record_id = _text(_first_value(record, ("exercise_id", "solution_id", "record_id", "id")))
    match = _NUMBER_PATTERN.fullmatch(record_id)
    return int(match.group(1)) if match else None


def _raw_title(record: Mapping[str, Any]) -> str:
    return _text(
        _first_value(
            record,
            (
                "raw_title",
                "normalized_title",
                "game_title",
                "title",
                "caption",
                "solution_title",
            ),
        )
    )


def _year(record: Mapping[str, Any], title: str) -> int | None:
    value = _first_value(record, ("year", "game_year", "date"))
    if value not in {None, ""}:
        match = _YEAR_PATTERN.search(str(value))
        if match:
            return int(match.group(1))
    match = _YEAR_PATTERN.search(title)
    return int(match.group(1)) if match else None


def _players(record: Mapping[str, Any], title: str) -> tuple[str, ...]:
    explicit = record.get("players")
    values: list[str] = []
    if isinstance(explicit, (list, tuple)):
        values.extend(_text(item) for item in explicit if _text(item))
    for key in ("white", "black", "player_1", "player_2", "player1", "player2"):
        value = _text(record.get(key))
        if value:
            values.append(value)
    if not values and title:
        title_without_year = _YEAR_PATTERN.sub("", title).split(",", 1)[0].strip()
        parts = _PLAYER_SPLIT_PATTERN.split(title_without_year, maxsplit=1)
        if len(parts) == 2:
            values.extend(parts)
    return tuple(value for value in (normalize_identity_text(item) for item in values) if value)


def _neighbors(record: Mapping[str, Any]) -> tuple[int, ...]:
    values: list[Any] = []
    explicit = record.get("neighboring_numbers") or record.get("neighbors")
    if isinstance(explicit, (list, tuple)):
        values.extend(explicit)
    values.extend(record.get(key) for key in ("previous_number", "next_number") if record.get(key) not in {None, ""})
    result: list[int] = []
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return tuple(result)


def _page(record: Mapping[str, Any]) -> int:
    value = _first_value(record, ("source_page", "solution_page", "source_page_number", "page_number", "_page_number"))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _record_id(record: Mapping[str, Any], *, prefix: str, index: int) -> str:
    value = _text(_first_value(record, ("solution_id", "exercise_id", "record_id", "id")))
    return value or f"{prefix}-{index + 1}"


@dataclass(frozen=True)
class CanonicalIdentity:
    record_id: str
    printed_number: int | None
    raw_title: str
    normalized_title: str
    players: tuple[str, ...] = ()
    location: str = ""
    year: int | None = None
    difficulty: str = ""
    source_page: int = 0
    neighboring_numbers: tuple[int, ...] = ()

    @classmethod
    def from_record(cls, record: Mapping[str, Any], *, prefix: str, index: int) -> "CanonicalIdentity":
        title = _raw_title(record)
        return cls(
            record_id=_record_id(record, prefix=prefix, index=index),
            printed_number=_canonical_number(record),
            raw_title=title,
            normalized_title=normalize_identity_text(title),
            players=_players(record, title),
            location=normalize_identity_text(_first_value(record, ("location", "place", "city", "event"))),
            year=_year(record, title),
            difficulty=normalize_identity_text(record.get("difficulty")),
            source_page=_page(record),
            neighboring_numbers=_neighbors(record),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "printed_number": self.printed_number,
            "raw_title": self.raw_title,
            "normalized_title": self.normalized_title,
            "players": list(self.players),
            "location": self.location,
            "year": self.year,
            "difficulty": self.difficulty,
            "source_page": self.source_page,
            "neighboring_numbers": list(self.neighboring_numbers),
        }


@dataclass(frozen=True)
class MatchCandidate:
    solution_index: int
    solution_id: str
    score: float
    auto_compatible: bool
    title_similarity: float
    evidence: tuple[str, ...] = ()
    blocking_mismatches: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "score": self.score,
            "auto_compatible": self.auto_compatible,
            "title_similarity": self.title_similarity,
            "evidence": list(self.evidence),
            "blocking_mismatches": list(self.blocking_mismatches),
        }


@dataclass(frozen=True)
class ReconciliationDecision:
    exercise_id: str
    status: str
    score: float
    selected_solution_id: str = ""
    selected_solution_index: int | None = None
    reassigned: bool = False
    production_blocked: bool = True
    blocking_reason: str = ""
    alternatives: tuple[MatchCandidate, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status in {"exact", "normalized"} and self.selected_solution_index is not None

    @property
    def usable_with_review(self) -> bool:
        return self.accepted or self.status == "legacy_id"

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "status": self.status,
            "score": self.score,
            "selected_solution_id": self.selected_solution_id,
            "reassigned": self.reassigned,
            "production_blocked": self.production_blocked,
            "blocking_reason": self.blocking_reason,
            "alternatives": [candidate.to_dict() for candidate in self.alternatives],
        }


@dataclass(frozen=True)
class ReconciliationReport:
    decisions: tuple[ReconciliationDecision, ...]
    exercise_identities: tuple[CanonicalIdentity, ...]
    solution_identities: tuple[CanonicalIdentity, ...]
    schema: str = field(default=RECONCILIATION_SCHEMA, init=False)

    @property
    def production_blocked(self) -> bool:
        return any(decision.production_blocked for decision in self.decisions)

    def decision_for(self, exercise_id: str) -> ReconciliationDecision | None:
        return next((decision for decision in self.decisions if decision.exercise_id == exercise_id), None)

    def to_dict(self) -> dict[str, Any]:
        counts = Counter(decision.status for decision in self.decisions)
        confidence = {"high": 0, "medium": 0, "low": 0}
        for decision in self.decisions:
            bucket = "high" if decision.score >= 0.9 else "medium" if decision.score >= 0.7 else "low"
            confidence[bucket] += 1
        return {
            "schema": self.schema,
            "summary": {
                "exercise_count": len(self.exercise_identities),
                "solution_count": len(self.solution_identities),
                "matched_count": sum(counts[status] for status in ("exact", "normalized")),
                "legacy_id_count": counts["legacy_id"],
                "ambiguous_count": counts["ambiguous"],
                "mismatch_count": counts["mismatch"],
                "unmatched_count": counts["unmatched"],
                "production_blocked": self.production_blocked,
                "confidence_distribution": confidence,
            },
            "exercise_identities": [identity.to_dict() for identity in self.exercise_identities],
            "solution_identities": [identity.to_dict() for identity in self.solution_identities],
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


def _candidate_score(exercise: CanonicalIdentity, solution: CanonicalIdentity, solution_index: int) -> MatchCandidate:
    score = 0.0
    evidence: list[str] = []
    blocking: list[str] = []

    if exercise.printed_number is not None and solution.printed_number is not None:
        if exercise.printed_number == solution.printed_number:
            score += 0.5
            evidence.append("printed_number_exact")
        else:
            blocking.append("printed_number_mismatch")
    elif exercise.printed_number is not None or solution.printed_number is not None:
        evidence.append("printed_number_incomplete")

    title_similarity = 0.0
    title_exact = bool(exercise.normalized_title and exercise.normalized_title == solution.normalized_title)
    if exercise.normalized_title and solution.normalized_title:
        title_similarity = SequenceMatcher(None, exercise.normalized_title, solution.normalized_title).ratio()
        if title_exact:
            score += 0.32
            evidence.append("normalized_title_exact")
        else:
            blocking.append("normalized_title_mismatch")
            if title_similarity >= 0.8:
                evidence.append("high_similarity_title_alternative")
    else:
        evidence.append("normalized_title_incomplete")

    if exercise.players and solution.players:
        if exercise.players == solution.players or set(exercise.players) == set(solution.players):
            score += 0.06
            evidence.append("players_exact")
        elif set(exercise.players) & set(solution.players):
            score += 0.03
            evidence.append("players_partial")

    if exercise.location and solution.location and exercise.location == solution.location:
        score += 0.03
        evidence.append("location_exact")
    if exercise.year and solution.year and exercise.year == solution.year:
        score += 0.04
        evidence.append("year_exact")
    if exercise.difficulty and solution.difficulty and exercise.difficulty == solution.difficulty:
        score += 0.02
        evidence.append("difficulty_exact")

    neighbor_overlap = set(exercise.neighboring_numbers) & set(solution.neighboring_numbers)
    if neighbor_overlap:
        score += 0.02
        evidence.append("neighboring_numbers_overlap")
    if exercise.source_page and solution.source_page:
        distance = abs(exercise.source_page - solution.source_page)
        if distance <= 1:
            score += 0.01
            evidence.append("source_page_adjacent")

    auto_compatible = (
        exercise.printed_number is not None
        and solution.printed_number is not None
        and exercise.printed_number == solution.printed_number
        and title_exact
        and not blocking
        and score >= _AUTO_MATCH_MIN_SCORE
    )
    return MatchCandidate(
        solution_index=solution_index,
        solution_id=solution.record_id,
        score=round(min(score, 1.0), 4),
        auto_compatible=auto_compatible,
        title_similarity=round(title_similarity, 4),
        evidence=tuple(evidence),
        blocking_mismatches=tuple(blocking),
    )


def reconcile_exercise_solution_pairs(
    exercises: Iterable[Mapping[str, Any]],
    solutions: Iterable[Mapping[str, Any]],
) -> ReconciliationReport:
    exercise_records = [dict(record) for record in exercises]
    solution_records = [dict(record) for record in solutions]
    exercise_identities = tuple(
        CanonicalIdentity.from_record(record, prefix="exercise", index=index)
        for index, record in enumerate(exercise_records)
    )
    solution_identities = tuple(
        CanonicalIdentity.from_record(record, prefix="solution", index=index)
        for index, record in enumerate(solution_records)
    )

    rankings: list[list[MatchCandidate]] = []
    compatible_claims: dict[int, list[int]] = defaultdict(list)
    for exercise_index, exercise in enumerate(exercise_identities):
        candidates = [
            _candidate_score(exercise, solution, solution_index)
            for solution_index, solution in enumerate(solution_identities)
        ]
        candidates.sort(key=lambda item: (-item.score, item.solution_id))
        rankings.append(candidates)
        for candidate in candidates:
            if candidate.auto_compatible:
                compatible_claims[candidate.solution_index].append(exercise_index)

    decisions: list[ReconciliationDecision] = []
    for exercise_index, exercise in enumerate(exercise_identities):
        candidates = rankings[exercise_index]
        compatible = [candidate for candidate in candidates if candidate.auto_compatible]
        unique_compatible = [
            candidate
            for candidate in compatible
            if len(compatible_claims.get(candidate.solution_index, [])) == 1
        ]
        top_alternatives = tuple(candidates[:5])

        if len(unique_compatible) == 1:
            selected = unique_compatible[0]
            competing = [candidate for candidate in compatible if candidate.solution_index != selected.solution_index]
            if competing and abs(competing[0].score - selected.score) <= _AMBIGUITY_MARGIN:
                decisions.append(
                    ReconciliationDecision(
                        exercise_id=exercise.record_id,
                        status="ambiguous",
                        score=selected.score,
                        blocking_reason="multiple_canonical_matches",
                        alternatives=top_alternatives,
                    )
                )
                continue
            solution = solution_identities[selected.solution_index]
            raw_exact = exercise.raw_title == solution.raw_title and bool(exercise.raw_title)
            decisions.append(
                ReconciliationDecision(
                    exercise_id=exercise.record_id,
                    status="exact" if raw_exact else "normalized",
                    score=selected.score,
                    selected_solution_id=selected.solution_id,
                    selected_solution_index=selected.solution_index,
                    reassigned=exercise.record_id != selected.solution_id,
                    production_blocked=False,
                    alternatives=top_alternatives,
                )
            )
            continue

        if compatible:
            decisions.append(
                ReconciliationDecision(
                    exercise_id=exercise.record_id,
                    status="ambiguous",
                    score=compatible[0].score,
                    blocking_reason="canonical_match_not_one_to_one",
                    alternatives=top_alternatives,
                )
            )
            continue

        legacy_candidates = [
            candidate
            for candidate in candidates
            if candidate.solution_id == exercise.record_id
            and candidate.solution_index not in compatible_claims
        ]
        if legacy_candidates and (
            exercise.printed_number is None
            or not exercise.normalized_title
            or solution_identities[legacy_candidates[0].solution_index].printed_number is None
            or not solution_identities[legacy_candidates[0].solution_index].normalized_title
        ):
            selected = legacy_candidates[0]
            decisions.append(
                ReconciliationDecision(
                    exercise_id=exercise.record_id,
                    status="legacy_id",
                    score=selected.score,
                    selected_solution_id=selected.solution_id,
                    selected_solution_index=selected.solution_index,
                    production_blocked=True,
                    blocking_reason="canonical_identity_incomplete",
                    alternatives=top_alternatives,
                )
            )
            continue

        top = candidates[0] if candidates else None
        if top and top.blocking_mismatches:
            decisions.append(
                ReconciliationDecision(
                    exercise_id=exercise.record_id,
                    status="mismatch",
                    score=top.score,
                    blocking_reason=top.blocking_mismatches[0],
                    alternatives=top_alternatives,
                )
            )
        else:
            decisions.append(
                ReconciliationDecision(
                    exercise_id=exercise.record_id,
                    status="unmatched",
                    score=top.score if top else 0.0,
                    blocking_reason="no_canonical_solution",
                    alternatives=top_alternatives,
                )
            )

    return ReconciliationReport(
        decisions=tuple(decisions),
        exercise_identities=exercise_identities,
        solution_identities=solution_identities,
    )
