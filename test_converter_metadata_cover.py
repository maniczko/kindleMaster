from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import fitz

from converter import ConversionConfig, _extract_pdf_metadata, build_epub, finalize_epub_bytes


NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _create_dense_handbook_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.set_metadata(
        {
            "title": "Untitled",
            "author": "Acrobat PDFMaker",
            "subject": "",
            "creator": "Adobe Acrobat",
            "producer": "Adobe PDF Library",
        }
    )
    cover = doc.new_page(width=595, height=842)
    cover.insert_text((72, 130), "A Guide to Enterprise Analysis", fontsize=28, fontname="helv")
    cover.insert_text((72, 172), "DENSE HANDBOOK GUIDE", fontsize=26, fontname="helv")
    cover.insert_text((72, 226), "Version 4", fontsize=16, fontname="helv")
    cover.insert_text((72, 720), "International Handbook Institute", fontsize=12, fontname="helv")

    copyright_page = doc.new_page(width=595, height=842)
    copyright_page.insert_text(
        (72, 120),
        "Copyright 2022 by International Handbook Institute. All rights reserved.",
        fontsize=12,
        fontname="helv",
    )
    copyright_page.insert_text((72, 152), "Published by International Handbook Institute", fontsize=12, fontname="helv")
    copyright_page.insert_text((72, 184), "ISBN 978-1-23456-789-0", fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()


def _create_training_pdf_with_technical_metadata(path: Path) -> None:
    doc = fitz.open()
    doc.set_metadata(
        {
            "title": "",
            "author": "python-docx",
            "subject": "",
            "creator": "Writer",
            "producer": "LibreOffice 25.2.3.2",
        }
    )
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 110), "MATERIAL DO NAUKI I PODNIESIENIA WARTOSCI RYNKOWEJ", fontsize=14)
    page.insert_text((72, 150), "Eursap: IT Project Manager / PMO (E-Invoicing & Coupa)", fontsize=22)
    page.insert_text((72, 198), "Spersonalizowany przewodnik na ok. 20 stron A4", fontsize=12)
    page.insert_text((72, 250), "1. Co ta oferta naprawde premiuje", fontsize=16)
    page.insert_text((72, 286), "Sama nazwa stanowiska moze mylic. " * 4, fontsize=10)
    doc.save(path)
    doc.close()


def _opf_root(epub_path: Path) -> ET.Element:
    with zipfile.ZipFile(epub_path) as zf:
        opf_name = next(name for name in zf.namelist() if name.endswith(".opf"))
        return ET.fromstring(zf.read(opf_name))


def _dc_text(root: ET.Element, name: str) -> str:
    element = root.find(f".//dc:{name}", NS)
    return element.text if element is not None and element.text else ""


class ConverterMetadataCoverTests(unittest.TestCase):
    def test_pdf_metadata_is_inferred_from_cover_and_copyright_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "dense-handbook.pdf"
            _create_dense_handbook_pdf(pdf_path)

            metadata = _extract_pdf_metadata(str(pdf_path))

        self.assertEqual(metadata["title"], "A Guide to Enterprise Analysis DENSE HANDBOOK GUIDE Version 4")
        self.assertEqual(metadata["author"], "International Handbook Institute")
        self.assertEqual(metadata["publisher"], "International Handbook Institute")
        self.assertEqual(metadata["date"], "2022")
        self.assertIn("A Guide to Enterprise Analysis", metadata["description"])
        self.assertIn("International Handbook Institute", metadata["description"])

    def test_pdf_metadata_rejects_technical_docx_values_and_infers_safe_filename_author(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "material_nauka_eursap_coupa_iwo_v5.pdf"
            _create_training_pdf_with_technical_metadata(pdf_path)

            metadata = _extract_pdf_metadata(str(pdf_path))

        self.assertEqual(metadata["title"], "Eursap: IT Project Manager / PMO (E-Invoicing & Coupa)")
        self.assertEqual(metadata["author"], "Iwo")
        self.assertNotEqual(metadata.get("publisher"), "python-docx")
        self.assertEqual(metadata["creator"], "Writer")
        self.assertIn("filename-author", metadata.get("metadata_inference", {}).get("author", []))

    def test_build_epub_renders_first_pdf_page_as_cover_with_opf_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "dense-handbook.pdf"
            epub_path = tmp_path / "dense-handbook.epub"
            _create_dense_handbook_pdf(pdf_path)
            metadata = {**_extract_pdf_metadata(str(pdf_path)), "source_pdf_path": str(pdf_path)}
            content = {
                "success": True,
                "chapters": [
                    {
                        "title": "Introduction",
                        "html_parts": ["<p>Dense handbook body text.</p>"],
                        "images": [],
                    }
                ],
                "images": [],
                "text_content": True,
            }

            epub_path.write_bytes(build_epub(content, ConversionConfig(language="en"), pdf_path.name, metadata))

            root = _opf_root(epub_path)
            manifest = {item.get("id"): item for item in root.findall(".//opf:item", NS)}
            cover_meta = root.find(".//opf:meta[@name='cover']", NS)
            with zipfile.ZipFile(epub_path) as zf:
                names = set(zf.namelist())

        self.assertEqual(_dc_text(root, "title"), "A Guide to Enterprise Analysis DENSE HANDBOOK GUIDE Version 4")
        self.assertEqual(_dc_text(root, "creator"), "International Handbook Institute")
        self.assertEqual(_dc_text(root, "publisher"), "International Handbook Institute")
        self.assertEqual(_dc_text(root, "date"), "2022")
        self.assertIn("A Guide to Enterprise Analysis", _dc_text(root, "description"))
        self.assertIsNotNone(cover_meta)
        self.assertEqual(cover_meta.get("content"), "cover-image")
        self.assertIn("cover-image", manifest)
        self.assertEqual(manifest["cover-image"].get("media-type"), "image/jpeg")
        self.assertIn("EPUB/images/cover.jpeg", names)
        self.assertIn("EPUB/cover.xhtml", names)

    def test_long_dense_handbook_skips_expensive_text_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / "dense-handbook.pdf"
            _create_dense_handbook_pdf(pdf_path)
            metadata = {
                **_extract_pdf_metadata(str(pdf_path)),
                "source_pdf_path": str(pdf_path),
                "source_page_count": 514,
                "ui_profile": "technical-study",
            }
            content = {
                "success": True,
                "chapters": [
                    {
                        "title": "Introduction",
                        "html_parts": ["<p>Dense handbook body text.</p>"],
                        "images": [],
                    }
                ],
                "images": [],
                "text_content": True,
            }
            epub_bytes = build_epub(content, ConversionConfig(language="en"), pdf_path.name, metadata)

            with patch(
                "text_normalization.clean_epub_text_package",
                side_effect=AssertionError("text cleanup should be bounded for long dense handbooks"),
            ):
                _, details = finalize_epub_bytes(
                    epub_bytes,
                    ConversionConfig(language="en"),
                    metadata,
                    pdf_path.name,
                    publication_profile="book_reflow",
                    return_details=True,
                )

        self.assertEqual(details["status"], "skipped")
        self.assertTrue(details["bounded_long_form_skip"])


if __name__ == "__main__":
    unittest.main()
