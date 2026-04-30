from __future__ import annotations

import html
import json
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping

from conversion_api_contracts import resolve_conversion_download_state


LIBRARY_INDEX_VERSION = "kindlemaster-library-v1"
DEFAULT_FULL_TEXT_EXCERPT_CHARS = 700
MAX_FULL_TEXT_EXCERPT_CHARS = 4000
MAX_EPUB_TEXT_FILES = 80
MAX_EPUB_TEXT_CHARS = 120_000


QualityStateBuilder = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
OutputSizeResolver = Callable[[Mapping[str, Any]], int | None]


def _text(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _lower_text(value: Any) -> str:
    return _text(value).lower()


def _int_or_none(value: Any) -> int | None:
    try:
        converted = int(value)
    except (TypeError, ValueError):
        return None
    return converted if converted >= 0 else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "svg", "math"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "svg", "math"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        text = _normalize_space(data)
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return _normalize_space(" ".join(self.parts))


def _extract_visible_text(markup: bytes) -> str:
    parser = _VisibleTextParser()
    try:
        parser.feed(markup.decode("utf-8", errors="ignore"))
        parser.close()
    except Exception:
        return ""
    return html.unescape(parser.text())


def extract_epub_text_excerpt(
    epub_path: str | Path,
    *,
    excerpt_chars: int = DEFAULT_FULL_TEXT_EXCERPT_CHARS,
) -> dict[str, Any]:
    """Extract a bounded text excerpt from a generated EPUB for local search."""

    path = Path(epub_path)
    excerpt_limit = max(120, min(int(excerpt_chars), MAX_FULL_TEXT_EXCERPT_CHARS))
    if not path.exists() or not path.is_file():
        return {
            "available": False,
            "excerpt": "",
            "char_count": 0,
            "source_files": [],
            "error": "missing_epub",
        }

    try:
        with zipfile.ZipFile(path) as archive:
            candidates = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".xhtml", ".html", ".htm"))
                and not name.lower().endswith(("nav.xhtml", "toc.xhtml"))
            ][:MAX_EPUB_TEXT_FILES]

            parts: list[str] = []
            char_count = 0
            source_files: list[str] = []
            for name in candidates:
                try:
                    text = _extract_visible_text(archive.read(name))
                except (KeyError, OSError, zipfile.BadZipFile):
                    continue
                if not text:
                    continue
                parts.append(text)
                source_files.append(name)
                char_count += len(text)
                if char_count >= MAX_EPUB_TEXT_CHARS:
                    break
    except (OSError, zipfile.BadZipFile) as error:
        return {
            "available": False,
            "excerpt": "",
            "char_count": 0,
            "source_files": [],
            "error": error.__class__.__name__,
        }

    full_text = _normalize_space(" ".join(parts))
    return {
        "available": bool(full_text),
        "excerpt": full_text[:excerpt_limit],
        "char_count": len(full_text),
        "source_files": source_files[:12],
        "error": "",
    }


def _quality_blocker_terms(quality_state: Mapping[str, Any]) -> list[str]:
    terms: list[str] = []
    for blocker in _list(quality_state.get("quality_blockers")):
        payload = _mapping(blocker)
        terms.extend(
            filter(
                None,
                [
                    _text(payload.get("severity")),
                    _text(payload.get("code")),
                    _text(payload.get("message")),
                    _text(payload.get("source")),
                    _text(payload.get("suggested_action")),
                ],
            )
        )
    return terms


def _issue_terms(quality_state: Mapping[str, Any]) -> list[str]:
    groups = _mapping(quality_state.get("issue_groups"))
    terms: list[str] = []
    for group_name in ("blockers", "warnings", "review"):
        for issue in _list(groups.get(group_name)):
            payload = _mapping(issue)
            terms.extend(
                filter(
                    None,
                    [
                        group_name,
                        _text(payload.get("severity")),
                        _text(payload.get("code")),
                        _text(payload.get("message")),
                        _text(payload.get("source")),
                    ],
                )
            )
    return terms


def _metadata_terms(job: Mapping[str, Any], quality_state: Mapping[str, Any]) -> list[str]:
    metadata = _mapping(job.get("metadata"))
    metadata_summary = _mapping(quality_state.get("metadata_summary"))
    source_analysis = _mapping(metadata.get("source_analysis"))
    document_summary = _mapping(metadata.get("document_summary"))
    return [
        _text(job.get("filename")),
        _text(job.get("download_name")),
        _text(job.get("source_type")),
        _text(metadata.get("profile")),
        _text(metadata.get("layout")),
        _text(metadata.get("strategy")),
        _text(metadata.get("document_class")),
        _text(source_analysis.get("document_class")),
        _text(document_summary.get("document_class")),
        _text(metadata_summary.get("title")),
        _text(metadata_summary.get("creator")),
        _text(metadata_summary.get("language")),
        _text(quality_state.get("release_verdict")),
        _text(quality_state.get("reading_verdict")),
    ]


