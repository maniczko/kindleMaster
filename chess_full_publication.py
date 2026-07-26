from __future__ import annotations

import hashlib
import html
import io
import json
import os
import posixpath
import re
import shutil
import time
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from chess_exercise_index import build_source_exercise_index
from chess_source_notation import (
    assign_source_exercise_labels_to_diagrams,
    extract_source_notation_pages,
    replay_source_notation_blocks,
)


PUBLICATION_SCHEMA = "kindlemaster.chess_full_publication.v1"
FINAL_READER_ARTIFACT_TYPE = "final_pdf_two_crop_reader"
_XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_OPF_NAMESPACE = "http://www.idpf.org/2007/opf"
_CONTAINER_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"
_GENERATED_FEN_CLASS = "kindlemaster-generated-fen"
_GENERATED_CONTENT_CLASS = "kindlemaster-generated-content"


class FullChessPublicationError(ValueError):
    pass


def publish_full_chess_publication(
    *,
    source_epub: str | Path,
    output_epub: str | Path,
    reader_dir: str | Path,
    verified_records: Sequence[Mapping[str, Any]],
    artifact_root: str | Path,
    accepted_pgn_path: str | Path | None = None,
) -> dict[str, Any]:
    """Enrich a complete EPUB without replacing its prose or source notation."""

    source = Path(source_epub).resolve()
    output = Path(output_epub).resolve()
    reader = Path(reader_dir).resolve()
    root = Path(artifact_root).resolve()
    if not source.is_file():
        raise FullChessPublicationError("source_epub_missing")
    if not _is_path_under(output, root) or not _is_path_under(reader, root):
        raise FullChessPublicationError("publication_target_outside_artifact")

    with zipfile.ZipFile(source, "r") as archive:
        member_order: list[str] = []
        member_by_name: dict[str, zipfile.ZipInfo] = {}
        for member in archive.infolist():
            if member.filename not in member_by_name:
                member_order.append(member.filename)
            member_by_name[member.filename] = member
        members = [member_by_name[name] for name in member_order]
        member_bytes = {
            name: archive.read(member_by_name[name])
            for name in member_order
        }
    if member_bytes.get("mimetype") != b"application/epub+zip":
        raise FullChessPublicationError("source_epub_mimetype_invalid")

    package_path = _package_path(member_bytes)
    spine_documents, manifest = _spine_documents(member_bytes, package_path)
    if not spine_documents:
        raise FullChessPublicationError("source_epub_spine_empty")

    source_text = _publication_text(spine_documents, member_bytes)
    if not source_text.strip():
        raise FullChessPublicationError("source_epub_text_empty")

    records = [dict(record) for record in verified_records if isinstance(record, Mapping)]
    record_by_diagram = {
        str(record.get("diagram_id") or record.get("id") or "").strip(): record
        for record in records
        if str(record.get("diagram_id") or record.get("id") or "").strip()
    }
    record_by_image = {
        image_name: record
        for record in records
        for image_name in [_source_diagram_image_name(record)]
        if image_name
    }
    if len(record_by_diagram) != len(
        [record for record in records if str(record.get("diagram_id") or record.get("id") or "").strip()]
    ):
        raise FullChessPublicationError("verified_diagram_ids_duplicate")

    generated_assets: dict[str, bytes] = {}
    generated_manifest_items: list[tuple[str, str, str]] = []
    modified_documents: dict[str, bytes] = {}
    matched_ids: set[str] = set()
    replacement_count = 0
    source_crop_count = 0
    full_fen_count = 0
    placement_count = 0

    ET.register_namespace("", _XHTML_NAMESPACE)
    ET.register_namespace("epub", "http://www.idpf.org/2007/ops")
    for document_path in spine_documents:
        payload = member_bytes.get(document_path)
        if payload is None:
            raise FullChessPublicationError(f"source_spine_document_missing:{document_path}")
        try:
            document_root = ET.fromstring(payload)
        except ET.ParseError as error:
            raise FullChessPublicationError(
                f"source_spine_document_invalid:{document_path}"
            ) from error
        parent_by_child = {
            child: parent for parent in document_root.iter() for child in list(parent)
        }
        changed = False
        for image in document_root.iter():
            if _local_name(image.tag) != "img":
                continue
            parent = parent_by_child.get(image)
            classes = {
                token
                for node in (image, parent)
                if node is not None
                for token in str(node.attrib.get("class") or "").split()
            }
            diagram_id = str(
                image.attrib.get("data-diagram-id")
                or (parent.attrib.get("data-diagram-id") if parent is not None else "")
                or ""
            ).strip()
            image_name = PurePosixPath(
                str(image.attrib.get("src") or "").split("?", 1)[0].split("#", 1)[0]
            ).name
            record = record_by_diagram.get(diagram_id) or record_by_image.get(image_name)
            if record is None or (
                "chess-diagram" not in classes
                and not image_name.startswith(("notation_layout_", "scan_chess_", "chess_p"))
            ):
                continue
            record_id = str(record.get("diagram_id") or record.get("id") or "").strip()
            if not record_id or record_id in matched_ids:
                continue
            matched_ids.add(record_id)
            image.attrib["data-diagram-id"] = record_id
            image.attrib["data-source-page"] = str(_diagram_page(record))
            if parent is not None:
                parent.attrib["data-diagram-id"] = record_id
                parent.attrib["data-source-page"] = str(_diagram_page(record))

            human_fen = record.get("fen_human_verified") is True
            human_placement = record.get("placement_human_verified") is True
            if not human_fen and not human_placement:
                if _record_is_confirmed_diagram(record):
                    image.attrib["data-fen-source"] = "unreadable_source_image"
                    image.attrib["data-fen-status"] = "unreadable"
                    source_crop_count += 1
                changed = True
                continue
            package_asset_href, package_asset_path, asset_payload, media_type = (
                _record_publication_asset(
                    record,
                    root=root,
                    package_path=package_path,
                    existing_assets=member_bytes,
                )
            )
            package_asset_path = _join_package_path(package_path, package_asset_href)
            generated_assets[package_asset_path] = asset_payload
            generated_manifest_items.append(
                (
                    f"verified-fen-{len(generated_manifest_items) + 1}",
                    package_asset_href,
                    media_type,
                )
            )
            image.attrib["src"] = posixpath.relpath(
                package_asset_path,
                posixpath.dirname(document_path) or ".",
            )
            image.attrib["data-fen-source"] = (
                "human_verified" if human_fen else "human_verified_placement"
            )
            side = str(record.get("side_to_move") or "").strip().lower()
            if side in {"w", "b"}:
                image.attrib["data-side-to-move"] = side
            replacement_count += 1
            if human_fen:
                full_fen = str(record.get("full_fen") or "").strip()
                if not full_fen:
                    raise FullChessPublicationError(f"verified_fen_missing:{record_id}")
                image.attrib["data-fen"] = full_fen
                image.attrib["data-fen-confidence"] = "1.000"
                full_fen_count += 1
                _append_fen_note(parent if parent is not None else image, full_fen)
            else:
                image.attrib["data-placement-status"] = "human_verified"
                placement_count += 1
            changed = True

        records_by_page: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            if not _record_is_confirmed_diagram(record):
                continue
            records_by_page.setdefault(_diagram_page(record), []).append(record)
        for page_records in records_by_page.values():
            page_records.sort(key=_record_source_order)

        for page_node in list(document_root.iter()):
            if _local_name(page_node.tag) not in {"pre", "div", "section"}:
                continue
            try:
                source_page = int(page_node.attrib.get("data-page") or 0)
            except (TypeError, ValueError):
                continue
            missing_page_records = [
                record
                for record in records_by_page.get(source_page, [])
                if str(record.get("diagram_id") or record.get("id") or "").strip()
                not in matched_ids
            ]
            if not missing_page_records:
                continue
            parent = parent_by_child.get(page_node)
            if parent is None:
                continue
            namespace = _namespace(parent.tag) or _XHTML_NAMESPACE
            group = ET.Element(
                f"{{{namespace}}}section",
                {
                    "class": (
                        "chess-diagrams-section "
                        f"{_GENERATED_CONTENT_CLASS}"
                    ),
                    "data-source-page": str(source_page),
                },
            )
            for record in missing_page_records:
                record_id = str(
                    record.get("diagram_id") or record.get("id") or ""
                ).strip()
                (
                    package_asset_href,
                    package_asset_path,
                    asset_payload,
                    media_type,
                ) = _record_publication_asset(
                    record,
                    root=root,
                    package_path=package_path,
                    existing_assets=member_bytes,
                )
                generated_assets[package_asset_path] = asset_payload
                generated_manifest_items.append(
                    (
                        f"chess-diagram-{len(generated_manifest_items) + 1}",
                        package_asset_href,
                        media_type,
                    )
                )
                figure = ET.SubElement(
                    group,
                    f"{{{namespace}}}figure",
                    {
                        "class": "figure chess-diagram-container",
                        "data-diagram-id": record_id,
                        "data-source-page": str(source_page),
                    },
                )
                image = ET.SubElement(
                    figure,
                    f"{{{namespace}}}img",
                    {
                        "class": "chess-diagram",
                        "src": posixpath.relpath(
                            package_asset_path,
                            posixpath.dirname(document_path) or ".",
                        ),
                        "alt": f"Chess diagram from source page {source_page}.",
                        "data-diagram-id": record_id,
                        "data-source-page": str(source_page),
                    },
                )
                if record.get("fen_human_verified") is True:
                    full_fen = str(record.get("full_fen") or "").strip()
                    if not full_fen:
                        raise FullChessPublicationError(
                            f"verified_fen_missing:{record_id}"
                        )
                    image.attrib["data-fen-source"] = "human_verified"
                    image.attrib["data-fen"] = full_fen
                    image.attrib["data-fen-confidence"] = "1.000"
                    full_fen_count += 1
                    replacement_count += 1
                    _append_fen_note(figure, full_fen)
                elif record.get("placement_human_verified") is True:
                    image.attrib["data-fen-source"] = "human_verified_placement"
                    image.attrib["data-placement-status"] = "human_verified"
                    placement_count += 1
                    replacement_count += 1
                else:
                    image.attrib["data-fen-source"] = "unreadable_source_crop"
                    image.attrib["data-fen-status"] = "unreadable"
                    source_crop_count += 1
                side = str(record.get("side_to_move") or "").strip().lower()
                if side in {"w", "b"}:
                    image.attrib["data-side-to-move"] = side
                matched_ids.add(record_id)
            children = list(parent)
            parent.insert(children.index(page_node) + 1, group)
            changed = True

        if changed:
            modified_documents[document_path] = ET.tostring(
                document_root,
                encoding="utf-8",
                xml_declaration=True,
            )

    expected_publishable = {
        str(record.get("diagram_id") or record.get("id") or "").strip()
        for record in records
        if _record_is_confirmed_diagram(record)
    }
    missing_publishable = sorted(expected_publishable - matched_ids)
    if missing_publishable:
        raise FullChessPublicationError(
            f"verified_diagram_mapping_incomplete:{len(missing_publishable)}"
        )

    accepted_pgn = _validated_pgn_payload(accepted_pgn_path)
    if accepted_pgn:
        pgn_href = "supplements/chess_games.pgn"
        generated_assets[_join_package_path(package_path, pgn_href)] = accepted_pgn
        generated_manifest_items.append(
            ("kindlemaster-accepted-pgn", pgn_href, "application/x-chess-pgn")
        )
    package_payload = _append_manifest_items(
        member_bytes[package_path],
        generated_manifest_items,
    )

    final_member_bytes = dict(member_bytes)
    final_member_bytes.update(modified_documents)
    final_member_bytes.update(generated_assets)
    final_member_bytes[package_path] = package_payload
    preserved_text = _publication_text(spine_documents, final_member_bytes)
    if _normalized_text(source_text) != _normalized_text(preserved_text):
        raise FullChessPublicationError("source_publication_text_changed")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with zipfile.ZipFile(temporary, "w") as target:
        target.writestr(
            "mimetype",
            member_bytes["mimetype"],
            compress_type=zipfile.ZIP_STORED,
        )
        replaced_generated_assets: set[str] = set()
        for member in members:
            if member.filename == "mimetype":
                continue
            payload = (
                package_payload
                if member.filename == package_path
                else generated_assets.get(
                    member.filename,
                    modified_documents.get(member.filename, member_bytes[member.filename]),
                )
            )
            target.writestr(member, payload)
            if member.filename in generated_assets:
                replaced_generated_assets.add(member.filename)
        for path, payload in sorted(generated_assets.items()):
            if path in replaced_generated_assets:
                continue
            target.writestr(path, payload, compress_type=zipfile.ZIP_DEFLATED)
    os.replace(temporary, output)

    reader_summary = _build_full_reader(
        reader,
        title=_package_title(package_payload),
        spine_documents=spine_documents,
        member_bytes=final_member_bytes,
        artifact_root=root,
        verified_records=records,
        accepted_pgn_payload=accepted_pgn,
        full_fen_count=full_fen_count,
        placement_count=placement_count,
        source_diagram_count=source_crop_count,
    )
    report = {
        "schema": PUBLICATION_SCHEMA,
        "status": "published",
        "source_epub": str(source),
        "output_epub": str(output),
        "reader": str(reader / "index.html"),
        "summary": {
            "source_member_count": len(member_bytes),
            "output_member_count": len(final_member_bytes),
            "spine_document_count": len(spine_documents),
            "source_text_characters": len(source_text),
            "source_text_sha256": hashlib.sha256(
                _normalized_text(source_text).encode("utf-8")
            ).hexdigest(),
            "confirmed_diagram_matches": len(expected_publishable & matched_ids),
            "verified_diagram_replacements": replacement_count,
            "unreadable_source_diagram_crops": source_crop_count,
            "diagrams_total": len(expected_publishable),
            "fen_human_verified": full_fen_count,
            "fen_placement_verified": placement_count,
            "accepted_pgn": _pgn_game_count(accepted_pgn),
            "source_text_preserved": True,
            **reader_summary,
        },
        "policy": (
            "The complete source EPUB is preserved. Verified boards enrich matched diagram "
            "images; source prose and notation are never replaced. Only parser-approved PGN "
            "is embedded as a downloadable supplement."
        ),
    }
    report_path = root / "report" / "chess_full_publication.json"
    _atomic_write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def _package_path(member_bytes: Mapping[str, bytes]) -> str:
    try:
        root = ET.fromstring(member_bytes["META-INF/container.xml"])
    except (KeyError, ET.ParseError) as error:
        raise FullChessPublicationError("epub_container_invalid") from error
    node = root.find(f".//{{{_CONTAINER_NAMESPACE}}}rootfile")
    path = str(node.attrib.get("full-path") if node is not None else "").strip()
    if not path or path not in member_bytes:
        raise FullChessPublicationError("epub_package_missing")
    return path


