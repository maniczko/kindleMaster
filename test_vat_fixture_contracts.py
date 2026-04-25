from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import fitz

from scripts import prepare_reference_inputs as prepare_reference_inputs_module
from scripts.run_smoke_tests import run_smoke_tests
from size_budget_policy import evaluate_size_budget, get_document_size_budget, load_size_budget_policy


REPO_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = REPO_ROOT / "reference_inputs" / "manifest.json"
README_PATH = REPO_ROOT / "reference_inputs" / "README.md"
POLICY_PATH = REPO_ROOT / "reference_inputs" / "size_budgets.json"
VAT_FIXTURE_IDS = ("ocr_stress_scan_pdf", "document_like_report_pdf")


def _minimal_epub_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
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
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>VAT fixture</dc:title>
    <dc:creator>KindleMaster QA</dc:creator>
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
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1 id="intro">Intro</h1><p>VAT probe.</p></body></html>
""",
        )
    return buffer.getvalue()


class VatFixtureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.case_map = {case["id"]: case for case in payload["cases"]}
        cls.ocr_case = cls.case_map["ocr_stress_scan_pdf"]
        cls.document_like_case = cls.case_map["document_like_report_pdf"]
        cls.policy = load_size_budget_policy(POLICY_PATH)

    def _fake_convert(self, input_path: str, **_kwargs):
        path = Path(input_path)
        return {
            "epub_bytes": _minimal_epub_bytes(),
            "analysis": {"profile": "book_reflow", "source": path.name},
            "quality_report": {"validation_status": "passed", "text_cleanup": {}, "warnings": []},
            "document_summary": {"title": path.stem, "author": "KindleMaster QA"},
            "summary": {"status": "passed", "error_count": 0, "warning_count": 0},
        }

    def test_prepare_reference_inputs_generates_the_shared_vat_slice(self) -> None:
        selected_cases = [
            next(case for case in prepare_reference_inputs_module.REFERENCE_CASES if case["id"] == case_id)
            for case_id in VAT_FIXTURE_IDS
        ]

        with patch.object(prepare_reference_inputs_module, "REFERENCE_CASES", selected_cases):
            with tempfile.TemporaryDirectory() as temp_dir:
                manifest = prepare_reference_inputs_module.prepare_reference_inputs(root_dir=temp_dir)
                manifest_path = Path(temp_dir) / "reference_inputs" / "manifest.json"
                ocr_path = Path(temp_dir) / "reference_inputs" / "pdf" / "ocr_stress_scan.pdf"
                report_path = Path(temp_dir) / "reference_inputs" / "pdf" / "document_like_report.pdf"

                self.assertEqual([case["id"] for case in manifest["cases"]], list(VAT_FIXTURE_IDS))
                self.assertTrue(manifest_path.exists())
                self.assertTrue(ocr_path.exists())
                self.assertTrue(report_path.exists())
                self.assertEqual(manifest["cases"][0]["source_path"], "<generated:ocr_stress_scan>")
                self.assertEqual(manifest["cases"][1]["source_path"], "<generated:document_like_report>")

                with fitz.open(ocr_path) as document:
                    self.assertEqual(document.page_count, 3)

                with fitz.open(report_path) as document:
                    self.assertEqual(document.page_count, 4)
                    self.assertEqual(document.metadata.get("title"), "Document-Like Report Fixture")

    def test_prepare_reference_inputs_generates_source_surrogates_for_clean_ci_checkout(self) -> None:
        source_backed_case_ids = ("ocr_probe_pdf", "scan_probe_epub", "magazine_layout_pdf")
        selected_cases = [
            next(case for case in prepare_reference_inputs_module.REFERENCE_CASES if case["id"] == case_id)
            for case_id in source_backed_case_ids
        ]

        with patch.object(prepare_reference_inputs_module, "REFERENCE_CASES", selected_cases):
            with tempfile.TemporaryDirectory() as temp_dir:
                manifest = prepare_reference_inputs_module.prepare_reference_inputs(root_dir=temp_dir)
                case_map = {case["id"]: case for case in manifest["cases"]}
                ocr_path = Path(temp_dir) / "reference_inputs" / "pdf" / "ocr_probe.pdf"
                scan_path = Path(temp_dir) / "reference_inputs" / "epub" / "scan_probe.epub"
                magazine_path = Path(temp_dir) / "reference_inputs" / "pdf" / "magazine_layout.pdf"

                self.assertEqual(set(case_map), set(source_backed_case_ids))
                for case_id in source_backed_case_ids:
                    self.assertEqual(case_map[case_id]["source_path"], f"<generated-fallback:{case_id}>")
                    self.assertGreater(int(case_map[case_id]["size_bytes"]), 0)

                with fitz.open(ocr_path) as document:
                    self.assertEqual(document.page_count, 1)
                    self.assertIn("Ocr Probe Pdf", document.metadata.get("title", ""))

                with fitz.open(magazine_path) as document:
                    self.assertEqual(document.page_count, 3)
                    self.assertIn("Magazine Layout Pdf", document.metadata.get("title", ""))

                with zipfile.ZipFile(scan_path) as archive:
                    self.assertIn("mimetype", archive.namelist())
                    self.assertIn("EPUB/content.opf", archive.namelist())
                    self.assertIn("EPUB/nav.xhtml", archive.namelist())

    def test_manifest_contains_real_vat_fixture_entries_with_required_shape(self) -> None:
        self.assertEqual(set(VAT_FIXTURE_IDS), {"ocr_stress_scan_pdf", "document_like_report_pdf"})

        self.assertEqual(self.ocr_case["document_class"], "ocr_stress_scan")
        self.assertEqual(self.ocr_case["source_path"], "<generated:ocr_stress_scan>")
        self.assertEqual(self.ocr_case["target_path"], "reference_inputs/pdf/ocr_stress_scan.pdf")
        self.assertIn("Deterministic", self.ocr_case["notes"])
        self.assertGreater(int(self.ocr_case["size_bytes"]), 0)

        self.assertEqual(self.document_like_case["document_class"], "document-like-report")
        self.assertEqual(self.document_like_case["source_path"], "<generated:document_like_report>")
        self.assertEqual(self.document_like_case["target_path"], "reference_inputs/pdf/document_like_report.pdf")
        self.assertIn("Generated multi-page", self.document_like_case["notes"])
        self.assertGreater(int(self.document_like_case["size_bytes"]), 0)

    def test_generated_fixture_files_exist_and_have_basic_shape(self) -> None:
        ocr_path = REPO_ROOT / self.ocr_case["target_path"]
        report_path = REPO_ROOT / self.document_like_case["target_path"]

        self.assertTrue(ocr_path.exists())
        self.assertTrue(report_path.exists())

        with fitz.open(ocr_path) as document:
            self.assertEqual(document.page_count, 3)
            image_count = 0
            for page in document:
                self.assertEqual(page.get_text("text").strip(), "")
                image_count += len(page.get_images(full=True))
            self.assertGreaterEqual(image_count, 3)

        with fitz.open(report_path) as document:
            self.assertEqual(document.page_count, 4)
            self.assertEqual(document.metadata.get("title"), "Document-Like Report Fixture")
            self.assertEqual(document.metadata.get("author"), "Anna Nowak")
            toc = document.get_toc()
            self.assertEqual(len(toc), 4)
            self.assertEqual(toc[0][1], "Executive summary")

    def test_size_budget_presence_is_explicit_for_vat_fixture_classes(self) -> None:
        ocr_budget = get_document_size_budget("ocr-stress-scan", policy=self.policy)
        document_budget = get_document_size_budget("document-like-report", policy=self.policy)

        self.assertIsNotNone(ocr_budget)
        self.assertIsNotNone(document_budget)
        assert ocr_budget is not None
        assert document_budget is not None

        self.assertEqual(ocr_budget["baseline_cases"], ["ocr_stress_scan_pdf"])
        self.assertEqual(document_budget["baseline_cases"], ["document_like_report_pdf"])

        ocr_gate = evaluate_size_budget(
            budget_key="ocr-stress-scan",
            budget=ocr_budget,
            epub_size_bytes=1_048_576,
            inspection={"entry_count": 5, "image_count": 1, "largest_assets": []},
            label="klasy dokumentu",
        )
        document_gate = evaluate_size_budget(
            budget_key="document-like-report",
            budget=document_budget,
            epub_size_bytes=65_536,
            inspection={"entry_count": 5, "image_count": 0, "largest_assets": []},
            label="klasy dokumentu",
        )

        self.assertEqual(ocr_gate["status"], "passed")
        self.assertEqual(document_gate["status"], "passed")

    def test_reference_input_readme_documents_the_shared_generated_pdf_slice(self) -> None:
        markdown = README_PATH.read_text(encoding="utf-8")

        self.assertIn("generates repo-local PDF and DOCX probes", markdown)
        self.assertIn("ocr_stress_scan", markdown)
        self.assertIn("document_like_report", markdown)
        self.assertIn("size_budgets.json", markdown)
        self.assertIn("document-like-report", markdown)

    @patch("scripts.run_smoke_tests.load_size_budget_policy")
    @patch("scripts.run_smoke_tests.validate_epub_bytes")
    @patch("scripts.run_smoke_tests.convert_document_to_epub_with_report")
    def test_corpus_smoke_selection_and_reporting_cover_vat_cases(
        self,
        mock_convert,
        mock_validate,
        mock_load_policy,
    ) -> None:
        mock_load_policy.return_value = self.policy
        mock_convert.side_effect = lambda input_path, **kwargs: self._fake_convert(input_path, **kwargs)
        mock_validate.return_value = {
            "summary": {"status": "passed", "error_count": 0, "warning_count": 0, "epubcheck_status": "passed"},
            "epubcheck": {"status": "passed"},
            "internal_links": {"errors": []},
            "external_links": {"errors": []},
            "package": {"errors": []},
        }

        manifest = {
            "version": 2,
            "root_dir": ".",
            "cases": [
                {
                    **self.ocr_case,
                    "target_path": str((REPO_ROOT / self.ocr_case["target_path"]).resolve()),
                },
                {
                    **self.document_like_case,
                    "target_path": str((REPO_ROOT / self.document_like_case["target_path"]).resolve()),
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            manifest_path = temp_root / "manifest.json"
            output_dir = temp_root / "output"
            reports_dir = temp_root / "reports"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

            payload = run_smoke_tests(
                manifest_path=manifest_path,
                mode="full",
                output_dir=output_dir,
                reports_dir=reports_dir,
            )

            self.assertTrue((reports_dir / "smoke_full.json").exists())
            self.assertTrue((reports_dir / "smoke_full.md").exists())
            markdown = (reports_dir / "smoke_full.md").read_text(encoding="utf-8")

        self.assertEqual(payload["summary"]["cases_run"], 2)
        self.assertEqual(payload["summary"]["overall_status"], "passed")
        self.assertIn("benchmark", payload["summary"])
        self.assertEqual(payload["summary"]["benchmark"]["class_count"], 2)
        self.assertEqual([case["id"] for case in payload["cases"]], list(VAT_FIXTURE_IDS))
        for case in payload["cases"]:
            self.assertEqual(case["size_gate"]["status"], "passed")
            self.assertIn("benchmark", case)
            self.assertEqual(case["benchmark"]["validation_status"], "passed")
        self.assertIn("ocr_stress_scan_pdf", markdown)
        self.assertIn("document_like_report_pdf", markdown)
        self.assertIn("## Benchmark", markdown)


if __name__ == "__main__":
    unittest.main()
