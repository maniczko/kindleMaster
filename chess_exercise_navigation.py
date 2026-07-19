from __future__ import annotations

import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit

NAVIGATION_SCHEMA = "kindlemaster.chess.exercise_solution_navigation.v1"
INTERNAL_LINK_VALIDATION_SCHEMA = "kindlemaster.chess.internal_link_validation.v1"

_ACCEPTED_MATCH_STATUSES = {"exact", "normalized"}
_BLOCKING_CODES = {
    "MISSING_EXERCISE_IDENTITY",
    "MISSING_EXERCISE_NUMBER",
    "MISSING_NORMALIZED_TITLE",
    "MISSING_DIAGRAM_IDENTITY",
    "MISSING_SOLUTION_IDENTITY",
    "CANONICAL_SOLUTION_IDENTITY_UNVERIFIED",
    "SOLUTION_INTEGRITY_BLOCKED",
    "DUPLICATE_EXERCISE_IDENTITY",
    "DUPLICATE_EXERCISE_TARGET",
    "DUPLICATE_SOLUTION_TARGET",
    "ORPHAN_FORWARD_LINK",
    "ORPHAN_BACKLINK",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: Any, *, fallback: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", _text(value)).strip("-").lower()
    return slug or fallback


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _clean_document(value: Any, *, default: str) -> str:
    raw = str(value or default).strip().replace("\\", "/")
    if not raw:
        raw = default
    normalized = posixpath.normpath(raw)
    while normalized.startswith("../"):
        normalized = normalized[3:]
    normalized = normalized.lstrip("/")
    return normalized if normalized not in {"", "."} else default


def _exercise_number(record: Mapping[str, Any]) -> str:
    for key in ("exercise_number", "printed_number", "number"):
        value = record.get(key)
        if value not in {None, ""}:
            return _text(value)
    exercise_id = _text(record.get("exercise_id"))
    match = re.fullmatch(r"ex_(\d+)_(\d+)", exercise_id)
    if match:
        return f"{int(match.group(1))}-{int(match.group(2))}"
    final_match = re.fullmatch(r"final_(\d+)", exercise_id)
    if final_match:
        return f"F-{int(final_match.group(1))}"
    trailing = re.search(r"(\d{1,4})$", exercise_id)
    return str(int(trailing.group(1))) if trailing else ""


def _exercise_label(exercise_id: str, exercise_number: str) -> str:
    if exercise_number:
        return f"Exercise {exercise_number}"
    return _text(exercise_id).replace("_", " ").title() or "Exercise"


def _relative_href(source_document: str, target_document: str, anchor: str) -> str:
    source = _clean_document(source_document, default="reader.xhtml")
    target = _clean_document(target_document, default="reader.xhtml")
    if source == target:
        return f"#{anchor}"
    source_dir = posixpath.dirname(source) or "."
    relative = posixpath.relpath(target, start=source_dir)
    return f"{relative}#{anchor}"


def _resolve_href(source_document: str, href: str) -> tuple[str, str] | None:
    value = str(href or "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not parts.fragment:
        return None
    source = _clean_document(source_document, default="reader.xhtml")
    if parts.path:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(source), unquote(parts.path)))
    else:
        target = source
    return (target.lstrip("/"), unquote(parts.fragment))


