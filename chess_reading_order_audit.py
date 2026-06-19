from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


READING_ORDER_SCHEMA_VERSION = "kindlemaster.chess_reading_order_audit.v1"

HIGH_SEVERITY_WARNINGS = {
    "local_path_leaked",
    "copyable_review_only_pgn_or_fen",
    "review_pgn_visible_without_reason",
    "pgn_source_page_mismatch",
}

WARNING_SEVERITY = {
    "diagram_without_caption": "medium",
    "diagram_detached_from_commentary": "medium",
    "diagram_without_pgn_or_fen": "medium",
    "diagram_caption_order_inversion": "low",
    "pgn_without_nearby_context": "medium",
    "pgn_source_page_mismatch": "high",
    "pgn_continuation_unlinked": "medium",
    "review_pgn_visible_without_reason": "high",
    "heading_text_gap": "low",
    "ocr_noise_promoted_to_visible_reader": "medium",
    "local_path_leaked": "high",
    "copyable_review_only_pgn_or_fen": "high",
}

LOCAL_PATH_RE = re.compile(r"(?i)\b(?:file://|localhost|127\.0\.0\.1|[a-z]:[\\/])")
OCR_NOISE_RE = re.compile(r"(?:�|Ă|Ĺ|â€|[\ue000-\uf8ff])")
DIAGRAM_NUMBER_RE = re.compile(r"(?i)\bdiagram\s+(\d+(?:[-.]\d+)?)")


@dataclass
class ChessReadingOrderElement:
    id: str
    page: int
    element_type: str
    source_order: int
    text: str = ""
    status: str = ""
    record_id: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChessDiagramPgnLink:
    diagram_id: str
    pgn_id: str
    page: int
    link_reason: str
    distance: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChessReadingOrderPage:
    page: int
    elements: list[ChessReadingOrderElement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "elements": [element.to_dict() for element in self.elements],
            "warnings": list(self.warnings),
        }


@dataclass
class ChessReadingOrderReport:
    schema_version: str = READING_ORDER_SCHEMA_VERSION
    status: str = "passed"
    pages: list[ChessReadingOrderPage] = field(default_factory=list)
    links: list[ChessDiagramPgnLink] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    html_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "summary": dict(self.summary),
            "pages": [page.to_dict() for page in self.pages],
            "links": [link.to_dict() for link in self.links],
            "warnings": [dict(warning) for warning in self.warnings],
            "html_path": self.html_path,
        }

    def high_severity_warnings(self) -> list[dict[str, Any]]:
        return [warning for warning in self.warnings if warning.get("severity") == "high"]


def audit_chess_reading_order(
    *,
    pages: Iterable[Mapping[str, Any]] | None = None,
    diagram_records: Iterable[Mapping[str, Any]] | None = None,
    fen_candidates: Iterable[Mapping[str, Any]] | None = None,
    pgn_records: Iterable[Any] | None = None,
    final_html_path: str | Path | None = None,
) -> ChessReadingOrderReport:
    page_map = _build_pages(
        pages=pages,
        diagram_records=diagram_records,
        pgn_records=pgn_records,
    )
    report = ChessReadingOrderReport(
        pages=[page_map[key] for key in sorted(page_map)],
        html_path=str(final_html_path or ""),
    )
    _link_diagrams_and_pgn(report)
    _audit_pages(report)
    _audit_unlinked_records(report, fen_candidates=fen_candidates)
    _audit_final_html(report, final_html_path)
    _finalize_report(report)
    return report


def write_chess_reading_order_report(
    report: ChessReadingOrderReport,
    output_dir: str | Path,
) -> dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "html_reading_order_report.json"
    html_path = target_dir / "html_reading_order_report.html"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(render_chess_reading_order_report_html(report), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


def render_chess_reading_order_report_html(report: ChessReadingOrderReport) -> str:
    rows = []
    for warning in report.warnings:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(warning.get('severity') or ''))}</td>"
            f"<td>{html.escape(str(warning.get('code') or ''))}</td>"
            f"<td>{html.escape(str(warning.get('page') or ''))}</td>"
            f"<td>{html.escape(str(warning.get('element_id') or ''))}</td>"
            f"<td>{html.escape(str(warning.get('message') or ''))}</td>"
            "</tr>"
        )
    page_sections = []
    for page in report.pages:
        items = "".join(
            f"<li><strong>{html.escape(element.element_type)}</strong> "
            f"#{html.escape(element.id)} order={element.source_order} "
            f"{html.escape(element.text[:160])}</li>"
            for element in page.elements
        )
        page_sections.append(f"<section><h2>Page {page.page}</h2><ul>{items}</ul></section>")
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<title>Chess HTML/PGN Reading Order Audit</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;line-height:1.45}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0}"
        "td,th{border:1px solid #ddd;padding:.45rem;text-align:left}"
        "th{background:#f5f5f5}.status-failed{color:#b00020;font-weight:700}"
        ".status-passed{color:#146c2e;font-weight:700}</style></head><body>"
        "<h1>Chess HTML/PGN Reading Order Audit</h1>"
        f"<p>Status: <span class=\"status-{html.escape(report.status)}\">{html.escape(report.status)}</span></p>"
        f"<p>Warnings: {len(report.warnings)} | High severity: {len(report.high_severity_warnings())}</p>"
        "<h2>Warnings</h2><table><thead><tr><th>Severity</th><th>Code</th><th>Page</th>"
        "<th>Element</th><th>Message</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table><h2>Pages</h2>"
        + "".join(page_sections)
        + "</body></html>"
    )


