"""Shared low-level EPUB package operations.

These helpers intentionally stay close to ZIP/container/OPF mechanics. Higher
level semantic cleanup, metadata inference, and repair policy live in their
own modules.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

from lxml import etree

OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
NS = {
    "opf": OPF_NS,
    "dc": DC_NS,
    "container": CONTAINER_NS,
}


def _extract_epub(epub_bytes: bytes, root_dir: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
        archive.extractall(root_dir)


def _locate_opf(root_dir: Path) -> Path:
    container_path = root_dir / "META-INF" / "container.xml"
    container_root = etree.parse(str(container_path)).getroot()
    rootfile = container_root.find(".//container:rootfile", NS)
    if rootfile is None:
        raise RuntimeError("EPUB container.xml does not define rootfile")
    full_path = rootfile.get("full-path")
    if not full_path:
        raise RuntimeError("EPUB rootfile path missing")
    return root_dir / full_path


def _get_spine_xhtml_paths(opf_path: Path) -> list[Path]:
    root = etree.parse(str(opf_path)).getroot()
    manifest_by_id = {}
    for item in root.findall(".//opf:manifest/opf:item", NS):
        manifest_by_id[item.get("id")] = item

    ordered_paths: list[Path] = []
    for itemref in root.findall(".//opf:spine/opf:itemref", NS):
        manifest_item = manifest_by_id.get(itemref.get("idref"))
        if manifest_item is None:
            continue
        href = manifest_item.get("href") or ""
        media_type = manifest_item.get("media-type") or ""
        if media_type != "application/xhtml+xml":
            continue
        if href.endswith("nav.xhtml"):
            continue
        ordered_paths.append((opf_path.parent / href).resolve())
    return ordered_paths


def _snapshot_package_metadata(
    opf_path: Path,
    *,
    normalize_text: Callable[[str], str] | None = None,
) -> dict[str, object]:
    normalize = normalize_text or _normalize_metadata_text
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(str(opf_path), parser)
    root = tree.getroot()
    metadata = root.find(".//opf:metadata", NS)
    if metadata is None:
        return {
            "title": "",
            "creator": "",
            "description": "",
            "language": "",
            "identifier": "",
            "modified": "",
            "counts": {
                "title": 0,
                "creator": 0,
                "description": 0,
                "language": 0,
                "identifier": 0,
                "modified": 0,
            },
            "title_values": [],
            "creator_values": [],
            "description_values": [],
            "language_values": [],
            "identifier_values": [],
        }

    def values(local_name: str) -> list[str]:
        return [
            normalize(element.text or "")
            for element in metadata.findall(f"dc:{local_name}", NS)
            if normalize(element.text or "")
        ]

    title_values = values("title")
    creator_values = values("creator")
    description_values = values("description")
    language_values = values("language")
    identifier_values = values("identifier")
    modified_values = [
        normalize(meta.text or "")
        for meta in metadata.findall(f"{{{OPF_NS}}}meta")
        if meta.get("property") == "dcterms:modified" and normalize(meta.text or "")
    ]

    unique_identifier_id = root.get("unique-identifier", "")
    resolved_identifier = ""
    if unique_identifier_id:
        resolved_identifier = next(
            (
                normalize(element.text or "")
                for element in metadata.findall("dc:identifier", NS)
                if element.get("id") == unique_identifier_id and normalize(element.text or "")
            ),
            "",
        )
    if not resolved_identifier:
        resolved_identifier = identifier_values[0] if identifier_values else ""

    return {
        "title": title_values[0] if title_values else "",
        "creator": creator_values[0] if creator_values else "",
        "description": description_values[0] if description_values else "",
        "language": language_values[0] if language_values else "",
        "identifier": resolved_identifier,
        "modified": modified_values[0] if modified_values else "",
        "counts": {
            "title": len(title_values),
            "creator": len(creator_values),
            "description": len(description_values),
            "language": len(language_values),
            "identifier": len(identifier_values),
            "modified": len(modified_values),
        },
        "title_values": title_values,
        "creator_values": creator_values,
        "description_values": description_values,
        "language_values": language_values,
        "identifier_values": identifier_values,
        "modified_values": modified_values,
    }


def _pack_epub(root_dir: Path) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        mimetype_path = root_dir / "mimetype"
        if mimetype_path.exists():
            archive.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        for file_path in sorted(root_dir.rglob("*")):
            if file_path.is_dir() or file_path == mimetype_path:
                continue
            archive.write(
                file_path,
                file_path.relative_to(root_dir).as_posix(),
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return output.getvalue()


def _normalize_metadata_text(text: str) -> str:
    normalized = html.unescape(text or "")
    normalized = normalized.replace("\u00ad", "")
    normalized = normalized.replace("\xa0", " ")
    return re.sub(r"\s+", " ", normalized).strip()

