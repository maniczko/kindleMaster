from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile

import fitz

from publication_analysis import analyze_publication
from size_budget_policy import load_size_budget_policy
from scripts import prepare_reference_inputs as reference_inputs_module


class OcrStressScanFixtureTests(unittest.TestCase):
    def test_ocr_stress_scan_pdf_generator_is_deterministic_and_image_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = Path(temp_dir) / "first.pdf"
            second_path = Path(temp_dir) / "second.pdf"

            reference_inputs_module._build_ocr_stress_scan_pdf(first_path)
            reference_inputs_module._build_ocr_stress_scan_pdf(second_path)

            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

            with fitz.open(first_path) as document:
                self.assertEqual(document.page_count, 3)
                image_count = 0
                for page in document:
                    self.assertEqual(page.get_text("text").strip(), "")
                    image_count += len(page.get_images(full=True))
                self.assertGreaterEqual(image_count, 3)

    def test_prepare_reference_inputs_writes_manifest_and_scanned_fixture(self) -> None:
        ocr_case = {
            "id": "ocr_stress_scan_pdf",
            "document_class": "ocr_stress_scan",
            "input_type": "pdf",
            "language": "pl",
            "quick_smoke": False,
            "generator": "ocr_stress_scan",
            "target": "reference_inputs/pdf/ocr_stress_scan.pdf",
            "notes": "Deterministic scanned PDF with OCR-stressed image-only pages and noisy text blocks.",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(reference_inputs_module, "REFERENCE_CASES", [ocr_case]):
                manifest = reference_inputs_module.prepare_reference_inputs(root_dir=temp_dir)

            target_path = Path(temp_dir) / "reference_inputs" / "pdf" / "ocr_stress_scan.pdf"
            manifest_path = Path(temp_dir) / "reference_inputs" / "manifest.json"
            policy = load_size_budget_policy()

            self.assertTrue(target_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertEqual(manifest["cases"][0]["id"], "ocr_stress_scan_pdf")
            self.assertEqual(manifest["cases"][0]["source_path"], "<generated:ocr_stress_scan>")
            self.assertEqual(manifest["cases"][0]["size_bytes"], target_path.stat().st_size)
            self.assertIn("ocr_stress_scan", policy["document_classes"])

            analysis = analyze_publication(str(target_path), preferred_profile="auto-premium")
            self.assertTrue(analysis.is_scanned)
            self.assertEqual(analysis.scanned_pages, 3)
            self.assertEqual(analysis.page_count, 3)
            self.assertEqual(analysis.to_dict()["scanned_pages"], 3)

    def test_mixed_scan_text_fixture_has_text_and_image_only_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = Path(temp_dir) / "mixed_scan_text.pdf"

            reference_inputs_module._build_mixed_scan_text_pdf(target_path)

            with fitz.open(target_path) as document:
                self.assertEqual(document.page_count, 2)
                self.assertIn("normal text layer", document[0].get_text("text"))
                self.assertEqual(document[1].get_text("text").strip(), "")
                self.assertGreaterEqual(len(document[1].get_images(full=True)), 1)

            analysis = analyze_publication(str(target_path), preferred_profile="auto-premium")
            self.assertGreaterEqual(analysis.scanned_pages, 1)
            self.assertGreaterEqual(analysis.text_pages, 1)

    def test_prepare_reference_inputs_normalizes_copied_epub_metadata_for_epubcheck(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.epub"
            _write_minimal_epub_without_modified(source_path)
            epub_case = {
                "id": "scan_probe_epub",
                "document_class": "scan_probe",
                "input_type": "epub",
                "language": "pl",
                "quick_smoke": True,
                "release_strict": False,
                "source": "source.epub",
                "target": "reference_inputs/epub/scan_probe.epub",
                "notes": "Small EPUB for fast validator and repair smoke.",
            }

            with patch.object(reference_inputs_module, "REFERENCE_CASES", [epub_case]):
                reference_inputs_module.prepare_reference_inputs(root_dir=root)

            target_path = root / "reference_inputs" / "epub" / "scan_probe.epub"
            with zipfile.ZipFile(target_path) as archive:
                opf = archive.read("EPUB/content.opf").decode("utf-8")

            self.assertIn('property="dcterms:modified"', opf)


def _write_minimal_epub_without_modified(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
""",
        )
        archive.writestr(
            "EPUB/content.opf",
            """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">fixture</dc:identifier>
    <dc:title>Fixture</dc:title>
    <dc:creator>Reference Fixture Team</dc:creator>
    <dc:language>pl</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
""",
        )
        archive.writestr(
            "EPUB/nav.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml#intro">Intro</a></li></ol></nav></body>
</html>
""",
        )
        archive.writestr(
            "EPUB/chapter.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="intro">Intro</h1></body></html>
""",
        )


if __name__ == "__main__":
    unittest.main()
