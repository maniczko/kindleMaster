from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import fitz
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import validate_fen


STYLE_PX_RE = re.compile(r"(?P<name>left|top|width|height)\s*:\s*(?P<value>-?\d+(?:\.\d+)?)px", re.IGNORECASE)
LOCALHOST_RE = re.compile(r"\b(?:localhost|127\.0\.0\.1|kindlemaster\.localhost)\b", re.IGNORECASE)


def audit_chess_html(
    pdf_path: str | Path,
    html_path: str | Path,
    *,
    output: str | Path = "dist/conversion_audit.json",
) -> dict[str, Any]:
    pdf = Path(pdf_path)
    html_file = Path(html_path)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_text = html_file.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")

    pdf_page_count = _pdf_page_count(pdf)
    page_nodes = soup.select(".chess-book-page, .pdf-page, section[data-page]")
    html_pages = _html_page_numbers(page_nodes)
    diagram_nodes = soup.select(".book-diagram img, img.chess-review-diagram, .chess-diagram-container img")
    fen_values = _fen_values(soup)
    pgn_blocks = _pgn_blocks(soup)
    copy_fen_buttons = _copy_buttons(soup, kind="fen")
    copy_pgn_buttons = _copy_buttons(soup, kind="pgn")
    localhost_links = _localhost_links(soup, html_text)
    empty_copy_buttons = _empty_copy_buttons(soup)
    overflow_items = _overflow_items(soup)

    missing_pages = [page for page in range(1, pdf_page_count + 1) if page not in set(html_pages)]
    duplicate_pages = sorted(page for page in set(html_pages) if html_pages.count(page) > 1)
    numbering_gaps = _numbering_gaps(html_pages)
    valid_fen = [fen for fen in fen_values if _is_valid_fen(fen)]
    invalid_fen = [fen for fen in fen_values if not _is_valid_fen(fen)]

    critical_errors: list[str] = []
    if pdf_page_count and len(set(html_pages)) != pdf_page_count:
        critical_errors.append("pdf_html_page_count_mismatch")
    if missing_pages:
        critical_errors.append("missing_pdf_pages_in_html")
    if duplicate_pages:
        critical_errors.append("duplicate_html_page_numbers")
    if localhost_links:
        critical_errors.append("localhost_links_present")
    if empty_copy_buttons:
        critical_errors.append("empty_or_inactive_copy_buttons")
    if overflow_items:
        critical_errors.append("layout_overflow_detected")

    report = {
        "status": "failed" if critical_errors else "passed",
        "critical_errors": critical_errors,
        "source_pdf": str(pdf),
        "source_html": str(html_file),
        "pdf_pages": pdf_page_count,
        "html_pages": len(set(html_pages)),
        "html_page_nodes": len(page_nodes),
        "missing_pages": missing_pages,
        "duplicate_pages": duplicate_pages,
        "numbering_gaps": numbering_gaps,
        "diagrams": {
            "detected": len(diagram_nodes),
            "with_fen": len(fen_values),
        },
        "fen": {
            "total": len(fen_values),
            "valid": len(valid_fen),
            "invalid": len(invalid_fen),
            "invalid_samples": invalid_fen[:10],
        },
        "pgn": {
            "blocks": len(pgn_blocks),
            "accepted": len(soup.select(".book-pgn-record.accepted, .chess-pgn-legal")),
            "needs_review": len(soup.select(".book-pgn-record.review, .chess-pgn-needs-review, .book-review-warning")),
            "invalid": len([block for block in pgn_blocks if not block["text"].strip()]),
        },
        "copy_buttons": {
            "fen": len(copy_fen_buttons),
            "pgn": len(copy_pgn_buttons),
            "empty_or_inactive": len(empty_copy_buttons),
            "empty_or_inactive_samples": empty_copy_buttons[:10],
        },
        "localhost_links": {
            "count": len(localhost_links),
            "samples": localhost_links[:20],
        },
        "layout": {
            "overflow_count": len(overflow_items),
            "overflow_samples": overflow_items[:20],
        },
    }
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _pdf_page_count(path: Path) -> int:
    with fitz.open(path) as document:
        return len(document)


def _html_page_numbers(page_nodes: list[Any]) -> list[int]:
    numbers: list[int] = []
    for index, node in enumerate(page_nodes, start=1):
        raw = node.get("data-page") or node.get("data-page-number") or ""
        try:
            numbers.append(int(str(raw).strip()))
        except ValueError:
            numbers.append(index)
    return numbers


def _numbering_gaps(numbers: list[int]) -> list[dict[str, int]]:
    if not numbers:
        return []
    ordered = sorted(set(numbers))
    gaps: list[dict[str, int]] = []
    previous = ordered[0]
    for value in ordered[1:]:
        if value != previous + 1:
            gaps.append({"after": previous, "before": value})
        previous = value
    return gaps


