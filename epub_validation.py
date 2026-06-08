from __future__ import annotations

import io
import re
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

import rfc3986
import tldextract
from lxml import etree

from premium_tools import detect_toolchain, run_epubcheck


CONTAINER_NS = {"container": "urn:oasis:names:tc:opendocument:xmlns:container"}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
XHTML_MIME_TYPES = {
    "application/xhtml+xml",
    "text/html",
}
# Keep URL validation fully local and lock-free. The default tldextract cache
# lives under site-packages on this runtime and can deadlock parallel test lanes.
_TLD_EXTRACT = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=None)
_BROKEN_ENCODING_TAIL_RE = re.compile(r"%(?:[0-9A-Fa-f])?$")
_IPV4_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def validate_epub_path(epub_path: str | Path) -> dict[str, Any]:
    source_path = Path(epub_path).resolve()
    epub_bytes = source_path.read_bytes()
    result = validate_epub_bytes(epub_bytes, label=str(source_path))
    result["epub_path"] = str(source_path)
    return result


def validate_epub_bytes(epub_bytes: bytes, *, label: str = "<memory>") -> dict[str, Any]:
    toolchain = detect_toolchain()
    epubcheck = run_epubcheck(epub_bytes)
    package_errors: list[str] = []
    package_warnings: list[str] = []
    internal_errors: list[str] = []
    internal_warnings: list[str] = []
    external_errors: list[str] = []
    external_warnings: list[str] = []
    metadata: dict[str, Any] = {}
    document_stats = {
        "documents_parsed": 0,
        "documents_with_duplicate_ids": 0,
        "manifest_duplicate_id_count": 0,
        "links_checked": 0,
        "internal_href_with_fragment_count": 0,
        "internal_href_without_fragment_count": 0,
        "internal_href_missing_document_count": 0,
        "internal_href_missing_fragment_count": 0,
        "external_links_checked": 0,
        "manifest_item_count": 0,
        "manifest_targets_missing_count": 0,
        "navigation_document_count": 0,
        "spine_item_count": 0,
        "spine_linear_item_count": 0,
        "spine_non_linear_item_count": 0,
        "spine_duplicate_targets": 0,
        "spine_unknown_manifest_references": 0,
        "non_linear_spine_targets": 0,
        "unreachable_non_linear_spine_targets": 0,
    }

    try:
        archive = zipfile.ZipFile(io.BytesIO(epub_bytes))
    except zipfile.BadZipFile:
        package_errors.append("EPUB container is not a valid ZIP archive.")
        return _build_validation_payload(
            label=label,
            toolchain=toolchain,
            epubcheck=epubcheck,
            package_errors=package_errors,
            package_warnings=package_warnings,
            internal_errors=internal_errors,
            internal_warnings=internal_warnings,
            external_errors=external_errors,
            external_warnings=external_warnings,
            metadata=metadata,
            document_stats=document_stats,
        )

    with archive:
        names = archive.namelist()
        if not names:
            package_errors.append("EPUB archive is empty.")
        else:
            if names[0] != "mimetype":
                package_errors.append("First ZIP entry must be 'mimetype'.")
            if "mimetype" not in names:
                package_errors.append("Missing 'mimetype' file.")
            else:
                mimetype = archive.read("mimetype").decode("utf-8", errors="ignore").strip()
                if mimetype != "application/epub+zip":
                    package_errors.append(f"Unexpected mimetype: {mimetype!r}.")
                info = archive.getinfo("mimetype")
                if info.compress_type != zipfile.ZIP_STORED:
                    package_warnings.append("The 'mimetype' entry should be stored without compression.")

        if "META-INF/container.xml" not in names:
            package_errors.append("Missing META-INF/container.xml.")
            return _build_validation_payload(
                label=label,
                toolchain=toolchain,
                epubcheck=epubcheck,
                package_errors=package_errors,
                package_warnings=package_warnings,
                internal_errors=internal_errors,
                internal_warnings=internal_warnings,
                external_errors=external_errors,
                external_warnings=external_warnings,
                metadata=metadata,
                document_stats=document_stats,
            )

        container_tree = _parse_xml_bytes(
            archive.read("META-INF/container.xml"),
            logical_name="META-INF/container.xml",
            errors=package_errors,
        )
        if container_tree is None:
            return _build_validation_payload(
                label=label,
                toolchain=toolchain,
                epubcheck=epubcheck,
                package_errors=package_errors,
                package_warnings=package_warnings,
                internal_errors=internal_errors,
                internal_warnings=internal_warnings,
                external_errors=external_errors,
                external_warnings=external_warnings,
                metadata=metadata,
                document_stats=document_stats,
            )

        rootfile = container_tree.find(".//container:rootfile", namespaces=CONTAINER_NS)
        if rootfile is None:
            package_errors.append("container.xml does not define a rootfile.")
            return _build_validation_payload(
                label=label,
                toolchain=toolchain,
                epubcheck=epubcheck,
                package_errors=package_errors,
                package_warnings=package_warnings,
                internal_errors=internal_errors,
                internal_warnings=internal_warnings,
                external_errors=external_errors,
                external_warnings=external_warnings,
                metadata=metadata,
                document_stats=document_stats,
            )

        opf_path = (rootfile.get("full-path") or "").strip()
        if not opf_path:
            package_errors.append("container.xml rootfile is missing the OPF path.")
            return _build_validation_payload(
                label=label,
                toolchain=toolchain,
                epubcheck=epubcheck,
                package_errors=package_errors,
                package_warnings=package_warnings,
                internal_errors=internal_errors,
                internal_warnings=internal_warnings,
                external_errors=external_errors,
                external_warnings=external_warnings,
                metadata=metadata,
                document_stats=document_stats,
            )
        if opf_path not in names:
            package_errors.append(f"OPF file missing from archive: {opf_path}")
            return _build_validation_payload(
                label=label,
                toolchain=toolchain,
                epubcheck=epubcheck,
                package_errors=package_errors,
                package_warnings=package_warnings,
                internal_errors=internal_errors,
                internal_warnings=internal_warnings,
                external_errors=external_errors,
                external_warnings=external_warnings,
                metadata=metadata,
                document_stats=document_stats,
            )

        opf_tree = _parse_xml_bytes(
            archive.read(opf_path),
            logical_name=opf_path,
            errors=package_errors,
        )
        if opf_tree is None:
            return _build_validation_payload(
                label=label,
                toolchain=toolchain,
                epubcheck=epubcheck,
                package_errors=package_errors,
                package_warnings=package_warnings,
                internal_errors=internal_errors,
                internal_warnings=internal_warnings,
                external_errors=external_errors,
                external_warnings=external_warnings,
                metadata=metadata,
                document_stats=document_stats,
            )

        metadata = _extract_package_metadata(opf_tree)
        manifest_by_id, manifest_targets, nav_target = _extract_manifest(
            opf_tree,
            opf_path=opf_path,
            archive_names=set(names),
            package_errors=package_errors,
            package_warnings=package_warnings,
            document_stats=document_stats,
        )
        _validate_spine(
            opf_tree,
            manifest_by_id=manifest_by_id,
            manifest_targets=manifest_targets,
            package_errors=package_errors,
            package_warnings=package_warnings,
            document_stats=document_stats,
        )
        if nav_target is None and not any(item["media_type"] == "application/x-dtbncx+xml" for item in manifest_by_id.values()):
            package_errors.append("Package is missing a navigation document or NCX entry.")

        document_index = _index_content_documents(
            archive=archive,
            manifest_targets=manifest_targets,
            internal_errors=internal_errors,
            internal_warnings=internal_warnings,
            external_errors=external_errors,
            external_warnings=external_warnings,
            document_stats=document_stats,
        )
        _validate_internal_links(
            manifest_targets=manifest_targets,
            document_index=document_index,
            internal_errors=internal_errors,
            internal_warnings=internal_warnings,
            document_stats=document_stats,
        )
        _validate_non_linear_spine_reachability(
            opf_tree,
            manifest_by_id=manifest_by_id,
            nav_target=nav_target,
            document_index=document_index,
            internal_errors=internal_errors,
            document_stats=document_stats,
        )

    return _build_validation_payload(
        label=label,
        toolchain=toolchain,
        epubcheck=epubcheck,
        package_errors=package_errors,
        package_warnings=package_warnings,
        internal_errors=internal_errors,
        internal_warnings=internal_warnings,
        external_errors=external_errors,
        external_warnings=external_warnings,
        metadata=metadata,
        document_stats=document_stats,
    )


