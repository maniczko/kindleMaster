import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lxml import etree

from epub_reference_repair import (
    ReferenceRepairRecord,
    _build_source_pdf_reference_records_from_rows,
    repair_epub_reference_sections,
    run_reference_repair_pipeline,
)


class EpubReferenceRepairTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, bytes | str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                payload = content.encode("utf-8") if isinstance(content, str) else content
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, payload, compress_type=compress_type)
        return output.getvalue()

    def _minimal_epub(self, chapter_source: str) -> bytes:
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
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">ref-test</dc:identifier>
    <dc:title>Reference Repair</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chapter" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
""",
                "EPUB/chapter_001.xhtml": chapter_source,
                "EPUB/nav.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Chapter</a></li></ol></nav></body>
</html>
""",
                "EPUB/style/default.css": "body { font-family: serif; }",
            }
        )

    def _epub_with_chapters(self, chapters: list[tuple[str, str]]) -> bytes:
        manifest_items = [
            f'    <item id="chapter{index}" href="{name}" media-type="application/xhtml+xml"/>'
            for index, (name, _source) in enumerate(chapters, start=1)
        ]
        spine_items = [f'    <itemref idref="chapter{index}"/>' for index, _chapter in enumerate(chapters, start=1)]
        files: dict[str, bytes | str] = {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
            "EPUB/content.opf": f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">ref-test</dc:identifier>
    <dc:title>Reference Repair</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="css" href="style/default.css" media-type="text/css"/>
  </manifest>
  <spine>
{chr(10).join(spine_items)}
  </spine>
</package>
""",
            "EPUB/nav.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_001.xhtml">Chapter</a></li></ol></nav></body>
</html>
""",
            "EPUB/style/default.css": "body { font-family: serif; }",
        }
        for name, source in chapters:
            files[f"EPUB/{name}"] = source
        return self._build_epub_bytes(files)

    def test_repair_epub_reference_sections_rebuilds_broken_bibliography(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Quarterly Report</title></head>
  <body>
    <section id="report">
      <h1>Quarterly Report</h1>
      <p>Intro paragraph should remain untouched.</p>
      <h2>References</h2>
      <p>ID Źródło Adres [R1] Visa Waiver Program - Official guidance.</p>
      <p>[R2] Example endpoints.</p>
      <p><a href="https://usa.visa">https://usa.visa</a><br/>.gov/travel/business/visa-waiver-program.html https://example.com/ahttps://example.com/b</p>
      <p>[R3] Broken registry notice https://the</p>
      <h2>Conclusions</h2>
      <p>Closing paragraph should remain untouched.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source)

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="en")

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn('epub:type="bibliography"', chapter)
        self.assertIn('href="https://usa.visa.gov/travel/business/visa-waiver-program.html"', chapter)
        self.assertIn('href="https://example.com/a"', chapter)
        self.assertIn('href="https://example.com/b"', chapter)
        self.assertNotIn('href="https://the"', chapter)
        self.assertNotIn("Unresolved URL:", chapter)
        self.assertNotIn("Link requires manual review.", chapter)
        self.assertNotIn("ID Źródło Adres", chapter)
        self.assertIn("Intro paragraph should remain untouched.", chapter)
        self.assertIn("Closing paragraph should remain untouched.", chapter)
        self.assertGreaterEqual(result.summary["records_detected"], 3)
        self.assertGreaterEqual(result.summary["records_flagged_for_review"], 1)
        self.assertEqual(result.summary["quality_gate_status"], "passed")

    def test_repair_epub_reference_sections_preserves_valid_reference_links(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>References</title></head>
  <body>
    <section>
      <h1>References</h1>
      <p>[1] EPUB 3.3 Specification https://www.w3.org/TR/epub-33/</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source)

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="en")

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn('href="https://www.w3.org/TR/epub-33/"', chapter)
        self.assertNotIn("Link requires manual review.", chapter)
        self.assertEqual(result.summary["records_flagged_for_review"], 0)
        self.assertEqual(result.summary["quality_gate_status"], "passed")

    def test_repair_epub_reference_sections_preserves_alphanumeric_ids_and_blocks_multi_id_titles(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>References</title></head>
  <body>
    <section>
      <h1>References</h1>
      <p>[R2] [R3] [R4] Visa Authorization and Reversal Processing Requirements.</p>
      <p>[R5] Visa Dispute Management Guidelines.</p>
      <p>https://usa.visa.com/content/dam/VCOM/regional/na/us/support-legal/documents/authorization-and-reversal-processing-bestpractices-for-merchants.pdf https://usa.visa.com/content/dam/VCOM/global/support-legal/documents/merchants-dispute-management-guidelines.pdf</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source)

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="en")

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_001.xhtml").decode("utf-8")

        self.assertIn("[R5]", chapter)
        self.assertNotIn("[1]", chapter)
        self.assertNotIn("[R2] [R3] [R4]", chapter)
        self.assertGreaterEqual(result.summary["records_flagged_for_review"], 1)
        self.assertEqual(result.summary["quality_gate_status"], "passed")

    def test_run_reference_repair_pipeline_writes_artifacts(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Sources</title></head>
  <body>
    <section>
      <h1>Sources</h1>
      <p>[1] Example https://example.com/path%2</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._minimal_epub(chapter_source)

        with TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "input.epub"
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            source_path.write_bytes(epub_bytes)

            with patch(
                "epub_reference_repair.run_epubcheck",
                return_value={"status": "passed", "tool": "epubcheck", "messages": []},
            ):
                result = run_reference_repair_pipeline(
                    source_path,
                    output_dir=output_dir,
                    reports_dir=reports_dir,
                    language_hint="en",
                )

            self.assertTrue((output_dir / "repaired.epub").exists())
            self.assertTrue((reports_dir / "reference_repair_report.json").exists())
            self.assertTrue((reports_dir / "reference_repair_summary.md").exists())
            self.assertTrue((reports_dir / "reference_before_after.md").exists())
            self.assertEqual(result["summary"]["epubcheck_status"], "passed")
            self.assertIn("quality_gate_status", result["summary"])

    def test_reference_coverage_blocks_missing_records_for_cited_ids(self):
        body_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Main Report</title></head>
  <body>
    <section>
      <h1>Main Report</h1>
      <p>The report cites [R1], [R3] and [R7] for verification.</p>
    </section>
  </body>
</html>
"""
        reference_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>References</title></head>
  <body>
    <section>
      <h1>References</h1>
      <p>[R1] Example One https://example.com/r1</p>
      <p>[R7] Example Seven https://example.com/r7</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._epub_with_chapters(
            [
                ("chapter_001.xhtml", body_source),
                ("chapter_002.xhtml", reference_source),
            ]
        )

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="en")

        self.assertEqual(result.summary["citations_detected"], 3)
        self.assertEqual(result.summary["citations_covered"], 2)
        self.assertEqual(result.summary["citations_missing_record"], 1)
        self.assertEqual(result.summary["reference_quality_gate_status"], "failed")
        coverage = {item["ref_id"]: item["status"] for item in result.citation_coverage}
        self.assertEqual(coverage["[R3]"], "missing_record")

    def test_reference_coverage_marks_ambiguous_record_when_citation_maps_to_review_only_entry(self):
        body_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Main Report</title></head>
  <body>
    <section>
      <h1>Main Report</h1>
      <p>The report cites [R2] in the narrative.</p>
    </section>
  </body>
</html>
"""
        reference_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>References</title></head>
  <body>
    <section>
      <h1>References</h1>
      <p>[R2] [R3] [R4] Shared source descriptor.</p>
      <p>https://example.com/r234</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._epub_with_chapters(
            [
                ("chapter_001.xhtml", body_source),
                ("chapter_002.xhtml", reference_source),
            ]
        )

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="en")

        self.assertEqual(result.summary["citations_ambiguous"], 1)
        self.assertEqual(result.summary["reference_quality_gate_status"], "failed")
        coverage = {item["ref_id"]: item["status"] for item in result.citation_coverage}
        self.assertEqual(coverage["[R2]"], "ambiguous_record")

    def test_reference_coverage_reports_unused_records_without_failing_gate(self):
        body_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Main Report</title></head>
  <body>
    <section>
      <h1>Main Report</h1>
      <p>The report cites only [R1].</p>
    </section>
  </body>
