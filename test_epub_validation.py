from __future__ import annotations

import io
import unittest
import zipfile
from unittest.mock import patch

from epub_premium_scoring import score_epub_premium_quality
from epub_validation import validate_epub_bytes


def _build_epub(
    *,
    chapter_body: str,
    nav_body: str | None = None,
    extra_manifest_items: str = "",
    extra_spine_items: str = "",
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
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
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
    def test_validate_epub_bytes_flags_duplicate_ids_as_release_blocker(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><h2 id="intro">Duplicate</h2><p>Body.</p>'
        )

        result = validate_epub_bytes(epub_bytes, label="duplicate_id.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertEqual(result["document_stats"]["documents_with_duplicate_ids"], 1)
        self.assertTrue(any("duplicate id values found" in message for message in result["internal_links"]["errors"]))

    @patch("epub_validation.run_epubcheck", return_value={"status": "passed", "tool": "epubcheck", "messages": []})
    def test_validate_epub_bytes_flags_unresolved_external_host(self, _mock_epubcheck) -> None:
        epub_bytes = _build_epub(
            chapter_body='<h1 id="intro">Intro</h1><p><a href="https://the">Broken URL</a></p>'
        )

        result = validate_epub_bytes(epub_bytes, label="broken_external.epub")

        self.assertEqual(result["summary"]["status"], "failed")
        self.assertTrue(any("host looks unresolved" in message for message in result["external_links"]["errors"]))

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