def _fen_values(soup: BeautifulSoup) -> list[str]:
    values: list[str] = []
    selectors = [
        ".book-fen code",
        ".diagram-fen-code",
        "[data-fen]",
        "[data-fen-candidate]",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            value = node.get("data-fen") or node.get("data-fen-candidate") or node.get_text(" ", strip=True)
            value = str(value or "").replace("FEN:", "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _is_valid_fen(value: str) -> bool:
    valid, warnings = validate_fen(value)
    if not valid or warnings:
        return False
    try:
        import chess

        chess.Board(value)
    except Exception:
        return False
    return True


def _pgn_blocks(soup: BeautifulSoup) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for index, node in enumerate(soup.select(".chess-pgn-mainline, pre[data-pgn], [data-pgn]"), start=1):
        text = node.get("data-pgn") or node.get_text("\n", strip=True)
        blocks.append({"index": str(index), "text": str(text or "")})
    return blocks


def _copy_buttons(soup: BeautifulSoup, *, kind: str) -> list[Any]:
    value = kind.lower()
    return [
        node
        for node in soup.find_all(["button", "a"])
        if value in " ".join(node.get("class", [])).lower()
        or value in str(node.get("data-copy-kind") or "").lower()
        or value in node.get_text(" ", strip=True).lower()
    ]


def _localhost_links(soup: BeautifulSoup, html_text: str) -> list[str]:
    links: list[str] = []
    for node in soup.find_all(True):
        for attr in ("href", "src", "action"):
            value = str(node.get(attr) or "")
            if value and LOCALHOST_RE.search(value):
                links.append(value)
    if LOCALHOST_RE.search(html_text) and not links:
        links.append("__inline_localhost_reference__")
    return _unique(links)


def _empty_copy_buttons(soup: BeautifulSoup) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    nodes = [
        node
        for node in soup.find_all(["button", "a"])
        if "copy" in " ".join(node.get("class", [])).lower()
        or "copy" in node.get_text(" ", strip=True).lower()
        or node.get("data-copy-target")
    ]
    for index, node in enumerate(nodes, start=1):
        target_id = str(node.get("data-copy-target") or "").lstrip("#")
        direct_value = str(node.get("data-copy-value") or node.get("data-clipboard-text") or "")
        target_text = ""
        if target_id:
            target = soup.find(id=target_id)
            target_text = target.get_text(" ", strip=True) if target else ""
        if not direct_value.strip() and not target_text.strip():
            issues.append(
                {
                    "index": str(index),
                    "text": node.get_text(" ", strip=True),
                    "target": target_id,
                }
            )
    return issues


def _overflow_items(soup: BeautifulSoup) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for page in soup.select(".chess-book-page, .pdf-page"):
        page_number = page.get("data-page") or page.get("data-page-number") or ""
        page_box = _style_box(page.get("style") or "")
        page_width = page_box.get("width", 0.0)
        page_height = page_box.get("height", 0.0)
        if page_width <= 0 or page_height <= 0:
            continue
        for element in page.select(".book-element"):
            box = _style_box(element.get("style") or "")
            if not box:
                continue
            left = box.get("left", 0.0)
            top = box.get("top", 0.0)
            width = box.get("width", 0.0)
            height = box.get("height", 0.0)
            if left < -0.5 or top < -0.5 or left + width > page_width + 0.5 or top + height > page_height + 0.5:
                issues.append(
                    {
                        "page": str(page_number),
                        "class": " ".join(element.get("class", [])),
                        "box": {"left": left, "top": top, "width": width, "height": height},
                        "page_size": {"width": page_width, "height": page_height},
                    }
                )
    return issues


def _style_box(style: str) -> dict[str, float]:
    return {match.group("name").lower(): float(match.group("value")) for match in STYLE_PX_RE.finditer(style)}


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _print_summary(report: dict[str, Any], output: Path) -> None:
    print(f"Chess HTML audit: {report['status']}")
    print(f"  PDF pages: {report['pdf_pages']}")
    print(f"  HTML pages: {report['html_pages']} ({report['html_page_nodes']} nodes)")
    print(f"  Missing pages: {len(report['missing_pages'])}")
    print(f"  Diagrams: {report['diagrams']['detected']}")
    print(f"  FEN valid/invalid: {report['fen']['valid']}/{report['fen']['invalid']}")
    print(f"  PGN accepted/review/invalid: {report['pgn']['accepted']}/{report['pgn']['needs_review']}/{report['pgn']['invalid']}")
    print(f"  Copy FEN/PGN: {report['copy_buttons']['fen']}/{report['copy_buttons']['pgn']}")
    print(f"  Localhost links: {report['localhost_links']['count']}")
    print(f"  Overflow items: {report['layout']['overflow_count']}")
    if report["critical_errors"]:
        print("  Critical errors: " + ", ".join(report["critical_errors"]))
    print(f"  Report: {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a generated chess-book HTML artifact against its source PDF.")
    parser.add_argument("pdf", help="Source PDF path.")
    parser.add_argument("html", help="Generated chess HTML path.")
    parser.add_argument("--output", default="dist/conversion_audit.json", help="JSON report path.")
    args = parser.parse_args()

    output = Path(args.output)
    report = audit_chess_html(args.pdf, args.html, output=output)
    _print_summary(report, output)
    return 1 if report["critical_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
