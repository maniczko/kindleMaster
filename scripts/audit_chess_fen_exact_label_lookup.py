from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_hardening import exact_crop_label_release_safety, normalize_crop_sha256  # noqa: E402


SCHEMA = "kindlemaster.chess_fen.exact_label_lookup_audit.v1"
DEFAULT_LABELS_DIR = Path("reference_inputs/chess_fen/labels")
DEFAULT_CROPS_DIR = Path("reference_inputs/chess_fen/crops")
DEFAULT_STRICT_DIFF = Path("reports/chess_fen/strict_regression_diff_exact_label_vs_marker_rule.json")
DEFAULT_REPORT_JSON = Path("reports/chess_fen/exact_label_lookup_audit.json")
DEFAULT_REPORT_MD = Path("reports/chess_fen/exact_label_lookup_audit.md")


def audit_chess_fen_exact_label_lookup(
    *,
    labels_dir: str | Path = DEFAULT_LABELS_DIR,
    crops_dir: str | Path = DEFAULT_CROPS_DIR,
    strict_diff_path: str | Path | None = DEFAULT_STRICT_DIFF,
    output_json: str | Path | None = DEFAULT_REPORT_JSON,
    output_markdown: str | Path | None = DEFAULT_REPORT_MD,
) -> dict[str, Any]:
    labels_root = Path(labels_dir)
    crops_root = Path(crops_dir)
    current_crops = _current_crops(crops_root)
    label_rows = _label_rows(labels_root)
    lost_cases = _lost_exact_label_cases(Path(strict_diff_path)) if strict_diff_path else []

    items: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}
    release_safe_count = 0
    matching_hash_count = 0
    matching_identifier_count = 0
    for row in label_rows:
        digest = _row_digest(row)
        safety = exact_crop_label_release_safety(row)
        release_safe = bool(safety["release_safe"])
        if release_safe:
            release_safe_count += 1
        for issue in safety["issues"]:
            code = str(issue.get("code") or "unknown")
            issue_counts[code] = issue_counts.get(code, 0) + 1

        filename = str(row.get("filename") or Path(str(row.get("source_crop_path") or "")).name).strip()
        page = int(row.get("page") or 0)
        hash_match = bool(digest and digest in current_crops["by_hash"])
        identifier_match = bool(filename and (page, filename) in current_crops["by_page_filename"])
        if hash_match:
            matching_hash_count += 1
        if identifier_match:
            matching_identifier_count += 1

        items.append(
            {
                "id": str(row.get("id") or ""),
                "labels_path": str(row.get("_labels_path") or ""),
                "page": page,
                "filename": filename,
                "sha256": digest,
                "fen": str(row.get("fen") or ""),
                "release_safe": release_safe,
                "matching_current_crop_by_hash": hash_match,
                "matching_current_crop_by_identifier": identifier_match,
                "missing_or_blocking_codes": [str(issue.get("code") or "") for issue in safety["issues"]],
                "has_sha256": bool(normalize_crop_sha256(row.get("sha256"))),
                "has_crop_sha256": bool(normalize_crop_sha256(row.get("crop_sha256"))),
                "has_human_verified": row.get("human_verified") is True,
                "verification_source": safety.get("verification_source") or "",
                "label_status": str(row.get("label_status") or ""),
            }
        )

    recoverable_lost = _recoverable_lost_cases(lost_cases, items)
    payload = {
        "schema": SCHEMA,
        "labels_dir": str(labels_root),
        "crops_dir": str(crops_root),
        "strict_diff_path": str(strict_diff_path or ""),
        "summary": {
            "label_count": len(label_rows),
            "release_safe_label_count": release_safe_count,
            "labels_missing_hash_or_provenance": len([item for item in items if not item["release_safe"]]),
            "labels_matching_current_crops_by_hash": matching_hash_count,
            "labels_matching_current_crops_by_identifier": matching_identifier_count,
            "labels_not_matching_current_crops_by_hash": len(label_rows) - matching_hash_count,
            "lost_strict_exact_label_case_count": len(lost_cases),
            "exact_label_candidates_able_to_recover_strict_cases": len(recoverable_lost),
            "issue_counts": dict(sorted(issue_counts.items())),
        },
        "release_safe_items": [item for item in items if item["release_safe"]],
        "blocked_items": [item for item in items if not item["release_safe"]],
        "recoverable_lost_cases_linked_to_issue_23": recoverable_lost,
    }
    if output_json:
        output = Path(output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if output_markdown:
        output = Path(output_markdown)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(_markdown(payload), encoding="utf-8")
    return payload


def _label_rows(labels_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not labels_root.exists():
        return rows
    for path in sorted(labels_root.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append({**row, "_labels_path": str(path)})
    return rows


def _current_crops(crops_root: Path) -> dict[str, Any]:
    by_hash: dict[str, list[str]] = {}
    by_page_filename: dict[tuple[int, str], str] = {}
    if not crops_root.exists():
        return {"by_hash": by_hash, "by_page_filename": by_page_filename}
    for path in sorted(crops_root.rglob("*.png")):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        by_hash.setdefault(digest, []).append(str(path))
        page = _page_from_filename(path.name)
        if page > 0:
            by_page_filename[(page, path.name)] = str(path)
    return {"by_hash": by_hash, "by_page_filename": by_page_filename}


def _lost_exact_label_cases(path: Path) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") if isinstance(payload, dict) else []
    if not isinstance(cases, list):
        return []
    return [
        case
        for case in cases
        if isinstance(case, dict)
        and case.get("classification") == "lost_strict_accepted"
        and str(case.get("previous_runtime_status") or "").replace("_", "-") == "verified-exact-crop-label"
    ]


def _recoverable_lost_cases(lost_cases: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    release_safe_by_fen = {str(item.get("fen") or ""): item for item in items if item.get("release_safe")}
    results: list[dict[str, Any]] = []
    for case in lost_cases:
        fen = str(case.get("previous_selected_value") or case.get("previous_candidate_fen") or "")
        item = release_safe_by_fen.get(fen)
        if not item:
            continue
        results.append(
            {
                "issue": "#23",
                "diagram_id": case.get("diagram_id"),
                "page": case.get("page"),
                "previous_selected_value": fen,
                "label_id": item.get("id"),
                "label_sha256": item.get("sha256"),
            }
        )
    return results


def _row_digest(row: dict[str, Any]) -> str:
    for key in ("crop_sha256", "sha256", "source_crop_hash"):
        digest = normalize_crop_sha256(row.get(key))
        if digest:
            return digest
    return ""


def _page_from_filename(filename: str) -> int:
    match = __import__("re").search(r"p(\d{3})", filename)
    return int(match.group(1)) if match else 0


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Exact Label Lookup Audit",
        "",
        f"- label_count: {summary['label_count']}",
        f"- release_safe_label_count: {summary['release_safe_label_count']}",
        f"- labels_missing_hash_or_provenance: {summary['labels_missing_hash_or_provenance']}",
        f"- labels_matching_current_crops_by_hash: {summary['labels_matching_current_crops_by_hash']}",
        f"- labels_matching_current_crops_by_identifier: {summary['labels_matching_current_crops_by_identifier']}",
        f"- labels_not_matching_current_crops_by_hash: {summary['labels_not_matching_current_crops_by_hash']}",
        f"- exact_label_candidates_able_to_recover_strict_cases: {summary['exact_label_candidates_able_to_recover_strict_cases']}",
        "",
        "## Blocking Issue Counts",
        "",
    ]
    issue_counts = summary.get("issue_counts") or {}
    if issue_counts:
        lines.extend(f"- {code}: {count}" for code, count in issue_counts.items())
    else:
        lines.append("- none")
    lines.extend(["", "## #23 Links", ""])
    links = payload.get("recoverable_lost_cases_linked_to_issue_23") or []
    if links:
        lines.extend(f"- {item['diagram_id']}: {item['label_id']} ({item['label_sha256']})" for item in links)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit release-safe exact crop label lookup for chess FEN.")
    parser.add_argument("--labels-dir", default=str(DEFAULT_LABELS_DIR))
    parser.add_argument("--crops-dir", default=str(DEFAULT_CROPS_DIR))
    parser.add_argument("--strict-diff", default=str(DEFAULT_STRICT_DIFF))
    parser.add_argument("--output-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--output-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args(argv)
    payload = audit_chess_fen_exact_label_lookup(
        labels_dir=args.labels_dir,
        crops_dir=args.crops_dir,
        strict_diff_path=args.strict_diff,
        output_json=args.output_json,
        output_markdown=args.output_md,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
