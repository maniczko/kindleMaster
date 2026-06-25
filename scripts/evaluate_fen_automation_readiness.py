from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_fen_blockers import categorize_blocker


SCHEMA = "kindlemaster.fen_automation_readiness.v1"


def evaluate_fen_automation_readiness(out_dir: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    root = Path(out_dir)
    input_paths = {
        "fen_candidates": root / "fen" / "fen_candidates.json",
        "acceptance_blockers": root / "report" / "acceptance_blockers.json",
        "quality_report": root / "report" / "quality_report.json",
        "chess_quality_dashboard": root / "reports" / "chess_quality_dashboard.json",
        "board_detection_quality": root / "reports" / "chess_fen" / "board_detection_quality.json",
        "fen_ensemble_eval": root / "reports" / "fen_ensemble_eval.json",
    }
    existing_inputs = {name: str(path) for name, path in input_paths.items() if path.exists()}
    missing_inputs = {name: str(path) for name, path in input_paths.items() if not path.exists()}
    fen_payload = _read_json(input_paths["fen_candidates"])
    blocker_payload = _read_json(input_paths["acceptance_blockers"])
    items = list(fen_payload.get("items") or [])
    total = len(items)
    full_machine = len([item for item in items if item.get("runtime_status") == "FEN_MACHINE_ACCEPTED"])
    placement_machine = len([item for item in items if item.get("placement_runtime_status") == "FEN_PLACEMENT_MACHINE_ACCEPTED"])
    failed = len([item for item in items if str(item.get("status") or "").startswith("FEN_FAILED")])
    full_review = max(0, total - full_machine)
    blocker_codes = _top_counts((blocker_payload.get("summary") or {}).get("by_code") or _count_item_blockers(items, field="code"))
    blocker_categories = _top_counts((blocker_payload.get("summary") or {}).get("by_category") or _count_item_blocker_categories(items))
    if not root.exists():
        status = "blocked"
    elif not input_paths["fen_candidates"].exists():
        status = "needs_benchmark"
    elif total == 0:
        status = "no_fen_candidates"
    elif placement_machine > 0:
        status = "ready_for_p0_review"
    else:
        status = "blocked"
    summary = {
        "total_fen_items": total,
        "full_machine_accepted_count": full_machine,
        "full_machine_accepted_rate": _ratio(full_machine, total),
        "placement_machine_accepted_count": placement_machine,
        "placement_machine_accepted_rate": _ratio(placement_machine, total),
        "full_review_required_count": full_review,
        "full_review_required_rate": _ratio(full_review, total),
        "failed_count": failed,
        "failed_rate": _ratio(failed, total),
        "top_blockers": blocker_codes,
        "top_blocker_categories": blocker_categories,
    }
    payload = {
        "schema": SCHEMA,
        "status": status,
        "summary": summary,
        "recommendation": _recommendation(blocker_categories, total=total, placement_machine=placement_machine),
        "thresholds": {
            "minimal_placement_acceptance_target": 0.50,
            "minimal_full_fen_target": 0.50,
        },
        "input_paths": {
            "out_dir": str(root),
            "existing": existing_inputs,
            "missing": missing_inputs,
        },
    }
    if output_path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "MISSING_ARTIFACT", "error": f"invalid_json:{exc}"}


def _count_item_blockers(items: list[dict[str, Any]], *, field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for blocker in item.get("acceptance_blockers") or item.get("validation_errors") or []:
            value = str(blocker.get(field) or "unknown")
            counts[value] = counts.get(value, 0) + 1
    return counts


def _count_item_blocker_categories(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for blocker in item.get("acceptance_blockers") or item.get("validation_errors") or []:
            categorized = categorize_blocker(blocker)
            value = str(categorized.get("category") or "unknown")
            counts[value] = counts.get(value, 0) + 1
    return counts


def _top_counts(values: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, count in values.items():
        try:
            numeric_count = int(count)
        except (TypeError, ValueError):
            numeric_count = 0
        rows.append({"code": str(key), "count": numeric_count})
    return sorted(rows, key=lambda row: (-int(row["count"]), row["code"]))[:10]


def _recommendation(categories: list[dict[str, Any]], *, total: int, placement_machine: int) -> str:
    if total <= 0:
        return "increase_ground_truth"
    if placement_machine <= 0:
        return "fix_placement_validation"
    if not categories:
        return "inspect_unknown"
    dominant = str(categories[0].get("code") or "unknown")
    if dominant == "crop_grid":
        return "fix_crop_grid"
    if dominant == "recognition":
        return "fix_recognition"
    if dominant == "placement":
        return "fix_placement_validation"
    if dominant in {"full_fen_validation", "metadata"}:
        return "fix_full_fen_metadata"
    if dominant == "unknown":
        return "inspect_unknown"
    return "inspect_unknown"


def _ratio(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(float(part) / float(total), 4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate FEN automation readiness for an auto chess output directory.")
    parser.add_argument("out_dir", nargs="?", help="Auto chess output directory.")
    parser.add_argument("--output", dest="output_path", default="", help="Optional JSON output path.")
    args = parser.parse_args(argv)
    if not args.out_dir:
        parser.print_help()
        return 0
    payload = evaluate_fen_automation_readiness(args.out_dir, output_path=args.output_path or None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