def _spine_documents(
    member_bytes: Mapping[str, bytes],
    package_path: str,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    try:
        package = ET.fromstring(member_bytes[package_path])
    except ET.ParseError as error:
        raise FullChessPublicationError("epub_package_invalid") from error
    manifest = {
        str(item.attrib.get("id") or ""): dict(item.attrib)
        for item in package.findall(f".//{{{_OPF_NAMESPACE}}}manifest/{{{_OPF_NAMESPACE}}}item")
    }
    documents: list[str] = []
    for itemref in package.findall(f".//{{{_OPF_NAMESPACE}}}spine/{{{_OPF_NAMESPACE}}}itemref"):
        item = manifest.get(str(itemref.attrib.get("idref") or ""))
        if not item:
            continue
        href = str(item.get("href") or "")
        path = _join_package_path(package_path, href)
        if path in member_bytes and str(item.get("media-type") or "") in {
            "application/xhtml+xml",
            "text/html",
        }:
            documents.append(path)
    return documents, manifest


def _publication_text(
    documents: Sequence[str],
    member_bytes: Mapping[str, bytes],
) -> str:
    chunks: list[str] = []

    def collect(node: ET.Element, *, excluded: bool = False) -> None:
        classes = set(str(node.attrib.get("class") or "").split())
        node_excluded = excluded or bool(
            classes & {_GENERATED_FEN_CLASS, _GENERATED_CONTENT_CLASS}
        )
        if not node_excluded and node.text:
            chunks.append(node.text)
        for child in list(node):
            collect(child, excluded=node_excluded)
            if not node_excluded and child.tail:
                chunks.append(child.tail)

    for document_path in documents:
        try:
            root = ET.fromstring(member_bytes[document_path])
        except (KeyError, ET.ParseError):
            continue
        collect(root)
    return "\n".join(chunks)


def _append_fen_note(container: ET.Element, fen: str) -> None:
    for child in list(container):
        if _GENERATED_FEN_CLASS in str(child.attrib.get("class") or "").split():
            child.text = f"Human verified FEN: {fen}"
            return
    namespace = _namespace(container.tag) or _XHTML_NAMESPACE
    note = ET.Element(
        f"{{{namespace}}}p",
        {"class": f"diagram-fen {_GENERATED_FEN_CLASS}"},
    )
    note.text = f"Human verified FEN: {fen}"
    container.append(note)


def _append_manifest_items(
    package_payload: bytes,
    items: Sequence[tuple[str, str, str]],
) -> bytes:
    text = _deduplicate_manifest_item_hrefs(package_payload.decode("utf-8"))
    if not items:
        return text.encode("utf-8")
    existing_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', text))
    existing_hrefs = set(re.findall(r'\bhref=["\']([^"\']+)["\']', text))
    fragments: list[str] = []
    for raw_id, href, media_type in items:
        if href in existing_hrefs:
            continue
        item_id = raw_id
        suffix = 2
        while item_id in existing_ids:
            item_id = f"{raw_id}-{suffix}"
            suffix += 1
        existing_ids.add(item_id)
        existing_hrefs.add(href)
        fragments.append(
            f'<item id="{html.escape(item_id, quote=True)}" '
            f'href="{html.escape(href, quote=True)}" '
            f'media-type="{html.escape(media_type, quote=True)}"/>'
        )
    if not fragments:
        return text.encode("utf-8")
    updated, count = re.subn(
        r"</(?P<prefix>[A-Za-z0-9_-]+:)?manifest\s*>",
        lambda match: "".join(fragments)
        + f"</{match.group('prefix') or ''}manifest>",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    if count != 1:
        raise FullChessPublicationError("epub_manifest_missing")
    return updated.encode("utf-8")


def _deduplicate_manifest_item_hrefs(text: str) -> str:
    manifest_match = re.search(
        r"<(?:(?P<prefix>[A-Za-z0-9_-]+):)?manifest\b[^>]*>(?P<body>.*?)"
        r"</(?:(?P=prefix):)?manifest\s*>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if manifest_match is None:
        raise FullChessPublicationError("epub_manifest_missing")

    body = manifest_match.group("body")
    item_pattern = re.compile(
        r"<(?:[A-Za-z0-9_-]+:)?item\b[^>]*?/?>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    href_pattern = re.compile(
        r"\bhref\s*=\s*[\"']([^\"']+)[\"']",
        flags=re.IGNORECASE,
    )
    seen_hrefs: set[str] = set()
    chunks: list[str] = []
    cursor = 0
    for item_match in item_pattern.finditer(body):
        chunks.append(body[cursor:item_match.start()])
        item = item_match.group(0)
        href_match = href_pattern.search(item)
        href = href_match.group(1) if href_match is not None else ""
        if not href or href not in seen_hrefs:
            chunks.append(item)
            if href:
                seen_hrefs.add(href)
        cursor = item_match.end()
    chunks.append(body[cursor:])
    deduplicated_body = "".join(chunks)
    return (
        text[:manifest_match.start("body")]
        + deduplicated_body
        + text[manifest_match.end("body"):]
    )


def _build_full_reader(
    reader_dir: Path,
    *,
    title: str,
    spine_documents: Sequence[str],
    member_bytes: Mapping[str, bytes],
    artifact_root: Path,
    verified_records: Sequence[Mapping[str, Any]],
    accepted_pgn_payload: bytes,
    full_fen_count: int,
    placement_count: int,
    source_diagram_count: int,
) -> dict[str, Any]:
    staging = reader_dir.with_name(reader_dir.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "epub").mkdir(parents=True, exist_ok=True)
    (staging / "data").mkdir(parents=True, exist_ok=True)
    (staging / "reports").mkdir(parents=True, exist_ok=True)
    for member_path, payload in member_bytes.items():
        if member_path.endswith("/"):
            continue
        safe = _safe_member_path(member_path)
        target = staging / "epub" / Path(*safe.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)

    diagram_assets = _reader_diagram_assets(
        spine_documents=spine_documents,
        member_bytes=member_bytes,
    )
    diagram_records_by_page: dict[int, list[Mapping[str, Any]]] = {}
    diagram_records_by_id: dict[str, Mapping[str, Any]] = {}
    for record in verified_records:
        if _record_is_confirmed_diagram(record):
            diagram_records_by_page.setdefault(_diagram_page(record), []).append(record)
            diagram_id = str(
                record.get("diagram_id") or record.get("id") or ""
            ).strip()
            if diagram_id:
                diagram_records_by_id[diagram_id] = record

    sections: list[str] = []
    notation_cards: list[str] = []
    notation_seen: set[str] = set()
    notation_blocker_count = 0
    notation_question_mark_count = 0
    source_decoded_notation_fragments = 0
    source_notation_review_fragments = 0
    source_solution_block_count = 0
    source_solution_replay_accepted = 0
    source_accepted_pgn_records: list[str] = []
    source_notation_audit_rows: list[dict[str, Any]] = []
    source_notation_pages = _reader_source_notation_pages(
        artifact_root=artifact_root,
        spine_documents=spine_documents,
        member_bytes=member_bytes,
        diagram_records=verified_records,
    )
    source_notation_page_rows = source_notation_pages.get("pages", {})
    exercise_index = dict(
        source_notation_pages.get("exercise_index") or {}
    )
    exercise_index_summary = dict(exercise_index.get("summary") or {})
    source_notation_consumed_pages: set[int] = set()
    visible_chars = 0
    spine_index = {
        document_path: index
        for index, document_path in enumerate(spine_documents, start=1)
    }
    for index, document_path in enumerate(spine_documents, start=1):
        soup = BeautifulSoup(
            member_bytes[document_path].decode("utf-8", errors="replace"),
            "html.parser",
        )
        for script in soup.select("script"):
            script.decompose()
        body = soup.body
        if body is None:
            continue
        id_prefix = f"doc-{index:03d}-"
        for node in body.select("[id]"):
            node_id = str(node.get("id") or "").strip()
            if node_id:
                node["id"] = f"{id_prefix}{node_id}"
        for node in body.select("[src], [href]"):
            for attribute in ("src", "href"):
                value = str(node.get(attribute) or "").strip()
                if not value or value.startswith(("data:", "http://", "https://", "mailto:")):
                    continue
                if attribute == "href" and value.startswith("#"):
                    node[attribute] = f"#{id_prefix}{value[1:]}"
                    continue
                raw_path, separator, fragment = value.partition("#")
                target_path = posixpath.normpath(
                    posixpath.join(posixpath.dirname(document_path), raw_path)
                )
                target_spine_index = spine_index.get(target_path)
                if attribute == "href" and target_spine_index is not None:
                    node[attribute] = (
                        f"#doc-{target_spine_index:03d}-{fragment}"
                        if separator and fragment
                        else f"#spine-{target_spine_index:03d}"
                    )
                    continue
                node[attribute] = (
                    f"epub/{target_path}" + (f"#{fragment}" if separator else "")
                )
        section_text = body.get_text(" ", strip=True)
        visible_chars += len(section_text)
        for notation_index, node in enumerate(
            body.select(
                ".chess-notation-page, .chess-notation-text, .notation-heavy"
            ),
            start=1,
        ):
            if node.find_parent(
                class_=lambda value: value
                and any(
                    token in {
                        "chess-notation-page",
                        "chess-notation-text",
                        "notation-heavy",
                    }
                    for token in str(value).split()
                )
            ):
                continue
            page_number = _notation_page_number(node)
            for fragment_index, notation_text in enumerate(
                _notation_text_candidates(node),
                start=1,
            ):
                source_page_row = (
                    source_notation_page_rows.get(str(page_number), {})
                    if page_number
                    else {}
                )
                source_status = str(source_page_row.get("status") or "")
                source_decoded_text = str(
                    source_page_row.get("decoded_text") or ""
                ).strip()
                source_notation_audit_rows.append(
                    {
                        "document_path": document_path,
                        "page_number": page_number,
                        "fragment_index": fragment_index,
                        "epub_text": notation_text,
                        "source_status": source_status or "unavailable",
                        "source_decoded_text": source_decoded_text,
                        "source_blockers": list(
                            source_page_row.get("blockers") or []
                        ),
                    }
                )
                solution_blocks = [
                    dict(block)
                    for block in source_page_row.get("solution_blocks") or []
                    if isinstance(block, Mapping)
                ]
                if (
                    solution_blocks
                    and page_number not in source_notation_consumed_pages
                ):
                    source_notation_consumed_pages.add(page_number)
                    source_solution_block_count += len(solution_blocks)
                    for block in solution_blocks:
                        display_notation_text = str(
                            block.get("decoded_text")
                            or block.get("notation_text")
                            or ""
                        ).strip()
                        if not display_notation_text:
                            continue
                        source_decoded_notation_fragments += int(
                            block.get("status") == "decoded"
                        )
                        replay_accepted = bool(
                            block.get("replay_status") == "accepted"
                            and block.get("accepted_pgn")
                        )
                        source_solution_replay_accepted += int(
                            replay_accepted
                        )
                        if replay_accepted:
                            source_accepted_pgn_records.append(
                                str(block.get("accepted_pgn") or "").strip()
                            )
                        block_reasons = sorted(
                            {
                                *[
                                    str(reason)
                                    for reason in block.get("blockers") or []
                                    if str(reason)
                                ],
                                *[
                                    str(reason)
                                    for reason in block.get(
                                        "replay_warnings"
                                    )
                                    or []
                                    if str(reason)
                                ],
                            }
                        )
                        source_notation_review_fragments += int(
                            bool(block_reasons) or not replay_accepted
                        )
                        quality = _notation_quality(
                            display_notation_text
                        )
                        card_status = (
                            "accepted" if replay_accepted else "blocked"
                        )
                        notation_blocker_count += int(
                            card_status == "blocked"
                        )
                        notation_question_mark_count += int(
                            quality["question_mark_count"]
                        )
                        exercise_id = str(
                            block.get("exercise_id") or ""
                        )
                        diagram_id = str(block.get("diagram_id") or "")
                        diagram_record = diagram_records_by_id.get(
                            diagram_id
                        )
                        diagram_page = int(
                            block.get("diagram_page")
                            or (
                                _diagram_page(diagram_record)
                                if diagram_record
                                else 0
                            )
                        )
                        diagram_context = _notation_diagram_context(
                            page_number=diagram_page or page_number,
                            records=(
                                [diagram_record]
                                if diagram_record is not None
                                else []
                            ),
                            diagram_assets=diagram_assets,
                        )
                        notation_id = (
                            f"notation-{len(notation_cards) + 1:04d}"
                        )
                        pgn_id = f"{notation_id}-pgn"
                        accepted_pgn = str(
                            block.get("accepted_pgn") or ""
                        ).strip()
                        reasons = [
                            *quality["reasons"],
                            *block_reasons,
                        ]
                        blocker_list = "".join(
                            f"<li>{html.escape(reason)}</li>"
                            for reason in dict.fromkeys(reasons)
                        )
                        pgn_markup = ""
                        if accepted_pgn:
                            pgn_markup = (
                                '<details class="accepted-pgn">'
                                "<summary>PGN po parserze i replayu</summary>"
                                f'<pre id="{pgn_id}" class="pgn-source"><code>'
                                f"{html.escape(accepted_pgn)}</code></pre>"
                                "</details>"
                            )
                        copy_pgn_button = (
                            '<button type="button" '
                            'class="copy-button copy-pgn primary" '
                            f'data-copy-target="{pgn_id}">Kopiuj PGN</button>'
                            if accepted_pgn
                            else (
                                '<button type="button" '
                                'class="copy-button copy-pgn" disabled '
                                'title="PGN jest dostepny dopiero po pelnym replayu">'
                                "Kopiuj PGN</button>"
                            )
                        )
                        notation_cards.append(
                            f'<article class="notation-card" '
                            f'data-status="{card_status}" '
                            f'data-source-page="{page_number or ""}" '
                            f'data-exercise-id="{html.escape(exercise_id, quote=True)}" '
                            f'data-diagram-id="{html.escape(diagram_id, quote=True)}" '
                            'data-decoding-source="source_font_sha_gid">'
                            '<div class="card-heading"><div>'
                            f'<p class="eyebrow">Ex. {html.escape(exercise_id)} '
                            f"- strona PDF {page_number}</p>"
                            "<h3>Notacja zrodlowa</h3></div>"
                            '<span class="quality-badge">'
                            f'{"Parser + replay" if replay_accepted else "Wymaga weryfikacji"}'
                            "</span></div>"
                            f'<pre id="{notation_id}" class="notation-source"><code>'
                            f"{html.escape(display_notation_text)}</code></pre>"
                            f"{pgn_markup}"
                            '<div class="copy-actions"><button type="button" '
                            'class="copy-button" '
                            f'data-copy-target="{notation_id}">'
                            "Kopiuj tekst notacji</button>"
                            f"{copy_pgn_button}</div>"
                            f"{diagram_context}"
                            f'{f"<ul class=quality-reasons>{blocker_list}</ul>" if blocker_list else ""}'
                            "</article>"
                        )
                    continue
                if (
                    source_status == "decoded"
                    and source_decoded_text
                    and page_number in source_notation_consumed_pages
                ):
                    continue
                display_notation_text = notation_text
                decoding_source = "epub_text_layer"
                if source_status == "decoded" and source_decoded_text:
                    display_notation_text = source_decoded_text
                    decoding_source = "source_font_sha_gid"
                    source_notation_consumed_pages.add(page_number)
                    source_decoded_notation_fragments += 1
                elif source_status == "needs_review":
                    source_notation_review_fragments += 1
                notation_key = hashlib.sha256(
                    (
                        f"{document_path}\0{page_number}\0{display_notation_text}"
                    ).encode("utf-8")
                ).hexdigest()
                if not display_notation_text or notation_key in notation_seen:
                    continue
                notation_seen.add(notation_key)
                quality = _notation_quality(display_notation_text)
                notation_blocker_count += int(quality["blocked"])
                notation_question_mark_count += int(quality["question_mark_count"])
                diagram_context = _notation_diagram_context(
                    page_number=page_number,
                    records=diagram_records_by_page.get(page_number, []),
                    diagram_assets=diagram_assets,
                )
                source_label = (
                    f"Strona PDF {page_number}, fragment {fragment_index}"
                    if page_number
                    else f"Sekcja {index}, fragment {notation_index}"
                )
                notation_id = f"notation-{len(notation_cards) + 1:04d}"
                blocker_list = "".join(
                    f"<li>{html.escape(reason)}</li>"
                    for reason in quality["reasons"]
                )
                notation_cards.append(
                    f'<article class="notation-card" data-status="{quality["status"]}" '
                    f'data-source-page="{page_number or ""}" '
                    f'data-decoding-source="{decoding_source}">'
                    f'<div class="card-heading"><div><p class="eyebrow">{html.escape(source_label)}</p>'
                    f"<h3>Notacja źródłowa</h3></div>"
                    f'<span class="quality-badge">{html.escape(quality["label"])}</span></div>'
                    f'<pre id="{notation_id}" class="notation-source"><code>'
                    f"{html.escape(display_notation_text)}</code></pre>"
                    f'<div class="copy-actions"><button type="button" class="copy-button" '
                    f'data-copy-target="{notation_id}">Kopiuj tekst notacji</button>'
                    f'<button type="button" class="copy-button copy-pgn" disabled '
                    f'title="PGN będzie dostępny po przejściu parsera i legalnego odtworzenia">'
                    f"Kopiuj PGN</button></div>"
                    f"{diagram_context}"
                    f'{f"<ul class=quality-reasons>{blocker_list}</ul>" if blocker_list else ""}'
                    f"</article>"
                )
        sections.append(
            f'<section class="book-spine-document" id="spine-{index:03d}" '
            f'data-spine-href="{html.escape(document_path, quote=True)}">'
            f'<div class="source-document-label">Section {index}</div>'
            f"{''.join(str(child) for child in body.contents)}</section>"
        )

    page_layout = _build_reader_page_layout(
        staging=staging,
        artifact_root=artifact_root,
        records=verified_records,
        diagram_assets=diagram_assets,
    )
    combined_pgn_payload = b"\n\n".join(
        payload
        for payload in (
            accepted_pgn_payload.strip(),
            "\n\n".join(source_accepted_pgn_records).encode("utf-8"),
        )
        if payload
    )
    reader_accepted_pgn_count = _pgn_game_count(combined_pgn_payload)
    pgn_cards = _reader_pgn_cards(combined_pgn_payload)
    safe_title = html.escape(title or "Chess publication")
    index_html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>{safe_title}</title><link rel="stylesheet" href="styles.css"></head>
<body><header class="reader-header"><p>KindleMaster full publication</p><h1>{safe_title}</h1>
<div class="reader-stats"><span>{page_layout["page_count"]} PDF pages</span><span>{len(spine_documents)} text sections</span><span>{full_fen_count} verified FEN</span>
<span>{placement_count} verified placements</span><span>{source_diagram_count} unreadable source diagrams</span>
<span>{reader_accepted_pgn_count} accepted PGN</span>
<span>{int(exercise_index_summary.get("exact_count") or 0) + int(exercise_index_summary.get("consensus_count") or 0)} linked exercises</span>
<span>{int(exercise_index_summary.get("review_queue_count") or 0)} binding reviews</span></div>
<p>Source prose and notation are preserved. Only parser-approved PGN can be copied as PGN.</p></header>
<nav class="reader-nav" aria-label="Publication views">
<a href="#pdf-pages">Strony PDF</a><a href="#book-text">Treść książki</a>
<a href="#notation">Notacja</a><a href="#pgn">PGN</a></nav>
<main>
<section class="reader-surface" id="pdf-pages"><div class="surface-heading"><p class="eyebrow">Układ źródłowy</p>
<h2>Strony PDF</h2><p>Diagramy zweryfikowane są nakładane w położeniu wynikającym ze współrzędnych źródłowych.</p></div>
<div class="pdf-page-list">{page_layout["html"]}</div></section>
<section class="reader-surface" id="book-text"><div class="surface-heading"><p class="eyebrow">Warstwa dostępna</p>
<h2>Pełna treść książki</h2><p>Tekst reflow zachowuje pełną treść i odnośniki EPUB.</p></div>
{''.join(sections)}</section>
<section class="reader-surface" id="notation"><div class="surface-heading"><p class="eyebrow">Weryfikacja ruchów</p>
<h2>Notacja</h2><p>Znaki oceny „?” i „!” mogą być poprawną częścią notacji. Blokowane są nierozpoznane glify, znaki zastępcze i tekst, który nie przeszedł replay.</p></div>
<div class="notation-list">{''.join(notation_cards) or '<p class="empty-state">Brak wydzielonej notacji.</p>'}</div></section>
<section class="reader-surface" id="pgn"><div class="surface-heading"><p class="eyebrow">Eksport maszynowy</p>
<h2>PGN</h2><p>Przycisk kopiowania pojawia się wyłącznie dla rekordów zaakceptowanych przez parser i legalne odtworzenie.</p></div>
{pgn_cards}</section>
</main><div class="copy-toast" id="copy-toast" role="status" aria-live="polite"></div>
<script src="reader.js"></script></body></html>"""
    styles = """
:root{--paper:#f6f0e5;--ink:#172019;--muted:#6b6257;--line:#d6c7b4;--accent:#a44920}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#efe4d4,#faf7ef 42%,#e7ecdf);
color:var(--ink);font-family:Georgia,serif;line-height:1.58}.reader-header,.reader-nav,main{width:min(1120px,calc(100% - 2rem));margin:auto}
.reader-header{padding:3rem 0 1.25rem}.reader-header p:first-child{color:var(--accent);font:700 .78rem/1.2 sans-serif;
letter-spacing:.13em;text-transform:uppercase}.reader-header h1{font-size:clamp(2.25rem,7vw,5rem);line-height:.95;margin:.4rem 0 1.2rem}
.reader-stats{display:flex;flex-wrap:wrap;gap:.55rem}.reader-stats span,.reader-nav a{border:1px solid var(--line);
border-radius:999px;background:#fffaf2;padding:.4rem .7rem}.reader-nav{display:flex;gap:.4rem;overflow:auto;padding:.75rem 0 1.25rem;
position:sticky;top:0;background:rgba(246,240,229,.94);z-index:5;scrollbar-width:none}.reader-nav::-webkit-scrollbar{display:none}.reader-nav a{color:var(--ink);text-decoration:none}
.reader-nav a{min-height:42px;display:inline-flex;align-items:center;flex:0 0 auto;white-space:nowrap}
.reader-surface{scroll-margin-top:5rem;margin-bottom:3rem}.surface-heading{max-width:760px;margin:1.5rem 0 1rem}.surface-heading h2{font-size:clamp(1.8rem,4vw,3rem);margin:.2rem 0}
.eyebrow{margin:0;color:var(--accent);font:700 .72rem/1.2 sans-serif;letter-spacing:.12em;text-transform:uppercase}
.pdf-page-list{display:grid;gap:1.4rem}.pdf-page{width:min(900px,100%);margin:auto;background:#fff;border:1px solid var(--line);border-radius:18px;overflow:hidden;box-shadow:0 18px 44px rgba(47,39,28,.12);scroll-margin-top:5.5rem}
.pdf-page-header{display:flex;justify-content:space-between;gap:1rem;padding:.7rem 1rem;background:#f4ecdf;border-bottom:1px solid var(--line);font:700 .78rem/1.2 sans-serif}
.page-canvas{position:relative;background:#fff;line-height:0}.page-image{display:block;width:100%;height:auto}.verified-diagram-overlay{position:absolute;left:var(--x);top:var(--y);width:var(--w);height:var(--h);object-fit:contain;margin:0;box-shadow:0 0 0 2px rgba(30,104,71,.72);background:transparent}
.page-layout-warning{padding:.7rem 1rem;color:#8a4b00;background:#fff4dc;font:600 .78rem/1.4 sans-serif}
.book-spine-document{background:#fffdf8;border:1px solid var(--line);border-radius:22px;padding:clamp(1.1rem,4vw,3rem);
margin:0 auto 1.25rem;box-shadow:0 14px 36px rgba(47,39,28,.08);min-width:0;overflow:hidden}.book-spine-document img,.book-spine-document svg{max-width:100%!important;height:auto!important}.book-spine-document table{display:block;max-width:100%;overflow-x:auto}.source-document-label{font:700 .72rem/1.2 sans-serif;
letter-spacing:.1em;color:var(--muted);text-transform:uppercase;margin-bottom:1rem}.chess-diagram{display:block;max-width:100%;height:auto;margin:1rem auto}
.kindlemaster-generated-fen{font:600 .82rem/1.4 ui-monospace,monospace;overflow-wrap:anywhere;background:#edf5e9;padding:.7rem;border-radius:10px}
pre,.chess-notation-page{white-space:pre-wrap;overflow-wrap:anywhere}.notation-list,.pgn-list{display:grid;gap:1rem}.notation-card,.pgn-card{background:#fffdf8;border:1px solid var(--line);border-radius:16px;padding:1rem;box-shadow:0 10px 26px rgba(47,39,28,.06)}
.notation-card[data-status=blocked]{border-left:5px solid #b5522d}.card-heading{display:flex;justify-content:space-between;gap:1rem;align-items:flex-start}.card-heading h3{margin:.15rem 0}.quality-badge{border-radius:999px;padding:.32rem .55rem;background:#edf5e9;font:700 .7rem/1.2 sans-serif}.notation-card[data-status=blocked] .quality-badge{background:#fff0e8;color:#8b3519}
.notation-source,.pgn-source{max-height:28rem;overflow:auto;padding:1rem;border-radius:10px;background:#172019;color:#f8f2e7;font:500 .84rem/1.55 ui-monospace,Consolas,monospace}.copy-actions{display:flex;flex-wrap:wrap;gap:.55rem;margin-top:.8rem}.copy-button{min-height:42px;border:1px solid #8f765f;border-radius:999px;background:#fff;padding:.58rem .85rem;color:var(--ink);font:700 .78rem/1 sans-serif;cursor:pointer}.copy-button:hover{background:#f5eadb}.copy-button:focus-visible{outline:3px solid #d98b5f;outline-offset:2px}.copy-button:disabled{cursor:not-allowed;opacity:.5}.copy-button.primary{background:var(--accent);border-color:var(--accent);color:#fff}.notation-evidence{margin-top:1rem;padding:1rem;border:1px solid #d9cbb8;border-radius:12px;background:#f7f2e8}.notation-evidence h4{margin:.2rem 0 .65rem}.notation-evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.7rem}.notation-diagram{display:grid;grid-template-columns:64px 1fr;gap:.65rem;align-items:start;padding:.65rem;border-radius:10px;background:#fff}.notation-diagram img{width:64px;height:64px;object-fit:contain;background:#fff}.notation-diagram code{display:block;font-size:.68rem;overflow-wrap:anywhere}.quality-reasons{color:#8a4b00}.copy-toast{position:fixed;right:1rem;bottom:1rem;z-index:20;max-width:24rem;padding:.75rem 1rem;border-radius:12px;background:#172019;color:#fff;opacity:0;transform:translateY(.5rem);pointer-events:none;transition:.2s}.copy-toast[data-visible=true]{opacity:1;transform:none}
a{color:#8b3d1c}@media(max-width:640px){.reader-header{padding-top:1.7rem}.book-spine-document,.pdf-page{border-radius:12px}.reader-nav{width:100%;padding-inline:1rem}.card-heading{display:block}.reader-surface{scroll-margin-top:4rem}}
"""
    script = """
const toast=document.getElementById("copy-toast");
function showToast(message){toast.textContent=message;toast.dataset.visible="true";window.clearTimeout(showToast.timer);showToast.timer=window.setTimeout(()=>{toast.dataset.visible="false"},2200)}
async function copyText(value){if(navigator.clipboard&&window.isSecureContext){try{await navigator.clipboard.writeText(value);return}catch{}}const area=document.createElement("textarea");area.value=value;area.setAttribute("readonly","");area.style.position="fixed";area.style.opacity="0";document.body.appendChild(area);area.focus({preventScroll:true});area.select();area.setSelectionRange(0,area.value.length);const copied=document.execCommand("copy");area.remove();if(!copied)throw new Error("clipboard_copy_failed")}
document.addEventListener("click",async event=>{const button=event.target.closest("[data-copy-target]");if(!button||button.disabled)return;const target=document.getElementById(button.dataset.copyTarget);if(!target)return;try{await copyText(target.textContent.trim());showToast("Skopiowano do schowka")}catch{showToast("Nie udało się skopiować")}});
"""
    (staging / "index.html").write_text(index_html, encoding="utf-8")
    (staging / "styles.css").write_text(styles.strip() + "\n", encoding="utf-8")
    (staging / "reader.js").write_text(script.strip() + "\n", encoding="utf-8")
    summary = {
        "full_publication": True,
        "spine_document_count": len(spine_documents),
        "pdf_page_count": page_layout["page_count"],
        "page_facsimile_count": page_layout["facsimile_count"],
        "diagram_overlay_count": page_layout["overlay_count"],
        "diagram_overlay_missing_bbox_count": page_layout["missing_bbox_count"],
        "page_facsimile_cache_hit_count": page_layout["cache_hit_count"],
        "page_facsimile_render_count": page_layout["render_count"],
        "page_facsimile_render_seconds": page_layout["render_seconds"],
        "reader_visible_text_characters": visible_chars,
        "fen_accepted": full_fen_count,
        "fen_placement_verified": placement_count,
        "accepted_pgn": reader_accepted_pgn_count,
        "notation_fragment_count": len(notation_cards),
        "notation_blocker_count": notation_blocker_count,
        "notation_question_mark_count": notation_question_mark_count,
        "source_decoded_notation_fragments": source_decoded_notation_fragments,
        "source_notation_review_fragments": source_notation_review_fragments,
        "source_solution_block_count": source_solution_block_count,
        "source_solution_replay_accepted": source_solution_replay_accepted,
        "source_solution_replay_review": (
            source_solution_block_count - source_solution_replay_accepted
        ),
        "exercise_index_exact": int(
            exercise_index_summary.get("exact_count") or 0
        ),
        "exercise_index_consensus": int(
            exercise_index_summary.get("consensus_count") or 0
        ),
        "exercise_index_candidate": int(
            exercise_index_summary.get("candidate_count") or 0
        ),
        "exercise_index_conflict": int(
            exercise_index_summary.get("conflict_count") or 0
        ),
        "exercise_index_orphan_solution": int(
            exercise_index_summary.get("orphan_solution_count") or 0
        ),
        "exercise_index_review_queue": int(
            exercise_index_summary.get("review_queue_count") or 0
        ),
        "exercise_index_vision_candidates": int(
            exercise_index_summary.get(
                "vision_candidate_diagram_count"
            )
            or 0
        ),
        "diagrams_total": full_fen_count + placement_count + source_diagram_count,
        "empty_img_src_count": 0,
    }
    manifest = {
        "schema": "kindlemaster.chess_study.artifact_manifest.v1",
        "artifact_type": FINAL_READER_ARTIFACT_TYPE,
        "pipeline_mode": "full_epub_enriched_reader",
        "summary": summary,
        "source_html_quality_gate": {
            "decision": "use_full_epub_as_final_reader",
            "source_html_evidence_only": False,
            "used_as_final_reader": True,
            "reasons": [],
        },
    }
    health = {
        "schema": "kindlemaster.chess_study.final_reader_health_gate.v1",
        "decision": "pass",
        "artifact_type": FINAL_READER_ARTIFACT_TYPE,
        "blockers": [],
        "warnings": [
            *(
                []
                if reader_accepted_pgn_count
                else ["no_parser_accepted_pgn"]
            ),
            *(
                []
                if not source_notation_review_fragments
                else ["source_notation_review_required"]
            ),
            *([] if page_layout["page_count"] else ["page_facsimile_unavailable"]),
        ],
        "summary": summary,
    }
    source_notation_audit = {
        **source_notation_pages,
        "epub_fragments": source_notation_audit_rows,
        "summary": {
            "source_decoded_notation_fragments": (
                source_decoded_notation_fragments
            ),
            "source_notation_review_fragments": (
                source_notation_review_fragments
            ),
            "source_solution_block_count": source_solution_block_count,
            "source_solution_replay_accepted": (
                source_solution_replay_accepted
            ),
            "source_solution_replay_review": (
                source_solution_block_count
                - source_solution_replay_accepted
            ),
            "exercise_index": exercise_index_summary,
            "epub_fragment_count": len(source_notation_audit_rows),
        },
    }
    _atomic_write_json(staging / "data" / "artifact_manifest.json", manifest)
    _atomic_write_json(staging / "reports" / "final_reader_health_gate.json", health)
    _atomic_write_json(
        staging / "reports" / "source_notation_decode.json",
        source_notation_audit,
    )
    _atomic_write_json(
        staging / "reports" / "chess_exercise_index.json",
        exercise_index,
    )
    if reader_dir.exists():
        backup = reader_dir.with_name(reader_dir.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        os.replace(reader_dir, backup)
        os.replace(staging, reader_dir)
        shutil.rmtree(backup)
    else:
        os.replace(staging, reader_dir)
    return summary


def _reader_source_notation_pages(
    *,
    artifact_root: Path,
    spine_documents: Sequence[str],
    member_bytes: Mapping[str, bytes],
    diagram_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    pdf_candidates = sorted((artifact_root / "input").glob("*.pdf"))
    if not pdf_candidates:
        return {
            "schema": "kindlemaster.source_bound_chess_notation.v1",
            "source_pdf": "",
            "source_pdf_sha256": "",
            "pages": {},
            "error": "source_pdf_unavailable",
        }
    page_numbers: set[int] = set()
    for document_path in spine_documents:
        soup = BeautifulSoup(
            member_bytes[document_path].decode("utf-8", errors="replace"),
            "html.parser",
        )
        for node in soup.select(
            ".chess-notation-page, .chess-notation-text, .notation-heavy"
        ):
            page_number = _notation_page_number(node)
            if page_number:
                page_numbers.add(page_number)
    if not page_numbers:
        return {
            "schema": "kindlemaster.source_bound_chess_notation.v1",
            "source_pdf": str(pdf_candidates[0]),
            "source_pdf_sha256": "",
            "pages": {},
            "error": "source_notation_pages_unavailable",
        }
    try:
        source_payload = extract_source_notation_pages(
            pdf_candidates[0],
            page_numbers=page_numbers,
        )
        known_exercise_ids = {
            str(block.get("exercise_id") or "").strip()
            for page in (source_payload.get("pages") or {}).values()
            if isinstance(page, Mapping)
            for block in page.get("solution_blocks") or []
            if isinstance(block, Mapping)
            and str(block.get("exercise_id") or "").strip()
        }
        diagram_binding = assign_source_exercise_labels_to_diagrams(
            pdf_candidates[0],
            diagram_records,
            known_exercise_ids=known_exercise_ids,
        )
        exercise_index = build_source_exercise_index(
            source_payload,
            diagram_binding["records"],
            diagram_binding["assignments"],
        )
        replayed = replay_source_notation_blocks(
            source_payload,
            diagram_binding["records"],
            exercise_index=exercise_index,
        )
        return {
            **replayed,
            "exercise_index": exercise_index,
            "diagram_binding": {
                "schema": diagram_binding["schema"],
                "summary": diagram_binding["summary"],
                "assignments": diagram_binding["assignments"],
            },
        }
    except Exception as error:
        return {
            "schema": "kindlemaster.source_bound_chess_notation.v1",
            "source_pdf": str(pdf_candidates[0]),
            "source_pdf_sha256": "",
            "pages": {},
            "error": f"source_notation_decode_failed:{type(error).__name__}",
        }


def _notation_quality(text: str) -> dict[str, Any]:
    normalized = str(text or "")
    reasons: list[str] = []
    if "\ufffd" in normalized:
        reasons.append("Znak zastępczy Unicode wskazuje utracony glif.")
    if any(
        ord(character) < 32 and character not in "\n\r\t"
        for character in normalized
    ):
        reasons.append("Tekst zawiera niedrukowalny znak kontrolny.")
    if re.search(r"(?:[\"']?t!;>|[\"'][^\s]{0,10}[;>])", normalized):
        reasons.append("Wykryto token charakterystyczny dla niezmapowanego fontu szachowego.")
    if re.search(r"(?:@|£|[a-h][1-8][t†‡¢](?=\s|[!?.,;:)]|$))", normalized):
        reasons.append(
            "Wykryto znak spoza poprawnego SAN, prawdopodobnie z fontu szachowego."
        )
    reasons.append(
        "Fragment nie ma jeszcze jednoznacznego powiązania z PGN zaakceptowanym przez parser i replay."
    )
    question_mark_count = normalized.count("?")
    return {
        "blocked": True,
        "status": "blocked",
        "label": "Niezweryfikowana",
        "reasons": reasons,
        "question_mark_count": question_mark_count,
    }


def _notation_text_candidates(node: Any) -> list[str]:
    classes = {
        str(value).strip()
        for value in (node.get("class") or [])
        if str(value).strip()
    }
    text = node.get_text("\n", strip=True)
    if not text:
        return []
    if "chess-notation-page" not in classes:
        return [text]
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    strong_indices = {
        index for index, line in enumerate(lines) if _looks_like_notation_line(line)
    }
    selected_indices = set(strong_indices)
    for index in strong_indices:
        for neighbor in (index - 1, index + 1):
            if 0 <= neighbor < len(lines) and _has_move_marker(lines[neighbor]):
                selected_indices.add(neighbor)
    candidates = [
        line for index, line in enumerate(lines) if index in selected_indices
    ]
    return ["\n".join(candidates)] if candidates else []


def _looks_like_notation_line(line: str) -> bool:
    markers = re.findall(r"(?<![\d.])\d{1,3}\.(?:\.\.)?\s*\S+", line)
    if not markers:
        return False
    move_like_tokens = re.findall(
        r"(?:O-O(?:-O)?|[A-Za-z@&£]?[a-h][1-8](?:[+#t†‡¢])?)",
        line,
    )
    return len(markers) >= 2 or len(move_like_tokens) >= 2


def _has_move_marker(line: str) -> bool:
    return bool(re.search(r"(?<![\d.])\d{1,3}\.(?:\.\.)?\s*\S+", line))


def _notation_page_number(node: Any) -> int:
    current = node
    while current is not None:
        for key in ("data-page", "data-source-page"):
            value = str(current.get(key) or "").strip() if hasattr(current, "get") else ""
            if value.isdigit():
                return int(value)
        current = getattr(current, "parent", None)
    if hasattr(node, "find_previous"):
        previous = node.find_previous(attrs={"data-source-page": True})
        value = str(previous.get("data-source-page") or "").strip() if previous else ""
        if value.isdigit():
            return int(value)
    return 0


def _notation_diagram_context(
    *,
    page_number: int,
    records: Sequence[Mapping[str, Any]],
    diagram_assets: Mapping[str, str],
) -> str:
    if not page_number:
        return (
            '<aside class="notation-evidence"><p>Brak numeru strony źródłowej, '
            "więc fragmentu nie można bezpiecznie powiązać z diagramem.</p></aside>"
        )
    cards: list[str] = []
    for record in sorted(records, key=_record_source_order):
        diagram_id = str(record.get("diagram_id") or record.get("id") or "").strip()
        if not diagram_id:
            continue
        asset = diagram_assets.get(diagram_id)
        full_fen = str(record.get("full_fen") or "").strip()
        fen_verified = bool(
            full_fen
            and (
                record.get("fen_human_verified") is True
                or record.get("human_verified") is True
            )
        )
        side_to_move = ""
        if fen_verified:
            parts = full_fen.split()
            if len(parts) > 1:
                side_to_move = "Białe na ruchu" if parts[1] == "w" else "Czarne na ruchu"
        image = (
            f'<img loading="lazy" src="{html.escape(asset, quote=True)}" '
            f'alt="Diagram {html.escape(diagram_id, quote=True)}">'
            if asset
            else ""
        )
        fen = (
            f'<code title="{html.escape(full_fen, quote=True)}">'
            f"{html.escape(full_fen)}</code>"
            if fen_verified
            else "<span>FEN niezweryfikowany</span>"
        )
        cards.append(
            f'<div class="notation-diagram" data-diagram-id="{html.escape(diagram_id, quote=True)}">'
            f"{image}<div><strong>{html.escape(diagram_id)}</strong>"
            f"{fen}<small>{html.escape(side_to_move)}</small></div></div>"
        )
    body = (
        f'<div class="notation-evidence-grid">{"".join(cards)}</div>'
        if cards
        else "<p>Na tej stronie nie ma potwierdzonego diagramu.</p>"
    )
    return (
        '<aside class="notation-evidence"><p class="eyebrow">Kontekst pozycji</p>'
        f"<h4>Diagramy ze strony {page_number}</h4>"
        f'<p><a href="#pdf-page-{page_number:04d}">Pokaż stronę PDF i położenie diagramu</a></p>'
        f"{body}</aside>"
    )


def _reader_pgn_cards(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return (
            '<div class="empty-state"><p>Brak PGN ruchów zaakceptowanego przez parser i replay.</p>'
            '<button type="button" class="copy-button" disabled>Kopiuj PGN</button></div>'
        )
    games: list[str] = []
    try:
        import chess.pgn

        stream = io.StringIO(text)
        while True:
            game = chess.pgn.read_game(stream)
            if game is None:
                break
            serialized = str(game).strip()
            if serialized:
                games.append(serialized)
    except ImportError:
        games = [text]
    cards = []
    for index, game in enumerate(games, start=1):
        target = f"pgn-record-{index:04d}"
        cards.append(
            f'<article class="pgn-card"><div class="card-heading"><div><p class="eyebrow">'
            f"PGN {index}</p><h3>Zaakceptowany zapis</h3></div>"
            f'<span class="quality-badge">Parser + replay</span></div>'
            f'<pre class="pgn-source" id="{target}"><code>{html.escape(game)}</code></pre>'
            f'<div class="copy-actions"><button type="button" class="copy-button primary" '
            f'data-copy-target="{target}">Kopiuj PGN</button></div></article>'
        )
    all_target = "pgn-all"
    return (
        f'<div class="copy-actions"><button type="button" class="copy-button primary" '
        f'data-copy-target="{all_target}">Kopiuj wszystkie PGN</button></div>'
        f'<pre id="{all_target}" hidden>{html.escape(text)}</pre>'
        f'<div class="pgn-list">{"".join(cards)}</div>'
    )


def _reader_diagram_assets(
    *,
    spine_documents: Sequence[str],
    member_bytes: Mapping[str, bytes],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for document_path in spine_documents:
        soup = BeautifulSoup(
            member_bytes[document_path].decode("utf-8", errors="replace"),
            "html.parser",
        )
        for image in soup.select("img[data-diagram-id][src]"):
            diagram_id = str(image.get("data-diagram-id") or "").strip()
            raw_src = str(image.get("src") or "").split("?", 1)[0].split("#", 1)[0]
            if not diagram_id or not raw_src:
                continue
            member_path = posixpath.normpath(
                posixpath.join(posixpath.dirname(document_path), raw_src)
            )
            if member_path in member_bytes:
                result[diagram_id] = f"epub/{member_path}"
    return result


def _build_reader_page_layout(
    *,
    staging: Path,
    artifact_root: Path,
    records: Sequence[Mapping[str, Any]],
    diagram_assets: Mapping[str, str],
) -> dict[str, Any]:
    pdf_candidates = sorted((artifact_root / "input").glob("*.pdf"))
    if not pdf_candidates:
        return {
            "html": '<p class="empty-state">Brak trwałego PDF do odtworzenia układu stron.</p>',
            "page_count": 0,
            "facsimile_count": 0,
            "overlay_count": 0,
            "missing_bbox_count": len(diagram_assets),
            "cache_hit_count": 0,
            "render_count": 0,
            "render_seconds": 0.0,
        }
    try:
        import fitz
        from PIL import Image
    except ImportError:
        return {
            "html": '<p class="empty-state">Renderer stron PDF jest niedostępny.</p>',
            "page_count": 0,
            "facsimile_count": 0,
            "overlay_count": 0,
            "missing_bbox_count": len(diagram_assets),
            "cache_hit_count": 0,
            "render_count": 0,
            "render_seconds": 0.0,
        }

    pages_dir = staging / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    cache_version = "webp-v1-scale135-q78-m2"
    pdf_digest = _file_sha256(pdf_candidates[0])
    cache_dir = (
        artifact_root
        / "assets"
        / "pdf_page_facsimiles"
        / f"{pdf_digest[:16]}-{cache_version}"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    records_by_page: dict[int, list[Mapping[str, Any]]] = {}
    for record in records:
        if _record_is_confirmed_diagram(record):
            records_by_page.setdefault(_diagram_page(record), []).append(record)
    page_cards: list[str] = []
    overlay_count = 0
    missing_bbox_count = 0
    cache_hit_count = 0
    render_count = 0
    render_seconds = 0.0
    with fitz.open(pdf_candidates[0]) as document:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            page_name = f"page-{page_number:04d}.webp"
            cached_page = cache_dir / page_name
            if cached_page.is_file() and cached_page.stat().st_size > 32:
                cache_hit_count += 1
            else:
                render_started = time.perf_counter()
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(1.35, 1.35),
                    alpha=False,
                )
                image = Image.frombytes(
                    "RGB",
                    (pixmap.width, pixmap.height),
                    pixmap.samples,
                )
                image.save(cached_page, "WEBP", quality=78, method=2)
                render_seconds += time.perf_counter() - render_started
                render_count += 1
            shutil.copy2(cached_page, pages_dir / page_name)
            overlays: list[str] = []
            for record in sorted(
                records_by_page.get(page_number, []),
                key=_record_source_order,
            ):
                diagram_id = str(
                    record.get("diagram_id") or record.get("id") or ""
                ).strip()
                asset = diagram_assets.get(diagram_id)
                bbox = _record_layout_bbox(record)
                if not asset or bbox is None:
                    missing_bbox_count += 1
                    continue
                x0, y0, x1, y1 = bbox
                width = float(page.rect.width)
                height = float(page.rect.height)
                if width <= 0 or height <= 0 or x1 <= x0 or y1 <= y0:
                    missing_bbox_count += 1
                    continue
                style = (
                    f"--x:{max(0.0, min(100.0, x0 / width * 100)):.5f}%;"
                    f"--y:{max(0.0, min(100.0, y0 / height * 100)):.5f}%;"
                    f"--w:{max(0.0, min(100.0, (x1 - x0) / width * 100)):.5f}%;"
                    f"--h:{max(0.0, min(100.0, (y1 - y0) / height * 100)):.5f}%"
                )
                overlays.append(
                    f'<img class="verified-diagram-overlay" src="{html.escape(asset, quote=True)}" '
                    f'alt="Zweryfikowany diagram, strona {page_number}" '
                    f'data-diagram-id="{html.escape(diagram_id, quote=True)}" style="{style}">'
                )
                overlay_count += 1
            warning = (
                '<div class="page-layout-warning">Nie wszystkie diagramy na tej stronie mają '
                "wystarczające współrzędne do bezpiecznej nakładki.</div>"
                if records_by_page.get(page_number)
                and len(overlays) < len(records_by_page[page_number])
                else ""
            )
            page_cards.append(
                f'<article class="pdf-page" id="pdf-page-{page_number:04d}" data-page="{page_number}">'
                f'<div class="pdf-page-header"><span>Strona {page_number}</span>'
                f"<span>{len(overlays)} zweryfikowanych nakładek</span></div>"
                f'<div class="page-canvas"><img class="page-image" loading="lazy" '
                f'src="pages/{page_name}" alt="Strona PDF {page_number}">{"".join(overlays)}</div>'
                f"{warning}</article>"
            )
    return {
        "html": "".join(page_cards),
        "page_count": len(page_cards),
        "facsimile_count": len(page_cards),
        "overlay_count": overlay_count,
        "missing_bbox_count": missing_bbox_count,
        "cache_hit_count": cache_hit_count,
        "render_count": render_count,
        "render_seconds": round(render_seconds, 4),
    }


def _record_layout_bbox(
    record: Mapping[str, Any],
) -> tuple[float, float, float, float] | None:
    for key in ("bbox", "board_bbox", "source_bbox", "crop_bbox"):
        value = record.get(key)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            continue
        if len(value) < 4:
            continue
        try:
            x0, y0, x1, y1 = (float(value[index]) for index in range(4))
        except (TypeError, ValueError):
            continue
        return x0, y0, x1, y1
    return None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_pgn_payload(path: str | Path | None) -> bytes:
    if not path:
        return b""
    candidate = Path(path)
    if not candidate.is_file():
        return b""
    payload = candidate.read_bytes().strip()
    if not payload:
        return b""
    text = payload.decode("utf-8", errors="replace")
    try:
        import chess.pgn
    except ImportError as error:
        raise FullChessPublicationError("python_chess_pgn_parser_unavailable") from error
    stream = io.StringIO(text)
    parsed_count = 0
    move_count = 0
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        parsed_count += 1
        if game.errors:
            raise FullChessPublicationError("accepted_pgn_parser_error")
        if any(
            not str(game.headers.get(header) or "").strip()
            for header in ("Date", "Round", "White", "Black", "Result")
        ):
            raise FullChessPublicationError("accepted_pgn_required_headers_missing")
        board = game.board()
        try:
            for move in game.mainline_moves():
                board.push(move)
                move_count += 1
        except ValueError as error:
            raise FullChessPublicationError("accepted_pgn_replay_error") from error
    if parsed_count == 0:
        raise FullChessPublicationError("accepted_pgn_empty")
    if move_count == 0:
        raise FullChessPublicationError("accepted_pgn_position_only")
    return payload + b"\n"


def _pgn_game_count(payload: bytes) -> int:
    return len(re.findall(r"(?m)^\[Result\s+\"", payload.decode("utf-8", errors="replace")))


def _record_is_confirmed_diagram(record: Mapping[str, Any]) -> bool:
    return (
        record.get("publication_included") is not False
        and record.get("confirmed_diagram") is not False
    )


def _record_source_order(record: Mapping[str, Any]) -> tuple[int, float, float, str]:
    bbox = list(record.get("bbox") or [])
    try:
        y0 = float(bbox[1])
        x0 = float(bbox[0])
    except (IndexError, TypeError, ValueError):
        y0 = 0.0
        x0 = 0.0
    try:
        source_order = int(record.get("source_order"))
    except (TypeError, ValueError):
        source_order = 1_000_000
    return (
        source_order,
        y0,
        x0,
        str(record.get("diagram_id") or record.get("id") or ""),
    )


def _record_publication_asset(
    record: Mapping[str, Any],
    *,
    root: Path,
    package_path: str,
    existing_assets: Mapping[str, bytes] | None = None,
) -> tuple[str, str, bytes, str]:
    record_id = str(record.get("diagram_id") or record.get("id") or "").strip()
    if not record_id:
        raise FullChessPublicationError("confirmed_diagram_id_missing")
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:20]
    if (
        record.get("fen_human_verified") is True
        or record.get("placement_human_verified") is True
    ):
        href = f"images/verified_fen/{digest}.svg"
        package_asset_path = _join_package_path(package_path, href)
        source = _resolve_record_artifact_path(
            root,
            record.get("verified_render_path") or record.get("rendered_svg"),
        )
        if source is None or not source.is_file():
            existing_payload = (existing_assets or {}).get(package_asset_path)
            if existing_payload is not None:
                return (
                    href,
                    package_asset_path,
                    existing_payload,
                    "image/svg+xml",
                )
            raise FullChessPublicationError(f"verified_render_missing:{record_id}")
        return (
            href,
            package_asset_path,
            source.read_bytes(),
            "image/svg+xml",
        )

    href = f"images/source_diagrams/{digest}.png"
    package_asset_path = _join_package_path(package_path, href)
    source = _resolve_artifact_path(root, record.get("board_crop_path"))
    if source is None or not source.is_file():
        existing_payload = (existing_assets or {}).get(package_asset_path)
        if existing_payload is not None:
            return (
                href,
                package_asset_path,
                existing_payload,
                "image/png",
            )
        raise FullChessPublicationError(f"source_diagram_crop_missing:{record_id}")
    return (
        href,
        package_asset_path,
        source.read_bytes(),
        "image/png",
    )


def _resolve_record_artifact_path(root: Path, value: object) -> Path | None:
    direct = _resolve_artifact_path(root, value)
    if direct is not None and direct.is_file():
        return direct
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return direct
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    semantic_candidate = (
        root / "semantic_chess_html" / Path(*path.parts)
    ).resolve()
    if (
        _is_path_under(semantic_candidate, root)
        and semantic_candidate.is_file()
    ):
        return semantic_candidate
    return direct


def _source_diagram_image_name(record: Mapping[str, Any]) -> str:
    board_crop = PurePosixPath(
        str(record.get("board_crop_path") or "").replace("\\", "/")
    ).name
    if board_crop.endswith("_board.png"):
        return board_crop[: -len("_board.png")] + ".png"
    return str(record.get("filename") or "").strip()


def _diagram_page(record: Mapping[str, Any]) -> int:
    try:
        return max(0, int(record.get("page_number") or record.get("page") or 0))
    except (TypeError, ValueError):
        return 0


def _package_title(package_payload: bytes) -> str:
    try:
        root = ET.fromstring(package_payload)
    except ET.ParseError:
        return "Chess publication"
    title = root.find(".//{http://purl.org/dc/elements/1.1/}title")
    return str(title.text or "").strip() if title is not None else "Chess publication"


def _join_package_path(package_path: str, href: str) -> str:
    value = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), href))
    safe = _safe_member_path(value)
    return safe.as_posix()


def _safe_member_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value or "").replace("\\", "/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FullChessPublicationError("unsafe_epub_member_path")
    return path


def _resolve_artifact_path(root: Path, value: object) -> Path | None:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    candidate = (root / Path(*path.parts)).resolve()
    return candidate if _is_path_under(candidate, root) else None


def _is_path_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _namespace(tag: object) -> str:
    value = str(tag or "")
    return value[1:].split("}", 1)[0] if value.startswith("{") and "}" in value else ""


def _local_name(tag: object) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
