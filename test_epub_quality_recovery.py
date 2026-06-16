import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lxml import etree

from epub_premium_scoring import score_epub_premium_quality
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

    def test_strict_premium_scoring_blocks_technically_valid_noisy_magazine(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">magazine-id</dc:identifier>
    <dc:title>TAJEMNICA REZYGNACJI KACZYNSKIEGO</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>LICZBA TYGODNIA</dc:creator>
    <dc:date>2024</dc:date>
  </metadata>
  <manifest>
    <item id="chapter_1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter_2" href="chapter_002.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter_3" href="chapter_003.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter_4" href="chapter_004.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_1"/>
    <itemref idref="chapter_2"/>
    <itemref idref="chapter_3"/>
    <itemref idref="chapter_4"/>
  </spine>
</package>
"""
        article = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Artykul</title></head>
  <body>
    <h1>Historia z okladki</h1>
    <p>Politykaiinfotainment oraz Niezwyklamatkaslynnegopremiera pokazuja, ze tekst ma widoczne artefakty OCR.</p>
    <p>#awny podzial ©schodͿ-zachod i pro- jekt powinny obnizac jakosc premium.</p>
  </body>
</html>
"""
        gallery = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Galeria</h1><p>Galeria</p><img src="images/a.jpg" alt=""/></body></html>
"""
        advert = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Reklama</h1><p>Reklama</p><img src="images/b.jpg" alt=""/></body></html>
"""
        sponsored = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Material sponsorowany</h1><p>Material sponsorowany</p></body></html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body><nav epub:type="toc"><ol>
    <li><a href="chapter_001.xhtml">Spis tresci</a></li>
    <li><a href="chapter_002.xhtml">Galeria</a></li>
    <li><a href="chapter_003.xhtml">Reklama</a></li>
    <li><a href="chapter_004.xhtml">To jest bardzo dlugi lead artykulu ktory nie powinien byc tytulem nawigacji poniewaz wyglada jak caly akapit w spisie tresci</a></li>
  </ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="magazine-id"/></head><docTitle><text>Legacy</text></docTitle><navMap/></ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": article,
                "EPUB/chapter_002.xhtml": gallery,
                "EPUB/chapter_003.xhtml": advert,
                "EPUB/chapter_004.xhtml": sponsored,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
                "EPUB/images/a.jpg": b"fake",
                "EPUB/images/b.jpg": b"fake",
            }
        )

        payload = score_epub_premium_quality(epub_bytes, epubcheck={"status": "passed", "messages": []})
        codes = [issue["code"] for issue in payload["issues"]]

        self.assertTrue(payload["technical_valid"])
        self.assertEqual(payload["mail_sendable"], "likely")
        self.assertFalse(payload["kindle_ready"])
        self.assertFalse(payload["premium_ready"])
        self.assertLessEqual(payload["premium_score"], 5.5)
        self.assertIn("suspicious_metadata_author", codes)
        self.assertIn("magazine_non_content_chapter", codes)
        self.assertIn("toc_non_content_entry", codes)
        self.assertIn("kindle_ready_blocked_by_quality", codes)

    def test_premium_scoring_does_not_block_long_sponsored_article_as_non_content(self):
        prose = " ".join(["Problem solving article explains governance, decisions, delivery, and team learning."] * 80)
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">sponsored-article</dc:identifier>
    <dc:title>Magazine</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Editorial Team</dc:creator>
  </metadata>
  <manifest>
    <item id="chapter_1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine><itemref idref="chapter_1"/></spine>
