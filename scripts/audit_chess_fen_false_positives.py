from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import (  # noqa: E402
    KNOWN_BAD_EXPECTED_FENS,
    infer_verification_source,
    is_ai_only_verification_source,
    is_human_verification_source,
    square_level_fen_diff,
)


def audit_chess_fen_false_positives(
    paths: Iterable[str | Path],
    *,
    output_path: str | Path | None = None,
    high_confidence_threshold: float = 0.90,
) -> dict[str, Any]:
    rows = []
    for path_value in paths:
        path = Path(path_value)
        if not path.exists():
            continue
        rows.extend(_read_records(path))

    findings: list[dict[str, Any]] = []
    for source_path, row in rows:
        findings.extend(_audit_row(source_path, row, high_confidence_threshold=high_confidence_threshold))

    summary = {
        "schema": "kindlemaster.chess_fen_false_positive_audit.v1",
        "status": "passed" if not findings else "failed",
        "input_count": len(rows),
        "finding_count": len(findings),
        "high_confidence_threshold": float(high_confidence_threshold),
        "findings": findings,
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _read_records(path: Path) -> list[tuple[str, dict[str, Any]]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if path.suffix.lower() == ".jsonl":
        records = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append((str(path), row))
        return records
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return [(str(path), row) for row in _walk_dict_records(parsed)]


def _walk_dict_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        rows = [value]
        for child in value.values():
            rows.extend(_walk_dict_records(child))
        return rows
    if isinstance(value, list):
        rows: list[dict[str, Any]] = []
        for child in value:
            rows.extend(_walk_dict_records(child))
        return rows
    return []


def _audit_row(source_path: str, row: dict[str, Any], *, high_confidence_threshold: float) -> list[dict[str, Any]]:
    row_id = str(row.get("id") or row.get("diagram_id") or "")
    if not row_id:
        return []
    findings: list[dict[str, Any]] = []
    source = infer_verification_source(row)
    confidence = _confidence(row)
    arbiter_approved = _truthy(row.get("arbiter_approved"))
    ai_approved = _truthy(row.get("ai_approved")) or _truthy(row.get("approved"))
    human_source = is_human_verification_source(source)
    ai_only_source = is_ai_only_verification_source(source)
    label_status = str(row.get("label_status") or row.get("status") or "").strip().lower()

    if (arbiter_approved or ai_approved or confidence >= high_confidence_threshold) and not human_source:
        findings.append(
            _finding(
                source_path,
                row_id,
                "high_confidence_or_ai_approved_without_human_verification",
                confidence=confidence,
                verification_source=source,
                arbiter_approved=arbiter_approved,
                ai_approved=ai_approved,
            )
        )
    if label_status == "verified" and ai_only_source:
        findings.append(_finding(source_path, row_id, "verified_label_has_ai_only_source", verification_source=source))
    if row_id in KNOWN_BAD_EXPECTED_FENS:
        actual = str(row.get("fen") or row.get("manual_fen") or row.get("ai_suggested_fen") or row.get("fen_candidate") or "").strip()
        if actual:
            diffs = _safe_square_diff(KNOWN_BAD_EXPECTED_FENS[row_id], actual)
            e5 = [diff for diff in diffs if diff["square"] == "e5"]
            if e5:
                findings.append(
                    _finding(
                        source_path,
                        row_id,
                        "known_bad_square_mismatch",
                        expected_fen=KNOWN_BAD_EXPECTED_FENS[row_id],
                        actual_fen=actual,
                        square_diffs=e5,
                    )
                )
    return findings


def _safe_square_diff(expected_fen: str, actual_fen: str) -> list[dict[str, str]]:
    try:
        return square_level_fen_diff(expected_fen, actual_fen)
    except ValueError as exc:
        return [{"square": "", "expected_piece": "", "actual_piece": "", "reason": f"invalid_fen:{exc}"}]


def _confidence(row: dict[str, Any]) -> float:
    for key in ("confidence", "ai_confidence", "fen_confidence", "arbiter_confidence"):
        try:
            return float(row.get(key) or 0.0)
        except Exception:
            continue
    return 0.0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "approved"}


def _finding(source_path: str, row_id: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"source_path": source_path, "id": row_id, "code": code, **extra}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit chess FEN artifacts for unsafe AI/high-confidence false positives.")
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--output", default="")
    parser.add_argument("--high-confidence-threshold", type=float, default=0.90)
    args = parser.parse_args()
    result = audit_chess_fen_false_positives(
        args.paths,
        output_path=args.output or None,
        high_confidence_threshold=args.high_confidence_threshold,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
