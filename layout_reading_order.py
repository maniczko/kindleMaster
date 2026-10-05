from __future__ import annotations

from statistics import median
from typing import Sequence


def cluster_horizontal_anchors(
    anchors: Sequence[float],
    page_width: float,
    *,
    min_items_per_column: int = 2,
    max_columns: int = 4,
    min_gap_ratio: float = 0.08,
) -> list[list[int]]:
    """Cluster horizontal anchors into left-to-right column groups."""
    if page_width <= 0 or not anchors:
        return [list(range(len(anchors)))] if anchors else []

    min_items_per_column = max(1, int(min_items_per_column))
    max_columns = max(1, int(max_columns))
    ordered = sorted(enumerate(float(value) for value in anchors), key=lambda item: (item[1], item[0]))
    if len(ordered) < min_items_per_column * 2:
        return [[index for index, _ in ordered]]

    gaps = [
        (ordered[pos + 1][1] - ordered[pos][1], pos)
        for pos in range(len(ordered) - 1)
    ]
    significant = [
        (gap, pos)
        for gap, pos in gaps
        if gap >= page_width * min_gap_ratio
    ]
    if not significant:
        return [[index for index, _ in ordered]]

    max_candidate_columns = min(max_columns, len(ordered) // min_items_per_column)
    for column_count in range(max_candidate_columns, 1, -1):
        if len(significant) < column_count - 1:
            continue
        strongest = sorted(significant, key=lambda item: item[0], reverse=True)[: column_count - 1]
        cut_positions = sorted(pos for _, pos in strongest)

        groups: list[list[tuple[int, float]]] = []
        start = 0
        for cut in cut_positions:
            groups.append(ordered[start : cut + 1])
            start = cut + 1
        groups.append(ordered[start:])

        if any(len(group) < min_items_per_column for group in groups):
            continue

        medians = [median(value for _, value in group) for group in groups]
        if any(
            medians[idx + 1] - medians[idx] < page_width * 0.12
            for idx in range(len(medians) - 1)
        ):
            continue
        return [[index for index, _ in group] for group in groups]

    return [[index for index, _ in ordered]]


def infer_column_layout(
    boxes: Sequence[tuple[float, float]],
    page_width: float,
    *,
    min_items_per_column: int = 2,
    max_columns: int = 4,
    min_gap_ratio: float = 0.08,
) -> dict:
    """Infer column groups from horizontal x0/x1 intervals."""
    if not boxes:
        return {
            "column_count": 1,
            "groups": [],
            "counts": [],
            "gaps": [],
            "confidence": 1.0,
        }

    anchors = [float(x0) for x0, _ in boxes]
    groups = cluster_horizontal_anchors(
        anchors,
        page_width,
        min_items_per_column=min_items_per_column,
        max_columns=max_columns,
        min_gap_ratio=min_gap_ratio,
    )
    if len(groups) <= 1:
        return {
            "column_count": 1,
            "groups": [list(range(len(boxes)))],
            "counts": [len(boxes)],
            "gaps": [],
            "confidence": 0.98,
        }

    median_starts = [median(boxes[index][0] for index in group) for group in groups]
    median_ends = [median(boxes[index][1] for index in group) for group in groups]
    gaps = [
        float(median_starts[idx + 1] - median_ends[idx])
        for idx in range(len(groups) - 1)
    ]

    if any(gap < -(page_width * 0.03) for gap in gaps):
        return {
            "column_count": 1,
            "groups": [list(range(len(boxes)))],
            "counts": [len(boxes)],
            "gaps": [],
            "confidence": 0.72,
        }

    positive_gap = min((gap for gap in gaps if gap > 0), default=0.0)
    confidence = 0.86 + min(0.10, positive_gap / max(page_width, 1.0))
    return {
        "column_count": len(groups),
        "groups": groups,
        "counts": [len(group) for group in groups],
        "gaps": [round(gap, 2) for gap in gaps],
        "confidence": round(min(confidence, 0.98), 3),
    }


def estimate_column_count_from_anchors(
    anchors: Sequence[float],
    page_width: float,
    *,
    min_items_per_column: int = 1,
    max_columns: int = 4,
) -> int:
    groups = cluster_horizontal_anchors(
        anchors,
        page_width,
        min_items_per_column=min_items_per_column,
        max_columns=max_columns,
    )
    return max(1, len(groups))
