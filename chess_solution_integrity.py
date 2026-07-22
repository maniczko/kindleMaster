from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

SOLUTION_INTEGRITY_SCHEMA = "kindlemaster.chess.solution_integrity.v1"
STRICT_BLOCKING_CODES = {
    "MISSING_SOLUTION_TEXT",
    "SOLUTION_STARTS_INSIDE_VARIATION",
    "SOLUTION_CONTINUATION_START",
    "NO_RECOGNIZABLE_FIRST_MOVE",
    "MISSING_FIRST_MOVE_NUMBER",
    "SIDE_TO_MOVE_MISMATCH",
    "FIRST_MOVE_NUMBER_MISMATCH",
    "UNBALANCED_VARIATION",
}

_FIGURINE_TRANSLATION = str.maketrans(
    {
        "¢": "K",
        "£": "Q",
        "¥": "B",
        "¦": "R",
        "¤": "N",
        "♔": "K",
        "♕": "Q",
        "♖": "R",
        "♗": "B",
        "♘": "N",
        "♚": "K",
        "♛": "Q",
        "♜": "R",
        "♝": "B",
        "♞": "N",
    }
)
_SAN_TOKEN_PATTERN = (
    r"(?:O-O(?:-O)?|"
    r"[KQRBN](?:[a-h1-8]{0,2})?x?[a-h][1-8](?:=[QRBN])?|"
    r"[a-h](?:x[a-h])?[1-8](?:=[QRBN])?)"
    r"[+#]?[!?]*"
)
_NUMBERED_MOVE_RE = re.compile(
    rf"^\s*(?P<number>[1-9]\d{{0,2}})\s*\.\s*(?P<black>\.\s*\.)?\s*(?P<move>{_SAN_TOKEN_PATTERN})(?![A-Za-z0-9])"
)
_ANY_NUMBERED_MOVE_RE = re.compile(
    rf"(?<!\d)(?P<number>[1-9]\d{{0,2}})\s*\.\s*(?P<black>\.\s*\.)?\s*(?P<move>{_SAN_TOKEN_PATTERN})(?![A-Za-z0-9])"
)
_SAN_TOKEN_RE = re.compile(rf"(?<![A-Za-z0-9]){_SAN_TOKEN_PATTERN}(?![A-Za-z0-9])")
_BARE_SAN_START_RE = re.compile(rf"^\s*(?P<move>{_SAN_TOKEN_PATTERN})(?![A-Za-z0-9])")
_CONTINUATION_WORD_RE = re.compile(
    r"^\s*(?:and|or|then|but|after|before|if|instead|with|followed\s+by|continuing)\b",
    re.IGNORECASE,
)
_RESULT_ONLY_RE = re.compile(r"^\s*(?:1-0|0-1|1/2-1/2|½-½|0\.5-0\.5|\*)\s*$")
_LEADING_COMMENT_RE = re.compile(r"^\s*(?:\{|\[|comment:|note:)", re.IGNORECASE)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _optional_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d{1,3}", str(value))
        return int(match.group(0)) if match else None


def normalize_side_to_move(value: Any) -> str:
    normalized = _text(value).casefold()
    if normalized in {"w", "white", "biale", "białe"}:
        return "white"
    if normalized in {"b", "black", "czarne"}:
        return "black"
    return "unknown"


@dataclass(frozen=True)
class SolutionIntegrityFinding:
    code: str
    severity: str
    message: str
    strict_blocking: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "strict_blocking": self.strict_blocking,
        }


