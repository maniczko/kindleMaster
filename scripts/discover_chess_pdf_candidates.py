from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from chess_position_recognizer import detect_board_candidates_in_page_image


PDF_SUFFIX = ".pdf"


def _sample_page_indexes(page_count: int, pages_per_pdf: int) -> list[int]:
    page_total = max(0, int(page_count))
    sample_count = max(0, int(pages_per_pdf))
    if page_total == 0 or sample_count == 0:
        return []
    if sample_count >= page_total:
        return list(range(page_total))
    if sample_count == 1:
        return [0]
    indexes = {0, page_total - 1}
    interior_slots = sample_count - len(indexes)
    for step in range(1, interior_slots + 1):
        indexes.add(round(step * (page_total - 1) / (interior_slots + 1)))
    return sorted(index for index in indexes if 0 <= index < page_total)[:sample_count]


def _iter_pdf_paths(roots: Iterable[str | Path], *, max_files: int) -> list[Path]:
    limit = max(0, int(max_files))
    seen: set[Path] = set()
    paths: list[Path] = []
    for root_value in roots:
        root = Path(root_value).expanduser()
        candidates = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
        for candidate in candidates:
            if candidate.suffix.lower() != PDF_SUFFIX or not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(resolved)
            if limit and len(paths) >= limit:
                return paths
    return paths


def _candidate_status(candidate_page_count: int, total_candidates: int, *, min_candidate_pages: int) -> str:
    if candidate_page_count >= max(1, int(min_candidate_pages)):
        return "candidate"
    if total_candidates > 0:
        return "weak_candidate"
    return "no_board_candidates"


def discover_chess_pdf_candidates(
    roots: Iterable[str | Path],
    *,
    max_files: int = 40,
    pages_per_pdf: int = 5,
    render_dpi: int = 120,
    min_grid_confidence: float = 0.58,
    max_candidates_per_page: int = 2,
    min_candidate_pages: int = 2,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    try:
        import fitz
    except Exception as exc:
        payload = {
            "status": "unavailable",
            "reason": f"pymupdf_unavailable:{exc.__class__.__name__}",
            "roots": [str(root) for root in roots],
            "candidates": [],
        }
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    started = time.perf_counter()
    pdf_paths = _iter_pdf_paths(roots, max_files=max_files)
    scale = max(0.5, float(render_dpi) / 72.0)
    results: list[dict[str, Any]] = []

    for pdf_path in pdf_paths:
        pdf_result: dict[str, Any] = {
            "path": str(pdf_path),
            "size_bytes": pdf_path.stat().st_size,
            "status": "not_scanned",
            "page_count": 0,
            "sampled_pages": [],
            "candidate_page_count": 0,
            "total_board_candidates": 0,
            "max_confidence": 0.0,
            "errors": [],
        }
        try:
            with fitz.open(pdf_path) as document:
                pdf_result["page_count"] = int(document.page_count)
                sample_indexes = _sample_page_indexes(document.page_count, pages_per_pdf)
                pdf_result["sampled_pages"] = [index + 1 for index in sample_indexes]
                for page_index in sample_indexes:
                    try:
                        page = document.load_page(page_index)
                        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        image_data = pixmap.tobytes("png")
                        page_candidates = detect_board_candidates_in_page_image(
                            image_data,
                            max_candidates=max_candidates_per_page,
                            min_grid_confidence=min_grid_confidence,
                        )
                    except Exception as exc:
                        pdf_result["errors"].append({"page": page_index + 1, "error": exc.__class__.__name__})
                        continue
                    if not page_candidates:
                        continue
                    pdf_result["candidate_page_count"] += 1
                    pdf_result["total_board_candidates"] += len(page_candidates)
                    pdf_result["max_confidence"] = max(
                        float(pdf_result["max_confidence"]),
                        max(float(candidate.confidence) for candidate in page_candidates),
                    )
        except Exception as exc:
            pdf_result["status"] = "unreadable"
            pdf_result["errors"].append({"error": exc.__class__.__name__})
            results.append(pdf_result)
            continue

        pdf_result["status"] = _candidate_status(
            int(pdf_result["candidate_page_count"]),
            int(pdf_result["total_board_candidates"]),
            min_candidate_pages=min_candidate_pages,
        )
        results.append(pdf_result)

    candidates = [item for item in results if item.get("status") in {"candidate", "weak_candidate"}]
    candidates.sort(
        key=lambda item: (
            0 if item.get("status") == "candidate" else 1,
            -int(item.get("candidate_page_count") or 0),
            -float(item.get("max_confidence") or 0.0),
            str(item.get("path") or ""),
        )
    )
    payload = {
        "status": "completed",
        "roots": [str(root) for root in roots],
        "pdf_count": len(results),
        "candidate_count": len(candidates),
        "elapsed_seconds": round(time.perf_counter() - started, 4),
        "settings": {
            "max_files": max_files,
            "pages_per_pdf": pages_per_pdf,
            "render_dpi": render_dpi,
            "min_grid_confidence": min_grid_confidence,
            "max_candidates_per_page": max_candidates_per_page,
            "min_candidate_pages": min_candidate_pages,
        },
        "candidates": candidates,
        "all_results": results,
    }
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover local PDF candidates that contain chess-board-like diagrams.")
    parser.add_argument("roots", nargs="+", help="PDF files or directories to scan.")
    parser.add_argument("--max-files", type=int, default=40)
    parser.add_argument("--pages-per-pdf", type=int, default=5)
    parser.add_argument("--render-dpi", type=int, default=120)
    parser.add_argument("--min-grid-confidence", type=float, default=0.58)
    parser.add_argument("--max-candidates-per-page", type=int, default=2)
    parser.add_argument("--min-candidate-pages", type=int, default=2)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    payload = discover_chess_pdf_candidates(
        args.roots,
        max_files=args.max_files,
        pages_per_pdf=args.pages_per_pdf,
        render_dpi=args.render_dpi,
        min_grid_confidence=args.min_grid_confidence,
        max_candidates_per_page=args.max_candidates_per_page,
        min_candidate_pages=args.min_candidate_pages,
        output_path=args.output or None,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
