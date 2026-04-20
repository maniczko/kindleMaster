from __future__ import annotations

import unittest
from pathlib import Path

from converter import ConversionConfig, convert_document_to_epub_with_report
from premium_tools import detect_toolchain
from publication_analysis import analyze_publication


class PdfRuntimeFlowTests(unittest.TestCase):
    def test_detect_toolchain_exposes_pdf_conversion_dependencies(self) -> None:
        toolchain = detect_toolchain()
        self.assertIn("java", toolchain)
        self.assertIn("tesseract", toolchain)
        self.assertIn("ocrmypdf", toolchain)
        self.assertIn("epubcheck", toolchain)
        self.assertIn("pdfbox", toolchain)
        self.assertIn("jar_found", toolchain["epubcheck"])
        self.assertIn("jar_found", toolchain["pdfbox"])

    def test_publication_analysis_exposes_external_tools(self) -> None:
        pdf_path = Path("reference_inputs/pdf/ocr_probe.pdf").resolve()
        analysis = analyze_publication(str(pdf_path))
        payload = analysis.to_dict()
        self.assertEqual(payload["page_count"], 1)
        self.assertIn("external_tools", payload)
        self.assertIn("epubcheck", payload["external_tools"])
        self.assertIn("pdfbox", payload["external_tools"])

    def test_small_pdf_converts_through_runtime_pipeline(self) -> None:
        pdf_path = Path("reference_inputs/pdf/ocr_probe.pdf").resolve()
        result = convert_document_to_epub_with_report(
            str(pdf_path),
            config=ConversionConfig(profile="auto-premium", language="pl"),
            original_filename=pdf_path.name,
            source_type="pdf",
        )
        self.assertEqual(result["source_type"], "pdf")
        self.assertGreater(len(result["epub_bytes"]), 1000)
        self.assertIn("quality_report", result)
        self.assertIn("document_summary", result)
        self.assertIn(result["quality_report"]["validation_status"], {"passed", "passed_with_warnings", "failed", "unavailable"})


if __name__ == "__main__":
    unittest.main()
