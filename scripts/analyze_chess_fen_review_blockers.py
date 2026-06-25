from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_blockers import categorize_blocker, classify_blocker_category, recommendation_for_category, sorted_category_counts


STRICT_ACCEPTED_STATUSES = {
    "accepted",
    "fen_machine_accepted",
    "machine_accepted",
    "verified_exact_crop_label_used",
}
STRICT_EXCLUDED_STATUSES = {
    "fen_placement_machine_accepted",
    "placement_machine_accepted",
    "fen_placement_valid",
    "ai_consensus",
    "ai_tie_break_resolved",
    "ai_best_effort",
    "ai_autoread",
}


def analyze_review_blockers(report_path: str | Path) -> dict[str, Any]:
    report = _load_json(Path(report_path))
    items: list[dict[str, Any]] = []
    by_category: Counter[str] = Counter()
    by_blocker_code: Counter[str] = Counter()
    by_page: Counter[str] = Counter()
    with_ai_candidate_count = 0
    with_placement_count = 0
    without_any_candidate_count = 0

    for index, record in enumerate(_extract_records(report)):
        if _is_strict_accepted(record):
            continue
        blockers = _blockers(record)
        missing_blocker_data = False
        if not blockers:
            blockers = ["missing_blocker_data"]
            missing_blocker_data = True
        blocker_items = [
            categorize_blocker(blocker, context=[*blockers, *_warnings(record), _status_blob(record)])
            for blocker in blockers
        ]
        primary_blocker = str(blocker_items[0].get("code") or blockers[0])
        primary_category = str(blocker_items[0].get("category") or "unknown")
        has_ai_candidate = _has_ai_candidate(record)
        has_placement = bool(_selected_placement(record))
        has_fen_candidate = bool(_candidate_fen(record))
        candidate_count = _candidate_count(record)
        page = _first_non_empty(record.get("page"), record.get("page_number"))
        item = {
            "diagram_id": _diagram_id(record, index),
            "page": page,
            "status": _status(record),
            "runtime_status": _runtime_status(record),
            "candidate_count": candidate_count,
            "has_fen_candidate": has_fen_candidate,
            "has_placement": has_placement,
            "has_ai_candidate": has_ai_candidate,
            "selected_value": _selected_value(record),
            "selected_placement": _selected_placement(record),
            "primary_blocker": primary_blocker,
            "primary_category": primary_category,
            "all_blockers": blockers,
            "blockers": blocker_items,
            "warnings": _warnings(record),
            "confidence": record.get("confidence"),
            "recommendation": _recommendation(primary_category),
        }
        if missing_blocker_data:
            item["missing_blocker_data"] = True
        items.append(item)
        by_category[primary_category] += 1
        by_page[str(page)] += 1
        for blocker in blockers:
            by_blocker_code[blocker] += 1
        if has_ai_candidate:
            with_ai_candidate_count += 1
        if has_placement:
            with_placement_count += 1
        if not has_fen_candidate and not has_placement and not has_ai_candidate:
            without_any_candidate_count += 1

    return {
        "schema": "kindlemaster.chess_fen.review_blockers.v1",
        "report_path": str(report_path),
        "summary": {
            "review_total": len(items),
            "by_category": sorted_category_counts(by_category),
            "by_blocker_code": dict(by_blocker_code.most_common()),
            "by_page": dict(sorted(by_page.items(), key=lambda item: _page_sort_key(item[0]))),
            "with_ai_candidate_count": with_ai_candidate_count,
            "with_placement_count": with_placement_count,
            "without_any_candidate_count": without_any_candidate_count,
        },
        "items": items,
    }


def write_markdown(payload: Mapping[str, Any], output_path: str | Path) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), Mapping) else {}
    lines = [
        "# Chess FEN Review Blocker Diagnostics",
        "",
        f"Source report: `{payload.get('report_path', '')}`",
        "",
        "## Summary",
        "",
        f"- Review total: `{summary.get('review_total', 0)}`",
        f"- With placement: `{summary.get('with_placement_count', 0)}`",
        f"- With AI candidate: `{summary.get('with_ai_candidate_count', 0)}`",
        f"- Without any candidate: `{summary.get('without_any_candidate_count', 0)}`",
        "",
        "## Top 20 Categories",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    for category, count in _top_items(summary.get("by_category"), limit=20):
        lines.append(f"| `{_md(category)}` | {count} |")
    lines.extend(["", "## Top 20 Blocker Codes", "", "| Blocker | Count |", "|---|---:|"])
    for blocker, count in _top_items(summary.get("by_blocker_code"), limit=20):
        lines.append(f"| `{_md(blocker)}` | {count} |")
    lines.extend(
        [
            "",
            "## First 50 Review Items",
            "",
            "| Diagram | Page | Category | Primary blocker | Has placement | Has FEN candidate | Recommendation |",
            "|---|---:|---|---|---:|---:|---|",
        ]
    )
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    for item in items[:50]:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            "| {diagram} | {page} | `{category}` | `{blocker}` | {placement} | {fen} | {recommendation} |".format(
                diagram=_md(str(item.get("diagram_id", ""))),
                page=_md(str(item.get("page", ""))),
                category=_md(str(item.get("primary_category", ""))),
                blocker=_md(str(item.get("primary_blocker", ""))),
                placement="yes" if item.get("has_placement") else "no",
                fen="yes" if item.get("has_fen_candidate") else "no",
                recommendation=_md(str(item.get("recommendation", ""))),
            )
        )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze non-strict chess FEN review blockers.")
    parser.add_argument("report_json")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)
    payload = analyze_review_blockers(args.report_json)
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_markdown(payload, args.output_md)
    print(json.dumps({"status": "ok", **payload["summary"]}, ensure_ascii=False, indent=2))
    return 0


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_records(report: Any) -> list[Mapping[str, Any]]:
    if not isinstance(report, Mapping):
        return []
    roots: list[Any] = [report]
    quality_report = report.get("quality_report")
    if isinstance(quality_report, Mapping):
        roots.append(quality_report)
        chess_fen = quality_report.get("chess_fen")
        if isinstance(chess_fen, Mapping):
            roots.append(chess_fen)
    chess_fen = report.get("chess_fen")
    if isinstance(chess_fen, Mapping):
        roots.append(chess_fen)
    for root in roots:
        if not isinstance(root, Mapping):
            continue
        for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
            value = root.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        summary = root.get("summary")
        if isinstance(summary, Mapping):
            for key in ("records", "items", "cases", "diagrams", "accepted_candidates", "fen_candidates"):
                value = summary.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, Mapping)]
    return []


