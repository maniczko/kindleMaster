"""Evaluation helpers for the chess side-marker crop classifier."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from pymupdf_chess_extractor import classify_scan_chess_side_marker_crop

MARKER_CLASSIFIER_REPORT_SCHEMA = "kindlemaster.chess_fen.marker_crop_classifier_report.v1"
MARKER_CLASSIFIER_VERSION = "marker_shape_v2"
DEFAULT_MARKER_CORPUS_ROOT = Path("reference_inputs/chess_fen/marker_crops")
DEFAULT_MARKER_CLASSIFIER_REPORT = Path("reports/chess_fen/marker_crop_classifier_report.json")


def evaluate_marker_crop_corpus(
    corpus_root: str | Path = DEFAULT_MARKER_CORPUS_ROOT,
    *,
    report_path: str | Path | None = DEFAULT_MARKER_CLASSIFIER_REPORT,
    min_triangle_accuracy: float = 0.90,
) -> dict[str, Any]:
    """Evaluate the deterministic marker crop classifier against a corpus manifest."""

    root = Path(corpus_root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    by_class: dict[str, dict[str, Any]] = {}

    for source_item in manifest.get("items") or []:
        if not isinstance(source_item, dict):
            continue
        item_class = str(source_item.get("class") or "")
        expected_side = str(source_item.get("label") or "")
        image_path = root / str(source_item.get("path") or "")
        classification = classify_scan_chess_side_marker_crop(Image.open(image_path))
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
                "id": source_item.get("id") or "",
                "class": item_class,
                "path": source_item.get("path") or "",
                "expected_side": expected_side or "unknown",
                "expected_symbol": source_item.get("symbol") or "",
                "predicted_side": predicted_side or "unknown",
                "predicted_symbol": classification.get("symbol") or "",
                "status": classification.get("status") or "",
                "trusted": trusted,
                "confidence": classification.get("confidence") or 0.0,
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
    }
    report = {
        "schema": MARKER_CLASSIFIER_REPORT_SCHEMA,
        "summary": summary,
        "by_class": by_class,
        "items": items,
        "policy": {
            "allowed_for_training": True,
            "allowed_for_runtime_truth": False,
            "full_fen_gate_changed": False,
        },
    }
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # The marker-crop corpus stores public fixture labels for classifier QA, not credentials or user secrets.
        # codeql[py/clear-text-storage-sensitive-data]
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # The markdown mirrors the same public benchmark metrics and fixture classes for auditability.
        # codeql[py/clear-text-storage-sensitive-data]
        path.with_suffix(".md").write_text(marker_crop_classifier_markdown(report), encoding="utf-8")
    return report


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