def _build_pages(
    *,
    pages: Iterable[Mapping[str, Any]] | None,
    diagram_records: Iterable[Mapping[str, Any]] | None,
    pgn_records: Iterable[Any] | None,
) -> dict[int, ChessReadingOrderPage]:
    page_map: dict[int, ChessReadingOrderPage] = {}
    for page_payload in pages or []:
        page_num = _safe_int(page_payload.get("page") or page_payload.get("page_num") or 1)
        page = page_map.setdefault(page_num, ChessReadingOrderPage(page=page_num))
        for index, raw_element in enumerate(list(page_payload.get("elements") or [])):
            element = _element_from_payload(raw_element, page_num, index)
            page.elements.append(element)

    for index, diagram in enumerate(diagram_records or []):
        page_num = _safe_int(diagram.get("page") or diagram.get("page_num") or 1)
        page = page_map.setdefault(page_num, ChessReadingOrderPage(page=page_num))
        page.elements.append(_diagram_element(diagram, page_num, len(page.elements), index))

    for index, record in enumerate(pgn_records or []):
        record_dict = _record_to_mapping(record)
        source_pages = record_dict.get("source_pages") or []
        page_num = _safe_int(source_pages[0] if source_pages else record_dict.get("page") or 1)
        page = page_map.setdefault(page_num, ChessReadingOrderPage(page=page_num))
        page.elements.append(_pgn_element(record_dict, page_num, len(page.elements), index))

    if not page_map:
        page_map[1] = ChessReadingOrderPage(page=1)
    for page in page_map.values():
        page.elements.sort(key=lambda element: (element.source_order, element.id))
    return page_map


def _element_from_payload(raw_element: Mapping[str, Any], page_num: int, fallback_order: int) -> ChessReadingOrderElement:
    element_type = str(raw_element.get("type") or raw_element.get("element_type") or "text").strip() or "text"
    return ChessReadingOrderElement(
        id=str(raw_element.get("id") or f"p{page_num}-e{fallback_order}"),
        page=page_num,
        element_type=element_type,
        source_order=_safe_int(raw_element.get("source_order"), fallback_order),
        text=str(raw_element.get("text") or raw_element.get("caption") or raw_element.get("html") or ""),
        status=str(raw_element.get("status") or ""),
        record_id=str(raw_element.get("record_id") or raw_element.get("id") or ""),
        warnings=[str(warning) for warning in list(raw_element.get("warnings") or []) if str(warning).strip()],
    )


def _diagram_element(diagram: Mapping[str, Any], page_num: int, source_order: int, index: int) -> ChessReadingOrderElement:
    caption = str(diagram.get("caption") or diagram.get("title") or diagram.get("diagram_number") or diagram.get("filename") or "")
    status = "requires_review" if bool(diagram.get("requires_review", not diagram.get("fen"))) else "accepted"
    return ChessReadingOrderElement(
        id=str(diagram.get("id") or diagram.get("filename") or f"diagram-{page_num}-{index}"),
        page=page_num,
        element_type="diagram",
        source_order=_safe_int(diagram.get("source_order"), source_order),
        text=caption,
        status=status,
        record_id=str(diagram.get("id") or diagram.get("filename") or ""),
    )


def _pgn_element(record: Mapping[str, Any], page_num: int, source_order: int, index: int) -> ChessReadingOrderElement:
    status = str(record.get("status") or ("accepted" if record.get("pgn") else "requires_review"))
    text = str(record.get("title") or record.get("raw_text") or record.get("movetext") or record.get("pgn") or "")
    return ChessReadingOrderElement(
        id=str(record.get("id") or f"pgn-{page_num}-{index}"),
        page=page_num,
        element_type="pgn",
        source_order=_safe_int(record.get("source_order"), source_order),
        text=text,
        status=status,
        record_id=str(record.get("id") or ""),
        warnings=[str(warning) for warning in list(record.get("warnings") or []) if str(warning).strip()],
    )


