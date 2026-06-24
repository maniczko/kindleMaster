from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


SCHEMA = "kindlemaster.chess_fen.template_strategy.v1"


def evaluate_chess_fen_template_strategy(
    *,
    recognizer_eval_path: str | Path | None = None,
    readiness_path: str | Path | None = None,
    label_inventory_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    recognizer_eval = _read_json(recognizer_eval_path)
    readiness = _read_json(readiness_path)
    inventory = _read_json(label_inventory_path)
    signals = _signals(recognizer_eval, readiness, inventory)
    recommendation, blockers, next_actions = _recommend(signals)
    payload = {
        "schema": SCHEMA,
        "status": "ok",
        "recommendation": recommendation,
        "signals": signals,
        "blockers": blockers,
        "next_actions": next_actions,
        "input_paths": {
            "recognizer_eval": str(recognizer_eval_path or ""),
            "readiness": str(readiness_path or ""),
            "label_inventory": str(label_inventory_path or ""),
        },
        "policy": {
            "no_model_replacement_without_hardened_ground_truth": True,
            "no_ai_or_external_authority": True,
            "no_threshold_change_recommendation": True,
        },
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    source = Path(path)
    if not source.exists():
        return {"_missing": True, "path": str(source)}
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_invalid_json": True, "path": str(source), "error": str(exc)}


def _signals(recognizer_eval: dict[str, Any], readiness: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    inventory_summary = inventory.get("summary") or {}
    readiness_summary = readiness.get("summary") or {}
    return {
        "recognizer_eval_available": bool(recognizer_eval and not recognizer_eval.get("_missing")),
        "eval_case_count": int(recognizer_eval.get("case_count") or 0),
        "exact_fen_accuracy": _float(recognizer_eval.get("exact_fen_accuracy")),
        "square_accuracy": _float(recognizer_eval.get("square_accuracy")),
        "false_positive_count": int(recognizer_eval.get("false_positive_count") or 0),
        "fen_count": int(recognizer_eval.get("fen_count") or 0),
        "valid_human_verified_label_count": int(inventory_summary.get("total_valid_human_verified_label_count") or 0),
        "profiles_meeting_target": int(inventory_summary.get("profiles_meeting_target") or 0),
        "profiles_missing_target": int(inventory_summary.get("profiles_missing_target") or 0),
        "placement_machine_accepted_rate": _float(readiness_summary.get("placement_machine_accepted_rate")),
        "full_machine_accepted_rate": _float(readiness_summary.get("full_machine_accepted_rate")),
        "top_blocker_categories": list(readiness_summary.get("top_blocker_categories") or []),
    }


def _recommend(signals: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[str]]:
    blockers: list[dict[str, Any]] = []
    next_actions: list[str] = []
    valid_labels = int(signals.get("valid_human_verified_label_count") or 0)
    false_positives = int(signals.get("false_positive_count") or 0)
    exact_accuracy = float(signals.get("exact_fen_accuracy") or 0.0)
    square_accuracy = float(signals.get("square_accuracy") or 0.0)
    eval_case_count = int(signals.get("eval_case_count") or 0)

    if valid_labels < 100:
        blockers.append(
            {
                "code": "insufficient_hardened_ground_truth",
                "message": "Do not replace or retrain recognizer before hardened human-verified labels exist.",
                "valid_human_verified_label_count": valid_labels,
                "minimum_recommended": 100,
            }
        )
        next_actions.append("Migrate or reverify existing labels to the full human evidence contract.")
        next_actions.append("Collect false-positive/cropped-board audit samples before model strategy decisions.")
    if false_positives > 0:
        blockers.append(
            {
                "code": "false_positive_risk_present",
                "message": "Recognizer strategy cannot be loosened while false positives remain.",
                "false_positive_count": false_positives,
            }
        )
        next_actions.append("Fix or isolate false-positive cases before expanding automation.")
    if eval_case_count <= 0:
        blockers.append({"code": "recognizer_eval_missing", "message": "Recognizer evaluation evidence is missing."})
        next_actions.append("Run evaluate_chess_fen_recognizer on a verified profile.")

    if blockers:
        return "keep_template_matcher_collect_evidence", blockers, _dedupe(next_actions)
    if exact_accuracy >= 0.90 and false_positives == 0:
        next_actions.append("Keep template matcher as baseline and augment reporting/crop evidence first.")
        if square_accuracy >= 0.98:
            next_actions.append("Prioritize side-to-move/full-FEN metadata evidence over replacing placement recognizer.")
        return "keep_template_matcher_augment_evidence", blockers, _dedupe(next_actions)
    if square_accuracy < 0.90:
        next_actions.append("Run a bounded alternative recognizer spike against the same verified holdout, without changing runtime gates.")
        return "consider_alternative_recognizer_spike", blockers, _dedupe(next_actions)
    next_actions.append("Improve template profile coverage and crop/grid diagnostics before model replacement.")
    return "augment_template_profile_before_replacement", blockers, _dedupe(next_actions)


def _float(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    rows = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate whether chess FEN template matching should be kept, augmented, or spiked.")
    parser.add_argument("--recognizer-eval", default="")
    parser.add_argument("--readiness", default="")
    parser.add_argument("--label-inventory", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    payload = evaluate_chess_fen_template_strategy(
        recognizer_eval_path=args.recognizer_eval or None,
        readiness_path=args.readiness or None,
        label_inventory_path=args.label_inventory or None,
        output_path=args.output or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
