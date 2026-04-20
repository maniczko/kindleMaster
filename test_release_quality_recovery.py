import base64
import io
import json
import sys
import unittest
import zipfile
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from lxml import etree

import publication_audit
from converter import ConversionConfig, convert_pdf_to_epub_with_report
from kindle_semantic_cleanup import _process_chapter, finalize_epub_for_kindle
from publication_model import PublicationAnalysis, PublicationDocument
from publication_pipeline import finalize_publication_epub
from text_cleanup_engine import CleanupDecision, TextCleanupResult


XHTML_NS = "http://www.w3.org/1999/xhtml"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
EPUB_NS = "http://www.idpf.org/2007/ops"


class ReleaseQualityRecoveryTests(unittest.TestCase):
    def _build_epub_bytes(self, files: dict[str, bytes | str]) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for archive_path, content in files.items():
                payload = content.encode("utf-8") if isinstance(content, str) else content
                compress_type = zipfile.ZIP_STORED if archive_path == "mimetype" else zipfile.ZIP_DEFLATED
                archive.writestr(archive_path, payload, compress_type=compress_type)
        return output.getvalue()

    def _fake_analysis(self) -> PublicationAnalysis:
        return PublicationAnalysis(
            profile="book_reflow",
            confidence=0.93,
            page_count=12,
            has_toc=True,
            has_tables=False,
            has_diagrams=False,
            has_meaningful_images=False,
            estimated_sections=3,
            fallback_recommendation="semantic-reflow",
            ui_profile="technical-study",
            legacy_strategy="text_reflowable",
            has_text_layer=True,
            is_scanned=False,
            layout_heavy=False,
            text_heavy=True,
        )

    def test_gate_a_b_d_e_finalize_epub_repairs_metadata_navigation_and_structure(self):
        opf_source = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid"></dc:identifier>
    <dc:title>Emvc</dc:title>
    <dc:language>en</dc:language>
    <dc:creator id="creator">python-docx</dc:creator>
    <dc:description></dc:description>
  </metadata>
  <manifest>
    <item id="style" href="style/default.css" media-type="text/css"/>
    <item id="cover-image" href="images/cover.png" media-type="image/png"/>
    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter_0" href="chapter_001.xhtml" media-type="application/xhtml+xml"/>
    <item id="chapter_1" href="chapter_002.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="cover"/>
    <itemref idref="chapter_0"/>
    <itemref idref="chapter_1"/>
    <itemref idref="nav"/>
  </spine>
</package>
"""
        cover_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Cover</title></head>
  <body><img src="images/cover.png" alt=""/></body>
</html>
"""
        chapter_one = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Emvc</title></head>
  <body>
    <section>
      <h1>Raport zgodnosci</h1>
      <p class="author">Jan Kowalski</p>
      <p>Ten raport opisuje jak system dziala w praktyce i jakie sa zaleznosci procesu wydawniczego.</p>
      <h2>Architektura</h2>
      <p>Opis warstwy integracyjnej porzadkuje czytanie rozdzialu.</p>
      <h3>Integracja API</h3>
      <p>Ta sekcja wyjasnia gdzie prowadza cele TOC i jakie relacje zachowuje spine.</p>
    </section>
  </body>
</html>
"""
        chapter_two = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Aneks</title></head>
  <body>
    <section>
      <h1>Aneks</h1>
      <p>Dodatek zbiera material uzupelniajacy i nie powinien zniknac ze spine ani TOC.</p>
    </section>
  </body>
</html>
"""
        nav_source = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Navigation</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter_999.xhtml#missing">Legacy</a></li></ol></nav></body>