def _record_to_mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return dict(record)
    if hasattr(record, "to_dict"):
        return dict(record.to_dict())
    return {
        "id": str(getattr(record, "id", "")),
        "source_pages": list(getattr(record, "source_pages", []) or []),
        "title": str(getattr(record, "title", "")),
        "movetext": str(getattr(record, "movetext", "")),
        "pgn": str(getattr(record, "pgn", "")),
        "status": str(getattr(record, "status", "")),
        "warnings": list(getattr(record, "warnings", []) or []),
        "fen": str(getattr(record, "fen", "")),
        "raw_text": str(getattr(record, "raw_text", "")),
    }


def _link_diagrams_and_pgn(report: ChessReadingOrderReport) -> None:
    for page in report.pages:
        diagrams = [element for element in page.elements if element.element_type == "diagram"]
        pgns = [element for element in page.elements if element.element_type == "pgn"]
        for diagram in diagrams:
            best: tuple[int, ChessReadingOrderElement] | None = None
            for pgn in pgns:
                distance = abs(pgn.source_order - diagram.source_order)
                if best is None or distance < best[0]:
                    best = (distance, pgn)
            if best is not None and best[0] <= 3:
                report.links.append(
                    ChessDiagramPgnLink(
                        diagram_id=diagram.id,
                        pgn_id=best[1].id,
                        page=page.page,
                        link_reason="same_page_nearby",
                        distance=best[0],
                    )
                )


def _audit_pages(report: ChessReadingOrderReport) -> None:
    linked_diagrams = {link.diagram_id for link in report.links}
    linked_pgns = {link.pgn_id for link in report.links}
    for page in report.pages:
        for index, element in enumerate(page.elements):
            _audit_common_element(report, page, element)
            if element.element_type == "diagram":
                _audit_diagram(report, page, element, index, linked_diagrams)
            elif element.element_type == "pgn":
                _audit_pgn(report, page, element, index, linked_pgns)
            elif element.element_type == "heading":
                next_text = _nearby_text(page.elements, index, direction=1, max_distance=2)
                if not next_text:
                    _add_warning(report, page, element, "heading_text_gap", "Heading is not followed by nearby explanatory text.")


def _audit_common_element(report: ChessReadingOrderReport, page: ChessReadingOrderPage, element: ChessReadingOrderElement) -> None:
    if LOCAL_PATH_RE.search(element.text):
        _add_warning(report, page, element, "local_path_leaked", "Visible reader content contains a local path or localhost URL.")
    if element.text and OCR_NOISE_RE.search(element.text):
        _add_warning(report, page, element, "ocr_noise_promoted_to_visible_reader", "Visible reader content contains OCR/mojibake noise.")
    if element.status == "requires_review" and "copy-pgn-button" in element.text:
        _add_warning(report, page, element, "copyable_review_only_pgn_or_fen", "Review-only PGN/FEN appears copyable.")


def _audit_diagram(
    report: ChessReadingOrderReport,
    page: ChessReadingOrderPage,
    element: ChessReadingOrderElement,
    index: int,
    linked_diagrams: set[str],
) -> None:
    caption_before = _nearby_element(page.elements, index, {"caption", "text"}, direction=-1, max_distance=2)
    caption_after = _nearby_element(page.elements, index, {"caption"}, direction=1, max_distance=1)
    if not element.text and not caption_before and not caption_after:
        _add_warning(report, page, element, "diagram_without_caption", "Diagram has no nearby caption.")
    if caption_after and not caption_before:
        _add_warning(report, page, element, "diagram_caption_order_inversion", "Diagram caption appears after the diagram.")
    commentary = _nearby_element(page.elements, index, {"text", "commentary"}, direction=-1, max_distance=2) or _nearby_element(
        page.elements, index, {"text", "commentary"}, direction=1, max_distance=2
    )
    if not commentary:
        _add_warning(report, page, element, "diagram_detached_from_commentary", "Diagram is detached from nearby commentary.")
    has_fen_signal = "fen" in element.text.lower() or element.status == "accepted"
    if element.id not in linked_diagrams and not has_fen_signal:
        _add_warning(report, page, element, "diagram_without_pgn_or_fen", "Diagram has no nearby PGN link or accepted FEN signal.")


