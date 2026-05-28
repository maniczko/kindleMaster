from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from chess_position_recognizer import normalize_board_crop_for_templates


PIECE_TEMPLATE_NAMES = {
    "K": "K-white",
    "Q": "Q-white",
    "R": "R-white",
    "B": "B-white",
    "N": "N-white",
    "P": "P-white",
    "k": "k-black",
    "q": "q-black",
    "r": "r-black",
    "b": "b-black",
    "n": "n-black",
    "p": "p-black",
}

GENERATED_TEMPLATE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEMPLATE_MANIFEST_NAME = "template_manifest.json"


def build_templates_from_labels(labels_path: str | Path, *, output_dir: str | Path, clean_output: bool = True) -> dict[str, Any]:
    """Build deterministic piece-cell templates from labeled board crops."""
    label_records = _read_jsonl(Path(labels_path))
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    removed_stale_files = _clean_generated_templates(target) if clean_output else 0

    counts: dict[str, int] = {}
    boards_processed = 0
    template_count = 0

    for record in label_records:
        fen = str(record.get("fen") or "").strip()
        crop_path = Path(str(record.get("crop_path") or ""))
        if not fen or not crop_path.exists():
            continue
        board = _placement_to_board(fen.split()[0])
        crop = ImageOps.autocontrast(Image.open(crop_path).convert("L"))
        board_image = normalize_board_crop_for_templates(crop)
        cell_size = board_image.width / 8.0

        for row in range(8):
            for col in range(8):
                piece = board[row][col]
                label = PIECE_TEMPLATE_NAMES.get(piece)
                if label is None:
                    label = f"empty-{'light' if (row + col) % 2 == 0 else 'dark'}"
                counts[label] = counts.get(label, 0) + 1
                cell = board_image.crop(
                    (
                        int(round(col * cell_size)),
                        int(round(row * cell_size)),
                        int(round((col + 1) * cell_size)),
                        int(round((row + 1) * cell_size)),
                    )
                ).resize((64, 64), Image.Resampling.LANCZOS)
                cell.save(target / f"{label}-{counts[label]:03d}.png", format="PNG", optimize=True)
                template_count += 1
        boards_processed += 1

    summary = {
        "status": "ok",
        "labels_path": str(labels_path),
        "output_dir": str(target),
        "boards_processed": boards_processed,
        "template_count": template_count,
        "label_counts": counts,
        "clean_output": clean_output,
        "removed_stale_files": removed_stale_files,
    }
    (target / TEMPLATE_MANIFEST_NAME).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _clean_generated_templates(target: Path) -> int:
    """Remove old generated template files before rebuilding.

    Stale templates are dangerous here: the recognizer loads every image in the
    directory, so one leftover piece template can publish a plausible but wrong
    FEN. Keep the cleanup scoped to generated image files and the manifest.
    """
    removed = 0
    for path in target.iterdir():
        if not path.is_file():
            continue
        if path.name == TEMPLATE_MANIFEST_NAME or path.suffix.lower() in GENERATED_TEMPLATE_SUFFIXES:
            path.unlink()
            removed += 1
    return removed


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _placement_to_board(placement: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for rank in placement.split("/"):
        row: list[str] = []
        for char in rank:
            if char.isdigit():
                row.extend([""] * int(char))
            else:
                row.append(char)
        if len(row) != 8:
            raise ValueError(f"Invalid FEN rank width: {rank}")
        rows.append(row)
    if len(rows) != 8:
        raise ValueError("FEN placement must have 8 ranks")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Build chess piece templates from labeled board crops.")
    parser.add_argument("labels")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--no-clean", action="store_true", help="Append to the output directory instead of removing old generated templates.")
    args = parser.parse_args()
    print(
        json.dumps(
            build_templates_from_labels(args.labels, output_dir=args.output_dir, clean_output=not args.no_clean),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
