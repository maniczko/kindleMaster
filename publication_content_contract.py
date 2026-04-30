from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


_MISSING = object()

_TABLE_INT_FIELDS = {
    "table_cell_count",
    "table_row_count",
    "table_page_count",
    "multi_page_table_count",
    "wide_table_count",
    "low_confidence_table_count",
    "fragment_table_count",
}
_TABLE_FLOAT_FIELDS = {"table_cell_coverage"}
_METADATA_INT_FIELDS = {"source_table_count", "xhtml_table_count"}
_CHAPTER_PAGE_FIELDS = {"_page_start", "_page_end", "page_num"}


def adapt_extractor_content(
    content: Any,
    *,
    expect_images: bool = False,
    expect_table_summary: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize extractor content to the minimal PublicationDocument contract."""

    warnings: list[dict[str, Any]] = []
    if not isinstance(content, Mapping):
        _warn(
            warnings,
            code="malformed_content",
            path="$",
            message="Extractor returned non-mapping content; using an empty content shell.",
            expected="dict",
            actual=content,
        )
        content = {}

    adapted = dict(content)
    adapted["metadata"] = _adapt_metadata(
        adapted.get("metadata", _MISSING),
        warnings,
        expect_table_summary=expect_table_summary,
    )

    chapters = _adapt_chapters(adapted.get("chapters", _MISSING), warnings)
    adapted["chapters"] = chapters
    adapted["images"] = _adapt_image_assets(adapted.get("images", _MISSING), "$.images", warnings)
    adapted["audit"] = _adapt_optional_mapping(adapted.get("audit", _MISSING), "$.audit", "malformed_audit", warnings)
    adapted["toc"] = _adapt_optional_sequence(adapted.get("toc", _MISSING), "$.toc", "malformed_toc", warnings)

    if expect_images and not _has_any_valid_images(adapted["images"], chapters):
        _warn(
            warnings,
            code="missing_images",
            path="$.images",
            message="Analysis expected image assets, but extractor content did not provide valid images.",
            expected="non-empty list of image asset dicts",
            actual=adapted.get("images", _MISSING),
        )

    return adapted, warnings


def _adapt_metadata(
    value: Any,
    warnings: list[dict[str, Any]],
    *,
    expect_table_summary: bool,
) -> dict[str, Any]:
    if value is _MISSING or value is None:
        metadata: dict[str, Any] = {}
    elif isinstance(value, Mapping):
        metadata = dict(value)
    else:
        _warn(
            warnings,
            code="malformed_metadata",
            path="$.metadata",
            message="Extractor metadata must be a mapping; ignoring malformed metadata.",
            expected="dict",
            actual=value,
        )
        metadata = {}

    for field in _METADATA_INT_FIELDS:
        if field in metadata:
            coerced = _coerce_int(metadata[field])
            if coerced is None:
                _warn(
                    warnings,
                    code="malformed_metadata_metric",
                    path=f"$.metadata.{field}",
                    message="Metadata table count must be numeric; using 0.",
                    expected="int-compatible value",
                    actual=metadata[field],
                )
                metadata[field] = 0
            else:
                metadata[field] = coerced

    table_summary = metadata.get("table_summary", _MISSING)
    if table_summary is _MISSING or table_summary is None:
        if expect_table_summary:
            _warn(
                warnings,
                code="missing_table_summary",
                path="$.metadata.table_summary",
                message="Analysis expected tables, but extractor metadata did not include table_summary.",
                expected="dict",
                actual=table_summary,
            )
    elif not isinstance(table_summary, Mapping):
        _warn(
            warnings,
            code="malformed_table_summary",
            path="$.metadata.table_summary",
            message="Extractor table_summary must be a mapping; ignoring malformed table summary.",
            expected="dict",
            actual=table_summary,
        )
        metadata["table_summary"] = {}
    else:
        metadata["table_summary"] = _adapt_table_summary(dict(table_summary), warnings)

    return metadata


def _adapt_table_summary(value: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    for field in _TABLE_INT_FIELDS:
        if field not in value:
            continue
        coerced = _coerce_int(value[field])
        if coerced is None:
            _warn(
                warnings,
                code="malformed_table_summary_metric",
                path=f"$.metadata.table_summary.{field}",
                message="Table summary count must be numeric; using 0.",
                expected="int-compatible value",
                actual=value[field],
            )
            value[field] = 0
        else:
            value[field] = coerced

    for field in _TABLE_FLOAT_FIELDS:
        if field not in value:
            continue
        coerced = _coerce_float(value[field])
        if coerced is None:
            _warn(
                warnings,
                code="malformed_table_summary_metric",
                path=f"$.metadata.table_summary.{field}",
                message="Table summary coverage must be numeric; using 0.0.",
                expected="float-compatible value",
                actual=value[field],
            )
            value[field] = 0.0
        else:
            value[field] = coerced

    return value


def _adapt_chapters(value: Any, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if value is _MISSING or value is None:
        _warn(
            warnings,
            code="missing_chapters",
            path="$.chapters",
            message="Extractor content did not include chapters; using an empty chapter list.",
            expected="list of chapter dicts",
            actual=value,
        )
        return []
    if not _is_non_string_sequence(value):
        _warn(
            warnings,
            code="malformed_chapters",
            path="$.chapters",
            message="Extractor chapters must be a sequence; using an empty chapter list.",
            expected="list of chapter dicts",
            actual=value,
        )
        return []

    chapters: list[dict[str, Any]] = []
    for index, chapter_value in enumerate(value):
        path = f"$.chapters[{index}]"
        if not isinstance(chapter_value, Mapping):
            _warn(
                warnings,
                code="malformed_chapter",
                path=path,
                message="Extractor chapter must be a mapping; skipping malformed chapter.",
                expected="chapter dict",
                actual=chapter_value,
            )
            continue

        chapter = dict(chapter_value)
        _adapt_chapter_scalar_fields(chapter, path, warnings)
        chapter["html_parts"] = _adapt_html_parts(chapter.get("html_parts", _MISSING), f"{path}.html_parts", warnings)
        chapter["images"] = _adapt_image_assets(chapter.get("images", _MISSING), f"{path}.images", warnings)
        chapters.append(chapter)

    return chapters


def _adapt_chapter_scalar_fields(chapter: dict[str, Any], path: str, warnings: list[dict[str, Any]]) -> None:
    title = chapter.get("title", _MISSING)
    if title is not _MISSING and title is not None and not isinstance(title, str):
        _warn(
            warnings,
            code="malformed_chapter_title",
            path=f"{path}.title",
            message="Chapter title must be text; coercing title to text.",
            expected="str",
            actual=title,
        )
        chapter["title"] = str(title)

    for field in _CHAPTER_PAGE_FIELDS:
        if field not in chapter or chapter[field] is None:
            continue
        coerced = _coerce_int(chapter[field])
        if coerced is None:
            _warn(
                warnings,
                code="malformed_chapter_page",
                path=f"{path}.{field}",
                message="Chapter page value must be numeric; falling back to pipeline defaults.",
                expected="int-compatible value",
                actual=chapter[field],
            )
            chapter.pop(field, None)
        else:
            chapter[field] = coerced


def _adapt_html_parts(value: Any, path: str, warnings: list[dict[str, Any]]) -> list[str]:
    if value is _MISSING or value is None:
        _warn(
            warnings,
            code="missing_html_parts",
            path=path,
            message="Chapter did not include html_parts; using an empty fragment list.",
            expected="list of HTML fragment strings",
            actual=value,
        )
        return []

    if isinstance(value, str):
        _warn(
            warnings,
            code="malformed_html_parts",
            path=path,
            message="Chapter html_parts must be a sequence; treating the string as one HTML fragment.",
            expected="list of HTML fragment strings",
            actual=value,
        )
        return [value]

    if isinstance(value, bytes):
        _warn(
            warnings,
            code="malformed_html_parts",
            path=path,
            message="Chapter html_parts must be text fragments; decoding bytes as one fragment.",
            expected="list of HTML fragment strings",
            actual=value,
        )
        return [value.decode("utf-8", errors="replace")]

    if not _is_non_string_sequence(value):
        _warn(
            warnings,
            code="malformed_html_parts",
            path=path,
            message="Chapter html_parts must be a sequence; coercing value to one text fragment.",
            expected="list of HTML fragment strings",
            actual=value,
        )
        return [str(value)]

    html_parts: list[str] = []
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if isinstance(item, str):
            html_parts.append(item)
        elif isinstance(item, bytes):
            _warn(
                warnings,
                code="malformed_html_part",
                path=item_path,
                message="HTML fragment was bytes; decoding as UTF-8 replacement text.",
                expected="str",
                actual=item,
            )
            html_parts.append(item.decode("utf-8", errors="replace"))
        elif item is None:
            _warn(
                warnings,
                code="malformed_html_part",
                path=item_path,
                message="HTML fragment was null; dropping it.",
                expected="str",
                actual=item,
            )
        else:
            _warn(
                warnings,
                code="malformed_html_part",
                path=item_path,
                message="HTML fragment must be text; coercing fragment to text.",
                expected="str",
                actual=item,
            )
            html_parts.append(str(item))
    return html_parts


def _adapt_image_assets(value: Any, path: str, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if value is _MISSING or value is None:
        return []
    if isinstance(value, Mapping) or not _is_non_string_sequence(value):
        _warn(
            warnings,
            code="malformed_images",
            path=path,
            message="Image assets must be a list of mappings; ignoring malformed image assets.",
            expected="list of image asset dicts",
            actual=value,
        )
        return []

    assets: list[dict[str, Any]] = []
    for index, asset in enumerate(value):
        if isinstance(asset, Mapping):
            assets.append(dict(asset))
            continue
        _warn(
            warnings,
            code="malformed_image",
            path=f"{path}[{index}]",
            message="Image asset must be a mapping; skipping malformed image asset.",
            expected="image asset dict",
            actual=asset,
        )
    return assets


def _adapt_optional_mapping(
    value: Any,
    path: str,
    code: str,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    if value is _MISSING or value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    _warn(
        warnings,
        code=code,
        path=path,
        message="Optional extractor field must be a mapping; ignoring malformed value.",
        expected="dict",
        actual=value,
    )
    return {}


def _adapt_optional_sequence(
    value: Any,
    path: str,
    code: str,
    warnings: list[dict[str, Any]],
) -> list[Any]:
    if value is _MISSING or value is None:
        return []
    if _is_non_string_sequence(value):
        return list(value)
    _warn(
        warnings,
        code=code,
        path=path,
        message="Optional extractor field must be a sequence; ignoring malformed value.",
        expected="list",
        actual=value,
    )
    return []


def _has_any_valid_images(global_assets: list[dict[str, Any]], chapters: list[dict[str, Any]]) -> bool:
    if global_assets:
        return True
    return any(chapter.get("images") for chapter in chapters)


def _is_non_string_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _warn(
    warnings: list[dict[str, Any]],
    *,
    code: str,
    path: str,
    message: str,
    expected: str,
    actual: Any,
) -> None:
    warnings.append(
        {
            "severity": "warning",
            "code": code,
            "path": path,
            "message": message,
            "expected": expected,
            "actual_type": "missing" if actual is _MISSING else type(actual).__name__,
        }
    )