def _is_strict_accepted(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    status_blob = _status_blob(record)
    if any(excluded in status_blob for excluded in STRICT_EXCLUDED_STATUSES):
        return False
    if "ai_" in status_blob or "ai-autoread" in status_blob:
        return False
    if record.get("requires_review") is True:
        return False
    if not _looks_like_full_fen(_selected_value(record)):
        return False
    if record.get("requires_review") is False:
        return True
    return any(status in status_blob for status in STRICT_ACCEPTED_STATUSES)


def _primary_category(primary_blocker: str, blockers: Iterable[str], record: Mapping[str, Any]) -> str:
    return classify_blocker_category(primary_blocker, context=[*blockers, *_warnings(record), _status_blob(record)])


def _recommendation(category: str) -> str:
    return recommendation_for_category(category)


def _diagram_id(record: Mapping[str, Any], index: int) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    page = _first_non_empty(record.get("page"), record.get("page_number"))
    filename = record.get("filename")
    if page not in (None, "") and filename:
        return f"p{page}:{filename}"
    return f"record:{index}"


def _status(record: Mapping[str, Any]) -> str:
    if record.get("requires_review") is False and _looks_like_full_fen(_selected_value(record)):
        return "strict_accepted"
    if record.get("requires_review") is True:
        return "requires_review"
    return str(record.get("status") or record.get("label_status") or record.get("method") or "unknown")


def _runtime_status(record: Mapping[str, Any]) -> str:
    return str(record.get("runtime_status") or record.get("workflow_state") or record.get("method") or _status(record))


def _candidate_count(record: Mapping[str, Any]) -> int:
    total = 0
    for key in ("candidates", "fen_candidates", "external_fen_candidates", "ai_candidates"):
        value = record.get(key)
        if isinstance(value, list):
            total += len(value)
    if total:
        return total
    return int(bool(_candidate_fen(record))) + int(bool(_selected_placement(record))) + int(_has_ai_candidate(record))


def _has_ai_candidate(record: Mapping[str, Any]) -> bool:
    if any(str(key).startswith("ai_") and value not in (None, "", []) for key, value in record.items()):
        return True
    status_blob = _status_blob(record)
    return "ai_" in status_blob or "ai-autoread" in status_blob


def _candidate_fen(record: Mapping[str, Any]) -> str:
    return str(record.get("candidate_fen") or record.get("full_fen") or record.get("fen") or "").strip()


def _selected_value(record: Mapping[str, Any]) -> str:
    return str(
        record.get("selected_value")
        or record.get("fen")
        or record.get("full_fen")
        or record.get("candidate_fen")
        or ""
    ).strip()


def _selected_placement(record: Mapping[str, Any]) -> str:
    value = record.get("selected_placement") or record.get("placement") or record.get("placement_fen") or ""
    if _looks_like_full_fen(str(value)):
        return str(value).split()[0]
    return str(value or "").strip()


def _blockers(record: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("blockers", "blocking_warnings", "errors"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif isinstance(value, str) and value.strip():
            values.append(value.strip())
    if not values and record.get("requires_review") is True:
        values.extend(_warnings(record))
    return _dedupe(values)


def _warnings(record: Mapping[str, Any]) -> list[str]:
    value = record.get("warnings")
    if isinstance(value, list):
        return _dedupe(str(item) for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _status_blob(record: Mapping[str, Any]) -> str:
    return " ".join(
        str(value or "").strip().lower()
        for value in (
            record.get("status"),
            record.get("runtime_status"),
            record.get("method"),
            record.get("source"),
            record.get("label_source"),
        )
    )


def _looks_like_full_fen(value: str) -> bool:
    parts = str(value or "").strip().split()
    return len(parts) == 6 and parts[1] in {"w", "b"}


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _page_sort_key(value: str) -> tuple[int, str]:
    try:
        return (0, f"{int(value):08d}")
    except (TypeError, ValueError):
        return (1, value)


def _top_items(value: Any, *, limit: int) -> list[tuple[str, int]]:
    if not isinstance(value, Mapping):
        return []
    return sorted(((str(key), int(count)) for key, count in value.items()), key=lambda item: (-item[1], item[0]))[:limit]


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
