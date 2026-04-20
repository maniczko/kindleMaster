import io
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from epub_release_pipeline import ReleasePipelineConfig, run_release_pipeline


class EpubReleasePipelineTests(TestCase):
    def _build_epub_bytes(self, files: dict[str, str | bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            for path, payload in files.items():
                if path == "mimetype":
                    continue
                data = payload.encode("utf-8") if isinstance(payload, str) else payload
                archive.writestr(path, data)
        return buffer.getvalue()

    def test_run_release_pipeline_writes_expected_reports(self):
        container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        baseline_opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">legacy-id</dc:identifier>
    <dc:title>Emvc</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>python-docx</dc:creator>
    <dc:description></dc:description>
  </metadata>
  <manifest>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="nav" linear="no"/>
  </spine>
</package>
"""
        baseline_chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Emvc</title></head>
  <body>
    <h1>Material sponsorowany - R4</h1>
    <h1>Raport finalny</h1>
    <p>Treść.</p>
  </body>
</html>
"""
        final_opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" prefix="dcterms: http://purl.org/dc/terms/">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:12345678-1234-1234-1234-123456789abc</dc:identifier>
    <dc:title>Raport finalny</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Anna Nowak</dc:creator>
    <dc:description>Opis publikacji.</dc:description>
    <meta xmlns="http://www.idpf.org/2007/opf" property="dcterms:modified">2026-04-18T10:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="nav" linear="no"/>
  </spine>
</package>
"""
        final_chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="pl" xml:lang="pl">
  <head><title>Raport finalny</title></head>
  <body>
    <h1 id="raport-finalny">Raport finalny</h1>
    <h2 id="sekcja-a">Sekcja A</h2>
    <p>Treść.</p>
  </body>
</html>
"""
        final_nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="chapter_001.xhtml#raport-finalny">Raport finalny</a></li>
        <li><a href="chapter_001.xhtml#sekcja-a">Sekcja A</a></li>
      </ol>
    </nav>
  </body>
</html>
"""
        toc_ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Raport finalny</text></docTitle>
  <navMap></navMap>
</ncx>
"""

        baseline_epub = self._build_epub_bytes(
            {
                "META-INF/container.xml": container,
                "EPUB/content.opf": baseline_opf,
                "EPUB/chapter_001.xhtml": baseline_chapter,
                "EPUB/nav.xhtml": final_nav,
                "EPUB/toc.ncx": toc_ncx,
            }
        )
        final_epub = self._build_epub_bytes(
            {
                "META-INF/container.xml": container,
                "EPUB/content.opf": final_opf,
                "EPUB/chapter_001.xhtml": final_chapter,
                "EPUB/nav.xhtml": final_nav,
                "EPUB/toc.ncx": toc_ncx,
            }
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_epub = root / "input.epub"
            input_epub.write_bytes(baseline_epub)

            with patch(
                "epub_release_pipeline.finalize_epub_for_kindle",
                return_value=(
                    final_epub,
                    {
                        "entries_rebuilt": 3,
                        "review_entry_count": 0,
                        "unresolved_fragment_count": 0,
                    },
                ),
            ):
                with patch(
                    "epub_release_pipeline.run_epubcheck",
                    return_value={"status": "passed", "tool": "epubcheck", "messages": []},
                ):
                    result = run_release_pipeline(
                        ReleasePipelineConfig(
                            input_epub=input_epub,
                            output_dir=root / "output",
                            reports_dir=root / "reports",
                            title="Raport finalny",
                            author="Anna Nowak",
                            language="pl",
                        )
                    )

            self.assertEqual(result["release_decision"]["decision"], "pass")
            self.assertTrue((root / "output" / "final.epub").exists())
            self.assertTrue((root / "reports" / "metadata_diff.json").exists())
            self.assertTrue((root / "reports" / "heading_decisions.json").exists())
            self.assertTrue((root / "reports" / "toc_map.json").exists())
            self.assertTrue((root / "reports" / "structural_integrity.json").exists())
            self.assertTrue((root / "reports" / "epubcheck.json").exists())
            self.assertTrue((root / "reports" / "release_report.md").exists())
            self.assertTrue((root / "reports" / "manual_review_queue.md").exists())

            metadata_diff = json.loads((root / "reports" / "metadata_diff.json").read_text(encoding="utf-8"))
            heading_decisions = json.loads((root / "reports" / "heading_decisions.json").read_text(encoding="utf-8"))
            toc_map = json.loads((root / "reports" / "toc_map.json").read_text(encoding="utf-8"))

            self.assertEqual(metadata_diff["after"]["title"], "Raport finalny")
            self.assertEqual(metadata_diff["after"]["creator"], "Anna Nowak")
            self.assertEqual(toc_map["summary"]["toc_nav_count"], 1)
            self.assertEqual(heading_decisions["summary"]["removed_count"], 1)

    def test_run_release_pipeline_marks_fail_when_epubcheck_fails(self):
        container = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">legacy-id</dc:identifier>
    <dc:title>Raport</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Anna Nowak</dc:creator>
  </metadata>
  <manifest>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="nav" linear="no"/>
  </spine>
</package>
"""
        chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport</title></head>
  <body><h1 id="raport">Raport</h1></body>
</html>
"""
        nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml#raport">Raport</a></li></ol></nav></body>
</html>
"""
        toc_ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Raport</text></docTitle>
  <navMap></navMap>
</ncx>
"""
        epub_bytes = self._build_epub_bytes(
            {
                "META-INF/container.xml": container,
                "EPUB/content.opf": opf,
                "EPUB/chapter_001.xhtml": chapter,
                "EPUB/nav.xhtml": nav,
                "EPUB/toc.ncx": toc_ncx,
            }
        )

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_epub = root / "input.epub"
            input_epub.write_bytes(epub_bytes)
            with patch(
                "epub_release_pipeline.finalize_epub_for_kindle",
                return_value=(epub_bytes, {"entries_rebuilt": 0, "review_entry_count": 0, "unresolved_fragment_count": 0}),
            ):
                with patch(
                    "epub_release_pipeline.run_epubcheck",
                    return_value={"status": "failed", "tool": "epubcheck", "messages": ["Broken package"]},
                ):
                    result = run_release_pipeline(
                        ReleasePipelineConfig(
                            input_epub=input_epub,
                            output_dir=root / "output",
                            reports_dir=root / "reports",
                        )
                    )

            self.assertEqual(result["release_decision"]["decision"], "fail")
            release_report = (root / "reports" / "release_report.md").read_text(encoding="utf-8")
            self.assertIn("EPUBCheck", release_report)
            self.assertIn("fail", release_report)