def _audit_pgn(
    report: ChessReadingOrderReport,
    page: ChessReadingOrderPage,
    element: ChessReadingOrderElement,
    index: int,
    linked_pgns: set[str],
) -> None:
    source_had_review_reason = bool(element.warnings)
    context = _nearby_element(page.elements, index, {"text", "commentary", "diagram", "caption"}, direction=-1, max_distance=3)
    if not context:
        _add_warning(report, page, element, "pgn_without_nearby_context", "PGN has no nearby context or diagram.")
    if element.id not in linked_pgns and _looks_like_continuation(element.text):
        _add_warning(report, page, element, "pgn_continuation_unlinked", "PGN continuation is not linked to a previous record.")
    if element.status == "requires_review" and not source_had_review_reason:
        _add_warning(report, page, element, "review_pgn_visible_without_reason", "Review-only PGN is visible without a review reason.")
    if element.status == "requires_review" and "copy-pgn-button" in element.text:
        _add_warning(report, page, element, "copyable_review_only_pgn_or_fen", "Review-only PGN/FEN appears copyable.")


def _audit_unlinked_records(
    report: ChessReadingOrderReport,
    *,
    fen_candidates: Iterable[Mapping[str, Any]] | None,
) -> None:
    fen_pages = {_safe_int(candidate.get("page") or candidate.get("page_num") or 0) for candidate in fen_candidates or []}
    for page in report.pages:
        for element in page.elements:
            if element.element_type == "pgn" and fen_pages and page.page not in fen_pages:
                _add_warning(report, page, element, "pgn_source_page_mismatch", "PGN source page does not match available FEN candidate pages.")


def _audit_final_html(report: ChessReadingOrderReport, final_html_path: str | Path | None) -> None:
    if not final_html_path:
        return
    path = Path(final_html_path)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    synthetic_page = report.pages[0] if report.pages else ChessReadingOrderPage(page=1)
    synthetic_element = ChessReadingOrderElement(
        id="final-html",
        page=synthetic_page.page,
        element_type="html",
        source_order=999999,
        text=text[:20000],
    )
    if LOCAL_PATH_RE.search(text):
        _add_warning(report, synthetic_page, synthetic_element, "local_path_leaked", "Final HTML contains a local path or localhost URL.")
    review_copyable = re.search(r"(?is)(requires-review|requires_review|Do weryfikacji).{0,800}(copy-pgn-button|Kopiuj (?:PGN|FEN))", text)
    if review_copyable:
        _add_warning(report, synthetic_page, synthetic_element, "copyable_review_only_pgn_or_fen", "Final HTML exposes copy controls near review-only chess content.")


def _finalize_report(report: ChessReadingOrderReport) -> None:
    codes = [warning["code"] for warning in report.warnings]
    high_count = len(report.high_severity_warnings())
    report.summary = {
        "page_count": len(report.pages),
        "element_count": sum(len(page.elements) for page in report.pages),
        "link_count": len(report.links),
        "warning_count": len(report.warnings),
        "high_severity_warning_count": high_count,
        "warning_buckets": {code: codes.count(code) for code in sorted(set(codes))},
    }
    report.status = "failed" if high_count else "passed_with_warnings" if report.warnings else "passed"


def _add_warning(
    report: ChessReadingOrderReport,
    page: ChessReadingOrderPage,
    element: ChessReadingOrderElement,
    code: str,
    message: str,
) -> None:
    if code not in element.warnings:
        element.warnings.append(code)
    if code not in page.warnings:
        page.warnings.append(code)
    severity = WARNING_SEVERITY.get(code, "medium")
    warning = {
        "code": code,
        "severity": severity,
        "page": page.page,
        "element_id": element.id,
        "element_type": element.element_type,
        "message": message,
    }
    if warning not in report.warnings:
        report.warnings.append(warning)


def _nearby_element(
    elements: list[ChessReadingOrderElement],
    index: int,
    types: set[str],
    *,
    direction: int,
    max_distance: int,
) -> ChessReadingOrderElement | None:
    for distance in range(1, max_distance + 1):
        probe_index = index + distance * direction
        if probe_index < 0 or probe_index >= len(elements):
            continue
        if elements[probe_index].element_type in types and elements[probe_index].text.strip():
            return elements[probe_index]
    return None


def _nearby_text(elements: list[ChessReadingOrderElement], index: int, *, direction: int, max_distance: int) -> str:
    element = _nearby_element(elements, index, {"text", "commentary"}, direction=direction, max_distance=max_distance)
    return element.text if element else ""


def _looks_like_continuation(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(re.match(r"^(?:\d+\.\.\.|\.\.\.|[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8])", stripped))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "ChessDiagramPgnLink",
    "ChessReadingOrderElement",
    "ChessReadingOrderPage",
    "ChessReadingOrderReport",
    "HIGH_SEVERITY_WARNINGS",
    "audit_chess_reading_order",
    "render_chess_reading_order_report_html",
    "write_chess_reading_order_report",
]
