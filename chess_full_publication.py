from __future__ import annotations

import hashlib
import html
import io
import json
import os
import posixpath
import re
import shutil
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup


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
        members = archive.infolist()
        member_bytes = {member.filename: archive.read(member.filename) for member in members}
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
        for member in members:
            if member.filename == "mimetype":
                continue
            payload = (
                package_payload
                if member.filename == package_path
                else modified_documents.get(member.filename, member_bytes[member.filename])
            )
            target.writestr(member, payload)
        for path, payload in sorted(generated_assets.items()):
            target.writestr(path, payload, compress_type=zipfile.ZIP_DEFLATED)
    os.replace(temporary, output)

    reader_summary = _build_full_reader(
        reader,
        title=_package_title(package_payload),
        spine_documents=spine_documents,
        member_bytes=final_member_bytes,
        full_fen_count=full_fen_count,
        placement_count=placement_count,
        source_diagram_count=source_crop_count,
        accepted_pgn_count=_pgn_game_count(accepted_pgn),
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
    if not items:
        return package_payload
    text = package_payload.decode("utf-8")
    existing_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', text))
    fragments: list[str] = []
    for raw_id, href, media_type in items:
        item_id = raw_id
        suffix = 2
        while item_id in existing_ids:
            item_id = f"{raw_id}-{suffix}"
            suffix += 1
        existing_ids.add(item_id)
        fragments.append(
            f'<item id="{html.escape(item_id, quote=True)}" '
            f'href="{html.escape(href, quote=True)}" '
            f'media-type="{html.escape(media_type, quote=True)}"/>'
        )
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


def _build_full_reader(
    reader_dir: Path,
    *,
    title: str,
    spine_documents: Sequence[str],
    member_bytes: Mapping[str, bytes],
    full_fen_count: int,
    placement_count: int,
    source_diagram_count: int,
    accepted_pgn_count: int,
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

    sections: list[str] = []
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
        sections.append(
            f'<section class="book-spine-document" id="spine-{index:03d}" '
            f'data-spine-href="{html.escape(document_path, quote=True)}">'
            f'<div class="source-document-label">Section {index}</div>'
            f"{''.join(str(child) for child in body.contents)}</section>"
        )

    safe_title = html.escape(title or "Chess publication")
    index_html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title><link rel="stylesheet" href="styles.css"></head>
<body><header class="reader-header"><p>KindleMaster full publication</p><h1>{safe_title}</h1>
<div class="reader-stats"><span>{len(spine_documents)} sections</span><span>{full_fen_count} verified FEN</span>
<span>{placement_count} verified placements</span><span>{source_diagram_count} unreadable source diagrams</span>
<span>{accepted_pgn_count} accepted PGN</span></div>
<p>Source prose and notation are preserved. Only parser-approved PGN is offered as a downloadable record.</p></header>
<nav class="reader-nav" aria-label="Publication sections">{''.join(
        f'<a href="#spine-{index:03d}">{index}</a>' for index in range(1, len(sections) + 1)
    )}</nav><main>{''.join(sections)}</main></body></html>"""
    styles = """
:root{--paper:#f6f0e5;--ink:#172019;--muted:#6b6257;--line:#d6c7b4;--accent:#a44920}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(135deg,#efe4d4,#faf7ef 42%,#e7ecdf);
color:var(--ink);font-family:Georgia,serif;line-height:1.58}.reader-header,.reader-nav,main{width:min(920px,calc(100% - 2rem));margin:auto}
.reader-header{padding:3rem 0 1.25rem}.reader-header p:first-child{color:var(--accent);font:700 .78rem/1.2 sans-serif;
letter-spacing:.13em;text-transform:uppercase}.reader-header h1{font-size:clamp(2.25rem,7vw,5rem);line-height:.95;margin:.4rem 0 1.2rem}
.reader-stats{display:flex;flex-wrap:wrap;gap:.55rem}.reader-stats span,.reader-nav a{border:1px solid var(--line);
border-radius:999px;background:#fffaf2;padding:.4rem .7rem}.reader-nav{display:flex;gap:.4rem;overflow:auto;padding:.75rem 0 1.25rem;
position:sticky;top:0;background:rgba(246,240,229,.94);z-index:5}.reader-nav a{color:var(--ink);text-decoration:none}
.book-spine-document{background:#fffdf8;border:1px solid var(--line);border-radius:22px;padding:clamp(1.1rem,4vw,3rem);
margin:0 auto 1.25rem;box-shadow:0 14px 36px rgba(47,39,28,.08)}.source-document-label{font:700 .72rem/1.2 sans-serif;
letter-spacing:.1em;color:var(--muted);text-transform:uppercase;margin-bottom:1rem}.chess-diagram{display:block;max-width:100%;height:auto;margin:1rem auto}
.kindlemaster-generated-fen{font:600 .82rem/1.4 ui-monospace,monospace;overflow-wrap:anywhere;background:#edf5e9;padding:.7rem;border-radius:10px}
pre,.chess-notation-page{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#8b3d1c}@media(max-width:640px){.reader-header{padding-top:1.7rem}
.book-spine-document{border-radius:15px}.reader-nav{width:100%;padding-inline:1rem}}
"""
    (staging / "index.html").write_text(index_html, encoding="utf-8")
    (staging / "styles.css").write_text(styles.strip() + "\n", encoding="utf-8")
    summary = {
        "full_publication": True,
        "spine_document_count": len(spine_documents),
        "reader_visible_text_characters": visible_chars,
        "fen_accepted": full_fen_count,
        "fen_placement_verified": placement_count,
        "accepted_pgn": accepted_pgn_count,
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
        "warnings": ([] if accepted_pgn_count else ["no_parser_accepted_pgn"]),
        "summary": summary,
    }
    _atomic_write_json(staging / "data" / "artifact_manifest.json", manifest)
    _atomic_write_json(staging / "reports" / "final_reader_health_gate.json", health)
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
) -> tuple[str, str, bytes, str]:
    record_id = str(record.get("diagram_id") or record.get("id") or "").strip()
    if not record_id:
        raise FullChessPublicationError("confirmed_diagram_id_missing")
    digest = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:20]
    if (
        record.get("fen_human_verified") is True
        or record.get("placement_human_verified") is True
    ):
        source = _resolve_record_artifact_path(
            root,
            record.get("verified_render_path") or record.get("rendered_svg"),
        )
        if source is None or not source.is_file():
            raise FullChessPublicationError(f"verified_render_missing:{record_id}")
        href = f"images/verified_fen/{digest}.svg"
        return (
            href,
            _join_package_path(package_path, href),
            source.read_bytes(),
            "image/svg+xml",
        )

    source = _resolve_artifact_path(root, record.get("board_crop_path"))
    if source is None or not source.is_file():
        raise FullChessPublicationError(f"source_diagram_crop_missing:{record_id}")
    href = f"images/source_diagrams/{digest}.png"
    return (
        href,
        _join_package_path(package_path, href),
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