</package>
"""
        chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Material sponsorowany - Problem Solving</title></head>
<body><h1>Material sponsorowany - Problem Solving</h1><p>{prose}</p></body></html>
"""
        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": chapter,
                "EPUB/nav.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Material sponsorowany - Problem Solving</a></li></ol></nav></body></html>""",
            }
        )

        payload = score_epub_premium_quality(epub_bytes, epubcheck={"status": "passed", "messages": []})

        self.assertNotIn("magazine_non_content_chapter", {issue["code"] for issue in payload["issues"]})

    def test_premium_scoring_blocks_polish_structural_labels_in_english_epub(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">english-magazine-id</dc:identifier>
    <dc:title>Global Projects Review</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>Editorial Team</dc:creator>
    <dc:publisher>Project Press</dc:publisher>
  </metadata>
  <manifest>
    <item id="chapter_1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_1"/>
  </spine>
</package>
"""
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Co to jest</title></head>
  <body>
    <h1>Co to jest</h1>
    <p class="kicker">Przykład</p>
    <p>This English article has enough clean prose to avoid relying on technical validity alone.</p>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Jak działa</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="english-magazine-id"/></head><docTitle><text>Global Projects Review</text></docTitle><navMap/></ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
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
            }
        )

        payload = score_epub_premium_quality(epub_bytes, epubcheck={"status": "passed", "messages": []})
        codes = [issue["code"] for issue in payload["issues"]]

        self.assertIn("language_label_contamination", codes)
        self.assertFalse(payload["kindle_ready"])
        self.assertGreater(payload["metrics"]["language_label_contamination"]["hit_count"], 2)
        self.assertIn("Co to jest", payload["metrics"]["language_label_contamination"]["labels"])

        polish_epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source.replace("<dc:language>en</dc:language>", "<dc:language>pl</dc:language>"),
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
            }
        )
        polish_payload = score_epub_premium_quality(polish_epub_bytes, epubcheck={"status": "passed", "messages": []})
        polish_codes = [issue["code"] for issue in polish_payload["issues"]]

        self.assertNotIn("language_label_contamination", polish_codes)
        self.assertEqual(polish_payload["metrics"]["language_label_contamination"]["hit_count"], 0)

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
            self.assertTrue((reports_dir / "premium_scoring.json").exists())
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

    def test_recovery_pipeline_rejects_worse_recovered_epub_quality_selection(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">quality-selection-id</dc:identifier>
    <dc:title>Quality Selection Sample</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>KindleMaster QA</dc:creator>
    <dc:publisher>KindleMaster</dc:publisher>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="chapter_1" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter_1"/>
  </spine>
</package>
"""
        clean_chapter = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Introduction</title></head>
<body><h1>Introduction</h1><p>This chapter has clean text and should remain the selected EPUB when recovery regresses.</p></body></html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Introduction</a></li></ol></nav></body></html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="quality-selection-id"/></head><docTitle><text>Quality Selection Sample</text></docTitle><navMap/></ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""
        clean_epub = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": clean_chapter,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
            }
        )
        worse_chapter = clean_chapter.replace("Introduction", "Material sponsorowany - R4").replace(
            "clean text",
            "worse-marker clean text",
        )
        worse_epub = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/chapter_001.xhtml": worse_chapter,
                "EPUB/nav.xhtml": nav_source.replace("Introduction", "Material sponsorowany - R4"),
                "EPUB/toc.ncx": toc_source,
            }
        )

        def scoring(epub_bytes, *, epubcheck=None):
            del epubcheck
            try:
                with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
                    has_worse_marker = any(b"worse-marker" in archive.read(name) for name in archive.namelist())
            except Exception:
                has_worse_marker = False
            if has_worse_marker:
                return {
                    "status": "failed",
                    "technical_valid": True,
                    "kindle_ready": False,
                    "premium_ready": False,
                    "premium_score": 7.0,
                    "release_verdict": "release_blocked",
                    "issue_counts": {"blocker": 1},
                    "issues": [{"severity": "blocker", "code": "magazine_non_content_chapter"}],
                }
            return {
                "status": "passed_with_warnings",
                "technical_valid": True,
                "kindle_ready": True,
                "premium_ready": False,
                "premium_score": 9.1,
                "release_verdict": "ready_with_review",
                "issue_counts": {"review": 1},
                "issues": [{"severity": "review", "code": "toc_lead_used_as_title"}],
            }

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.epub"
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            source_path.write_bytes(clean_epub)

            passed_epubcheck = {"status": "passed", "tool": "epubcheck", "messages": []}
            with patch("epub_quality_recovery.run_epubcheck", return_value=passed_epubcheck):
                with patch("epub_quality_selection.score_epub_premium_quality", side_effect=scoring):
                    with patch("epub_quality_recovery.score_epub_premium_quality", side_effect=scoring):
                        with patch(
                            "epub_quality_recovery._run_recovery_phases",
                            return_value=(
                                worse_epub,
                                {"summary": {"removed_count": 1, "recovered_count": 1}, "manual_review": []},
                                {"entries": [], "warnings": [], "toc_nav_count": 1},
                                {"status": "passed"},
                                passed_epubcheck,
                            ),
                        ):
                            result = run_epub_publishing_quality_recovery(
                                source_path,
                                output_dir=output_dir,
                                reports_dir=reports_dir,
                                expected_language="en",
                                strict_premium=True,
                            )

            self.assertEqual(result["quality_selection"]["status"], "rejected")
            self.assertEqual(result["quality_selection"]["selected_stage"], "pre_recovery")
            self.assertEqual(result["quality_selection"]["rejected_stage"], "recovered")
            self.assertIn("recovery_rejected_due_to_quality_regression", result["quality_selection"]["reason_codes"])
            self.assertLess(result["quality_selection"]["candidate_score"], result["quality_selection"]["baseline_score"])
            self.assertTrue((reports_dir / "quality_selection.json").exists())
            final_bytes = (output_dir / "final.epub").read_bytes()
            with zipfile.ZipFile(io.BytesIO(final_bytes), "r") as archive:
                final_text = b"\n".join(archive.read(name) for name in archive.namelist())
            self.assertNotIn(b"worse-marker", final_text)
            self.assertNotIn(b"Material sponsorowany - R4", final_text)


if __name__ == "__main__":
    unittest.main()
