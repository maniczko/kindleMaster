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
    "ai_consensus",
    "ai_tie_break_resolved",
    "ai_best_effort",
    "ai_autoread",
}


def diff_strict_reports(previous_path: str | Path, latest_path: str | Path) -> dict[str, Any]:
    previous_report = _load_json(Path(previous_path))
    latest_report = _load_json(Path(latest_path))
    previous_items = _records_by_diagram_id(_extract_records(previous_report))
    latest_items = _records_by_diagram_id(_extract_records(latest_report))

    all_ids = sorted(set(previous_items) | set(latest_items), key=_diagram_sort_key)
    cases: list[dict[str, Any]] = []
    summary_counts: Counter[str] = Counter()
    lost_by_category: Counter[str] = Counter()
    lost_by_blocker_code: Counter[str] = Counter()

    previous_strict_count = sum(1 for item in previous_items.values() if _is_strict_accepted(item))
    latest_strict_count = sum(1 for item in latest_items.values() if _is_strict_accepted(item))

    for diagram_id in all_ids:
        previous = previous_items.get(diagram_id)
        latest = latest_items.get(diagram_id)
        previous_strict = _is_strict_accepted(previous)
        latest_strict = _is_strict_accepted(latest)
        classification = _classify_transition(previous, latest, previous_strict, latest_strict)
        primary_category = ""
        recommended_action = ""
        if classification == "lost_strict_accepted":
            primary_category = _primary_regression_category(latest)
            recommended_action = _recommended_action(primary_category)
            lost_by_category[primary_category] += 1
            for blocker in _blockers(latest):
                lost_by_blocker_code[blocker] += 1
        summary_counts[classification] += 1
        cases.append(
            {
                "diagram_id": diagram_id,
                "page": _first_non_empty(_value(previous, "page"), _value(latest, "page")),
                "classification": classification,
                "primary_regression_category": primary_category,
                "recommended_action": recommended_action,
                "previous_status": _status(previous),
                "latest_status": _status(latest),
                "previous_runtime_status": _runtime_status(previous),
                "latest_runtime_status": _runtime_status(latest),
                "previous_selected_value": _selected_value(previous),
                "latest_selected_value": _selected_value(latest),
                "previous_selected_placement": _selected_placement(previous),
                "latest_selected_placement": _selected_placement(latest),
                "previous_candidate_fen": _candidate_fen(previous),
                "latest_candidate_fen": _candidate_fen(latest),
                "previous_blockers": _blockers(previous),
                "latest_blockers": _blockers(latest),
                "latest_blocker_items": [
                    categorize_blocker(blocker, context=[*_blockers(latest), *_warnings(latest)])
                    for blocker in _blockers(latest)
                ],
                "previous_warnings": _warnings(previous),
                "latest_warnings": _warnings(latest),
            }
        )

    lost_cases = [case for case in cases if case["classification"] == "lost_strict_accepted"]
    return {
        "schema": "kindlemaster.chess_fen.strict_report_diff.v1",
        "previous_report": str(previous_path),
        "latest_report": str(latest_path),
        "summary": {
            "previous_strict_accepted_count": previous_strict_count,
            "latest_strict_accepted_count": latest_strict_count,
            "strict_delta": latest_strict_count - previous_strict_count,
            "lost_strict_count": summary_counts["lost_strict_accepted"],
            "new_strict_count": summary_counts["new_strict_accepted"],
            "net_change": summary_counts["new_strict_accepted"] - summary_counts["lost_strict_accepted"],
            "classification_counts": dict(sorted(summary_counts.items())),
            "lost_by_category": sorted_category_counts(lost_by_category),
            "lost_by_blocker_code": dict(lost_by_blocker_code.most_common()),
            "top_20_lost_cases": lost_cases[:20],
        },
        "cases": cases,
    }


