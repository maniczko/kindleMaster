from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lxml import etree
from epub_premium_scoring import score_epub_premium_quality
from epub_validation import (
    _normalize_archive_path,
    _parse_xml_bytes,
    _validate_external_href,
    _validate_non_linear_spine_reachability,
    _validate_spine,
    build_validation_markdown,
    validate_epub_bytes,
    validate_epub_path,
)


def _build_epub(
    *,
    chapter_body: str,
    nav_body: str | None = None,
    extra_manifest_items: str = "",
    extra_spine_items: str = "",
    include_nav: bool = True,
    extra_files: dict[str, str] | None = None,
) -> bytes:
    container_xml = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    content_opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Validator Probe</dc:title>
    <dc:creator>Codex</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    {"<item id=\"nav\" href=\"nav.xhtml\" media-type=\"application/xhtml+xml\" properties=\"nav\"/>" if include_nav else ""}
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    {extra_manifest_items}
  </manifest>
  <spine>
    <itemref idref="chapter"/>
    {extra_spine_items}
  </spine>
</package>
"""
    nav_links = nav_body or '<ol><li><a href="chapter.xhtml#intro">Intro</a></li></ol>'
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>TOC</title></head>
  <body>
    <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
      {nav_links}
    </nav>
  </body>
</html>
""".format(nav_links=nav_links)
    chapter_xhtml = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter</title></head>
  <body>{chapter_body}</body>
</html>
"""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container_xml)
        archive.writestr("OEBPS/content.opf", content_opf)
        archive.writestr("OEBPS/nav.xhtml", nav_xhtml)
        archive.writestr("OEBPS/chapter.xhtml", chapter_xhtml)
        for name, content in (extra_files or {}).items():
            archive.writestr(f"OEBPS/{name}", content)
    return buffer.getvalue()


def _container_xml(full_path: str = "OEBPS/content.opf") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{full_path}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _content_opf(
    *,
    include_nav: bool = True,
    manifest_items: str = "",
    spine_items: str = '    <itemref idref="chapter"/>',
) -> str:
    nav_item = (
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        if include_nav
        else ""
    )
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Validator Probe</dc:title>
    <dc:creator>Codex</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    {nav_item}
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    {manifest_items}
  </manifest>
  <spine>
{spine_items}
  </spine>
</package>
"""


