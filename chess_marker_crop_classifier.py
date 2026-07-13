"""Evaluation helpers for the chess side-marker crop classifier."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from chess_marker_classifier_adaptive import (
    ADAPTIVE_CLASSIFIER_VERSION,
    calibrate_marker_confidence,
    fit_reliability_calibration,
    reliability_metrics,
)
from pymupdf_chess_extractor import classify_scan_chess_side_marker_crop

MARKER_CLASSIFIER_REPORT_SCHEMA = "kindlemaster.chess_fen.marker_crop_classifier_report.v2"
MARKER_CLASSIFIER_VERSION = ADAPTIVE_CLASSIFIER_VERSION
DEFAULT_MARKER_CORPUS_ROOT = Path("reference_inputs/chess_fen/marker_crops")
DEFAULT_MARKER_CLASSIFIER_REPORT = Path("reports/chess_fen/marker_crop_classifier_report.json")
MARKER_CLASS_EXPECTED_SIDE = {
    "white_outline_triangle": "w",
    "black_filled_triangle": "b",
}
MARKER_CLASS_EXPECTED_SYMBOL = {
    "white_outline_triangle": "△",
    "black_filled_triangle": "▼",
}
MARKER_CROP_CORPUS_CLASSES = (
    "white_outline_triangle",
    "black_filled_triangle",
    "bad_crop",
    "multiple",
    "unclear",
)


def evaluate_marker_crop_corpus(
    corpus_root: str | Path = DEFAULT_MARKER_CORPUS_ROOT,
    *,
    report_path: str | Path | None = DEFAULT_MARKER_CLASSIFIER_REPORT,
    min_triangle_accuracy: float = 0.90,
    source_profile: str = "yusupov-fundamentals",
) -> dict[str, Any]:
    """Evaluate the deterministic marker crop classifier against a corpus manifest."""

    root = Path(corpus_root)
    manifest = _load_manifest(root / "manifest.json")
    if manifest.get("schema") == "kindlemaster.chess.marker_acceptance_manifest.v1":
        return _evaluate_real_split_corpus(
            root,
            manifest=manifest,
            report_path=report_path,
            min_triangle_accuracy=min_triangle_accuracy,
            source_profile=source_profile,
        )
    items: list[dict[str, Any]] = []
    by_class: dict[str, dict[str, Any]] = {}

    for item_class in MARKER_CROP_CORPUS_CLASSES:
        expected_side = MARKER_CLASS_EXPECTED_SIDE.get(item_class, "")
        class_dir = root / item_class
        for image_path in sorted(class_dir.glob("*.png")):
            with Image.open(image_path) as image:
                classification = classify_scan_chess_side_marker_crop(
                    image,
                    source_profile="synthetic-baseline",
                )
            trusted = classification.get("status") == "trusted_marker" and classification.get("side") in {"w", "b"}
            predicted_side = str(classification.get("side") or "")
            if expected_side in {"w", "b"}:
                correct = trusted and predicted_side == expected_side
                false_trusted = False
            else:
                correct = not trusted
                false_trusted = bool(trusted)

            class_stats = by_class.setdefault(
                item_class,
                {
                    "total": 0,
                    "correct": 0,
                    "trusted": 0,
                    "false_trusted": 0,
                    "marker_classification_accuracy": 0.0,
                    "false_trusted_rate": 0.0,
                    "reasons": {},
                },
            )
            class_stats["total"] += 1
            class_stats["correct"] += 1 if correct else 0
            class_stats["trusted"] += 1 if trusted else 0
            class_stats["false_trusted"] += 1 if false_trusted else 0
            reason = str(classification.get("reason") or "")
            class_stats["reasons"][reason] = int(class_stats["reasons"].get(reason, 0)) + 1

            items.append(
                {
                    "id": image_path.stem,
                    "class": item_class,
                    "path": image_path.relative_to(root).as_posix(),
                    "expected_side": expected_side or "unknown",
                    "expected_symbol": MARKER_CLASS_EXPECTED_SYMBOL.get(item_class, ""),
                    "predicted_side": predicted_side or "unknown",
                    "predicted_symbol": classification.get("symbol") or "",
                    "status": classification.get("status") or "",
                    "trusted": trusted,
                    "confidence": classification.get("confidence") or 0.0,
                    "raw_confidence": classification.get("raw_confidence") or classification.get("confidence") or 0.0,
                    "calibration_status": classification.get("calibration_status") or "profile_default_conservative",
                    "classifier_version": classification.get("classifier_version") or MARKER_CLASSIFIER_VERSION,
                    "reason": reason,
                    "correct": correct,
                    "false_trusted": false_trusted,
                }
            )

    for class_stats in by_class.values():
        total = int(class_stats.get("total") or 0)
        class_stats["marker_classification_accuracy"] = round(class_stats["correct"] / total, 4) if total else 0.0
        class_stats["false_trusted_rate"] = round(class_stats["false_trusted"] / total, 4) if total else 0.0

    triangle_classes = ("white_outline_triangle", "black_filled_triangle")
    triangle_failures = [
        name
        for name in triangle_classes
        if float((by_class.get(name) or {}).get("marker_classification_accuracy") or 0.0) < min_triangle_accuracy
    ]
    negative_false_trusted = sum(
        int((by_class.get(name) or {}).get("false_trusted") or 0)
        for name in ("bad_crop", "multiple", "unclear")
    )
    decision = "pass" if not triangle_failures and negative_false_trusted == 0 else "fail"
    summary = {
        "classifier_version": MARKER_CLASSIFIER_VERSION,
        "corpus_root": str(root),
        "total": len(items),
        "marker_classification_accuracy": round(
            sum(1 for item in items if item.get("correct")) / len(items),
            4,
        )
        if items
        else 0.0,
        "white_outline_triangle_accuracy": float((by_class.get("white_outline_triangle") or {}).get("marker_classification_accuracy") or 0.0),
        "black_filled_triangle_accuracy": float((by_class.get("black_filled_triangle") or {}).get("marker_classification_accuracy") or 0.0),
        "negative_false_trusted_count": negative_false_trusted,
        "min_triangle_accuracy": min_triangle_accuracy,
        "triangle_accuracy_failures": triangle_failures,
        "decision": decision,
        "corpus_kind": "synthetic_baseline",
    }
    report = {
        "schema": MARKER_CLASSIFIER_REPORT_SCHEMA,
        "summary": summary,
        "by_class": by_class,
        "items": items,
        "confidence_reliability": {
            "synthetic_baseline": reliability_metrics(items),
            "real_holdout": {"status": "corpus_unavailable", "sample_count": 0},
        },
        "calibration": {
            "status": "not_applicable_to_synthetic_baseline",
            "source_split": "none",
            "holdout_used_for_tuning": False,
        },
        "real_fixed_edition_holdout": {
            "status": "corpus_unavailable",
            "reason": "secure_real_split_manifest_not_supplied",
        },
        "policy": {
            "allowed_for_training": True,
            "allowed_for_runtime_truth": False,
            "full_fen_gate_changed": False,
            "synthetic_baseline_reported_separately": True,
            "holdout_used_for_tuning": False,
        },
    }
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return report


def _evaluate_real_split_corpus(
    root: Path,
    *,
    manifest: dict[str, Any],
    report_path: str | Path | None,
    min_triangle_accuracy: float,
    source_profile: str,
) -> dict[str, Any]:
    from chess_yusupov_acceptance import (
        load_acceptance_profile,
        validate_acceptance_manifest,
    )

    validation = validate_acceptance_manifest(manifest, source_profile=source_profile)
    acceptance_profile = load_acceptance_profile(
        source_profile,
        repo_root=Path(__file__).parent,
    )
    profile_thresholds = acceptance_profile.get("thresholds") or {}
    required_clear_marker_accuracy = max(
        float(min_triangle_accuracy),
        float(
            profile_thresholds.get(
                "minimum_clear_marker_classification_accuracy",
                min_triangle_accuracy,
            )
        ),
    )
    rows: list[dict[str, Any]] = []
    missing_crops: list[str] = []
    for label in manifest.get("diagrams") or []:
        if not isinstance(label, dict):
            continue
        crop_path = str(
            label.get("marker_crop_path")
            or label.get("side_marker_crop_path")
            or label.get("micro_crop_path")
            or ""
        ).strip()
        if not crop_path:
            if label.get("marker_status") == "present":
                missing_crops.append(str(label.get("diagram_fingerprint") or ""))
            continue
        row = _evaluate_real_crop(
            root,
            crop_path=crop_path,
            label=label,
            source_profile=source_profile,
            item_id=str(label.get("diagram_fingerprint") or ""),
            item_kind="diagram",
        )
        if row is None:
            missing_crops.append(crop_path)
        else:
            rows.append(row)
    for label in manifest.get("hard_negatives") or []:
        if not isinstance(label, dict):
            continue
        crop_path = str(label.get("crop_path") or label.get("micro_crop_path") or "").strip()
        if not crop_path:
            missing_crops.append(str(label.get("hard_negative_fingerprint") or ""))
            continue
        row = _evaluate_real_crop(
            root,
            crop_path=crop_path,
            label=label,
            source_profile=source_profile,
            item_id=str(label.get("hard_negative_fingerprint") or ""),
            item_kind="hard_negative",
        )
        if row is None:
            missing_crops.append(crop_path)
        else:
            rows.append(row)

    calibration = fit_reliability_calibration(rows)
    for row in rows:
        calibrated, status = calibrate_marker_confidence(
            float(row.get("raw_confidence") or 0.0),
            calibration,
        )
        row["confidence"] = round(calibrated, 4)
        row["calibration_status"] = status

    by_split = {
        split: _real_split_summary([row for row in rows if row.get("split") == split])
        for split in ("train", "calibration", "holdout")
    }
    holdout = by_split["holdout"]
    holdout_pass = bool(
        holdout.get("clear_marker_count")
        and float(holdout.get("clear_marker_classification_accuracy") or 0.0)
        >= required_clear_marker_accuracy
        and int(holdout.get("false_trusted_marker_count") or 0) == 0
    )
    decision = (
        "pass"
        if validation.get("status") == "valid"
        and not missing_crops
        and calibration.get("status") == "fitted"
        and holdout_pass
        else "fail"
    )
    summary = {
        "classifier_version": MARKER_CLASSIFIER_VERSION,
        "corpus_root": str(root),
        "corpus_kind": "real_fixed_edition",
        "total": len(rows),
        "marker_classification_accuracy": _rate(
            len([row for row in rows if row.get("correct")]),
            len(rows),
        ),
        "white_outline_triangle_accuracy": _side_accuracy(rows, "w"),
        "black_filled_triangle_accuracy": _side_accuracy(rows, "b"),
        "negative_false_trusted_count": len(
            [row for row in rows if row.get("false_trusted")]
        ),
        "min_triangle_accuracy": min_triangle_accuracy,
        "required_clear_marker_accuracy": required_clear_marker_accuracy,
        "required_clear_marker_accuracy_source": "fixed_edition_acceptance_profile",
        "triangle_accuracy_failures": (
            [] if holdout_pass else ["real_holdout_clear_markers"]
        ),
        "decision": decision,
        "real_holdout_clear_marker_accuracy": holdout.get(
            "clear_marker_classification_accuracy", 0.0
        ),
        "real_holdout_false_trusted_count": holdout.get(
            "false_trusted_marker_count", 0
        ),
    }
    report = {
        "schema": "kindlemaster.chess_fen.marker_crop_classifier_report.v2",
        "summary": summary,
        "manifest_validation": validation,
        "missing_crops": missing_crops,
        "by_split": by_split,
        "items": rows,
        "calibration": calibration,
        "confidence_reliability": {
            split: reliability_metrics([row for row in rows if row.get("split") == split])
            for split in ("train", "calibration", "holdout")
        },
        "real_fixed_edition_holdout": {
            "status": "evaluated" if holdout.get("total") else "corpus_unavailable",
            **holdout,
        },
        "optional_model_ensemble": {
            "status": "not_configured",
            "training_split_only": True,
            "holdout_training_forbidden": True,
        },
        "policy": {
            "allowed_for_training": True,
            "allowed_for_runtime_truth": False,
            "full_fen_gate_changed": False,
            "synthetic_baseline_reported_separately": True,
            "calibration_source_split": "calibration",
            "holdout_used_for_tuning": False,
            "holdout_evaluated_once_per_report": True,
        },
    }
    _write_report(report, report_path)
    return report


def _evaluate_real_crop(
    root: Path,
    *,
    crop_path: str,
    label: dict[str, Any],
    source_profile: str,
    item_id: str,
    item_kind: str,
) -> dict[str, Any] | None:
    path = root / crop_path
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    with Image.open(resolved) as image:
        classification = classify_scan_chess_side_marker_crop(
            image,
            source_profile=source_profile,
        )
    trusted = classification.get("status") == "trusted_marker"
    expected_side = str(label.get("expected_side") or "unknown")
    expected_trusted = bool(
        item_kind == "diagram"
        and label.get("marker_status") == "present"
        and label.get("crop_quality") == "clear"
        and label.get("marker_ownership") == "assigned"
        and expected_side in {"w", "b"}
    )
    correct = (
        trusted and classification.get("side") == expected_side
        if expected_trusted
        else not trusted
    )
    return {
        "id": item_id,
        "kind": item_kind,
        "path": crop_path,
        "split": str(label.get("split") or ""),
        "page": label.get("page"),
        "chapter_id": label.get("chapter_id"),
        "expected_side": expected_side,
        "expected_trusted": expected_trusted,
        "predicted_side": classification.get("side") or "unknown",
        "predicted_symbol": classification.get("symbol") or "",
        "status": classification.get("status") or "",
        "trusted": trusted,
        "raw_confidence": classification.get("raw_confidence") or classification.get("confidence") or 0.0,
        "confidence": classification.get("confidence") or 0.0,
        "classifier_version": classification.get("classifier_version") or MARKER_CLASSIFIER_VERSION,
        "reason": classification.get("reason") or "",
        "correct": correct,
        "false_trusted": bool(trusted and not correct),
        "holdout_used_for_tuning": False,
    }


def _real_split_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clear = [row for row in rows if row.get("expected_trusted")]
    return {
        "total": len(rows),
        "correct_count": len([row for row in rows if row.get("correct")]),
        "accuracy": _rate(len([row for row in rows if row.get("correct")]), len(rows)),
        "clear_marker_count": len(clear),
        "clear_marker_classification_accuracy": _rate(
            len([row for row in clear if row.get("correct")]),
            len(clear),
        ),
        "false_trusted_marker_count": len(
            [row for row in rows if row.get("false_trusted")]
        ),
    }


def _side_accuracy(rows: list[dict[str, Any]], side: str) -> float:
    relevant = [row for row in rows if row.get("expected_trusted") and row.get("expected_side") == side]
    return _rate(len([row for row in relevant if row.get("correct")]), len(relevant))


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _write_report(report: dict[str, Any], report_path: str | Path | None) -> None:
    if not report_path:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def marker_crop_classifier_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Marker Crop Classifier Report",
        "",
        f"- classifier version: `{summary.get('classifier_version')}`",
        f"- decision: `{summary.get('decision')}`",
        f"- total: {summary.get('total', 0)}",
        f"- overall marker_classification_accuracy: {summary.get('marker_classification_accuracy', 0.0)}",
        f"- white_outline_triangle accuracy: {summary.get('white_outline_triangle_accuracy', 0.0)}",
        f"- black_filled_triangle accuracy: {summary.get('black_filled_triangle_accuracy', 0.0)}",
        f"- negative false trusted count: {summary.get('negative_false_trusted_count', 0)}",
        f"- corpus kind: `{summary.get('corpus_kind')}`",
        f"- real holdout status: `{(report.get('real_fixed_edition_holdout') or {}).get('status')}`",
        f"- calibration status: `{(report.get('calibration') or {}).get('status')}`",
        f"- holdout used for tuning: `{(report.get('policy') or {}).get('holdout_used_for_tuning')}`",
        "",
        "## By Class",
        "",
        "| Class | Total | Correct | Trusted | Accuracy | False trusted | False trusted rate | Reasons |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for class_name, stats in (report.get("by_class") or {}).items():
        reasons = ", ".join(f"{key}:{value}" for key, value in sorted((stats.get("reasons") or {}).items()))
        lines.append(
            "| {name} | {total} | {correct} | {trusted} | {accuracy} | {false_trusted} | {false_rate} | {reasons} |".format(
                name=class_name,
                total=stats.get("total", 0),
                correct=stats.get("correct", 0),
                trusted=stats.get("trusted", 0),
                accuracy=stats.get("marker_classification_accuracy", 0.0),
                false_trusted=stats.get("false_trusted", 0),
                false_rate=stats.get("false_trusted_rate", 0.0),
                reasons=reasons,
            )
        )
    return "\n".join(lines).rstrip() + "\n"