def write_markdown(payload: Mapping[str, Any], output_path: str | Path) -> None:
    summary = payload.get("summary", {}) if isinstance(payload.get("summary"), Mapping) else {}
    lost_cases = [
        case
        for case in payload.get("cases", [])
        if isinstance(case, Mapping) and case.get("classification") == "lost_strict_accepted"
    ]
    lines = [
        "# Chess FEN Strict Regression Diff",
        "",
        f"Previous report: `{payload.get('previous_report', '')}`",
        f"Latest report: `{payload.get('latest_report', '')}`",
        "",
        "## Summary",
        "",
        f"- Previous strict accepted: `{summary.get('previous_strict_accepted_count', 0)}`",
        f"- Latest strict accepted: `{summary.get('latest_strict_accepted_count', 0)}`",
        f"- Strict delta: `{summary.get('strict_delta', 0)}`",
        f"- Lost strict accepted: `{summary.get('lost_strict_count', 0)}`",
        f"- New strict accepted: `{summary.get('new_strict_count', 0)}`",
        "",
        "## Lost Strict Accepted",
        "",
        "| Diagram | Page | Previous FEN | Latest status | Category | Blockers | Recommended action |",
        "|---|---:|---|---|---|---|---|",
    ]
    for case in lost_cases:
        lines.append(
            "| {diagram_id} | {page} | `{fen}` | `{status}` | `{category}` | {blockers} | {action} |".format(
                diagram_id=_md(str(case.get("diagram_id", ""))),
                page=_md(str(case.get("page", ""))),
                fen=_md(str(case.get("previous_selected_value") or case.get("previous_candidate_fen") or "")),
                status=_md(str(case.get("latest_status") or case.get("latest_runtime_status") or "")),
                category=_md(str(case.get("primary_regression_category", ""))),
                blockers=_md(", ".join(str(item) for item in case.get("latest_blockers", []) or [])),
                action=_md(str(case.get("recommended_action", ""))),
            )
        )
    if not lost_cases:
        lines.append("| _none_ |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Lost By Category",
            "",
        ]
    )
    for category, count in (summary.get("lost_by_category") or {}).items():
        lines.append(f"- `{category}`: {count}")
    lines.extend(["", "## Lost By Blocker Code", ""])
    for blocker, count in (summary.get("lost_by_blocker_code") or {}).items():
        lines.append(f"- `{blocker}`: {count}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff two chess FEN strict runtime reports.")
    parser.add_argument("previous_report")
    parser.add_argument("latest_report")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    args = parser.parse_args(argv)
    try:
        payload = diff_strict_reports(args.previous_report, args.latest_report)
    except FileNotFoundError as error:
        missing_path = error.filename or (error.args[0] if error.args else "")
        print(
            json.dumps({"status": "failed", "error": "missing_input_report", "path": str(missing_path)}, indent=2),
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError as error:
        print(json.dumps({"status": "failed", "error": "invalid_json", "message": str(error)}, indent=2), file=sys.stderr)
        return 2

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


def _records_by_diagram_id(records: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    rows: dict[str, Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        rows[_diagram_id(record, index)] = record
    return rows


def _diagram_id(record: Mapping[str, Any], index: int) -> str:
    for key in ("diagram_id", "id", "case_id", "record_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    page = record.get("page", record.get("page_number", ""))
    filename = str(record.get("filename") or record.get("crop_filename") or record.get("image") or "").strip()
    diagram_index = record.get("diagram_index", record.get("index", ""))
    if filename:
        return f"p{page}:{filename}"
    if page != "":
        return f"p{page}:d{diagram_index or index}"
    return f"record:{index}"


def _diagram_sort_key(diagram_id: str) -> tuple[int, str]:
    if diagram_id.startswith("p"):
        page_part = diagram_id[1:].split(":", 1)[0]
        try:
            return (int(page_part), diagram_id)
        except ValueError:
            pass
    return (10**9, diagram_id)


def _is_strict_accepted(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    status_blob = " ".join(
        str(value or "").strip().lower()
        for value in (
            record.get("status"),
            record.get("runtime_status"),
            record.get("method"),
            record.get("source"),
            record.get("label_source"),
        )
    )
    if any(excluded in status_blob for excluded in STRICT_EXCLUDED_STATUSES):
        return False
    if "ai_" in status_blob or "ai-autoread" in status_blob:
        return False
    selected = _selected_value(record)
    has_full_fen = _looks_like_full_fen(selected)
    if not has_full_fen:
        return False
    requires_review = record.get("requires_review")
    if requires_review is True:
        return False
    if requires_review is False:
        return True
    if any(status in status_blob for status in STRICT_ACCEPTED_STATUSES):
        return True
    return False


def _looks_like_full_fen(value: str) -> bool:
    parts = str(value or "").strip().split()
    return len(parts) == 6 and parts[1] in {"w", "b"}


def _classify_transition(
    previous: Mapping[str, Any] | None,
    latest: Mapping[str, Any] | None,
    previous_strict: bool,
    latest_strict: bool,
) -> str:
    if previous_strict and latest_strict:
        return "kept_strict_accepted"
    if previous_strict and not latest_strict:
        return "lost_strict_accepted"
    if not previous_strict and latest_strict:
        return "new_strict_accepted"
    if _is_review(previous) and _is_review(latest):
        return "still_review"
    if _is_failed(previous) and _is_failed(latest):
        return "still_failed"
    return "status_changed_other"


def _is_review(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    if record.get("requires_review") is True:
        return True
    status = _status(record).lower()
    return "review" in status


def _is_failed(record: Mapping[str, Any] | None) -> bool:
    if not isinstance(record, Mapping):
        return False
    status = _status(record).lower()
    return "failed" in status or "invalid" in status


def _primary_regression_category(record: Mapping[str, Any] | None) -> str:
    blockers = _blockers(record)
    if blockers:
        return classify_blocker_category(blockers[0], context=[*blockers, *_warnings(record)])
    return classify_blocker_category("unknown_blocker", context=_warnings(record))


def _recommended_action(category: str) -> str:
    return recommendation_for_category(category)


def _status(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return "missing"
    if record.get("requires_review") is False and _looks_like_full_fen(_selected_value(record)):
        return "strict_accepted"
    if record.get("requires_review") is True:
        return "requires_review"
    return str(record.get("status") or record.get("label_status") or record.get("method") or "unknown")


def _runtime_status(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return "missing"
    return str(record.get("runtime_status") or record.get("workflow_state") or record.get("method") or _status(record))


def _selected_value(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    return str(
        record.get("selected_value")
        or record.get("fen")
        or record.get("full_fen")
        or record.get("candidate_fen")
        or ""
    ).strip()


def _selected_placement(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    value = record.get("selected_placement") or record.get("placement") or record.get("placement_fen") or ""
    if _looks_like_full_fen(str(value)):
        return str(value).split()[0]
    return str(value or "").strip()


def _candidate_fen(record: Mapping[str, Any] | None) -> str:
    if not isinstance(record, Mapping):
        return ""
    return str(record.get("candidate_fen") or record.get("full_fen") or record.get("fen") or "").strip()


def _blockers(record: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(record, Mapping):
        return []
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


def _warnings(record: Mapping[str, Any] | None) -> list[str]:
    if not isinstance(record, Mapping):
        return []
    value = record.get("warnings")
    if isinstance(value, list):
        return _dedupe(str(item) for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _value(record: Mapping[str, Any] | None, key: str) -> Any:
    return record.get(key) if isinstance(record, Mapping) else None


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