def build_validation_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    package = result.get("package", {})
    internal = result.get("internal_links", {})
    external = result.get("external_links", {})
    epubcheck = result.get("epubcheck", {})
    lines = [
        f"# EPUB Validation Report: {result.get('epub_path', result.get('label', '<memory>'))}",
        "",
        f"- Overall status: `{summary.get('status', 'unknown')}`",
        f"- Error count: `{summary.get('error_count', 0)}`",
        f"- Warning count: `{summary.get('warning_count', 0)}`",
        f"- EPUBCheck: `{epubcheck.get('status', 'unavailable')}`",
        "",
        "## Package",
        "",
        f"- Status: `{package.get('status', 'unknown')}`",
    ]
    for message in package.get("errors", [])[:25]:
        lines.append(f"- error: {message}")
    for message in package.get("warnings", [])[:25]:
        lines.append(f"- warning: {message}")
    lines.extend(
        [
            "",
            "## Internal Links",
            "",
            f"- Status: `{internal.get('status', 'unknown')}`",
        ]
    )
    for message in internal.get("errors", [])[:25]:
        lines.append(f"- error: {message}")
    for message in internal.get("warnings", [])[:25]:
        lines.append(f"- warning: {message}")
    lines.extend(
        [
            "",
            "## External Links",
            "",
            f"- Status: `{external.get('status', 'unknown')}`",
        ]
    )
    for message in external.get("errors", [])[:25]:
        lines.append(f"- error: {message}")
    for message in external.get("warnings", [])[:25]:
        lines.append(f"- warning: {message}")
    lines.extend(
        [
            "",
            "## Metadata",
            "",
            f"- Title: `{(result.get('metadata') or {}).get('title', '')}`",
            f"- Creator: `{(result.get('metadata') or {}).get('creator', '')}`",
            f"- Language: `{(result.get('metadata') or {}).get('language', '')}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _extract_package_metadata(opf_tree: etree._ElementTree) -> dict[str, str]:
    title = _xpath_string(opf_tree, "string(//*[local-name()='metadata']/*[local-name()='title'][1])")
    creator = _xpath_string(opf_tree, "string(//*[local-name()='metadata']/*[local-name()='creator'][1])")
    language = _xpath_string(opf_tree, "string(//*[local-name()='metadata']/*[local-name()='language'][1])")
    identifier = _xpath_string(opf_tree, "string(//*[local-name()='metadata']/*[local-name()='identifier'][1])")
    return {
        "title": title.strip(),
        "creator": creator.strip(),
        "language": language.strip(),
        "identifier": identifier.strip(),
    }


def _extract_manifest(
    opf_tree: etree._ElementTree,
    *,
    opf_path: str,
    archive_names: set[str],
    package_errors: list[str],
    package_warnings: list[str],
    document_stats: dict[str, int],
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], str | None]:
    opf_dir = PurePosixPath(opf_path).parent
    manifest_by_id: dict[str, dict[str, str]] = {}
    manifest_targets: dict[str, dict[str, str]] = {}
    nav_target: str | None = None
    has_navigation_file = False
    manifest_targets_missing = 0
    manifest_duplicate_ids = 0
    seen_manifest_ids: set[str] = set()
    for item in opf_tree.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
        item_id = (item.get("id") or "").strip()
        href = (item.get("href") or "").strip()
        media_type = (item.get("media-type") or "").strip().lower()
        properties = (item.get("properties") or "").strip()
        if not item_id or not href:
            package_errors.append("Manifest item is missing id or href.")
            continue
        if item_id in seen_manifest_ids:
            package_errors.append(f"Manifest contains duplicate id: {item_id}")
            manifest_duplicate_ids += 1
            continue
        seen_manifest_ids.add(item_id)
        resolved = _normalize_archive_path(str(opf_dir / href))
        entry = {
            "id": item_id,
            "href": href,
            "resolved_path": resolved,
            "media_type": media_type,
            "properties": properties,
        }
        manifest_by_id[item_id] = entry
        manifest_targets[resolved] = entry
        if resolved not in archive_names:
            package_errors.append(f"Manifest target missing from archive: {resolved}")
            manifest_targets_missing += 1
        if "nav" in properties.split():
            nav_target = resolved
        if media_type == "application/x-dtbncx+xml":
            has_navigation_file = True
    if not manifest_by_id:
        package_errors.append("Manifest is empty.")
    if nav_target is None:
        nav_candidates = [
            path
            for path, item in manifest_targets.items()
            if item["media_type"] == "application/xhtml+xml" and PurePosixPath(path).name.lower() == "nav.xhtml"
        ]
        if nav_candidates:
            nav_target = nav_candidates[0]
        else:
            package_warnings.append("Navigation document was not marked with the 'nav' property.")
    document_stats["manifest_item_count"] = len(manifest_by_id)
    document_stats["manifest_duplicate_id_count"] = manifest_duplicate_ids
    document_stats["manifest_targets_missing_count"] = manifest_targets_missing
    document_stats["navigation_document_count"] = 1 if nav_target or has_navigation_file else 0
    return manifest_by_id, manifest_targets, nav_target


def _validate_spine(
    opf_tree: etree._ElementTree,
    *,
    manifest_by_id: dict[str, dict[str, str]],
    manifest_targets: dict[str, dict[str, str]],
    package_errors: list[str],
    package_warnings: list[str],
    document_stats: dict[str, int],
) -> None:
    itemrefs = opf_tree.xpath("//*[local-name()='spine']/*[local-name()='itemref']")
    if not itemrefs:
        package_errors.append("Spine is empty.")
        return
    spine_targets: list[str] = []
    spine_linear_count = 0
    spine_non_linear_count = 0
    spine_unknown_manifest_references = 0
    for itemref in itemrefs:
        idref = (itemref.get("idref") or "").strip()
        if not idref:
            package_errors.append("Spine itemref is missing idref.")
            continue
        manifest_item = manifest_by_id.get(idref)
        if manifest_item is None:
            package_errors.append(f"Spine references unknown manifest id: {idref}")
            spine_unknown_manifest_references += 1
            continue
        if (itemref.get("linear") or "yes").strip().lower() == "no":
            spine_non_linear_count += 1
        else:
            spine_linear_count += 1
        spine_targets.append(manifest_item["resolved_path"])
    if not spine_targets:
        package_errors.append("Spine does not resolve to any content documents.")
    elif len(set(spine_targets)) != len(spine_targets):
        package_warnings.append("Spine contains duplicate reading-order targets.")
    for target in spine_targets:
        if target not in manifest_targets:
            package_errors.append(f"Resolved spine target missing from manifest: {target}")
    document_stats["spine_unknown_manifest_references"] = spine_unknown_manifest_references
    document_stats["spine_item_count"] = len(itemrefs)
    document_stats["spine_linear_item_count"] = spine_linear_count
    document_stats["spine_non_linear_item_count"] = spine_non_linear_count
    document_stats["spine_duplicate_targets"] = len(spine_targets) - len(set(spine_targets))


def _validate_non_linear_spine_reachability(
    opf_tree: etree._ElementTree,
    *,
    manifest_by_id: dict[str, dict[str, str]],
    nav_target: str | None,
    document_index: dict[str, dict[str, Any]],
    internal_errors: list[str],
    document_stats: dict[str, int],
) -> None:
    non_linear_targets: set[str] = set()
    for itemref in opf_tree.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
        if (itemref.get("linear") or "yes").strip().lower() != "no":
            continue
        manifest_item = manifest_by_id.get((itemref.get("idref") or "").strip())
        if not manifest_item or manifest_item.get("media_type") not in XHTML_MIME_TYPES:
            continue
        target = manifest_item["resolved_path"]
        if target == nav_target or PurePosixPath(target).name.lower() in {"nav.xhtml", "cover.xhtml"}:
            continue
        non_linear_targets.add(target)

    document_stats["non_linear_spine_targets"] = len(non_linear_targets)
    if not non_linear_targets:
        return

    reachable_targets: set[str] = set()
    for current_path, payload in document_index.items():
        for ref in payload.get("refs", []):
            value = ref.get("value", "")
            if not value or _is_external_href(value) or value.startswith(("mailto:", "data:")):
                continue
            target_doc, _fragment = _resolve_href_target(current_path, value)
            if target_doc in non_linear_targets:
                reachable_targets.add(target_doc)

    unreachable = sorted(non_linear_targets - reachable_targets)
    document_stats["unreachable_non_linear_spine_targets"] = len(unreachable)
    for target in unreachable:
        internal_errors.append(f"Non-linear spine content is unreachable by hyperlink: {target}")


def _index_content_documents(
    *,
    archive: zipfile.ZipFile,
    manifest_targets: dict[str, dict[str, str]],
    internal_errors: list[str],
    internal_warnings: list[str],
    external_errors: list[str],
    external_warnings: list[str],
    document_stats: dict[str, int],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for path, item in manifest_targets.items():
        if item["media_type"] not in XHTML_MIME_TYPES:
            continue
        xml_tree = _parse_xml_bytes(
            archive.read(path),
            logical_name=path,
            errors=internal_errors,
        )
        if xml_tree is None:
            continue
        document_stats["documents_parsed"] += 1
        ids: list[str] = []
        refs: list[dict[str, str]] = []
        for element in xml_tree.iter():
            element_id = (element.get("id") or "").strip()
            if element_id:
                ids.append(element_id)
            for attr_name in ("href", "src", XLINK_HREF):
                attr_value = (element.get(attr_name) or "").strip()
                if attr_value:
                    refs.append(
                        {
                            "attribute": attr_name,
                            "value": attr_value,
                            "tag": etree.QName(element).localname if isinstance(element.tag, str) else str(element.tag),
                        }
                    )
        duplicate_ids = [name for name, count in Counter(ids).items() if count > 1]
        if duplicate_ids:
            document_stats["documents_with_duplicate_ids"] += 1
            internal_errors.append(f"{path}: duplicate id values found: {', '.join(sorted(duplicate_ids)[:10])}")
        indexed[path] = {"ids": set(ids), "refs": refs}
        for ref in refs:
            value = ref["value"]
            if _is_external_href(value):
                document_stats["external_links_checked"] += 1
                _validate_external_href(path=path, href=value, external_errors=external_errors, external_warnings=external_warnings)
    return indexed


def _validate_internal_links(
    *,
    manifest_targets: dict[str, dict[str, str]],
    document_index: dict[str, dict[str, Any]],
    internal_errors: list[str],
    internal_warnings: list[str],
    document_stats: dict[str, int],
) -> None:
    known_documents = set(manifest_targets)
    for current_path, payload in document_index.items():
        if PurePosixPath(current_path).name.lower() == "nav.xhtml":
            continue
        for ref in payload["refs"]:
            value = ref["value"]
            if not value or _is_external_href(value) or value.startswith("mailto:") or value.startswith("data:"):
                continue
            document_stats["links_checked"] += 1
            target_doc, fragment = _resolve_href_target(current_path, value)
            if fragment:
                document_stats["internal_href_with_fragment_count"] += 1
            else:
                document_stats["internal_href_without_fragment_count"] += 1
            if target_doc not in known_documents:
                internal_errors.append(f"{current_path}: missing target document for {value!r}")
                document_stats["internal_href_missing_document_count"] += 1
                continue
            if fragment:
                target_ids = (document_index.get(target_doc) or {}).get("ids", set())
                if fragment not in target_ids:
                    internal_errors.append(f"{current_path}: fragment #{fragment} not found in {target_doc}")
                    document_stats["internal_href_missing_fragment_count"] += 1
            elif PurePosixPath(target_doc).suffix.lower() not in {
                ".xhtml",
                ".html",
                ".ncx",
                ".css",
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".svg",
                ".otf",
                ".ttf",
                ".woff",
                ".woff2",
            }:
                internal_warnings.append(f"{current_path}: target {value!r} resolves to uncommon resource type.")


def _validate_external_href(
    *,
    path: str,
    href: str,
    external_errors: list[str],
    external_warnings: list[str],
) -> None:
    parsed = rfc3986.uri_reference(href)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.host or "").strip()
    if scheme not in {"http", "https"}:
        external_warnings.append(f"{path}: unsupported external URL scheme for {href!r}")
        return
    if not host:
        external_errors.append(f"{path}: external URL is missing host: {href!r}")
        return
    if _BROKEN_ENCODING_TAIL_RE.search(href):
        external_errors.append(f"{path}: external URL looks truncated by broken percent-encoding: {href!r}")
    if " " in href:
        external_errors.append(f"{path}: external URL contains whitespace: {href!r}")
    try:
        if "." not in host and host.lower() != "localhost" and not _IPV4_RE.match(host):
            external_errors.append(f"{path}: external URL host looks unresolved: {href!r}")
            return
        extracted = _TLD_EXTRACT(host)
        if not extracted.suffix and host.lower() != "localhost" and not _IPV4_RE.match(host):
            external_errors.append(f"{path}: external URL host looks unresolved: {href!r}")
    except Exception as exc:
        external_warnings.append(f"{path}: could not validate external host for {href!r}: {exc}")


def _resolve_href_target(current_path: str, href: str) -> tuple[str, str]:
    if href.startswith("#"):
        return current_path, href[1:]
    target, _, fragment = href.partition("#")
    normalized_target = _normalize_archive_path(str(PurePosixPath(current_path).parent / target))
    return normalized_target, fragment


def _is_external_href(href: str) -> bool:
    lowered = href.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _normalize_archive_path(path: str) -> str:
    pure = PurePosixPath(path)
    parts: list[str] = []
    for part in pure.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts).as_posix()