def _build_custom_epub(entries: list[tuple[str, str, int | None]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for entry in entries:
            if len(entry) == 3:
                name, content, compress_type = entry
            else:
                name, content = entry
                compress_type = zipfile.ZIP_STORED
            archive.writestr(name, content, compress_type=compress_type)
    return buffer.getvalue()


class TestEpubValidation(unittest.TestCase):
    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_passes_on_minimal_valid_epub(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(chapter_body='<h1 id="intro">Intro</h1><p><a href="#intro">Jump</a></p>')

        result = validate_epub_bytes(epub_bytes, label="valid.epub")

        self.assertEqual(result["summary"]["status"], "passed")
        self.assertEqual(result["package"]["status"], "passed")
        self.assertEqual(result["internal_links"]["status"], "passed")

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_missing_fragment(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(chapter_body='<h1 id="intro">Intro</h1><p><a href="#missing">Broken</a></p>')

        result = validate_epub_bytes(epub_bytes, label="broken_fragment.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("fragment #missing" in message for message in result["internal_links"]["errors"]))
        self.assertEqual(result["document_stats"]["internal_href_with_fragment_count"], 1)
        self.assertEqual(result["document_stats"]["internal_href_without_fragment_count"], 0)
        self.assertEqual(result["document_stats"]["internal_href_missing_document_count"], 0)
        self.assertEqual(result["document_stats"]["internal_href_missing_fragment_count"], 1)

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_counts_href_targets_with_and_without_fragments(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body=(
                '<h1 id="intro">Intro</h1>'
                '<p><a href="#intro">Self section</a>'
                '<a href="chapter.xhtml#intro">Explicit same doc section</a>'
                '<a href="chapter.xhtml">Document only</a>'
                '<a href="chapter.xhtml#missing">Broken fragment</a></p>'
            ),
            nav_body="<ol></ol>",
        )

        result = validate_epub_bytes(epub_bytes, label="href_counts.epub")

        self.assertEqual(result["document_stats"]["links_checked"], 4)
        self.assertEqual(result["document_stats"]["internal_href_with_fragment_count"], 3)
        self.assertEqual(result["document_stats"]["internal_href_without_fragment_count"], 1)
        self.assertEqual(result["document_stats"]["internal_href_missing_document_count"], 0)
        self.assertEqual(result["document_stats"]["internal_href_missing_fragment_count"], 1)

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_duplicate_ids_as_release_blocker(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><h2 id="intro">Duplicate</h2><p>Body.</p>'
        )

        result = validate_epub_bytes(epub_bytes, label="duplicate_id.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["documents_with_duplicate_ids"], 1)
        self.assertTrue(any("duplicate id values found" in message for message in result["internal_links"]["errors"]))

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_duplicate_manifest_id(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p>Body.</p>',
            extra_manifest_items='<item id="chapter" href="duplicate.xhtml" media-type="application/xhtml+xml"/>',
        )

        result = validate_epub_bytes(epub_bytes, label="duplicate_manifest.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["manifest_duplicate_id_count"], 1)
        self.assertTrue(any("Manifest contains duplicate id: chapter" in message for message in result["package"]["errors"]))

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_unknown_spine_manifest_references(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p>Body.</p>',
            extra_spine_items='<itemref idref="missing-id"/>',
        )

        result = validate_epub_bytes(epub_bytes, label="unknown_spine_ref.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["spine_unknown_manifest_references"], 1)
        self.assertTrue(
            any("Spine references unknown manifest id: missing-id" in message for message in result["package"]["errors"])
        )

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_unresolved_external_host(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p><a href="https://the">Broken URL</a></p>'
        )

        result = validate_epub_bytes(epub_bytes, label="broken_external.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("host looks unresolved" in message for message in result["external_links"]["errors"]))

    def test_validate_epub_bytes_flags_uncommon_internal_resource_type(self) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p><a href="notes.txt">Download notes</a></p>',
            extra_manifest_items='<item id="notes" href="notes.txt" media-type="text/plain"/>',
            extra_files={"notes.txt": "text resource"},
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes, label="uncommon_internal_resource.epub")

        self.assertEqual(result["internal_links"]["status"], "passed_with_warnings")
        self.assertTrue(any("uncommon resource type" in message for message in result["internal_links"]["warnings"]))
        self.assertEqual(result["summary"]["warning_count"], 1)

    def test_validate_epub_path_uses_source_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(_build_epub(chapter_body='<h1 id="intro">Intro</h1>'))
            tmp_path = Path(tmp.name)

        try:
            with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
                result = validate_epub_path(tmp_path)
            self.assertEqual(result["epub_path"], str(tmp_path))
            self.assertEqual(result["metadata"]["title"], "Validator Probe")
        finally:
            tmp_path.unlink()

    def test_validate_epub_bytes_rejects_bad_zip(self) -> None:
        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(b"not-an-epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("valid ZIP archive" in message for message in result["package"]["errors"]))

    def test_validate_epub_bytes_flags_empty_archive(self) -> None:
        with io.BytesIO() as buffer:
            with zipfile.ZipFile(buffer, "w"):
                pass
            epub_bytes = buffer.getvalue()

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("EPUB archive is empty.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_wrong_zip_structure(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("First ZIP entry must be 'mimetype'." in message for message in result["package"]["errors"]))
        self.assertTrue(any("Missing 'mimetype' file." in message for message in result["package"]["errors"]))

    def test_validate_epub_bytes_flags_wrong_mimetype_value(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "text/plain", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("Unexpected mimetype" in message for message in result["package"]["errors"]))

    def test_validate_epub_bytes_flags_compressed_mimetype_entry(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_DEFLATED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["package"]["status"], "passed_with_warnings")
        self.assertTrue(any("mimetype' entry should be stored without compression" in message for message in result["package"]["warnings"]))

    def test_validate_epub_bytes_flags_missing_container(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("Missing META-INF/container.xml.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_invalid_container_xml(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", "<container><rootfile", zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("XML parse failed" in message for message in result["package"]["errors"]))

    def test_validate_epub_bytes_flags_container_without_rootfile(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", "<?xml version='1.0'?><container></container>", zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("container.xml does not define a rootfile.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_missing_opf_path(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(""), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("container.xml rootfile is missing the OPF path.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_missing_opf_file(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml("OEBPS/missing.opf"), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("OPF file missing from archive: OEBPS/missing.opf", result["package"]["errors"])

    def test_validate_epub_bytes_flags_invalid_opf_xml(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", "<package><metadata></package", zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("XML parse failed" in message for message in result["package"]["errors"]))

    def test_validate_epub_bytes_flags_no_navigation_document(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(include_nav=False), zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("Navigation document was not marked with the 'nav' property." in message for message in result["package"]["warnings"]))
        self.assertTrue(any("Package is missing a navigation document or NCX entry." in message for message in result["package"]["errors"]))

    def test_validate_epub_bytes_flags_ncx_navigation_document(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                (
                    "OEBPS/content.opf",
                    _content_opf(
                        include_nav=False,
                        manifest_items='<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                    ),
                    zipfile.ZIP_STORED,
                ),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
                ("OEBPS/toc.ncx", "<ncx></ncx>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "passed_with_warnings")
        self.assertTrue(any("Navigation document was not marked with the 'nav' property." in message for message in result["package"]["warnings"]))

    def test_validate_epub_bytes_flags_manifest_item_missing_id(self) -> None:
        malformed_opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
  </metadata>
  <manifest>
    <item href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", malformed_opf, zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("Manifest item is missing id or href.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_manifest_target_missing(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                (
                    "OEBPS/content.opf",
                    _content_opf(manifest_items='<item id="missing" href="missing.txt" media-type="text/plain"/>'),
                    zipfile.ZIP_STORED,
                ),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["manifest_targets_missing_count"], 1)

    def test_validate_epub_bytes_flags_empty_spine(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(spine_items=""), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("Spine is empty.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_spine_missing_idref(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(spine_items='    <itemref/>'), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertIn("Spine itemref is missing idref.", result["package"]["errors"])

    def test_validate_epub_bytes_flags_spine_duplicate_targets(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                (
                    "OEBPS/content.opf",
                    _content_opf(spine_items='    <itemref idref="chapter"/>\n    <itemref idref="chapter"/>'),
                    zipfile.ZIP_STORED,
                ),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter</h1></body></html>", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["package"]["status"], "passed_with_warnings")
        self.assertIn("Spine contains duplicate reading-order targets.", result["package"]["warnings"])
        self.assertEqual(result["document_stats"]["spine_duplicate_targets"], 1)

    def test_validate_epub_bytes_flags_missing_internal_target_document(self) -> None:
        epub_bytes = _build_epub(chapter_body='<a href="missing.xhtml">Missing target</a>')

        result = validate_epub_bytes(epub_bytes, label="missing_target.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["internal_href_missing_document_count"], 1)
        self.assertTrue(any("missing target document for 'missing.xhtml'" in message for message in result["internal_links"]["errors"]))

    def test_validate_epub_bytes_flags_invalid_xhtml(self) -> None:
        epub_bytes = _build_custom_epub(
            [
                ("mimetype", "application/epub+zip", zipfile.ZIP_STORED),
                ("META-INF/container.xml", _container_xml(), zipfile.ZIP_STORED),
                ("OEBPS/content.opf", _content_opf(), zipfile.ZIP_STORED),
                ("OEBPS/nav.xhtml", "<html><body>nav</body></html>", zipfile.ZIP_STORED),
                ("OEBPS/chapter.xhtml", "<html><body><h1>chapter", zipfile.ZIP_STORED),
            ]
        )

        with patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []}):
            result = validate_epub_bytes(epub_bytes)

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("XML parse failed" in message for message in result["internal_links"]["errors"]))

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_external_href_flags_unusual_host_and_whitespace(self, _mock_epubcheck) -> None:
        external_errors: list[str] = []
        external_warnings: list[str] = []
        _validate_external_href(
            path="OEBPS/chapter.xhtml",
            href="https://example.invalid/search?x=1",
            external_errors=external_errors,
            external_warnings=external_warnings,
        )
        self.assertTrue(any("host looks unresolved" in message for message in external_errors))

        external_errors = []
        external_warnings = []
        _validate_external_href(
            path="OEBPS/chapter.xhtml",
            href="https://example.com/has space.html",
            external_errors=external_errors,
            external_warnings=external_warnings,
        )
        self.assertTrue(any("contains whitespace" in message for message in external_errors))

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_external_href_flags_tldextract_failure(self, _mock_epubcheck) -> None:
        with patch("epub_validation._TLD_EXTRACT", side_effect=RuntimeError("resolver-bad")):
            external_errors: list[str] = []
            external_warnings: list[str] = []
            _validate_external_href(
                path="OEBPS/chapter.xhtml",
                href="https://example.com/ok",
                external_errors=external_errors,
                external_warnings=external_warnings,
            )
            self.assertTrue(any("could not validate external host" in message for message in external_warnings))

    def test_validate_non_linear_spine_reachability_skips_nav_and_cover_targets(self) -> None:
        opf_xml = """<package xmlns="http://www.idpf.org/2007/opf"><spine><itemref idref="nav" linear="no"/><itemref idref="cover" linear="no"/></spine></package>"""
        opf_tree = etree.fromstring(opf_xml.encode("utf-8"), parser=etree.XMLParser(recover=False, resolve_entities=False, huge_tree=True))

        internal_errors: list[str] = []
        document_stats = {
            "non_linear_spine_targets": 0,
            "unreachable_non_linear_spine_targets": 0,
            "documents_parsed": 0,
            "documents_with_duplicate_ids": 0,
            "manifest_duplicate_id_count": 0,
            "manifest_item_count": 0,
            "manifest_targets_missing_count": 0,
            "navigation_document_count": 0,
            "spine_item_count": 0,
            "spine_linear_item_count": 0,
            "spine_non_linear_item_count": 0,
            "spine_duplicate_targets": 0,
            "spine_unknown_manifest_references": 0,
            "links_checked": 0,
            "internal_href_with_fragment_count": 0,
            "internal_href_without_fragment_count": 0,
            "internal_href_missing_document_count": 0,
            "internal_href_missing_fragment_count": 0,
            "external_links_checked": 0,
        }

        _validate_non_linear_spine_reachability(
            opf_tree,
            manifest_by_id={
                "nav": {"id": "nav", "href": "nav.xhtml", "resolved_path": "OEBPS/nav.xhtml", "media_type": "application/xhtml+xml", "properties": "nav"},
                "cover": {"id": "cover", "href": "cover.xhtml", "resolved_path": "OEBPS/cover.xhtml", "media_type": "application/xhtml+xml", "properties": ""},
            },
            nav_target=None,
            document_index={
                "OEBPS/nav.xhtml": {"refs": [{"value": "chapter.xhtml#intro"}]},
                "OEBPS/cover.xhtml": {"refs": []},
            },
            internal_errors=internal_errors,
            document_stats=document_stats,
        )

        self.assertEqual(document_stats["non_linear_spine_targets"], 0)
        self.assertEqual(internal_errors, [])

    def test_normalize_archive_path_collapses_dot_segments(self) -> None:
        self.assertEqual(_normalize_archive_path("./OEBPS/../OEBPS/chapter.xhtml"), "OEBPS/chapter.xhtml")
        self.assertEqual(_normalize_archive_path("OEBPS/chapter/.././section.xhtml"), "OEBPS/section.xhtml")

    def test_parse_xml_bytes_returns_none_for_invalid_xml(self) -> None:
        errors: list[str] = []
        parsed = _parse_xml_bytes(b"<package><metadata>", logical_name="broken.xml", errors=errors)
        self.assertIsNone(parsed)
        self.assertTrue(any("XML parse failed" in message for message in errors))

    def test_validate_spine_reports_missing_manifest_target_in_reference(self) -> None:
        opf_xml = """<package xmlns="http://www.idpf.org/2007/opf">
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>"""
        opf_tree = etree.fromstring(opf_xml.encode("utf-8"), parser=etree.XMLParser(recover=False, resolve_entities=False, huge_tree=True))
        package_errors: list[str] = []
        package_warnings: list[str] = []
        document_stats = {
            "documents_parsed": 0,
            "documents_with_duplicate_ids": 0,
            "manifest_duplicate_id_count": 0,
            "manifest_targets_missing_count": 0,
            "navigation_document_count": 0,
            "spine_item_count": 0,
            "spine_linear_item_count": 0,
            "spine_non_linear_item_count": 0,
            "spine_duplicate_targets": 0,
            "spine_unknown_manifest_references": 0,
            "non_linear_spine_targets": 0,
            "unreachable_non_linear_spine_targets": 0,
            "links_checked": 0,
            "internal_href_with_fragment_count": 0,
            "internal_href_without_fragment_count": 0,
            "internal_href_missing_document_count": 0,
            "internal_href_missing_fragment_count": 0,
            "external_links_checked": 0,
        }

        _validate_spine(
            opf_tree,
            manifest_by_id={"chapter": {"id": "chapter", "href": "chapter.xhtml", "resolved_path": "OEBPS/chapter.xhtml", "media_type": "application/xhtml+xml", "properties": ""}},
            manifest_targets={},
            package_errors=package_errors,
            package_warnings=package_warnings,
            document_stats=document_stats,
        )

        self.assertTrue(any("Resolved spine target missing from manifest" in message for message in package_errors))

    @patch("epub_validation.run_epubcheck", return_value={"status": "failed", "tool": "epubcheck", "messages": ["ERR"]})
    def test_build_validation_markdown_reports_sections(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(chapter_body='<a href="https://broken.example">Link</a>')
        result = validate_epub_bytes(epub_bytes, label="report.epub")
        markdown_text = build_validation_markdown(result)

        self.assertIn("# EPUB Validation Report: report.epub", markdown_text)
        self.assertIn("## Package", markdown_text)
        self.assertIn("## Internal Links", markdown_text)
        self.assertIn("## External Links", markdown_text)
        self.assertIn("## Metadata", markdown_text)
        self.assertIn("Overall status", markdown_text)

    @patch("epub_validation.run_epubcheck", return_value={"status": "failed", "tool": "epubcheck", "messages": ["ERR1", "ERR2"]})
    def test_validate_epub_bytes_includes_epubcheck_failure_in_summary(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(chapter_body='<h1 id="intro">Intro</h1>')
        result = validate_epub_bytes(epub_bytes, label="epubcheck_fail.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["summary"]["error_count"], 1)
        self.assertEqual(result["summary"]["epubcheck_status"], "failed")

    def test_validate_external_href_flags_unsupported_scheme(self) -> None:
        external_errors: list[str] = []
        external_warnings: list[str] = []
        _validate_external_href(
            path="OEBPS/chapter.xhtml",
            href="ftp://example.com/file.html",
            external_errors=external_errors,
            external_warnings=external_warnings,
        )
        self.assertEqual(external_errors, [])
        self.assertTrue(any("unsupported external URL scheme" in message for message in external_warnings))

    def test_validate_external_href_flags_missing_host_and_encoding_tail(self) -> None:
        external_errors: list[str] = []
        external_warnings: list[str] = []
        _validate_external_href(
            path="OEBPS/chapter.xhtml",
            href="https:///notes.html",
            external_errors=external_errors,
            external_warnings=external_warnings,
        )
        self.assertEqual(external_warnings, [])
        self.assertTrue(any("external URL is missing host" in message for message in external_errors))

        external_errors = []
        external_warnings = []
        _validate_external_href(
            path="OEBPS/chapter.xhtml",
            href="https://example.com/page%",
            external_errors=external_errors,
            external_warnings=external_warnings,
        )
        self.assertTrue(any("broken percent-encoding" in message for message in external_errors))
        self.assertEqual(external_warnings, [])

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_unreachable_non_linear_spine_content(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p>Body.</p>',
            extra_manifest_items='<item id="extra" href="extra.xhtml" media-type="application/xhtml+xml"/>',
            extra_spine_items='<itemref idref="extra" linear="no"/>',
            extra_files={
                "extra.xhtml": (
                    "<?xml version='1.0' encoding='utf-8'?>"
                    "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1>Extra</h1></body></html>"
                )
            },
        )

        result = validate_epub_bytes(epub_bytes, label="unreachable_non_linear.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["unreachable_non_linear_spine_targets"], 1)
        self.assertTrue(
            any("Non-linear spine content is unreachable" in message for message in result["internal_links"]["errors"])
        )

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_allows_linked_non_linear_spine_content_from_main_doc(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p><a href="extra.xhtml#extra">Open extra</a></p>',
            nav_body="<ol></ol>",
            extra_manifest_items='<item id="extra" href="extra.xhtml" media-type="application/xhtml+xml"/>',
            extra_spine_items='<itemref idref="extra" linear="no"/>',
            extra_files={
                "extra.xhtml": (
                    "<?xml version='1.0' encoding='utf-8'?>"
                    "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1 id='extra'>Extra</h1></body></html>"
                )
            },
        )

        result = validate_epub_bytes(epub_bytes, label="linked_non_linear_from_main.doc")

        self.assertEqual(result["summary"]["status"], "passed")
        self.assertEqual(result["document_stats"]["non_linear_spine_targets"], 1)
        self.assertEqual(result["document_stats"]["unreachable_non_linear_spine_targets"], 0)

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_allows_linked_non_linear_spine_content(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p>Body.</p>',
            nav_body='<ol><li><a href="chapter.xhtml#intro">Intro</a></li><li><a href="extra.xhtml#extra">Extra</a></li></ol>',
            extra_manifest_items='<item id="extra" href="extra.xhtml" media-type="application/xhtml+xml"/>',
            extra_spine_items='<itemref idref="extra" linear="no"/>',
            extra_files={
                "extra.xhtml": (
                    "<?xml version='1.0' encoding='utf-8'?>"
                    "<html xmlns='http://www.w3.org/1999/xhtml'><body><h1 id='extra'>Extra</h1></body></html>"
                )
            },
        )

        result = validate_epub_bytes(epub_bytes, label="linked_non_linear.epub")

        self.assertEqual(result["summary"]["status"], "passed")
        self.assertEqual(result["document_stats"]["non_linear_spine_targets"], 1)
        self.assertEqual(result["document_stats"]["unreachable_non_linear_spine_targets"], 0)

    def test_strict_premium_score_caps_epubcheck_opf096_failures(self) -> None:
        epub_bytes = _build_epub(chapter_body='<h1 id="intro">Intro</h1><p>Clean body text.</p>')

        result = score_epub_premium_quality(
            epub_bytes,
            epubcheck={
                "status": "failed",
                "tool": "epubcheck",
                "messages": [
                    "ERROR(OPF-096): validation.epub/OEBPS/content.opf(20,10): "
                    "Non-linear content document is unreachable from the navigation.",
                ],
            },
        )

        self.assertFalse(result["technical_valid"])
        self.assertLessEqual(result["premium_score"], 4.5)
        self.assertIn("epubcheck_failed", [issue["code"] for issue in result["issues"]])
        self.assertIn("epubcheck_non_linear_unreachable", [issue["code"] for issue in result["issues"]])