@dataclass(frozen=True)
class SolutionIntegrityRecord:
    exercise_id: str
    exercise_number: int | None
    source_page: int
    solution_page: int
    excerpt: str
    expected_side_to_move: str
    detected_side_to_move: str
    detected_first_move_number: int | None
    numbered_move_count: int
    san_token_count: int
    status: str
    findings: tuple[SolutionIntegrityFinding, ...]

    @property
    def strict_blocked(self) -> bool:
        return any(item.strict_blocking for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "exercise_number": self.exercise_number,
            "source_page": self.source_page,
            "solution_page": self.solution_page,
            "excerpt": self.excerpt,
            "expected_side_to_move": self.expected_side_to_move,
            "detected_side_to_move": self.detected_side_to_move,
            "detected_first_move_number": self.detected_first_move_number,
            "numbered_move_count": self.numbered_move_count,
            "san_token_count": self.san_token_count,
            "status": self.status,
            "strict_blocked": self.strict_blocked,
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True)
class SolutionIntegrityReport:
    records: tuple[SolutionIntegrityRecord, ...]
    schema: str = SOLUTION_INTEGRITY_SCHEMA

    @property
    def strict_blocked(self) -> bool:
        return any(item.strict_blocked for item in self.records)

    def exit_code(self, mode: str = "warning") -> int:
        normalized = str(mode or "warning").strip().lower()
        if normalized not in {"warning", "strict"}:
            raise ValueError("mode must be 'warning' or 'strict'")
        return 1 if normalized == "strict" and self.strict_blocked else 0

    def to_dict(self) -> dict[str, Any]:
        status_counts = Counter(item.status for item in self.records)
        finding_counts = Counter(
            finding.code
            for record in self.records
            for finding in record.findings
        )
        return {
            "schema": self.schema,
            "summary": {
                "record_count": len(self.records),
                "accepted_count": status_counts.get("accepted", 0),
                "warning_count": status_counts.get("warning", 0),
                "blocked_count": status_counts.get("blocked", 0),
                "strict_blocked": self.strict_blocked,
                "warning_exit_code": self.exit_code("warning"),
                "strict_exit_code": self.exit_code("strict"),
                "finding_counts": dict(sorted(finding_counts.items())),
            },
            "records": [item.to_dict() for item in self.records],
        }


def _finding(code: str, message: str, *, severity: str = "warning") -> SolutionIntegrityFinding:
    return SolutionIntegrityFinding(
        code=code,
        severity=severity,
        message=message,
        strict_blocking=code in STRICT_BLOCKING_CODES,
    )


