from __future__ import annotations

import io
import unittest
import zipfile
from unittest.mock import patch

from epub_validation import validate_epub_bytes


def _build_epub(*, chapter_body: str) -> bytes:
    container_xml = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    content_opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Validator Probe</dc:title>
    <dc:creator>Codex</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    nav_xhtml = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>TOC</title></head>
  <body>
    <nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">
      <ol><li><a href="chapter.xhtml#intro">Intro</a></li></ol>
    </nav>
  </body>
</html>
"""
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

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_unresolved_external_host(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p><a href="https://the">Broken URL</a></p>'
        )

        result = validate_epub_bytes(epub_bytes, label="broken_external.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("host looks unresolved" in message for message in result["external_links"]["errors"]))
