from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image

from chess_position_recognizer import (
    _estimate_board_grid_confidence,
    _has_board_visual_pattern,
    _normalize_board_square,
    load_piece_templates,
    recognize_chess_position_from_image,
)

DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE = 0.84
DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN = 0.90


def evaluate_chess_fen_recognizer(
    labels_path: str | Path,
    *,
    template_dir: str | Path,
    min_confidence: float = DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE,
    min_exact_accuracy: float = DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate deterministic FEN recognition against labeled board crops."""
    labels = [json.loads(line) for line in Path(labels_path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    templates = load_piece_templates(template_dir)
    cases: list[dict[str, Any]] = []
    exact_count = 0
    fen_count = 0
    false_positive_count = 0
    square_total = 0
    square_exact = 0
    per_piece: dict[str, dict[str, int]] = {}
    confusion: dict[str, dict[str, int]] = {}

    for record in labels:
        expected_fen = str(record.get("fen") or "").strip()
        expected_placement = expected_fen.split()[0] if expected_fen else ""
        crop_path = Path(str(record.get("crop_path") or ""))
        if not expected_fen or not crop_path.exists():
            cases.append(
                {
                    "id": record.get("id", ""),
                    "crop_path": str(crop_path),
                    "expected_fen": expected_fen,
                    "actual_fen": "",
                    "expected_placement": expected_placement,
                    "actual_placement": "",
                    "matched": False,
                    "square_accuracy": 0.0,
                    "requires_review": True,
                    "warnings": ["missing_label_or_crop"],
                }
            )
            continue
        crop_bytes = crop_path.read_bytes()
        result = recognize_chess_position_from_image(
            crop_bytes,
            piece_templates=templates,
            min_confidence=min_confidence,
        ).to_dict()
        diagnostics = _image_board_diagnostics(crop_bytes)
        actual_fen = str(result.get("fen") or "").strip()
        actual_placement = str(result.get("placement") or "").strip()
        matched = bool(actual_fen and actual_fen == expected_fen)
        false_positive = bool(actual_fen and actual_fen != expected_fen)
        exact_count += int(matched)
        fen_count += int(bool(actual_fen))
        false_positive_count += int(false_positive)
        case_square_total, case_square_exact = _score_placement(
            expected_placement,
            actual_placement,
            per_piece=per_piece,
            confusion=confusion,
        )
        square_total += case_square_total
        square_exact += case_square_exact
        cases.append(
            {
                "id": record.get("id", ""),
                "crop_path": str(crop_path),
                "expected_fen": expected_fen,
                "actual_fen": actual_fen,
                "expected_placement": expected_placement,
                "actual_placement": actual_placement,
                "matched": matched,
                "false_positive": false_positive,
                "square_accuracy": round(case_square_exact / max(1, case_square_total), 4),
                "confidence": result.get("confidence", 0.0),
                "warnings": result.get("warnings", []),
                "requires_review": result.get("requires_review", True),
                "recognition_diagnostics": {
                    **diagnostics,
                    "suppressed_reason": _suppressed_reason(result.get("warnings", []), actual_fen=actual_fen),
                    "exact_placement_without_fen": bool(not actual_fen and actual_placement == expected_placement),
                },
            }
        )

    exact_fen_accuracy = round(exact_count / max(1, len(labels)), 4)
    status_passed = bool(labels and exact_fen_accuracy >= min_exact_accuracy and false_positive_count == 0)
    summary = {
        "status": "passed" if status_passed else "failed",
        "case_count": len(labels),
        "min_confidence": float(min_confidence),
        "fen_count": fen_count,
        "exact_fen_count": exact_count,
        "exact_fen_accuracy": exact_fen_accuracy,
        "false_positive_count": false_positive_count,
        "false_positive_rate": round(false_positive_count / max(1, fen_count), 4),
        "min_exact_accuracy": float(min_exact_accuracy),
        "square_accuracy": round(square_exact / max(1, square_total), 4),
        "per_piece_accuracy": {
            piece: round(stats["correct"] / max(1, stats["total"]), 4)
            for piece, stats in sorted(per_piece.items())
        },
        "per_piece_counts": dict(sorted(per_piece.items())),
        "confusion": {piece: dict(sorted(values.items())) for piece, values in sorted(confusion.items())},
        "cases": cases,
    }
    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _image_board_diagnostics(image_data: bytes) -> dict[str, Any]:
    try:
        image = Image.open(io.BytesIO(image_data)).convert("L")
    except Exception:
        return {"board_detected": False, "grid_confidence": 0.0, "board_signal": 0.0}
    board = _normalize_board_square(image)
    detected, signal = _has_board_visual_pattern(board)
    return {
        "board_detected": bool(detected),
        "grid_confidence": round(float(_estimate_board_grid_confidence(board)), 4),
        "board_signal": round(float(signal), 4),
        "normalized_size": list(board.size),
    }


def _suppressed_reason(warnings: Any, *, actual_fen: str) -> str:
    if actual_fen:
        return ""
    warning_set = {str(warning) for warning in warnings or []}
    for warning in (
        "partial_board_crop_without_dense_board_evidence",
        "piece_template_confidence_below_threshold",
        "board_visual_pattern_not_detected",
        "board_grid_not_detected",
        "piece_template_set_incomplete",
        "sparse_position_confidence_below_threshold",
    ):
        if warning in warning_set:
            return warning
    king_warnings = sorted(warning for warning in warning_set if warning.endswith("king_count_invalid"))
    if king_warnings:
        return king_warnings[0]
    return "requires_review_or_unclassified"


def _score_placement(
    expected_placement: str,
    actual_placement: str,
    *,
    per_piece: dict[str, dict[str, int]],
    confusion: dict[str, dict[str, int]],
) -> tuple[int, int]:
    try:
        expected = _placement_to_cells(expected_placement)
        actual = _placement_to_cells(actual_placement)
    except ValueError:
        return 0, 0

    total = 0
    correct = 0
    for expected_piece, actual_piece in zip(expected, actual):
        expected_label = expected_piece or "empty"
        actual_label = actual_piece or "empty"
        stats = per_piece.setdefault(expected_label, {"total": 0, "correct": 0})
        stats["total"] += 1
        confusion.setdefault(expected_label, {})
        confusion[expected_label][actual_label] = confusion[expected_label].get(actual_label, 0) + 1
        total += 1
        if expected_label == actual_label:
            stats["correct"] += 1
            correct += 1
    return total, correct


def _placement_to_cells(placement: str) -> list[str]:
    rows = str(placement or "").split("/")
    if len(rows) != 8:
        raise ValueError("placement must have 8 ranks")
    cells: list[str] = []
    for rank in rows:
        width = 0
        for char in rank:
            if char.isdigit():
                value = int(char)
                cells.extend([""] * value)
                width += value
            else:
                cells.append(char)
                width += 1
        if width != 8:
            raise ValueError("rank width must be 8")
    if len(cells) != 64:
        raise ValueError("placement must contain 64 cells")
    return cells


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic chess FEN recognition.")
    parser.add_argument("labels")
    parser.add_argument("--template-dir", required=True)
    parser.add_argument("--min-confidence", type=float, default=DEFAULT_CHESS_FEN_EVAL_MIN_CONFIDENCE)
    parser.add_argument("--min-exact-accuracy", type=float, default=DEFAULT_CHESS_FEN_EXACT_ACCURACY_MIN)
    parser.add_argument("--output", default="reports/chess_fen/evals/latest.json")
    args = parser.parse_args()
    result = evaluate_chess_fen_recognizer(
        args.labels,
        template_dir=args.template_dir,
        min_confidence=args.min_confidence,
        min_exact_accuracy=args.min_exact_accuracy,
        output_path=args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
