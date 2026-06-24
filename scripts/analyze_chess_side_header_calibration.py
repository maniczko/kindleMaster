from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def analyze_side_header_calibration(
    candidates_jsonl: str | Path,
    report_json: str | Path,
    *,
    output_json: str | Path,
    output_md: str | Path | None = None,
    min_support: int = 5,
    min_purity: float = 0.9,
) -> dict[str, Any]:
    rows = _read_jsonl(Path(candidates_jsonl))
    report = json.loads(Path(report_json).read_text(encoding="utf-8"))
    records = list((((report.get("quality_report") or {}).get("chess_fen") or {}).get("records") or []))
    by_key = {(int(record.get("page") or 0), str(record.get("filename") or "")): record for record in records}

    symbol_support: dict[str, Counter[str]] = defaultdict(Counter)
    symbol_explicit_support: dict[str, Counter[str]] = defaultdict(Counter)
    symbol_status: dict[str, Counter[str]] = defaultdict(Counter)
    symbol_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    untrusted_symbol_rows = 0
    symbol_row_count = 0

    for row in rows:
        symbols = [str(candidate.get("symbol") or "") for candidate in row.get("header_symbol_candidates") or []]
        symbols = [symbol for symbol in symbols if symbol]
        if not symbols:
            continue
        symbol_row_count += 1
        record = by_key.get((int(row.get("page") or 0), str(row.get("filename") or "")), {})
        trusted_side = _trusted_side_to_move(record)
        explicit_side = _explicit_evidence_side_to_move(record)
        status = _record_status(record)
        if not trusted_side:
            untrusted_symbol_rows += 1
        for symbol in symbols:
            symbol_status[symbol][status] += 1
            if trusted_side:
                symbol_support[symbol][trusted_side] += 1
            if explicit_side:
                symbol_explicit_support[symbol][explicit_side] += 1
            if len(symbol_examples[symbol]) < 12:
                symbol_examples[symbol].append(
                    {
                        "page": int(row.get("page") or 0),
                        "filename": str(row.get("filename") or ""),
                        "status": status,
                        "trusted_side": trusted_side,
                        "ocr_text": _first_ocr_text(row),
                    }
                )

    symbol_calibration = []
    for symbol in sorted(set(symbol_status) | set(symbol_support)):
        support = dict(symbol_support.get(symbol, Counter()))
        explicit_support = dict(symbol_explicit_support.get(symbol, Counter()))
        trusted_total = sum(support.values())
        explicit_total = sum(explicit_support.values())
        best_side, best_count = _best_side(support)
        _, explicit_best_count = _best_side(explicit_support)
        purity = (best_count / trusted_total) if trusted_total else 0.0
        explicit_purity = (explicit_best_count / explicit_total) if explicit_total else 0.0
        suggested_mapping = best_side if trusted_total >= min_support and purity >= min_purity else ""
        symbol_calibration.append(
            {
                "symbol": symbol,
                "trusted_support": support,
                "trusted_total": trusted_total,
                "explicit_evidence_support": explicit_support,
                "explicit_evidence_total": explicit_total,
                "best_side": best_side,
                "purity": round(purity, 4),
                "explicit_evidence_purity": round(explicit_purity, 4),
                "status_counts": dict(symbol_status.get(symbol, Counter())),
                "suggested_mapping": suggested_mapping,
                "recommendation": "candidate_trusted_mapping" if suggested_mapping else "evidence_only_needs_more_calibration",
                "examples": symbol_examples.get(symbol, []),
            }
        )

    summary = {
        "status": "ok",
        "mode": "side_header_symbol_calibration_audit",
        "candidates_jsonl": str(candidates_jsonl),
        "report_json": str(report_json),
        "record_count": len(rows),
        "symbol_row_count": symbol_row_count,
        "untrusted_symbol_rows": untrusted_symbol_rows,
        "min_support": min_support,
        "min_purity": min_purity,
        "symbol_calibration": symbol_calibration,
        "trusted_mapping_count": sum(1 for item in symbol_calibration if item.get("suggested_mapping")),
        "policy": "audit_only_no_runtime_mapping",
    }
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_md:
        Path(output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(output_md).write_text(_markdown_summary(summary), encoding="utf-8")
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _trusted_side_to_move(record: dict[str, Any]) -> str:
    if bool(record.get("requires_review")):
        return ""
    return _explicit_evidence_side_to_move(record)


def _explicit_evidence_side_to_move(record: dict[str, Any]) -> str:
    if str(record.get("side_to_move_status") or "") != "explicit":
        return ""
    if str(record.get("side_to_move_evidence") or "") not in {"marker", "caption", "exact_label"}:
        return ""
    side = str(record.get("side_to_move") or "").strip()
    if side in {"w", "b"}:
        return side
    fen = str(record.get("fen") or record.get("full_fen") or "").strip()
    parts = fen.split()
    if len(parts) >= 2 and parts[1] in {"w", "b"}:
        return parts[1]
    return ""


def _record_status(record: dict[str, Any]) -> str:
    warnings = {str(warning) for warning in (record.get("warnings") or [])}
    if not record:
        return "missing_record"
    if bool(record.get("requires_review")):
        if "side_to_move_marker_multi_region_conflict" in warnings:
            return "review_conflict"
        if "side_to_move_inferred" in warnings:
            return "review_inferred"
        return "review_other"
    if "side_to_move_marker_multi_region_conflict" in warnings:
        return "accepted_with_conflict_warning"
    return "accepted"


def _best_side(support: dict[str, int]) -> tuple[str, int]:
    if not support:
        return "", 0
    side, count = max(support.items(), key=lambda item: (item[1], item[0]))
    return side, int(count)


def _first_ocr_text(row: dict[str, Any]) -> str:
    for item in row.get("ocr_line_items") or []:
        text = str(item.get("text") or "").strip()
        if text:
            return text
    return ""


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Side Header Symbol Calibration",
        "",
        f"- Policy: `{summary.get('policy')}`",
        f"- Rows with symbols: `{summary.get('symbol_row_count')}`",
        f"- Trusted mapping count: `{summary.get('trusted_mapping_count')}`",
        f"- Minimum support/purity: `{summary.get('min_support')}` / `{summary.get('min_purity')}`",
        "",
        "| Symbol | Trusted support | Explicit evidence support | Purity | Recommendation |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for item in summary.get("symbol_calibration") or []:
        support = json.dumps(item.get("trusted_support") or {}, ensure_ascii=False, sort_keys=True)
        explicit_support = json.dumps(item.get("explicit_evidence_support") or {}, ensure_ascii=False, sort_keys=True)
        lines.append(
            f"| `{item.get('symbol')}` | `{support}` | `{explicit_support}` | `{item.get('purity')}` | `{item.get('recommendation')}` |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "No runtime side-to-move mapping should be added unless a symbol reaches the configured support and purity threshold.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze scanned-chess header symbols against trusted side-to-move evidence.")
    parser.add_argument("candidates_jsonl")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", default="")
    parser.add_argument("--min-support", type=int, default=5)
    parser.add_argument("--min-purity", type=float, default=0.9)
    args = parser.parse_args(argv)
    summary = analyze_side_header_calibration(
        args.candidates_jsonl,
        args.report_json,
        output_json=args.output_json,
        output_md=args.output_md or None,
        min_support=args.min_support,
        min_purity=args.min_purity,
    )
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