</html>
"""
        reference_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>References</title></head>
  <body>
    <section>
      <h1>References</h1>
      <p>[R1] Example One https://example.com/r1</p>
      <p>[R9] Example Nine https://example.com/r9</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._epub_with_chapters(
            [
                ("chapter_001.xhtml", body_source),
                ("chapter_002.xhtml", reference_source),
            ]
        )

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="en")

        self.assertEqual(result.summary["reference_quality_gate_status"], "passed")
        self.assertIn("[R9]", result.summary["unused_reference_records"])
        coverage = {item["ref_id"]: item["status"] for item in result.citation_coverage}
        self.assertEqual(coverage["[R9]"], "unused_record")

    def test_reference_coverage_flags_empty_reference_section_when_citations_exist(self):
        body_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Main Report</title></head>
  <body>
    <section>
      <h1>Main Report</h1>
      <p>The report cites [R1]-[R2] in the executive summary.</p>
    </section>
  </body>
</html>
"""
        empty_reference_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Referencje publiczne</title></head>
  <body>
    <section>
      <h1>Referencje publiczne</h1>
      <p> </p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._epub_with_chapters(
            [
                ("chapter_001.xhtml", body_source),
                ("chapter_002.xhtml", empty_reference_source),
            ]
        )

        with patch(
            "epub_reference_repair.run_epubcheck",
            return_value={"status": "passed", "tool": "epubcheck", "messages": []},
        ):
            result = repair_epub_reference_sections(epub_bytes, language_hint="pl")

        self.assertEqual(result.summary["citations_detected"], 2)
        self.assertEqual(result.summary["citations_missing_record"], 2)
        self.assertGreaterEqual(result.summary["empty_reference_sections_detected"], 1)
        self.assertEqual(result.summary["reference_quality_gate_status"], "failed")

    def test_reference_repair_uses_source_pdf_records_for_scope_ids(self):
        body_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Main Report</title></head>
  <body>
    <section>
      <h1>Main Report</h1>
      <p>The report cites [R1], [R2] and [R3].</p>
    </section>
  </body>
