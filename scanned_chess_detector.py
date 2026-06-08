from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Iterable

import fitz
import numpy as np
from PIL import Image, ImageOps


@dataclass(frozen=True)
class ScannedChessBoard:
    page: int
    bbox: tuple[float, float, float, float]
    confidence: float
    filename: str
    image_data: bytes
    width: int
    height: int
    source_type: str = "scanned_board"


def detect_scanned_chess_boards(
    pdf_path: str,
    *,
    pages: Iterable[int] | None = None,
    render_dpi: int = 90,
    crop_dpi: int = 190,
    max_boards_per_page: int = 4,
    confidence_threshold: float = 0.26,
) -> list[ScannedChessBoard]:
    """Detect and crop raster chess boards from scanned pages.

    The detector intentionally looks for the visual 8x8 grid, not for OCR text.
    It is conservative: low-confidence candidates are discarded rather than
    turning noisy scan regions into fake semantic diagrams.
    """
    doc = fitz.open(pdf_path)
    boards: list[ScannedChessBoard] = []
    try:
        selected_pages = list(pages) if pages is not None else list(range(len(doc)))
        for page_index in selected_pages:
            if page_index < 0 or page_index >= len(doc):
                continue
            page = doc[page_index]
            page_boards = _detect_boards_on_page(
                page,
                page_index=page_index,
                render_dpi=render_dpi,
                crop_dpi=crop_dpi,
                max_boards=max_boards_per_page,
                confidence_threshold=confidence_threshold,
            )
            boards.extend(page_boards)
    finally:
        doc.close()
    return boards


def _detect_boards_on_page(
    page: fitz.Page,
    *,
    page_index: int,
    render_dpi: int,
    crop_dpi: int,
    max_boards: int,
    confidence_threshold: float,
) -> list[ScannedChessBoard]:
    matrix = fitz.Matrix(render_dpi / 72.0, render_dpi / 72.0)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    prepared = ImageOps.autocontrast(image)
    arr = np.asarray(prepared)
    candidates = _find_grid_candidates(arr, confidence_threshold=confidence_threshold)
    candidates = _dedupe_candidates(candidates, max_count=max_boards)

    boards: list[ScannedChessBoard] = []
    scale = render_dpi / 72.0
    for board_index, candidate in enumerate(candidates, start=1):
        x0, y0, x1, y1, confidence = candidate
        padding = max(6, int((x1 - x0) * 0.18))
        clip = fitz.Rect(
            max(0, (x0 - padding) / scale),
            max(0, (y0 - padding) / scale),
            min(page.rect.width, (x1 + padding) / scale),
            min(page.rect.height, (y1 + padding) / scale),
        )
        crop_matrix = fitz.Matrix(crop_dpi / 72.0, crop_dpi / 72.0)
        crop_pix = page.get_pixmap(matrix=crop_matrix, clip=clip, alpha=False)
        crop_image = Image.open(io.BytesIO(crop_pix.tobytes("png"))).convert("RGB")
        output = io.BytesIO()
        crop_image.save(output, format="PNG", optimize=True)
        filename = f"scanned_board_p{page_index + 1}_{board_index}.png"
        boards.append(
            ScannedChessBoard(
                page=page_index,
                bbox=(clip.x0, clip.y0, clip.x1, clip.y1),
                confidence=round(float(confidence), 3),
                filename=filename,
                image_data=output.getvalue(),
                width=crop_image.width,
                height=crop_image.height,
            )
        )
    return boards