def _matches_query(search_text: str, query: str) -> bool:
    normalized_query = _lower_text(query)
    if not normalized_query:
        return True
    tokens = [token for token in re.split(r"\s+", normalized_query) if token]
    if not tokens:
        return True
    normalized_search_text = search_text.lower()
    return all(token in normalized_search_text for token in tokens)


def _matched_fields(fields: Mapping[str, str], query: str) -> list[str]:
    normalized_query = _lower_text(query)
    if not normalized_query:
        return []
    tokens = [token for token in re.split(r"\s+", normalized_query) if token]
    matched: list[str] = []
    for name, value in fields.items():
        lowered_value = value.lower()
        if any(token in lowered_value for token in tokens):
            matched.append(name)
    return matched


def _document_class(job: Mapping[str, Any], quality_state: Mapping[str, Any]) -> str:
    metadata = _mapping(job.get("metadata"))
    source_analysis = _mapping(metadata.get("source_analysis"))
    document_summary = _mapping(metadata.get("document_summary"))
    return (
        _text(metadata.get("document_class"))
        or _text(source_analysis.get("document_class"))
        or _text(document_summary.get("document_class"))
        or _text(quality_state.get("source_type"))
        or _text(job.get("source_type"))
    )


@dataclass(frozen=True)
class LibraryFilters:
    query: str = ""
    status: str = ""
    release_verdict: str = ""
    include_text: bool = False
    limit: int = 25


def build_library_item(
    job_id: str,
    job: Mapping[str, Any],
    *,
    quality_state: Mapping[str, Any],
    output_size_bytes: int | None,
    text_excerpt: Mapping[str, Any] | None = None,
    query: str = "",
) -> dict[str, Any]:
    status = _lower_text(job.get("status")) or "unknown"
    response_job_id = _text(job.get("job_id")) or job_id
    metadata_summary = dict(_mapping(quality_state.get("metadata_summary")))
    title = _text(metadata_summary.get("title")) or _text(job.get("filename")) or "Untitled"
    output_path = _text(job.get("output_path"))
    download_state = resolve_conversion_download_state(
        job_status=status,
        output_path=output_path,
        download_url=f"/convert/download/{response_job_id}" if status == "ready" else "",
    )
    download_available = download_state.download_available
    blockers = [
        dict(_mapping(blocker))
        for blocker in _list(quality_state.get("quality_blockers"))
    ]
    text_payload = dict(text_excerpt or {})
    fields = {
        "filename": _text(job.get("filename")),
        "title": title,
        "source_type": _text(job.get("source_type")),
        "document_class": _document_class(job, quality_state),
        "release_verdict": _text(quality_state.get("release_verdict")),
        "reading_verdict": _text(quality_state.get("reading_verdict")),
        "quality_blockers": " ".join(_quality_blocker_terms(quality_state)),
        "issues": " ".join(_issue_terms(quality_state)),
        "full_text": _text(text_payload.get("excerpt")),
    }

    item = {
        "job_id": response_job_id,
        "title": title,
        "filename": _text(job.get("filename")),
        "source_type": _text(job.get("source_type")),
        "document_class": fields["document_class"],
        "status": status,
        "created_at": _text(job.get("created_at")),
        "updated_at": _text(job.get("updated_at")),
        "elapsed_seconds": _int_or_none(job.get("elapsed_seconds")),
        "output_size_bytes": output_size_bytes,
        "download_available": download_available,
        "download_state": download_state.to_dict(),
        "download_url": download_state.download_url or "",
        "quality_state_url": f"/convert/quality/{response_job_id}",
        "report_json_url": f"/convert/report/{response_job_id}.json",
        "report_markdown_url": f"/convert/report/{response_job_id}.md",
        "release_verdict": fields["release_verdict"] or "unknown",
        "reading_verdict": fields["reading_verdict"] or "unknown",
        "release_blocked": bool(quality_state.get("release_blocked")),
        "quality_blockers": blockers,
        "metadata_summary": metadata_summary,
        "searchable_text_available": bool(text_payload.get("available")),
        "text_excerpt": _text(text_payload.get("excerpt")),
        "text_char_count": _int_or_none(text_payload.get("char_count")) or 0,
        "matched_fields": _matched_fields(fields, query),
    }
    return item