def analyze_solution_integrity(
    *,
    exercise_id: Any,
    exercise_number: Any = None,
    source_page: Any = 0,
    solution_page: Any = 0,
    text: Any,
    expected_side_to_move: Any = "unknown",
    expected_first_move_number: Any = None,
) -> SolutionIntegrityRecord:
    raw = _text(text)
    excerpt = raw[:200]
    expected_side = normalize_side_to_move(expected_side_to_move)
    expected_number = _optional_int(expected_first_move_number)
    findings: list[SolutionIntegrityFinding] = []

    if not raw:
        findings.append(_finding("MISSING_SOLUTION_TEXT", "Solution text is empty.", severity="error"))
        return SolutionIntegrityRecord(
            exercise_id=str(exercise_id or ""),
            exercise_number=_optional_int(exercise_number),
            source_page=int(source_page or 0),
            solution_page=int(solution_page or 0),
            excerpt="",
            expected_side_to_move=expected_side,
            detected_side_to_move="unknown",
            detected_first_move_number=None,
            numbered_move_count=0,
            san_token_count=0,
            status="blocked",
            findings=tuple(findings),
        )

    detection_text = raw.translate(_FIGURINE_TRANSLATION)
    prefix = _NUMBERED_MOVE_RE.match(detection_text)
    all_numbered = list(_ANY_NUMBERED_MOVE_RE.finditer(detection_text))
    san_tokens = list(_SAN_TOKEN_RE.finditer(detection_text))
    detected_number: int | None = None
    detected_side = "unknown"

    if detection_text.lstrip().startswith("("):
        findings.append(
            _finding(
                "SOLUTION_STARTS_INSIDE_VARIATION",
                "Solution starts with a parenthesized variation instead of the main line.",
                severity="error",
            )
        )
    if re.match(r"^\s*(?:\.\.\.|[)\]}]|[,;:])", detection_text):
        findings.append(
            _finding(
                "SOLUTION_CONTINUATION_START",
                "Solution starts with continuation punctuation or a closing delimiter.",
                severity="error",
            )
        )
    elif _CONTINUATION_WORD_RE.match(detection_text):
        findings.append(
            _finding(
                "SOLUTION_CONTINUATION_START",
                "Solution starts with continuation prose instead of the first move.",
                severity="error",
            )
        )

    if prefix:
        detected_number = int(prefix.group("number"))
        detected_side = "black" if prefix.group("black") else "white"
        if expected_side != "unknown" and detected_side != expected_side:
            findings.append(
                _finding(
                    "SIDE_TO_MOVE_MISMATCH",
                    f"Expected {expected_side} to move but the solution starts with {detected_side}.",
                    severity="error",
                )
            )
        if expected_number is not None and detected_number != expected_number:
            findings.append(
                _finding(
                    "FIRST_MOVE_NUMBER_MISMATCH",
                    f"Expected move number {expected_number} but the solution starts with {detected_number}.",
                    severity="error",
                )
            )
    else:
        if _BARE_SAN_START_RE.match(detection_text):
            findings.append(
                _finding(
                    "MISSING_FIRST_MOVE_NUMBER",
                    "Solution starts with a chess move but omits the first move number.",
                    severity="warning",
                )
            )
            detected_side = expected_side
        elif _LEADING_COMMENT_RE.match(detection_text) or (all_numbered and all_numbered[0].start() > 0):
            findings.append(
                _finding(
                    "COMMENTARY_BEFORE_FIRST_MOVE",
                    "Commentary appears before the first numbered move; verify that the key move was not lost.",
                    severity="warning",
                )
            )
            first = all_numbered[0] if all_numbered else None
            if first:
                detected_number = int(first.group("number"))
                detected_side = "black" if first.group("black") else "white"
        elif not _RESULT_ONLY_RE.fullmatch(detection_text):
            findings.append(
                _finding(
                    "NO_RECOGNIZABLE_FIRST_MOVE",
                    "No recognizable numbered first move was found.",
                    severity="error",
                )
            )

    if detection_text.count("(") != detection_text.count(")"):
        findings.append(
            _finding(
                "UNBALANCED_VARIATION",
                "Parenthesized variation delimiters are unbalanced.",
                severity="error",
            )
        )

    if not _RESULT_ONLY_RE.fullmatch(detection_text) and len(san_tokens) <= 1 and len(detection_text) < 40:
        findings.append(
            _finding(
                "SUSPICIOUSLY_SHORT_SOLUTION",
                "Solution is unusually short; verify that the continuation was not truncated.",
                severity="warning",
            )
        )

    strict_blocked = any(item.strict_blocking for item in findings)
    status = "blocked" if strict_blocked else ("warning" if findings else "accepted")
    return SolutionIntegrityRecord(
        exercise_id=str(exercise_id or ""),
        exercise_number=_optional_int(exercise_number),
        source_page=int(source_page or 0),
        solution_page=int(solution_page or 0),
        excerpt=excerpt,
        expected_side_to_move=expected_side,
        detected_side_to_move=detected_side,
        detected_first_move_number=detected_number,
        numbered_move_count=len(all_numbered),
        san_token_count=len(san_tokens),
        status=status,
        findings=tuple(findings),
    )


def analyze_solution_integrity_records(records: Iterable[Mapping[str, Any]]) -> SolutionIntegrityReport:
    analyzed: list[SolutionIntegrityRecord] = []
    for record in records:
        analyzed.append(
            analyze_solution_integrity(
                exercise_id=record.get("exercise_id"),
                exercise_number=record.get("exercise_number") or record.get("printed_number"),
                source_page=record.get("source_page"),
                solution_page=record.get("solution_page"),
                text=record.get("text") or record.get("solution_text") or record.get("book_line") or record.get("pgn"),
                expected_side_to_move=record.get("expected_side_to_move") or record.get("side_to_move"),
                expected_first_move_number=record.get("expected_first_move_number") or record.get("first_move_number"),
            )
        )
    return SolutionIntegrityReport(records=tuple(analyzed))