def _find_grid_candidates(arr: np.ndarray, *, confidence_threshold: float) -> list[tuple[int, int, int, int, float]]:
    height, width = arr.shape[:2]
    min_dim = min(width, height)
    dark = arr < 105
    integral = _integral_image(dark)
    candidates: list[tuple[int, int, int, int, float]] = []

    min_size = max(90, int(min_dim * 0.14))
    max_size = max(min_size + 1, int(min_dim * 0.72))
    size_step = max(28, int(min_dim * 0.06))

    for size in range(min_size, max_size + 1, size_step):
        stride = max(34, size // 3)
        for y0 in range(0, max(height - size, 1), stride):
            for x0 in range(0, max(width - size, 1), stride):
                score = _score_8x8_grid(dark, integral, x0, y0, size)
                if score >= confidence_threshold:
                    candidates.append((x0, y0, x0 + size, y0 + size, score))
    candidates.sort(key=lambda item: item[4], reverse=True)
    return candidates


def _integral_image(mask: np.ndarray) -> np.ndarray:
    return np.pad(mask.astype(np.float32), ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)


def _rect_mean(integral: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    x0 = max(0, min(x0, integral.shape[1] - 1))
    x1 = max(0, min(x1, integral.shape[1] - 1))
    y0 = max(0, min(y0, integral.shape[0] - 1))
    y1 = max(0, min(y1, integral.shape[0] - 1))
    if x1 <= x0 or y1 <= y0:
        return 0.0
    total = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
    return float(total / max((x1 - x0) * (y1 - y0), 1))


def _score_8x8_grid(dark: np.ndarray, integral: np.ndarray, x0: int, y0: int, size: int) -> float:
    crop = dark[y0 : y0 + size, x0 : x0 + size]
    if crop.size == 0:
        return 0.0
    band = max(1, size // 85)
    tolerance = max(2, size // 24)
    expected = [round(i * (size - 1) / 8) for i in range(9)]
    vertical_scores = []
    horizontal_scores = []
    for pos in expected:
        vertical_scores.append(_best_vertical_line_score(integral, x0, y0, size, pos, band=band, tolerance=tolerance))
        horizontal_scores.append(_best_horizontal_line_score(integral, x0, y0, size, pos, band=band, tolerance=tolerance))

    grid_score = (sum(vertical_scores) + sum(horizontal_scores)) / 18.0
    body_density = _rect_mean(integral, x0, y0, x0 + size, y0 + size)
    checker_score = _checkerboard_density_score(integral, x0, y0, size)
    border_bonus = min(vertical_scores[0], vertical_scores[-1], horizontal_scores[0], horizontal_scores[-1])
    density_penalty = max(0.0, body_density - 0.45) * 0.55
    sparse_penalty = max(0.0, 0.035 - body_density) * 2.5
    return max(
        0.0,
        min(1.0, grid_score * 0.52 + border_bonus * 0.16 + checker_score * 0.32 - density_penalty - sparse_penalty),
    )


def _checkerboard_density_score(integral: np.ndarray, x0: int, y0: int, size: int) -> float:
    cell = size / 8.0
    light_group = []
    dark_group = []
    for row in range(8):
        for col in range(8):
            cx0 = x0 + int(col * cell + cell * 0.18)
            cy0 = y0 + int(row * cell + cell * 0.18)
            cx1 = x0 + int((col + 1) * cell - cell * 0.18)
            cy1 = y0 + int((row + 1) * cell - cell * 0.18)
            density = _rect_mean(integral, cx0, cy0, cx1, cy1)
            if (row + col) % 2:
                dark_group.append(density)
            else:
                light_group.append(density)
    if not light_group or not dark_group:
        return 0.0
    contrast = abs((sum(light_group) / len(light_group)) - (sum(dark_group) / len(dark_group)))
    variance = float(np.std(light_group + dark_group))
    return min(1.0, contrast * 5.0 + variance * 1.8)


def _best_vertical_line_score(
    integral: np.ndarray,
    x0: int,
    y0: int,
    size: int,
    pos: int,
    *,
    band: int,
    tolerance: int,
) -> float:
    best = 0.0
    for candidate in range(max(0, pos - tolerance), min(size, pos + tolerance + 1)):
        start = x0 + max(0, candidate - band)
        end = x0 + min(size, candidate + band + 1)
        best = max(best, _rect_mean(integral, start, y0, end, y0 + size))
    return best


def _best_horizontal_line_score(
    integral: np.ndarray,
    x0: int,
    y0: int,
    size: int,
    pos: int,
    *,
    band: int,
    tolerance: int,
) -> float:
    best = 0.0
    for candidate in range(max(0, pos - tolerance), min(size, pos + tolerance + 1)):
        start = y0 + max(0, candidate - band)
        end = y0 + min(size, candidate + band + 1)
        best = max(best, _rect_mean(integral, x0, start, x0 + size, end))
    return best


def _dedupe_candidates(
    candidates: list[tuple[int, int, int, int, float]],
    *,
    max_count: int,
) -> list[tuple[int, int, int, int, float]]:
    selected: list[tuple[int, int, int, int, float]] = []
    for candidate in candidates:
        if all(_intersection_over_union(candidate, existing) < 0.25 and not _has_nearby_center(candidate, existing) for existing in selected):
            selected.append(candidate)
        if len(selected) >= max_count:
            break
    selected.sort(key=lambda item: (item[1], item[0]))
    return selected


def _has_nearby_center(
    first: tuple[int, int, int, int, float],
    second: tuple[int, int, int, int, float],
) -> bool:
    ax0, ay0, ax1, ay1, _ = first
    bx0, by0, bx1, by1, _ = second
    acx = (ax0 + ax1) / 2.0
    acy = (ay0 + ay1) / 2.0
    bcx = (bx0 + bx1) / 2.0
    bcy = (by0 + by1) / 2.0
    size = min(ax1 - ax0, ay1 - ay0, bx1 - bx0, by1 - by0)
    return abs(acx - bcx) <= size * 0.85 and abs(acy - bcy) <= size * 0.85


def _intersection_over_union(
    first: tuple[int, int, int, int, float],
    second: tuple[int, int, int, int, float],
) -> float:
    ax0, ay0, ax1, ay1, _ = first
    bx0, by0, bx1, by1, _ = second
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    area_a = (ax1 - ax0) * (ay1 - ay0)
    area_b = (bx1 - bx0) * (by1 - by0)
    return intersection / max(area_a + area_b - intersection, 1)
