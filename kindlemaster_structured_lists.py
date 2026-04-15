from __future__ import annotations

import re


INLINE_ALPHA_MARKER_RE = re.compile(r"(?<!\S)(?P<marker>[A-Z])[.)]\s+")
INLINE_NUMERIC_MARKER_RE = re.compile(r"(?<!\S)(?P<marker>\d{1,3})[.)]\s+")
MAX_INLINE_LIST_ITEMS = 16
MAX_INLINE_LIST_ITEM_LENGTH = 280


def normalize_inline_list_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _extract_items(text: str, matches: list[re.Match[str]]) -> list[str]:
    items: list[str] = []
    for index, match in enumerate(matches):
        item_start = match.end()
        item_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        item_text = normalize_inline_list_text(text[item_start:item_end])
        if item_text:
            item_text = re.sub(r"\s+([,.;:!?])", r"\1", item_text)
        items.append(item_text)
    return items


def _sequential_values(markers: list[str], *, marker_type: str) -> bool:
    if marker_type == "upper-alpha":
        values = [ord(marker) - ord("A") + 1 for marker in markers]
        if values[0] != 1:
            return False
    else:
        values = [int(marker) for marker in markers]
        if values[0] != 1:
            return False
    return all(current == previous + 1 for previous, current in zip(values, values[1:]))


def detect_inline_ordered_list(text: str, *, min_items: int = 3) -> dict[str, object] | None:
    normalized = normalize_inline_list_text(text)
    if not normalized:
        return None

    for marker_type, pattern in (
        ("upper-alpha", INLINE_ALPHA_MARKER_RE),
        ("decimal", INLINE_NUMERIC_MARKER_RE),
    ):
        matches = list(pattern.finditer(normalized))
        if len(matches) < min_items:
            continue
        if matches[0].start() != 0:
            continue
        if len(matches) > MAX_INLINE_LIST_ITEMS:
            continue
        markers = [match.group("marker") for match in matches]
        if not _sequential_values(markers, marker_type=marker_type):
            continue
        items = _extract_items(normalized, matches)
        if len(items) < min_items:
            continue
        if any(not item for item in items):
            continue
        if any(len(item) > MAX_INLINE_LIST_ITEM_LENGTH for item in items):
            continue
        return {
            "kind": "ordered_list",
            "list_style": marker_type,
            "marker_style": "period",
            "items": items,
            "source_text": normalized,
            "item_count": len(items),
        }
    return None
