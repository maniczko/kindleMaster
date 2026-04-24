from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