</html>
"""
        toc_source = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="legacy-id"/></head>
  <docTitle><text>Legacy</text></docTitle>
  <navMap><navPoint id="legacy" playOrder="1"><navLabel><text>Legacy</text></navLabel><content src="chapter_999.xhtml#missing"/></navPoint></navMap>
</ncx>
"""
        container_source = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO8B9SMAAAAASUVORK5CYII="
        )

        epub_bytes = self._build_epub_bytes(
            {
                "mimetype": "application/epub+zip",
                "META-INF/container.xml": container_source,
                "EPUB/content.opf": opf_source,
                "EPUB/cover.xhtml": cover_source,
                "EPUB/chapter_001.xhtml": chapter_one,
                "EPUB/chapter_002.xhtml": chapter_two,
                "EPUB/nav.xhtml": nav_source,
                "EPUB/toc.ncx": toc_source,
                "EPUB/style/default.css": "body { font-family: serif; }",
                "EPUB/images/cover.png": tiny_png,
            }
        )

        cleaned_epub = finalize_epub_for_kindle(
            epub_bytes,
            title="Emvc",
            author="python-docx",
            language="en",
        )

        with TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(io.BytesIO(cleaned_epub), "r") as archive:
                archive.extractall(temp_dir)

            opf_path = Path(temp_dir) / "EPUB" / "content.opf"
            nav_path = Path(temp_dir) / "EPUB" / "nav.xhtml"
            opf_tree = etree.parse(str(opf_path))
            nav_tree = etree.parse(str(nav_path))
            ns = {"opf": OPF_NS, "dc": DC_NS, "x": XHTML_NS, "epub": EPUB_NS}
            root = opf_tree.getroot()

            self.assertEqual(root.findtext(".//dc:title", namespaces=ns), "Raport zgodnosci")
            self.assertEqual(root.findtext(".//dc:creator", namespaces=ns), "Jan Kowalski")
            self.assertEqual(root.findtext(".//dc:language", namespaces=ns), "pl")
            self.assertRegex(
                root.findtext(".//opf:meta[@property='dcterms:modified']", namespaces=ns),
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
            )
            self.assertTrue(opf_path.exists())
            self.assertTrue(nav_path.exists())

            manifest_items = {
                item.get("id"): item
                for item in root.findall(".//opf:manifest/opf:item", namespaces=ns)
            }
            self.assertIn("nav", manifest_items["nav"].get("properties", ""))

            toc_navs = nav_tree.xpath("//x:nav[@epub:type='toc']", namespaces=ns)
            self.assertEqual(len(toc_navs), 1)
            toc_hrefs = toc_navs[0].xpath(".//x:a/@href", namespaces=ns)
            self.assertGreaterEqual(len(toc_hrefs), 3)

            spine_itemrefs = root.findall(".//opf:spine/opf:itemref", namespaces=ns)
            spine_manifest_hrefs = {
                item_id: manifest_items[item_id].get("href")
                for item_id in manifest_items
                if item_id in manifest_items
            }
            reading_order = [
                spine_manifest_hrefs[itemref.get("idref")]
                for itemref in spine_itemrefs
                if spine_manifest_hrefs.get(itemref.get("idref")) not in {None, "nav.xhtml", "cover.xhtml"}
            ]

            toc_file_order: list[str] = []
            for href in toc_hrefs:
                file_name, anchor = href.split("#", 1) if "#" in href else (href, "")
                if file_name not in toc_file_order:
                    toc_file_order.append(file_name)
                target_path = Path(temp_dir) / "EPUB" / file_name
                self.assertTrue(target_path.exists(), href)
                if anchor:
                    target_tree = etree.parse(str(target_path))
                    self.assertTrue(target_tree.xpath(f"//*[@id='{anchor}']"), href)

            self.assertEqual(toc_file_order, reading_order[: len(toc_file_order)])

    def test_gate_c_process_chapter_demotes_layout_noise_and_keeps_single_h1(self):
        chapter_source = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Analiza zgodnosci</title></head>
  <body>
    <h1>Material sponsorowany - R4</h1>
    <p>Krotki baner reklamowy nie powinien zostac rozdzialem.</p>
    <h1>Analiza zgodnosci</h1>
    <p>To jest prawdziwy punkt wejscia do rozdzialu i powinien pozostac w TOC.</p>
    <p>Rys. 1. Schemat warstwy publikacji</p>
  </body>