@dataclass(frozen=True)
class NavigationFinding:
    code: str
    message: str
    severity: str = "error"

    @property
    def blocking(self) -> bool:
        return self.code in _BLOCKING_CODES or self.severity == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ExerciseSolutionNavigation:
    exercise_id: str
    exercise_number: str
    normalized_title: str
    diagram_id: str
    solution_id: str
    exercise_document: str
    solution_document: str
    exercise_anchor: str
    solution_anchor: str
    forward_href: str
    backlink_href: str
    forward_text: str
    backlink_text: str
    status: str
    findings: tuple[NavigationFinding, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status == "accepted" and not any(item.blocking for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exercise_id": self.exercise_id,
            "exercise_number": self.exercise_number,
            "normalized_title": self.normalized_title,
            "diagram_id": self.diagram_id,
            "solution_id": self.solution_id,
            "exercise_document": self.exercise_document,
            "solution_document": self.solution_document,
            "exercise_anchor": self.exercise_anchor,
            "solution_anchor": self.solution_anchor,
            "forward_href": self.forward_href if self.accepted else "",
            "backlink_href": self.backlink_href if self.accepted else "",
            "forward_text": self.forward_text,
            "backlink_text": self.backlink_text,
            "status": self.status,
            "accepted": self.accepted,
            "findings": [item.to_dict() for item in self.findings],
        }


@dataclass(frozen=True)
class NavigationReport:
    records: tuple[ExerciseSolutionNavigation, ...]
    schema: str = field(default=NAVIGATION_SCHEMA, init=False)

    @property
    def production_blocked(self) -> bool:
        return any(not item.accepted for item in self.records)

    def record_for(self, exercise_id: str) -> ExerciseSolutionNavigation | None:
        return next((item for item in self.records if item.exercise_id == exercise_id), None)

    def to_dict(self) -> dict[str, Any]:
        finding_counts = Counter(finding.code for record in self.records for finding in record.findings)
        accepted = [item for item in self.records if item.accepted]
        return {
            "schema": self.schema,
            "summary": {
                "record_count": len(self.records),
                "accepted_count": len(accepted),
                "blocked_count": len(self.records) - len(accepted),
                "forward_link_count": len(accepted),
                "backlink_count": len(accepted),
                "orphan_count": sum(
                    finding_counts[code] for code in ("ORPHAN_FORWARD_LINK", "ORPHAN_BACKLINK")
                ),
                "duplicate_target_count": sum(
                    finding_counts[code]
                    for code in ("DUPLICATE_EXERCISE_TARGET", "DUPLICATE_SOLUTION_TARGET")
                ),
                "production_blocked": self.production_blocked,
                "finding_counts": dict(sorted(finding_counts.items())),
            },
            "records": [item.to_dict() for item in self.records],
        }


def _initial_record(record: Mapping[str, Any], *, default_document: str) -> ExerciseSolutionNavigation:
    exercise_id = _text(record.get("exercise_id"))
    number = _exercise_number(record)
    game = _mapping(record.get("game"))
    normalized_title = _text(game.get("normalized_title") or record.get("normalized_title"))
    diagram = _mapping(record.get("diagram"))
    solution = _mapping(record.get("solution"))
    match = _mapping(record.get("solution_match"))
    integrity = _mapping(record.get("solution_integrity"))
    diagram_id = _text(diagram.get("diagram_id"))
    solution_id = _text(match.get("selected_solution_id") or solution.get("solution_id") or exercise_id)
    navigation_documents = _mapping(record.get("navigation_documents"))
    exercise_document = _clean_document(
        record.get("exercise_document") or navigation_documents.get("exercise"),
        default=default_document,
    )
    solution_document = _clean_document(
        record.get("solution_document") or navigation_documents.get("solution"),
        default=default_document,
    )
    exercise_anchor = f"exercise-{_slug(exercise_id, fallback='exercise')}"
    solution_anchor = f"solution-{_slug(exercise_id, fallback='solution')}"
    label = _exercise_label(exercise_id, number)
    findings: list[NavigationFinding] = []

    if not exercise_id:
        findings.append(NavigationFinding("MISSING_EXERCISE_IDENTITY", "Navigation requires an explicit exercise ID."))
    if not number:
        findings.append(
            NavigationFinding(
                "MISSING_EXERCISE_NUMBER",
                f"{exercise_id or 'Exercise'} has no printed number for link text.",
            )
        )
    if not normalized_title:
        findings.append(
            NavigationFinding(
                "MISSING_NORMALIZED_TITLE",
                f"{exercise_id or 'Exercise'} has no normalized title.",
            )
        )
    if not diagram_id:
        findings.append(
            NavigationFinding(
                "MISSING_DIAGRAM_IDENTITY",
                f"{exercise_id or 'Exercise'} has no canonical diagram ID.",
            )
        )
    if not solution:
        findings.append(
            NavigationFinding(
                "MISSING_SOLUTION_IDENTITY",
                f"{exercise_id or 'Exercise'} has no canonical solution record.",
            )
        )
    if (
        match.get("status") not in _ACCEPTED_MATCH_STATUSES
        or bool(match.get("production_blocked", True))
        or not solution_id
    ):
        findings.append(
            NavigationFinding(
                "CANONICAL_SOLUTION_IDENTITY_UNVERIFIED",
                f"{exercise_id or 'Exercise'} does not have an accepted canonical solution identity.",
            )
        )
    if bool(integrity.get("strict_blocked")) or integrity.get("status") == "blocked":
        findings.append(
            NavigationFinding(
                "SOLUTION_INTEGRITY_BLOCKED",
                f"{exercise_id or 'Exercise'} has a solution-integrity blocker.",
            )
        )

    status = "accepted" if not findings else "blocked"
    return ExerciseSolutionNavigation(
        exercise_id=exercise_id,
        exercise_number=number,
        normalized_title=normalized_title,
        diagram_id=diagram_id,
        solution_id=solution_id,
        exercise_document=exercise_document,
        solution_document=solution_document,
        exercise_anchor=exercise_anchor,
        solution_anchor=solution_anchor,
        forward_href=_relative_href(exercise_document, solution_document, solution_anchor),
        backlink_href=_relative_href(solution_document, exercise_document, exercise_anchor),
        forward_text=f"Open solution for {label}",
        backlink_text=f"Back to {label}",
        status=status,
        findings=tuple(findings),
    )


def build_navigation_report(
    exercises: Iterable[Mapping[str, Any]],
    *,
    default_document: str = "reader.xhtml",
) -> NavigationReport:
    records = [_initial_record(dict(record), default_document=default_document) for record in exercises]
    id_counts = Counter(item.exercise_id for item in records if item.exercise_id)
    exercise_targets: dict[tuple[str, str], list[int]] = defaultdict(list)
    solution_targets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        exercise_targets[(record.exercise_document, record.exercise_anchor)].append(index)
        solution_targets[(record.solution_document, record.solution_anchor)].append(index)

    updated: list[ExerciseSolutionNavigation] = []
    for record in records:
        findings = list(record.findings)
        if record.exercise_id and id_counts[record.exercise_id] > 1:
            findings.append(
                NavigationFinding(
                    "DUPLICATE_EXERCISE_IDENTITY",
                    f"Exercise ID {record.exercise_id} appears {id_counts[record.exercise_id]} times.",
                )
            )
        if len(exercise_targets[(record.exercise_document, record.exercise_anchor)]) > 1:
            findings.append(
                NavigationFinding(
                    "DUPLICATE_EXERCISE_TARGET",
                    f"Exercise target {record.exercise_document}#{record.exercise_anchor} is not unique.",
                )
            )
        if len(solution_targets[(record.solution_document, record.solution_anchor)]) > 1:
            findings.append(
                NavigationFinding(
                    "DUPLICATE_SOLUTION_TARGET",
                    f"Solution target {record.solution_document}#{record.solution_anchor} is not unique.",
                )
            )
        updated.append(
            replace(record, status="accepted" if not findings else "blocked", findings=tuple(findings))
        )

    accepted_targets = {
        (record.exercise_document, record.exercise_anchor)
        for record in updated
        if record.accepted
    } | {
        (record.solution_document, record.solution_anchor)
        for record in updated
        if record.accepted
    }
    final: list[ExerciseSolutionNavigation] = []
    for record in updated:
        findings = list(record.findings)
        if record.accepted:
            if _resolve_href(record.exercise_document, record.forward_href) not in accepted_targets:
                findings.append(
                    NavigationFinding(
                        "ORPHAN_FORWARD_LINK",
                        f"Forward link {record.forward_href} has no accepted target.",
                    )
                )
            if _resolve_href(record.solution_document, record.backlink_href) not in accepted_targets:
                findings.append(
                    NavigationFinding(
                        "ORPHAN_BACKLINK",
                        f"Backlink {record.backlink_href} has no accepted target.",
                    )
                )
        final.append(
            replace(record, status="accepted" if not findings else "blocked", findings=tuple(findings))
        )
    return NavigationReport(records=tuple(final))


class _DocumentLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if values.get("id"):
            self.anchors.append(values["id"])
        if values.get("href"):
            self.hrefs.append(values["href"])


@dataclass(frozen=True)
class InternalLinkValidation:
    document_count: int
    anchor_count: int
    href_count: int
    duplicate_anchors: tuple[str, ...]
    orphan_hrefs: tuple[str, ...]
    schema: str = field(default=INTERNAL_LINK_VALIDATION_SCHEMA, init=False)

    @property
    def valid(self) -> bool:
        return not self.duplicate_anchors and not self.orphan_hrefs

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "summary": {
                "document_count": self.document_count,
                "anchor_count": self.anchor_count,
                "href_count": self.href_count,
                "duplicate_anchor_count": len(self.duplicate_anchors),
                "orphan_href_count": len(self.orphan_hrefs),
                "valid": self.valid,
            },
            "duplicate_anchors": list(self.duplicate_anchors),
            "orphan_hrefs": list(self.orphan_hrefs),
        }


def validate_internal_links(documents: Mapping[str, str]) -> InternalLinkValidation:
    anchors: set[tuple[str, str]] = set()
    duplicate_anchors: list[str] = []
    hrefs: list[tuple[str, str]] = []
    for raw_document, body in documents.items():
        document = _clean_document(raw_document, default="reader.xhtml")
        parser = _DocumentLinkParser()
        parser.feed(str(body or ""))
        for anchor in parser.anchors:
            key = (document, anchor)
            if key in anchors:
                duplicate_anchors.append(f"{document}#{anchor}")
            anchors.add(key)
        hrefs.extend((document, href) for href in parser.hrefs)

    orphan_hrefs: list[str] = []
    internal_href_count = 0
    for source_document, href in hrefs:
        target = _resolve_href(source_document, href)
        if target is None:
            continue
        internal_href_count += 1
        if target not in anchors:
            orphan_hrefs.append(f"{source_document} -> {href}")
    return InternalLinkValidation(
        document_count=len(documents),
        anchor_count=len(anchors),
        href_count=internal_href_count,
        duplicate_anchors=tuple(sorted(set(duplicate_anchors))),
        orphan_hrefs=tuple(sorted(set(orphan_hrefs))),
    )