</html>
"""
        reference_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>References</title></head>
  <body>
    <section>
      <h1>References</h1>
      <p>[R1] Placeholder.</p>
      <p>[R2] Placeholder.</p>
      <p>[R3] Placeholder.</p>
    </section>
  </body>
</html>
"""
        epub_bytes = self._epub_with_chapters(
            [
                ("chapter_001.xhtml", body_source),
                ("chapter_002.xhtml", reference_source),
            ]
        )
        source_records = {
            "R1": ReferenceRepairRecord(
                document_path="source-pdf:page-19",
                section_id="source-pdf-page-19",
                ref_id="[R1]",
                display_ref_id="[R1]",
                source_name="Source One",
                source_title="Source One",
                description="Reference one.",
                url="https://example.com/r1",
                links=["https://example.com/r1"],
                confidence=0.99,
                review_flag=False,
                link_status="valid",
            ),
            "R2": ReferenceRepairRecord(
                document_path="source-pdf:page-19",
                section_id="source-pdf-page-19",
                ref_id="[R2]",
                display_ref_id="[R2]",
                source_name="Source Two",
                source_title="Source Two",
                description="Reference two.",
                url="https://example.com/r2",
                links=["https://example.com/r2"],
                confidence=0.99,
                review_flag=False,
                link_status="valid",
            ),
            "R3": ReferenceRepairRecord(
                document_path="source-pdf:page-19",
                section_id="source-pdf-page-19",
                ref_id="[R3]",
                display_ref_id="[R3]",
                source_name="Source Three",
                source_title="Source Three",
                description="Reference three.",
                url="https://example.com/r3",
                links=["https://example.com/r3"],
                confidence=0.99,
                review_flag=False,
                link_status="valid",
            ),
        }

        with patch(
            "epub_reference_repair._extract_source_pdf_reference_records",
            return_value=source_records,
        ):
            with patch(
                "epub_reference_repair.run_epubcheck",
                return_value={"status": "passed", "tool": "epubcheck", "messages": []},
            ):
                result = repair_epub_reference_sections(
                    epub_bytes,
                    language_hint="en",
                    source_pdf_path="dummy.pdf",
                )

        with zipfile.ZipFile(io.BytesIO(result.epub_bytes), "r") as archive:
            chapter = archive.read("EPUB/chapter_002.xhtml").decode("utf-8")

        self.assertEqual(chapter.count('class="reference-entry"'), 3)
        self.assertIn("https://example.com/r1", chapter)
        self.assertIn("https://example.com/r2", chapter)
        self.assertIn("https://example.com/r3", chapter)
        self.assertEqual(result.summary["citations_covered"], 3)
        self.assertEqual(result.summary["citations_missing_record"], 0)
        self.assertEqual(result.summary["reference_quality_gate_status"], "passed")

    def test_source_pdf_table_row_parser_recovers_multiline_rows(self):
        rows = [
            {"id": "[R11]", "src": "PSD2 consolidated text - safeguarding,", "url": "https://eur-lex.europa.eu/legal-content/", "row": 762},
            {"id": "", "src": "refund rights, value date, authorization", "url": "EN/TXT/HTML/?uri=CELEX%3A02015L2366-", "row": 774},
            {"id": "", "src": "rules.", "url": "20151223", "row": 786},
            {"id": "[R12]", "src": "European Commission - payment services", "url": "https://finance.ec.europa.eu/consumer-", "row": 870},
            {"id": "", "src": "context interchange fee regulation.", "url": "finance-and-payments/payment-services/", "row": 882},
            {"id": "", "src": "", "url": "payment-services_en", "row": 894},
        ]

        records = _build_source_pdf_reference_records_from_rows(
            rows,
            document_path="source-pdf:page-19",
            section_id="source-pdf-page-19",
        )
        record_map = {record.display_ref_id or record.ref_id: record for record in records}

        self.assertIn("[R11]", record_map)
        self.assertIn("[R12]", record_map)
        self.assertEqual(
            record_map["[R11]"].links[0],
            "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX%3A02015L2366-20151223",
        )
        self.assertEqual(
            record_map["[R12]"].links[0],
            "https://finance.ec.europa.eu/consumer-finance-and-payments/payment-services/payment-services_en",
        )
        self.assertFalse(record_map["[R11]"].review_flag)
        self.assertFalse(record_map["[R12]"].review_flag)

if __name__ == "__main__":
    unittest.main()
