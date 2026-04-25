import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lxml import etree

from epub_quality_recovery import _evaluate_gate_c, run_epub_publishing_quality_recovery


class EpubQualityRecoveryTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, bytes | str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                payload = content.encode("utf-8") if isinstance(content, str) else content
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, payload, compress_type=compress_type)
        return output.getvalue()

    def test_gate_c_treats_h1_count_anomalies_as_review_not_release_blockers(self):
        result = _evaluate_gate_c(
            {"summary": {"removed_count": 1, "recovered_count": 1}, "manual_review": []},
            {
                "headings": {
                    "chapter_001.xhtml": [],
                    "chapter_002.xhtml": [
                        {"level": 1, "text": "Main Section"},
                        {"level": 1, "text": "Nested Section"},
                    ],
                    "cover.xhtml": [],
                }
            },
        )

        self.assertEqual(result["status"], "pass_with_review")
        self.assertEqual(result["blockers"], [])
        self.assertTrue(any("no heading elements" in warning for warning in result["warnings"]))
        self.assertTrue(any("2 H1 headings" in warning for warning in result["warnings"]))

    def test_gate_c_still_blocks_suspicious_or_missing_heading_structure(self):
        suspicious_result = _evaluate_gate_c(
            {"summary": {"removed_count": 1, "recovered_count": 1}, "manual_review": []},
            {"headings": {"chapter_001.xhtml": [{"level": 1, "text": "Material sponsorowany - R4"}]}},
        )
        empty_result = _evaluate_gate_c(
            {"summary": {"removed_count": 1, "recovered_count": 1}, "manual_review": []},
            {"headings": {"chapter_001.xhtml": [], "cover.xhtml": []}},
        )

        self.assertEqual(suspicious_result["status"], "fail")
        self.assertTrue(any("suspicious headings" in blocker for blocker in suspicious_result["blockers"]))
        self.assertEqual(empty_result["status"], "fail")
        self.assertIn("No heading structure detected in content documents.", empty_result["blockers"])

    def test_recovery_pipeline_writes_reports_and_final_epub(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid">legacy-id</dc:identifier>
    <dc:title>python-docx</dc:title>
    <dc:language>en</dc:language>
    <dc:creator id="creator">Technical Converter</dc:creator>
    <dc:description></dc:description>
  </metadata>
  <manifest>
    <item id="style" href="style/default.css" media-type="text/css"/>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="nav"/>
  </spine>
</package>
"""
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Emvc</title></head>
  <body>
    <section>
      <h1>Raport interoperacyjności</h1>
      <p class="author">Anna Nowak</p>
      <p>Ten raport opisuje architekturę procesu oraz zależności systemowe potrzebne do przygotowania finalnego wydania EPUB.</p>
      <h2>Architektura</h2>
      <p>Opis sekcji architektury.</p>
    </section>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Legacy label</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap><navPoint id="legacy" playOrder="1"><navLabel><text>Legacy label</text></navLabel><content src="chapter_001.xhtml"/></navPoint></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
            }
        )

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.epub"
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            source_path.write_bytes(epub_bytes)

            with patch(
                "epub_quality_recovery.run_epubcheck",
                return_value={"status": "passed", "tool": "epubcheck", "messages": []},
            ):
                result = run_epub_publishing_quality_recovery(
                    source_path,
                    output_dir=output_dir,
                    reports_dir=reports_dir,
                    expected_language="pl",
                )

            self.assertIn(result["decision"], {"pass", "pass_with_review"})
            final_epub = output_dir / "final.epub"
            self.assertTrue(final_epub.exists())
            self.assertTrue((reports_dir / "metadata_diff.json").exists())
            self.assertTrue((reports_dir / "heading_decisions.json").exists())
            self.assertTrue((reports_dir / "toc_map.json").exists())
            self.assertTrue((reports_dir / "structural_integrity.json").exists())
            self.assertTrue((reports_dir / "epubcheck.json").exists())
            self.assertTrue((reports_dir / "release_report.md").exists())
            self.assertTrue((reports_dir / "manual_review_queue.md").exists())

            with zipfile.ZipFile(io.BytesIO(final_epub.read_bytes()), "r") as archive:
                archive.extractall(Path(temp_dir) / "unpacked")

            opf_tree = etree.parse(str(Path(temp_dir) / "unpacked" / "EPUB" / "content.opf"))
            root = opf_tree.getroot()
            ns = {
                "opf": "http://www.idpf.org/2007/opf",
                "dc": "http://purl.org/dc/elements/1.1/",
            }
            self.assertEqual(root.findtext(".//dc:title", namespaces=ns), "Raport interoperacyjności")
            self.assertEqual(root.findtext(".//dc:creator", namespaces=ns), "Anna Nowak")
            self.assertEqual(root.findtext(".//dc:language", namespaces=ns), "pl")

            toc_map = json.loads((reports_dir / "toc_map.json").read_text(encoding="utf-8"))
            self.assertEqual(toc_map["gate"]["status"], "pass")
            self.assertGreaterEqual(len(toc_map["entries"]), 1)

    def test_recovery_pipeline_runs_heading_phases_even_when_metadata_gate_b_fails(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">legacy-id</dc:identifier>
    <dc:title>Executive summary</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Unknown</dc:creator>
  </metadata>
  <manifest>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
  </spine>
</package>
"""
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport platnosci</title></head>
  <body>
    <section>
      <h1>Material sponsorowany - R4</h1>
      <p>Krotki baner reklamowy nie powinien byc rozdzialem.</p>
      <h1>Raport platnosci</h1>
      <p>Wstep do raportu opisuje proces i architekture systemu.</p>
    </section>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml#missing-banner">Material sponsorowany - R4</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap><navPoint id="legacy" playOrder="1"><navLabel><text>Material sponsorowany - R4</text></navLabel><content src="chapter_001.xhtml#missing-banner"/></navPoint></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
            }
        )

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.epub"
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            source_path.write_bytes(epub_bytes)

            passed_epubcheck = {"status": "passed", "tool": "epubcheck", "messages": []}
            with patch("epub_quality_recovery.run_epubcheck", return_value=passed_epubcheck):
                with patch("epub_heading_repair.run_epubcheck", return_value=passed_epubcheck):
                    result = run_epub_publishing_quality_recovery(
                        source_path,
                        output_dir=output_dir,
                        reports_dir=reports_dir,
                        expected_language="pl",
                    )

            self.assertEqual(result["gates"]["B"]["status"], "fail")
            self.assertNotIn("skipped", result["gates"]["C"]["summary"].lower())
            self.assertNotIn("skipped", result["gates"]["D"]["summary"].lower())
            self.assertNotIn("skipped", result["gates"]["E"]["summary"].lower())

            toc_map = json.loads((reports_dir / "toc_map.json").read_text(encoding="utf-8"))
            toc_labels = [entry["label"] for entry in toc_map["entries"]]
            self.assertIn("Raport platnosci", toc_labels)
            self.assertNotIn("Material sponsorowany - R4", toc_labels)

    def test_recovery_pipeline_allows_continuation_file_without_h1_after_noise_demoted(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">legacy-id</dc:identifier>
    <dc:title>Executive summary</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Unknown</dc:creator>
  </metadata>
  <manifest>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter_1" href="chapter_002.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_0"/>
    <itemref idref="chapter_1"/>
  </spine>
</package>
"""
        chapter_one = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport platnosci</title></head>
  <body>
    <section>
      <h1>Raport platnosci</h1>
      <p>Wstep do raportu.</p>
    </section>
  </body>
</html>
"""
        chapter_two = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Kontynuacja</title></head>
  <body>
    <section>
      <h1>Material sponsorowany - R4</h1>
      <p>Krotki baner reklamowy nie powinien byc rozdzialem.</p>
      <h2>Proces</h2>
      <p>To jest kontynuacja rozdzialu po usunieciu falszywego H1.</p>
    </section>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml#raport-platnosci">Raport platnosci</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap><navPoint id="main" playOrder="1"><navLabel><text>Raport platnosci</text></navLabel><content src="chapter_001.xhtml#raport-platnosci"/></navPoint></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter_one,
                "EPUB/chapter_002.xhtml": chapter_two,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
            }
        )

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.epub"
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            source_path.write_bytes(epub_bytes)

            passed_epubcheck = {"status": "passed", "tool": "epubcheck", "messages": []}
            with patch("epub_quality_recovery.run_epubcheck", return_value=passed_epubcheck):
                with patch("epub_heading_repair.run_epubcheck", return_value=passed_epubcheck):
                    result = run_epub_publishing_quality_recovery(
                        source_path,
                        output_dir=output_dir,
                        reports_dir=reports_dir,
                        expected_language="pl",
                    )

            self.assertIn(result["gates"]["C"]["status"], {"pass", "pass_with_review"})


if __name__ == "__main__":
    unittest.main()
