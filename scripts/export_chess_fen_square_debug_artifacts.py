from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import _normalize_board_square  # noqa: E402


SCHEMA = "kindlemaster.chess_fen.square_debug_artifacts.v1"


def export_square_debug_artifacts(
    crop_path: str | Path,
    squares: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    case_id: str = "",
    top_n: int = 3,
) -> dict[str, Any]:
    crop = Path(crop_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    square_dir = target / "squares"
    square_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    if not crop.exists():
        payload = {
            "schema": SCHEMA,
            "status": "failed",
            "case_id": case_id,
            "crop_path": str(crop),
            "grid_overlay_path": "",
            "grid_overlay_href": "",
            "square_count": 0,
            "squares_dir": str(square_dir),
            "squares_jsonl": str(target / "squares.jsonl"),
            "issues": [{"code": "crop_path_missing_on_disk", "crop_path": str(crop)}],
            "squares": rows,
        }
        _write_manifest(target, payload)
        return payload

    board = _normalize_board_square(Image.open(crop).convert("RGB")).resize((800, 800), Image.Resampling.BILINEAR)
    overlay_path = target / "grid_overlay.png"
    _write_grid_overlay(board, overlay_path)
    square_by_name = {str(square.get("square") or ""): dict(square) for square in squares if isinstance(square, dict)}
    for row in range(8):
        for col in range(8):
            square_name = f"{chr(ord('a') + col)}{8 - row}"
            square_crop = board.crop((col * 100, row * 100, (col + 1) * 100, (row + 1) * 100))
            filename = f"{square_name}.png"
            square_path = square_dir / filename
            square_crop.save(square_path)
            source_square = square_by_name.get(square_name, {})
            alternatives = list(source_square.get("alternatives") or [])[: max(0, int(top_n or 0))]
            rows.append(
                {
                    "case_id": case_id,
                    "square": square_name,
                    "piece": source_square.get("piece", ""),
                    "confidence": _safe_float(source_square.get("confidence")),
                    "alternatives": alternatives,
                    "warnings": list(source_square.get("warnings") or []),
                    "square_crop_path": str(square_path),
                    "square_crop_href": str(Path("squares") / filename).replace("\\", "/"),
                }
            )
    jsonl_path = target / "squares.jsonl"
    jsonl_path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    payload = {
        "schema": SCHEMA,
        "status": "ok",
        "case_id": case_id,
        "crop_path": str(crop),
        "grid_overlay_path": str(overlay_path),
        "grid_overlay_href": overlay_path.name,
        "square_count": len(rows),
        "squares_dir": str(square_dir),
        "squares_jsonl": str(jsonl_path),
        "issues": [],
        "squares": rows,
    }
    _write_manifest(target, payload)
    return payload


def _write_manifest(target: Path, payload: dict[str, Any]) -> None:
    (target / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_float(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _write_grid_overlay(board: Image.Image, output_path: Path) -> None:
    overlay = board.convert("RGB")
    draw = ImageDraw.Draw(overlay)
    width, height = overlay.size
    line_color = (255, 64, 64)
    for index in range(9):
        x = round(index * width / 8)
        y = round(index * height / 8)
        draw.line((x, 0, x, height), fill=line_color, width=2)
        draw.line((0, y, width, y), fill=line_color, width=2)
    overlay.save(output_path)


def _load_squares(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        value = payload.get("squares") or payload.get("items") or []
        return [dict(item) for item in value if isinstance(item, dict)]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export 64 square crops and per-square FEN debug metadata.")
    parser.add_argument("--crop", required=True)
    parser.add_argument("--squares-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--case-id", default="")
    parser.add_argument("--top-n", type=int, default=3)
    args = parser.parse_args(argv)
    payload = export_square_debug_artifacts(
        args.crop,
        _load_squares(args.squares_json),
        args.output_dir,
        case_id=args.case_id,
        top_n=args.top_n,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
