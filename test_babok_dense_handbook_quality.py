from __future__ import annotations

import io
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from converter import ConversionConfig, _extract_pdf_metadata, build_epub
from premium_reflow import extract_book_premium, pdfplumber
from publication_analysis import analyze_publication


FIXTURE = Path("reference_inputs/pdf/dense_business_guide.pdf")


def _read_opf(epub_bytes: bytes) -> ET.Element:
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
        opf_name = next(name for name in archive.namelist() if name.endswith(".opf"))
        return ET.fromstring(archive.read(opf_name))


class BabokDenseHandbookQualityTests(unittest.TestCase):
    @unittest.skipUnless(FIXTURE.exists(), "dense business guide fixture is required")
    def test_babok_metadata_inference_is_publication_grade(self) -> None:
        metadata = _extract_pdf_metadata(str(FIXTURE))

        self.assertEqual(metadata["title"], "BABOK Guide v3")
        self.assertEqual(metadata["author"], "International Institute of Business Analysis")
        self.assertEqual(metadata["publisher"], "International Institute of Business Analysis")
        self.assertEqual(metadata["language"] if "language" in metadata else "en", "en")
        self.assertEqual(metadata["description"], "A Guide to the Business Analysis Body of Knowledge, Version 3.")
        self.assertEqual(metadata["date"], "2015")
        self.assertIn("Business Analysis", metadata["subjects"])
        self.assertIn("Requirements Management", metadata["subjects"])
        self.assertIn("Business Analysis Body of Knowledge", metadata["subjects"])
        self.assertIn("IIBA", metadata["subjects"])

    @unittest.skipUnless(FIXTURE.exists(), "dense business guide fixture is required")
    def test_babok_routes_as_dense_book_reflow_not_diagram_or_magazine(self) -> None:
        analysis = analyze_publication(str(FIXTURE), preferred_profile="auto-premium")

        self.assertEqual(analysis.profile, "book_reflow")
        self.assertEqual(analysis.ui_profile, "technical-study")
        self.assertTrue(analysis.has_toc)
        self.assertTrue(analysis.has_tables)
        self.assertFalse(analysis.layout_heavy)

    @unittest.skipUnless(FIXTURE.exists(), "dense business guide fixture is required")
    def test_babok_cover_and_package_metadata_are_written_to_epub(self) -> None:
        metadata = _extract_pdf_metadata(str(FIXTURE))
        metadata["source_pdf_path"] = str(FIXTURE)
        epub_bytes = build_epub(
            {
                "chapters": [
                    {
                        "title": "Introduction",
                        "html_parts": ["<p>Business analysis handbook sample.</p>"],
                        "images": [],
                    }
                ],
                "images": [],
            },
            ConversionConfig(language="en"),
            "dense_business_guide.pdf",
            metadata,
        )

        with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as archive:
            names = set(archive.namelist())
            self.assertIn("EPUB/images/cover.jpeg", names)
            self.assertIn("EPUB/cover.xhtml", names)

        root = _read_opf(epub_bytes)
        ns = {
            "opf": "http://www.idpf.org/2007/opf",
            "dc": "http://purl.org/dc/elements/1.1/",
        }
        values = {
            node.tag.rsplit("}", 1)[-1]: node.text
            for node in root.findall(".//dc:*", ns)
            if node.text
        }
        subjects = [node.text for node in root.findall(".//dc:subject", ns)]
        cover_meta = root.find(".//opf:meta[@name='cover']", ns)

        self.assertEqual(values.get("title"), "BABOK Guide v3")
        self.assertEqual(values.get("creator"), "International Institute of Business Analysis")
        self.assertEqual(values.get("publisher"), "International Institute of Business Analysis")
        self.assertEqual(values.get("description"), "A Guide to the Business Analysis Body of Knowledge, Version 3.")
        self.assertEqual(values.get("date"), "2015")
        self.assertIn("Business Analysis", subjects)
        self.assertIn("Requirements Management", subjects)
        self.assertIsNotNone(cover_meta)
        self.assertEqual(cover_meta.get("content"), "cover-image")

    @unittest.skipIf(pdfplumber is None, "pdfplumber is required for dense handbook extraction")
    @unittest.skipUnless(FIXTURE.exists(), "dense business guide fixture is required")
    def test_babok_extraction_recovers_pdf_figures_and_table_metrics(self) -> None:
        content = extract_book_premium(
            str(FIXTURE),
            config=ConversionConfig(language="en"),
            pdf_metadata={"title": "BABOK Guide v3", "author": "International Institute of Business Analysis"},
        )

        metadata = content.get("metadata") or {}
        table_summary = metadata.get("table_summary") or {}
        html = "\n".join(part for chapter in content.get("chapters", []) for part in chapter.get("html_parts", []))

        self.assertGreaterEqual(len(content.get("images", [])), 6)
        self.assertIn("Figure 3.0.1", html)
        self.assertIn("<figcaption>Figure 3.0.1", html)
        self.assertGreaterEqual(int(metadata.get("source_table_count", 0)), 50)
        self.assertGreaterEqual(int(table_summary.get("wide_table_count", 0)), 1)
        self.assertGreaterEqual(int(table_summary.get("transformed_table_count", 0)), 0)


if __name__ == "__main__":
    unittest.main()
