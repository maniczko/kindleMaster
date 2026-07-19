from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping

from chess_exercise_navigation import validate_internal_links

GATE_SCHEMA = "kindlemaster.chess.semantic_release_gate.v1"
MODES = {"development", "strict", "release"}
DEFAULT_ALLOWED_WARNINGS = frozenset({
    "SHORT_SOLUTION_REVIEW",
    "LEADING_COMMENTARY_BEFORE_FIRST_MOVE",
    "SOLUTION_IDENTITY_REASSIGNED",
})


@dataclass(frozen=True)
class GateFinding:
    code: str
    message: str
    severity: str = "error"
    p0: bool = True
    exercise_id: str = ""
    exercise_number: str = ""
    page: int = 0
    coordinates: tuple[float, float, float, float] | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "p0": self.p0,
            "exercise_id": self.exercise_id,
            "exercise_number": self.exercise_number,
            "page": self.page,
            "coordinates": list(self.coordinates) if self.coordinates else None,
            "source": self.source,
        }


@dataclass(frozen=True)
class SemanticReleaseGateReport:
    mode: str
    findings: tuple[GateFinding, ...]
    allowed_warnings: tuple[str, ...]
    checks: Mapping[str, Any] = field(default_factory=dict)
    schema: str = field(default=GATE_SCHEMA, init=False)

    @property
    def blocking_findings(self) -> tuple[GateFinding, ...]:
        if self.mode == "development":
            return ()
        return tuple(item for item in self.findings if item.p0 or item.severity == "error")

    @property
    def exit_code(self) -> int:
        return 1 if self.blocking_findings else 0

    @property
    def status(self) -> str:
        if self.blocking_findings:
            return "failed"
        if self.findings:
            return "passed_with_findings"
        return "passed"

    def to_dict(self) -> dict[str, Any]:
        counts = Counter(item.code for item in self.findings)
        return {
            "schema": self.schema,
            "mode": self.mode,
            "status": self.status,
            "exit_code": self.exit_code,
            "summary": {
                "finding_count": len(self.findings),
                "blocking_count": len(self.blocking_findings),
                "warning_count": sum(item.severity == "warning" for item in self.findings),
                "finding_counts": dict(sorted(counts.items())),
            },
            "allowed_warnings": list(self.allowed_warnings),
            "checks": dict(self.checks),
            "findings": [item.to_dict() for item in self.findings],
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _exercise_number(exercise: Mapping[str, Any]) -> str:
    nav = _mapping(exercise.get("navigation"))
    for value in (nav.get("exercise_number"), exercise.get("printed_number"), exercise.get("exercise_number")):
        if value not in {None, ""}:
            return _text(value)
    exercise_id = _text(exercise.get("exercise_id"))
    match = re.fullmatch(r"ex_(\d+)_(\d+)", exercise_id)
    if match:
        return f"{int(match.group(1))}-{int(match.group(2))}"
    final = re.fullmatch(r"final_(\d+)", exercise_id)
    return f"F-{int(final.group(1))}" if final else ""


def _context(exercise: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(exercise.get("source"))
    return {
        "exercise_id": _text(exercise.get("exercise_id")),
        "exercise_number": _exercise_number(exercise),
        "page": int(source.get("page_number") or 0),
        "coordinates": _bbox(source.get("bounding_box")),
    }


def _finding(
    code: str,
    message: str,
    *,
    exercise: Mapping[str, Any] | None = None,
    severity: str = "error",
    p0: bool = True,
    source: str = "",
) -> GateFinding:
    return GateFinding(
        code=code,
        message=message,
        severity=severity,
        p0=p0,
        source=source,
        **(_context(exercise or {})),
    )


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: set[str] = set()
        self.hrefs: list[str] = []
        self.list_depth = 0
        self.list_item_text: list[str] = []
        self._current_li: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        if tag == "a" and values.get("href"):
            self.hrefs.append(str(values["href"]))
        if tag in {"ol", "ul"}:
            self.list_depth += 1
        if tag == "li" and self.list_depth:
            self._current_li = []

    def handle_data(self, data: str) -> None:
        if self._current_li is not None:
            self._current_li.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self._current_li is not None:
            self.list_item_text.append(_text("".join(self._current_li)))
            self._current_li = None
        if tag in {"ol", "ul"} and self.list_depth:
            self.list_depth -= 1


def _validate_documents(documents: Mapping[str, str]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    for name, body in documents.items():
        parser = _DocumentParser()
        parser.feed(str(body or ""))
        for item in parser.list_item_text:
            if re.match(r"^\d+\.(?:\.\.)?\s*[KQRBNOa-h]", item):
                findings.append(
                    _finding(
                        "CHESS_NOTATION_RENDERED_AS_HTML_LIST",
                        f"Chess notation was rendered as a list item in {name}: {item[:80]}",
                        source=str(name),
                    )
                )

    link_report = validate_internal_links(documents)
    for duplicate in link_report.duplicate_anchors:
        findings.append(
            _finding(
                "DUPLICATE_INTERNAL_ANCHOR",
                f"Duplicate internal anchor: {duplicate}.",
                source=duplicate.split("#", 1)[0],
            )
        )
    for orphan in link_report.orphan_hrefs:
        findings.append(
            _finding(
                "ORPHAN_INTERNAL_FRAGMENT",
                f"Internal link has no matching target: {orphan}.",
                source=orphan.split(" -> ", 1)[0],
            )
        )
    return findings

def _warning_codes(
    semantic_book: Mapping[str, Any], exercises: list[Mapping[str, Any]]
) -> Iterable[tuple[str, Mapping[str, Any] | None]]:
    for item in _sequence(semantic_book.get("exercise_model_warnings")):
        if isinstance(item, Mapping) and item.get("code"):
            yield str(item["code"]), None
    for exercise in exercises:
        validation = _mapping(exercise.get("validation"))
        for item in _sequence(validation.get("warnings")):
            if isinstance(item, Mapping) and item.get("code") and str(item.get("severity") or "warning") != "error":
                yield str(item["code"]), exercise
        for field_name in ("solution_integrity", "navigation"):
            payload = _mapping(exercise.get(field_name))
            for item in _sequence(payload.get("findings")):
                if isinstance(item, Mapping) and item.get("code") and str(item.get("severity") or "warning") != "error":
                    yield str(item["code"]), exercise


def evaluate_semantic_release_gate(
    semantic_book: Mapping[str, Any],
    *,
    mode: str = "development",
    allowed_warnings: Iterable[str] = DEFAULT_ALLOWED_WARNINGS,
    expected_counts: Mapping[str, Any] | None = None,
    publication_metadata: Mapping[str, Any] | None = None,
    toc_report: Mapping[str, Any] | None = None,
    fen_release_report: Mapping[str, Any] | None = None,
    documents: Mapping[str, str] | None = None,
    require_fen: bool | None = None,
) -> SemanticReleaseGateReport:
    normalized_mode = _text(mode).lower()
    if normalized_mode not in MODES:
        raise ValueError(f"Unsupported release gate mode: {mode}")
    allowlist = tuple(sorted({_text(item) for item in allowed_warnings if _text(item)}))
    allowset = set(allowlist)
    findings: list[GateFinding] = []
    checks: dict[str, Any] = {}

    exercises = [item for item in _sequence(semantic_book.get("exercises")) if isinstance(item, Mapping)]
    if not semantic_book:
        findings.append(_finding("MISSING_SEMANTIC_BOOK", "Semantic book payload is missing.", source="semantic_book"))
    if not exercises:
        findings.append(_finding("MISSING_CANONICAL_EXERCISES", "No canonical exercises are available.", source="semantic_book"))

    seen_ids: set[str] = set()
    exercise_anchors: set[str] = set()
    solution_anchors: set[str] = set()
    solution_count = 0
    fen_required = normalized_mode == "release" if require_fen is None else bool(require_fen)
    canonical_ids = {_text(item.get("exercise_id")) for item in exercises if _text(item.get("exercise_id"))}

    for exercise in exercises:
        context = _context(exercise)
        exercise_id = context["exercise_id"]
        if not exercise_id:
            findings.append(_finding("MISSING_EXERCISE_ID", "Canonical exercise ID is missing.", exercise=exercise))
        elif exercise_id in seen_ids:
            findings.append(_finding("DUPLICATE_EXERCISE_ID", f"Duplicate exercise ID {exercise_id}.", exercise=exercise))
        seen_ids.add(exercise_id)

        game = _mapping(exercise.get("game"))
        if not _text(game.get("normalized_title")):
            findings.append(_finding("MISSING_NORMALIZED_TITLE", "Normalized title is missing.", exercise=exercise))

        diagram = _mapping(exercise.get("diagram"))
        if not _text(diagram.get("diagram_id")):
            findings.append(_finding("MISSING_DIAGRAM_ID", "Diagram identity is missing.", exercise=exercise))
        solution = _mapping(exercise.get("solution"))
        if not solution:
            findings.append(_finding("MISSING_SOLUTION", "Canonical solution is missing.", exercise=exercise))
        else:
            solution_count += 1

        match = _mapping(exercise.get("solution_match"))
        if _text(match.get("status")) not in {"exact", "normalized"} or bool(match.get("production_blocked")):
            findings.append(
                _finding(
                    "CANONICAL_PAIR_NOT_ACCEPTED",
                    f"Canonical reconciliation is not accepted: {_text(match.get('status')) or 'missing'}.",
                    exercise=exercise,
                )
            )
        integrity = _mapping(exercise.get("solution_integrity"))
        if bool(integrity.get("strict_blocked")) or _text(integrity.get("status")) == "blocked":
            findings.append(_finding("SOLUTION_INTEGRITY_BLOCKED", "Solution integrity is blocked.", exercise=exercise))

        navigation = _mapping(exercise.get("navigation"))
        if _text(navigation.get("status")) != "accepted" or not bool(navigation.get("accepted", True)):
            findings.append(_finding("NAVIGATION_NOT_ACCEPTED", "Bidirectional navigation is not accepted.", exercise=exercise))
        else:
            forward = _text(navigation.get("forward_href"))
            back = _text(navigation.get("backlink_href"))
            exercise_anchor = _text(navigation.get("exercise_anchor"))
            solution_anchor = _text(navigation.get("solution_anchor"))
            if not forward or not back:
                findings.append(_finding("MISSING_BIDIRECTIONAL_LINK", "Forward link or backlink is missing.", exercise=exercise))
            if not context["exercise_number"] or context["exercise_number"] not in _text(navigation.get("forward_text")):
                findings.append(
                    _finding(
                        "LINK_TEXT_MISSING_EXERCISE_NUMBER",
                        "Forward link text does not contain the exercise number.",
                        exercise=exercise,
                    )
                )
            if exercise_anchor in exercise_anchors or solution_anchor in solution_anchors:
                findings.append(_finding("DUPLICATE_NAVIGATION_TARGET", "Exercise or solution anchor is duplicated.", exercise=exercise))
            exercise_anchors.add(exercise_anchor)
            solution_anchors.add(solution_anchor)

        fen = _text(diagram.get("fen"))
        fen_status = _text(diagram.get("fen_status") or diagram.get("review_status"))
        if fen_required and (not fen or fen_status not in {"available", "accepted", "verified"}):
            findings.append(_finding("FEN_NOT_ACCEPTED", "Published exercise lacks an accepted FEN.", exercise=exercise))

    for page in _sequence(semantic_book.get("pages")):
        if not isinstance(page, Mapping):
            continue
        for block in _sequence(page.get("blocks")):
            if not isinstance(block, Mapping):
                continue
            block_id = _text(block.get("exercise_id"))
            if block_id and block_id not in canonical_ids and _text(block.get("type")) in {"exercise", "solution", "diagram", "pgn"}:
                findings.append(
                    _finding(
                        "ORPHAN_SEMANTIC_CONTENT",
                        f"Semantic block references unknown exercise {block_id}.",
                        source=f"page:{int(page.get('page_number') or 0)}",
                    )
                )

    counts = dict(expected_counts or {})
    actual_counts = {"exercise_count": len(exercises), "solution_count": solution_count}
    checks["counts"] = {"actual": actual_counts, "expected": counts}
    if normalized_mode == "release" and not counts:
        findings.append(
            _finding(
                "SOURCE_BOUND_COUNTS_NOT_CONFIGURED",
                "Release mode requires source-bound expected counts.",
                source="expected_counts",
            )
        )
    for key, expected in counts.items():
        if key in actual_counts and int(actual_counts[key]) != int(expected):
            findings.append(_finding("COUNT_MISMATCH", f"{key} is {actual_counts[key]}, expected {expected}.", source="expected_counts"))

    metadata = dict(publication_metadata or {})
    required_metadata = ("title", "language", "identifier")
    missing_metadata = [key for key in required_metadata if not _text(metadata.get(key))]
    checks["metadata"] = {"required": list(required_metadata), "missing": missing_metadata}
    if normalized_mode == "release" and missing_metadata:
        findings.append(
            _finding(
                "PUBLICATION_METADATA_INCOMPLETE",
                f"Missing publication metadata: {', '.join(missing_metadata)}.",
                source="metadata",
            )
        )

    toc = dict(toc_report or {})
    checks["toc"] = {"status": _text(toc.get("status")) or "not_provided"}
    if normalized_mode == "release" and _text(toc.get("status")) not in {"passed", "approved"}:
        findings.append(_finding("TOC_NOT_APPROVED", "Release mode requires an approved TOC report.", source="toc"))

    fen_report = dict(fen_release_report or {})
    checks["fen_release"] = {"status": _text(fen_report.get("status")) or "not_provided"}
    if normalized_mode == "release":
        if _text(fen_report.get("status")) != "passed":
            findings.append(
                _finding(
                    "FIXED_EDITION_FEN_GATE_NOT_PASSED",
                    "Release mode requires the existing fixed-edition FEN gate (#295) to pass.",
                    source="fen_release",
                )
            )
        summary = _mapping(fen_report.get("summary"))
        false_accepted = int(summary.get("false_accepted_full_fen_count") or fen_report.get("false_accepted_full_fen_count") or 0)
        published_coverage = summary.get("published_full_fen_coverage", fen_report.get("published_full_fen_coverage"))
        if false_accepted:
            findings.append(
                _finding(
                    "FALSE_ACCEPTED_FEN",
                    f"Fixed-edition report contains {false_accepted} false accepted FEN records.",
                    source="fen_release",
                )
            )
        if published_coverage not in {None, ""} and float(published_coverage) < 1.0:
            findings.append(
                _finding(
                    "INCOMPLETE_PUBLISHED_FEN_COVERAGE",
                    f"Published full-FEN coverage is {published_coverage}, expected 1.0.",
                    source="fen_release",
                )
            )

    if documents:
        findings.extend(_validate_documents(documents))
    checks["documents"] = {"count": len(documents or {})}

    for code, exercise in _warning_codes(semantic_book, exercises):
        if code not in allowset:
            findings.append(
                _finding(
                    "UNALLOWLISTED_WARNING",
                    f"Warning code {code} is not present in the explicit release allowlist.",
                    exercise=exercise,
                    source=code,
                )
            )

    unique: list[GateFinding] = []
    seen: set[tuple[Any, ...]] = set()
    for item in findings:
        key = (item.code, item.exercise_id, item.page, item.source, item.message)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return SemanticReleaseGateReport(
        mode=normalized_mode,
        findings=tuple(unique),
        allowed_warnings=allowlist,
        checks=checks,
    )


def write_semantic_release_gate_reports(
    report: SemanticReleaseGateReport, reports_dir: str | Path
) -> tuple[Path, Path]:
    root = Path(reports_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "semantic_release_gate.json"
    markdown_path = root / "semantic_release_gate.md"
    payload = report.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Chess Semantic Release Gate",
        "",
        f"- Mode: `{report.mode}`",
        f"- Status: `{report.status}`",
        f"- Exit code: `{report.exit_code}`",
        f"- Findings: `{len(report.findings)}`",
        f"- Blocking: `{len(report.blocking_findings)}`",
        "",
        "## Findings",
        "",
    ]
    if not report.findings:
        lines.append("- None.")
    else:
        for item in report.findings:
            location = f" exercise={item.exercise_number or item.exercise_id}" if item.exercise_id else ""
            if item.page:
                location += f" page={item.page}"
            lines.append(f"- `{item.code}` [{item.severity}]{location}: {item.message}")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def load_optional_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_existing_json(paths: Iterable[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_optional_json(path)
        if payload:
            return payload
    return {}


def _publication_metadata_from_book(book: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(_mapping(book.get("publication_metadata") or book.get("metadata")))
    artifact = _mapping(book.get("artifact_manifest"))
    for key in ("title", "language", "identifier", "author"):
        if not _text(metadata.get(key)):
            metadata[key] = artifact.get(key) or book.get(f"book_{key}") or book.get(key)
    if not _text(metadata.get("title")):
        metadata["title"] = book.get("book_title") or book.get("title")
    return metadata


def run_output_semantic_release_gate(
    semantic_book: Mapping[str, Any],
    *,
    out_dir: str | Path,
    mode: str | None = None,
    book_payload: Mapping[str, Any] | None = None,
    documents: Mapping[str, str] | None = None,
    allowed_warnings: Iterable[str] = DEFAULT_ALLOWED_WARNINGS,
) -> SemanticReleaseGateReport:
    import os

    root = Path(out_dir)
    book = dict(book_payload or {})
    effective_mode = _text(mode or os.environ.get("KINDLEMASTER_CHESS_INTEGRITY_MODE") or "development").lower()
    expected_counts = dict(_mapping(semantic_book.get("expected_counts") or book.get("expected_counts")))
    if not expected_counts:
        expected_counts = _first_existing_json([root / "data" / "expected_counts.json", root / "reports" / "expected_counts.json"])
    toc_report = dict(_mapping(semantic_book.get("toc_report") or book.get("toc_report")))
    if not toc_report:
        toc_report = _first_existing_json(
            [root / "reports" / "toc_approval.json", root / "reports" / "chess_reader" / "toc_approval.json"]
        )
    fen_report = dict(_mapping(semantic_book.get("fen_release_report") or book.get("fen_release_report")))
    if not fen_report:
        fen_report = _first_existing_json(
            [
                root / "reports" / "chess_fen" / "fixed_edition_acceptance.json",
                root / "reports" / "fixed_edition_acceptance.json",
                root / "reports" / "chess_fen" / "exact_fen_release_gate.json",
            ]
        )
    report = evaluate_semantic_release_gate(
        semantic_book,
        mode=effective_mode,
        allowed_warnings=allowed_warnings,
        expected_counts=expected_counts,
        publication_metadata=_publication_metadata_from_book({**book, **dict(semantic_book)}),
        toc_report=toc_report,
        fen_release_report=fen_report,
        documents=documents,
    )
    write_semantic_release_gate_reports(report, root / "reports" / "chess_reader")
    return report


def _load_documents(root: str | Path | None) -> dict[str, str]:
    if not root:
        return {}
    base = Path(root)
    if not base.is_dir():
        return {}
    documents: dict[str, str] = {}
    for path in sorted([*base.rglob("*.html"), *base.rglob("*.xhtml")]):
        try:
            documents[path.relative_to(base).as_posix()] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return documents


def run_gate_from_files(
    semantic_json: str | Path,
    *,
    mode: str,
    reports_dir: str | Path,
    expected_counts_json: str | Path | None = None,
    metadata_json: str | Path | None = None,
    toc_report_json: str | Path | None = None,
    fen_release_report_json: str | Path | None = None,
    documents_root: str | Path | None = None,
    allowed_warnings: Iterable[str] = DEFAULT_ALLOWED_WARNINGS,
) -> SemanticReleaseGateReport:
    semantic_book = load_optional_json(semantic_json)
    report = evaluate_semantic_release_gate(
        semantic_book,
        mode=mode,
        allowed_warnings=allowed_warnings,
        expected_counts=load_optional_json(expected_counts_json),
        publication_metadata=load_optional_json(metadata_json),
        toc_report=load_optional_json(toc_report_json),
        fen_release_report=load_optional_json(fen_release_report_json),
        documents=_load_documents(documents_root),
    )
    write_semantic_release_gate_reports(report, reports_dir)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate chess semantic integrity before publication.")
    parser.add_argument("semantic_json")
    parser.add_argument("--mode", choices=sorted(MODES), default="development")
    parser.add_argument("--reports-dir", default="reports/chess_reader")
    parser.add_argument("--expected-counts-json", default="")
    parser.add_argument("--metadata-json", default="")
    parser.add_argument("--toc-report-json", default="")
    parser.add_argument("--fen-release-report-json", default="")
    parser.add_argument("--documents-root", default="")
    parser.add_argument("--allow-warning", action="append", default=[])
    args = parser.parse_args(argv)
    allowlist = set(DEFAULT_ALLOWED_WARNINGS)
    allowlist.update(args.allow_warning)
    report = run_gate_from_files(
        args.semantic_json,
        mode=args.mode,
        reports_dir=args.reports_dir,
        expected_counts_json=args.expected_counts_json or None,
        metadata_json=args.metadata_json or None,
        toc_report_json=args.toc_report_json or None,
        fen_release_report_json=args.fen_release_report_json or None,
        documents_root=args.documents_root or None,
        allowed_warnings=allowlist,
    )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
