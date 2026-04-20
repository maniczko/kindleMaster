from __future__ import annotations

import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from workflow_runner import _detect_input_type, _load_targeted_tests_from_agents, run_workflow_baseline, run_workflow_verify


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
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:test</dc:identifier>
    <dc:title>Workflow Probe</dc:title>
    <dc:creator>Codex</dc:creator>
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
  <head><title>TOC</title></head>
  <body><nav epub:type="toc"><ol><li><a href="chapter.xhtml#intro">Intro</a></li></ol></nav></body>
</html>
""",
        )
        archive.writestr(
            "EPUB/chapter.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head><body><h1 id="intro">Intro</h1></body></html>
""",
        )
    return buffer.getvalue()


class WorkflowRunnerTests(unittest.TestCase):
    def test_change_area_matrix_is_complete_and_non_empty(self) -> None:
        expected = {"app", "converter", "reference", "heading", "text", "semantic", "pipeline", "corpus"}
        discovered = {area for area in expected if _load_targeted_tests_from_agents(area)}
        self.assertEqual(discovered, expected)

    @patch("workflow_runner.run_epub_publishing_quality_recovery", return_value={"decision": "pass", "gates": {"C": {"status": "pass"}, "D": {"status": "pass"}}})
    @patch("workflow_runner.validate_epub_bytes", return_value={"summary": {"status": "failed", "error_count": 2, "warning_count": 0}, "epubcheck": {"status": "failed"}, "internal_links": {"errors": ["broken fragment"]}, "external_links": {"errors": []}, "package": {"errors": []}})
    @patch("workflow_runner.convert_document_to_epub_with_report")
    def test_baseline_for_pdf_writes_manifest_report_and_before_epub(self, mock_convert, _mock_validate, _mock_audit) -> None:
        mock_convert.return_value = {
            "epub_bytes": _minimal_epub_bytes(),
            "analysis": {"profile": "book_reflow"},
            "quality_report": {
                "validation_status": "failed",
                "warnings": ["EPUBCheck zglosil problemy wymagajace poprawy przed release."],
                "text_cleanup": {"review_needed_count": 4, "blocked_count": 1, "reference_cleanup": {"quality_gate_status": "passed", "visible_junk_detected": 0}},
            },
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf_path = root / "probe.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 workflow probe")

            payload = run_workflow_baseline(
                pdf_path,
                change_area="reference",
                reports_root=root / "reports",
                output_root=root / "output",
            )

            run_id = payload["run_id"]
            self.assertTrue((root / "reports" / run_id / "baseline.json").exists())
            self.assertTrue((root / "reports" / run_id / "baseline.md").exists())
            self.assertTrue((root / "reports" / run_id / "isolation.json").exists())
            self.assertTrue((root / "output" / run_id / "before.epub").exists())
            isolation = json.loads((root / "reports" / run_id / "isolation.json").read_text(encoding="utf-8"))
            self.assertEqual(isolation["change_area"], "reference")
            self.assertTrue(isolation["recommended_tests"])
            self.assertEqual(isolation["recommended_smoke"][1]["mode"], "quick")

    @patch("workflow_runner.run_epub_publishing_quality_recovery", return_value={"decision": "pass", "gates": {"C": {"status": "pass"}, "D": {"status": "pass"}}})
    @patch("workflow_runner.validate_epub_bytes", return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "epubcheck": {"status": "passed"}, "internal_links": {"errors": []}, "external_links": {"errors": []}, "package": {"errors": []}})
    @patch("workflow_runner.convert_document_to_epub_with_report")
    def test_baseline_for_docx_writes_before_epub_and_docx_marker(self, mock_convert, _mock_validate, _mock_audit) -> None:
        mock_convert.return_value = {
            "epub_bytes": _minimal_epub_bytes(),
            "source_type": "docx",
            "analysis": {"profile": "docx_reflow"},
            "quality_report": {"validation_status": "passed", "warnings": []},
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docx_path = root / "probe.docx"
            docx_path.write_bytes(b"docx-bytes")

            payload = run_workflow_baseline(
                docx_path,
                change_area="converter",
                reports_root=root / "reports",
                output_root=root / "output",
            )

            self.assertEqual(payload["input_type"], "docx")
            self.assertTrue((root / "output" / payload["run_id"] / "before.epub").exists())
            isolation = json.loads((root / "reports" / payload["run_id"] / "isolation.json").read_text(encoding="utf-8"))
            self.assertEqual(isolation["recommended_smoke"][1]["mode"], "quick")
            self.assertEqual(isolation["recommended_smoke"][1]["case_filters"], [])

    @patch("workflow_runner.run_epub_publishing_quality_recovery", return_value={"decision": "pass_with_review", "gates": {"C": {"status": "pass_with_review"}, "D": {"status": "pass"}}})
    @patch("workflow_runner.validate_epub_path", return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "epubcheck": {"status": "passed"}, "internal_links": {"errors": []}, "external_links": {"errors": []}, "package": {"errors": []}})
    def test_baseline_for_epub_writes_validation_and_audit_snapshots(self, _mock_validate, _mock_audit) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub_path = root / "probe.epub"
            epub_path.write_bytes(_minimal_epub_bytes())

            payload = run_workflow_baseline(
                epub_path,
                change_area="heading",
                reports_root=root / "reports",
                output_root=root / "output",
            )

            run_id = payload["run_id"]
            report_dir = root / "reports" / run_id
            self.assertTrue((report_dir / "baseline_validation.json").exists())
            self.assertTrue((report_dir / "baseline_audit_result.json").exists())
            self.assertEqual(payload["input_type"], "epub")

    def test_verify_without_baseline_fails_cleanly(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub_path = root / "probe.epub"
            epub_path.write_bytes(_minimal_epub_bytes())

            payload = run_workflow_verify(
                epub_path,
                run_id="missing-run",
                reports_root=root / "reports",
                output_root=root / "output",
            )

            self.assertEqual(payload["status"], "failed")
            self.assertIn("Baseline artifacts not found", payload["error"])

    def test_detect_input_type_accepts_docx(self) -> None:
        self.assertEqual(_detect_input_type(Path("sample.docx")), "docx")

    @patch("workflow_runner.subprocess.run")
    @patch("workflow_runner.run_smoke_tests")
    @patch("workflow_runner.run_epub_publishing_quality_recovery")
    @patch("workflow_runner.validate_epub_path")
    def test_verify_uses_saved_change_area_and_writes_before_after(
        self,
        mock_validate,
        mock_audit,
        mock_smoke,
        mock_subprocess,
    ) -> None:
        mock_validate.side_effect = [
            {"summary": {"status": "failed", "error_count": 3, "warning_count": 0}, "epubcheck": {"status": "failed"}, "internal_links": {"errors": ["duplicate id values found"]}, "external_links": {"errors": []}, "package": {"errors": []}},
            {"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "epubcheck": {"status": "passed"}, "internal_links": {"errors": []}, "external_links": {"errors": []}, "package": {"errors": []}},
        ]
        mock_audit.side_effect = [
            {"decision": "fail", "gates": {"C": {"status": "fail", "message": "Heading gate failed."}, "D": {"status": "pass"}}},
            {"decision": "pass", "gates": {"C": {"status": "pass"}, "D": {"status": "pass"}}},
        ]
        mock_smoke.return_value = {
            "summary": {"overall_status": "passed", "cases_run": 1, "failed_cases": 0, "warning_cases": 0},
            "cases": [],
        }
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "ok"
        mock_subprocess.return_value.stderr = ""

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub_path = root / "probe.epub"
            epub_path.write_bytes(_minimal_epub_bytes())

            baseline = run_workflow_baseline(
                epub_path,
                change_area="text",
                reports_root=root / "reports",
                output_root=root / "output",
            )

            payload = run_workflow_verify(
                epub_path,
                run_id=baseline["run_id"],
                reports_root=root / "reports",
                output_root=root / "output",
            )

            self.assertEqual(payload["change_area"], "text")
            self.assertIn(payload["status"], {"passed", "passed_with_warnings"})
            self.assertTrue((root / "reports" / baseline["run_id"] / "verification.json").exists())
            self.assertTrue((root / "reports" / baseline["run_id"] / "before_after.json").exists())
            before_after = json.loads((root / "reports" / baseline["run_id"] / "before_after.json").read_text(encoding="utf-8"))
            self.assertEqual(before_after["before"]["status"], "failed")
            self.assertEqual(before_after["after"]["status"], "passed")
            self.assertLess(before_after["delta"]["error_count"], 0)

    @patch("workflow_runner.run_epub_publishing_quality_recovery", return_value={"decision": "pass", "gates": {"C": {"status": "pass"}, "D": {"status": "pass"}}})
    @patch("workflow_runner.validate_epub_path", return_value={"summary": {"status": "passed", "error_count": 0, "warning_count": 0}, "epubcheck": {"status": "passed"}, "internal_links": {"errors": []}, "external_links": {"errors": []}, "package": {"errors": []}})
    @patch("workflow_runner.run_smoke_tests")
    @patch("workflow_runner.subprocess.run")
    def test_shared_module_verify_executes_quick_smoke_without_case_filter(
        self,
        mock_subprocess,
        mock_smoke,
        _mock_validate,
        _mock_audit,
    ) -> None:
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "ok"
        mock_subprocess.return_value.stderr = ""
        mock_smoke.return_value = {
            "summary": {"overall_status": "passed", "cases_run": 2, "failed_cases": 0, "warning_cases": 0},
            "cases": [],
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            epub_path = root / "probe.epub"
            epub_path.write_bytes(_minimal_epub_bytes())

            baseline = run_workflow_baseline(
                epub_path,
                change_area="semantic",
                reports_root=root / "reports",
                output_root=root / "output",
            )
            payload = run_workflow_verify(
                epub_path,
                run_id=baseline["run_id"],
                reports_root=root / "reports",
                output_root=root / "output",
            )

            self.assertIn(payload["status"], {"passed", "passed_with_warnings"})
            self.assertEqual(mock_smoke.call_args.kwargs["mode"], "quick")
            self.assertEqual(mock_smoke.call_args.kwargs["case_filters"], [])
