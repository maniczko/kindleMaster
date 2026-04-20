from __future__ import annotations

import re
from typing import Iterable


_PARATEXT_TITLE_PATTERNS = (
    r"(?i)^front cover$",
    r"(?i)^back cover$",
    r"(?i)^title$",
    r"(?i)^copyright$",
    r"(?i)^(table of )?contents$",
    r"(?i)^spis treści$",
    r"(?i)^spis tresci$",
    r"(?i)^name index$",
    r"(?i)^index$",
    r"(?i)^opening index$",
    r"(?i)^sample record sheets?$",
    r"(?i)^about the authors?$",
    r"(?i)^about the author$",
    r"(?i)^acknowledg(e)?ments$",
)

_CONTAINER_TITLE_PATTERNS = (
    r"(?i)^part\b",
    r"(?i)^book\b",
    r"(?i)^unit\b",
    r"(?i)^module\b",
    r"(?i)^volume\b",
    r"(?i)^vol\.\b",
    r"(?i)^część\b",
    r"(?i)^czesc\b",
    r"(?i)^tom\b",
)


def normalize_toc_entries(raw_toc: Iterable[Iterable]) -> list[dict]:
    entries: list[dict] = []
    for row in raw_toc:
        row = list(row)
        if len(row) < 3:
            continue
        title = _normalize_title(row[1])
        if not title:
            continue
        try:
            level = int(row[0])
            page = max(0, int(row[2]) - 1)
        except (TypeError, ValueError):
            continue
        entries.append({"level": level, "title": title, "page": page})
    entries.sort(key=lambda item: (item["page"], item["level"], item["title"].lower()))
    return entries


def select_section_outline_entries(entries: list[dict]) -> list[dict]:
    if not entries:
        return []

    levels = sorted({entry["level"] for entry in entries})
    if len(levels) == 1:
        return _dedupe_entries_by_page(entries)

    top_level = levels[0]
    nested_level = levels[1]
    top_entries = [entry for entry in entries if entry["level"] == top_level]
    nested_entries = [entry for entry in entries if entry["level"] == nested_level]

    if not _should_prefer_nested_outline(top_entries, nested_entries):
        return _dedupe_entries_by_page(top_entries)

    first_nested_page = nested_entries[0]["page"]
    last_nested_page = nested_entries[-1]["page"]
    selected: list[dict] = []
    for entry in entries:
        if entry["level"] == nested_level:
            selected.append(entry)
            continue
        if entry["level"] != top_level:
            continue
        if entry["page"] < first_nested_page or entry["page"] > last_nested_page:
            selected.append(entry)
            continue
        if _is_contents_title(entry["title"]):
            selected.append(entry)
            continue
        if not _looks_like_container_outline_title(entry["title"]):
            selected.append(entry)
    return _dedupe_entries_by_page(selected)


def _normalize_title(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip(" -")


def _should_prefer_nested_outline(top_entries: list[dict], nested_entries: list[dict]) -> bool:
    if len(nested_entries) < 4:
        return False

    body_top_entries = [
        entry
        for entry in top_entries
        if not _is_paratext_toc_title(entry["title"]) and not _is_contents_title(entry["title"])
    ]
    if not body_top_entries:
        return True

    if len(body_top_entries) <= 4 and all(_looks_like_container_outline_title(entry["title"]) for entry in body_top_entries):
        return True

    return False


def _dedupe_entries_by_page(entries: list[dict]) -> list[dict]:
    best_by_page: dict[int, dict] = {}
    for entry in entries:
        page = entry["page"]
        current = best_by_page.get(page)
        if current is None or _entry_specificity_score(entry) > _entry_specificity_score(current):
            best_by_page[page] = entry
    return [best_by_page[page] for page in sorted(best_by_page)]


def _entry_specificity_score(entry: dict) -> int:
    title = entry["title"]
    score = 0
    if not _is_paratext_toc_title(title):
        score += 4
    if not _is_contents_title(title):
        score += 2
    if entry["level"] > 1:
        score += 2
    if not _looks_like_container_outline_title(title):
        score += 1
    word_count = len(title.split())
    if 1 < word_count <= 12:
        score += 1
    if len(title) >= 12:
        score += 1
    return score


def _is_contents_title(title: str) -> bool:
    normalized = title.strip().lower()
    return normalized in {"contents", "table of contents", "spis treści", "spis tresci"}


def _is_paratext_toc_title(title: str) -> bool:
    normalized = title.strip()
    return any(re.fullmatch(pattern, normalized) for pattern in _PARATEXT_TITLE_PATTERNS)


def _looks_like_container_outline_title(title: str) -> bool:
    normalized = title.strip()
    return any(re.match(pattern, normalized) for pattern in _CONTAINER_TITLE_PATTERNS)