def _xpath_string(tree: etree._ElementTree, expression: str) -> str:
    value = tree.xpath(expression)
    return str(value) if value is not None else ""


def _parse_xml_bytes(data: bytes, *, logical_name: str, errors: list[str]) -> etree._ElementTree | None:
    parser = etree.XMLParser(recover=False, resolve_entities=False, huge_tree=True)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        errors.append(f"{logical_name}: XML parse failed: {exc}")
        return None
    return root.getroottree()


def _status_from_lists(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "failed"
    if warnings:
        return "passed_with_warnings"
    return "passed"


def _build_validation_payload(
    *,
    label: str,
    toolchain: dict[str, Any],
    epubcheck: dict[str, Any],
    package_errors: list[str],
    package_warnings: list[str],
    internal_errors: list[str],
    internal_warnings: list[str],
    external_errors: list[str],
    external_warnings: list[str],
    metadata: dict[str, Any],
    document_stats: dict[str, int],
) -> dict[str, Any]:
    epubcheck_failed = epubcheck.get("status") == "failed"
    all_errors = list(package_errors) + list(internal_errors) + list(external_errors)
    all_warnings = list(package_warnings) + list(internal_warnings) + list(external_warnings)
    if epubcheck_failed:
        all_errors = list(all_errors) + [f"EPUBCheck failed with {len(epubcheck.get('messages', []) or [])} message(s)."]
    summary_status = "failed" if all_errors else ("passed_with_warnings" if all_warnings or epubcheck.get("status") == "unavailable" else "passed")
    return {
        "label": label,
        "toolchain": toolchain,
        "epubcheck": epubcheck,
        "metadata": metadata,
        "package": {
            "status": _status_from_lists(package_errors, package_warnings),
            "errors": package_errors,
            "warnings": package_warnings,
        },
        "internal_links": {
            "status": _status_from_lists(internal_errors, internal_warnings),
            "errors": internal_errors,
            "warnings": internal_warnings,
        },
        "external_links": {
            "status": _status_from_lists(external_errors, external_warnings),
            "errors": external_errors,
            "warnings": external_warnings,
        },
        "document_stats": document_stats,
        "summary": {
            "status": summary_status,
            "error_count": len(all_errors),
            "warning_count": len(all_warnings),
            "epubcheck_status": epubcheck.get("status", "unavailable"),
        },
    }
