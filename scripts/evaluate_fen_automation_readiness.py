from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


SCHEMA = "kindlemaster.fen_automation_readiness.v1"
STRICT_FULL_FEN_STATUSES = {
    "FEN_MACHINE_ACCEPTED",
    "FEN_MACHINE_REPAIRED",
    "FEN_CORPUS_VERIFIED",
}
PLACEMENT_MACHINE_STATUS = "FEN_PLACEMENT_MACHINE_ACCEPTED"
AI_ONLY_STATUSES = {
    "ai_consensus",
    "ai_tie_break_resolved",
    "ai_unreadable",
    "ai_best_effort",
    "AI_CONSENSUS",
    "AI_TIE_BREAK_RESOLVED",
    "AI_UNREADABLE",
    "AI_BEST_EFFORT",
}


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
    ai_payload = _read_json(input_paths["fen_ensemble_eval"])
    items = list(fen_payload.get("items") or [])
    total = len(items)
    full_machine = len([item for item in items if _is_strict_full_fen(item)])
    placement_machine = len([item for item in items if item.get("placement_runtime_status") == PLACEMENT_MACHINE_STATUS])
    failed = len([item for item in items if str(item.get("status") or "").startswith("FEN_FAILED")])
    full_review = max(0, total - full_machine)
    ai_readout = _ai_readout_metrics(
        items,
        ai_payload,
        required_path=input_paths["fen_ensemble_eval"],
        denominator=total,
    )
    strict_full_fen = {
        "accepted_count": full_machine,
        "accepted_rate": _ratio(full_machine, total),
        "review_count": full_review,
        "review_rate": _ratio(full_review, total),
    }
    placement = {
        "machine_accepted_count": placement_machine,
        "machine_accepted_rate": _ratio(placement_machine, total),
    }
    release_safe = {
        "canonical_accepted_count": full_machine,
        "canonical_accepted_rate": _ratio(full_machine, total),
    }
    blocker_codes = _top_counts((blocker_payload.get("summary") or {}).get("by_code") or _count_item_blockers(items, field="code"))
    blocker_categories = _top_counts((blocker_payload.get("summary") or {}).get("by_category") or _count_item_blockers(items, field="category"))
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
        "strict_full_fen": strict_full_fen,
        "placement": placement,
        "ai_readout": ai_readout,
        "release_safe": release_safe,
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


def _is_strict_full_fen(item: dict[str, Any]) -> bool:
    status_blob = " ".join(str(item.get(key) or "") for key in ("status", "runtime_status", "source", "method"))
    lowered = status_blob.lower()
    if PLACEMENT_MACHINE_STATUS.lower() in lowered:
        return False
    if any(ai_status.lower() in lowered for ai_status in AI_ONLY_STATUSES):
        return False
    return str(item.get("runtime_status") or item.get("status") or "") in STRICT_FULL_FEN_STATUSES


def _ai_readout_metrics(
    items: list[dict[str, Any]],
    ai_payload: dict[str, Any],
    *,
    required_path: Path,
    denominator: int,
) -> dict[str, Any]:
    summary = ai_payload.get("summary") if isinstance(ai_payload.get("summary"), dict) else {}
    ai_counts = _ai_counts_from_items(items)
    if not ai_payload and not any(ai_counts.values()):
        return {"status": "MISSING_ARTIFACT", "required_path": str(required_path)}
    coverage = _int_from(summary, "coverage_count", default=sum(ai_counts.values()))
    strict_existing = _int_from(summary, "strict_existing", default=_int_from(summary, "strict_accepted", default=0))
    ai_consensus = _int_from(summary, "ai_consensus", default=ai_counts["ai_consensus"])
    ai_tie_break = _int_from(summary, "ai_tie_break_resolved", default=ai_counts["ai_tie_break_resolved"])
    ai_unreadable = _int_from(summary, "ai_unreadable", default=ai_counts["ai_unreadable"])
    ai_best_effort = _int_from(summary, "ai_best_effort", default=ai_counts["ai_best_effort"])
    if coverage <= 0:
        coverage = ai_consensus + ai_tie_break + ai_unreadable + ai_best_effort
    return {
        "coverage_count": coverage,
        "coverage_rate": _ratio(coverage, denominator),
        "strict_existing": strict_existing,
        "ai_consensus": ai_consensus,
        "ai_tie_break_resolved": ai_tie_break,
        "ai_unreadable": ai_unreadable,
        "ai_best_effort": ai_best_effort,
        "release_safe_accepted_count": 0,
    }


def _ai_counts_from_items(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "ai_consensus": 0,
        "ai_tie_break_resolved": 0,
        "ai_unreadable": 0,
        "ai_best_effort": 0,
    }
    for item in items:
        status_blob = " ".join(str(item.get(key) or "").lower() for key in ("status", "runtime_status", "source", "method"))
        for key in counts:
            if key in status_blob:
                counts[key] += 1
                break
    return counts


def _int_from(payload: dict[str, Any], key: str, *, default: int) -> int:
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def _count_item_blockers(items: list[dict[str, Any]], *, field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        for blocker in item.get("acceptance_blockers") or item.get("validation_errors") or []:
            value = str(blocker.get(field) or "unknown")
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
