from __future__ import annotations

import re
import statistics
from collections.abc import Callable, Iterable, Mapping
from typing import Any, TypeVar


T = TypeVar("T")
GEOMETRY_SCHEMA = "kindlemaster.chess.page_geometry.v1"
PRINTED_NUMBER_RE = re.compile(r"^\s*(?P<number>\d{1,4})[.)]?\s*$")


def bbox4(value: Any) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        x0, y0, x1, y1 = (float(part) for part in value[:4])
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)
    return (x0, y0, x1, y1)


def order_geometry_items(
    items: Iterable[T],
    *,
    bbox_getter: Callable[[T], Any],
    page_width: float,
) -> list[T]:
    values = list(items)
    if len(values) < 2:
        return values
    boxes = [bbox4(bbox_getter(item)) for item in values]
    columns, column_count = infer_columns(boxes, page_width=page_width)
    if column_count < 2:
        return [item for _, item in sorted(zip(boxes, values), key=lambda pair: (pair[0][1], pair[0][0]))]
    indexed = list(zip(values, boxes, columns))
    indexed.sort(key=lambda entry: (entry[2], entry[1][1], entry[1][0]))
    return [entry[0] for entry in indexed]


def infer_columns(
    boxes: Iterable[Any],
    *,
    page_width: float,
    max_columns: int = 4,
) -> tuple[list[int], int]:
    normalized = [bbox4(value) for value in boxes]
    if len(normalized) < 4 or page_width <= 0:
        return [0] * len(normalized), 1
    widths = [max(0.0, box[2] - box[0]) for box in normalized if box[2] > box[0]]
    if not widths:
        return [0] * len(normalized), 1
    median_width = statistics.median(widths)
    centers = sorted(((box[0] + box[2]) / 2.0, index) for index, box in enumerate(normalized))
    threshold = max(page_width * 0.12, median_width * 0.35)
    gaps = [
        (centers[index + 1][0] - centers[index][0], index)
        for index in range(len(centers) - 1)
        if centers[index + 1][0] - centers[index][0] >= threshold
    ]
    if not gaps:
        return [0] * len(normalized), 1
    split_indexes = sorted(index for _, index in sorted(gaps, reverse=True)[: max_columns - 1])
    clusters: list[list[tuple[float, int]]] = []
    start = 0
    for split in split_indexes:
        clusters.append(centers[start : split + 1])
        start = split + 1
    clusters.append(centers[start:])
    if len(clusters) < 2 or any(len(cluster) < 2 for cluster in clusters):
        return [0] * len(normalized), 1
    assignments = [0] * len(normalized)
    for column, cluster in enumerate(clusters):
        for _, original_index in cluster:
            assignments[original_index] = column
    return assignments, len(clusters)


