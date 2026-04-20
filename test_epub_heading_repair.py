import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lxml import etree

from epub_heading_repair import repair_epub_headings_and_toc, run_heading_repair_pipeline


class EpubHeadingRepairTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, bytes | str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                payload = content.encode("utf-8") if isinstance(content, str) else content
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, payload, compress_type=compress_type)
        return output.getvalue()

    def _minimal_epub(self, chapter_source: str, *, nav_label: str = "Legacy label", nav_href: str = "chapter_001.xhtml") -> bytes:
        return self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
                "EPUB/content.opf": """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid">heading-repair</dc:identifier>
    <dc:title>python-docx</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Technical Converter</dc:creator>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter"/>
  </spine>
</package>
""",
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="{nav_href}">{nav_label}</a></li></ol></nav></body>
</html>
""",
                "EPUB/toc.ncx": f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="heading-repair"/></head>
  <docTitle><text>{nav_label}</text></docTitle>
  <navMap><navPoint id="legacy" playOrder="1"><navLabel><text>{nav_label}</text></navLabel><content src="{nav_href}"/></navPoint></navMap>
</ncx>
""",
                "EPUB/style/default.css": "body { font-family: serif; }",
            }
        )

    def test_repair_epub_headings_and_toc_removes_fake_heading_and_rebuilds_toc(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport platnosci</title></head>
  <body>
    <section>
      <h1>Material sponsorowany - R4</h1>
      <p>Krotki baner reklamowy nie jest realnym rozdzialem.</p>
      <h1>Raport platnosci</h1>
      <p>Wstep do raportu opisuje proces i cele wdrozenia.</p>
      <p class="section-title">Co to jest</p>
      <p>Ta sekcja wyjasnia zakres systemu i jego definicje robocze.</p>
      <p class="section-title">Jak dziala</p>
      <p>Ta sekcja opisuje przeplyw i relacje komponentow.</p>
      <p class="section-title">Implikacje biznesowe</p>
      <p>Ta sekcja pokazuje ryzyka i decyzje organizacyjne.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(
            chapter_source,
            nav_label="Material sponsorowany - R4",
            nav_href="chapter_001.xhtml#missing-banner",
        )

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="pl")

        rejected_texts = {item["text"] for item in result.rejected_candidates}
        toc_labels = {item["label"] for item in result.toc_mapping}

        self.assertIn("Material sponsorowany - R4", rejected_texts)
        self.assertNotIn("Material sponsorowany - R4", toc_labels)
        self.assertIn("Raport platnosci", toc_labels)
        self.assertEqual(result.summary["toc_entries_after"], 1)
        self.assertNotIn("Co to jest", toc_labels)
        self.assertNotIn("Jak dziala", toc_labels)
        self.assertNotIn("Implikacje biznesowe", toc_labels)
        self.assertEqual(result.epubcheck["status"], "passed")

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")

        self.assertIn('<p class="promo-banner">Material sponsorowany - R4</p>', chapter)
        self.assertNotIn("<h1>Material sponsorowany - R4</h1>", chapter)
        self.assertIn("Raport platnosci", nav)
        self.assertNotIn("Material sponsorowany - R4", nav)

    def test_run_heading_repair_pipeline_writes_reports_and_preserves_clean_structure(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>System rozliczen</title></head>
  <body>
    <section>
      <h1 id="system-rozliczen">System rozliczen</h1>
      <p>Wprowadzenie do rozdzialu.</p>
      <h2 id="architektura">Architektura</h2>
      <p>Opis architektury.</p>
      <h2 id="ryzyka">Ryzyka</h2>
      <p>Opis ryzyk.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(
            chapter_source,
            nav_label="System rozliczen",
            nav_href="chapter_001.xhtml#system-rozliczen",
        )

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.epub"
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            source_path.write_bytes(epub_bytes)

            with patch(
                "epub_heading_repair.run_epubcheck",
                return_value={"status": "passed", "tool": "epubcheck", "messages": []},
            ):
                result = run_heading_repair_pipeline(
                    source_path,
                    output_dir=output_dir,
                    reports_dir=reports_dir,
                    language_hint="pl",
                )

            self.assertTrue((output_dir / "repaired.epub").exists())
            self.assertTrue((reports_dir / "heading_inventory.json").exists())
            self.assertTrue((reports_dir / "rejected_heading_candidates.json").exists())
            self.assertTrue((reports_dir / "toc_mapping.json").exists())
            self.assertTrue((reports_dir / "heading_diff_report.md").exists())
            self.assertTrue((reports_dir / "qa_report.md").exists())
            self.assertEqual(result["summary"]["epubcheck_status"], "passed")

            heading_inventory = json.loads((reports_dir / "heading_inventory.json").read_text(encoding="utf-8"))
            rejected = json.loads((reports_dir / "rejected_heading_candidates.json").read_text(encoding="utf-8"))
            toc_mapping = json.loads((reports_dir / "toc_mapping.json").read_text(encoding="utf-8"))
            qa_report = (reports_dir / "qa_report.md").read_text(encoding="utf-8")

            self.assertGreaterEqual(heading_inventory["summary"]["candidate_count"], 3)
            self.assertEqual(rejected["summary"]["rejected_count"], 0)
            self.assertGreaterEqual(toc_mapping["summary"]["entry_count"], 2)
            self.assertIn("Release status:", qa_report)
            self.assertIn("EPUBCheck: passed", qa_report)

            with zipfile.ZipFile(io.BytesIO((output_dir / "repaired.epub").read_bytes()), "r") as archive:
                nav_tree = etree.fromstring(archive.read("EPUB/nav.xhtml"))

            ns = {"x": "http://www.w3.org/1999/xhtml", "epub": "http://www.idpf.org/2007/ops"}
            toc_entries = nav_tree.xpath(".//x:nav[@epub:type='toc']//x:a/text()", namespaces=ns)
            self.assertIn("System rozliczen", toc_entries)
            self.assertIn("Architektura", toc_entries)

    def test_repair_epub_headings_and_toc_filters_repeated_generic_schema_labels_from_toc(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport platnosci</title></head>
  <body>
    <section>
      <h1>Raport platnosci</h1>
      <h2>Proces</h2>
      <p>Wprowadzenie do procesu i mapy odpowiedzialnosci finansowej.</p>
      <h3>Co to jest</h3>
      <p>Opis definicji procesu.</p>
      <h3>Jak dziala</h3>
      <p>Opis przeplywu zdarzen.</p>
      <h3>Implikacje biznesowe</h3>
      <p>Opis ryzyk i decyzji.</p>
      <h3>Co to jest</h3>
      <p>Dodatkowy blok, ktory nie powinien zasmiecac TOC.</p>
      <h2>Architektura</h2>
      <p>Opis architektury systemu.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(
            chapter_source,
            nav_label="Raport platnosci",
            nav_href="chapter_001.xhtml#raport-platnosci",
        )

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="pl")

        toc_labels = [item["label"] for item in result.toc_mapping]
        self.assertIn("Raport platnosci", toc_labels)
        self.assertIn("Proces", toc_labels)
        self.assertIn("Architektura", toc_labels)
        self.assertNotIn("Co to jest", toc_labels)
        self.assertNotIn("Jak dziala", toc_labels)
        self.assertNotIn("Implikacje biznesowe", toc_labels)

    def test_repair_epub_headings_and_toc_excludes_table_header_like_entries_from_toc(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Raport platnosci</title></head>
  <body>
    <section>
      <h1>Raport platnosci</h1>
      <p>Wstep do raportu.</p>
      <h2>Proces</h2>
      <p>Opis procesu.</p>
      <h3>Kategoria Wymaganie Dlaczego to ważne Przykładowy miernik / test</h3>
      <p>To jest w praktyce zlepiony naglowek tabeli, nie sekcja.</p>
      <h3>KPI Definicja robocza Po co Pułapka interpretacyjna</h3>
      <p>To także jest zlepiony naglowek tabeli.</p>
      <h2>Architektura</h2>
      <p>Opis architektury.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source)

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="pl")

        toc_labels = {item["label"] for item in result.toc_mapping}
        self.assertIn("Raport platnosci", toc_labels)
        self.assertIn("Proces", toc_labels)
        self.assertIn("Architektura", toc_labels)
        self.assertNotIn("Kategoria Wymaganie Dlaczego to ważne Przykładowy miernik / test", toc_labels)
        self.assertNotIn("KPI Definicja robocza Po co Pułapka interpretacyjna", toc_labels)

    def test_repair_epub_headings_and_toc_sanitizes_unknown_markup(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Dense Handbook</title></head>
  <body>
    <section>
      <h1 id="dense-handbook">Dense Handbook</h1>
      <p>For example, "As a <who>, I need to <what>, so that <why>."</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source, nav_label="Dense Handbook", nav_href="chapter_001.xhtml#dense-handbook")

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="en")

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertNotIn("<who>", chapter)
        self.assertNotIn("<what>", chapter)
        self.assertNotIn("<why>", chapter)
        self.assertIn("&lt;who&gt;", chapter)
        self.assertEqual(result.epubcheck["status"], "passed")

    def test_repair_epub_headings_and_toc_dedupes_duplicate_heading_ids(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Dense Handbook</title></head>
  <body>
    <section>
      <h1 id="dense-handbook">Dense Handbook</h1>
      <h2 id="business-analysis-planning-and-monitoring-co-to-jest">Business Analysis Planning and Monitoring - Co to jest</h2>
      <p>Opis pierwszego bloku.</p>
      <h2 id="business-analysis-planning-and-monitoring-co-to-jest">Business Analysis Planning and Monitoring - Co to jest</h2>
      <p>Opis drugiego bloku.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source, nav_label="Dense Handbook", nav_href="chapter_001.xhtml#dense-handbook")

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="en")

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertEqual(chapter.count('id="business-analysis-planning-and-monitoring-co-to-jest"'), 1)
        self.assertEqual(result.epubcheck["status"], "passed")

    def test_repair_epub_headings_and_toc_merges_split_heading_clusters(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Dense Handbook</title></head>
  <body>
    <section>
      <h1 id="dense-handbook">Dense Handbook</h1>
      <h2>Requirements Life</h2>
      <h2>Cycle Management</h2>
      <p>Opis sekcji po scaleniu powinien pozostac czytelny i wspierac nawigacje.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source, nav_label="Dense Handbook", nav_href="chapter_001.xhtml#dense-handbook")

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="en")

        toc_labels = {item["label"] for item in result.toc_mapping}
        self.assertIn("Requirements Life Cycle Management", toc_labels)
        self.assertNotIn("Requirements Life", toc_labels)
        self.assertNotIn("Cycle Management", toc_labels)

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("Requirements Life Cycle Management", chapter)

    def test_repair_epub_headings_and_toc_demotes_dense_diagram_heading_clusters(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Dense Handbook</title></head>
  <body>
    <section>
      <h1 id="dense-handbook">Dense Handbook</h1>
      <p>The following diagram shows a general relationship between the knowledge areas.</p>
      <h2>Figure 1.4.1: Relationships Between Knowledge Areas</h2>
      <h2>Business Analysis</h2>
      <h2>Planning and</h2>
      <h2>Monitoring</h2>
      <h2>Requirements</h2>
      <h2>Analysis and Design</h2>
      <h2>Definition</h2>
      <p>Regular body content starts after the diagram labels and should remain the real section content.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source, nav_label="Dense Handbook", nav_href="chapter_001.xhtml#dense-handbook")

        with patch(
            "epub_heading_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_headings_and_toc(epub_bytes, language_hint="en")

        toc_labels = {item["label"] for item in result.toc_mapping}
        self.assertEqual(toc_labels, {"Dense Handbook"})

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertNotIn("<h2>Business Analysis</h2>", chapter)
        self.assertNotIn("<h2>Planning and</h2>", chapter)
        self.assertNotIn("<h2>Requirements</h2>", chapter)


if __name__ == "__main__":
    unittest.main()
