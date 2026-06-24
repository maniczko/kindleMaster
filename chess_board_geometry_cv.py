from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class BoardGeometryResult:
    found: bool = False
    bbox: tuple[int, int, int, int] | None = None
    quad: tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]] | None = None
    confidence: float = 0.0
    method: str = "opencv-unavailable"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "quad": [list(point) for point in self.quad] if self.quad is not None else None,
            "confidence": round(float(self.confidence or 0.0), 3),
            "method": self.method,
            "warnings": list(self.warnings),
        }


def opencv_available() -> bool:
    return importlib.util.find_spec("cv2") is not None


def detect_board_quad_cv(image: Image.Image | bytes) -> BoardGeometryResult:
    """Detect a likely chessboard quad for audit-only diagnostics.

    This function is intentionally not used by runtime recognition. It returns
    unavailable when OpenCV is not installed, allowing CI/quick lanes to skip CV
    geometry tests cleanly.
    """
    if not opencv_available():
        return BoardGeometryResult(warnings=["opencv_unavailable"])
    import cv2  # type: ignore

    pil_image = _coerce_image(image)
    gray = np.array(pil_image.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        return BoardGeometryResult(method="opencv-contours", warnings=["image_empty"])
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 40, 120)
    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(gray.shape[0] * gray.shape[1])
    best: tuple[float, np.ndarray] | None = None
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < image_area * 0.08:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        approx = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
        if len(approx) != 4:
            continue
        x, y, w, h = cv2.boundingRect(approx)
        ratio = w / float(max(h, 1))
        if not 0.72 <= ratio <= 1.38:
            continue
        extent = area / float(max(w * h, 1))
        score = min(area / image_area, 1.0) * 0.65 + min(extent, 1.0) * 0.35
        if best is None or score > best[0]:
            best = (score, approx)
    if best is None:
        return BoardGeometryResult(method="opencv-contours", warnings=["board_quad_not_found"])
    score, approx = best
    points = _order_quad_points([(float(point[0][0]), float(point[0][1])) for point in approx])
    xs = [int(round(point[0])) for point in points]
    ys = [int(round(point[1])) for point in points]
    return BoardGeometryResult(
        found=True,
        bbox=(min(xs), min(ys), max(xs), max(ys)),
        quad=tuple(points),  # type: ignore[arg-type]
        confidence=max(0.0, min(1.0, score)),
        method="opencv-contours",
        warnings=[],
    )


def warp_board_quad_cv(image: Image.Image | bytes, quad: tuple[tuple[float, float], ...]) -> Image.Image | None:
    if not opencv_available():
        return None
    import cv2  # type: ignore

    if len(quad) != 4:
        return None
    pil_image = _coerce_image(image).convert("RGB")
    source = np.array(quad, dtype=np.float32)
    side = int(round(max(_distance(source[0], source[1]), _distance(source[1], source[2]), _distance(source[2], source[3]), _distance(source[3], source[0]))))
    if side <= 0:
        return None
    destination = np.array([[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(np.array(pil_image), matrix, (side, side))
    return Image.fromarray(warped)


def render_cv_geometry_overlay(image: Image.Image | bytes, result: BoardGeometryResult, output_path: str | Path) -> dict[str, Any]:
    pil_image = _coerce_image(image).convert("RGB")
    draw = ImageDraw.Draw(pil_image)
    if result.quad:
        points = [(float(x), float(y)) for x, y in result.quad]
        draw.line([*points, points[0]], fill=(0, 255, 0), width=max(2, min(pil_image.size) // 120))
    elif result.bbox:
        draw.rectangle(result.bbox, outline=(0, 255, 0), width=max(2, min(pil_image.size) // 120))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    pil_image.save(output)
    return {"status": "written", "path": str(output), "found": result.found, "method": result.method}


def _coerce_image(image: Image.Image | bytes) -> Image.Image:
    if isinstance(image, Image.Image):
        return image
    import io

    return Image.open(io.BytesIO(image))


def _order_quad_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted(points, key=lambda item: (item[1], item[0]))
    top = sorted(ordered[:2], key=lambda item: item[0])
    bottom = sorted(ordered[2:], key=lambda item: item[0])
    return [top[0], top[1], bottom[1], bottom[0]]


def _distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))