</html>
"""
        with TemporaryDirectory() as temp_dir:
            chapter_path = Path(temp_dir) / "chapter_noise.xhtml"
            chapter_path.write_text(chapter_source, encoding="utf-8")

            result = _process_chapter(
                chapter_path,
                repeated_counts=Counter(),
                keep_first_seen=set(),
                title="Analiza zgodnosci",
                author="Tester",
                language="pl",
            )

        tree = etree.fromstring(result.xhtml.encode("utf-8"))
        h1_texts = tree.xpath("//*[local-name()='h1']/text()")
        self.assertEqual(h1_texts, ["Analiza zgodnosci"])
        self.assertNotIn("<h1>Material sponsorowany - R4</h1>", result.xhtml)
        self.assertIn('<p class="promo-banner">Material sponsorowany - R4</p>', result.xhtml)
        self.assertEqual(result.nav_entries[0]["text"], "Analiza zgodnosci")

    def test_gate_f_finalize_publication_epub_marks_epubcheck_failure_as_blocking_warning(self):
        document = PublicationDocument(
            title="Raport koncowy",
            author="Jan Kowalski",
            language="pl",
            profile="book_reflow",
            analysis=self._fake_analysis(),
        )

        with patch(
            "publication_pipeline.run_epubcheck",
            return_value={
                "status": "failed",
                "tool": "epubcheck",
                "messages": ["ERROR(RSC-012): broken href"],
            },
        ):
            report = finalize_publication_epub(document, b"synthetic-epub")

        self.assertEqual(report.validation_status, "failed")
        self.assertEqual(report.validation_tool, "epubcheck")
        self.assertIn("ERROR(RSC-012): broken href", report.validation_messages)
        self.assertTrue(any("EPUBCheck" in warning for warning in report.warnings))

    def test_text_cleanup_result_to_dict_preserves_auditable_manual_review_fields(self):
        result = TextCleanupResult(
            epub_bytes=b"cleaned",
            summary={
                "auto_fix_count": 4,
                "review_needed_count": 1,
                "blocked_count": 0,
                "release_gate": "soft",
                "publish_blocked": False,
            },
            decisions=[
                CleanupDecision(
                    document_path="EPUB/chapter_001.xhtml",
                    node_xpath="/html/body/p[3]/text()[1]",
                    before="Wpraktyce",
                    after="W praktyce",
                    error_class="glued-word",
                    score=0.92,
                    status="review_needed",
                    reason_codes=["lexical-hit", "context-fit"],
                )
            ],
            unknown_terms=[{"term": "babok", "count": 3}],
            epubcheck={"status": "passed", "tool": "epubcheck", "messages": []},
            markdown_report="# Release QA",
            chapter_diffs={"EPUB/chapter_001.xhtml": "--- before\n+++ after"},
        )

        payload = result.to_dict()
        decision = payload["decisions"][0]

        self.assertEqual(decision["document_path"], "EPUB/chapter_001.xhtml")
        self.assertEqual(decision["node_xpath"], "/html/body/p[3]/text()[1]")
        self.assertEqual(decision["status"], "review_needed")
        self.assertEqual(decision["reason_codes"], ["lexical-hit", "context-fit"])
        self.assertEqual(payload["chapter_diffs"]["EPUB/chapter_001.xhtml"].splitlines()[0], "--- before")
        json.dumps(payload)

    def test_conversion_payload_can_materialize_output_and_reports_contract(self):
        analysis = self._fake_analysis()
        document = PublicationDocument(
            title="Raport koncowy",
            author="Jan Kowalski",
            language="pl",
            profile="book_reflow",
            analysis=analysis,
        )

        def fake_finalize_publication_epub(pipeline_document, epub_bytes):
            pipeline_document.quality_report.validation_status = "passed"
            pipeline_document.quality_report.validation_messages = ["EPUBCheck completed"]
            pipeline_document.quality_report.validation_tool = "epubcheck"
            return pipeline_document.quality_report

        with patch("converter._extract_pdf_metadata", return_value={"title": "Legacy draft", "author": "Unknown"}):
            with patch("publication_analysis.analyze_publication", return_value=analysis):
                with patch("publication_pipeline.build_publication_document", return_value=document):
                    with patch(
                        "publication_pipeline.publication_to_content",
                        return_value={"success": True, "layout_mode": "reflowable", "images": [], "chapters": []},
                    ):
                        with patch("converter.build_epub", return_value=b"draft-epub"):
                            with patch(
                                "converter.finalize_epub_bytes",
                                return_value=(
                                    b"final-epub",
                                    {
                                        "status": "passed",
                                        "epubcheck_status": "passed",
                                        "auto_fix_count": 8,
                                        "review_needed_count": 2,
                                        "blocked_count": 0,
                                        "publish_blocked": False,
                                        "release_gate": "soft",
                                        "reference_cleanup": {
                                            "sections_detected": 1,
                                            "entries_rebuilt": 3,
                                            "review_entry_count": 1,
                                            "unresolved_fragment_count": 1,
                                        },
                                    },
                                ),
                            ):
                                with patch(
                                    "publication_pipeline.finalize_publication_epub",
                                    side_effect=fake_finalize_publication_epub,
                                ):
                                    result = convert_pdf_to_epub_with_report(
                                        "synthetic.pdf",
                                        config=ConversionConfig(language="pl"),
                                        original_filename="synthetic.pdf",
                                    )

        self.assertEqual(result["epub_bytes"], b"final-epub")
        self.assertEqual(result["quality_report"]["validation_status"], "passed")
        self.assertEqual(result["quality_report"]["text_cleanup"]["reference_cleanup"]["entries_rebuilt"], 3)
        self.assertEqual(result["document_summary"]["title"], "Raport koncowy")

        release_decision = (
            "fail"
            if result["quality_report"]["validation_status"] != "passed"
            else "pass_with_review"
            if result["quality_report"]["text_cleanup"]["review_needed_count"] > 0
            else "pass"
        )

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            reports_dir = Path(temp_dir) / "reports"
            output_dir.mkdir()
            reports_dir.mkdir()

            (output_dir / "final.epub").write_bytes(result["epub_bytes"])
            (reports_dir / "metadata_diff.json").write_text(
                json.dumps(
                    {
                        "before": {"title": "Legacy draft", "author": "Unknown"},
                        "after": result["document_summary"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (reports_dir / "heading_decisions.json").write_text(
                json.dumps(
                    {
                        "suspect_heading_count": 0,
                        "review_needed_count": result["quality_report"]["text_cleanup"]["review_needed_count"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (reports_dir / "toc_map.json").write_text(
                json.dumps(
                    {
                        "title": result["document_summary"]["title"],
                        "profile": result["document_summary"]["profile"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (reports_dir / "structural_integrity.json").write_text(
                json.dumps(
                    {
                        "validation_status": result["quality_report"]["validation_status"],
                        "validation_messages": result["quality_report"]["validation_messages"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (reports_dir / "epubcheck.json").write_text(
                json.dumps(
                    {
                        "status": result["quality_report"]["validation_status"],
                        "messages": result["quality_report"]["validation_messages"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (reports_dir / "release_report.md").write_text(
                "\n".join(
                    [
                        "# Release report",
                        f"- title: {result['document_summary']['title']}",
                        f"- author: {result['document_summary']['author']}",
                        f"- decision: {release_decision}",
                    ]
                ),
                encoding="utf-8",
            )
            (reports_dir / "manual_review_queue.md").write_text(
                "\n".join(
                    [
                        "# Manual review",
                        f"- review_needed_count: {result['quality_report']['text_cleanup']['review_needed_count']}",
                        f"- unresolved_reference_fragments: {result['quality_report']['text_cleanup']['reference_cleanup']['unresolved_fragment_count']}",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertTrue((output_dir / "final.epub").exists())
            self.assertEqual(json.loads((reports_dir / "metadata_diff.json").read_text(encoding="utf-8"))["after"]["title"], "Raport koncowy")
            self.assertIn("pass_with_review", (reports_dir / "release_report.md").read_text(encoding="utf-8"))
            self.assertIn("review_needed_count: 2", (reports_dir / "manual_review_queue.md").read_text(encoding="utf-8"))

    def test_publication_audit_cli_writes_json_report_artifact(self):
        analysis = self._fake_analysis()

        with TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "input.pdf"
            out_path = Path(temp_dir) / "audit.json"
            pdf_path.write_bytes(b"%PDF-1.4\n%synthetic\n")

            with patch.object(sys, "argv", ["publication_audit.py", str(pdf_path), str(out_path)]):
                with patch("publication_audit.analyze_publication", return_value=analysis):
                    with patch(
                        "publication_audit.convert_pdf_to_epub_with_report",
                        return_value={
                            "document_summary": {
                                "title": "Raport koncowy",
                                "author": "Jan Kowalski",
                                "profile": "book_reflow",
                                "layout_mode": "reflowable",
                                "section_count": 4,
                                "asset_count": 1,
                            },
                            "quality_report": {
                                "validation_status": "passed",
                                "validation_messages": [],
                                "text_cleanup": {"review_needed_count": 1},
                            },
                            "document": {
                                "metadata": {
                                    "audit": {
                                        "high_risk_pages": [],
                                        "manual_review_queue": ["title-conflict"],
                                    }
                                }
                            },
                        },
                    ):
                        exit_code = publication_audit.main()

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["document_summary"]["title"], "Raport koncowy")
            self.assertEqual(payload["quality_report"]["validation_status"], "passed")
            self.assertEqual(payload["audit"]["manual_review_queue"], ["title-conflict"])


if __name__ == "__main__":
    unittest.main()