def analyze_exercise_page_geometry(
    blocks: Iterable[Mapping[str, Any]],
    *,
    page_number: int,
    page_width: float,
    page_height: float,
) -> dict[str, Any]:
    source_blocks = [dict(block) for block in blocks if isinstance(block, Mapping)]
    grouped: dict[int, list[dict[str, Any]]] = {}
    number_candidates: list[dict[str, Any]] = []
    for index, block in enumerate(source_blocks):
        block_index = int(block.get("block_index") if block.get("block_index") is not None else index)
        grouped.setdefault(block_index, []).append(block)
        text = str(block.get("text") or "").strip()
        match = PRINTED_NUMBER_RE.fullmatch(text)
        box = bbox4(block.get("bbox"))
        if match and box[1] >= max(0.0, page_height * 0.08):
            number_candidates.append(
                {
                    "number": int(match.group("number")),
                    "raw_text": text,
                    "bbox": list(box),
                    "block_index": block_index,
                }
            )

    diagram_regions: list[dict[str, Any]] = []
    for block_index, members in grouped.items():
        text = " ".join(str(member.get("text") or "") for member in members)
        parent_box = bbox4(next((member.get("parent_bbox") for member in members if member.get("parent_bbox")), None))
        private_use_count = sum(0xE000 <= ord(char) <= 0xF8FF for char in text)
        width = max(0.0, parent_box[2] - parent_box[0])
        height = max(0.0, parent_box[3] - parent_box[1])
        if (
            private_use_count >= 24
            and page_width * 0.18 <= width <= page_width * 0.62
            and height >= page_height * 0.12
        ):
            diagram_regions.append(
                {
                    "block_index": block_index,
                    "bbox": list(parent_box),
                    "private_use_count": private_use_count,
                }
            )

    ordered_regions = order_geometry_items(
        diagram_regions,
        bbox_getter=lambda item: item["bbox"],
        page_width=page_width,
    )
    region_columns, column_count = infer_columns(
        [item["bbox"] for item in ordered_regions],
        page_width=page_width,
    )
    for visual_order, (region, column) in enumerate(zip(ordered_regions, region_columns), start=1):
        region["column"] = column + 1
        region["visual_order"] = visual_order

    used_candidates: set[int] = set()
    assignments: list[dict[str, Any]] = []
    for region in ordered_regions:
        region_box = bbox4(region["bbox"])
        target_y = region_box[1] + (region_box[3] - region_box[1]) * 0.14
        region_center_x = (region_box[0] + region_box[2]) / 2.0
        ranked: list[tuple[float, int, dict[str, Any]]] = []
        for candidate_index, candidate in enumerate(number_candidates):
            if candidate_index in used_candidates:
                continue
            candidate_box = bbox4(candidate["bbox"])
            candidate_center_x = (candidate_box[0] + candidate_box[2]) / 2.0
            if abs(candidate_center_x - region_box[0]) > page_width * 0.08:
                continue
            vertical_distance = abs(candidate_box[1] - target_y)
            horizontal_distance = abs(candidate_center_x - region_center_x) * 0.08
            ranked.append((vertical_distance + horizontal_distance, candidate_index, candidate))
        ranked.sort(key=lambda item: item[0])
        warning_codes: list[str] = []
        selected = ranked[0] if ranked and ranked[0][0] <= page_height * 0.12 else None
        if selected is None:
            warning_codes.append("UNKNOWN_EXERCISE_NUMBER")
            assignment = {
                "candidate_number": None,
                "number_bbox": None,
                "confidence": 0.0,
            }
        else:
            score, candidate_index, candidate = selected
            used_candidates.add(candidate_index)
            if len(ranked) > 1 and abs(ranked[1][0] - score) <= page_height * 0.015:
                warning_codes.append("AMBIGUOUS_DIAGRAM_MATCH")
            confidence = round(max(0.0, 1.0 - score / max(1.0, page_height * 0.12)), 4)
            if confidence < 0.8:
                warning_codes.append("LOW_CONFIDENCE_EXERCISE_NUMBER_MATCH")
            assignment = {
                "candidate_number": int(candidate["number"]),
                "number_bbox": list(candidate["bbox"]),
                "confidence": confidence,
            }
        assignments.append(
            {
                "source_page": page_number,
                "column": int(region["column"]),
                "visual_order": int(region["visual_order"]),
                "diagram_bbox": list(region["bbox"]),
                "diagram_block_index": int(region["block_index"]),
                **assignment,
                "status": "candidate",
                "warnings": warning_codes,
            }
        )

    numbers = [item["candidate_number"] for item in assignments if item.get("candidate_number") is not None]
    duplicate_numbers = sorted({number for number in numbers if numbers.count(number) > 1})
    sequence_is_contiguous = bool(numbers) and len(numbers) == len(assignments) and all(
        current == previous + 1 for previous, current in zip(numbers, numbers[1:])
    )
    page_warnings: list[str] = []
    if duplicate_numbers:
        page_warnings.append("DUPLICATE_EXERCISE_NUMBER")
    if assignments and not sequence_is_contiguous:
        page_warnings.append("NON_CONTIGUOUS_EXERCISE_SEQUENCE")
    if len(used_candidates) < len(number_candidates):
        page_warnings.append("DETACHED_EXERCISE_NUMBER")

    for assignment in assignments:
        assignment["warnings"] = sorted(set([*assignment["warnings"], *page_warnings]))
        accepted = (
            assignment.get("candidate_number") is not None
            and assignment.get("confidence", 0.0) >= 0.8
            and not assignment["warnings"]
        )
        assignment["status"] = "accepted" if accepted else "needs_review"
        assignment["exercise_number"] = assignment["candidate_number"] if accepted else None

    all_warnings = sorted(
        set([*page_warnings, *(warning for assignment in assignments for warning in assignment["warnings"])])
    )
    return {
        "schema": GEOMETRY_SCHEMA,
        "source_page": page_number,
        "page_width": round(float(page_width), 3),
        "page_height": round(float(page_height), 3),
        "column_count": column_count,
        "diagram_count": len(ordered_regions),
        "number_candidate_count": len(number_candidates),
        "assignments": assignments,
        "warnings": all_warnings,
        "status": "accepted" if assignments and all(item["status"] == "accepted" for item in assignments) else "needs_review",
    }


def bbox_containment(inner_value: Any, outer_value: Any) -> float:
    inner = bbox4(inner_value)
    outer = bbox4(outer_value)
    width = max(0.0, inner[2] - inner[0])
    height = max(0.0, inner[3] - inner[1])
    area = width * height
    if area <= 0:
        return 0.0
    intersection_width = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    intersection_height = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    return (intersection_width * intersection_height) / area