def build_library_index(
    jobs: Mapping[str, Mapping[str, Any]],
    *,
    quality_state_builder: QualityStateBuilder,
    output_size_resolver: OutputSizeResolver,
    filters: LibraryFilters,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    status_filter = _lower_text(filters.status)
    verdict_filter = _lower_text(filters.release_verdict)

    for job_id, job in jobs.items():
        status = _lower_text(job.get("status")) or "unknown"
        if status_filter and status != status_filter:
            continue

        quality_state = quality_state_builder(job_id, job)
        release_verdict = _lower_text(quality_state.get("release_verdict"))
        if verdict_filter and release_verdict != verdict_filter:
            continue

        text_payload = None
        output_path = _text(job.get("output_path"))
        if filters.include_text and output_path:
            text_payload = extract_epub_text_excerpt(output_path)

        search_terms = [
            *_metadata_terms(job, quality_state),
            *_quality_blocker_terms(quality_state),
            *_issue_terms(quality_state),
            _text(text_payload.get("excerpt")) if text_payload else "",
        ]
        search_text = _normalize_space(" ".join(term for term in search_terms if term))
        if not _matches_query(search_text, filters.query):
            continue

        item = build_library_item(
            job_id,
            job,
            quality_state=quality_state,
            output_size_bytes=output_size_resolver(job),
            text_excerpt=text_payload,
            query=filters.query,
        )
        items.append(item)

    items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    limited_items = items[: max(1, filters.limit)]
    return {
        "success": True,
        "index_version": LIBRARY_INDEX_VERSION,
        "query": filters.query,
        "filters": {
            "status": filters.status,
            "release_verdict": filters.release_verdict,
            "include_text": filters.include_text,
            "limit": filters.limit,
        },
        "items": limited_items,
        "count": len(limited_items),
        "total": len(items),
    }


def build_quality_report_payload(
    job_id: str,
    job: Mapping[str, Any],
    *,
    quality_state: Mapping[str, Any],
    output_size_bytes: int | None,
    include_text: bool = True,
) -> dict[str, Any]:
    text_payload = None
    output_path = _text(job.get("output_path"))
    if include_text and output_path:
        text_payload = extract_epub_text_excerpt(output_path)
    item = build_library_item(
        job_id,
        job,
        quality_state=quality_state,
        output_size_bytes=output_size_bytes,
        text_excerpt=text_payload,
    )
    return {
        "success": True,
        "index_version": LIBRARY_INDEX_VERSION,
        "job": item,
        "quality_state": dict(quality_state),
    }


def render_quality_report_markdown(payload: Mapping[str, Any]) -> str:
    job = _mapping(payload.get("job"))
    quality_state = _mapping(payload.get("quality_state"))
    blockers = _list(job.get("quality_blockers"))
    metadata = _mapping(job.get("metadata_summary"))
    lines = [
        f"# KindleMaster quality report: {_text(job.get('title')) or _text(job.get('job_id'))}",
        "",
        f"- Job: `{_text(job.get('job_id'))}`",
        f"- File: `{_text(job.get('filename'))}`",
        f"- Status: `{_text(job.get('status'))}`",
        f"- Release verdict: `{_text(job.get('release_verdict'))}`",
        f"- Reading verdict: `{_text(job.get('reading_verdict'))}`",
        f"- Release blocked: `{str(bool(job.get('release_blocked'))).lower()}`",
        f"- Download available: `{str(bool(job.get('download_available'))).lower()}`",
        f"- Output size bytes: `{job.get('output_size_bytes')}`",
        "",
        "## Metadata",
        "",
        f"- Title: {_text(metadata.get('title')) or 'Not reported'}",
        f"- Creator: {_text(metadata.get('creator')) or 'Not reported'}",
        f"- Language: {_text(metadata.get('language')) or 'Not reported'}",
        "",
        "## Quality blockers",
        "",
    ]
    if blockers:
        for blocker in blockers:
            payload_blocker = _mapping(blocker)
            lines.append(
                f"- `{_text(payload_blocker.get('code')) or 'unknown'}` "
                f"{_text(payload_blocker.get('message')) or 'No message'}"
            )
    else:
        lines.append("- None")
    text_excerpt = _text(job.get("text_excerpt"))
    if text_excerpt:
        lines.extend(["", "## Text excerpt", "", text_excerpt])
    lines.extend(["", "## Raw Quality State", "", "```json", json.dumps(dict(quality_state), ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)
