from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import Any

import fitz
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import detect_board_candidates_in_page_image


def extract_chess_diagram_crops(
    pdf_path: str | Path,
    *,
    output_dir: str | Path = "reports/chess_fen/crops",
    pages: int = 48,
    dpi: int = 72,
    max_candidates_per_page: int = 4,
    min_grid_confidence: float = 0.50,
    enable_sliding_probe: bool = False,
) -> dict[str, Any]:
    """Extract board-like page crops for later piece-template labeling."""
    source = Path(pdf_path)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    crop_records: list[dict[str, Any]] = []
    with fitz.open(source) as document:
        page_limit = min(max(0, int(pages)), len(document))
        zoom = max(24, int(dpi)) / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in range(page_limit):
            page = document[page_index]
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            page_png = pixmap.tobytes("png")
            candidates = detect_board_candidates_in_page_image(
                page_png,
                max_candidates=max_candidates_per_page,
                min_grid_confidence=min_grid_confidence,
                enable_sliding_probe=enable_sliding_probe,
            )
            if not candidates:
                continue

            page_image = Image.open(io.BytesIO(page_png)).convert("L")
            for candidate_index, candidate in enumerate(candidates):
                if not candidate.bbox:
                    continue
                x0, y0, x1, y1 = (int(round(value)) for value in candidate.bbox)
                crop = page_image.crop((x0, y0, x1, y1))
                filename = f"{source.stem}_p{page_index + 1:03d}_c{candidate_index + 1}.png"
                crop_path = target / filename
                crop.save(crop_path, format="PNG", optimize=True)
                crop_records.append(
                    {
                        "source_pdf": str(source),
                        "page": page_index + 1,
                        "candidate": candidate_index + 1,
                        "bbox": [x0, y0, x1, y1],
                        "confidence": round(float(candidate.confidence or 0.0), 3),
                        "method": candidate.method,
                        "crop_path": str(crop_path),
                        "requires_labeling": True,
                    }
                )

    manifest = {
        "status": "ok",
        "source_pdf": str(source),
        "output_dir": str(target),
        "page_limit": int(pages),
        "dpi": int(dpi),
        "crop_count": len(crop_records),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "crops": crop_records,
    }
    manifest_path = target / f"{source.stem}_crop_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract candidate chess-board crops from scanned PDFs.")
    parser.add_argument("pdf", help="Source PDF path.")
    parser.add_argument("--output-dir", default="reports/chess_fen/crops")
    parser.add_argument("--pages", type=int, default=48)
    parser.add_argument("--dpi", type=int, default=72)
    parser.add_argument("--max-candidates-per-page", type=int, default=4)
    parser.add_argument("--min-grid-confidence", type=float, default=0.50)
    parser.add_argument("--sliding-probe", action="store_true")
    args = parser.parse_args()

    payload = extract_chess_diagram_crops(
        args.pdf,
        output_dir=args.output_dir,
        pages=args.pages,
        dpi=args.dpi,
        max_candidates_per_page=args.max_candidates_per_page,
        min_grid_confidence=args.min_grid_confidence,
        enable_sliding_probe=args.sliding_probe,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
