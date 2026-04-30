from __future__ import annotations

from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urljoin, urlparse
from zipfile import BadZipFile, ZipFile


IMAGE_EXTENSIONS = {".apng", ".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
NCX_EXTENSIONS = {".ncx"}
KINDLE_RISKY_IMAGE_EXTENSIONS = {".apng", ".avif", ".svg", ".webp"}
KINDLE_RISKY_MEDIA_EXTENSIONS = {
    ".js",
    ".mjs",
    ".mp3",
    ".mp4",
    ".m4a",
    ".mov",
    ".avi",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
} | KINDLE_RISKY_IMAGE_EXTENSIONS
DEFAULT_OVERSIZE_IMAGE_BYTES = 1_000_000
LOW_RES_IMAGE_MIN_LONG_EDGE = 600
LOW_RES_IMAGE_MIN_SHORT_EDGE = 300
KINDLE_COVER_MIN_LONG_EDGE = 1_000
KINDLE_COVER_MIN_SHORT_EDGE = 625
KINDLE_COVER_ASPECT_MIN = 0.55
KINDLE_COVER_ASPECT_MAX = 0.75
TOC_ENTRY_LIMIT = 12
ASSET_ENTRY_LIMIT = 5
IMAGE_QUALITY_ENTRY_LIMIT = 8
EPUBCHECK_MESSAGE_LIMIT = 8

PLACEHOLDER_MARKERS = {
    "",
    "unknown",
    "unknown author",
    "untitled",
    "untitled document",
    "document",
    "ebook",
    "epub",
    "python-docx",
    "emvc",
}


def build_quality_cockpit_preview(
    metadata: Mapping[str, Any] | None = None,
    *,
    quality_report: Mapping[str, Any] | None = None,
    epub_bytes: bytes | bytearray | memoryview | None = None,
    epub_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build JSON-serializable lightweight cockpit preview summaries."""
    archive = _inspect_epub_archive(epub_bytes=epub_bytes, epub_path=epub_path)
    return {
        "toc_preview": build_toc_preview(metadata, quality_report=quality_report, archive_summary=archive),
        "asset_summary": build_asset_summary(metadata, quality_report=quality_report, archive_summary=archive),
        "epubcheck_detail": build_epubcheck_detail(metadata, quality_report=quality_report),
        "metadata_summary": build_metadata_summary(metadata, quality_report=quality_report, archive_summary=archive),
    }


def build_toc_preview(
    metadata: Mapping[str, Any] | None = None,
    *,
    quality_report: Mapping[str, Any] | None = None,
    archive_summary: Mapping[str, Any] | None = None,
    epub_bytes: bytes | bytearray | memoryview | None = None,
    epub_path: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize short TOC signal from runtime metadata or a small EPUB archive scan."""
    sources = _sources(metadata, quality_report)
    archive = _ensure_archive_summary(archive_summary, epub_bytes=epub_bytes, epub_path=epub_path)
    entries = _first_entries_from_sources(sources)
    warnings = _warnings_from_sources(sources)

    if not entries:
        entries = list(archive.get("toc_entries", []) or [])
    if not warnings and archive.get("toc_warning"):
        warnings.append(str(archive["toc_warning"]))

    toc_before = _first_int(
        _deep_get(sources, "toc_before"),
        _deep_get(sources, "toc_entries_before"),
        _deep_get(sources, "heading_repair.toc_before"),
        _deep_get(sources, "heading_repair.toc_entries_before"),
    )
    toc_after = _first_int(
        _deep_get(sources, "toc_after"),
        _deep_get(sources, "toc_entries_after"),
        _deep_get(sources, "heading_repair.toc_after"),
        _deep_get(sources, "heading_repair.toc_entries_after"),
    )
    entry_count = _first_int(
        _deep_get(sources, "toc_entry_count"),
        _deep_get(sources, "entry_count"),
        _deep_get(sources, "heading_repair.toc_after"),
        _deep_get(sources, "heading_repair.toc_entries_after"),
        default=len(entries),
    )
    if entry_count == 0 and entries:
        entry_count = len(entries)

    return {
        "entry_count": entry_count,
        "entries": [_json_entry(entry) for entry in entries[:TOC_ENTRY_LIMIT]],
        "warnings": warnings[:TOC_ENTRY_LIMIT],
        "toc_before": toc_before,
        "toc_after": toc_after,
    }


def build_asset_summary(
    metadata: Mapping[str, Any] | None = None,
    *,
    quality_report: Mapping[str, Any] | None = None,
    archive_summary: Mapping[str, Any] | None = None,
    epub_bytes: bytes | bytearray | memoryview | None = None,
    epub_path: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize image-heavy EPUB signals without building a reader preview."""
    sources = _sources(metadata, quality_report)
    archive = _ensure_archive_summary(archive_summary, epub_bytes=epub_bytes, epub_path=epub_path)
    largest_assets = _first_list(
        _deep_get(sources, "largest_assets"),
        _deep_get(sources, "quality_report.largest_assets"),
        _deep_get(sources, "size_budget_inspection.largest_assets"),
        _deep_get(sources, "quality_report.size_budget_inspection.largest_assets"),
        archive.get("largest_assets"),
    )
    total_image_bytes = _first_int(
        _deep_get(sources, "total_image_bytes"),
        _deep_get(sources, "quality_report.total_image_bytes"),
        _deep_get(sources, "size_budget_inspection.total_image_bytes"),
        _deep_get(sources, "quality_report.size_budget_inspection.total_image_bytes"),
        archive.get("total_image_bytes"),
    )
    image_count = _first_int(
        _deep_get(sources, "image_count"),
        _deep_get(sources, "archive_image_count"),
        _deep_get(sources, "quality_report.image_count"),
        _deep_get(sources, "quality_report.archive_image_count"),
        _deep_get(sources, "size_budget_inspection.image_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.image_count"),
        archive.get("image_count"),
    )
    oversize_count = _first_int(
        _deep_get(sources, "oversize_count"),
        _deep_get(sources, "quality_report.oversize_count"),
        _deep_get(sources, "size_budget_inspection.oversize_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.oversize_count"),
        archive.get("oversize_count"),
    )
    duplicate_src_count = _first_int(
        _deep_get(sources, "duplicate_src_count"),
        _deep_get(sources, "quality_report.duplicate_src_count"),
        _deep_get(sources, "size_budget_inspection.duplicate_src_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.duplicate_src_count"),
        archive.get("duplicate_src_count"),
    )
    asset_budget_status = _first_text(
        _deep_get(sources, "asset_budget_status"),
        _deep_get(sources, "asset_summary.asset_budget_status"),
        _deep_get(sources, "quality_report.asset_budget_status"),
        _deep_get(sources, "size_budget_inspection.asset_budget_status"),
        _deep_get(sources, "quality_report.size_budget_inspection.asset_budget_status"),
        default="not_reported",
    )
    image_quality = _image_quality_summary(sources, archive)
    media_risk_count = _first_int(
        _deep_get(sources, "media_risk_count"),
        _deep_get(sources, "asset_summary.media_risk_count"),
        _deep_get(sources, "image_quality.media_risk_count"),
        _deep_get(sources, "asset_summary.image_quality.media_risk_count"),
        _deep_get(sources, "quality_report.image_quality.media_risk_count"),
        _deep_get(sources, "size_budget_inspection.media_risk_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.media_risk_count"),
        image_quality.get("media_risk_count"),
        archive.get("media_risk_count"),
    )

    return {
        "image_count": image_count,
        "largest_assets": [_asset_entry(item) for item in largest_assets[:ASSET_ENTRY_LIMIT]],
        "total_image_bytes": total_image_bytes,
        "diagram_chess": _diagram_chess_metrics(sources, archive),
        "oversize_count": oversize_count,
        "duplicate_src_count": duplicate_src_count,
        "asset_budget_status": asset_budget_status,
        "unsupported_media_count": _first_int(
            _deep_get(sources, "unsupported_media_count"),
            _deep_get(sources, "quality_report.unsupported_media_count"),
            _deep_get(sources, "size_budget_inspection.unsupported_media_count"),
            _deep_get(sources, "quality_report.size_budget_inspection.unsupported_media_count"),
            archive.get("unsupported_media_count"),
        ),
        "script_count": _first_int(
            _deep_get(sources, "script_count"),
            _deep_get(sources, "quality_report.script_count"),
            _deep_get(sources, "size_budget_inspection.script_count"),
            _deep_get(sources, "quality_report.size_budget_inspection.script_count"),
            archive.get("script_count"),
        ),
        "media_risk_count": media_risk_count,
        "image_quality": image_quality,
    }


def build_epubcheck_detail(
    metadata: Mapping[str, Any] | None = None,
    *,
    quality_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize EPUBCheck or validation messages from existing quality data."""
    sources = _sources(metadata, quality_report)
    payload = _first_mapping(
        _deep_get(sources, "epubcheck"),
        _deep_get(sources, "heading_repair.epubcheck"),
        _deep_get(sources, "validation.epubcheck"),
        _deep_get(sources, "quality_report.epubcheck"),
    )
    status = _first_text(
        payload.get("status"),
        _deep_get(sources, "epubcheck_status"),
        _deep_get(sources, "validation_status"),
        _deep_get(sources, "validation"),
        default="unavailable",
    )
    tool = _first_text(
        payload.get("tool"),
        payload.get("name"),
        _deep_get(sources, "validation_tool"),
        default="unknown",
    )
    messages = _normalize_epubcheck_messages(
        _first_list(payload.get("messages"), _deep_get(sources, "epubcheck_messages"), _deep_get(sources, "validation_messages"))
    )
    error_count = _first_int(
        payload.get("error_count"),
        payload.get("errors"),
        _deep_get(sources, "epubcheck_error_count"),
        default=_count_messages(messages, {"error", "fatal"}),
    )
    warning_count = _first_int(
        payload.get("warning_count"),
        payload.get("warnings"),
        _deep_get(sources, "epubcheck_warning_count"),
        default=_count_messages(messages, {"warning", "warn"}),
    )

    return {
        "status": status,
        "tool": tool,
        "error_count": error_count,
        "warning_count": warning_count,
        "messages": [item["message"] for item in messages[:EPUBCHECK_MESSAGE_LIMIT]],
    }


def build_metadata_summary(
    metadata: Mapping[str, Any] | None = None,
    *,
    quality_report: Mapping[str, Any] | None = None,
    archive_summary: Mapping[str, Any] | None = None,
    epub_bytes: bytes | bytearray | memoryview | None = None,
    epub_path: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize reader-facing metadata and obvious placeholder values."""
    sources = _sources(metadata, quality_report)
    archive = _ensure_archive_summary(archive_summary, epub_bytes=epub_bytes, epub_path=epub_path)
    opf_metadata = _mapping(archive.get("metadata"))
    title = _first_text(
        _deep_get(sources, "title"),
        _deep_get(sources, "metadata.title"),
        _deep_get(sources, "primary_metadata.title"),
        opf_metadata.get("title"),
    )
    creator = _first_text(
        _deep_get(sources, "creator"),
        _deep_get(sources, "author"),
        _deep_get(sources, "metadata.creator"),
        _deep_get(sources, "metadata.author"),
        _deep_get(sources, "primary_metadata.creator"),
        opf_metadata.get("creator"),
    )
    language = _first_text(
        _deep_get(sources, "language"),
        _deep_get(sources, "metadata.language"),
        _deep_get(sources, "primary_metadata.language"),
        opf_metadata.get("language"),
    )

    placeholders = []
    if _is_placeholder(title):
        placeholders.append("title")
    if _is_placeholder(creator):
        placeholders.append("creator")
    if _is_placeholder(language):
        placeholders.append("language")

    return {
        "title": title,
        "creator": creator,
        "language": language,
        "placeholders_detected": placeholders,
    }


class _TocHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict[str, str]] = []
        self._active_href: str | None = None
        self._active_text: list[str] = []
        self._nav_stack = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "nav" and "toc" in attrs_dict.get("epub:type", "").lower():
            self._nav_stack += 1
        if tag.lower() == "a" and (self._nav_stack or not self.entries):
            self._active_href = attrs_dict.get("href", "")
            self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href is not None:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active_href is not None:
            title = " ".join("".join(self._active_text).split())
            if title:
                self.entries.append({"title": title, "href": self._active_href})
            self._active_href = None
            self._active_text = []
        if tag.lower() == "nav" and self._nav_stack:
            self._nav_stack -= 1


class _NcxParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict[str, str]] = []
        self._in_text = False
        self._text: list[str] = []
        self._pending_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "text":
            self._in_text = True
            self._text = []
        if tag.lower() == "content":
            attrs_dict = {name.lower(): value or "" for name, value in attrs}
            self._pending_href = attrs_dict.get("src", "")

    def handle_data(self, data: str) -> None:
        if self._in_text:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "text" and self._in_text:
            title = " ".join("".join(self._text).split())
            if title:
                self.entries.append({"title": title, "href": self._pending_href})
            self._in_text = False
            self._text = []


class _ImageSrcParser(HTMLParser):
    def __init__(self, base_path: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_path = base_path
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        src = attrs_dict.get("src")
        if src:
            self.sources.append(_normalize_archive_href(self.base_path, src))


def _inspect_epub_archive(
    *,
    epub_bytes: bytes | bytearray | memoryview | None = None,
    epub_path: str | Path | None = None,
) -> dict[str, Any]:
    if epub_bytes is None and epub_path is None:
        return _empty_archive_summary()
    try:
        if epub_bytes is not None:
            with ZipFile(BytesIO(bytes(epub_bytes))) as archive:
                return _inspect_zip(archive)
        path = Path(epub_path) if epub_path is not None else None
        if path is None or not path.exists() or not path.is_file():
            summary = _empty_archive_summary()
            summary["warnings"].append("epub file missing")
            return summary
        with ZipFile(path) as archive:
            return _inspect_zip(archive)
    except (BadZipFile, OSError, ValueError):
        summary = _empty_archive_summary()
        summary["warnings"].append("epub inspection unavailable")
        return summary


def _inspect_zip(archive: ZipFile) -> dict[str, Any]:
    summary = _empty_archive_summary()
    image_entries: list[dict[str, Any]] = []
    image_details: list[dict[str, Any]] = []
    img_sources: list[str] = []
    image_infos = []
    risky_media_count = 0
    script_count = 0
    media_risks: list[dict[str, str]] = []
    cover_image_path = ""
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        suffix = Path(name).suffix.lower()
        if suffix in KINDLE_RISKY_MEDIA_EXTENSIONS:
            risky_media_count += 1
            media_risks.append({"path": name, "kind": _media_risk_kind(suffix)})
        if suffix in {".js", ".mjs"}:
            script_count += 1
        if suffix in IMAGE_EXTENSIONS:
            image_infos.append(info)
            image_entries.append({"path": name, "bytes": max(0, int(info.file_size))})
        if suffix in HTML_EXTENSIONS:
            try:
                text = archive.read(info).decode("utf-8", errors="replace")
            except (KeyError, OSError):
                continue
            src_parser = _ImageSrcParser(name)
            src_parser.feed(text)
            img_sources.extend(src_parser.sources)
        if suffix in HTML_EXTENSIONS and "nav" in Path(name).name.lower() and not summary["toc_entries"]:
            summary["toc_entries"] = _parse_toc_html(archive, name)
        if suffix in NCX_EXTENSIONS and not summary["toc_entries"]:
            summary["toc_entries"] = _parse_toc_ncx(archive, name)
        if suffix == ".opf" and not summary["metadata"]:
            opf_summary = _parse_opf_summary(archive, name)
            summary["metadata"] = dict(opf_summary.get("metadata", {}) or {})
            cover_image_path = _coerce_text(opf_summary.get("cover_image_path")) or cover_image_path

    for info in image_infos:
        name = info.filename.replace("\\", "/")
        image_details.append(_image_detail_from_archive(archive, info, is_cover=name == cover_image_path))

    image_entries.sort(key=lambda item: int(item["bytes"]), reverse=True)
    duplicate_sources = {src for src in img_sources if img_sources.count(src) > 1}
    summary.update(
        {
            "image_count": len(image_entries),
            "largest_assets": image_entries[:ASSET_ENTRY_LIMIT],
            "total_image_bytes": sum(int(item["bytes"]) for item in image_entries),
            "oversize_count": sum(1 for item in image_entries if int(item["bytes"]) >= DEFAULT_OVERSIZE_IMAGE_BYTES),
            "duplicate_src_count": len(duplicate_sources),
            "diagram_count": sum(1 for item in image_entries if "diagram" in item["path"].lower()),
            "chess_diagram_count": sum(
                1
                for item in image_entries
                if any(marker in item["path"].lower() for marker in ("chess", "board", "diagram"))
            ),
            "unsupported_media_count": risky_media_count,
            "script_count": script_count,
            "media_risk_count": len(media_risks),
            "media_risks": media_risks[:IMAGE_QUALITY_ENTRY_LIMIT],
            "image_quality": _archive_image_quality_summary(
                image_details=image_details,
                cover_image_path=cover_image_path,
                media_risks=media_risks,
            ),
        }
    )
    return summary


def _parse_toc_html(archive: ZipFile, name: str) -> list[dict[str, str]]:
    try:
        text = archive.read(name).decode("utf-8", errors="replace")
    except (KeyError, OSError):
        return []
    parser = _TocHtmlParser()
    parser.feed(text)
    base_path = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
    return [
        {"title": entry["title"], "href": _normalize_archive_href(base_path, entry.get("href", ""))}
        for entry in parser.entries
    ]


def _parse_toc_ncx(archive: ZipFile, name: str) -> list[dict[str, str]]:
    try:
        text = archive.read(name).decode("utf-8", errors="replace")
    except (KeyError, OSError):
        return []
    parser = _NcxParser()
    parser.feed(text)
    base_path = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
    return [
        {"title": entry["title"], "href": _normalize_archive_href(base_path, entry.get("href", ""))}
        for entry in parser.entries
    ]


def _parse_opf_metadata(archive: ZipFile, name: str) -> dict[str, str]:
    return dict(_parse_opf_summary(archive, name).get("metadata", {}) or {})


def _parse_opf_summary(archive: ZipFile, name: str) -> dict[str, Any]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(archive.read(name))
    except (ET.ParseError, KeyError, OSError):
        return {"metadata": {}, "cover_image_path": ""}
    metadata = {"title": "", "creator": "", "language": ""}
    manifest_items: dict[str, dict[str, str]] = {}
    cover_id = ""
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in metadata and not metadata[tag]:
            metadata[tag] = _coerce_text(element.text)
        if tag == "meta":
            attrs = {key.rsplit("}", 1)[-1].lower(): value for key, value in element.attrib.items()}
            if attrs.get("name", "").lower() == "cover" and attrs.get("content"):
                cover_id = attrs.get("content", "")
        if tag == "item":
            attrs = {key.rsplit("}", 1)[-1].lower(): value for key, value in element.attrib.items()}
            item_id = attrs.get("id", "")
            href = attrs.get("href", "")
            if item_id and href:
                manifest_items[item_id] = attrs
            properties = attrs.get("properties", "").lower().split()
            if not cover_id and "cover-image" in properties and item_id:
                cover_id = item_id
    cover_href = ""
    if cover_id and cover_id in manifest_items:
        cover_href = manifest_items[cover_id].get("href", "")
    if not cover_href:
        for attrs in manifest_items.values():
            href = attrs.get("href", "")
            if href and Path(href).suffix.lower() in IMAGE_EXTENSIONS and "cover" in href.lower():
                cover_href = href
                break
    base_path = name.rsplit("/", 1)[0] + "/" if "/" in name else ""
    return {
        "metadata": metadata,
        "cover_image_path": _normalize_archive_href(base_path, cover_href) if cover_href else "",
    }


def _media_risk_kind(suffix: str) -> str:
    if suffix in {".js", ".mjs"}:
        return "script"
    if suffix in {".mp3", ".mp4", ".m4a", ".mov", ".avi"}:
        return "audio_video"
    if suffix in {".woff", ".woff2", ".ttf", ".otf"}:
        return "embedded_font"
    if suffix in KINDLE_RISKY_IMAGE_EXTENSIONS:
        return "risky_image_format"
    return "unsupported_media"


def _image_detail_from_archive(archive: ZipFile, info: Any, *, is_cover: bool) -> dict[str, Any]:
    name = info.filename.replace("\\", "/")
    suffix = Path(name).suffix.lower()
    detail: dict[str, Any] = {
        "path": name,
        "bytes": max(0, int(info.file_size)),
        "is_cover": bool(is_cover),
    }
    try:
        data = archive.read(info)
    except (KeyError, OSError):
        data = b""
    if suffix in {".jpg", ".jpeg"} and data:
        detail["progressive_jpeg"] = _is_progressive_jpeg(data)
    dimensions = _probe_image_dimensions(data)
    if dimensions:
        detail.update(dimensions)
    return detail


def _probe_image_dimensions(data: bytes) -> dict[str, Any]:
    if not data:
        return {}
    try:
        from PIL import Image
    except Exception:
        return {}
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            return {
                "width": max(0, int(width)),
                "height": max(0, int(height)),
                "format": _coerce_text(getattr(image, "format", "")),
            }
    except Exception:
        return {}


def _is_progressive_jpeg(data: bytes) -> bool:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return False
    index = 2
    while index < len(data) - 1:
        if data[index] != 0xFF:
            index += 1
            continue
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker == 0xC2:
            return True
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2:
            break
        index += segment_length
    return False


def _archive_image_quality_summary(
    *,
    image_details: list[dict[str, Any]],
    cover_image_path: str,
    media_risks: list[dict[str, str]],
) -> dict[str, Any]:
    cover_entry = next((item for item in image_details if item.get("path") == cover_image_path), None)
    if cover_entry is None:
        cover_entry = next((item for item in image_details if "cover" in _coerce_text(item.get("path")).lower()), None)
    low_resolution_images = [item for item in image_details if _is_low_resolution_image(item)]
    progressive_jpegs = [item for item in image_details if item.get("progressive_jpeg") is True]
    inspected_image_count = sum(1 for item in image_details if _has_dimensions(item))
    cover = _cover_quality_payload(cover_entry, cover_image_path=cover_image_path)
    status = _computed_image_quality_status(
        cover_status=cover.get("status"),
        low_resolution_count=len(low_resolution_images),
        progressive_jpeg_count=len(progressive_jpegs),
        media_risk_count=len(media_risks),
        inspected_image_count=inspected_image_count,
        image_count=len(image_details),
    )
    return {
        "status": status,
        "inspected_image_count": inspected_image_count,
        "cover": cover,
        "low_resolution_count": len(low_resolution_images),
        "low_resolution_images": [_image_quality_entry(item) for item in low_resolution_images[:IMAGE_QUALITY_ENTRY_LIMIT]],
        "progressive_jpeg_count": len(progressive_jpegs),
        "progressive_jpeg_images": [_image_quality_entry(item) for item in progressive_jpegs[:IMAGE_QUALITY_ENTRY_LIMIT]],
        "media_risk_count": len(media_risks),
        "media_risks": [dict(item) for item in media_risks[:IMAGE_QUALITY_ENTRY_LIMIT]],
    }


def _empty_archive_summary() -> dict[str, Any]:
    return {
        "image_count": 0,
        "largest_assets": [],
        "total_image_bytes": 0,
        "oversize_count": 0,
        "duplicate_src_count": 0,
        "diagram_count": 0,
        "chess_diagram_count": 0,
        "unsupported_media_count": 0,
        "script_count": 0,
        "media_risk_count": 0,
        "media_risks": [],
        "image_quality": _empty_image_quality_summary(),
        "toc_entries": [],
        "toc_warning": "",
        "metadata": {},
        "warnings": [],
    }


def _ensure_archive_summary(
    archive_summary: Mapping[str, Any] | None,
    *,
    epub_bytes: bytes | bytearray | memoryview | None,
    epub_path: str | Path | None,
) -> Mapping[str, Any]:
    if isinstance(archive_summary, Mapping):
        return archive_summary
    return _inspect_epub_archive(epub_bytes=epub_bytes, epub_path=epub_path)


def _sources(*items: Mapping[str, Any] | None) -> tuple[Mapping[str, Any], ...]:
    return tuple(item for item in items if isinstance(item, Mapping))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_mapping(*values: Any) -> Mapping[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return value
    return {}


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _first_text(*values: Any, default: str = "") -> str:
    for value in values:
        text = _coerce_text(value)
        if text:
            return text
    return default


def _first_int(*values: Any, default: int = 0) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return max(0, int(default))


def _first_optional_int(*values: Any) -> int | None:
    for value in values:
        if value is None:
            continue
        try:
            converted = int(value)
        except (TypeError, ValueError):
            continue
        if converted >= 0:
            return converted
    return None


def _first_optional_float(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            converted = float(value)
        except (TypeError, ValueError):
            continue
        if converted >= 0:
            return round(converted, 3)
    return None


def _first_list(*values: Any) -> list[Any]:
    for value in values:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
    return []


def _deep_get(sources: tuple[Mapping[str, Any], ...], path: str) -> Any:
    parts = path.split(".")
    for source in sources:
        value: Any = source
        for part in parts:
            if not isinstance(value, Mapping) or part not in value:
                value = None
                break
            value = value[part]
        if value is not None:
            return value
    return None


def _first_entries_from_sources(sources: tuple[Mapping[str, Any], ...]) -> list[Any]:
    return _first_list(
        _deep_get(sources, "toc_entries"),
        _deep_get(sources, "entries"),
        _deep_get(sources, "toc_preview.entries"),
        _deep_get(sources, "toc.items"),
        _deep_get(sources, "nav.entries"),
    )


def _warnings_from_sources(sources: tuple[Mapping[str, Any], ...]) -> list[str]:
    values = _first_list(
        _deep_get(sources, "toc_warnings"),
        _deep_get(sources, "toc_preview.warnings"),
        _deep_get(sources, "heading_repair.warnings"),
    )
    return [_coerce_text(item) for item in values if _coerce_text(item)]


def _json_entry(entry: Any) -> dict[str, str]:
    if isinstance(entry, Mapping):
        return {
            "title": _first_text(entry.get("title"), entry.get("label"), entry.get("text")),
            "href": _first_text(entry.get("href"), entry.get("src"), entry.get("url")),
        }
    return {"title": _coerce_text(entry), "href": ""}


def _asset_entry(item: Any) -> dict[str, Any]:
    if isinstance(item, Mapping):
        return {
            "path": _first_text(item.get("path"), item.get("href"), item.get("name"), item.get("src")),
            "bytes": _first_int(item.get("bytes"), item.get("size"), item.get("size_bytes"), item.get("file_size")),
        }
    return {"path": _coerce_text(item), "bytes": 0}


def _image_quality_summary(sources: tuple[Mapping[str, Any], ...], archive: Mapping[str, Any]) -> dict[str, Any]:
    archive_quality = _mapping(archive.get("image_quality"))
    source_quality = _first_mapping(
        _deep_get(sources, "image_quality"),
        _deep_get(sources, "asset_summary.image_quality"),
        _deep_get(sources, "quality_report.image_quality"),
        _deep_get(sources, "quality_report.asset_summary.image_quality"),
        _deep_get(sources, "size_budget_inspection.image_quality"),
        _deep_get(sources, "quality_report.size_budget_inspection.image_quality"),
    )
    raw_cover = _first_mapping(
        source_quality.get("cover"),
        _deep_get(sources, "cover_health"),
        _deep_get(sources, "cover_image"),
        _deep_get(sources, "asset_summary.cover"),
        _deep_get(sources, "asset_summary.cover_health"),
        _deep_get(sources, "quality_report.cover_health"),
        _deep_get(sources, "quality_report.cover_image"),
        _deep_get(sources, "quality_report.asset_summary.cover"),
        _deep_get(sources, "quality_report.asset_summary.cover_health"),
        _deep_get(sources, "size_budget_inspection.cover"),
        _deep_get(sources, "size_budget_inspection.cover_image"),
        _deep_get(sources, "quality_report.size_budget_inspection.cover"),
        _deep_get(sources, "quality_report.size_budget_inspection.cover_image"),
        archive_quality.get("cover"),
    )
    progressive_jpeg_count = _first_optional_int(
        source_quality.get("progressive_jpeg_count"),
        _deep_get(sources, "progressive_jpeg_count"),
        _deep_get(sources, "asset_summary.progressive_jpeg_count"),
        _deep_get(sources, "quality_report.progressive_jpeg_count"),
        _deep_get(sources, "quality_report.asset_summary.progressive_jpeg_count"),
        _deep_get(sources, "size_budget_inspection.progressive_jpeg_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.progressive_jpeg_count"),
        archive_quality.get("progressive_jpeg_count"),
    )
    low_resolution_count = _first_int(
        source_quality.get("low_resolution_count"),
        _deep_get(sources, "low_resolution_count"),
        _deep_get(sources, "asset_summary.low_resolution_count"),
        _deep_get(sources, "quality_report.low_resolution_count"),
        _deep_get(sources, "quality_report.asset_summary.low_resolution_count"),
        _deep_get(sources, "size_budget_inspection.low_resolution_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.low_resolution_count"),
        archive_quality.get("low_resolution_count"),
    )
    media_risk_count = _first_int(
        source_quality.get("media_risk_count"),
        _deep_get(sources, "media_risk_count"),
        _deep_get(sources, "asset_summary.media_risk_count"),
        _deep_get(sources, "quality_report.media_risk_count"),
        _deep_get(sources, "quality_report.asset_summary.media_risk_count"),
        _deep_get(sources, "size_budget_inspection.media_risk_count"),
        _deep_get(sources, "quality_report.size_budget_inspection.media_risk_count"),
        archive_quality.get("media_risk_count"),
    )
    inspected_image_count = _first_int(
        source_quality.get("inspected_image_count"),
        _deep_get(sources, "inspected_image_count"),
        _deep_get(sources, "asset_summary.inspected_image_count"),
        _deep_get(sources, "quality_report.inspected_image_count"),
        _deep_get(sources, "quality_report.asset_summary.inspected_image_count"),
        archive_quality.get("inspected_image_count"),
    )
    cover = _normalize_cover_payload(raw_cover)
    status = _first_text(
        source_quality.get("status"),
        _deep_get(sources, "image_quality_status"),
        _deep_get(sources, "asset_summary.image_quality_status"),
        _deep_get(sources, "quality_report.image_quality_status"),
        _deep_get(sources, "quality_report.asset_summary.image_quality_status"),
        default=_computed_image_quality_status(
            cover_status=cover.get("status"),
            low_resolution_count=low_resolution_count,
            progressive_jpeg_count=progressive_jpeg_count or 0,
            media_risk_count=media_risk_count,
            inspected_image_count=inspected_image_count,
            image_count=_first_int(_deep_get(sources, "image_count"), archive.get("image_count")),
        ),
    )
    return {
        "status": status,
        "inspected_image_count": inspected_image_count,
        "cover": cover,
        "low_resolution_count": low_resolution_count,
        "low_resolution_images": [
            _image_quality_entry(item)
            for item in _first_list(
                source_quality.get("low_resolution_images"),
                _deep_get(sources, "low_resolution_images"),
                _deep_get(sources, "asset_summary.low_resolution_images"),
                _deep_get(sources, "quality_report.low_resolution_images"),
                _deep_get(sources, "quality_report.asset_summary.low_resolution_images"),
                archive_quality.get("low_resolution_images"),
            )[:IMAGE_QUALITY_ENTRY_LIMIT]
        ],
        "progressive_jpeg_count": progressive_jpeg_count,
        "progressive_jpeg_images": [
            _image_quality_entry(item)
            for item in _first_list(
                source_quality.get("progressive_jpeg_images"),
                _deep_get(sources, "progressive_jpeg_images"),
                _deep_get(sources, "asset_summary.progressive_jpeg_images"),
                _deep_get(sources, "quality_report.progressive_jpeg_images"),
                _deep_get(sources, "quality_report.asset_summary.progressive_jpeg_images"),
                archive_quality.get("progressive_jpeg_images"),
            )[:IMAGE_QUALITY_ENTRY_LIMIT]
        ],
        "media_risk_count": media_risk_count,
        "media_risks": [
            dict(item) if isinstance(item, Mapping) else {"path": _coerce_text(item), "kind": "media_risk"}
            for item in _first_list(
                source_quality.get("media_risks"),
                _deep_get(sources, "media_risks"),
                _deep_get(sources, "asset_summary.media_risks"),
                _deep_get(sources, "quality_report.media_risks"),
                _deep_get(sources, "quality_report.asset_summary.media_risks"),
                archive_quality.get("media_risks"),
            )[:IMAGE_QUALITY_ENTRY_LIMIT]
        ],
    }


def _empty_image_quality_summary() -> dict[str, Any]:
    return {
        "status": "not_reported",
        "inspected_image_count": 0,
        "cover": _empty_cover_quality(),
        "low_resolution_count": 0,
        "low_resolution_images": [],
        "progressive_jpeg_count": None,
        "progressive_jpeg_images": [],
        "media_risk_count": 0,
        "media_risks": [],
    }


def _empty_cover_quality() -> dict[str, Any]:
    return {
        "status": "not_reported",
        "path": "",
        "width": None,
        "height": None,
        "aspect_ratio": None,
        "bytes": None,
        "issues": [],
    }


def _normalize_cover_payload(value: Any) -> dict[str, Any]:
    payload = _mapping(value)
    if not payload:
        return _empty_cover_quality()
    normalized = {
        "status": _first_text(payload.get("status"), payload.get("health"), default="not_reported"),
        "path": _first_text(payload.get("path"), payload.get("href"), payload.get("name"), payload.get("src")),
        "width": _first_optional_int(payload.get("width"), payload.get("pixel_width")),
        "height": _first_optional_int(payload.get("height"), payload.get("pixel_height")),
        "aspect_ratio": _first_optional_float(payload.get("aspect_ratio"), payload.get("ratio")),
        "bytes": _first_optional_int(payload.get("bytes"), payload.get("size"), payload.get("size_bytes"), payload.get("file_size")),
        "issues": [
            _coerce_text(item)
            for item in _first_list(payload.get("issues"), payload.get("warnings"), payload.get("flags"))
            if _coerce_text(item)
        ][:IMAGE_QUALITY_ENTRY_LIMIT],
    }
    if normalized["status"] == "not_reported" and any(
        normalized.get(key) not in ("", None) for key in ("path", "width", "height", "aspect_ratio", "bytes")
    ):
        normalized["status"] = "reported"
    return normalized


def _cover_quality_payload(cover_entry: Mapping[str, Any] | None, *, cover_image_path: str) -> dict[str, Any]:
    if not cover_entry:
        cover = _empty_cover_quality()
        cover["path"] = cover_image_path
        return cover
    normalized = _normalize_cover_payload(cover_entry)
    width = normalized.get("width")
    height = normalized.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        normalized["status"] = "not_reported"
        return normalized
    aspect_ratio = round(width / height, 3)
    normalized["aspect_ratio"] = aspect_ratio
    issues: list[str] = []
    if width >= height:
        issues.append("cover_not_portrait")
    if aspect_ratio < KINDLE_COVER_ASPECT_MIN or aspect_ratio > KINDLE_COVER_ASPECT_MAX:
        issues.append("cover_aspect_ratio")
    if max(width, height) < KINDLE_COVER_MIN_LONG_EDGE or min(width, height) < KINDLE_COVER_MIN_SHORT_EDGE:
        issues.append("cover_resolution")
    normalized["issues"] = issues
    normalized["status"] = "failed" if issues else "passed"
    return normalized


def _has_dimensions(item: Mapping[str, Any]) -> bool:
    width = _first_optional_int(item.get("width"))
    height = _first_optional_int(item.get("height"))
    return bool(width and height)


def _is_low_resolution_image(item: Mapping[str, Any]) -> bool:
    width = _first_optional_int(item.get("width"))
    height = _first_optional_int(item.get("height"))
    if not width or not height:
        return False
    return max(width, height) < LOW_RES_IMAGE_MIN_LONG_EDGE or min(width, height) < LOW_RES_IMAGE_MIN_SHORT_EDGE


def _image_quality_entry(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        return {"path": _coerce_text(item), "width": None, "height": None, "bytes": None}
    return {
        "path": _first_text(item.get("path"), item.get("href"), item.get("name"), item.get("src")),
        "width": _first_optional_int(item.get("width"), item.get("pixel_width")),
        "height": _first_optional_int(item.get("height"), item.get("pixel_height")),
        "bytes": _first_optional_int(item.get("bytes"), item.get("size"), item.get("size_bytes"), item.get("file_size")),
    }


def _computed_image_quality_status(
    *,
    cover_status: Any,
    low_resolution_count: int,
    progressive_jpeg_count: int,
    media_risk_count: int,
    inspected_image_count: int,
    image_count: int,
) -> str:
    normalized_cover_status = _coerce_text(cover_status).lower()
    if normalized_cover_status == "failed" or low_resolution_count > 0 or media_risk_count > 0:
        return "failed"
    if normalized_cover_status in {"warning", "warnings", "passed_with_warnings"} or progressive_jpeg_count > 0:
        return "passed_with_warnings"
    if inspected_image_count > 0 or image_count > 0 or normalized_cover_status in {"passed", "reported"}:
        return "passed"
    return "not_reported"


def _diagram_chess_metrics(sources: tuple[Mapping[str, Any], ...], archive: Mapping[str, Any]) -> dict[str, int]:
    return {
        "diagram_count": _first_int(
            _deep_get(sources, "diagram_count"),
            _deep_get(sources, "diagrams"),
            _deep_get(sources, "diagram_metrics.diagram_count"),
            archive.get("diagram_count"),
        ),
        "chess_diagram_count": _first_int(
            _deep_get(sources, "chess_diagram_count"),
            _deep_get(sources, "chess_diagrams"),
            _deep_get(sources, "diagram_metrics.chess_diagram_count"),
            archive.get("chess_diagram_count"),
        ),
    }


def _normalize_epubcheck_messages(messages: list[Any]) -> list[dict[str, str]]:
    normalized = []
    for item in messages:
        if isinstance(item, Mapping):
            message = _first_text(item.get("message"), item.get("detail"), item.get("text"), item.get("description"))
            severity = _first_text(item.get("severity"), item.get("type"), item.get("level")).lower()
            if message:
                normalized.append({"severity": severity, "message": message})
        else:
            text = _coerce_text(item)
            if text:
                severity = "error" if text.lower().startswith(("error", "fatal")) else "warning" if text.lower().startswith("warn") else ""
                normalized.append({"severity": severity, "message": text})
    return normalized


def _count_messages(messages: list[dict[str, str]], severities: set[str]) -> int:
    return sum(1 for item in messages if item.get("severity", "").lower() in severities)


def _is_placeholder(value: str) -> bool:
    normalized = " ".join(_coerce_text(value).lower().split())
    return normalized in PLACEHOLDER_MARKERS


def _normalize_archive_href(base_path: str, href: str) -> str:
    parsed = urlparse(href)
    if parsed.scheme or href.startswith("#"):
        return href
    joined = urljoin(base_path, href)
    return unquote(joined).split("?", 1)[0]


__all__ = [
    "build_asset_summary",
    "build_epubcheck_detail",
    "build_metadata_summary",
    "build_quality_cockpit_preview",
    "build_toc_preview",
]
